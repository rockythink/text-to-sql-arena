from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.app.adapters.base import (
    AdapterError,
    AdapterHealth,
    AdapterProfile,
    EventSink,
    GenerationResponse,
)
from backend.app.adapters.registry import adapter_registry
from backend.app.domain import GenerationOutput, GenerationRequest, QueryPlan
from backend.app.main import app
from backend.app.services.evidence import export_all_evidence, verify_evidence

TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled", "interrupted"}
DATA_DIR = Path(__file__).parents[1] / "backend" / "app" / "data" / "retail-analytics-v1"
REFERENCES = {
    case["stable_key"]: case["reference_sql"]
    for case in yaml.safe_load((DATA_DIR / "cases.yaml").read_text())
}


class FixtureAdapter:
    async def check(self, profile: AdapterProfile) -> AdapterHealth:
        return AdapterHealth(
            status="healthy",
            message="fixture ready",
            resolved_model_id=profile.model_id,
            version="fixture-1",
        )

    async def generate(
        self,
        profile: AdapterProfile,
        request: GenerationRequest,
        emit: EventSink,
        cancel: asyncio.Event,
    ) -> GenerationResponse:
        await emit("provider.requested", "info", {"model": profile.model_id})
        if profile.model_id == "always-fail":
            raise AdapterError(
                "fixture_failure",
                "planned model failure api_key=private-token at /Users/private/secret",
            )
        if profile.model_id == "slow-model":
            for _ in range(100):
                if cancel.is_set():
                    raise AdapterError("cancelled", "cancelled")
                await asyncio.sleep(0.01)
        output = GenerationOutput(
            plan=QueryPlan(
                grain="reference result grain",
                sources=[],
                joins=[],
                filters=[],
                metrics=["reference result"],
                steps=["execute reference-equivalent query"],
                risks=[],
            ),
            sql=REFERENCES[request.case_key],
            summary="fixture reference answer",
            assumptions=[],
        )
        await emit("provider.completed", "info", {"model": profile.model_id})
        return GenerationResponse(
            raw_output=output.model_dump_json(),
            parsed_output=output,
            resolved_model_id=profile.model_id,
            token_usage={"input_tokens": 10, "output_tokens": 20},
            latency_ms=1.0,
            provider_request_id=f"fixture-{profile.model_id}-{request.case_key}",
            protocol_strict=True,
        )


def csrf(client: TestClient) -> dict[str, str]:
    token = client.get("/api/bootstrap").json()["csrf_token"]
    return {"X-CSRF-Token": token}


def create_profile(
    client: TestClient, headers: dict[str, str], name: str, kind: str, model_id: str
) -> int:
    response = client.post(
        "/api/model-profiles",
        headers=headers,
        json={
            "name": name,
            "adapter_kind": kind,
            "model_id": model_id,
            "response_mode": "text",
            "parameters": {},
        },
    )
    assert response.status_code == 200, response.text
    profile_id = int(response.json()["id"])
    checked = client.post(f"/api/model-profiles/{profile_id}/check", headers=headers)
    assert checked.status_code == 200 and checked.json()["health_status"] == "healthy"
    return profile_id


def wait_for_run(client: TestClient, run_id: int, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/runs/{run_id}")
        assert snapshot.status_code == 200, snapshot.text
        payload = snapshot.json()
        assert isinstance(payload, dict)
        if payload["status"] in TERMINAL:
            return cast(dict[str, Any], payload)
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


def test_two_model_state_machine_resume_and_report(
    monkeypatch: Any, tmp_path: Path
) -> None:
    fixture = FixtureAdapter()
    monkeypatch.setitem(adapter_registry._adapters, "codex_cli", fixture)
    monkeypatch.setitem(adapter_registry._adapters, "gemini_cli", fixture)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = csrf(client)
        good = create_profile(client, headers, "Reference", "codex_cli", "reference-model")
        bad = create_profile(client, headers, "Failure", "gemini_cli", "always-fail")
        suite = client.get("/api/suites").json()[0]["versions"][0]
        preview = client.get(
            f"/api/suite-versions/{suite['id']}/prompt-preview?case_id={suite['cases'][0]['id']}"
        )
        assert preview.status_code == 200, preview.text
        assert suite["cases"][0]["question"] in preview.json()["prompt"]
        assert preview.json()["output_schema"]["required"] == [
            "plan",
            "sql",
            "summary",
            "assumptions",
        ]
        created = client.post(
            "/api/runs",
            headers=headers,
            json={
                "suite_version_id": suite["id"],
                "model_profile_ids": [good, bad],
                "case_ids": None,
                "attempts": 1,
            },
        )
        assert created.status_code == 200, created.text
        run_id = int(created.json()["id"])
        snapshot = wait_for_run(client, run_id)
        assert snapshot["status"] == "completed_with_errors"
        assert [model["status"] for model in snapshot["models"]] == ["completed", "failed"], (
            json.dumps(snapshot, ensure_ascii=False)
        )
        assert snapshot["models"][0]["official_score"] == 100.0
        assert snapshot["models"][1]["official_score"] == 0.0

        history = client.get(f"/api/runs/{run_id}/events/history?limit=5000").json()[
            "events"
        ]
        assert history == sorted(history, key=lambda event: event["seq"])
        assert any(event["event_type"] == "run.started" for event in history)
        model_started = [event for event in history if event["event_type"] == "model.started"]
        assert {event["model_run_id"] for event in model_started} == {
            model["id"] for model in snapshot["models"]
        }
        assert any(event["event_type"] == "case.failed" for event in history)
        assert any(event["event_type"] == "plan.completed" for event in history)
        assert "private-token" not in json.dumps(history)
        pivot = history[len(history) // 2]["seq"]
        resumed = client.get(
            f"/api/runs/{run_id}/events/history?after_seq={pivot}&limit=5000"
        ).json()["events"]
        assert resumed and all(event["seq"] > pivot for event in resumed)
        with client.stream("GET", f"/api/runs/{run_id}/events?after_seq={pivot}") as stream:
            streamed = [
                json.loads(line.removeprefix("data: "))
                for line in stream.iter_lines()
                if line.startswith("data: ")
            ]
        assert streamed and all(event["seq"] > pivot for event in streamed)
        assert all("message" in event and "payload" in event for event in streamed)

        case_run = snapshot["models"][0]["cases"][0]
        hidden = client.get(f"/api/case-runs/{case_run['id']}").json()
        revealed = client.get(f"/api/case-runs/{case_run['id']}?include_reference=true").json()
        assert "reference_sql" not in hidden
        assert "expected_result_preview" not in hidden
        assert hidden["plan"]["grain"] == "reference result grain"
        assert hidden["assumptions"] == []
        assert hidden["raw_output"]
        assert hidden["generation_ms"] == 1.0
        assert hidden["provider_request_id"].startswith("fixture-reference-model-")
        assert revealed["reference_sql"] == REFERENCES[case_run["stable_key"]]
        assert revealed["expected_result_preview"]["digest"] == revealed["expected_digest"]
        assert revealed["expected_result_preview"]["row_count"] >= 1

        report = client.get(f"/api/runs/{run_id}/report")
        assert report.status_code == 200
        report_data = report.json()
        assert report_data["conclusion"]["champions"] == ["Reference"]
        assert report_data["protocol"] == {
            "output_contract": "query-plan-v1",
            "app_version": "0.2.0",
            "scorer_version": "1.0.0",
            "duckdb_version": "1.5.5",
            "sqlglot_version": "30.17.0",
            "case_count": 18,
            "attempts": 1,
        }
        assert report_data["fairness"]["comparison_mode"] == "access_path"
        assert "adapter_kind" in report_data["fairness"]["differences"]
        assert set(report_data["models"][0]["categories"]) == {
            "基础查询",
            "连接与粒度",
            "聚合与指标",
            "时间与窗口",
            "复杂查询",
            "数据开发",
        }
        recent = client.get("/api/runs").json()["runs"]
        assert any(item["id"] == run_id and item["case_count"] == 18 for item in recent)

        patched = client.patch(
            f"/api/model-profiles/{good}",
            headers=headers,
            json={"name": "Renamed Current Profile", "model_id": "mutated-current-model"},
        )
        assert patched.status_code == 200, patched.text
        exact = client.post(f"/api/runs/{run_id}/rerun", headers=headers)
        assert exact.status_code == 200, exact.text
        exact_snapshot = wait_for_run(client, int(exact.json()["id"]))
        assert exact_snapshot["source_run_id"] == run_id
        assert exact_snapshot["suite_content_hash"] == snapshot["suite_content_hash"]
        assert exact_snapshot["models"][0]["requested_model_id"] == "reference-model"
        assert exact_snapshot["models"][0]["parameters"] == snapshot["models"][0]["parameters"]
        assert exact_snapshot["models"][0]["name"] == "Reference"
        frozen_report = client.get(f"/api/runs/{run_id}/report").json()
        assert frozen_report["models"][0]["name"] == "Reference"

        evidence_dir = tmp_path / "evidence"
        index = asyncio.run(export_all_evidence(evidence_dir))
        assert any(item["run_id"] == run_id for item in index["runs"])
        verified = verify_evidence(evidence_dir)
        assert verified["run_count"] >= 2
        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in evidence_dir.rglob("*")
            if path.is_file()
        )
        assert "private-token" not in public_text
        assert "/Users/" not in public_text
        report_path = evidence_dir / "runs" / f"run-{run_id:04d}" / "report.json"
        original_report = report_path.read_text(encoding="utf-8")
        report_path.write_text(original_report + " ", encoding="utf-8")
        with pytest.raises(RuntimeError, match="digest mismatch"):
            verify_evidence(evidence_dir)
        report_path.write_text(original_report, encoding="utf-8")
        assert verify_evidence(evidence_dir) == verified
        json.dumps(report_data)


def test_browser_safety_rejects_untrusted_requests() -> None:
    with TestClient(app, base_url="http://127.0.0.1") as client:
        untrusted_host = client.get("/api/health", headers={"Host": "evil.example"})
        assert untrusted_host.status_code == 403
        assert untrusted_host.json()["code"] == "host_forbidden"

        untrusted_origin = client.get(
            "/api/health", headers={"Origin": "https://evil.example"}
        )
        assert untrusted_origin.status_code == 403
        assert untrusted_origin.json()["code"] == "origin_forbidden"

        missing_csrf = client.post("/api/runs", json={})
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "csrf_forbidden"


def test_cancelled_run_reaches_terminal_state(monkeypatch: Any) -> None:
    fixture = FixtureAdapter()
    monkeypatch.setitem(adapter_registry._adapters, "codex_cli", fixture)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = csrf(client)
        slow = create_profile(client, headers, "Slow", "codex_cli", "slow-model")
        suite = client.get("/api/suites").json()[0]["versions"][0]
        created = client.post(
            "/api/runs",
            headers=headers,
            json={
                "suite_version_id": suite["id"],
                "model_profile_ids": [slow],
                "case_ids": [suite["cases"][0]["id"]],
                "attempts": 1,
            },
        )
        run_id = int(created.json()["id"])
        cancelled = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
        assert cancelled.status_code == 200
        snapshot = wait_for_run(client, run_id)
        assert snapshot["status"] == "cancelled"
        history = client.get(f"/api/runs/{run_id}/events/history?limit=5000").json()["events"]
        assert history[-1]["event_type"] == "run.cancelled"
