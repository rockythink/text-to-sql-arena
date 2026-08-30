from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.models import (
    BenchmarkCase,
    CaseRun,
    ComparisonRun,
    ModelProfile,
    ModelRun,
)
from backend.app.services.efficiency import aggregate_efficiency, case_efficiency
from backend.app.services.sql_evaluator import (
    attempt_statistics,
    build_conclusion,
    weighted_average,
)


class EvidenceLookupError(LookupError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


async def build_run_snapshot(session: AsyncSession, run_id: int) -> dict[str, Any]:
    run = await session.get(ComparisonRun, run_id)
    if run is None:
        raise EvidenceLookupError("run_not_found", "运行不存在")
    models = list(
        (
            await session.scalars(
                select(ModelRun)
                .where(ModelRun.comparison_run_id == run_id)
                .order_by(ModelRun.selection_order)
            )
        ).all()
    )
    profiles = {
        profile.id: profile
        for profile in (
            await session.scalars(
                select(ModelProfile).where(
                    ModelProfile.id.in_([model.model_profile_id for model in models])
                )
            )
        ).all()
    }
    result_models: list[dict[str, Any]] = []
    for model in models:
        rows = (
            await session.execute(
                select(CaseRun, BenchmarkCase)
                .join(BenchmarkCase, BenchmarkCase.id == CaseRun.benchmark_case_id)
                .where(CaseRun.model_run_id == model.id)
                .order_by(BenchmarkCase.sort_order, CaseRun.attempt)
            )
        ).all()
        profile = profiles.get(model.model_profile_id)
        name = model.profile_name_snapshot or (
            profile.name if profile is not None else f"model-{model.model_profile_id}"
        )
        result_models.append(
            {
                "id": model.id,
                "name": name,
                "status": model.status,
                "official_score": model.official_score,
                "requested_model_id": model.requested_model_id,
                "resolved_model_id": model.resolved_model_id,
                "adapter_kind": model.adapter_kind_snapshot,
                "response_mode": model.response_mode_snapshot,
                "parameters": model.parameters_snapshot_json,
                "pricing": model.pricing_snapshot_json,
                "cli_version": model.cli_version_snapshot,
                "isolation": model.isolation_snapshot_json,
                "cases": [
                    {
                        "id": case_run.id,
                        "case_id": case.id,
                        "stable_key": case_run.stable_case_key_snapshot,
                        "title": case.title,
                        "category": case.category,
                        "radar_dimension": case.radar_dimension,
                        "attempt": case_run.attempt,
                        "status": case_run.status,
                        "visible_summary": case_run.visible_summary,
                        "formatted_sql": case_run.formatted_sql,
                        "generation_ms": case_run.generation_ms,
                        "execution_ms": case_run.execution_ms,
                        "provider_request_id": case_run.provider_request_id,
                        "token_usage": case_run.token_usage_json,
                        "score": case_run.score_breakdown_json,
                        "error_code": case_run.error_code,
                        "error_message": case_run.error_message,
                    }
                    for case_run, case in rows
                ],
            }
        )
    controls = {
        "adapter_kind": {model.adapter_kind_snapshot or "" for model in models},
        "base_url": {model.base_url_snapshot or "" for model in models},
        "response_mode": {model.response_mode_snapshot or "" for model in models},
        "parameters": {
            json.dumps(model.parameters_snapshot_json, ensure_ascii=False, sort_keys=True)
            for model in models
        },
        "cli_version": {model.cli_version_snapshot or "" for model in models},
    }
    differences = [field for field, values in controls.items() if len(values) > 1]
    if len(models) < 2:
        comparison_mode = "single_model"
    elif differences:
        comparison_mode = "access_path"
    else:
        comparison_mode = "pure_model"
    return {
        "id": run.id,
        "source_run_id": run.source_run_id,
        "suite_version_id": run.suite_version_id,
        "suite_content_hash": run.suite_content_hash,
        "selected_case_keys": run.selected_case_keys_json,
        "status": run.status,
        "attempts": run.attempts,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "protocol": {
            "output_contract": run.output_contract_snapshot,
            "app_version": run.app_version_snapshot,
            "scorer_version": run.scorer_version_snapshot,
            "duckdb_version": run.duckdb_version_snapshot,
            "sqlglot_version": run.sqlglot_version_snapshot,
            "case_count": len(run.selected_case_keys_json),
            "attempts": run.attempts,
        },
        "fairness": {
            "comparison_mode": comparison_mode,
            "pure_model_comparison": comparison_mode == "pure_model",
            "controlled_fields": [field for field in controls if field not in differences],
            "differences": differences,
            "model_variable": [model.requested_model_id for model in models],
            "exact_rerun_default": True,
        },
        "models": result_models,
    }


def build_run_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    model_reports: list[dict[str, Any]] = []
    for model in snapshot["models"]:
        category_values: defaultdict[str, list[tuple[float, float]]] = defaultdict(list)
        attempts_by_case: defaultdict[str, list[float]] = defaultdict(list)
        for case in model["cases"]:
            score = float((case["score"] or {}).get("total", 0))
            category_values[case["radar_dimension"]].append((score, 1.0))
            attempts_by_case[case["stable_key"]].append(score)
        model_reports.append(
            {
                **model,
                "categories": {
                    name: round(weighted_average(values), 2)
                    for name, values in category_values.items()
                },
                "attempt_statistics": {
                    key: attempt_statistics(values) for key, values in attempts_by_case.items()
                },
                "failure_count": sum(case["status"] == "failed" for case in model["cases"]),
                "efficiency": aggregate_efficiency(
                    model["cases"], model["adapter_kind"], model.get("pricing")
                ),
            }
        )
    protocol = snapshot["protocol"]
    report = {
        **snapshot,
        "report_schema_version": "run-report-v2",
        "app_version": protocol["app_version"],
        "scorer_version": protocol["scorer_version"],
        "duckdb_version": protocol["duckdb_version"],
        "sqlglot_version": protocol["sqlglot_version"],
        "models": model_reports,
    }
    report["conclusion"] = (
        build_conclusion(report)
        if snapshot["status"] in {"completed", "completed_with_errors"}
        else {"status": "incomplete", "champions": [], "models": []}
    )
    return report


async def build_case_evidence(
    session: AsyncSession,
    case_run_id: int,
    *,
    include_reference: bool = False,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(CaseRun, BenchmarkCase, ModelRun, ComparisonRun)
            .join(BenchmarkCase, BenchmarkCase.id == CaseRun.benchmark_case_id)
            .join(ModelRun, ModelRun.id == CaseRun.model_run_id)
            .join(ComparisonRun, ComparisonRun.id == ModelRun.comparison_run_id)
            .where(CaseRun.id == case_run_id)
        )
    ).one_or_none()
    if row is None:
        raise EvidenceLookupError("case_run_not_found", "Case run 不存在")
    case_run, case, model, run = row
    result: dict[str, Any] = {
        "id": case_run.id,
        "run_id": run.id,
        "model_run_id": model.id,
        "model_name": model.profile_name_snapshot or f"model-{model.model_profile_id}",
        "requested_model_id": model.requested_model_id,
        "resolved_model_id": model.resolved_model_id,
        "stable_key": case_run.stable_case_key_snapshot,
        "title": case.title,
        "question": case.question,
        "category": case.category,
        "radar_dimension": case.radar_dimension,
        "difficulty": case.difficulty,
        "status": case_run.status,
        "attempt": case_run.attempt,
        "started_at": case_run.started_at,
        "finished_at": case_run.finished_at,
        "prompt": case_run.prompt_snapshot,
        "raw_output": case_run.raw_output,
        "plan": case_run.plan_json,
        "assumptions": case_run.assumptions_json,
        "visible_summary": case_run.visible_summary,
        "generated_sql": case_run.generated_sql,
        "formatted_sql": case_run.formatted_sql,
        "generation_ms": case_run.generation_ms,
        "execution_ms": case_run.execution_ms,
        "provider_request_id": case_run.provider_request_id,
        "token_usage": case_run.token_usage_json,
        "efficiency": case_efficiency(
            {
                "token_usage": case_run.token_usage_json,
                "generation_ms": case_run.generation_ms,
                "execution_ms": case_run.execution_ms,
            },
            model.adapter_kind_snapshot,
            model.pricing_snapshot_json,
        ),
        "expected_digest": case_run.expected_digest,
        "actual_digest": case_run.actual_digest,
        "result_preview": case_run.result_preview_json,
        "score": case_run.score_breakdown_json,
        "error_code": case_run.error_code,
        "error_message": case_run.error_message,
        "required_ast": case.required_ast_json,
        "comparison": case.comparison_json,
        "suite_content_hash": run.suite_content_hash,
    }
    if not include_reference:
        return result
    if case_run.status != "completed":
        raise EvidenceLookupError("reference_not_available", "仅完成 case 可查看参考证据")
    root = artifact_root or settings.var_dir / "suites"
    gold_path = root / run.suite_content_hash / "gold" / f"{case_run.stable_case_key_snapshot}.json"
    if not gold_path.exists():
        raise EvidenceLookupError("gold_artifact_missing", "固定金标结果资产不存在")
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    result["reference_sql"] = case.reference_sql
    result["expected_result_preview"] = {
        "columns": gold["columns"],
        "rows": gold["rows"][:200],
        "row_count": len(gold["rows"]),
        "digest": gold["digest"],
    }
    return result
