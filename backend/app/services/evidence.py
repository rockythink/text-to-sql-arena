from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.config import settings
from backend.app.db import SessionLocal, ensure_schema
from backend.app.domain import BenchmarkCaseDefinition, ComparisonConfig, SemanticLayer, SuiteSource
from backend.app.models import (
    BenchmarkCase,
    BenchmarkSuite,
    CaseRun,
    ComparisonRun,
    ModelRun,
    RunEvent,
    SuiteVersion,
)
from backend.app.security import redact_secrets
from backend.app.services.reporting import (
    build_case_evidence,
    build_run_report,
    build_run_snapshot,
)
from backend.app.services.suites import compute_content_hash, validate_and_build

EVIDENCE_SCHEMA_VERSION = "text-to-sql-evidence-v1"
_TEMP_PATH = re.compile(r"(?:/private)?/var/folders/[^\s\"']+/T/llm-test-[^\s\"']+")
_USER_PATH = re.compile(r"/(?:Users|home)/[^/\s\"']+")
_FORBIDDEN_PUBLIC_PATTERNS = {
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "google_key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "bearer_secret": re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]{16,}"),
    "absolute_user_path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
}


def public_sanitize(value: Any) -> Any:
    safe = redact_secrets(value)
    if isinstance(safe, str):
        text = safe.replace(str(settings.root_dir), "$PROJECT_ROOT")
        text = text.replace(str(Path.home()), "$HOME")
        text = _USER_PATH.sub("$HOME", text)
        return _TEMP_PATH.sub("$TMPDIR/llm-test-<redacted>", text)
    if isinstance(safe, Mapping):
        return {str(key): public_sanitize(item) for key, item in safe.items()}
    if isinstance(safe, Sequence) and not isinstance(safe, (bytes, bytearray)):
        return [public_sanitize(item) for item in safe]
    if isinstance(safe, Path):
        return public_sanitize(str(safe))
    if isinstance(safe, (datetime, date)):
        return safe.isoformat()
    return safe


def _json_bytes(value: Any, *, pretty: bool = True) -> bytes:
    return (
        json.dumps(
            public_sanitize(value),
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            default=str,
        ).encode("utf-8")
        + b"\n"
    )


def _assert_public(data: bytes, path: Path) -> None:
    text = data.decode("utf-8", errors="ignore")
    for name, pattern in _FORBIDDEN_PUBLIC_PATTERNS.items():
        if pattern.search(text):
            raise RuntimeError(f"Public evidence contains {name}: {path}")


def _write_json(path: Path, value: Any) -> None:
    data = _json_bytes(value)
    _assert_public(data, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_jsonl(path: Path, values: Sequence[Any]) -> None:
    lines = [_json_bytes(value, pretty=False).rstrip(b"\n") for value in values]
    data = b"\n".join(lines) + (b"\n" if lines else b"")
    _assert_public(data, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_manifest(directory: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    records = [
        {
            "path": str(path.relative_to(directory)),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "bundle-manifest.json"
    ]
    bundle_sha256 = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        **metadata,
        "bundle_sha256": bundle_sha256,
        "files": records,
    }
    _write_json(directory / "bundle-manifest.json", manifest)
    return manifest


def _suite_source(suite: BenchmarkSuite, version: SuiteVersion) -> SuiteSource:
    cases = [
        BenchmarkCaseDefinition(
            stable_key=case.stable_key,
            title=case.title,
            category=case.category,
            radar_dimension=case.radar_dimension,
            difficulty=case.difficulty,
            question=case.question,
            reference_sql=case.reference_sql,
            required_ast=case.required_ast_json,
            comparison=ComparisonConfig.model_validate(case.comparison_json),
            weight=case.weight,
            sort_order=case.sort_order,
        )
        for case in sorted(version.cases, key=lambda item: item.sort_order)
    ]
    return SuiteSource(
        name=suite.name,
        description=suite.description,
        dialect="duckdb",
        schema_sql=version.schema_sql,
        seed_sql=version.seed_sql,
        semantic=SemanticLayer.model_validate(version.semantic_layer_json),
        prompt_template=version.prompt_template,
        cases=cases,
    )


def _write_suite_source(directory: Path, source: SuiteSource) -> None:
    source_dir = directory / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "schema.sql").write_text(source.schema_sql, encoding="utf-8")
    (source_dir / "seed.sql").write_text(source.seed_sql, encoding="utf-8")
    (source_dir / "prompt.md").write_text(source.prompt_template, encoding="utf-8")
    _write_json(source_dir / "semantic.json", source.semantic.model_dump(mode="json"))
    cases = [case.model_dump(mode="json") for case in source.cases]
    cases_yaml = yaml.safe_dump(cases, allow_unicode=True, sort_keys=False).encode("utf-8")
    _assert_public(cases_yaml, source_dir / "cases.yaml")
    (source_dir / "cases.yaml").write_bytes(cases_yaml)


async def _export_suites(staging: Path) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        suites = list(
            (
                await session.scalars(
                    select(BenchmarkSuite)
                    .options(selectinload(BenchmarkSuite.versions).selectinload(SuiteVersion.cases))
                    .order_by(BenchmarkSuite.id)
                )
            ).all()
        )
    index: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="llm-test-evidence-suites-") as temp_name:
        artifact_root = Path(temp_name)
        for suite in suites:
            for version in sorted(suite.versions, key=lambda item: item.version):
                if version.status != "published" or not version.content_hash:
                    continue
                source = _suite_source(suite, version)
                computed = compute_content_hash(source)
                if computed != version.content_hash:
                    raise RuntimeError(
                        f"Suite version {version.id} hash mismatch: "
                        f"{computed} != {version.content_hash}"
                    )
                published = validate_and_build(source, artifact_root)
                if published.content_hash != version.content_hash:
                    raise RuntimeError(f"Suite version {version.id} rebuilt with different hash")
                target = staging / "suites" / version.content_hash
                _write_suite_source(target, source)
                artifact = Path(published.artifact_dir)
                shutil.copy2(artifact / "manifest.json", target / "artifact-manifest.json")
                shutil.copytree(artifact / "gold", target / "gold")
                _write_json(
                    target / "suite.json",
                    {
                        "suite_id": suite.id,
                        "suite_version_id": version.id,
                        "name": suite.name,
                        "description": suite.description,
                        "version": version.version,
                        "status": version.status,
                        "dialect": version.dialect,
                        "content_hash": version.content_hash,
                        "published_at": version.published_at,
                        "structure": version.structure_snapshot_json,
                        "warehouse": {
                            "committed": False,
                            "reason": (
                                "Deterministically rebuilt from source/schema.sql "
                                "and source/seed.sql"
                            ),
                        },
                    },
                )
                manifest = _bundle_manifest(
                    target,
                    {
                        "kind": "suite",
                        "suite_version_id": version.id,
                        "content_hash": version.content_hash,
                    },
                )
                index.append(
                    {
                        "suite_version_id": version.id,
                        "version": version.version,
                        "content_hash": version.content_hash,
                        "path": f"suites/{version.content_hash}",
                        "bundle_sha256": manifest["bundle_sha256"],
                    }
                )
    return index


async def _export_runs(staging: Path) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        runs = list(
            (
                await session.scalars(select(ComparisonRun).order_by(ComparisonRun.id))
            ).all()
        )
        index: list[dict[str, Any]] = []
        for run in runs:
            target = staging / "runs" / f"run-{run.id:04d}"
            target.mkdir(parents=True, exist_ok=True)
            snapshot = await build_run_snapshot(session, run.id)
            report = build_run_report(snapshot)
            _write_json(target / "report.json", report)
            events = list(
                (
                    await session.scalars(
                        select(RunEvent)
                        .where(RunEvent.comparison_run_id == run.id)
                        .order_by(RunEvent.seq)
                    )
                ).all()
            )
            event_documents = [
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
            ]
            _write_jsonl(target / "events.jsonl", event_documents)
            case_ids = list(
                (
                    await session.scalars(
                        select(CaseRun.id)
                        .join(ModelRun, ModelRun.id == CaseRun.model_run_id)
                        .join(BenchmarkCase, BenchmarkCase.id == CaseRun.benchmark_case_id)
                        .where(ModelRun.comparison_run_id == run.id)
                        .order_by(
                            ModelRun.selection_order,
                            BenchmarkCase.sort_order,
                            CaseRun.attempt,
                        )
                    )
                ).all()
            )
            for case_id in case_ids:
                case = await session.get(CaseRun, case_id)
                assert case is not None
                document = await build_case_evidence(
                    session,
                    case_id,
                    include_reference=case.status == "completed",
                    artifact_root=staging / "suites",
                )
                _write_json(target / "cases" / f"case-run-{case_id:05d}.json", document)
            manifest = _bundle_manifest(
                target,
                {
                    "kind": "run",
                    "run_id": run.id,
                    "status": run.status,
                    "suite_content_hash": run.suite_content_hash,
                    "event_count": len(events),
                    "case_run_count": len(case_ids),
                },
            )
            index.append(
                {
                    "run_id": run.id,
                    "status": run.status,
                    "suite_content_hash": run.suite_content_hash,
                    "path": f"runs/run-{run.id:04d}",
                    "bundle_sha256": manifest["bundle_sha256"],
                }
            )
    return index


def _prepare_output_path(output_dir: Path) -> Path:
    output = output_dir.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


async def export_all_evidence(output_dir: Path) -> dict[str, Any]:
    await ensure_schema()
    output = _prepare_output_path(output_dir)
    with tempfile.TemporaryDirectory(prefix="llm-test-evidence-export-", dir=output.parent) as name:
        staging = Path(name)
        suites = await _export_suites(staging)
        runs = await _export_runs(staging)
        index = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "exported_at": datetime.now(UTC),
            "scope": "all published suite versions and all persisted runs",
            "suite_count": len(suites),
            "run_count": len(runs),
            "suites": suites,
            "runs": runs,
        }
        _write_json(staging / "index.json", index)
        output.mkdir(parents=True, exist_ok=True)
        for generated in ("runs", "suites", "index.json"):
            destination = output / generated
            if destination.is_dir():
                shutil.rmtree(destination)
            elif destination.exists():
                destination.unlink()
            shutil.move(str(staging / generated), destination)
    verify_evidence(output)
    return index


def _verify_bundle(directory: Path) -> str:
    manifest_path = directory / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("files")
    if not isinstance(records, list):
        raise RuntimeError(f"Invalid bundle manifest: {manifest_path}")
    for record in records:
        path = directory / str(record["path"])
        if not path.is_file():
            raise RuntimeError(f"Missing evidence file: {path}")
        if _sha256(path) != record["sha256"]:
            raise RuntimeError(f"Evidence digest mismatch: {path}")
        if path.stat().st_size != record["bytes"]:
            raise RuntimeError(f"Evidence size mismatch: {path}")
    expected = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if expected != manifest.get("bundle_sha256"):
        raise RuntimeError(f"Bundle digest mismatch: {directory}")
    return expected


def verify_evidence(output_dir: Path) -> dict[str, int]:
    output = output_dir.resolve()
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    suite_count = 0
    run_count = 0
    for record in index.get("suites", []):
        digest = _verify_bundle(output / record["path"])
        if digest != record["bundle_sha256"]:
            raise RuntimeError(f"Suite index digest mismatch: {record['path']}")
        suite_count += 1
    for record in index.get("runs", []):
        digest = _verify_bundle(output / record["path"])
        if digest != record["bundle_sha256"]:
            raise RuntimeError(f"Run index digest mismatch: {record['path']}")
        run_count += 1
    if suite_count != index.get("suite_count") or run_count != index.get("run_count"):
        raise RuntimeError("Evidence index count mismatch")
    return {"suite_count": suite_count, "run_count": run_count}
