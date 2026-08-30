from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain import BenchmarkCaseDefinition, SemanticLayer


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorResponse(ApiModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str


class ModelProfileCreate(ApiModel):
    name: str
    adapter_kind: Literal["openai_compatible", "codex_cli", "claude_cli", "gemini_cli"]
    model_id: str
    base_url: str | None = None
    response_mode: Literal["json_schema", "json_object", "text"] = "json_schema"
    api_key: str | None = None
    api_key_env: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def one_secret_source(self) -> ModelProfileCreate:
        if self.api_key and self.api_key_env:
            raise ValueError("api_key and api_key_env are mutually exclusive")
        return self


class ModelProfilePatch(ApiModel):
    name: str | None = None
    model_id: str | None = None
    base_url: str | None = None
    response_mode: Literal["json_schema", "json_object", "text"] | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    parameters: dict[str, Any] | None = None
    enabled: bool | None = None


class ModelProfileOut(ApiModel):
    id: int
    name: str
    adapter_kind: str
    model_id: str
    base_url: str | None
    response_mode: str
    parameters: dict[str, Any]
    enabled: bool
    has_secret: bool
    secret_backend: Literal["keyring", "environment", "none"]
    health_status: str
    health_details: dict[str, Any]
    last_checked_at: datetime | None
    health_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SuiteDraftCreate(ApiModel):
    name: str
    description: str = ""
    dialect: Literal["duckdb"] = "duckdb"
    schema_sql: str
    seed_sql: str
    semantic: SemanticLayer
    prompt_template: str
    cases: list[BenchmarkCaseDefinition]


class SuiteDraftPatch(ApiModel):
    schema_sql: str | None = None
    seed_sql: str | None = None
    semantic: SemanticLayer | None = None
    prompt_template: str | None = None
    cases: list[BenchmarkCaseDefinition] | None = None


class PublishOut(ApiModel):
    suite_version_id: int
    content_hash: str
    manifest: dict[str, Any]


class RunCreate(ApiModel):
    suite_version_id: int
    model_profile_ids: list[int] = Field(min_length=1, max_length=6)
    case_ids: list[int] | None = None
    attempts: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def unique_models(self) -> RunCreate:
        if len(self.model_profile_ids) != len(set(self.model_profile_ids)):
            raise ValueError("model_profile_ids must be unique")
        return self


class RunCreated(ApiModel):
    id: int
    mode: Literal["single", "comparison"]
    status: str


class EventOut(ApiModel):
    seq: int
    event_type: str
    level: str
    created_at: datetime
    model_run_id: int | None
    case_run_id: int | None
    message: str
    payload: dict[str, Any]
