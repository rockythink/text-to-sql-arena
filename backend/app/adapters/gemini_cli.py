from __future__ import annotations

import asyncio
import json
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
    run_cli,
    safe_subprocess_env,
)
from backend.app.adapters.cli_common import ensure_private_directory, file_hash, probe_cli
from backend.app.config import settings
from backend.app.domain import GenerationRequest

GEMINI_FLAGS = ("--output-format", "--approval-mode", "--policy", "-e", "--model")
GEMINI_POLICY = """[[rule]]
toolName = "*"
decision = "deny"
priority = 999
"""
GEMINI_SETTINGS = {"hooksConfig": {"enabled": False}, "skills": {"enabled": False}}


def parse_gemini_jsonl(
    raw: str, requested_model: str
) -> tuple[str, dict[str, int], str, str | None]:
    chunks: list[str] = []
    usage: dict[str, int] = {}
    resolved_model = requested_model
    request_id: str | None = None
    for event in json_lines(raw):
        event_type = event.get("type")
        if event_type in {"tool_call", "tool_result"} or event.get("tool_name"):
            raise AdapterError("adapter_policy_violation", "Gemini 尝试调用工具")
        if event_type in {"message", "content", "assistant"}:
            role = event.get("role")
            if role in {None, "assistant", "model"}:
                text = event.get("content") or event.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        elif event_type == "result":
            response = event.get("response") or event.get("result")
            if isinstance(response, str) and not chunks:
                chunks.append(response)
            raw_usage = event.get("usage") or event.get("stats")
            if isinstance(raw_usage, dict):
                usage = {
                    str(key): int(value)
                    for key, value in raw_usage.items()
                    if isinstance(value, int)
                }
            request_id = str(event.get("session_id") or event.get("id") or "") or request_id
            resolved_model = str(event.get("model") or resolved_model)
            if event.get("error"):
                raise AdapterError("provider_cli_error", json.dumps(event, ensure_ascii=False))
    if not chunks:
        raise AdapterError("provider_protocol_error", "Gemini stream-json 未包含可见文本")
    return "".join(chunks), usage, resolved_model, request_id


def _safe_gemini_settings(path: Path) -> dict[str, object]:
    settings: dict[str, object] = {
        "hooksConfig": {"enabled": False},
        "skills": {"enabled": False},
    }
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return settings
    auth = existing.get("security", {}).get("auth", {}) if isinstance(existing, dict) else {}
    selected_type = auth.get("selectedType") if isinstance(auth, dict) else None
    if isinstance(selected_type, str):
        settings["security"] = {"auth": {"selectedType": selected_type}}
    return settings


def _gemini_exit_code(stderr: str) -> str:
    lowered = stderr.lower()
    auth_markers = (
        "auth method",
        "authenticating",
        "gemini_api_key",
        "ineligibletiererror",
    )
    if any(marker in lowered for marker in auth_markers):
        return "provider_auth_error"
    return "provider_cli_error"


class GeminiCliAdapter:
    async def check(self, profile: AdapterProfile) -> AdapterHealth:
        health = await probe_cli("gemini", GEMINI_FLAGS)
        if health.status == "healthy":
            health.resolved_model_id = profile.model_id
            health.details["extensions"] = "none"
            health.details["policy_hash"] = file_hash(GEMINI_POLICY)
        return health

    async def generate(
        self,
        profile: AdapterProfile,
        request: GenerationRequest,
        emit: EventSink,
        cancel: asyncio.Event,
    ) -> GenerationResponse:
        gemini = shutil.which("gemini")
        if not gemini:
            raise AdapterError("profile_unavailable", "command not found: gemini")
        cli_home = settings.var_dir / "cli-homes" / "gemini" / str(profile.id)
        ensure_private_directory(cli_home)
        gemini_config = cli_home / ".gemini"
        ensure_private_directory(gemini_config)
        settings_path = gemini_config / "settings.json"
        settings_path.write_text(
            json.dumps(_safe_gemini_settings(settings_path), separators=(",", ":")),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory(prefix="llm-test-gemini-") as temp_name:
            case_dir = Path(temp_name)
            policy_path = case_dir / "policy.toml"
            policy_path.write_text(GEMINI_POLICY, encoding="utf-8")
            command = [
                gemini,
                "-p",
                request.prompt,
                "--output-format",
                "stream-json",
                "--approval-mode",
                "plan",
                "--policy",
                str(policy_path),
                "-e",
                "none",
                "--model",
                profile.model_id,
            ]
            await emit(
                "provider.requested",
                "info",
                {"status": "running", "isolation_policy_hash": file_hash(GEMINI_POLICY)},
            )
            result = await run_cli(
                command,
                cwd=case_dir,
                environment=safe_subprocess_env({"GEMINI_CLI_HOME": str(cli_home)}),
                stdin=None,
                cancel=cancel,
                timeout_seconds=float(profile.parameters.get("timeout_seconds", 120)),
            )
        if result.returncode != 0:
            raise AdapterError(
                _gemini_exit_code(result.stderr),
                f"Gemini 退出码 {result.returncode}: {result.stderr}",
            )
        raw_output, usage, resolved_model, request_id = parse_gemini_jsonl(
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
