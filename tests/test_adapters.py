from __future__ import annotations

import pytest

from backend.app.adapters.base import (
    AdapterError,
    AdapterProfile,
    parse_generation_output,
    provider_request_payload,
)
from backend.app.domain import GenerationRequest
from backend.app.security import redact_secrets


def test_provider_request_payload_redacts_profile_secrets() -> None:
    profile = AdapterProfile(
        id=1,
        name="test",
        adapter_kind="pi",
        model_id="model-a",
        base_url="https://api.example.com/v1",
        response_mode="text",
        api_key_ref="env:API_KEY",
        parameters={"provider": "openai", "api_key": "private-token"},
    )
    request = GenerationRequest(
        case_key="case-a",
        prompt="full prompt",
        output_schema={"type": "object"},
    )

    payload = provider_request_payload(
        profile,
        request,
        transport="pi",
        invocation={"method": "POST", "body": {"prompt": "full prompt"}},
    )

    assert "private-token" not in str(payload)


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


def test_secret_redaction_is_recursive() -> None:
    redacted = redact_secrets(
        {
            "authorization": "Bearer private-token",
            "nested": ["api_key=private-token", {"token": "private-token"}],
        }
    )
    assert "private-token" not in str(redacted)
