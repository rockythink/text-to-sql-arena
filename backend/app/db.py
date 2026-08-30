from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def configure_sqlite(dbapi_connection: object, _: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def ensure_schema() -> None:
    from backend.app import models as _models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        table_columns: dict[str, set[str]] = {}
        for table in ("model_profiles", "comparison_runs", "model_runs", "case_runs"):
            table_columns[table] = {
                str(row[1])
                for row in (await connection.execute(text(f"PRAGMA table_info({table})"))).all()
            }

        additions = {
            "model_profiles": {
                "pricing_json": "JSON",
            },
            "comparison_runs": {
                "app_version_snapshot": "VARCHAR(32) NOT NULL DEFAULT '0.1.0'",
                "scorer_version_snapshot": "VARCHAR(32) NOT NULL DEFAULT '1.0.0'",
                "duckdb_version_snapshot": "VARCHAR(32) NOT NULL DEFAULT '1.5.5'",
                "sqlglot_version_snapshot": "VARCHAR(32) NOT NULL DEFAULT '30.17.0'",
                "output_contract_snapshot": ("VARCHAR(40) NOT NULL DEFAULT 'query-plan-v1'"),
            },
            "model_runs": {
                "profile_name_snapshot": "VARCHAR(120)",
                "pricing_snapshot_json": "JSON",
            },
            "case_runs": {
                "plan_json": "JSON",
                "assumptions_json": "JSON",
                "generation_ms": "FLOAT",
                "provider_request_id": "VARCHAR(255)",
            },
        }
        for table, columns in additions.items():
            for name, definition in columns.items():
                if name not in table_columns[table]:
                    await connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    )
        await connection.execute(
            text(
                """
                UPDATE model_runs
                SET profile_name_snapshot = (
                    SELECT model_profiles.name
                    FROM model_profiles
                    WHERE model_profiles.id = model_runs.model_profile_id
                )
                WHERE profile_name_snapshot IS NULL
                """
            )
        )


async def recover_interrupted_runs() -> list[int]:
    from backend.app.models import CaseRun, ComparisonRun, ModelRun, RunEvent

    affected: set[int] = set()
    async with SessionLocal.begin() as session:
        rows = await session.execute(
            text("SELECT id FROM comparison_runs WHERE status IN ('queued','running','cancelling')")
        )
        for run_id in rows.scalars():
            affected.add(int(run_id))
        await session.execute(
            update(ComparisonRun)
            .where(ComparisonRun.status.in_(["queued", "running", "cancelling"]))
            .values(status="interrupted")
        )
        await session.execute(
            update(ModelRun)
            .where(ModelRun.status.in_(["queued", "running", "cancelling"]))
            .values(status="interrupted")
        )
        await session.execute(
            update(CaseRun)
            .where(
                CaseRun.status.in_(["queued", "generating", "validating", "executing", "scoring"])
            )
            .values(status="interrupted")
        )
        for run_id in sorted(affected):
            result = await session.execute(
                update(ComparisonRun)
                .where(ComparisonRun.id == run_id)
                .values(next_event_seq=ComparisonRun.next_event_seq + 1)
                .returning(ComparisonRun.next_event_seq)
            )
            seq = int(result.scalar_one())
            session.add(
                RunEvent(
                    comparison_run_id=run_id,
                    seq=seq,
                    level="warning",
                    event_type="run.interrupted",
                    message="应用重启，未完成运行已中断",
                    payload_json={"status": "interrupted"},
                )
            )
    return sorted(affected)
