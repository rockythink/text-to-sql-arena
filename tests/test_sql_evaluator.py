from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.bootstrap import load_builtin_source
from backend.app.domain import ChallengeCandidate, ChallengeVariant
from backend.app.services.quality import (
    aggregate_result_quality,
    case_result_quality,
    run_challenge_check,
)
from backend.app.services.reporting import build_run_report
from backend.app.services.sql_evaluator import (
    EvaluationError,
    attempt_statistics,
    evaluate_ast_rules,
    evaluate_case,
    execute_guarded_query,
    parse_and_guard_sql,
    weighted_average,
)
from backend.app.services.suites import validate_and_build

ALLOWED_TABLES = {
    "dim_customers",
    "dim_products",
    "dim_channels",
    "fact_orders",
    "fact_order_items",
    "fact_payments",
    "fact_returns",
}


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("DELETE FROM fact_orders", "non_read_only_statement"),
        ("CREATE TABLE bad(id INT)", "non_read_only_statement"),
        ("COPY (SELECT 1) TO '/tmp/x.csv'", "non_read_only_statement"),
        ("ATTACH '/tmp/x.db' AS x", "non_read_only_statement"),
        ("PRAGMA threads=8", "non_read_only_statement"),
        ("SELECT * FROM read_csv_auto('/tmp/x.csv')", "external_access_forbidden"),
        ("SELECT * FROM read_csv('/tmp/x.csv')", "external_access_forbidden"),
        ("SELECT * FROM generate_series(1, 3)", "external_access_forbidden"),
        ("SELECT * FROM range(3)", "external_access_forbidden"),
        ("SELECT * FROM missing", "unknown_table"),
        ("SELECT * FROM other.fact_orders", "schema_not_allowed"),
    ],
)
def test_sql_guard_rejects_unsafe_queries(sql: str, code: str) -> None:
    with pytest.raises(EvaluationError) as error:
        parse_and_guard_sql(sql, ALLOWED_TABLES)
    assert error.value.code == code


def test_all_reference_sql_satisfies_literal_ast_rules() -> None:
    source = load_builtin_source()
    for case in source.cases:
        guarded = parse_and_guard_sql(case.reference_sql, ALLOWED_TABLES)
        results = evaluate_ast_rules(guarded.expression, case.required_ast)
        assert all(result.passed for result in results), (
            case.stable_key,
            [result.model_dump() for result in results],
        )
def test_window_rule_accepts_table_alias_renaming() -> None:
    source = load_builtin_source()
    case = next(item for item in source.cases if item.stable_key == "latest_successful_payment")
    guarded = parse_and_guard_sql(
        "SELECT ROW_NUMBER() OVER ("
        "PARTITION BY payments.order_id "
        "ORDER BY payments.paid_at DESC, payments.payment_id DESC"
        ") AS rank_no FROM fact_payments payments",
        ALLOWED_TABLES,
    )

    result = evaluate_ast_rules(guarded.expression, case.required_ast)

    assert result[0].passed


def test_window_rule_does_not_conflate_same_named_columns_from_different_sources() -> None:
    source = load_builtin_source()
    case = next(item for item in source.cases if item.stable_key == "latest_successful_payment")
    guarded = parse_and_guard_sql(
        "SELECT ROW_NUMBER() OVER ("
        "PARTITION BY orders.order_id "
        "ORDER BY payments.paid_at DESC, payments.payment_id DESC"
        ") AS rank_no "
        "FROM fact_payments payments "
        "JOIN fact_orders orders ON orders.order_id = payments.order_id",
        ALLOWED_TABLES,
    )

    result = evaluate_ast_rules(guarded.expression, case.required_ast)

    assert not result[0].passed


def test_preaggregation_rule_accepts_equivalent_cte_names() -> None:
    source = load_builtin_source()
    case = next(item for item in source.cases if item.stable_key == "category_return_rate")
    rule = [item for item in case.required_ast if item.kind == "separate_measure_preaggregation"]
    guarded = parse_and_guard_sql(
        "WITH sales_by_category AS ("
        "SELECT p.category, SUM(i.quantity) AS sold_qty "
        "FROM fact_order_items i JOIN dim_products p ON p.product_id = i.product_id "
        "GROUP BY p.category"
        "), returns_by_category AS ("
        "SELECT p.category, SUM(r.return_qty) AS returned_qty "
        "FROM fact_returns r "
        "JOIN fact_order_items i ON i.order_id = r.order_id AND i.line_no = r.line_no "
        "JOIN dim_products p ON p.product_id = i.product_id "
        "GROUP BY p.category"
        ") SELECT s.category, s.sold_qty, r.returned_qty "
        "FROM sales_by_category s LEFT JOIN returns_by_category r ON r.category = s.category",
        ALLOWED_TABLES,
    )

    result = evaluate_ast_rules(guarded.expression, rule)

    assert result[0].passed


def test_preaggregation_rule_rejects_wrong_measure_source() -> None:
    source = load_builtin_source()
    case = next(item for item in source.cases if item.stable_key == "category_return_rate")
    rule = [item for item in case.required_ast if item.kind == "separate_measure_preaggregation"]
    guarded = parse_and_guard_sql(
        "WITH sales AS ("
        "SELECT p.category, SUM(i.quantity) AS sold_qty "
        "FROM fact_order_items i JOIN dim_products p ON p.product_id = i.product_id "
        "GROUP BY p.category"
        "), fake_returns AS ("
        "SELECT p.category, SUM(i.quantity) AS returned_qty "
        "FROM fact_order_items i JOIN dim_products p ON p.product_id = i.product_id "
        "GROUP BY p.category"
        ") SELECT * FROM sales s JOIN fake_returns r ON r.category = s.category",
        ALLOWED_TABLES,
    )

    result = evaluate_ast_rules(guarded.expression, rule)

    assert not result[0].passed


def test_preaggregation_rule_rejects_fact_amplifying_cross_join() -> None:
    source = load_builtin_source()
    case = next(item for item in source.cases if item.stable_key == "category_return_rate")
    rule = [item for item in case.required_ast if item.kind == "separate_measure_preaggregation"]
    guarded = parse_and_guard_sql(
        "WITH sales AS ("
        "SELECT p.category, SUM(i.quantity) AS sold_qty "
        "FROM fact_order_items i JOIN dim_products p ON p.product_id = i.product_id "
        "GROUP BY p.category"
        "), amplified_returns AS ("
        "SELECT p.category, SUM(r.return_qty) AS returned_qty "
        "FROM fact_returns r CROSS JOIN fact_order_items i "
        "JOIN dim_products p ON p.product_id = i.product_id "
        "GROUP BY p.category"
        ") SELECT * FROM sales s JOIN amplified_returns r ON r.category = s.category",
        ALLOWED_TABLES,
    )

    result = evaluate_ast_rules(guarded.expression, rule)

    assert not result[0].passed


def test_all_reference_sql_scores_100(tmp_path: Path) -> None:
    source = load_builtin_source()
    published = validate_and_build(source, tmp_path)
    warehouse = Path(published.artifact_dir) / "warehouse.duckdb"
    for case in source.cases:
        outcome = evaluate_case(
            sql=case.reference_sql,
            warehouse_path=warehouse,
            gold_path=Path(published.artifact_dir) / "gold" / f"{case.stable_key}.json",
            allowed_tables=ALLOWED_TABLES,
            comparison=case.comparison,
            required_ast=case.required_ast,
            protocol_strict=True,
        )
        assert outcome.status == "completed", (case.stable_key, outcome.error_message)
        assert outcome.score.total == 100, (case.stable_key, outcome.score.model_dump())


def test_worker_enforces_row_limit(tmp_path: Path) -> None:
    published = validate_and_build(load_builtin_source(), tmp_path)
    guarded = parse_and_guard_sql(
        "SELECT a.order_id FROM fact_orders a CROSS JOIN fact_orders b",
        ALLOWED_TABLES,
    )
    with pytest.raises(EvaluationError) as error:
        execute_guarded_query(
            guarded,
            Path(published.artifact_dir) / "warehouse.duckdb",
            max_rows=10000,
        )
    assert error.value.code == "row_limit_exceeded"


def test_worker_timeout_terminates_query(tmp_path: Path) -> None:
    published = validate_and_build(load_builtin_source(), tmp_path)
    guarded = parse_and_guard_sql(
        "WITH RECURSIVE counter(n) AS ("
        "SELECT 1 UNION ALL SELECT n+1 FROM counter WHERE n < 1000000000"
        ") SELECT SUM(n) FROM counter",
        ALLOWED_TABLES,
    )
    with pytest.raises(EvaluationError) as error:
        execute_guarded_query(
            guarded,
            Path(published.artifact_dir) / "warehouse.duckdb",
            timeout_seconds=0.05,
        )
    assert error.value.code == "query_timeout"


def test_attempt_statistics_include_failures() -> None:
    statistics = attempt_statistics([100, 0, 50])
    assert statistics["mean"] == 50
    assert statistics["nonzero_score_rate"] == pytest.approx(2 / 3)
    assert statistics["stddev"] == pytest.approx(40.824829, rel=1e-6)


def test_same_result_without_required_window_scores_85_and_overall_99_17(
    tmp_path: Path,
) -> None:
    source = load_builtin_source()
    published = validate_and_build(source, tmp_path)
    case = next(item for item in source.cases if item.stable_key == "top3_products_per_category")
    sql = """
        WITH product_revenue AS (
            SELECT p.category, p.product_id, p.product_name,
                   SUM(i.quantity * i.unit_price - i.discount_amount) AS net_revenue
            FROM fact_order_items i
            JOIN fact_orders o ON o.order_id = i.order_id
            JOIN dim_products p ON p.product_id = i.product_id
            WHERE o.status = 'completed'
            GROUP BY p.category, p.product_id, p.product_name
        )
        SELECT pr.category, pr.product_id, pr.product_name, pr.net_revenue,
               (SELECT COUNT(*) + 1
                FROM product_revenue better
                WHERE better.category = pr.category
                  AND (better.net_revenue > pr.net_revenue
                       OR (better.net_revenue = pr.net_revenue
                           AND better.product_id < pr.product_id))) AS rank_no
        FROM product_revenue pr
        WHERE (SELECT COUNT(*)
               FROM product_revenue better
               WHERE better.category = pr.category
                 AND (better.net_revenue > pr.net_revenue
                      OR (better.net_revenue = pr.net_revenue
                          AND better.product_id < pr.product_id))) < 3
        ORDER BY pr.category ASC, rank_no ASC
    """
    outcome = evaluate_case(
        sql=sql,
        warehouse_path=Path(published.artifact_dir) / "warehouse.duckdb",
        gold_path=Path(published.artifact_dir) / "gold" / f"{case.stable_key}.json",
        allowed_tables=ALLOWED_TABLES,
        comparison=case.comparison,
        required_ast=case.required_ast,
        protocol_strict=True,
    )
    assert outcome.status == "completed"
    assert outcome.diff is not None and outcome.diff.f1 == 1 and outcome.diff.ordered_equal
    assert outcome.score.sql_capability == 0
    assert outcome.score.total == 85
    scores = [
        (outcome.score.total if item is case else 100.0, item.weight) for item in source.cases
    ]
    assert weighted_average(scores) == pytest.approx(99.166667, rel=1e-6)


def test_case_11_naive_detail_join_loses_result_or_required_ast(tmp_path: Path) -> None:
    source = load_builtin_source()
    published = validate_and_build(source, tmp_path)
    case = next(item for item in source.cases if item.stable_key == "category_return_rate")
    sql = """
        SELECT p.category,
               SUM(i.quantity) AS sold_qty,
               COALESCE(SUM(r.return_qty), 0) AS returned_qty,
               ROUND(COALESCE(SUM(r.return_qty), 0)::DECIMAL
                     / NULLIF(SUM(i.quantity), 0), 4) AS return_rate
        FROM fact_order_items i
        JOIN fact_orders o ON o.order_id = i.order_id
        JOIN dim_products p ON p.product_id = i.product_id
        LEFT JOIN fact_returns r
          ON r.order_id = i.order_id AND r.line_no = i.line_no
        WHERE o.status = 'completed'
        GROUP BY p.category
        ORDER BY p.category ASC
    """
    outcome = evaluate_case(
        sql=sql,
        warehouse_path=Path(published.artifact_dir) / "warehouse.duckdb",
        gold_path=Path(published.artifact_dir) / "gold" / f"{case.stable_key}.json",
        allowed_tables=ALLOWED_TABLES,
        comparison=case.comparison,
        required_ast=case.required_ast,
        protocol_strict=True,
    )
    assert outcome.status == "completed"
    assert outcome.score.total < 100
    assert outcome.diff is not None
    assert outcome.diff.f1 < 1 or any(not rule.passed for rule in outcome.score.ast_rules)


def test_result_quality_keeps_business_correctness_separate_from_format() -> None:
    score = {
        "protocol": 5,
        "execution": 10,
        "column_count": 5,
        "column_names": 0,
        "row_f1": 45,
        "ordering": 10,
    }

    quality = case_result_quality(score)

    assert quality["result_correct"] is True
    assert quality["format_ok"] is False
    assert quality["failure_kind"] == "format_mismatch"
    legacy = case_result_quality(score, legacy=True)
    assert legacy["result_correct"] is False
    assert "format_ok" not in legacy


def test_result_quality_distinguishes_policy_rejection_from_wrong_result() -> None:
    wrong_score = {
        "protocol": 5,
        "execution": 10,
        "column_count": 5,
        "column_names": 5,
        "row_f1": 0,
        "ordering": 10,
    }

    wrong = case_result_quality(wrong_score)
    rejected = case_result_quality(None, error_code="external_access_forbidden")

    assert wrong["result_correct"] is False
    assert wrong["failure_kind"] == "result_mismatch"
    assert rejected["result_correct"] is None
    assert rejected["format_ok"] is None
    assert rejected["failure_kind"] == "policy_rejected"


def test_result_quality_aggregate_counts_format_and_failure_kinds() -> None:
    correct_but_misnamed = {
        "protocol": 5,
        "execution": 10,
        "column_count": 5,
        "column_names": 0,
        "row_f1": 45,
        "ordering": 10,
    }
    wrong_result = correct_but_misnamed.copy()
    wrong_result["column_names"] = 5
    wrong_result["row_f1"] = 0

    aggregate = aggregate_result_quality(
        [
            {"score": correct_but_misnamed},
            {"score": wrong_result},
            {"score": None, "error_code": "external_access_forbidden"},
        ]
    )

    assert aggregate["evaluated"] == 2
    assert aggregate["result_correct"] == 1
    assert aggregate["format_ok"] == 1
    assert aggregate["failure_counts"] == {
        "format_mismatch": 1,
        "result_mismatch": 1,
        "policy_rejected": 1,
    }


def test_challenge_check_distinguishes_wrong_result_with_real_duckdb() -> None:
    source = load_builtin_source()
    case = next(item for item in source.cases if item.stable_key == "basic_filter_sort")
    result = run_challenge_check(
        source,
        case_key=case.stable_key,
        variants=[ChallengeVariant(name="canonical", seed_sql=source.seed_sql)],
        candidates=[
            ChallengeCandidate(
                name="reference-equivalent",
                sql=case.reference_sql,
                expected="correct",
            ),
            ChallengeCandidate(
                name="wrong-empty",
                sql=(
                    "SELECT o.order_id, c.customer_name, o.order_date, o.total_amount "
                    "FROM fact_orders o JOIN dim_customers c ON c.customer_id=o.customer_id "
                    "WHERE o.status='never' ORDER BY o.total_amount DESC, o.order_id ASC"
                ),
                expected="incorrect",
            ),
        ],
    )
    assert result.summary.passed
    assert result.variants[0].baseline.result_correct
    wrong = result.variants[0].candidates[1]
    assert wrong.status == "different"
    assert wrong.distinguished


def test_challenge_check_reports_all_wrong_indistinguishable_boundary() -> None:
    source = load_builtin_source()
    empty_seed = source.seed_sql + "\nUPDATE fact_orders SET status = 'cancelled';"
    result = run_challenge_check(
        source,
        case_key="basic_filter_sort",
        variants=[ChallengeVariant(name="empty-target", seed_sql=empty_seed)],
        candidates=[
            ChallengeCandidate(
                name="wrong-empty",
                sql=(
                    "SELECT o.order_id, c.customer_name, o.order_date, o.total_amount "
                    "FROM fact_orders o JOIN dim_customers c ON c.customer_id=o.customer_id "
                    "WHERE o.status='never' ORDER BY o.total_amount DESC, o.order_id ASC"
                ),
                expected="incorrect",
            )
        ],
    )
    assert not result.summary.passed
    assert result.summary.indistinguishable == ["wrong-empty"]
    candidate = result.variants[0].candidates[0]
    assert candidate.status == "matched"
    assert not candidate.distinguished


def test_run_report_v4_preserves_official_score_and_exposes_coverage() -> None:
    score = {
        "total": 100,
        "protocol": 5,
        "execution": 10,
        "column_count": 5,
        "column_names": 5,
        "row_f1": 45,
        "ordering": 10,
    }
    case = {
        "stable_key": "case",
        "radar_dimension": "dimension",
        "weight": 3,
        "status": "completed",
        "score": score,
        "token_usage": None,
        "generation_ms": None,
        "execution_ms": 1,
    }
    snapshot = {
        "id": 1,
        "status": "running",
        "protocol": {
            "app_version": "test",
            "scorer_version": "test",
            "duckdb_version": "test",
            "sqlglot_version": "test",
        },
        "models": [
            {
                "name": "model",
                "official_score": 73.25,
                "adapter_kind": "fixture",
                "pricing": None,
                "cases": [case, {**case, "score": None, "status": "pending"}],
            }
        ],
    }
    report = build_run_report(snapshot)
    model = report["models"][0]
    assert report["report_schema_version"] == "run-report-v4"
    assert report["quality_schema_version"] == "result-quality-v2"
    assert model["official_score"] == 73.25
    assert model["quality"]["total"] == 2
    assert model["quality"]["evaluated"] == 1
    assert model["quality"]["correct_rate"] == 0.5
    assert model["cases"][1]["quality"]["result_correct"] is None
    assert model["attempt_statistics"]["case"]["nonzero_score_rate"] == 0.5
