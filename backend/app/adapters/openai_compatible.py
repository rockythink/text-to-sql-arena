from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from backend.app.adapters.base import (
    AdapterError,
    AdapterHealth,
    AdapterProfile,
    EventSink,
    GenerationResponse,
    parse_generation_output,
    provider_request_payload,
)
from backend.app.domain import GenerationRequest
from backend.app.security import SecretStore

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def trust_environment_proxy(base_url: str) -> bool:
    return urlsplit(base_url).hostname not in LOOPBACK_HOSTS


class OpenAIStreamState:
    def __init__(self, requested_model: str) -> None:
        self.chunks: list[str] = []
        self.usage: dict[str, int] = {}
        self.provider_request_id: str | None = None
        self.resolved_model = requested_model

    def consume(self, data: str) -> str | None:
        event = json.loads(data)
        self.provider_request_id = event.get("id", self.provider_request_id)
        self.resolved_model = event.get("model", self.resolved_model)
        if isinstance(event.get("usage"), dict):
            self.usage = {
                str(key): int(value)
                for key, value in event["usage"].items()
                if isinstance(value, int)
            }
        for choice in event.get("choices", []):
            delta = choice.get("delta", {}).get("content")
            if delta:
                text = str(delta)
                self.chunks.append(text)
                return text
        return None

    @property
    def raw_output(self) -> str:
        return "".join(self.chunks)


class OpenAICompatibleAdapter:
    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self.secret_store = secret_store or SecretStore()

    async def check(self, profile: AdapterProfile) -> AdapterHealth:
        if not profile.base_url:
            return AdapterHealth(status="incompatible", message="缺少 Base URL")
        secret = self.secret_store.get(profile.api_key_ref)
        if not secret:
            return AdapterHealth(status="unavailable", message="API Key 不可用")
        try:
            async with httpx.AsyncClient(
                timeout=10, trust_env=trust_environment_proxy(profile.base_url)
            ) as client:
                response = await client.get(
                    profile.base_url.rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {secret}"},
                )
            if response.status_code >= 400:
                return AdapterHealth(
                    status="error",
                    message=f"模型列表探测返回 HTTP {response.status_code}",
                )
            return AdapterHealth(
                status="healthy",
                message="OpenAI-compatible 接口可用",
                resolved_model_id=profile.model_id,
            )
        except httpx.HTTPError as exc:
            return AdapterHealth(status="error", message=str(exc))

    async def generate(
        self,
        profile: AdapterProfile,
        request: GenerationRequest,
        emit: EventSink,
        cancel: asyncio.Event,
    ) -> GenerationResponse:
        if not profile.base_url:
            raise AdapterError("profile_incompatible", "缺少 Base URL")
        secret = self.secret_store.get(profile.api_key_ref)
        if not secret:
            raise AdapterError("provider_auth_error", "API Key 不可用")
        body: dict[str, Any] = {
            "model": profile.model_id,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": True,
            **profile.parameters,
        }
        if profile.response_mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "text_to_sql",
                    "strict": True,
                    "schema": request.output_schema,
                },
            }
        elif profile.response_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        await emit(
            "provider.requested",
            "info",
            provider_request_payload(
                profile,
                request,
                transport="http",
                invocation={
                    "method": "POST",
                    "path": "/chat/completions",
                    "body": body,
                },
            ),
        )
        started = time.perf_counter()
        state = OpenAIStreamState(profile.model_id)
        try:
            async with httpx.AsyncClient(
                timeout=None, trust_env=trust_environment_proxy(profile.base_url)
            ) as client:
                async with client.stream(
                    "POST",
                    profile.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {secret}"},
                    json=body,
                ) as response:
                    if response.status_code >= 400:
                        error_body = (await response.aread()).decode(errors="replace")
                        code = (
                            "provider_capability_error"
                            if response.status_code in {400, 404, 422}
                            else "provider_request_error"
                        )
                        raise AdapterError(code, f"HTTP {response.status_code}: {error_body}")
                    async for line in response.aiter_lines():
                        if cancel.is_set():
                            raise AdapterError("cancelled", "模型调用已取消")
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        delta = state.consume(data)
                        if delta:
                            await emit("provider.delta", "info", {"text": delta})
        except httpx.HTTPError as exc:
            raise AdapterError("provider_request_error", str(exc)) from exc
        raw_output = state.raw_output
        parsed, strict = parse_generation_output(raw_output)
        latency = (time.perf_counter() - started) * 1000
        await emit(
            "provider.completed",
            "info",
            {
                "status": "completed",
                "elapsed_ms": latency,
                "token_usage": state.usage,
            },
        )
        return GenerationResponse(
            raw_output=raw_output,
            parsed_output=parsed,
            resolved_model_id=state.resolved_model,
            token_usage=state.usage,
            provider_request_id=state.provider_request_id,
            latency_ms=latency,
            protocol_strict=strict,
        )
