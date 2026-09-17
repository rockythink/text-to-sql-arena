from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.schemas import RunCreate
from backend.app.models import (
    BenchmarkCase,
    CaseRun,
    ComparisonRun,
    ModelProfile,
    ModelRun,
    SuiteVersion,
)
from backend.app.services.efficiency import estimate_cost_usd, normalize_token_usage
from backend.app.services.profiles import health_is_current

TERMINAL_RUN_STATUSES = {"completed", "completed_with_errors", "failed", "cancelled", "interrupted"}


def _issue(
    code: str,
    severity: str,
    message: str,
    entity_type: str,
    entity_id: int | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


async def preflight_run(session: AsyncSession, payload: RunCreate) -> dict[str, Any]:
    """Validate a prospective run using persisted state only; never probes providers or writes."""
    issues: list[dict[str, Any]] = []
    version = await session.scalar(
        select(SuiteVersion)
        .options(selectinload(SuiteVersion.cases))
        .where(SuiteVersion.id == payload.suite_version_id)
    )
    selected_cases: list[BenchmarkCase] = []
    if version is None:
        issues.append(
            _issue(
                "suite_not_found",
                "error",
                "测试集版本不存在",
                "suite",
                payload.suite_version_id,
            )
        )
    elif version.status != "published" or not version.content_hash:
        issues.append(
            _issue(
                "suite_not_published",
                "error",
                "运行要求已发布且内容哈希完整的测试集版本",
                "suite",
                version.id,
            )
        )
    else:
        ordered_cases = sorted(version.cases, key=lambda item: item.sort_order)
        if payload.case_ids is None:
            selected_cases = ordered_cases
        else:
            wanted = set(payload.case_ids)
            selected_cases = [case for case in ordered_cases if case.id in wanted]
            missing = wanted - {case.id for case in selected_cases}
            for case_id in sorted(missing):
                issues.append(
                    _issue(
                        "case_not_in_suite",
                        "error",
                        "题目不属于所选测试集版本",
                        "case",
                        case_id,
                    )
                )
        if not selected_cases:
            issues.append(_issue("case_selection_empty", "error", "至少选择一道题", "case"))

    profiles = list(
        (
            await session.scalars(
                select(ModelProfile).where(ModelProfile.id.in_(payload.model_profile_ids))
            )
        ).all()
    )
    by_id = {profile.id: profile for profile in profiles}
    model_rows: list[dict[str, Any]] = []
    for profile_id in payload.model_profile_ids:
        profile = by_id.get(profile_id)
        if profile is None or profile.deleted_at is not None or not profile.enabled:
            issues.append(
                _issue(
                    "profile_unavailable",
                    "error",
                    "模型配置不存在、已删除或已禁用",
                    "model",
                    profile_id,
                )
            )
            model_rows.append(
                {
                    "id": profile_id,
                    "name": f"model-{profile_id}",
                    "health_status": "unavailable",
                    "health_current": False,
                    "health_expires_at": None,
                    "pricing_available": False,
                    "historical_calls": 0,
                }
            )
            continue
        if profile.adapter_kind != "pi":
            issues.append(
                _issue(
                    "legacy_profile_migration_required",
                    "error",
                    f"模型 {profile.name} 使用历史适配器，必须新建 Pi 模型配置后才能运行",
                    "model",
                    profile.id,
                )
            )
        current = health_is_current(profile)
        if not current:
            issues.append(
                _issue(
                    "health_check_stale",
                    "error",
                    f"模型 {profile.name} 的健康检查已过期，请先重新检查",
                    "model",
                    profile.id,
                )
            )
        elif profile.health_status != "healthy":
            issues.append(
                _issue(
                    "profile_not_healthy",
                    "error",
                    f"模型 {profile.name} 当前不可运行",
                    "model",
                    profile.id,
                )
            )
        api_billed = not (
            profile.adapter_kind == "pi"
            and profile.parameters_json.get("auth_mode", "api_key") == "oauth"
        )
        if not profile.pricing_json or not api_billed:
            message = (
                f"模型 {profile.name} 使用订阅 OAuth，不估算 API 账单成本"
                if not api_billed
                else f"模型 {profile.name} 未配置价格，无法给出成本估算"
            )
            issues.append(
                _issue("pricing_missing", "warning", message, "model", profile.id)
            )
        model_rows.append(
            {
                "id": profile.id,
                "name": profile.name,
                "health_status": profile.health_status,
                "health_current": current,
                "health_expires_at": profile.health_expires_at,
                "pricing_available": bool(profile.pricing_json) and api_billed,
                "historical_calls": 0,
            }
        )

    historical: defaultdict[int, list[tuple[float | None, float | None]]] = defaultdict(list)
    if version is not None and by_id:
        rows = (
            await session.execute(
                select(ModelRun, CaseRun)
                .join(ComparisonRun, ComparisonRun.id == ModelRun.comparison_run_id)
                .join(CaseRun, CaseRun.model_run_id == ModelRun.id)
                .where(
                    ComparisonRun.suite_version_id == version.id,
                    ModelRun.model_profile_id.in_(list(by_id)),
                    CaseRun.status == "completed",
                    CaseRun.benchmark_case_id.in_([case.id for case in selected_cases]),
                )
            )
        ).all()
        for model_run, case_run in rows:
            profile = by_id[model_run.model_profile_id]
            if (
                model_run.requested_model_id != profile.model_id
                or model_run.adapter_kind_snapshot != profile.adapter_kind
                or model_run.base_url_snapshot != profile.base_url
                or model_run.response_mode_snapshot != profile.response_mode
                or model_run.parameters_snapshot_json != profile.parameters_json
                or model_run.cli_version_snapshot != profile.health_details_json.get("version")
            ):
                continue
            # SQL execution alone cannot estimate the provider's generation time.
            duration = (
                (case_run.generation_ms + (case_run.execution_ms or 0)) / 1000
                if case_run.generation_ms is not None
                else None
            )
            tokens = normalize_token_usage(case_run.token_usage_json, profile.adapter_kind)
            pricing = (
                None
                if profile.adapter_kind == "pi"
                and profile.parameters_json.get("auth_mode", "api_key") == "oauth"
                else profile.pricing_json
            )
            cost = estimate_cost_usd(tokens, pricing)
            if duration is not None or cost is not None:
                historical[model_run.model_profile_id].append((duration, cost))

    by_model_row = {row["id"]: row for row in model_rows}
    estimated_duration = 0.0
    estimated_cost = 0.0
    duration_complete = bool(payload.model_profile_ids)
    cost_complete = bool(payload.model_profile_ids)
    sample_calls = 0
    per_model_calls = len(selected_cases) * payload.attempts
    for profile_id in payload.model_profile_ids:
        samples = historical.get(profile_id, [])
        by_model_row[profile_id]["historical_calls"] = len(samples)
        sample_calls += len(samples)
        durations = [value for value, _ in samples if value is not None]
        costs = [value for _, value in samples if value is not None]
        if durations:
            estimated_duration = max(
                estimated_duration, sum(durations) / len(durations) * per_model_calls
            )
        else:
            duration_complete = False
        if costs:
            estimated_cost += sum(costs) / len(costs) * per_model_calls
        else:
            cost_complete = False

    total_calls = len(payload.model_profile_ids) * per_model_calls
    return {
        "ready": not any(issue["severity"] == "error" for issue in issues),
        "total_calls": total_calls,
        "selected_case_count": len(selected_cases),
        "issues": issues,
        "models": model_rows,
        "estimate": {
            "basis": "matching_completed_calls",
            "sample_calls": sample_calls,
            "estimated_duration_seconds": round(estimated_duration, 2)
            if duration_complete and sample_calls
            else None,
            "estimated_cost_usd": round(estimated_cost, 8)
            if cost_complete and sample_calls
            else None,
        },
    }


async def failed_case_keys(session: AsyncSession, source_run_id: int) -> list[str]:
    """Return the ordered union of unsuccessful or unfinished cases across all compared models."""
    source = await session.get(ComparisonRun, source_run_id)
    if source is None:
        return []
    rows = (
        await session.scalars(
            select(CaseRun)
            .join(ModelRun, ModelRun.id == CaseRun.model_run_id)
            .where(ModelRun.comparison_run_id == source_run_id)
        )
    ).all()
    failed = {
        case.stable_case_key_snapshot
        for case in rows
        if case.status != "completed"
        or not isinstance((case.score_breakdown_json or {}).get("total"), (int, float))
        or float((case.score_breakdown_json or {}).get("total", 0)) < 100.0
    }
    return [key for key in source.selected_case_keys_json if key in failed]
