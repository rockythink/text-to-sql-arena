from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from backend.app.domain import StructureSnapshot
from backend.app.services.suites import (
    SuiteValidationError,
    build_generation_request,
    parse_suite_source,
    validate_and_build,
)


def minimal_source() -> dict[str, object]:
    return {
        "name": "mini",
        "description": "minimal deterministic suite",
        "dialect": "duckdb",
        "schema_sql": "CREATE TABLE items(id BIGINT PRIMARY KEY, amount DECIMAL(14,2));",
        "seed_sql": "INSERT INTO items VALUES (1, 10.50), (2, 20.00);",
        "semantic": {
            "entities": [
                {
                    "name": "item",
                    "table": "items",
                    "description": "items",
                    "grain": "one row per item",
                    "primary_key": ["id"],
                }
            ],
            "relationships": [],
            "metrics": [
                {
                    "name": "amount",
                    "expression": "items.amount",
                    "description": "amount",
                    "grain": "item",
                    "filters": [],
                }
            ],
            "dimensions": [
                {
                    "name": "id",
                    "expression": "items.id",
                    "data_type": "BIGINT",
                    "description": "id",
                }
            ],
            "business_rules": ["Use all items"],
        },
        "prompt_template": """Rules: {{dialect_rules}}
Structure: {{structure}}
Semantic: {{semantic}}
Question: {{question}}
Contract: {{output_contract}}""",
        "cases": [
            {
                "stable_key": "sum_amount",
                "title": "sum",
                "category": "aggregate",
                "radar_dimension": "聚合与指标",
                "difficulty": "easy",
                "question": "sum amount",
                "reference_sql": "SELECT SUM(amount) AS amount FROM items",
                "required_ast": [],
                "comparison": {
                    "row_order_significant": True,
                    "duplicate_policy": "multiset",
                    "decimal_scale": 2,
                    "abs_tolerance": "0.005",
                    "rel_tolerance": "0",
                    "max_rows": 10000,
                },
                "weight": 1,
                "sort_order": 1,
            }
        ],
    }


def test_publish_builds_immutable_artifact(tmp_path: Path) -> None:
    source = parse_suite_source(minimal_source())
    first = validate_and_build(source, tmp_path)
    second = validate_and_build(source, tmp_path)
    assert first.content_hash == second.content_hash
    assert Path(first.artifact_dir, "warehouse.duckdb").is_file()
    manifest = json.loads(Path(first.artifact_dir, "manifest.json").read_text())
    assert manifest["gold"]["sum_amount"]["row_count"] == 1
    connection = duckdb.connect(str(Path(first.artifact_dir, "warehouse.duckdb")), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone() == (2,)
    finally:
        connection.close()


def test_generation_prompt_does_not_leak_reference_or_rules() -> None:
    source = parse_suite_source(minimal_source())
    structure = StructureSnapshot(tables=[], semantic_relationships=[])
    case = source.cases[0]
    request = build_generation_request(source.prompt_template, structure, source.semantic, case)
    assert case.reference_sql not in request.prompt
    assert "required_ast" not in request.prompt
    assert "sum amount" in request.prompt
    assert '"plan"' in request.prompt
    assert '"grain"' in request.prompt
    assert request.output_schema["required"] == ["plan", "sql", "summary", "assumptions"]


def test_prompt_rejects_unknown_or_duplicate_variables() -> None:
    payload = minimal_source()
    payload["prompt_template"] = "{{question}} {{question}} {{include}}"
    source = parse_suite_source(payload)
    with pytest.raises(SuiteValidationError) as error:
        validate_and_build(source)
    assert any(issue.code == "prompt_variable_count" for issue in error.value.issues)


def test_semantic_contract_forbids_extra_fields() -> None:
    payload = minimal_source()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    semantic["unexpected"] = True
    with pytest.raises(SuiteValidationError):
        parse_suite_source(payload)
