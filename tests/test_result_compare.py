from __future__ import annotations

from decimal import Decimal

from backend.app.domain import ComparisonConfig
from backend.app.services.result_compare import QueryResult, ResultColumn, compare_results


def comparison(order: bool = True) -> ComparisonConfig:
    return ComparisonConfig(
        row_order_significant=order,
        duplicate_policy="multiset",
        decimal_scale=2,
        abs_tolerance="0.005",
        rel_tolerance="0",
        max_rows=10000,
    )


def test_decimal_null_duplicates_and_column_reorder() -> None:
    expected = QueryResult(
        columns=[
            ResultColumn(name="name", type="VARCHAR"),
            ResultColumn(name="amount", type="DECIMAL(14,2)"),
        ],
        rows=[["甲", "10.00"], [None, "20.00"], [None, "20.00"]],
    )
    actual = QueryResult(
        columns=[
            ResultColumn(name="amount", type="DECIMAL(14,2)"),
            ResultColumn(name="NAME", type="VARCHAR"),
        ],
        rows=[
            [Decimal("10.004"), "甲"],
            [Decimal("20.00"), None],
            [Decimal("20.00"), None],
        ],
    )
    diff = compare_results(expected, actual, comparison())
    assert diff.f1 == 1
    assert diff.ordered_equal
    assert diff.column_mapping == [1, 0]
    assert not diff.column_names_equal


def test_multiset_equal_but_ordering_wrong() -> None:
    expected = QueryResult(
        columns=[ResultColumn(name="id", type="BIGINT")],
        rows=[[1], [2], [2]],
    )
    actual = QueryResult(
        columns=[ResultColumn(name="id", type="BIGINT")],
        rows=[[2], [1], [2]],
    )
    diff = compare_results(expected, actual, comparison())
    assert diff.f1 == 1
    assert not diff.ordered_equal


def test_tolerance_boundary_is_inclusive() -> None:
    expected = QueryResult(
        columns=[ResultColumn(name="value", type="DECIMAL(14,3)")],
        rows=[["1.000"]],
    )
    actual = QueryResult(
        columns=[ResultColumn(name="value", type="DOUBLE")],
        rows=[[1.005]],
    )
    config = comparison()
    config.decimal_scale = 3
    diff = compare_results(expected, actual, config)
    assert diff.f1 == 1


def test_ambiguous_fingerprint_alignment_fails_closed() -> None:
    expected = QueryResult(
        columns=[
            ResultColumn(name="left", type="BIGINT"),
            ResultColumn(name="right", type="BIGINT"),
        ],
        rows=[[1, 1], [2, 2]],
    )
    actual = QueryResult(
        columns=[
            ResultColumn(name="x", type="BIGINT"),
            ResultColumn(name="y", type="BIGINT"),
        ],
        rows=[[1, 1], [2, 2]],
    )
    diff = compare_results(expected, actual, comparison())
    assert diff.verdict == "column_alignment_ambiguous"
    assert diff.column_mapping is None


def test_empty_results_have_perfect_f1() -> None:
    result = QueryResult(columns=[ResultColumn(name="id", type="BIGINT")], rows=[])
    diff = compare_results(result, result, comparison())
    assert diff.precision == diff.recall == diff.f1 == 1
    assert diff.ordered_equal
