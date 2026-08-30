from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from backend.app.adapters.base import (
    AdapterError,
    AdapterProfile,
    parse_generation_output,
    provider_request_payload,
    run_cli,
    safe_subprocess_env,
)
from backend.app.adapters.claude_cli import parse_claude_jsonl
from backend.app.adapters.codex_cli import (
    _resolve_codex_binary,
    build_seatbelt_profile,
    parse_codex_jsonl,
)
from backend.app.adapters.gemini_cli import (
    _gemini_exit_code,
    _safe_gemini_settings,
    parse_gemini_jsonl,
)
from backend.app.adapters.openai_compatible import (
    OpenAIStreamState,
    trust_environment_proxy,
)
from backend.app.domain import GenerationRequest
from backend.app.security import redact_secrets

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_SQL = "SELECT 1 AS value"

def test_provider_request_payload_preserves_context_and_redacts_secrets() -> None:
    profile = AdapterProfile(
        id=1,
        name="test",
        adapter_kind="openai_compatible",
        model_id="model-a",
        base_url="https://api.example.com/v1",
        response_mode="json_schema",
        api_key_ref="env:API_KEY",
        parameters={"temperature": 0, "api_key": "private-token"},
    )
    request = GenerationRequest(
        case_key="case-a",
        prompt="full prompt",
        output_schema={"type": "object"},
    )

    payload = provider_request_payload(
        profile,
        request,
        transport="http",
        invocation={"method": "POST", "body": {"prompt": "full prompt"}},
    )

    assert payload["context"] == {
        "prompt": "full prompt",
        "output_schema": {"type": "object"},
    }
    assert payload["invocation"] == {
        "method": "POST",
        "body": {"prompt": "full prompt"},
    }
    assert payload["parameters"] == {"temperature": 0, "api_key": "[REDACTED]"}
    assert "private-token" not in str(payload)


def test_loopback_compatible_endpoint_bypasses_environment_proxy() -> None:
    assert trust_environment_proxy("http://127.0.0.1:8765/v1") is False
    assert trust_environment_proxy("http://localhost:8765/v1") is False
    assert trust_environment_proxy("http://[::1]:8765/v1") is False
    assert trust_environment_proxy("https://api.example.com/v1") is True


def test_codex_js_launcher_resolves_single_native_binary(tmp_path: Path) -> None:
    package = tmp_path / "lib/node_modules/@openai/codex"
    launcher = package / "bin/codex.js"
    native = package / "node_modules/@openai/codex-darwin-arm64/vendor/aarch64/bin/codex"
    launcher.parent.mkdir(parents=True)
    native.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    native.write_text("", encoding="utf-8")
    native.chmod(0o755)

    assert _resolve_codex_binary(str(launcher)) == native.resolve()


def test_codex_seatbelt_denies_by_default_and_only_allows_case_inputs(tmp_path: Path) -> None:
    codex = (tmp_path / "codex").resolve()
    case_dir = (tmp_path / "case").resolve()
    auth = (tmp_path / "auth.json").resolve()
    policy = build_seatbelt_profile(codex, case_dir, auth)

    project_root = Path(__file__).resolve().parents[1]
    assert "(deny default)" in policy
    assert f'(require-not (subpath "{Path.home()}"))' in policy
    assert f'(deny file-read* (subpath "{project_root}"))' in policy
    assert f'(allow file-read* (literal "{auth}"))' in policy
    assert f'(allow file-read* (subpath "{case_dir}"))' in policy
    assert f'(allow file-write* (subpath "{case_dir}"))' in policy

def test_recorded_codex_jsonl_contract() -> None:
    raw, usage, model, request_id = parse_codex_jsonl(
        (FIXTURES / "codex.jsonl").read_text(), "requested"
    )
    parsed, strict = parse_generation_output(raw)
    assert parsed.sql == EXPECTED_SQL
    assert strict
    assert model == "gpt-5.3-codex"
    assert usage["output_tokens"] == 30
    assert request_id == "codex-request-1"


def test_recorded_claude_stream_json_contract() -> None:
    raw, usage, model, request_id = parse_claude_jsonl(
        (FIXTURES / "claude.jsonl").read_text(), "requested"
    )
    assert parse_generation_output(raw)[0].sql == EXPECTED_SQL
    assert model == "claude-sonnet-4-5"
    assert usage["input_tokens"] == 110
    assert request_id == "claude-request-1"


def test_recorded_gemini_stream_json_contract() -> None:
    raw, usage, model, request_id = parse_gemini_jsonl(
        (FIXTURES / "gemini.jsonl").read_text(), "requested"
    )
    assert parse_generation_output(raw)[0].sql == EXPECTED_SQL
    assert model == "gemini-2.5-pro"
    assert usage["output_tokens"] == 20
    assert request_id == "gemini-request-1"


def test_recorded_openai_sse_contract() -> None:
    state = OpenAIStreamState("requested")
    for line in (FIXTURES / "openai.sse").read_text().splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        state.consume(data)
    assert parse_generation_output(state.raw_output)[0].sql == EXPECTED_SQL
    assert state.resolved_model == "model-a"
    assert state.usage["completion_tokens"] == 18
    assert state.provider_request_id == "chatcmpl-1"


def test_only_single_json_fence_is_recoverable() -> None:
    raw = (
        '```json\n{"plan":{"grain":"single row","sources":[],"joins":[],'
        '"filters":[],"metrics":["value"],"steps":["select constant"],"risks":[]},'
        '"sql":"SELECT 1","summary":"ok","assumptions":[]}\n```'
    )
    parsed, strict = parse_generation_output(raw)
    assert parsed.sql == "SELECT 1"
    assert not strict
    with pytest.raises(AdapterError) as error:
        parse_generation_output(f"prefix {raw}")
    assert error.value.code == "output_contract_error"


def test_gemini_settings_preserve_only_auth_selection(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}},"skills":{"enabled":true}}',
        encoding="utf-8",
    )

    settings = _safe_gemini_settings(path)

    assert settings["security"] == {"auth": {"selectedType": "oauth-personal"}}
    assert settings["skills"] == {"enabled": False}
    assert _gemini_exit_code("Please set an Auth method") == "provider_auth_error"
    assert _gemini_exit_code("unexpected exit") == "provider_cli_error"


def test_gemini_tool_call_is_policy_violation() -> None:
    with pytest.raises(AdapterError) as error:
        parse_gemini_jsonl('{"type":"tool_call","tool_name":"read_file"}\n', "requested")
    assert error.value.code == "adapter_policy_violation"


def test_secret_redaction_is_recursive() -> None:
    redacted = redact_secrets(
        {
            "authorization": "Bearer private-token",
            "nested": ["api_key=private-token", {"token": "private-token"}],
        }
    )
    assert "private-token" not in str(redacted)


@pytest.mark.asyncio
async def test_cli_output_limit_terminates_oversized_response(tmp_path: Path) -> None:
    with pytest.raises(AdapterError) as error:
        await run_cli(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * (1024 * 1024 + 1))"],
            cwd=tmp_path,
            environment=safe_subprocess_env(),
            stdin=None,
            cancel=asyncio.Event(),
            timeout_seconds=5,
        )
    assert error.value.code == "provider_output_too_large"

@pytest.mark.asyncio
async def test_cli_timeout_terminates_process_group(tmp_path: Path) -> None:
    with pytest.raises(AdapterError) as error:
        await run_cli(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=tmp_path,
            environment=safe_subprocess_env(),
            stdin=None,
            cancel=asyncio.Event(),
            timeout_seconds=0.05,
        )
    assert error.value.code == "provider_timeout"
