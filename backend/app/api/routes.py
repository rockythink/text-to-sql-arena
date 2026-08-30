from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.schemas import (
    ModelProfileCreate,
    ModelProfileOut,
    ModelProfilePatch,
    PublishOut,
    RunCreate,
    RunCreated,
    SuiteDraftCreate,
    SuiteDraftPatch,
)
from backend.app.config import settings
from backend.app.db import SessionLocal, get_session
from backend.app.domain import (
    AstRule,
    BenchmarkCaseDefinition,
    ComparisonConfig,
    SemanticLayer,
    StructureSnapshot,
    SuiteSource,
)
from backend.app.middleware import bootstrap_payload
from backend.app.models import (
    BenchmarkCase,
    BenchmarkSuite,
    CaseRun,
    ComparisonRun,
    ModelProfile,
    ModelRun,
    RunEvent,
    SuiteVersion,
)
from backend.app.security import secret_store
from backend.app.services.benchmark_engine import benchmark_engine
from backend.app.services.events import event_hub, event_writer
from backend.app.services.profiles import (
    check_profile,
    health_is_current,
    profile_public,
    secret_reference,
)
from backend.app.services.reporting import (
    EvidenceLookupError,
    build_case_evidence,
    build_run_report,
    build_run_snapshot,
)
from backend.app.services.suites import (
    SuiteValidationError,
    build_generation_request,
    validate_and_build,
)

router = APIRouter(prefix="/api")
TERMINAL_EVENTS = {"run.completed", "run.cancelled", "run.interrupted"}


def fail(status: int, code: str, message: str, details: Any = None) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, "details": details or {}},
    )


@router.get("/bootstrap")
async def bootstrap(response: Response) -> dict[str, Any]:
    return {
        **bootstrap_payload(response),
        "app_version": settings.app_version,
        "scorer_version": settings.scorer_version,
        "keyring_available": secret_store.available(),
    }


@router.get("/model-profiles", response_model=list[ModelProfileOut])
async def list_model_profiles(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    profiles = list(
        (
            await session.scalars(
                select(ModelProfile)
                .where(ModelProfile.deleted_at.is_(None))
                .order_by(ModelProfile.created_at)
            )
        ).all()
    )
    return [profile_public(profile) for profile in profiles]


@router.post("/model-profiles", response_model=ModelProfileOut)
async def create_model_profile(
    payload: ModelProfileCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        reference = secret_reference(payload.api_key, payload.api_key_env)
    except (RuntimeError, ValueError) as exc:
        raise fail(422, "secret_backend_unavailable", str(exc)) from exc
    profile = ModelProfile(
        name=payload.name,
        adapter_kind=payload.adapter_kind,
        model_id=payload.model_id,
        base_url=payload.base_url,
        response_mode=payload.response_mode,
        api_key_ref=reference,
        parameters_json=payload.parameters,
        enabled=payload.enabled,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile_public(profile)


@router.patch("/model-profiles/{profile_id}", response_model=ModelProfileOut)
async def patch_model_profile(
    profile_id: int,
    payload: ModelProfilePatch,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await session.get(ModelProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise fail(404, "profile_not_found", "模型配置不存在")
    updates = payload.model_dump(exclude_unset=True, exclude={"api_key", "api_key_env"})
    if "parameters" in updates:
        updates["parameters_json"] = updates.pop("parameters")
    for key, value in updates.items():
        setattr(profile, key, value)
    if payload.api_key is not None or payload.api_key_env is not None:
        try:
            replacement = secret_reference(payload.api_key, payload.api_key_env)
        except (RuntimeError, ValueError) as exc:
            raise fail(422, "secret_backend_unavailable", str(exc)) from exc
        secret_store.delete(profile.api_key_ref)
        profile.api_key_ref = replacement
    profile.health_status = "unknown"
    profile.health_details_json = {}
    profile.health_expires_at = None
    await session.commit()
    await session.refresh(profile)
    return profile_public(profile)


@router.delete("/model-profiles/{profile_id}")
async def delete_model_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    profile = await session.get(ModelProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise fail(404, "profile_not_found", "模型配置不存在")
    secret_store.delete(profile.api_key_ref)
    profile.api_key_ref = None
    profile.deleted_at = datetime.now(UTC)
    profile.enabled = False
    await session.commit()
    return {"status": "deleted"}


@router.post("/model-profiles/{profile_id}/check", response_model=ModelProfileOut)
async def check_model_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await session.get(ModelProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise fail(404, "profile_not_found", "模型配置不存在")
    await check_profile(profile)
    await session.commit()
    await session.refresh(profile)
    return profile_public(profile)


def case_to_dict(case: BenchmarkCase, include_reference: bool) -> dict[str, Any]:
    result = {
        "id": case.id,
        "stable_key": case.stable_key,
        "title": case.title,
        "category": case.category,
        "radar_dimension": case.radar_dimension,
        "difficulty": case.difficulty,
        "question": case.question,
        "required_ast": case.required_ast_json,
        "comparison": case.comparison_json,
        "weight": case.weight,
        "sort_order": case.sort_order,
    }
    if include_reference:
        result["reference_sql"] = case.reference_sql
    return result


@router.get("/suites")
async def list_suites(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    suites = list(
        (
            await session.scalars(
                select(BenchmarkSuite)
                .options(selectinload(BenchmarkSuite.versions).selectinload(SuiteVersion.cases))
                .order_by(BenchmarkSuite.created_at)
            )
        ).all()
    )
    return [
        {
            "id": suite.id,
            "name": suite.name,
            "description": suite.description,
            "versions": [
                {
                    "id": version.id,
                    "version": version.version,
                    "status": version.status,
                    "dialect": version.dialect,
                    "content_hash": version.content_hash,
                    "published_at": version.published_at,
                    "schema_sql": version.schema_sql,
                    "seed_sql": version.seed_sql,
                    "semantic": version.semantic_layer_json,
                    "prompt_template": version.prompt_template,
                    "structure": version.structure_snapshot_json,
                    "cases": [
                        case_to_dict(case, include_reference=True)
                        for case in sorted(version.cases, key=lambda item: item.sort_order)
                    ],
                }
                for version in sorted(suite.versions, key=lambda item: item.version)
            ],
        }
        for suite in suites
    ]


@router.get("/suite-versions/{version_id}/prompt-preview")
async def prompt_preview(
    version_id: int,
    case_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(SuiteVersion, BenchmarkCase)
            .join(BenchmarkCase, BenchmarkCase.suite_version_id == SuiteVersion.id)
            .where(SuiteVersion.id == version_id, BenchmarkCase.id == case_id)
        )
    ).one_or_none()
    if row is None:
        raise fail(404, "suite_case_not_found", "测试集版本或题目不存在")
    version, case = row
    if not version.structure_snapshot_json:
        raise fail(409, "structure_not_available", "测试集尚未生成结构快照")
    definition = BenchmarkCaseDefinition(
        stable_key=case.stable_key,
        title=case.title,
        category=case.category,
        radar_dimension=case.radar_dimension,
        difficulty=case.difficulty,
        question=case.question,
        reference_sql=case.reference_sql,
        required_ast=TypeAdapter(list[AstRule]).validate_python(case.required_ast_json),
        comparison=ComparisonConfig.model_validate(case.comparison_json),
        weight=case.weight,
        sort_order=case.sort_order,
    )
    request = build_generation_request(
        version.prompt_template,
        StructureSnapshot.model_validate(version.structure_snapshot_json),
        SemanticLayer.model_validate(version.semantic_layer_json),
        definition,
    )
    return {
        "case_id": case.id,
        "stable_key": case.stable_key,
        "prompt": request.prompt,
        "output_schema": request.output_schema,
    }


async def add_cases(
    session: AsyncSession,
    suite_version_id: int,
    cases: list[BenchmarkCaseDefinition],
) -> None:
    session.add_all(
        [
            BenchmarkCase(
                suite_version_id=suite_version_id,
                stable_key=case.stable_key,
                title=case.title,
                category=case.category,
                radar_dimension=case.radar_dimension,
                difficulty=case.difficulty,
                question=case.question,
                reference_sql=case.reference_sql,
                required_ast_json=[rule.model_dump(mode="json") for rule in case.required_ast],
                comparison_json=case.comparison.model_dump(mode="json"),
                weight=case.weight,
                sort_order=case.sort_order,
            )
            for case in cases
        ]
    )


@router.post("/suites")
async def create_suite(
    payload: SuiteDraftCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await session.scalar(select(BenchmarkSuite.id).where(BenchmarkSuite.name == payload.name)):
        raise fail(409, "suite_name_exists", "测试集名称已存在")
    suite = BenchmarkSuite(name=payload.name, description=payload.description)
    session.add(suite)
    await session.flush()
    version = SuiteVersion(
        suite_id=suite.id,
        version=1,
        status="draft",
        dialect=payload.dialect,
        schema_sql=payload.schema_sql,
        seed_sql=payload.seed_sql,
        semantic_layer_json=payload.semantic.model_dump(mode="json"),
        prompt_template=payload.prompt_template,
    )
    session.add(version)
    await session.flush()
    await add_cases(session, version.id, payload.cases)
    await session.commit()
    return {"suite_id": suite.id, "suite_version_id": version.id, "status": "draft"}


@router.post("/suites/{suite_id}/clone")
async def clone_suite_version(
    suite_id: int,
    source_version_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    source = await session.scalar(
        select(SuiteVersion)
        .options(selectinload(SuiteVersion.cases))
        .where(SuiteVersion.id == source_version_id, SuiteVersion.suite_id == suite_id)
    )
    if source is None:
        raise fail(404, "suite_version_not_found", "测试集版本不存在")
    latest = await session.scalar(
        select(func.max(SuiteVersion.version)).where(SuiteVersion.suite_id == suite_id)
    )
    clone = SuiteVersion(
        suite_id=suite_id,
        version=int(latest or 0) + 1,
        status="draft",
        dialect=source.dialect,
        schema_sql=source.schema_sql,
        seed_sql=source.seed_sql,
        semantic_layer_json=source.semantic_layer_json,
        prompt_template=source.prompt_template,
    )
    session.add(clone)
    await session.flush()
    definitions = [
        BenchmarkCaseDefinition(
            stable_key=case.stable_key,
            title=case.title,
            category=case.category,
            radar_dimension=case.radar_dimension,
            difficulty=case.difficulty,
            question=case.question,
            reference_sql=case.reference_sql,
            required_ast=TypeAdapter(list[AstRule]).validate_python(case.required_ast_json),
            comparison=ComparisonConfig.model_validate(case.comparison_json),
            weight=case.weight,
            sort_order=case.sort_order,
        )
        for case in source.cases
    ]
    await add_cases(session, clone.id, definitions)
    await session.commit()
    return {"suite_version_id": clone.id, "version": clone.version, "status": "draft"}


@router.patch("/suite-versions/{version_id}")
async def patch_suite_version(
    version_id: int,
    payload: SuiteDraftPatch,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    version = await session.get(SuiteVersion, version_id)
    if version is None:
        raise fail(404, "suite_version_not_found", "测试集版本不存在")
    if version.status != "draft":
        raise fail(409, "published_suite_immutable", "已发布版本不可修改，请先复制 draft")
    if payload.schema_sql is not None:
        version.schema_sql = payload.schema_sql
    if payload.seed_sql is not None:
        version.seed_sql = payload.seed_sql
    if payload.semantic is not None:
        version.semantic_layer_json = payload.semantic.model_dump(mode="json")
    if payload.prompt_template is not None:
        version.prompt_template = payload.prompt_template
    if payload.cases is not None:
        await session.execute(
            delete(BenchmarkCase).where(BenchmarkCase.suite_version_id == version_id)
        )
        await add_cases(session, version_id, payload.cases)
    await session.commit()
    return {"suite_version_id": version_id, "status": "draft"}


async def load_suite_source(
    session: AsyncSession, version_id: int
) -> tuple[SuiteVersion, SuiteSource]:
    version = await session.scalar(
        select(SuiteVersion)
        .options(selectinload(SuiteVersion.cases), selectinload(SuiteVersion.suite))
        .where(SuiteVersion.id == version_id)
    )
    if version is None:
        raise fail(404, "suite_version_not_found", "测试集版本不存在")
    definitions = [
        BenchmarkCaseDefinition(
            stable_key=case.stable_key,
            title=case.title,
            category=case.category,
            radar_dimension=case.radar_dimension,
            difficulty=case.difficulty,
            question=case.question,
            reference_sql=case.reference_sql,
            required_ast=TypeAdapter(list[AstRule]).validate_python(case.required_ast_json),
            comparison=ComparisonConfig.model_validate(case.comparison_json),
            weight=case.weight,
            sort_order=case.sort_order,
        )
        for case in sorted(version.cases, key=lambda item: item.sort_order)
    ]
    return version, SuiteSource(
        name=version.suite.name,
        description=version.suite.description,
        dialect="duckdb",
        schema_sql=version.schema_sql,
        seed_sql=version.seed_sql,
        semantic=SemanticLayer.model_validate(version.semantic_layer_json),
        prompt_template=version.prompt_template,
        cases=definitions,
    )


@router.post("/suite-versions/{version_id}/publish", response_model=PublishOut)
async def publish_suite_version(
    version_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    version, source = await load_suite_source(session, version_id)
    if version.status != "draft":
        raise fail(409, "published_suite_immutable", "仅 draft 可发布")
    try:
        result = await asyncio.to_thread(validate_and_build, source)
    except SuiteValidationError as exc:
        raise fail(
            422,
            "suite_validation_failed",
            "测试集校验失败",
            [issue.model_dump(mode="json") for issue in exc.issues],
        ) from exc
    duplicate = await session.scalar(
        select(SuiteVersion.id).where(
            SuiteVersion.content_hash == result.content_hash,
            SuiteVersion.id != version.id,
        )
    )
    if duplicate is not None:
        raise fail(409, "suite_content_exists", "相同内容已发布", {"version_id": duplicate})
    version.status = "published"
    version.content_hash = result.content_hash
    version.structure_snapshot_json = result.structure.model_dump(mode="json")
    version.published_at = datetime.now(UTC)
    await session.commit()
    return {
        "suite_version_id": version.id,
        "content_hash": result.content_hash,
        "manifest": result.manifest,
    }


async def ensure_profile_healthy(profile: ModelProfile) -> None:
    if not health_is_current(profile):
        await check_profile(profile)
    if profile.health_status != "healthy":
        raise fail(
            422,
            "profile_not_healthy",
            f"模型 {profile.name} 当前不可运行",
            {"health_status": profile.health_status, "health_details": profile.health_details_json},
        )


async def create_run_record(
    payload: RunCreate,
    session: AsyncSession,
    source_run_id: int | None = None,
) -> ComparisonRun:
    version = await session.scalar(
        select(SuiteVersion)
        .options(selectinload(SuiteVersion.cases))
        .where(SuiteVersion.id == payload.suite_version_id)
    )
    if version is None or version.status != "published" or not version.content_hash:
        raise fail(422, "suite_not_published", "运行要求 published 测试集")
    profiles = list(
        (
            await session.scalars(
                select(ModelProfile).where(
                    ModelProfile.id.in_(payload.model_profile_ids),
                    ModelProfile.deleted_at.is_(None),
                    ModelProfile.enabled.is_(True),
                )
            )
        ).all()
    )
    profile_by_id = {profile.id: profile for profile in profiles}
    if set(profile_by_id) != set(payload.model_profile_ids):
        raise fail(422, "profile_unavailable", "存在禁用、删除或不存在的模型配置")
    for profile_id in payload.model_profile_ids:
        await ensure_profile_healthy(profile_by_id[profile_id])
    cases = sorted(version.cases, key=lambda item: item.sort_order)
    if payload.case_ids is not None:
        selected = set(payload.case_ids)
        cases = [case for case in cases if case.id in selected]
        if {case.id for case in cases} != selected:
            raise fail(422, "case_not_in_suite", "存在不属于测试集版本的 case")
    if not cases:
        raise fail(422, "case_selection_empty", "至少选择一道题")
    run = ComparisonRun(
        source_run_id=source_run_id,
        suite_version_id=version.id,
        suite_content_hash=version.content_hash,
        selected_case_keys_json=[case.stable_key for case in cases],
        status="queued",
        attempts=payload.attempts,
        app_version_snapshot=settings.app_version,
        scorer_version_snapshot=settings.scorer_version,
        duckdb_version_snapshot=__import__("duckdb").__version__,
        sqlglot_version_snapshot=package_version("sqlglot"),
        output_contract_snapshot="query-plan-v1",
    )
    session.add(run)
    await session.flush()
    for order, profile_id in enumerate(payload.model_profile_ids):
        profile = profile_by_id[profile_id]
        model = ModelRun(
            comparison_run_id=run.id,
            model_profile_id=profile.id,
            selection_order=order,
            profile_name_snapshot=profile.name,
            adapter_kind_snapshot=profile.adapter_kind,
            base_url_snapshot=profile.base_url,
            response_mode_snapshot=profile.response_mode,
            requested_model_id=profile.model_id,
            resolved_model_id=profile.health_details_json.get("resolved_model_id"),
            parameters_snapshot_json=profile.parameters_json,
            api_key_ref_snapshot=profile.api_key_ref,
            cli_version_snapshot=profile.health_details_json.get("version"),
            isolation_snapshot_json=profile.health_details_json,
            status="queued",
        )
        session.add(model)
        await session.flush()
        session.add_all(
            [
                CaseRun(
                    model_run_id=model.id,
                    benchmark_case_id=case.id,
                    stable_case_key_snapshot=case.stable_key,
                    attempt=attempt,
                    status="queued",
                )
                for case in cases
                for attempt in range(1, payload.attempts + 1)
            ]
        )
    await session.flush()
    return run


@router.get("/runs")
async def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    runs = list(
        (
            await session.scalars(
                select(ComparisonRun).order_by(ComparisonRun.created_at.desc()).limit(limit)
            )
        ).all()
    )
    run_ids = [run.id for run in runs]
    models_by_run: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    if run_ids:
        rows = (
            await session.execute(
                select(ModelRun, ModelProfile)
                .outerjoin(ModelProfile, ModelProfile.id == ModelRun.model_profile_id)
                .where(ModelRun.comparison_run_id.in_(run_ids))
                .order_by(ModelRun.comparison_run_id, ModelRun.selection_order)
            )
        ).all()
        for model, profile in rows:
            models_by_run[model.comparison_run_id].append(
                {
                    "id": model.id,
                    "name": profile.name if profile else f"model-{model.model_profile_id}",
                    "requested_model_id": model.requested_model_id,
                    "status": model.status,
                    "official_score": model.official_score,
                }
            )
    return {
        "runs": [
            {
                "id": run.id,
                "source_run_id": run.source_run_id,
                "suite_version_id": run.suite_version_id,
                "suite_content_hash": run.suite_content_hash,
                "status": run.status,
                "attempts": run.attempts,
                "case_count": len(run.selected_case_keys_json),
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "models": models_by_run[run.id],
            }
            for run in runs
        ]
    }


@router.post("/runs", response_model=RunCreated)
async def create_run(
    payload: RunCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    run = await create_run_record(payload, session)
    await session.commit()
    await event_writer.emit(run.id, "run.created", "info", {"status": "queued"})
    benchmark_engine.launch(run.id)
    return {
        "id": run.id,
        "mode": "single" if len(payload.model_profile_ids) == 1 else "comparison",
        "status": "queued",
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int) -> dict[str, str]:
    try:
        status = await benchmark_engine.cancel(run_id)
    except LookupError as exc:
        raise fail(404, "run_not_found", "运行不存在") from exc
    return {"status": status}


@router.post("/runs/{run_id}/rerun", response_model=RunCreated)
async def rerun(
    run_id: int,
    mode: str = Query(default="exact", pattern="^(exact|current)$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    source = await session.get(ComparisonRun, run_id)
    if source is None:
        raise fail(404, "run_not_found", "运行不存在")
    source_models = list(
        (
            await session.scalars(
                select(ModelRun)
                .where(ModelRun.comparison_run_id == run_id)
                .order_by(ModelRun.selection_order)
            )
        ).all()
    )
    cases = list(
        (
            await session.scalars(
                select(BenchmarkCase).where(
                    BenchmarkCase.suite_version_id == source.suite_version_id,
                    BenchmarkCase.stable_key.in_(source.selected_case_keys_json),
                )
            )
        ).all()
    )
    case_by_key = {case.stable_key: case for case in cases}
    ordered_cases = [case_by_key[key] for key in source.selected_case_keys_json]
    if mode == "current":
        payload = RunCreate(
            suite_version_id=source.suite_version_id,
            model_profile_ids=[model.model_profile_id for model in source_models],
            case_ids=[case.id for case in ordered_cases],
            attempts=source.attempts,
        )
        run = await create_run_record(payload, session, source_run_id=run_id)
    else:
        run = ComparisonRun(
            source_run_id=run_id,
            suite_version_id=source.suite_version_id,
            suite_content_hash=source.suite_content_hash,
            selected_case_keys_json=list(source.selected_case_keys_json),
            status="queued",
            attempts=source.attempts,
            app_version_snapshot=source.app_version_snapshot,
            scorer_version_snapshot=source.scorer_version_snapshot,
            duckdb_version_snapshot=source.duckdb_version_snapshot,
            sqlglot_version_snapshot=source.sqlglot_version_snapshot,
            output_contract_snapshot=source.output_contract_snapshot,
        )
        session.add(run)
        await session.flush()
        for source_model in source_models:
            model = ModelRun(
                comparison_run_id=run.id,
                model_profile_id=source_model.model_profile_id,
                selection_order=source_model.selection_order,
                profile_name_snapshot=source_model.profile_name_snapshot,
                adapter_kind_snapshot=source_model.adapter_kind_snapshot,
                base_url_snapshot=source_model.base_url_snapshot,
                response_mode_snapshot=source_model.response_mode_snapshot,
                requested_model_id=source_model.requested_model_id,
                resolved_model_id=source_model.resolved_model_id,
                parameters_snapshot_json=source_model.parameters_snapshot_json,
                api_key_ref_snapshot=source_model.api_key_ref_snapshot,
                cli_version_snapshot=source_model.cli_version_snapshot,
                isolation_snapshot_json=source_model.isolation_snapshot_json,
                status="queued",
            )
            session.add(model)
            await session.flush()
            session.add_all(
                [
                    CaseRun(
                        model_run_id=model.id,
                        benchmark_case_id=case.id,
                        stable_case_key_snapshot=case.stable_key,
                        attempt=attempt,
                        status="queued",
                    )
                    for case in ordered_cases
                    for attempt in range(1, source.attempts + 1)
                ]
            )
    await session.commit()
    await event_writer.emit(
        run.id,
        "run.created",
        "info",
        {"status": "queued", "rerun_mode": mode, "source_run_id": run_id},
    )
    benchmark_engine.launch(run.id)
    return {
        "id": run.id,
        "mode": "single" if len(source_models) == 1 else "comparison",
        "status": "queued",
    }


async def run_snapshot(session: AsyncSession, run_id: int) -> dict[str, Any]:
    try:
        return await build_run_snapshot(session, run_id)
    except EvidenceLookupError as exc:
        raise fail(404, exc.code, str(exc)) from exc

@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await run_snapshot(session, run_id)


def sse_message(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False)
    return f"id: {event['seq']}\nevent: {event['event_type']}\ndata: {data}\n\n"


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: int,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    resume_after = max(last_event_id or 0, after_seq)

    async def generate() -> Any:
        async with event_hub.subscribe(run_id, lambda: event_writer.watermark(run_id)) as (
            queue,
            watermark,
        ):
            async with SessionLocal() as session:
                backlog = list(
                    (
                        await session.scalars(
                            select(RunEvent)
                            .where(
                                RunEvent.comparison_run_id == run_id,
                                RunEvent.seq > resume_after,
                                RunEvent.seq <= watermark,
                            )
                            .order_by(RunEvent.seq)
                        )
                    ).all()
                )
            emitted: set[int] = set()
            for stored in backlog:
                event = {
                    "seq": stored.seq,
                    "event_type": stored.event_type,
                    "level": stored.level,
                    "created_at": stored.created_at.isoformat(),
                    "model_run_id": stored.model_run_id,
                    "case_run_id": stored.case_run_id,
                    "message": stored.message,
                    "payload": stored.payload_json,
                }
                emitted.add(stored.seq)
                yield sse_message(event)
                if event["event_type"] in TERMINAL_EVENTS:
                    return
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event["seq"] in emitted:
                    continue
                emitted.add(cast(int, event["seq"]))
                yield sse_message(event)
                if event["event_type"] in TERMINAL_EVENTS:
                    break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/events/history")
async def event_history(
    run_id: int,
    model_run_ids: list[int] | None = Query(default=None),
    case_run_ids: list[int] | None = Query(default=None),
    levels: list[str] | None = Query(default=None),
    event_types: list[str] | None = Query(default=None),
    search: str | None = None,
    after_seq: int = Query(default=0, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    conditions = [RunEvent.comparison_run_id == run_id]
    if after_seq:
        conditions.append(RunEvent.seq > after_seq)
    if model_run_ids:
        conditions.append(RunEvent.model_run_id.in_(model_run_ids))
    if case_run_ids:
        conditions.append(RunEvent.case_run_id.in_(case_run_ids))
    if levels:
        conditions.append(RunEvent.level.in_(levels))
    if event_types:
        conditions.append(RunEvent.event_type.in_(event_types))
    if search:
        conditions.append(
            or_(RunEvent.message.ilike(f"%{search}%"), RunEvent.event_type.ilike(f"%{search}%"))
        )
    total = await session.scalar(
        select(func.count()).select_from(RunEvent).where(and_(*conditions))
    )
    events = list(
        (
            await session.scalars(
                select(RunEvent)
                .where(and_(*conditions))
                .order_by(RunEvent.seq)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return {
        "total": int(total or 0),
        "events": [
            {
                "seq": event.seq,
                "event_type": event.event_type,
                "level": event.level,
                "created_at": event.created_at,
                "model_run_id": event.model_run_id,
                "case_run_id": event.case_run_id,
                "message": event.message,
                "payload": event.payload_json,
            }
            for event in events
        ],
    }


@router.get("/case-runs/{case_run_id}")
async def get_case_run(
    case_run_id: int,
    include_reference: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        return await build_case_evidence(
            session, case_run_id, include_reference=include_reference
        )
    except EvidenceLookupError as exc:
        status = 404 if exc.code == "case_run_not_found" else 409
        raise fail(status, exc.code, str(exc)) from exc

@router.get("/runs/{run_id}/report")
async def run_report(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    report = build_run_report(await run_snapshot(session, run_id))
    return Response(
        content=json.dumps(report, ensure_ascii=False, default=str),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )
