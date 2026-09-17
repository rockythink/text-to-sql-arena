from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import duckdb

from backend.app.bootstrap import BUILTIN_DIR, expected_lock, load_builtin_source
from backend.app.services.suites import validate_and_build


def test_retail_suite_is_reproducible(tmp_path: Path) -> None:
    source = load_builtin_source()
    first = validate_and_build(source, tmp_path / "first")
    second = validate_and_build(source, tmp_path / "second")
    assert first.content_hash == second.content_hash
    gold = first.manifest["gold"]
    assert isinstance(gold, dict)
    assert len(gold) == 18
    assert expected_lock(first.manifest, first.content_hash) == expected_lock(
        second.manifest, second.content_hash
    )


def test_retail_suite_has_balanced_eighteen_case_matrix() -> None:
    source = load_builtin_source()
    assert len(source.cases) == 18
    assert Counter(case.radar_dimension for case in source.cases) == {
        "基础查询": 3,
        "连接与粒度": 3,
        "聚合与指标": 3,
        "时间与窗口": 3,
        "复杂查询": 3,
        "数据开发": 3,
    }
    assert Counter(case.difficulty for case in source.cases) == {
        "easy": 3,
        "medium": 10,
        "hard": 5,
    }
    assert len({case.stable_key for case in source.cases}) == 18


def test_suite_dimensions_are_versioned_data_not_application_enums() -> None:
    case = load_builtin_source().cases[0]
    legacy = type(case).model_validate(
        {**case.model_dump(mode="json"), "radar_dimension": "历史连接语义"}
    )
    assert legacy.radar_dimension == "历史连接语义"


def test_retail_seed_cardinalities_and_fixtures(tmp_path: Path) -> None:
    result = validate_and_build(load_builtin_source(), tmp_path)
    connection = duckdb.connect(
        str(Path(result.artifact_dir) / "warehouse.duckdb"), read_only=True
    )
    try:
        counts: dict[str, int] = {}
        for table in ("dim_customers", "dim_products", "dim_channels", "fact_orders"):
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            assert row is not None
            counts[table] = int(row[0])
        assert counts == {
            "dim_customers": 123,
            "dim_products": 39,
            "dim_channels": 5,
            "fact_orders": 617,
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM dim_customers WHERE customer_id > 110"
        ).fetchone() == (13,)
        assert connection.execute(
            "SELECT COUNT(*) FROM fact_orders WHERE customer_id > 110"
        ).fetchone() == (3,)
        assert connection.execute(
            "SELECT payment_id FROM fact_payments WHERE order_id=900003 "
            "ORDER BY paid_at DESC, payment_id DESC"
        ).fetchall() == [(9000032,), (9000031,)]
        revenues = connection.execute(
            "SELECT product_id, SUM(i.quantity*i.unit_price-i.discount_amount) revenue "
            "FROM fact_order_items i JOIN fact_orders o USING(order_id) "
            "WHERE o.status='completed' AND product_id IN (1,2) "
            "GROUP BY product_id ORDER BY product_id"
        ).fetchall()
        assert revenues[0][1] == revenues[1][1]
        assert connection.execute(
            "SELECT COUNT(*) FROM dim_channels c "
            "LEFT JOIN fact_orders o USING(channel_id) "
            "WHERE c.channel_id=5 AND o.order_id IS NULL"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT o.total_amount-SUM(i.quantity*i.unit_price-i.discount_amount) "
            "FROM fact_orders o JOIN fact_order_items i USING(order_id) "
            "WHERE o.order_id=900004 GROUP BY o.total_amount"
        ).fetchone() == (10,)
    finally:
        connection.close()


def test_checked_in_lock_matches_published_artifact(tmp_path: Path) -> None:
    result = validate_and_build(load_builtin_source(), tmp_path)
    checked_in = json.loads((BUILTIN_DIR / "suite.lock.json").read_text())
    assert checked_in == expected_lock(result.manifest, result.content_hash)


def _reference_sql(stable_key: str) -> str:
    return next(
        case.reference_sql
        for case in load_builtin_source().cases
        if case.stable_key == stable_key
    )


def test_retail_seed_has_objective_boundary_rows(tmp_path: Path) -> None:
    result = validate_and_build(load_builtin_source(), tmp_path)
    connection = duckdb.connect(
        str(Path(result.artifact_dir) / "warehouse.duckdb"), read_only=True
    )
    try:
        assert connection.execute(
            "SELECT order_id FROM fact_orders "
            "WHERE status='completed' AND order_date=DATE '2026-01-01' "
            "AND order_id=910100"
        ).fetchall() == [(910100,)]
        assert connection.execute(
            "SELECT total_amount FROM fact_orders o JOIN dim_customers c USING(customer_id) "
            "WHERE c.segment='boundary_avg' ORDER BY total_amount"
        ).fetchall() == [(Decimal("10.00"),), (Decimal("10.01"),), (Decimal("10.01"),)]
        assert connection.execute(
            "SELECT product_id, SUM(quantity*unit_price-discount_amount) revenue "
            "FROM fact_order_items i JOIN fact_orders o USING(order_id) "
            "JOIN dim_products p USING(product_id) "
            "WHERE o.status='completed' AND p.category='品类-不足三件' "
            "GROUP BY product_id ORDER BY product_id"
        ).fetchall() == [(37, Decimal("25.00")), (38, Decimal("25.00"))]
        assert connection.execute(
            "SELECT strftime(order_date, '%Y-%m'), COUNT(*), SUM(total_amount) "
            "FROM fact_orders WHERE status='completed' "
            "AND order_date>=DATE '2025-05-01' AND order_date<DATE '2025-07-01' "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall() == [("2025-06", 1, Decimal("0.00"))]
        assert connection.execute(
            "SELECT i.quantity, COUNT(r.return_id), SUM(r.return_qty) "
            "FROM fact_order_items i JOIN fact_returns r "
            "ON r.order_id=i.order_id AND r.line_no=i.line_no "
            "WHERE i.order_id=910200 GROUP BY i.quantity"
        ).fetchall() == [(3, 2, 3)]
        assert connection.execute(
            "SELECT total_amount, CASE WHEN total_amount>=2000 THEN 'high' "
            "WHEN total_amount>=1000 THEN 'medium' ELSE 'low' END "
            "FROM fact_orders WHERE order_id BETWEEN 910100 AND 910103 "
            "ORDER BY order_id"
        ).fetchall() == [
            (Decimal("999.99"), "low"),
            (Decimal("1000.00"), "medium"),
            (Decimal("1999.99"), "medium"),
            (Decimal("2000.00"), "high"),
        ]
        assert connection.execute(
            "SELECT o.status, p.status, p.amount FROM fact_orders o "
            "JOIN fact_payments p USING(order_id) WHERE o.order_id=910300"
        ).fetchall() == [("pending", "paid", Decimal("77.77"))]
        assert connection.execute(
            "SELECT COUNT(*) FROM dim_products p "
            "LEFT JOIN fact_order_items i USING(product_id) "
            "WHERE p.category='品类-无销量' AND i.order_id IS NULL"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT order_id, status, total_amount FROM fact_orders "
            "WHERE order_id IN (910301,910302) ORDER BY order_id"
        ).fetchall() == [
            (910301, "pending", Decimal("33.00")),
            (910302, "cancelled", Decimal("44.00")),
        ]
    finally:
        connection.close()


def test_retail_references_distinguish_boundary_shortcuts(tmp_path: Path) -> None:
    result = validate_and_build(load_builtin_source(), tmp_path)
    connection = duckdb.connect(
        str(Path(result.artifact_dir) / "warehouse.duckdb")
    )
    try:
        correct_average = connection.execute(
            "SELECT COUNT(*) FROM fact_orders o JOIN dim_customers c USING(customer_id) "
            "WHERE c.segment='boundary_avg' AND o.total_amount > "
            "(SELECT AVG(o2.total_amount) FROM fact_orders o2 "
            "JOIN dim_customers c2 USING(customer_id) "
            "WHERE c2.segment='boundary_avg')"
        ).fetchone()
        rounded_average = connection.execute(
            "SELECT COUNT(*) FROM fact_orders o JOIN dim_customers c USING(customer_id) "
            "WHERE c.segment='boundary_avg' AND o.total_amount > "
            "(SELECT ROUND(AVG(o2.total_amount),2) FROM fact_orders o2 "
            "JOIN dim_customers c2 USING(customer_id) "
            "WHERE c2.segment='boundary_avg')"
        ).fetchone()
        assert correct_average == (2,)
        assert rounded_average == (0,)

        top_rows = [
            row
            for row in connection.execute(
                _reference_sql("top3_products_per_category")
            ).fetchall()
            if row[0] == "品类-不足三件"
        ]
        assert top_rows == [
            ("品类-不足三件", 37, "边界并列商品-A", Decimal("25.00"), 1),
            ("品类-不足三件", 38, "边界并列商品-B", Decimal("25.00"), 2),
        ]

        running = connection.execute(
            _reference_sql("monthly_running_revenue")
        ).fetchall()
        growth = connection.execute(_reference_sql("monthly_mom_growth")).fetchall()
        assert len(running) == len(growth) == 12
        assert running[4][0:2] == ("2025-05", Decimal("0.00"))
        assert running[5][0:2] == ("2025-06", Decimal("0.00"))
        assert growth[5] == ("2025-06", Decimal("0.00"), None)
        assert growth[6][2] is None

        payment_rows = {
            row[0]: row[1:]
            for row in connection.execute(
                _reference_sql("payment_status_by_channel")
            ).fetchall()
        }
        assert payment_rows["partner"] == (
            Decimal("0.00"), Decimal("0.00"), Decimal("0.00")
        )
        all_paid = connection.execute(
            "SELECT SUM(amount) FROM fact_payments WHERE status='paid'"
        ).fetchone()
        completed_paid = connection.execute(
            "SELECT SUM(p.amount) FROM fact_payments p JOIN fact_orders o USING(order_id) "
            "WHERE p.status='paid' AND o.status='completed'"
        ).fetchone()
        assert all_paid is not None and completed_paid is not None
        assert all_paid[0] - completed_paid[0] == Decimal("77.77")

        category_rows = {
            row[0]: row[1:]
            for row in connection.execute(
                _reference_sql("category_revenue_share")
            ).fetchall()
        }
        assert category_rows["品类-无销量"] == (Decimal("0.00"), 0.0)
        connection.execute("BEGIN")
        connection.execute(
            "UPDATE fact_orders SET status='pending' WHERE status='completed'"
        )
        zero_total_rows = connection.execute(
            _reference_sql("category_revenue_share")
        ).fetchall()
        connection.execute("ROLLBACK")
        assert zero_total_rows
        assert all(row[1] == Decimal("0.00") and row[2] is None for row in zero_total_rows)

        reconciliation = {
            row[0]: row[1:]
            for row in connection.execute(
                _reference_sql("order_total_reconciliation")
            ).fetchall()
        }
        assert reconciliation[910301] == (
            Decimal("33.00"), Decimal("0.00"), Decimal("33.00")
        )
        assert reconciliation[910302] == (
            Decimal("44.00"), Decimal("40.00"), Decimal("4.00")
        )
        completed_only = connection.execute(
            "WITH item_totals AS (SELECT order_id, "
            "SUM(quantity*unit_price-discount_amount) total FROM fact_order_items "
            "GROUP BY order_id) SELECT COUNT(*) FROM fact_orders o "
            "LEFT JOIN item_totals i USING(order_id) WHERE o.status='completed' "
            "AND ABS(o.total_amount-COALESCE(i.total,0))>0.005"
        ).fetchone()
        assert completed_only == (1,)
        assert len(reconciliation) == 3
    finally:
        connection.close()
