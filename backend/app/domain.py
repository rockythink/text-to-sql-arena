from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticEntity(StrictModel):
    name: str
    table: str
    description: str
    grain: str
    primary_key: list[str]


class SemanticRelationship(StrictModel):
    from_entity: str
    to_entity: str
    sql_on: str
    cardinality: str


class SemanticMetric(StrictModel):
    name: str
    expression: str
    description: str
    grain: str
    filters: list[str] = Field(default_factory=list)


class SemanticDimension(StrictModel):
    name: str
    expression: str
    data_type: str
    description: str


class SemanticLayer(StrictModel):
    entities: list[SemanticEntity]
    relationships: list[SemanticRelationship]
    metrics: list[SemanticMetric]
    dimensions: list[SemanticDimension]
    business_rules: list[str]


class StructureColumn(StrictModel):
    name: str
    data_type: str
    nullable: bool


class StructureForeignKey(StrictModel):
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]


class StructureTable(StrictModel):
    name: str
    columns: list[StructureColumn]
    primary_key: list[str]
    foreign_keys: list[StructureForeignKey]


class StructureSnapshot(StrictModel):
    tables: list[StructureTable]
    semantic_relationships: list[SemanticRelationship]


class AstRuleBase(StrictModel):
    id: str


class NodeCountRule(AstRuleBase):
    kind: Literal["min_node_count"]
    node: Literal["Join", "Case"]
    min: int = Field(ge=1)


class JoinTypeRule(AstRuleBase):
    kind: Literal["join_type"]
    join_type: Literal["LEFT"]
    min: int = Field(ge=1)


class CorrelatedSubqueryRule(AstRuleBase):
    kind: Literal["correlated_subquery"]
    min: int = Field(ge=1)


class NotExistsRule(AstRuleBase):
    kind: Literal["not_exists"]
    min: int = Field(ge=1)


class WindowOrder(StrictModel):
    expression: str
    direction: Literal["ASC", "DESC"]


class WindowFunctionRule(AstRuleBase):
    kind: Literal["window_function"]
    name: Literal["ROW_NUMBER", "SUM", "LAG"]
    partition_columns: list[str]
    order_by: list[WindowOrder]
    min: int = Field(ge=1)


class QueryDepthRule(AstRuleBase):
    kind: Literal["query_depth"]
    min: int = Field(ge=1)


class CteCountRule(AstRuleBase):
    kind: Literal["cte_count"]
    min: int = Field(ge=1)


class AggregateCaseRule(AstRuleBase):
    kind: Literal["aggregate_case_count"]
    aggregate: Literal["SUM"]
    when_count: int = Field(ge=1)


class SeparateMeasurePreaggregationRule(AstRuleBase):
    kind: Literal["separate_measure_preaggregation"]
    left_cte: str
    left_measure_table: str
    left_measure_column: str
    right_cte: str
    right_measure_table: str
    right_measure_column: str
    group_keys: list[str]


AstRule = Annotated[
    NodeCountRule
    | JoinTypeRule
    | CorrelatedSubqueryRule
    | NotExistsRule
    | WindowFunctionRule
    | QueryDepthRule
    | CteCountRule
    | AggregateCaseRule
    | SeparateMeasurePreaggregationRule,
    Field(discriminator="kind"),
]


class ComparisonConfig(StrictModel):
    row_order_significant: bool
    duplicate_policy: Literal["multiset"]
    decimal_scale: int = Field(ge=0, le=12)
    abs_tolerance: str
    rel_tolerance: str
    max_rows: int = Field(ge=1, le=10000)


class BenchmarkCaseDefinition(StrictModel):
    stable_key: str
    title: str
    category: str
    radar_dimension: str = Field(min_length=1, max_length=100)
    difficulty: str
    question: str
    reference_sql: str
    required_ast: list[AstRule] = Field(default_factory=list)
    comparison: ComparisonConfig
    weight: float = Field(gt=0)
    sort_order: int = Field(ge=1)


class SuiteSource(StrictModel):
    name: str
    description: str
    dialect: Literal["duckdb"]
    schema_sql: str
    seed_sql: str
    semantic: SemanticLayer
    prompt_template: str
    cases: list[BenchmarkCaseDefinition]

    @field_validator("cases")
    @classmethod
    def cases_must_be_unique(
        cls, value: list[BenchmarkCaseDefinition]
    ) -> list[BenchmarkCaseDefinition]:
        keys = [case.stable_key for case in value]
        if len(keys) != len(set(keys)):
            raise ValueError("case stable_key must be unique")
        orders = [case.sort_order for case in value]
        if len(orders) != len(set(orders)):
            raise ValueError("case sort_order must be unique")
        return value


class QueryPlan(StrictModel):
    grain: str
    sources: list[str]
    joins: list[str]
    filters: list[str]
    metrics: list[str]
    steps: list[str]
    risks: list[str]


class GenerationOutput(StrictModel):
    plan: QueryPlan
    sql: str
    summary: str
    assumptions: list[str]


class GenerationRequest(StrictModel):
    case_key: str
    prompt: str
    output_schema: dict[str, object]


class ValidationIssue(StrictModel):
    code: str
    message: str
    location: str


class PublishResult(StrictModel):
    content_hash: str
    artifact_dir: str
    structure: StructureSnapshot
    manifest: dict[str, object]
