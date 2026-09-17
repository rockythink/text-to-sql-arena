from __future__ import annotations

import asyncio
import base64
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import keyring
import pytest
from keyring.backend import KeyringBackend

from backend.app.adapters.base import AdapterError, AdapterProfile
from backend.app.adapters.pi import PiAdapter
from backend.app.domain import GenerationOutput, GenerationRequest
from backend.app.security import SERVICE_NAME

ANSWER = {
    "plan": {
        "grain": "one row",
        "sources": [],
        "joins": [],
        "filters": [],
        "metrics": ["value"],
        "steps": ["select constant"],
        "risks": [],
    },
    "sql": "SELECT 1 AS value",
    "summary": "constant",
    "assumptions": [],
}

ProviderServer = tuple[str, list[dict[str, Any]], dict[str, str]]


@pytest.fixture
def provider_server() -> Iterator[ProviderServer]:
    requests: list[dict[str, Any]] = []
    mode = {"value": "answer"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if mode["value"] == "rate_limit":
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"rate limit audit-secret-value"}}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if mode["value"] == "tool":
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tool-1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"command":"echo unsafe"}'},
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": json.dumps(ANSWER)}
                finish = "stop"
            for chunk in [
                {
                    "id": "response-1",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": "response-1",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
            ]:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, mode
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def profile(url: str) -> AdapterProfile:
    return AdapterProfile(
        id=1,
        name="local proof",
        adapter_kind="pi",
        model_id="local-proof",
        base_url=url,
        response_mode="text",
        api_key_ref="env:PI_AUDIT_KEY",
        parameters={"provider": "openai", "auth_mode": "api_key", "timeout_seconds": 10},
    )


def request(prompt: str = "Select a constant") -> GenerationRequest:
    return GenerationRequest(
        case_key="local", prompt=prompt, output_schema=GenerationOutput.model_json_schema()
    )


@pytest.mark.asyncio
async def test_real_pi_calls_are_isolated_and_have_no_tools(
    provider_server: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, calls, _ = provider_server
    monkeypatch.setenv("PI_AUDIT_KEY", "audit-secret-value")
    events = []

    async def emit(kind: str, level: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    adapter = PiAdapter()
    health = await adapter.check(profile(url))
    assert health.status == "healthy" and not calls
    first = await adapter.generate(
        profile(url), request("FIRST_CASE_SENTINEL"), emit, asyncio.Event()
    )
    second = await adapter.generate(
        profile(url), request("SECOND_CASE_SENTINEL"), emit, asyncio.Event()
    )
    assert first.parsed_output.sql == second.parsed_output.sql == "SELECT 1 AS value"
    assert len(calls) == 2
    assert all(not call.get("tools") for call in calls)
    assert len(calls[1]["messages"]) == 2
    assert "FIRST_CASE_SENTINEL" not in json.dumps(calls[1])
    assert calls[0]["messages"][0] == calls[1]["messages"][0]
    assert "audit-secret-value" not in json.dumps(events)
    completed = [payload for kind, payload in events if kind == "provider.completed"]
    assert all(
        item["generation_attempts"] == 1 and item["tool_calls_observed"] == 0 for item in completed
    )


@pytest.mark.asyncio
async def test_real_pi_does_not_retry_rate_limits_or_leak_credentials(
    provider_server: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, calls, mode = provider_server
    mode["value"] = "rate_limit"
    monkeypatch.setenv("PI_AUDIT_KEY", "audit-secret-value")

    async def emit(*args: Any) -> None:
        pass

    with pytest.raises(AdapterError) as caught:
        await PiAdapter().generate(profile(url), request(), emit, asyncio.Event())
    assert len(calls) == 1
    assert caught.value.details["generation_attempts"] == 1
    assert "audit-secret-value" not in str(caught.value)


@pytest.mark.asyncio
async def test_real_pi_refuses_tool_calls_without_continuation(
    provider_server: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, calls, mode = provider_server
    mode["value"] = "tool"
    monkeypatch.setenv("PI_AUDIT_KEY", "audit-secret-value")

    async def emit(*args: Any) -> None:
        pass

    with pytest.raises(AdapterError) as caught:
        await PiAdapter().generate(profile(url), request(), emit, asyncio.Event())
    assert caught.value.code == "adapter_policy_violation"
    assert len(calls) == 1
    assert caught.value.details["tool_calls_observed"] >= 1


@pytest.mark.asyncio
async def test_oauth_check_imports_new_login_without_rolling_back_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self) -> None:
            self.values: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self.values.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self.values[service, username] = password

    monkeypatch.setenv("HOME", str(tmp_path))
    pi_file = tmp_path / ".pi/agent/auth.json"
    pi_file.parent.mkdir(parents=True)
    old_login = {"type": "oauth", "access": "old-login", "refresh": "old-refresh", "expires": 1000}
    pi_file.write_text(json.dumps({"openai-codex": old_login}))
    source_before = pi_file.read_bytes()
    reference = "keyring:pi-oauth:openai-codex"
    refreshed = {**old_login, "access": "refreshed", "refresh": "rotated", "expires": 2000}
    previous_backend = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        keyring.set_password(SERVICE_NAME, reference, json.dumps(refreshed))
        adapter = PiAdapter()
        oauth_profile = AdapterProfile(
            id=1,
            name="OAuth",
            adapter_kind="pi",
            model_id="gpt-5.6-luna",
            response_mode="text",
            parameters={"provider": "openai-codex", "auth_mode": "oauth"},
        )
        assert (await adapter.check(oauth_profile)).status == "healthy"
        assert (
            json.loads(keyring.get_password(SERVICE_NAME, reference) or "{}")["access"]
            == "refreshed"
        )
        claims = base64.urlsafe_b64encode(b'{"exp":3}').decode().rstrip("=")
        new_access = f"header.{claims}.signature"
        codex_file = tmp_path / ".codex/auth.json"
        codex_file.parent.mkdir()
        codex_file.write_text(
            json.dumps(
                {
                    "tokens": {
                        "access_token": new_access,
                        "refresh_token": "new-refresh",
                        "account_id": "account",
                    }
                }
            )
        )
        codex_before = codex_file.read_bytes()
        assert (await adapter.check(oauth_profile)).status == "healthy"
        assert (
            json.loads(keyring.get_password(SERVICE_NAME, reference) or "{}")["access"]
            == new_access
        )
        assert pi_file.read_bytes() == source_before
        assert codex_file.read_bytes() == codex_before
    finally:
        keyring.set_keyring(previous_backend)
