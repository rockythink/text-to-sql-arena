from __future__ import annotations

from decimal import Decimal

from backend.app.domain import ComparisonConfig
from backend.app.services.result_compare import (
    QueryResult,
    ResultColumn,
    compare_results,
    result_digest,
)


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


def test_integer_gold_is_exact_and_preserves_mismatch_precision() -> None:
    expected = QueryResult(columns=[ResultColumn(name="count", type="BIGINT")], rows=[[12]])
    equivalent = QueryResult(columns=[ResultColumn(name="count", type="DOUBLE")], rows=[[12.0]])
    different = QueryResult(columns=[ResultColumn(name="count", type="DOUBLE")], rows=[[12.4]])
    config = comparison()
    config.decimal_scale = 0

    assert compare_results(expected, equivalent, config).verdict == "equal"
    diff = compare_results(expected, different, config)

    assert diff.verdict == "row_mismatch"
    assert diff.missing_rows == [["12"]]
    assert diff.extra_rows == [["12.4"]]


def test_decimal_scale_does_not_change_digest_or_numeric_precision() -> None:
    precise = QueryResult(
        columns=[ResultColumn(name="amount", type="DECIMAL(14,4)")],
        rows=[[Decimal("1234567890123456789012345678.0001")]],
    )
    nearby = QueryResult(
        columns=[ResultColumn(name="amount", type="DECIMAL(14,4)")],
        rows=[[Decimal("1234567890123456789012345678.0002")]],
    )

    assert result_digest(precise, 0) == result_digest(precise, 9)
    assert result_digest(precise, 0) != result_digest(nearby, 0)


def test_integer_column_cannot_borrow_decimal_tolerance() -> None:
    expected = QueryResult(
        columns=[
            ResultColumn(name="id", type="BIGINT"),
            ResultColumn(name="amount", type="DECIMAL(14,3)"),
        ],
        rows=[[100, Decimal("10.000")]],
    )
    actual = QueryResult(
        columns=[
            ResultColumn(name="id", type="DOUBLE"),
            ResultColumn(name="amount", type="DOUBLE"),
        ],
        rows=[[100.004, 10.004]],
    )

    diff = compare_results(expected, actual, comparison())

    assert diff.verdict == "row_mismatch"
    assert diff.matched_count == 0


def test_alias_alignment_uses_unique_type_aware_tolerant_mapping() -> None:
    expected = QueryResult(
        columns=[
            ResultColumn(name="id", type="BIGINT"),
            ResultColumn(name="amount", type="DECIMAL(14,3)"),
        ],
        rows=[[1, Decimal("10.000")], [2, Decimal("20.000")]],
    )
    actual = QueryResult(
        columns=[
            ResultColumn(name="computed_amount", type="DOUBLE"),
            ResultColumn(name="computed_id", type="BIGINT"),
        ],
        rows=[[10.004, 1], [20.004, 2]],
    )

    diff = compare_results(expected, actual, comparison())

    assert diff.verdict == "equal"
    assert diff.column_mapping == [1, 0]


def test_alias_alignment_rejects_multiple_tolerant_mappings() -> None:
    expected = QueryResult(
        columns=[
            ResultColumn(name="left", type="DECIMAL(14,3)"),
            ResultColumn(name="right", type="DECIMAL(14,3)"),
        ],
        rows=[[Decimal("10.000"), Decimal("10.001")]],
    )
    actual = QueryResult(
        columns=[
            ResultColumn(name="x", type="DOUBLE"),
            ResultColumn(name="y", type="DOUBLE"),
        ],
        rows=[[10.002, 10.003]],
    )

    diff = compare_results(expected, actual, comparison())

    assert diff.verdict == "column_alignment_ambiguous"
    assert diff.column_mapping is None
