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
from backend.app.adapters.cli_common import probe_cli
from backend.app.domain import GenerationRequest

CLAUDE_FLAGS = (
    "--restricted",
    "--tools",
    "--disallowedTools",
    "--json-schema",
    "--output-format",
)


def parse_claude_jsonl(
    raw: str, requested_model: str
) -> tuple[str, dict[str, int], str, str | None]:
    chunks: list[str] = []
    usage: dict[str, int] = {}
    resolved_model = requested_model
    request_id: str | None = None
    for event in json_lines(raw):
        event_type = event.get("type")
        if event_type in {"assistant", "content_block_delta"}:
            message = event.get("message")
            if isinstance(message, dict):
                resolved_model = str(message.get("model") or resolved_model)
                content = message.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text")
                            if isinstance(text, str):
                                chunks.append(text)
            delta = event.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                chunks.append(delta["text"])
        elif event_type == "result":
            request_id = str(event.get("session_id") or "") or request_id
            if isinstance(event.get("result"), str) and not chunks:
                chunks.append(event["result"])
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = {
                    str(key): int(value)
                    for key, value in raw_usage.items()
                    if isinstance(value, int)
                }
            if event.get("is_error"):
                raise AdapterError("provider_cli_error", json.dumps(event, ensure_ascii=False))
    if not chunks:
        raise AdapterError("provider_protocol_error", "Claude stream-json 未包含可见文本")
    return "".join(chunks), usage, resolved_model, request_id


class ClaudeCliAdapter:
    async def check(self, profile: AdapterProfile) -> AdapterHealth:
        health = await probe_cli("claude", CLAUDE_FLAGS)
        if health.status == "healthy":
            health.resolved_model_id = profile.model_id
            health.details["tools"] = []
            health.details["restricted"] = True
        return health

    async def generate(
        self,
        profile: AdapterProfile,
        request: GenerationRequest,
        emit: EventSink,
        cancel: asyncio.Event,
    ) -> GenerationResponse:
        claude = shutil.which("claude")
        if not claude:
            raise AdapterError("profile_unavailable", "command not found: claude")
        schema = json.dumps(request.output_schema, separators=(",", ":"))
        command = [
            claude,
            "-p",
            "--output-format",
            "stream-json",
            "--json-schema",
            schema,
            "--model",
            profile.model_id,
            "--tools",
            "",
            "--disallowedTools",
            "mcp__*",
            "--restricted",
            "--max-turns",
            "1",
            request.prompt,
        ]
        await emit("provider.requested", "info", {"status": "running"})
        with tempfile.TemporaryDirectory(prefix="llm-test-claude-") as temp_name:
            result = await run_cli(
                command,
                cwd=Path(temp_name),
                environment=safe_subprocess_env(),
                stdin=None,
                cancel=cancel,
                timeout_seconds=float(profile.parameters.get("timeout_seconds", 120)),
            )
        if result.returncode != 0:
            raise AdapterError(
                "provider_cli_error",
                f"Claude 退出码 {result.returncode}: {result.stderr}",
            )
        raw_output, usage, resolved_model, request_id = parse_claude_jsonl(
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
