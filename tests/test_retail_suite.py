from __future__ import annotations

import json
from collections import Counter
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
    connection = duckdb.connect(str(Path(result.artifact_dir) / "warehouse.duckdb"), read_only=True)
    try:
        counts: dict[str, int] = {}
        for table in ("dim_customers", "dim_products", "dim_channels", "fact_orders"):
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            assert row is not None
            counts[table] = int(row[0])
        assert counts == {
            "dim_customers": 120,
            "dim_products": 36,
            "dim_channels": 5,
            "fact_orders": 604,
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM dim_customers WHERE customer_id > 110"
        ).fetchone() == (10,)
        assert connection.execute(
            "SELECT COUNT(*) FROM fact_orders WHERE customer_id > 110"
        ).fetchone() == (0,)
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
