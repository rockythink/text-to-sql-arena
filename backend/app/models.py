from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModelProfile(TimestampMixin, Base):
    __tablename__ = "model_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    adapter_kind: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str | None] = mapped_column(String(500))
    response_mode: Mapped[str] = mapped_column(String(24), default="json_schema")
    api_key_ref: Mapped[str | None] = mapped_column(String(300))
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_status: Mapped[str] = mapped_column(String(24), default="unknown")
    health_details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BenchmarkSuite(Base):
    __tablename__ = "benchmark_suites"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    versions: Mapped[list[SuiteVersion]] = relationship(back_populates="suite")


class SuiteVersion(Base):
    __tablename__ = "suite_versions"
    __table_args__ = (UniqueConstraint("suite_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    suite_id: Mapped[int] = mapped_column(ForeignKey("benchmark_suites.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    dialect: Mapped[str] = mapped_column(String(24), default="duckdb")
    schema_sql: Mapped[str] = mapped_column(Text)
    seed_sql: Mapped[str] = mapped_column(Text)
    semantic_layer_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_template: Mapped[str] = mapped_column(Text)
    structure_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    content_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suite: Mapped[BenchmarkSuite] = relationship(back_populates="versions")
    cases: Mapped[list[BenchmarkCase]] = relationship(back_populates="suite_version")


class BenchmarkCase(Base):
    __tablename__ = "benchmark_cases"
    __table_args__ = (UniqueConstraint("suite_version_id", "stable_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    suite_version_id: Mapped[int] = mapped_column(ForeignKey("suite_versions.id"))
    stable_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(80))
    radar_dimension: Mapped[str] = mapped_column(String(40))
    difficulty: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    reference_sql: Mapped[str] = mapped_column(Text)
    required_ast_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    comparison_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    sort_order: Mapped[int] = mapped_column(Integer)
    suite_version: Mapped[SuiteVersion] = relationship(back_populates="cases")


class ComparisonRun(Base):
    __tablename__ = "comparison_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("comparison_runs.id"))
    suite_version_id: Mapped[int] = mapped_column(ForeignKey("suite_versions.id"))
    suite_content_hash: Mapped[str] = mapped_column(String(64))
    selected_case_keys_json: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    next_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    app_version_snapshot: Mapped[str] = mapped_column(String(32), default="0.1.0")
    scorer_version_snapshot: Mapped[str] = mapped_column(String(32), default="1.0.0")
    duckdb_version_snapshot: Mapped[str] = mapped_column(String(32), default="unknown")
    sqlglot_version_snapshot: Mapped[str] = mapped_column(String(32), default="unknown")
    output_contract_snapshot: Mapped[str] = mapped_column(String(40), default="query-plan-v1")


class ModelRun(Base):
    __tablename__ = "model_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_run_id: Mapped[int] = mapped_column(ForeignKey("comparison_runs.id"))
    model_profile_id: Mapped[int] = mapped_column(ForeignKey("model_profiles.id"))
    profile_name_snapshot: Mapped[str | None] = mapped_column(String(120))
    selection_order: Mapped[int] = mapped_column(Integer)
    adapter_kind_snapshot: Mapped[str] = mapped_column(String(32))
    base_url_snapshot: Mapped[str | None] = mapped_column(String(500))
    response_mode_snapshot: Mapped[str] = mapped_column(String(24))
    requested_model_id: Mapped[str] = mapped_column(String(200))
    resolved_model_id: Mapped[str | None] = mapped_column(String(200))
    parameters_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    api_key_ref_snapshot: Mapped[str | None] = mapped_column(String(300))
    cli_version_snapshot: Mapped[str | None] = mapped_column(String(120))
    isolation_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    official_score: Mapped[float | None] = mapped_column(Float)
    conclusion_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class CaseRun(Base):
    __tablename__ = "case_runs"
    __table_args__ = (UniqueConstraint("model_run_id", "benchmark_case_id", "attempt"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"))
    benchmark_case_id: Mapped[int] = mapped_column(ForeignKey("benchmark_cases.id"))
    stable_case_key_snapshot: Mapped[str] = mapped_column(String(120))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    prompt_snapshot: Mapped[str | None] = mapped_column(Text)
    raw_output: Mapped[str | None] = mapped_column(Text)
    plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    assumptions_json: Mapped[list[str] | None] = mapped_column(JSON)
    visible_summary: Mapped[str | None] = mapped_column(Text)
    generated_sql: Mapped[str | None] = mapped_column(Text)
    formatted_sql: Mapped[str | None] = mapped_column(Text)
    execution_ms: Mapped[float | None] = mapped_column(Float)
    token_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    generation_ms: Mapped[float | None] = mapped_column(Float)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    expected_digest: Mapped[str | None] = mapped_column(String(64))
    actual_digest: Mapped[str | None] = mapped_column(String(64))
    result_preview_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    score_breakdown_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("comparison_run_id", "seq"),
        Index("ix_run_events_run_seq", "comparison_run_id", "seq"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_run_id: Mapped[int] = mapped_column(ForeignKey("comparison_runs.id"))
    model_run_id: Mapped[int | None] = mapped_column(ForeignKey("model_runs.id"))
    case_run_id: Mapped[int | None] = mapped_column(ForeignKey("case_runs.id"))
    seq: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
