from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
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


def _decimal_quantum(scale: int) -> Decimal:
    return Decimal(1).scaleb(-scale)


def _number_cell(value: Decimal, scale: int) -> NormalizedCell:
    if not value.is_finite():
        raise ValueError("numeric values must be finite")
    quantized = value.quantize(_decimal_quantum(scale), rounding=ROUND_HALF_UP)
    rendered = f"{quantized:.{scale}f}"
    return NormalizedCell(kind="number", value=quantized, public=rendered)


def _normalize_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        candidate = value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
        parsed = datetime.fromisoformat(candidate)
    else:
        parsed = value
    aware = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_cell(value: Any, declared_type: str, scale: int) -> NormalizedCell:
    type_name = declared_type.upper()
    if value is None:
        return NormalizedCell(kind="null", value=None, public=None)
    if isinstance(value, bool):
        return NormalizedCell(kind="bool", value=value, public=value)
    if isinstance(value, int):
        return _number_cell(Decimal(value), scale)
    if isinstance(value, Decimal):
        return _number_cell(value, scale)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("float values must be finite")
        return _number_cell(Decimal(str(value)), scale)
    if isinstance(value, datetime):
        rendered = _normalize_timestamp(value)
        return NormalizedCell(kind="timestamp", value=rendered, public=rendered)
    if isinstance(value, date):
        rendered = value.isoformat()
        return NormalizedCell(kind="date", value=rendered, public=rendered)
    if isinstance(value, str):
        if any(
            token in type_name
            for token in (
                "DECIMAL",
                "NUMERIC",
                "HUGEINT",
                "BIGINT",
                "INTEGER",
                "SMALLINT",
                "TINYINT",
                "DOUBLE",
                "FLOAT",
                "REAL",
            )
        ):
            return _number_cell(Decimal(value), scale)
        if "TIMESTAMP" in type_name:
            rendered = _normalize_timestamp(value)
            return NormalizedCell(kind="timestamp", value=rendered, public=rendered)
        if type_name == "DATE":
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
        normalized.append(
            NormalizedRow(cells=cells, public=public, canonical=_canonical_bytes(public))
        )
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


def _fingerprint_mapping(
    expected_rows: list[NormalizedRow], actual_rows: list[NormalizedRow], width: int
) -> tuple[int, ...] | None:
    expected = [_column_fingerprint(expected_rows, index) for index in range(width)]
    actual = [_column_fingerprint(actual_rows, index) for index in range(width)]
    candidates = [
        [actual_index for actual_index, value in enumerate(actual) if value == expected_value]
        for expected_value in expected
    ]
    if any(not options for options in candidates):
        return None
    solutions: list[tuple[int, ...]] = []

    def search(position: int, chosen: list[int]) -> None:
        if len(solutions) > 1:
            return
        if position == width:
            solutions.append(tuple(chosen))
            return
        for candidate in candidates[position]:
            if candidate not in chosen:
                search(position + 1, [*chosen, candidate])

    search(0, [])
    return solutions[0] if len(solutions) == 1 else None


def _align_rows(rows: list[NormalizedRow], mapping: tuple[int, ...]) -> list[NormalizedRow]:
    aligned: list[NormalizedRow] = []
    for row in rows:
        cells = tuple(row.cells[index] for index in mapping)
        public = [cell.public for cell in cells]
        aligned.append(
            NormalizedRow(cells=cells, public=public, canonical=_canonical_bytes(public))
        )
    return aligned


def _cell_equal(
    expected: NormalizedCell,
    actual: NormalizedCell,
    abs_tolerance: Decimal,
    rel_tolerance: Decimal,
) -> bool:
    if expected.kind == "number" and actual.kind == "number":
        assert isinstance(expected.value, Decimal)
        assert isinstance(actual.value, Decimal)
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
    abs_tolerance: Decimal,
    rel_tolerance: Decimal,
) -> bool:
    return len(expected.cells) == len(actual.cells) and all(
        _cell_equal(left, right, abs_tolerance, rel_tolerance)
        for left, right in zip(expected.cells, actual.cells, strict=True)
    )


def _maximum_matching(
    expected: list[NormalizedRow],
    actual: list[NormalizedRow],
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
                expected[expected_index], actual[actual_index], abs_tolerance, rel_tolerance
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
        mapping = _fingerprint_mapping(expected_rows, actual_rows, len(expected.columns))
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
        expected_rows, aligned_actual, abs_tolerance, rel_tolerance
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
            _row_equal(left, right, abs_tolerance, rel_tolerance)
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
