from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.bootstrap import load_builtin_source
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
    assert statistics["success_rate"] == pytest.approx(2 / 3)
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
