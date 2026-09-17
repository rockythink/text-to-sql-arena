from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import keyring
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.adapters.base import (
    MAX_RAW_OUTPUT_BYTES,
    AdapterError,
    AdapterHealth,
    AdapterProfile,
    EventSink,
    GenerationResponse,
    parse_generation_output,
    provider_request_payload,
    safe_subprocess_env,
)
from backend.app.domain import GenerationRequest
from backend.app.security import SERVICE_NAME, SecretStore, redact_secrets

RUNTIME = Path(__file__).resolve().parents[3] / "runtime" / "pi"
POLICY = json.loads((RUNTIME / "policy.json").read_text())
PI_VERSION = json.loads((RUNTIME / "package.json").read_text())["dependencies"][
    "@earendil-works/pi-ai"
]
_OAUTH_LOCKS: dict[str, asyncio.Lock] = {}


class PiParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    provider: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=100)
    auth_mode: Literal["oauth", "api_key"] = "api_key"
    timeout_seconds: float = Field(default=180, gt=0, le=1800)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=128000)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "max"] | None = None

    @model_validator(mode="after")
    def supported_subscription(self) -> PiParameters:
        if self.provider == "openai-codex":
            if self.auth_mode != "oauth":
                raise ValueError("openai-codex 使用订阅 OAuth，不使用 API Key")
            if self.max_tokens is not None:
                raise ValueError("Pi 的 Codex 订阅通道不发送 max_tokens；不能假装该限制已生效")
        return self


def validate_parameters(
    parameters: dict[str, Any], base_url: str | None, response_mode: str
) -> dict[str, Any]:
    parsed = PiParameters.model_validate(parameters)
    if response_mode != "text":
        raise ValueError(
            "Pi 统一使用 text 通道，在固定提示中规定 JSON 输出，不采用厂商专属 JSON 模式"
        )
    if base_url:
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Base URL 必须是无内嵌凭证的 HTTP(S) 地址")
        if url.query or url.fragment:
            raise ValueError("Base URL 不能包含查询参数或片段；凭证必须放入钥匙串")
        if parsed.auth_mode == "oauth":
            raise ValueError("订阅 OAuth 不允许覆盖服务端地址")
    return parsed.model_dump(exclude_none=True)


def validate_pi_profile(profile: AdapterProfile) -> PiParameters:
    if profile.adapter_kind != "pi":
        raise ValueError("旧接入仅供历史存证，请新建 Pi 模型配置")
    return PiParameters.model_validate(
        validate_parameters(profile.parameters, profile.base_url, profile.response_mode)
    )


def _oauth_reference(provider: str) -> str:
    return f"keyring:pi-oauth:{provider}"


def _credential_from_file(provider: str) -> dict[str, Any] | None:
    # Read credentials only. Never load Pi/Codex settings, rules, sessions, or extensions.
    pi_auth = Path.home() / ".pi" / "agent" / "auth.json"
    pi_credential: dict[str, Any] | None = None
    if pi_auth.is_file():
        try:
            entry = json.loads(pi_auth.read_text()).get(provider)
        except (ValueError, OSError):
            entry = None
        if isinstance(entry, dict) and entry.get("type") == "oauth":
            pi_credential = {
                key: entry[key]
                for key in ("type", "access", "refresh", "expires", "accountId")
                if key in entry
            }
    if provider != "openai-codex":
        return pi_credential
    codex_auth = Path.home() / ".codex" / "auth.json"
    if not codex_auth.is_file():
        return pi_credential
    try:
        tokens = json.loads(codex_auth.read_text()).get("tokens", {})
        access, refresh = tokens.get("access_token"), tokens.get("refresh_token")
        if not isinstance(access, str) or not isinstance(refresh, str):
            return pi_credential
        encoded = access.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        credential = {
            "type": "oauth",
            "access": access,
            "refresh": refresh,
            "expires": float(claims.get("exp", 0)) * 1000,
            "accountId": tokens.get("account_id"),
        }
        if pi_credential and float(pi_credential.get("expires", 0)) >= credential["expires"]:
            return pi_credential
        return credential
    except (ValueError, OSError, IndexError, TypeError):
        return pi_credential


def _scrub(value: Any, credential: dict[str, Any]) -> Any:
    if isinstance(value, str):
        for key in ("key", "access", "refresh"):
            secret = credential.get(key)
            if isinstance(secret, str) and secret:
                value = value.replace(secret, "[REDACTED]")
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _scrub(item, credential) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, credential) for item in value]
    return value


class PiAdapter:
    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self.secret_store = secret_store or SecretStore()

    def _credential(self, profile: AdapterProfile, params: PiParameters) -> dict[str, Any]:
        if params.auth_mode == "api_key":
            key = self.secret_store.get(profile.api_key_ref)
            if not key and urlsplit(profile.base_url or "").hostname in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                key = "local-no-auth"
            if not key:
                raise AdapterError(
                    "provider_auth_error", "请配置系统钥匙串 API Key 或 Key 环境变量"
                )
            return {"type": "api_key", "key": key}
        if not self.secret_store.available():
            raise AdapterError("provider_auth_error", "订阅 OAuth 需要系统钥匙串，禁止回退明文存储")
        stored = self.secret_store.get(_oauth_reference(params.provider))
        try:
            previous = json.loads(stored) if stored else None
        except ValueError:
            previous = None
        credential = _credential_from_file(params.provider)
        # Import a newer login without rolling back a token already refreshed in keyring.
        if isinstance(previous, dict) and (
            not credential
            or float(previous.get("expires", 0)) >= float(credential.get("expires", 0))
        ):
            credential = previous
        if not isinstance(credential, dict) or not all(
            credential.get(key) for key in ("access", "refresh", "expires")
        ):
            raise AdapterError(
                "provider_auth_error",
                "未找到订阅凭证。请在 Pi 中 /login；GPT 也可使用已有 Codex 登录，然后重新检查",
            )
        if credential != previous:
            self._save_oauth(params.provider, credential)
        return credential

    def _save_oauth(self, provider: str, credential: dict[str, Any]) -> None:
        if not self.secret_store.available():
            raise AdapterError(
                "provider_auth_error", "系统钥匙串不可用，无法安全保存 OAuth 刷新结果"
            )
        keyring.set_password(SERVICE_NAME, _oauth_reference(provider), json.dumps(credential))

    async def _invoke(
        self,
        payload: dict[str, Any],
        cancel: asyncio.Event,
        timeout_seconds: float,
        emit: EventSink | None = None,
        profile: AdapterProfile | None = None,
        request: GenerationRequest | None = None,
    ) -> dict[str, Any]:
        node = shutil.which("node")
        if not node or not (RUNTIME / "node_modules" / "@earendil-works" / "pi-ai").exists():
            raise AdapterError(
                "profile_unavailable",
                "Pi 运行时未安装：pnpm --dir runtime/pi install --frozen-lockfile；"
                "需要 Node >=22.19",
            )
        if cancel.is_set():
            raise AdapterError("cancelled", "模型调用已取消")
        credential = payload.get("credential", {})
        with tempfile.TemporaryDirectory(prefix="sql-arena-pi-") as directory:
            process = await asyncio.create_subprocess_exec(
                node,
                str(RUNTIME / "bridge.mjs"),
                cwd=directory,
                env=safe_subprocess_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=MAX_RAW_OUTPUT_BYTES + 1,
            )

            async def exchange() -> dict[str, Any]:
                assert process.stdin and process.stdout and process.stderr
                process.stdin.write(json.dumps(payload, ensure_ascii=False).encode())
                await process.stdin.drain()
                process.stdin.close()
                received = 0
                result: dict[str, Any] | None = None
                while line := await process.stdout.readline():
                    received += len(line)
                    if received > MAX_RAW_OUTPUT_BYTES:
                        raise AdapterError("provider_output_too_large", "Pi 输出超过 1 MiB")
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        raise AdapterError(
                            "provider_protocol_error", "Pi 桥接输出不是合法 JSON"
                        ) from exc
                    if not isinstance(event, dict):
                        raise AdapterError("provider_protocol_error", "Pi 桥接事件结构无效")
                    event_type = event.get("type")
                    if event_type == "requested" and emit and profile and request:
                        await emit(
                            "provider.requested",
                            "info",
                            provider_request_payload(
                                profile,
                                request,
                                transport="pi",
                                invocation=_scrub(event["evidence"], credential),
                            ),
                        )
                    elif event_type == "delta" and emit:
                        await emit(
                            "provider.delta", "info", {"text": _scrub(event["text"], credential)}
                        )
                    elif event_type in {"result", "error"}:
                        result = event
                await process.wait()
                if result is None:
                    # Never echo process stderr: SDK/auth failures may contain credentials.
                    raise AdapterError(
                        "provider_runtime_error", "Pi 运行时未返回结果，请检查 Node 版本与依赖安装"
                    )
                if result.get("type") == "error":
                    error = result.get("error", {})
                    raise AdapterError(
                        str(error.get("code", "provider_error")),
                        str(_scrub(error.get("message", "Pi 调用失败"), credential)),
                        _scrub(result.get("evidence", {}), credential),
                    )
                if process.returncode:
                    raise AdapterError("provider_runtime_error", "Pi 运行时异常退出")
                return result

            async def drain_stderr() -> None:
                assert process.stderr
                while await process.stderr.read(8192):
                    pass

            worker = asyncio.create_task(exchange())
            stderr = asyncio.create_task(drain_stderr())
            cancelled = asyncio.create_task(cancel.wait())
            try:
                done, _ = await asyncio.wait(
                    {worker, cancelled},
                    timeout=timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if worker in done:
                    return await worker
                if cancelled in done:
                    raise AdapterError("cancelled", "模型调用已取消")
                raise AdapterError("provider_timeout", f"Pi 调用超过 {timeout_seconds:g} 秒")
            finally:
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        await asyncio.wait_for(process.wait(), 2)
                    except (ProcessLookupError, TimeoutError):
                        if process.returncode is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            await process.wait()
                for task in (worker, stderr, cancelled):
                    task.cancel()
                await asyncio.gather(worker, stderr, cancelled, return_exceptions=True)

    def _payload(
        self,
        profile: AdapterProfile,
        params: PiParameters,
        credential: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "model_id": profile.model_id,
            "base_url": profile.base_url,
            "parameters": params.model_dump(exclude_none=True),
            "credential": credential,
        }

    async def check(self, profile: AdapterProfile) -> AdapterHealth:
        try:
            params = validate_pi_profile(profile)
            credential = await asyncio.to_thread(self._credential, profile, params)
            result = await self._invoke(
                self._payload(profile, params, credential, "check"), asyncio.Event(), 20
            )
            return AdapterHealth(
                status="healthy",
                message="Pi 本地配置就绪；未发送模型请求，实际模型权限在运行时验证",
                version=f"pi-ai {PI_VERSION}",
                details=result["evidence"],
            )
        except (AdapterError, ValueError) as exc:
            return AdapterHealth(
                status="unavailable",
                message=str(exc),
                version=f"pi-ai {PI_VERSION}",
                details={"code": getattr(exc, "code", "profile_incompatible")},
            )

    async def generate(
        self,
        profile: AdapterProfile,
        request: GenerationRequest,
        emit: EventSink,
        cancel: asyncio.Event,
    ) -> GenerationResponse:
        params = validate_pi_profile(profile)
        if params.auth_mode == "oauth":
            lock = _OAUTH_LOCKS.setdefault(params.provider, asyncio.Lock())
            async with lock:
                credential = await asyncio.to_thread(self._credential, profile, params)
                refreshed = await self._invoke(
                    self._payload(profile, params, credential, "auth"),
                    cancel,
                    min(params.timeout_seconds, 60),
                )
                if refreshed["credential"] != credential:
                    credential = refreshed["credential"]
                    await asyncio.to_thread(self._save_oauth, params.provider, credential)
        else:
            credential = await asyncio.to_thread(self._credential, profile, params)
        payload = self._payload(profile, params, credential, "generate")
        payload.update(prompt=request.prompt, output_schema=request.output_schema)
        result = await self._invoke(payload, cancel, params.timeout_seconds, emit, profile, request)
        evidence = _scrub(result["evidence"], credential)
        await emit(
            "provider.completed",
            "info",
            {
                "status": "completed",
                **evidence,
                "elapsed_ms": result["latency_ms"],
                "token_usage": result["token_usage"],
            },
        )
        raw = str(_scrub(result["raw_output"], credential))
        parsed, strict = parse_generation_output(raw)
        return GenerationResponse(
            raw_output=raw,
            parsed_output=parsed,
            resolved_model_id=None,
            token_usage=result["token_usage"],
            provider_request_id=result.get("provider_request_id"),
            latency_ms=result["latency_ms"],
            protocol_strict=strict,
        )
