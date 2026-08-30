from __future__ import annotations

import argparse
import asyncio
import json
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import duckdb
import yaml
from sqlalchemy import select

from backend.app.config import settings
from backend.app.db import SessionLocal
from backend.app.domain import SuiteSource
from backend.app.models import BenchmarkCase, BenchmarkSuite, SuiteVersion
from backend.app.services.suites import canonical_bytes, parse_suite_source, validate_and_build

BUILTIN_DIR = Path(__file__).parent / "data" / "retail-analytics-v1"


def load_builtin_source(source_dir: Path = BUILTIN_DIR) -> SuiteSource:
    semantic = json.loads((source_dir / "semantic.json").read_text(encoding="utf-8"))
    cases = yaml.safe_load((source_dir / "cases.yaml").read_text(encoding="utf-8"))
    return parse_suite_source(
        {
            "name": "retail-analytics-v1",
            "description": "固定可复现的零售分析 Text-to-SQL 基准",
            "dialect": "duckdb",
            "schema_sql": (source_dir / "schema.sql").read_text(encoding="utf-8"),
            "seed_sql": (source_dir / "seed.sql").read_text(encoding="utf-8"),
            "semantic": semantic,
            "prompt_template": (source_dir / "prompt.md").read_text(encoding="utf-8"),
            "cases": cases,
        }
    )


def expected_lock(result_manifest: dict[str, Any], content_hash: str) -> dict[str, Any]:
    gold = result_manifest["gold"]
    assert isinstance(gold, dict)
    return {
        "content_hash": content_hash,
        "duckdb_version": duckdb.__version__,
        "sqlglot_version": package_version("sqlglot"),
        "scorer_version": settings.scorer_version,
        "gold_digests": {
            key: value["digest"] for key, value in sorted(gold.items()) if isinstance(value, dict)
        },
    }


async def persist_builtin(source: SuiteSource, content_hash: str, structure: dict[str, Any]) -> int:
    async with SessionLocal.begin() as session:
        suite = await session.scalar(
            select(BenchmarkSuite).where(BenchmarkSuite.name == source.name)
        )
        if suite is None:
            suite = BenchmarkSuite(name=source.name, description=source.description)
            session.add(suite)
            await session.flush()
        existing = await session.scalar(
            select(SuiteVersion).where(SuiteVersion.content_hash == content_hash)
        )
        if existing is not None:
            return existing.id
        latest = await session.scalar(
            select(SuiteVersion.version)
            .where(SuiteVersion.suite_id == suite.id)
            .order_by(SuiteVersion.version.desc())
            .limit(1)
        )
        version = SuiteVersion(
            suite_id=suite.id,
            version=(latest or 0) + 1,
            status="published",
            dialect="duckdb",
            schema_sql=source.schema_sql,
            seed_sql=source.seed_sql,
            semantic_layer_json=source.semantic.model_dump(mode="json"),
            prompt_template=source.prompt_template,
            structure_snapshot_json=structure,
            content_hash=content_hash,
            published_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        session.add(version)
        await session.flush()
        session.add_all(
            [
                BenchmarkCase(
                    suite_version_id=version.id,
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
                for case in source.cases
            ]
        )
        return version.id


async def bootstrap_builtin(write_lock: bool = False) -> dict[str, Any]:
    source = load_builtin_source()
    result = validate_and_build(source)
    lock = expected_lock(result.manifest, result.content_hash)
    lock_path = BUILTIN_DIR / "suite.lock.json"
    if write_lock:
        lock_path.write_bytes(canonical_bytes(lock) + b"\n")
    else:
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        if existing != lock:
            raise RuntimeError(
                "suite.lock.json mismatch; inspect source changes and run "
                "with --write-lock explicitly"
            )
    version_id = await persist_builtin(
        source,
        result.content_hash,
        result.structure.model_dump(mode="json"),
    )
    return {"suite_version_id": version_id, **lock}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-lock", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(bootstrap_builtin(args.write_lock)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
