from __future__ import annotations

import json
import math
import multiprocessing
import time
from collections.abc import Iterable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Literal, cast

import duckdb
import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from backend.app.domain import (
    AggregateCaseRule,
    AstRule,
    ComparisonConfig,
    CorrelatedSubqueryRule,
    CteCountRule,
    JoinTypeRule,
    NodeCountRule,
    NotExistsRule,
    QueryDepthRule,
    SeparateMeasurePreaggregationRule,
    WindowFunctionRule,
)
from backend.app.services.result_compare import (
    QueryResult,
    ResultDiff,
    compare_results,
)

FORBIDDEN_NODE_KEYS = frozenset(
    {
        "alter",
        "attach",
        "cache",
        "command",
        "copy",
        "create",
        "delete",
        "detach",
        "drop",
        "export",
        "grant",
        "insert",
        "install",
        "load_data",
        "merge",
        "pragma",
        "refresh",
        "revoke",
        "set",
        "transaction",
        "truncate_table",
        "uncache",
        "update",
        "use",
    }
)
EXTERNAL_FUNCTION_NAMES = frozenset(
    {
        "glob",
        "http_get",
        "parquet_scan",
        "postgres_scan",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_parquet",
        "sqlite_scan",
    }
)


class GuardedQuery(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    expression: exp.Query
    formatted_sql: str
    referenced_tables: list[str]


class AstRuleResult(BaseModel):
    id: str
    kind: str
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    protocol: float
    read_only_ast: float
    execution: float
    column_count: float
    column_names: float
    row_f1: float
    ordering: float
    sql_capability: float
    total: float
    ast_rules: list[AstRuleResult] = Field(default_factory=list)


class EvaluationOutcome(BaseModel):
    status: Literal["completed", "failed"]
    formatted_sql: str | None = None
    actual: QueryResult | None = None
    diff: ResultDiff | None = None
    score: ScoreBreakdown
    execution_ms: float | None = None
    error_code: str | None = None
    error_message: str | None = None


class EvaluationError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def parse_and_guard_sql(sql: str, allowed_tables: set[str]) -> GuardedQuery:
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise EvaluationError("sql_parse_error", str(exc)) from exc
    if len(statements) != 1:
        raise EvaluationError("multiple_statements", "SQL 必须且只能包含一条语句")
    expression = statements[0]
    if not isinstance(expression, exp.Query):
        raise EvaluationError("non_read_only_statement", "仅允许只读查询")
    for node in expression.walk():
        if node.key in FORBIDDEN_NODE_KEYS:
            raise EvaluationError("forbidden_sql_operation", f"禁止 SQL 节点: {node.key}")
        if isinstance(node, exp.Anonymous):
            function_name = node.name.casefold()
            if (
                function_name in EXTERNAL_FUNCTION_NAMES
                or function_name.startswith("read_")
                or function_name.endswith("_scan")
            ):
                raise EvaluationError("external_access_forbidden", f"禁止外部函数: {function_name}")
    cte_names = {cte.alias_or_name.casefold() for cte in expression.find_all(exp.CTE)}
    allowed = {table.casefold() for table in allowed_tables}
    referenced: set[str] = set()
    for table in expression.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise EvaluationError("external_access_forbidden", "FROM 不允许表函数")
        if table.catalog or (table.db and table.db.casefold() not in {"main"}):
            raise EvaluationError("schema_not_allowed", f"禁止 schema: {table.db or table.catalog}")
        table_name = table.name.casefold()
        if table_name not in allowed and table_name not in cte_names:
            raise EvaluationError("unknown_table", f"未知表: {table.name}")
        if table_name in allowed:
            referenced.add(table_name)
    return GuardedQuery(
        expression=expression,
        formatted_sql=expression.sql(dialect="duckdb", pretty=True),
        referenced_tables=sorted(referenced),
    )


def _query_worker(
    connection: Connection,
    database_path: str,
    sql: str,
    max_rows: int,
) -> None:
    database: duckdb.DuckDBPyConnection | None = None
    try:
        database = duckdb.connect(database_path, read_only=True)
        database.execute("SET enable_external_access=false")
        database.execute("SET threads=1")
        database.execute("SET memory_limit='512MB'")
        database.execute("SET TimeZone='UTC'")
        cursor = database.execute(sql)
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            connection.send({"error_code": "row_limit_exceeded", "message": "结果超过行数上限"})
            return
        columns = [
            {"name": str(item[0]), "type": str(item[1])} for item in (cursor.description or [])
        ]
        connection.send({"columns": columns, "rows": [list(row) for row in rows]})
    except Exception as exc:
        connection.send({"error_code": "sql_execution_error", "message": str(exc)})
    finally:
        if database is not None:
            database.close()
        connection.close()


def execute_guarded_query(
    query: GuardedQuery,
    database_path: Path,
    *,
    timeout_seconds: float = 5,
    max_rows: int = 10000,
) -> tuple[QueryResult, float]:
    if not database_path.is_file():
        raise EvaluationError("warehouse_missing", f"执行库不存在: {database_path}")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_query_worker,
        args=(child, str(database_path), query.formatted_sql, max_rows),
        daemon=True,
    )
    started = time.perf_counter()
    process.start()
    child.close()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(1)
        if process.is_alive():
            process.kill()
            process.join()
        parent.close()
        raise EvaluationError("query_timeout", f"查询超过 {timeout_seconds:.0f} 秒")
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not parent.poll():
        parent.close()
        raise EvaluationError("sql_worker_error", f"SQL worker 异常退出: {process.exitcode}")
    payload = parent.recv()
    parent.close()
    if "error_code" in payload:
        raise EvaluationError(str(payload["error_code"]), str(payload["message"]))
    return QueryResult.model_validate(payload), elapsed_ms


def load_gold(path: Path) -> QueryResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return QueryResult.model_validate(payload)


def _canonical_expression(expression: exp.Expression, aliases: dict[str, exp.Expression]) -> str:
    candidate = expression.copy()
    if isinstance(candidate, exp.Column) and not candidate.table:
        replacement = aliases.get(candidate.name.casefold())
        if replacement is not None:
            candidate = replacement.copy()
    return candidate.sql(dialect="duckdb").casefold()


def _window_matches(query: exp.Query, rule: WindowFunctionRule) -> int:
    matches = 0
    for window in query.find_all(exp.Window):
        function = window.this
        function_name = function.sql_name().upper()
        if function_name != rule.name:
            continue
        select = window.find_ancestor(exp.Select)
        aliases: dict[str, exp.Expression] = {}
        if select is not None:
            for projection in select.expressions:
                if isinstance(projection, exp.Alias):
                    aliases[projection.alias.casefold()] = projection.this
        partitions = [
            _canonical_expression(cast(exp.Expression, item), aliases)
            for item in (window.args.get("partition_by") or [])
        ]
        wanted_partitions = [
            _canonical_expression(
                cast(exp.Expression, sqlglot.parse_one(item, read="duckdb")), aliases
            )
            for item in rule.partition_columns
        ]
        order = window.args.get("order")
        actual_order: list[tuple[str, str]] = []
        if isinstance(order, exp.Order):
            for ordered in order.expressions:
                assert isinstance(ordered, exp.Ordered)
                actual_order.append(
                    (
                        _canonical_expression(cast(exp.Expression, ordered.this), aliases),
                        "DESC" if ordered.args.get("desc") else "ASC",
                    )
                )
        wanted_order = [
            (
                _canonical_expression(
                    cast(
                        exp.Expression,
                        sqlglot.parse_one(item.expression, read="duckdb"),
                    ),
                    aliases,
                ),
                item.direction,
            )
            for item in rule.order_by
        ]
        if partitions == wanted_partitions and actual_order == wanted_order:
            matches += 1
    return matches


def _query_depth(query: exp.Query) -> int:
    depths = []
    for select in query.find_all(exp.Select):
        depth = 1
        parent = select.parent
        while parent is not None:
            if isinstance(parent, exp.Select):
                depth += 1
            parent = parent.parent
        depths.append(depth)
    return max(depths, default=0)


def _table_aliases(select: exp.Select) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in select.find_all(exp.Table):
        if isinstance(table.this, exp.Identifier):
            aliases[(table.alias_or_name or table.name).casefold()] = table.name.casefold()
    return aliases


def _cte_has_measure(
    cte: exp.CTE,
    table_name: str,
    column_name: str,
    group_keys: list[str],
) -> bool:
    select = cte.this
    if not isinstance(select, exp.Select):
        return False
    aliases = _table_aliases(select)
    groups = select.args.get("group")
    grouped = {
        item.name.casefold()
        for item in (groups.expressions if isinstance(groups, exp.Group) else [])
        if isinstance(item, exp.Column)
    }
    if not {key.casefold() for key in group_keys}.issubset(grouped):
        return False
    for aggregate in select.find_all(exp.Sum):
        for column in aggregate.find_all(exp.Column):
            source = aliases.get(column.table.casefold(), column.table.casefold())
            if column.name.casefold() == column_name.casefold() and (
                source == table_name.casefold()
                or (not source and table_name.casefold() in aliases.values())
            ):
                return True
    return False


def _direct_final_tables(query: exp.Query) -> set[str]:
    if not isinstance(query, exp.Select):
        return set()
    tables: set[str] = set()
    from_clause = query.args.get("from_")
    if isinstance(from_clause, exp.From) and isinstance(from_clause.this, exp.Table):
        tables.add(from_clause.this.name.casefold())
    for join in query.args.get("joins") or []:
        if isinstance(join, exp.Join) and isinstance(join.this, exp.Table):
            tables.add(join.this.name.casefold())
    return tables


def _separate_preaggregation(query: exp.Query, rule: SeparateMeasurePreaggregationRule) -> bool:
    ctes = {cte.alias_or_name.casefold(): cte for cte in query.find_all(exp.CTE)}
    left = ctes.get(rule.left_cte.casefold())
    right = ctes.get(rule.right_cte.casefold())
    if left is None or right is None:
        return False
    if _direct_final_tables(query) != {rule.left_cte.casefold(), rule.right_cte.casefold()}:
        return False
    return _cte_has_measure(
        left,
        rule.left_measure_table,
        rule.left_measure_column,
        rule.group_keys,
    ) and _cte_has_measure(
        right,
        rule.right_measure_table,
        rule.right_measure_column,
        rule.group_keys,
    )


def evaluate_ast_rules(query: exp.Query, rules: Iterable[AstRule]) -> list[AstRuleResult]:
    results: list[AstRuleResult] = []
    for rule in rules:
        passed = False
        details: dict[str, Any] = {}
        if isinstance(rule, NodeCountRule):
            node_type = {"Join": exp.Join, "Case": exp.Case}[rule.node]
            count = sum(1 for _ in query.find_all(node_type))
            passed = count >= rule.min
            details = {"actual": count, "required": rule.min}
        elif isinstance(rule, JoinTypeRule):
            count = sum(
                1
                for join in query.find_all(exp.Join)
                if str(join.args.get("side") or "").upper() == rule.join_type
            )
            passed = count >= rule.min
            details = {"actual": count, "required": rule.min}
        elif isinstance(rule, CorrelatedSubqueryRule):
            count = sum(1 for scope in traverse_scope(query) if scope.is_correlated_subquery)
            passed = count >= rule.min
            details = {"actual": count, "required": rule.min}
        elif isinstance(rule, NotExistsRule):
            count = sum(
                1 for exists in query.find_all(exp.Exists) if isinstance(exists.parent, exp.Not)
            )
            passed = count >= rule.min
            details = {"actual": count, "required": rule.min}
        elif isinstance(rule, WindowFunctionRule):
            count = _window_matches(query, rule)
            passed = count >= rule.min
            details = {"actual": count, "required": rule.min}
        elif isinstance(rule, QueryDepthRule):
            depth = _query_depth(query)
            passed = depth >= rule.min
            details = {"actual": depth, "required": rule.min}
        elif isinstance(rule, CteCountRule):
            count = sum(1 for _ in query.find_all(exp.CTE))
            passed = count >= rule.min
            details = {"actual": count, "required": rule.min}
        elif isinstance(rule, AggregateCaseRule):
            count = sum(
                1 for aggregate in query.find_all(exp.Sum) if aggregate.find(exp.Case) is not None
            )
            passed = count >= rule.when_count
            details = {"actual": count, "required": rule.when_count}
        elif isinstance(rule, SeparateMeasurePreaggregationRule):
            passed = _separate_preaggregation(query, rule)
        results.append(AstRuleResult(id=rule.id, kind=rule.kind, passed=passed, details=details))
    return results


def score_case(
    *,
    protocol_strict: bool,
    guard_ok: bool,
    execution_ok: bool,
    diff: ResultDiff | None,
    ast_rules: list[AstRuleResult],
) -> ScoreBreakdown:
    protocol = 5.0 if protocol_strict else 0.0
    read_only_ast = 5.0 if guard_ok else 0.0
    execution = 10.0 if execution_ok else 0.0
    column_count = 5.0 if diff and diff.column_count_equal else 0.0
    column_names = 5.0 if diff and diff.column_names_equal else 0.0
    row_f1 = 45.0 * diff.f1 if diff else 0.0
    ordering = 10.0 if diff and diff.ordered_equal else 0.0
    if not guard_ok:
        capability = 0.0
    elif not ast_rules:
        capability = 15.0
    else:
        capability = 15.0 * sum(result.passed for result in ast_rules) / len(ast_rules)
    total = (
        protocol
        + read_only_ast
        + execution
        + column_count
        + column_names
        + row_f1
        + ordering
        + capability
    )
    return ScoreBreakdown(
        protocol=protocol,
        read_only_ast=read_only_ast,
        execution=execution,
        column_count=column_count,
        column_names=column_names,
        row_f1=row_f1,
        ordering=ordering,
        sql_capability=capability,
        total=round(total, 2),
        ast_rules=ast_rules,
    )


def evaluate_case(
    *,
    sql: str,
    warehouse_path: Path,
    gold_path: Path,
    allowed_tables: set[str],
    comparison: ComparisonConfig,
    required_ast: list[AstRule],
    protocol_strict: bool,
) -> EvaluationOutcome:
    try:
        guarded = parse_and_guard_sql(sql, allowed_tables)
    except EvaluationError as exc:
        return EvaluationOutcome(
            status="failed",
            score=score_case(
                protocol_strict=protocol_strict,
                guard_ok=False,
                execution_ok=False,
                diff=None,
                ast_rules=[],
            ),
            error_code=exc.code,
            error_message=str(exc),
        )
    ast_results = evaluate_ast_rules(guarded.expression, required_ast)
    try:
        actual, execution_ms = execute_guarded_query(
            guarded,
            warehouse_path,
            max_rows=comparison.max_rows,
        )
    except EvaluationError as exc:
        return EvaluationOutcome(
            status="failed",
            formatted_sql=guarded.formatted_sql,
            score=score_case(
                protocol_strict=protocol_strict,
                guard_ok=True,
                execution_ok=False,
                diff=None,
                ast_rules=ast_results,
            ),
            error_code=exc.code,
            error_message=str(exc),
        )
    expected = load_gold(gold_path)
    diff = compare_results(expected, actual, comparison)
    score = score_case(
        protocol_strict=protocol_strict,
        guard_ok=True,
        execution_ok=True,
        diff=diff,
        ast_rules=ast_results,
    )
    return EvaluationOutcome(
        status="completed",
        formatted_sql=guarded.formatted_sql,
        actual=actual,
        diff=diff,
        score=score,
        execution_ms=execution_ms,
    )


def weighted_average(values: Iterable[tuple[float, float]]) -> float:
    entries = list(values)
    denominator = sum(weight for _, weight in entries)
    if denominator == 0:
        return 0.0
    return sum(value * weight for value, weight in entries) / denominator


def attempt_statistics(scores: list[float]) -> dict[str, float]:
    if not scores:
        return {"mean": 0.0, "success_rate": 0.0, "stddev": 0.0}
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores) / len(scores)
    return {
        "mean": round(mean, 2),
        "success_rate": sum(score > 0 for score in scores) / len(scores),
        "stddev": math.sqrt(variance),
    }


def build_conclusion(report: dict[str, Any]) -> dict[str, Any]:
    models = report.get("models", [])
    scored = [model for model in models if isinstance(model.get("official_score"), (int, float))]
    if not scored:
        return {"status": "incomplete", "champions": [], "models": []}
    best = max(float(model["official_score"]) for model in scored)
    champions = [
        str(model["name"]) for model in scored if best - float(model["official_score"]) <= 1.0
    ]
    conclusions = []
    for model in scored:
        score = float(model["official_score"])
        label = "强" if score >= 90 else "可用但有短板" if score >= 75 else "暂不推荐"
        categories = model.get("categories", {})
        strengths = sorted(name for name, value in categories.items() if float(value) >= 90)
        weaknesses = sorted(name for name, value in categories.items() if float(value) < 70)
        conclusions.append(
            {
                "name": model["name"],
                "label": label,
                "strengths": strengths,
                "weaknesses": weaknesses,
            }
        )
    return {"status": "completed", "champions": champions, "models": conclusions}
