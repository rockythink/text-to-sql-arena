from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

TEST_VAR_DIR = Path(tempfile.mkdtemp(prefix="llm-test-pytest-"))
os.environ["LLM_TEST_VAR_DIR"] = str(TEST_VAR_DIR)
os.environ["LLM_TEST_DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_VAR_DIR / 'app.db'}"


@pytest.fixture(scope="session", autouse=True)
def isolated_database() -> Iterator[None]:
    from backend.app import models as _models  # noqa: F401
    from backend.app.db import Base, engine

    async def create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create())
    yield
    asyncio.run(engine.dispose())
    shutil.rmtree(TEST_VAR_DIR, ignore_errors=True)
