from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from pydantic import TypeAdapter, ValidationError
from sqlglot import exp

from backend.app.config import settings
from backend.app.domain import (
    AstRule,
    BenchmarkCaseDefinition,
    GenerationOutput,
    GenerationRequest,
    PublishResult,
    SemanticLayer,
    StructureColumn,
    StructureForeignKey,
    StructureSnapshot,
    StructureTable,
    SuiteSource,
    ValidationIssue,
)

PROMPT_VARIABLES = (
    "dialect_rules",
    "structure",
    "semantic",
    "question",
    "output_contract",
)
DIALECT_RULES = (
    "Use DuckDB SQL. Return exactly one read-only query. "
    "Do not access files, URLs, extensions, or schemas outside the supplied tables."
)


class SuiteValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


def normalize_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_suite_payload(source: SuiteSource) -> dict[str, Any]:
    return {
        "schema_sql": normalize_text(source.schema_sql),
        "seed_sql": normalize_text(source.seed_sql),
        "semantic": source.semantic.model_dump(mode="json"),
        "prompt_template": normalize_text(source.prompt_template),
        "cases": [
            case.model_dump(mode="json")
            for case in sorted(source.cases, key=lambda item: item.sort_order)
        ],
    }


def compute_content_hash(source: SuiteSource) -> str:
    return hashlib.sha256(canonical_bytes(canonical_suite_payload(source))).hexdigest()


def validate_prompt_template(template: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for variable in PROMPT_VARIABLES:
        token = "{{" + variable + "}}"
        count = template.count(token)
        if count != 1:
            issues.append(
                ValidationIssue(
                    code="prompt_variable_count",
                    message=f"{token} 必须且只能出现一次，当前 {count} 次",
                    location=f"prompt_template.{variable}",
                )
            )
    scrubbed = template
    for variable in PROMPT_VARIABLES:
        scrubbed = scrubbed.replace("{{" + variable + "}}", "")
    if "{{" in scrubbed or "}}" in scrubbed or "{%" in scrubbed or "{#" in scrubbed:
        issues.append(
            ValidationIssue(
                code="prompt_expression_forbidden",
                message="Prompt 仅允许五个固定变量，不允许表达式或 include",
                location="prompt_template",
            )
        )
    return issues


def build_generation_request(
    prompt_template: str,
    structure: StructureSnapshot,
    semantic: SemanticLayer,
    case: BenchmarkCaseDefinition,
) -> GenerationRequest:
    issues = validate_prompt_template(prompt_template)
    if issues:
        raise SuiteValidationError(issues)
    values = {
        "dialect_rules": DIALECT_RULES,
        "structure": canonical_bytes(structure.model_dump(mode="json")).decode(),
        "semantic": canonical_bytes(semantic.model_dump(mode="json")).decode(),
        "question": case.question,
        "output_contract": canonical_bytes(GenerationOutput.model_json_schema()).decode(),
    }
    prompt = normalize_text(prompt_template)
    for name, value in values.items():
        prompt = prompt.replace("{{" + name + "}}", value)
    return GenerationRequest(
        case_key=case.stable_key,
        prompt=prompt,
        output_schema=GenerationOutput.model_json_schema(),
    )


def _extract_structure(
    connection: duckdb.DuckDBPyConnection, semantic: SemanticLayer
) -> StructureSnapshot:
    column_rows = connection.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'main'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    constraints = connection.execute(
        """
        SELECT table_name, constraint_type, constraint_column_names,
               referenced_table, referenced_column_names
        FROM duckdb_constraints()
        WHERE schema_name = 'main'
        ORDER BY table_name, constraint_index
        """
    ).fetchall()
    by_table: dict[str, list[StructureColumn]] = {}
    for table_name, column_name, data_type, is_nullable in column_rows:
        by_table.setdefault(str(table_name), []).append(
            StructureColumn(
                name=str(column_name),
                data_type=str(data_type),
                nullable=str(is_nullable).upper() == "YES",
            )
        )
    primary_keys: dict[str, list[str]] = {}
    foreign_keys: dict[str, list[StructureForeignKey]] = {}
    for table, kind, columns, referenced_table, referenced_columns in constraints:
        table_name = str(table)
        if kind == "PRIMARY KEY":
            primary_keys[table_name] = [str(item) for item in columns]
        elif kind == "FOREIGN KEY":
            foreign_keys.setdefault(table_name, []).append(
                StructureForeignKey(
                    columns=[str(item) for item in columns],
                    referenced_table=str(referenced_table),
                    referenced_columns=[str(item) for item in referenced_columns],
                )
            )
    return StructureSnapshot(
        tables=[
            StructureTable(
                name=name,
                columns=columns,
                primary_key=primary_keys.get(name, []),
                foreign_keys=foreign_keys.get(name, []),
            )
            for name, columns in sorted(by_table.items())
        ],
        semantic_relationships=semantic.relationships,
    )


def _validate_semantic(
    semantic: SemanticLayer, structure: StructureSnapshot
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tables = {table.name: {column.name for column in table.columns} for table in structure.tables}
    entities = {entity.name: entity for entity in semantic.entities}
    for index, entity in enumerate(semantic.entities):
        if entity.table not in tables:
            issues.append(
                ValidationIssue(
                    code="semantic_table_missing",
                    message=f"语义实体 {entity.name} 引用不存在的表 {entity.table}",
                    location=f"semantic.entities[{index}].table",
                )
            )
            continue
        for entity_column in entity.primary_key:
            if entity_column not in tables[entity.table]:
                issues.append(
                    ValidationIssue(
                        code="semantic_column_missing",
                        message=(f"语义实体 {entity.name} 主键字段 {entity_column} 不存在"),
                        location=f"semantic.entities[{index}].primary_key",
                    )
                )
    for index, relationship in enumerate(semantic.relationships):
        if relationship.from_entity not in entities or relationship.to_entity not in entities:
            issues.append(
                ValidationIssue(
                    code="semantic_relationship_entity_missing",
                    message=(
                        f"关系引用不存在实体: {relationship.from_entity}->{relationship.to_entity}"
                    ),
                    location=f"semantic.relationships[{index}]",
                )
            )
            continue
        try:
            parsed = sqlglot.parse_one(relationship.sql_on, read="duckdb")
        except sqlglot.errors.ParseError as exc:
            issues.append(
                ValidationIssue(
                    code="semantic_relationship_invalid",
                    message=str(exc),
                    location=f"semantic.relationships[{index}].sql_on",
                )
            )
            continue
        allowed_columns = set().union(*tables.values()) if tables else set()
        for expression_column in parsed.find_all(exp.Column):
            if expression_column.name.lower() not in {name.lower() for name in allowed_columns}:
                issues.append(
                    ValidationIssue(
                        code="semantic_relationship_column_missing",
                        message=f"关系字段不存在: {expression_column.name}",
                        location=f"semantic.relationships[{index}].sql_on",
                    )
                )
    return issues


def _guard_reference_sql(sql: str, allowed_tables: set[str]) -> exp.Query:
    statements = sqlglot.parse(sql, read="duckdb")
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("reference SQL must contain exactly one read-only query")
    query = statements[0]
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Copy,
        exp.Command,
    )
    if any(query.find(node) is not None for node in forbidden):
        raise ValueError("reference SQL contains a forbidden statement")
    cte_names = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
    for table in query.find_all(exp.Table):
        if table.name.lower() not in {name.lower() for name in allowed_tables} | cte_names:
            raise ValueError(f"unknown table: {table.name}")
    return query


def _gold_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, float):
        if not Decimal(str(value)).is_finite():
            raise ValueError("non-finite float in reference result")
        return str(value)
    raise TypeError(f"Unsupported gold value: {type(value).__name__}")


def _gold_payload(cursor: duckdb.DuckDBPyConnection, max_rows: int) -> dict[str, Any]:
    rows = cursor.fetchmany(max_rows + 1)
    if len(rows) > max_rows:
        raise ValueError("reference SQL exceeds max_rows")
    columns = [{"name": str(item[0]), "type": str(item[1])} for item in (cursor.description or [])]
    normalized_rows = [[_gold_value(value) for value in row] for row in rows]
    payload: dict[str, Any] = {"columns": columns, "rows": normalized_rows}
    payload["digest"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return payload


def validate_and_build(
    source: SuiteSource,
    artifact_root: Path | None = None,
) -> PublishResult:
    issues = validate_prompt_template(source.prompt_template)
    if issues:
        raise SuiteValidationError(issues)
    content_hash = compute_content_hash(source)
    root = artifact_root or settings.var_dir / "suites"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / content_hash
    if destination.exists():
        existing_manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        warehouse = duckdb.connect(str(destination / "warehouse.duckdb"), read_only=True)
        try:
            structure = _extract_structure(warehouse, source.semantic)
        finally:
            warehouse.close()
        return PublishResult(
            content_hash=content_hash,
            artifact_dir=str(destination),
            structure=structure,
            manifest=existing_manifest,
        )

    temp_parent = Path(tempfile.mkdtemp(prefix=f".{content_hash}-", dir=root))
    warehouse_path = temp_parent / "warehouse.duckdb"
    gold_dir = temp_parent / "gold"
    gold_dir.mkdir()
    connection = duckdb.connect(str(warehouse_path))
    try:
        connection.execute("SET TimeZone='UTC'")
        connection.execute(source.schema_sql)
        connection.execute(source.seed_sql)
        structure = _extract_structure(connection, source.semantic)
        issues.extend(_validate_semantic(source.semantic, structure))
        allowed_tables = {table.name for table in structure.tables}
        gold_manifest: dict[str, Any] = {}
        for case in sorted(source.cases, key=lambda item: item.sort_order):
            try:
                _guard_reference_sql(case.reference_sql, allowed_tables)
                cursor = connection.execute(case.reference_sql)
                gold = _gold_payload(cursor, case.comparison.max_rows)
            except Exception as exc:
                issues.append(
                    ValidationIssue(
                        code="reference_sql_invalid",
                        message=str(exc),
                        location=f"cases.{case.stable_key}.reference_sql",
                    )
                )
                continue
            gold_path = gold_dir / f"{case.stable_key}.json"
            gold_path.write_bytes(canonical_bytes(gold))
            gold_manifest[case.stable_key] = {
                "path": f"gold/{case.stable_key}.json",
                "digest": gold["digest"],
                "row_count": len(gold["rows"]),
            }
        if issues:
            raise SuiteValidationError(issues)
        manifest: dict[str, Any] = {
            "suite_hash": content_hash,
            "duckdb_version": duckdb.__version__,
            "sqlglot_version": package_version("sqlglot"),
            "scorer_version": settings.scorer_version,
            "schema_hash": hashlib.sha256(normalize_text(source.schema_sql).encode()).hexdigest(),
            "seed_hash": hashlib.sha256(normalize_text(source.seed_sql).encode()).hexdigest(),
            "gold": gold_manifest,
        }
        (temp_parent / "manifest.json").write_bytes(canonical_bytes(manifest))
        connection.close()
        os.replace(temp_parent, destination)
        return PublishResult(
            content_hash=content_hash,
            artifact_dir=str(destination),
            structure=structure,
            manifest=manifest,
        )
    except Exception:
        connection.close()
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise


def parse_suite_source(payload: dict[str, Any]) -> SuiteSource:
    try:
        source = SuiteSource.model_validate(payload)
        TypeAdapter(list[AstRule]).validate_python(
            [rule for case in source.cases for rule in case.required_ast]
        )
        return source
    except ValidationError as exc:
        raise SuiteValidationError(
            [
                ValidationIssue(
                    code="suite_contract_invalid",
                    message=error["msg"],
                    location=".".join(str(part) for part in error["loc"]),
                )
                for error in exc.errors()
            ]
        ) from exc
