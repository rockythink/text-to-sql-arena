from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import tempfile
from pathlib import Path

from backend.app.adapters.base import (
    AdapterError,
    AdapterHealth,
    AdapterProfile,
    EventSink,
    GenerationResponse,
    json_lines,
    parse_generation_output,
    provider_request_payload,
    run_cli,
    safe_subprocess_env,
)
from backend.app.adapters.cli_common import file_hash, probe_cli
from backend.app.domain import GenerationRequest

CODEX_FLAGS = (
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--sandbox",
    "--skip-git-repo-check",
    "--output-schema",
)


def _resolve_codex_binary(launcher: str) -> Path | None:
    resolved = Path(launcher).resolve()
    if resolved.suffix != ".js":
        return resolved if resolved.is_file() and os.access(resolved, os.X_OK) else None
    package_root = resolved.parent.parent
    candidates = sorted(package_root.glob("node_modules/@openai/codex-*/vendor/*/bin/codex"))
    executables = [candidate.resolve() for candidate in candidates if os.access(candidate, os.X_OK)]
    return executables[0] if len(executables) == 1 else None


def build_seatbelt_profile(codex_path: Path, case_dir: Path, auth_path: Path) -> str:
    system_roots = (
        "/System",
        "/usr/lib",
        "/usr/share",
        "/Library/Apple",
        "/etc/ssl",
        "/private/etc/ssl",
        "/etc/codex",
        "/private/etc/codex",
        "/private/etc",
    )
    project_root = Path(__file__).resolve().parents[3]
    home_path = Path.home()
    ssh_path = home_path / ".ssh"
    allows = "\n".join(f'(allow file-read* (subpath "{root}"))' for root in system_roots)
    return f"""(version 1)
(deny default)
(allow file-read-data (require-all
  (require-not (subpath "{home_path}"))))
(allow file-read-metadata (require-all
  (require-not (subpath "{project_root}"))
  (require-not (subpath "{ssh_path}"))))
(deny file-read* (subpath "{project_root}"))
(deny file-read* (subpath "{ssh_path}"))
(allow network*)
(allow process-info*)
(allow mach-lookup
  (global-name "com.apple.SecurityServer")
  (global-name "com.apple.SystemConfiguration.configd")
  (global-name "com.apple.system.opendirectoryd.libinfo")
  (global-name "com.apple.system.notification_center")
  (global-name "com.apple.logd")
  (global-name "com.apple.analyticsd"))
(allow sysctl-read)
(allow signal (target self))
(allow process-exec (literal "{codex_path}"))
(allow file-read* (literal "{codex_path}"))
(allow file-read* (literal "{auth_path}"))
(allow file-read* (subpath "{case_dir}"))
(allow file-write* (subpath "{case_dir}"))
{allows}
"""


def prepare_codex_case(
    temp_name: str,
    codex: str,
    auth_path: Path,
    output_schema: dict[str, object],
) -> tuple[Path, Path, Path, Path, str]:
    case_dir = Path(temp_name).resolve()
    codex_home = case_dir / "codex-home"
    codex_home.mkdir(mode=0o700)
    (codex_home / "auth.json").symlink_to(auth_path.resolve())
    schema_path = case_dir / "output-schema.json"
    schema_path.write_text(json.dumps(output_schema), encoding="utf-8")
    policy = build_seatbelt_profile(Path(codex).resolve(), case_dir, auth_path.resolve())
    policy_path = case_dir / "seatbelt.sb"
    policy_path.write_text(policy, encoding="utf-8")
    return case_dir, codex_home, schema_path, policy_path, policy


def parse_codex_jsonl(
    raw: str, requested_model: str
) -> tuple[str, dict[str, int], str, str | None]:
    chunks: list[str] = []
    usage: dict[str, int] = {}
    resolved_model = requested_model
    request_id: str | None = None
    for event in json_lines(raw):
        event_type = event.get("type")
        if event_type == "thread.started":
            request_id = str(event.get("thread_id") or event.get("id") or "") or request_id
        elif event_type in {"item.completed", "item.updated"}:
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        elif event_type == "turn.completed":
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = {
                    str(key): int(value)
                    for key, value in raw_usage.items()
                    if isinstance(value, int)
                }
            if isinstance(event.get("model"), str):
                resolved_model = event["model"]
        elif event_type in {"turn.failed", "error"}:
            raise AdapterError("provider_cli_error", json.dumps(event, ensure_ascii=False))
    if not chunks:
        raise AdapterError("provider_protocol_error", "Codex JSONL 未包含可见 agent_message")
    return "".join(chunks), usage, resolved_model, request_id


class CodexCliAdapter:
    async def check(self, profile: AdapterProfile) -> AdapterHealth:
        if platform.system() != "Darwin":
            return AdapterHealth(status="incompatible", message="Codex 首版仅支持 macOS Seatbelt")
        if shutil.which("sandbox-exec") is None:
            return AdapterHealth(status="incompatible", message="sandbox-exec 不可用")
        auth_path = Path.home() / ".codex" / "auth.json"
        if not auth_path.is_file():
            return AdapterHealth(status="unavailable", message="Codex auth.json 不可用")
        health = await probe_cli("codex", CODEX_FLAGS, help_args=("exec", "--help"))
        if health.status == "healthy":
            launcher = shutil.which("codex")
            native = _resolve_codex_binary(launcher) if launcher else None
            if native is None:
                return AdapterHealth(
                    status="incompatible",
                    message="无法唯一定位 Codex 原生二进制",
                    version=health.version,
                )
            health.resolved_model_id = profile.model_id
            health.details["sandbox_exec"] = shutil.which("sandbox-exec")
            health.details["native_executable"] = str(native)
            health.details["auth_allowlisted"] = True

        return health

    async def generate(
        self,
        profile: AdapterProfile,
        request: GenerationRequest,
        emit: EventSink,
        cancel: asyncio.Event,
    ) -> GenerationResponse:
        launcher = shutil.which("codex")
        codex = _resolve_codex_binary(launcher) if launcher else None
        sandbox_exec = shutil.which("sandbox-exec")
        if codex is None or not sandbox_exec or platform.system() != "Darwin":
            raise AdapterError("profile_incompatible", "Codex 或 macOS sandbox-exec 不可用")
        auth_path = Path.home() / ".codex" / "auth.json"
        if not auth_path.is_file():
            raise AdapterError("provider_auth_error", "Codex auth.json 不可用")
        with tempfile.TemporaryDirectory(prefix="llm-test-codex-") as temp_name:
            case_dir, codex_home, schema_path, policy_path, policy = await asyncio.to_thread(
                prepare_codex_case,
                temp_name,
                str(codex),
                auth_path,
                request.output_schema,
            )
            command = [
                sandbox_exec,
                "-f",
                str(policy_path),
                str(codex),
                "exec",
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--model",
                profile.model_id,
                "--output-schema",
                str(schema_path),
                "-",
            ]
            await emit(
                "provider.requested",
                "info",
                provider_request_payload(
                    profile,
                    request,
                    transport="cli",
                    invocation={
                        "command": "codex exec",
                        "arguments": [
                            "--json",
                            "--ephemeral",
                            "--ignore-user-config",
                            "--ignore-rules",
                            "--sandbox",
                            "read-only",
                            "--skip-git-repo-check",
                            "--model",
                            profile.model_id,
                            "--output-schema",
                            "<generated-schema.json>",
                            "-",
                        ],
                        "stdin": request.prompt,
                        "isolation_policy_hash": file_hash(policy),
                    },
                ),
            )
            result = await run_cli(
                command,
                cwd=case_dir,
                environment=safe_subprocess_env({"CODEX_HOME": str(codex_home)}),
                stdin=request.prompt,
                cancel=cancel,
                timeout_seconds=float(profile.parameters.get("timeout_seconds", 120)),
            )
        if result.returncode != 0:
            raise AdapterError(
                "provider_cli_error",
                f"Codex 退出码 {result.returncode}: {result.stderr}",
            )
        raw_output, usage, resolved_model, request_id = parse_codex_jsonl(
            result.stdout, profile.model_id
        )
        await emit("provider.delta", "info", {"text": raw_output})
        parsed, strict = parse_generation_output(raw_output)
        await emit(
            "provider.completed",
            "info",
            {"status": "completed", "elapsed_ms": result.latency_ms, "token_usage": usage},
        )
        return GenerationResponse(
            raw_output=raw_output,
            parsed_output=parsed,
            resolved_model_id=resolved_model,
            token_usage=usage,
            provider_request_id=request_id,
            latency_ms=result.latency_ms,
            protocol_strict=strict,
        )
