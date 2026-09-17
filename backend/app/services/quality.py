from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from backend.app.domain import (
    ChallengeCandidate,
    ChallengeCandidateResult,
    ChallengeCheckResult,
    ChallengeSummary,
    ChallengeVariant,
    ChallengeVariantResult,
    SuiteSource,
)
from backend.app.services.sql_evaluator import EvaluationOutcome, evaluate_case
from backend.app.services.suites import validate_and_build

QUALITY_SCHEMA_VERSION = "result-quality-v2"
_RESULT_COMPONENTS = {
    "execution": 10.0,
    "column_count": 5.0,
    "column_names": 5.0,
    "row_f1": 45.0,
    "ordering": 10.0,
}


def case_result_quality(
    score: dict[str, Any] | None, *, legacy: bool = False, error_code: str | None = None
) -> dict[str, bool | str | None]:
    """Separate executed business results from output format and policy failures."""
    complete = isinstance(score, dict) and all(
        isinstance(score.get(key), (int, float)) for key in {"protocol", *_RESULT_COMPONENTS}
    )
    result: dict[str, bool | str | None]
    if not complete or score is None:
        result = {
            "result_correct": None,
            "execution_ok": None,
            "protocol_ok": None,
            "reason": "quality evidence unavailable"
            if score is None
            else "quality evidence incomplete",
        }
    else:
        execution_ok = float(score["execution"]) == 10
        result_correct = execution_ok and all(
            float(score[key]) == maximum
            for key, maximum in _RESULT_COMPONENTS.items()
            if legacy or key != "column_names"
        )
        result = {
            "result_correct": result_correct if legacy or execution_ok else None,
            "execution_ok": execution_ok,
            "protocol_ok": float(score["protocol"]) == 5,
            "reason": "result matches the reference contract"
            if result_correct
            else (
                "query did not execute successfully"
                if not execution_ok
                else "result differs from the reference contract"
            ),
        }
    if legacy:
        return result
    failure: str | None = None
    if error_code in {
        "multiple_statements",
        "non_read_only_statement",
        "forbidden_sql_operation",
        "external_access_forbidden",
        "schema_not_allowed",
        "adapter_policy_violation",
    }:
        failure = "policy_rejected"
    elif error_code == "output_contract_error":
        failure = "protocol_error"
    elif error_code in {
        "sql_parse_error",
        "unknown_table",
        "sql_execution_error",
        "query_timeout",
        "row_limit_exceeded",
    }:
        failure = "execution_error"
    elif error_code == "cancelled":
        failure = "cancelled"
    elif error_code in {"internal_error", "warehouse_missing", "sql_worker_error"}:
        failure = "infrastructure_error"
    elif error_code:
        failure = "provider_error"
    elif result["result_correct"] is False:
        failure = "result_mismatch"
    format_ok = (
        bool(
            result["protocol_ok"]
            and score
            and score["column_names"] == 5
            and score["column_count"] == 5
        )
        if result["execution_ok"]
        else None
    )
    if failure is None and format_ok is False:
        failure = "format_mismatch"
    result.update(format_ok=format_ok, failure_kind=failure)
    return result


def aggregate_result_quality(
    cases: list[dict[str, Any]], *, planned_total: int | None = None, legacy: bool = False
) -> dict[str, Any]:
    total = planned_total if planned_total is not None else len(cases)
    qualities = [
        case_result_quality(case.get("score"), legacy=legacy, error_code=case.get("error_code"))
        for case in cases
    ]

    def count(key: str) -> int:
        return sum(item.get(key) is True for item in qualities)

    def rate(key: str) -> float | None:
        return round(count(key) / total, 4) if total else None

    result: dict[str, Any] = {
        "total": total,
        "evaluated": sum(item["result_correct"] is not None for item in qualities),
        "result_correct": count("result_correct"),
        "execution_ok": count("execution_ok"),
        "protocol_ok": count("protocol_ok"),
        "correct_rate": rate("result_correct"),
        "execution_rate": rate("execution_ok"),
        "protocol_rate": rate("protocol_ok"),
    }
    if not legacy:
        failures: dict[str, int] = {}
        for item in qualities:
            kind = item.get("failure_kind")
            if isinstance(kind, str):
                failures[kind] = failures.get(kind, 0) + 1
        result.update(
            format_ok=count("format_ok"), format_rate=rate("format_ok"), failure_counts=failures
        )
    return result


def _outcome_payload(
    outcome: EvaluationOutcome,
) -> tuple[str, bool, str | None, str | None, dict[str, Any] | None]:
    if outcome.status == "failed":
        return "execution_error", False, outcome.error_code, outcome.error_message, None
    quality = case_result_quality(outcome.score.model_dump(mode="json"))
    return (
        "matched" if quality["result_correct"] else "different",
        quality["result_correct"] is True,
        None,
        None,
        outcome.diff.model_dump(mode="json") if outcome.diff is not None else None,
    )


def run_challenge_check(
    source: SuiteSource,
    *,
    case_key: str,
    variants: list[ChallengeVariant],
    candidates: list[ChallengeCandidate],
) -> ChallengeCheckResult:
    """Exercise candidate answers against isolated, deterministic dataset variants."""
    if not variants:
        raise ValueError("at least one data variant is required")
    if not candidates:
        raise ValueError("at least one candidate is required")
    if len(variants) > 10 or len(candidates) > 20:
        raise ValueError("challenge check is limited to 10 variants and 20 candidates")
    case = next((item for item in source.cases if item.stable_key == case_key), None)
    if case is None:
        raise ValueError(f"unknown case_key: {case_key}")

    variant_results: list[ChallengeVariantResult] = []
    candidate_observations: dict[str, list[tuple[str, bool]]] = {
        candidate.name: [] for candidate in candidates
    }
    with tempfile.TemporaryDirectory(prefix="llm-test-challenge-") as temporary:
        root = Path(temporary)
        for index, variant in enumerate(variants):
            variant_source = source.model_copy(update={"seed_sql": variant.seed_sql}, deep=True)
            published = validate_and_build(variant_source, root / f"variant-{index}")
            artifact = Path(published.artifact_dir)
            warehouse_path = artifact / "warehouse.duckdb"
            gold_path = artifact / "gold" / f"{case_key}.json"
            allowed_tables = {table.name for table in published.structure.tables}
            baseline = evaluate_case(
                sql=case.reference_sql,
                warehouse_path=warehouse_path,
                gold_path=gold_path,
                allowed_tables=allowed_tables,
                comparison=case.comparison,
                required_ast=case.required_ast,
                protocol_strict=True,
            )
            baseline_status, baseline_correct, baseline_code, baseline_message, baseline_diff = (
                _outcome_payload(baseline)
            )
            candidate_results: list[ChallengeCandidateResult] = []
            for candidate in candidates:
                outcome = evaluate_case(
                    sql=candidate.sql,
                    warehouse_path=warehouse_path,
                    gold_path=gold_path,
                    allowed_tables=allowed_tables,
                    comparison=case.comparison,
                    required_ast=[],
                    protocol_strict=True,
                )
                status, correct, error_code, error_message, diff = _outcome_payload(outcome)
                distinguished = candidate.expected == "incorrect" and status == "different"
                candidate_observations[candidate.name].append((status, correct))
                candidate_results.append(
                    ChallengeCandidateResult(
                        name=candidate.name,
                        expected=candidate.expected,
                        status=status,
                        result_correct=correct,
                        distinguished=distinguished,
                        error_code=error_code,
                        error_message=error_message,
                        diff=diff,
                    )
                )
            variant_results.append(
                ChallengeVariantResult(
                    name=variant.name,
                    baseline=ChallengeCandidateResult(
                        name="reference",
                        expected="correct",
                        status=baseline_status,
                        result_correct=baseline_correct,
                        distinguished=False,
                        error_code=baseline_code,
                        error_message=baseline_message,
                        diff=baseline_diff,
                    ),
                    candidates=candidate_results,
                )
            )

    passed_candidates = 0
    indistinguishable: list[str] = []
    execution_errors: list[str] = []
    for candidate in candidates:
        observations = candidate_observations[candidate.name]
        has_execution_error = any(status == "execution_error" for status, _ in observations)
        if has_execution_error:
            execution_errors.append(candidate.name)
        if candidate.expected == "correct":
            passed = bool(observations) and all(correct for _, correct in observations)
        else:
            passed = any(status == "different" for status, _ in observations)
            if not passed and not has_execution_error:
                indistinguishable.append(candidate.name)
        passed_candidates += passed

    return ChallengeCheckResult(
        case_key=case_key,
        variants=variant_results,
        summary=ChallengeSummary(
            candidate_count=len(candidates),
            passed_candidates=passed_candidates,
            passed=passed_candidates == len(candidates),
            indistinguishable=indistinguishable,
            execution_errors=execution_errors,
        ),
    )
