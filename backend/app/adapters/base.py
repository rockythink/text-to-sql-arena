from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.domain import GenerationOutput, GenerationRequest
from backend.app.security import redact_secrets

MAX_RAW_OUTPUT_BYTES = 1024 * 1024
EVENT_TYPES = frozenset(
    {
        "run.created",
        "run.started",
        "model.started",
        "case.started",
        "prompt.built",
        "plan.completed",
        "provider.requested",
        "provider.delta",
        "provider.completed",
        "sql.parsed",
        "sql.rejected",
        "sql.executed",
        "result.compared",
        "score.completed",
        "case.failed",
        "model.completed",
        "run.completed",
        "run.cancelled",
        "run.interrupted",
    }
)
SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


class AdapterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    name: str
    adapter_kind: Literal["pi", "openai_compatible", "codex_cli", "claude_cli", "gemini_cli"]
    model_id: str
    base_url: str | None = None
    response_mode: Literal["json_schema", "json_object", "text"] = "json_schema"
    api_key_ref: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class AdapterHealth(BaseModel):
    status: Literal["healthy", "unavailable", "incompatible", "error"]
    message: str
    resolved_model_id: str | None = None
    version: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class GenerationResponse(BaseModel):
    raw_output: str
    parsed_output: GenerationOutput
    resolved_model_id: str | None
    token_usage: dict[str, int] = Field(default_factory=dict)
    provider_request_id: str | None = None
    latency_ms: float
    protocol_strict: bool


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = redact_secrets(details or {})
        super().__init__(str(redact_secrets(message)))


EventSink = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def provider_request_payload(
    profile: AdapterProfile,
    request: GenerationRequest,
    *,
    transport: Literal["pi", "http"],
    invocation: dict[str, Any],
) -> dict[str, Any]:
    """Build the secret-redacted request envelope persisted in run events."""
    payload = redact_secrets(
        {
            "status": "running",
            "transport": transport,
            "adapter_kind": profile.adapter_kind,
            "requested_model_id": profile.model_id,
            "response_mode": profile.response_mode,
            "parameters": profile.parameters,
            "context": {
                "prompt": request.prompt,
                "output_schema": request.output_schema,
            },
            "invocation": invocation,
        }
    )
    if not isinstance(payload, dict):
        raise TypeError("redacted provider request must remain an object")
    return payload


class ModelAdapter(Protocol):
    async def check(self, profile: AdapterProfile) -> AdapterHealth: ...

    async def generate(
        self,
        profile: AdapterProfile,
        request: GenerationRequest,
        emit: EventSink,
        cancel: asyncio.Event,
    ) -> GenerationResponse: ...


def safe_subprocess_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    if extra:
        environment.update(extra)
    return environment


def parse_generation_output(raw: str) -> tuple[GenerationOutput, bool]:
    try:
        return GenerationOutput.model_validate_json(raw), True
    except ValidationError as strict_error:
        fenced = re.fullmatch(r"\s*```json\s*\n?(.*?)\n?```\s*", raw, flags=re.DOTALL)
        if fenced is None:
            raise AdapterError(
                "output_contract_error",
                "模型输出不是严格 JSON，且不符合单层 json fence 恢复规则",
                {
                    "validation": strict_error.errors(include_input=False),
                    "raw_output": raw[:MAX_RAW_OUTPUT_BYTES],
                },
            ) from strict_error
        try:
            return GenerationOutput.model_validate_json(fenced.group(1)), False
        except ValidationError as fenced_error:
            raise AdapterError(
                "output_contract_error",
                "模型输出不符合结构化契约",
                {
                    "validation": fenced_error.errors(include_input=False),
                    "raw_output": raw[:MAX_RAW_OUTPUT_BYTES],
                },
            ) from fenced_error
