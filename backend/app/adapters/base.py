from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
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
    adapter_kind: Literal["openai_compatible", "codex_cli", "claude_cli", "gemini_cli"]
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
    resolved_model_id: str
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
    transport: Literal["http", "cli"],
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


@dataclass(slots=True)
class CliResult:
    stdout: str
    stderr: str
    returncode: int
    latency_ms: float


async def run_cli(
    command: list[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    stdin: str | None,
    cancel: asyncio.Event,
    timeout_seconds: float,
) -> CliResult:
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=dict(environment),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    communicate_task = asyncio.create_task(
        process.communicate(stdin.encode() if stdin is not None else None)
    )
    cancel_task = asyncio.create_task(cancel.wait())
    timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
    done, pending = await asyncio.wait(
        {communicate_task, cancel_task, timeout_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    if communicate_task not in done:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        communicate_task.cancel()
        if cancel_task in done:
            raise AdapterError("cancelled", "模型调用已取消")
        raise AdapterError("provider_timeout", f"模型调用超过 {timeout_seconds:.0f} 秒")
    stdout_bytes, stderr_bytes = communicate_task.result()
    if len(stdout_bytes) > MAX_RAW_OUTPUT_BYTES:
        raise AdapterError("provider_output_too_large", "模型原始输出超过 1 MiB")
    return CliResult(
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        returncode=int(process.returncode or 0),
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def json_lines(raw: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterError("provider_protocol_error", f"JSONL 第 {line_number} 行无效") from exc
        if not isinstance(item, dict):
            raise AdapterError("provider_protocol_error", "JSONL 事件必须为对象")
        records.append(item)
    return records
