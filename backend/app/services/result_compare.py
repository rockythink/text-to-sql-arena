from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain import ComparisonConfig


class ResultColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[ResultColumn]
    rows: list[list[Any]]
    digest: str | None = None


class ResultDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str
    column_count_equal: bool
    column_names_equal: bool
    column_mapping: list[int] | None
    expected_count: int
    actual_count: int
    matched_count: int
    precision: float
    recall: float
    f1: float
    ordered_equal: bool
    expected_digest: str
    actual_digest: str
    missing_rows: list[list[Any]] = Field(default_factory=list)
    extra_rows: list[list[Any]] = Field(default_factory=list)


CellKind = Literal["null", "bool", "number", "date", "timestamp", "string"]


@dataclass(frozen=True, slots=True)
class NormalizedCell:
    kind: CellKind
    value: None | bool | Decimal | str
    public: Any

    @property
    def canonical(self) -> bytes:
        return _canonical_bytes({"kind": self.kind, "value": self.public})


@dataclass(frozen=True, slots=True)
class NormalizedRow:
    cells: tuple[NormalizedCell, ...]
    public: list[Any]
    canonical: bytes


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _number_cell(value: Decimal) -> NormalizedCell:
    if not value.is_finite():
        raise ValueError("numeric values must be finite")
    if value.is_zero():
        return NormalizedCell(kind="number", value=Decimal(0), public="0")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return NormalizedCell(kind="number", value=value, public=rendered)


def _normalize_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        candidate = value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
        parsed = datetime.fromisoformat(candidate)
    else:
        parsed = value
    aware = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_cell(value: Any, declared_type: str, scale: int) -> NormalizedCell:
    type_family = _type_family(declared_type)
    if value is None:
        return NormalizedCell(kind="null", value=None, public=None)
    if isinstance(value, bool):
        return NormalizedCell(kind="bool", value=value, public=value)
    if isinstance(value, int):
        return _number_cell(Decimal(value))
    if isinstance(value, Decimal):
        return _number_cell(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("float values must be finite")
        return _number_cell(Decimal(str(value)))
    if isinstance(value, datetime):
        rendered = _normalize_timestamp(value)
        return NormalizedCell(kind="timestamp", value=rendered, public=rendered)
    if isinstance(value, date):
        rendered = value.isoformat()
        return NormalizedCell(kind="date", value=rendered, public=rendered)
    if isinstance(value, str):
        if type_family in {"exact_number", "tolerant_number"}:
            return _number_cell(Decimal(value))
        if type_family == "timestamp":
            rendered = _normalize_timestamp(value)
            return NormalizedCell(kind="timestamp", value=rendered, public=rendered)
        if type_family == "date":
            rendered = date.fromisoformat(value).isoformat()
            return NormalizedCell(kind="date", value=rendered, public=rendered)
        rendered = unicodedata.normalize("NFC", value)
        return NormalizedCell(kind="string", value=rendered, public=rendered)
    raise TypeError(f"Unsupported result value: {type(value).__name__}")


def normalize_rows(result: QueryResult, scale: int) -> list[NormalizedRow]:
    if any(len(row) != len(result.columns) for row in result.rows):
        raise ValueError("row width does not match result columns")
    normalized: list[NormalizedRow] = []
    for row in result.rows:
        cells = tuple(
            normalize_cell(value, result.columns[index].type, scale)
            for index, value in enumerate(row)
        )
        public = [cell.public for cell in cells]
        normalized.append(NormalizedRow(cells=cells, public=public, canonical=_row_key(cells)))
    return normalized


def result_digest(result: QueryResult, scale: int) -> str:
    rows = normalize_rows(result, scale)
    payload = {
        "columns": [column.model_dump(mode="json") for column in result.columns],
        "rows": [row.public for row in rows],
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _normalized_name(name: str) -> str:
    stripped = name.strip()
    if len(stripped) >= 2 and (
        (stripped[0] == stripped[-1] == '"')
        or (stripped[0] == stripped[-1] == "`")
        or (stripped[0] == "[" and stripped[-1] == "]")
    ):
        stripped = stripped[1:-1]
    return stripped.casefold()


def _row_key(cells: tuple[NormalizedCell, ...]) -> bytes:
    return _canonical_bytes([{"kind": cell.kind, "value": cell.public} for cell in cells])


def _type_family(declared_type: str) -> str:
    base_type = declared_type.upper().partition("(")[0].strip()
    if base_type in {"DECIMAL", "NUMERIC", "DOUBLE", "DOUBLE PRECISION", "FLOAT", "REAL"}:
        return "tolerant_number"
    if base_type in {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "INT",
        "INT1",
        "INT2",
        "INT4",
        "INT8",
        "INT16",
        "INT32",
        "INT64",
        "UINT",
        "UINT8",
        "UINT16",
        "UINT32",
        "UINT64",
    }:
        return "exact_number"
    if base_type.startswith("TIMESTAMP"):
        return "timestamp"
    if base_type == "DATE":
        return "date"
    if base_type in {"BOOL", "BOOLEAN"}:
        return "bool"
    return "string"


def _types_compatible(expected_type: str, actual_type: str) -> bool:
    expected_family = _type_family(expected_type)
    actual_family = _type_family(actual_type)
    numeric = {"exact_number", "tolerant_number"}
    return (
        expected_family in numeric and actual_family in numeric
    ) or expected_family == actual_family


def _name_mapping(expected: QueryResult, actual: QueryResult) -> tuple[int, ...] | None:
    expected_names = [_normalized_name(column.name) for column in expected.columns]
    actual_names = [_normalized_name(column.name) for column in actual.columns]
    if len(set(expected_names)) != len(expected_names) or len(set(actual_names)) != len(
        actual_names
    ):
        return None
    if set(expected_names) != set(actual_names):
        return None
    index = {name: position for position, name in enumerate(actual_names)}
    return tuple(index[name] for name in expected_names)


def _column_fingerprint(rows: list[NormalizedRow], index: int) -> tuple[tuple[bytes, int], ...]:
    values = Counter(row.cells[index].canonical for row in rows)
    return tuple(sorted(values.items()))


def _maximum_cardinality_mapping(
    candidates: list[list[int]], banned: tuple[int, int] | None = None
) -> tuple[int, ...] | None:
    right_to_left: dict[int, int] = {}

    def augment(left: int, seen: set[int]) -> bool:
        for right in candidates[left]:
            if (left, right) == banned or right in seen:
                continue
            seen.add(right)
            owner = right_to_left.get(right)
            if owner is None or augment(owner, seen):
                right_to_left[right] = left
                return True
        return False

    if not all(augment(left, set()) for left in range(len(candidates))):
        return None
    left_to_right = {left: right for right, left in right_to_left.items()}
    return tuple(left_to_right[left] for left in range(len(candidates)))


def _unique_mapping(candidates: list[list[int]]) -> tuple[int, ...] | None:
    mapping = _maximum_cardinality_mapping(candidates)
    if mapping is None:
        return None
    if any(
        _maximum_cardinality_mapping(candidates, (left, right)) is not None
        for left, right in enumerate(mapping)
    ):
        return None
    return mapping


def _columns_equal(
    expected_rows: list[NormalizedRow],
    expected_index: int,
    actual_rows: list[NormalizedRow],
    actual_index: int,
    tolerant_numeric: bool,
    abs_tolerance: Decimal,
    rel_tolerance: Decimal,
) -> bool:
    if len(expected_rows) != len(actual_rows):
        return False
    candidates = [
        [
            actual_row
            for actual_row in range(len(actual_rows))
            if _cell_equal(
                expected_rows[expected_row].cells[expected_index],
                actual_rows[actual_row].cells[actual_index],
                tolerant_numeric,
                abs_tolerance,
                rel_tolerance,
            )
        ]
        for expected_row in range(len(expected_rows))
    ]
    return _maximum_cardinality_mapping(candidates) is not None


def _fingerprint_mapping(
    expected_result: QueryResult,
    actual_result: QueryResult,
    expected_rows: list[NormalizedRow],
    actual_rows: list[NormalizedRow],
    abs_tolerance: Decimal,
    rel_tolerance: Decimal,
) -> tuple[int, ...] | None:
    width = len(expected_result.columns)
    expected_fingerprints = [_column_fingerprint(expected_rows, index) for index in range(width)]
    actual_fingerprints = [_column_fingerprint(actual_rows, index) for index in range(width)]
    exact_candidates = [
        [
            actual_index
            for actual_index, actual_fingerprint in enumerate(actual_fingerprints)
            if actual_fingerprint == expected_fingerprint
            and _types_compatible(
                expected_result.columns[expected_index].type,
                actual_result.columns[actual_index].type,
            )
        ]
        for expected_index, expected_fingerprint in enumerate(expected_fingerprints)
    ]
    exact_mapping = _unique_mapping(exact_candidates)
    if exact_mapping is not None:
        return exact_mapping

    tolerant_candidates = [
        [
            actual_index
            for actual_index in range(width)
            if _types_compatible(
                expected_result.columns[expected_index].type,
                actual_result.columns[actual_index].type,
            )
            and _columns_equal(
                expected_rows,
                expected_index,
                actual_rows,
                actual_index,
                _type_family(expected_result.columns[expected_index].type) == "tolerant_number",
                abs_tolerance,
                rel_tolerance,
            )
        ]
        for expected_index in range(width)
    ]
    return _unique_mapping(tolerant_candidates)


def _align_rows(rows: list[NormalizedRow], mapping: tuple[int, ...]) -> list[NormalizedRow]:
    aligned: list[NormalizedRow] = []
    for row in rows:
        cells = tuple(row.cells[index] for index in mapping)
        public = [cell.public for cell in cells]
        aligned.append(NormalizedRow(cells=cells, public=public, canonical=_row_key(cells)))
    return aligned


def _cell_equal(
    expected: NormalizedCell,
    actual: NormalizedCell,
    tolerant_numeric: bool,
    abs_tolerance: Decimal,
    rel_tolerance: Decimal,
) -> bool:
    if expected.kind == "number" and actual.kind == "number":
        assert isinstance(expected.value, Decimal)
        assert isinstance(actual.value, Decimal)
        if not tolerant_numeric:
            return expected.value == actual.value
        difference = abs(expected.value - actual.value)
        tolerance = max(
            abs_tolerance,
            rel_tolerance * max(abs(expected.value), abs(actual.value)),
        )
        return difference <= tolerance
    if expected.kind != actual.kind:
        return False
    return expected.value == actual.value


def _row_equal(
    expected: NormalizedRow,
    actual: NormalizedRow,
    tolerant_columns: tuple[bool, ...],
    abs_tolerance: Decimal,
    rel_tolerance: Decimal,
) -> bool:
    return len(expected.cells) == len(actual.cells) and all(
        _cell_equal(left, right, tolerant_numeric, abs_tolerance, rel_tolerance)
        for left, right, tolerant_numeric in zip(
            expected.cells, actual.cells, tolerant_columns, strict=True
        )
    )


def _maximum_matching(
    expected: list[NormalizedRow],
    actual: list[NormalizedRow],
    tolerant_columns: tuple[bool, ...],
    abs_tolerance: Decimal,
    rel_tolerance: Decimal,
) -> tuple[dict[int, int], list[int], list[int]]:
    expected_order = sorted(
        range(len(expected)), key=lambda index: (expected[index].canonical, index)
    )
    actual_order = sorted(range(len(actual)), key=lambda index: (actual[index].canonical, index))
    exact_expected = Counter(expected[index].canonical for index in expected_order)
    exact_actual = Counter(actual[index].canonical for index in actual_order)
    if exact_expected == exact_actual:
        buckets: dict[bytes, deque[int]] = {}
        for actual_index in actual_order:
            buckets.setdefault(actual[actual_index].canonical, deque()).append(actual_index)
        pairs = {
            expected_index: buckets[expected[expected_index].canonical].popleft()
            for expected_index in expected_order
        }
        return pairs, [], []

    adjacency: dict[int, list[int]] = {
        expected_index: [
            actual_index
            for actual_index in actual_order
            if _row_equal(
                expected[expected_index],
                actual[actual_index],
                tolerant_columns,
                abs_tolerance,
                rel_tolerance,
            )
        ]
        for expected_index in expected_order
    }
    pair_left: dict[int, int] = {}
    pair_right: dict[int, int] = {}
    distance: dict[int, int] = {}

    def bfs() -> bool:
        queue: deque[int] = deque()
        found = False
        for left in expected_order:
            if left not in pair_left:
                distance[left] = 0
                queue.append(left)
            else:
                distance[left] = -1
        while queue:
            left = queue.popleft()
            for right in adjacency[left]:
                paired = pair_right.get(right)
                if paired is None:
                    found = True
                elif distance.get(paired, -1) < 0:
                    distance[paired] = distance[left] + 1
                    queue.append(paired)
        return found

    def dfs(left: int) -> bool:
        for right in adjacency[left]:
            paired = pair_right.get(right)
            if paired is None or (distance.get(paired) == distance[left] + 1 and dfs(paired)):
                pair_left[left] = right
                pair_right[right] = left
                return True
        distance[left] = -1
        return False

    while bfs():
        for left in expected_order:
            if left not in pair_left:
                dfs(left)
    missing = [index for index in expected_order if index not in pair_left]
    extra = [index for index in actual_order if index not in pair_right]
    return pair_left, missing, extra


def compare_results(
    expected: QueryResult,
    actual: QueryResult,
    comparison: ComparisonConfig,
    preview_limit: int = 20,
) -> ResultDiff:
    expected_digest = expected.digest or result_digest(expected, comparison.decimal_scale)
    actual_digest = result_digest(actual, comparison.decimal_scale)
    if len(expected.columns) != len(actual.columns):
        return ResultDiff(
            verdict="column_count_mismatch",
            column_count_equal=False,
            column_names_equal=False,
            column_mapping=None,
            expected_count=len(expected.rows),
            actual_count=len(actual.rows),
            matched_count=0,
            precision=0,
            recall=0,
            f1=0,
            ordered_equal=False,
            expected_digest=expected_digest,
            actual_digest=actual_digest,
        )
    expected_rows = normalize_rows(expected, comparison.decimal_scale)
    actual_rows = normalize_rows(actual, comparison.decimal_scale)
    mapping = _name_mapping(expected, actual)
    column_names_equal = [_normalized_name(column.name) for column in expected.columns] == [
        _normalized_name(column.name) for column in actual.columns
    ]
    if mapping is None:
        mapping = _fingerprint_mapping(
            expected,
            actual,
            expected_rows,
            actual_rows,
            Decimal(comparison.abs_tolerance),
            Decimal(comparison.rel_tolerance),
        )
        column_names_equal = False
    if mapping is None:
        return ResultDiff(
            verdict="column_alignment_ambiguous",
            column_count_equal=True,
            column_names_equal=False,
            column_mapping=None,
            expected_count=len(expected.rows),
            actual_count=len(actual.rows),
            matched_count=0,
            precision=0,
            recall=0,
            f1=0,
            ordered_equal=False,
            expected_digest=expected_digest,
            actual_digest=actual_digest,
        )
    aligned_actual = _align_rows(actual_rows, mapping)
    abs_tolerance = Decimal(comparison.abs_tolerance)
    rel_tolerance = Decimal(comparison.rel_tolerance)
    pairs, missing, extra = _maximum_matching(
        expected_rows,
        aligned_actual,
        tuple(_type_family(column.type) == "tolerant_number" for column in expected.columns),
        abs_tolerance,
        rel_tolerance,
    )
    matched = len(pairs)
    expected_count = len(expected_rows)
    actual_count = len(aligned_actual)
    precision = matched / actual_count if actual_count else (1.0 if expected_count == 0 else 0.0)
    recall = matched / expected_count if expected_count else (1.0 if actual_count == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    ordered_equal = f1 == 1.0
    if comparison.row_order_significant:
        ordered_equal = expected_count == actual_count and all(
            _row_equal(
                left,
                right,
                tuple(
                    _type_family(column.type) == "tolerant_number" for column in expected.columns
                ),
                abs_tolerance,
                rel_tolerance,
            )
            for left, right in zip(expected_rows, aligned_actual, strict=True)
        )
    verdict = "equal" if f1 == 1.0 and ordered_equal else "row_mismatch"
    return ResultDiff(
        verdict=verdict,
        column_count_equal=True,
        column_names_equal=column_names_equal,
        column_mapping=list(mapping),
        expected_count=expected_count,
        actual_count=actual_count,
        matched_count=matched,
        precision=precision,
        recall=recall,
        f1=f1,
        ordered_equal=ordered_equal,
        expected_digest=expected_digest,
        actual_digest=actual_digest,
        missing_rows=[expected_rows[index].public for index in missing[:preview_limit]],
        extra_rows=[aligned_actual[index].public for index in extra[:preview_limit]],
    )
