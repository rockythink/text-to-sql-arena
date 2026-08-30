from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    root_dir: Path
    var_dir: Path
    database_url: str
    static_dir: Path
    host: str
    port: int
    app_version: str
    scorer_version: str


def get_settings() -> Settings:
    root = Path(__file__).resolve().parents[2]
    var_dir = Path(os.environ.get("LLM_TEST_VAR_DIR", root / "var")).resolve()
    host = os.environ.get("LLM_TEST_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"} and os.environ.get("LLM_TEST_ALLOW_LAN") != "1":
        raise RuntimeError("Non-loopback binding requires LLM_TEST_ALLOW_LAN=1")
    return Settings(
        root_dir=root,
        var_dir=var_dir,
        database_url=os.environ.get(
            "LLM_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{var_dir / 'app.db'}"
        ),
        static_dir=root / "backend" / "app" / "static",
        host=host,
        port=int(os.environ.get("LLM_TEST_PORT", "8000")),
        app_version="0.3.0",
        scorer_version="1.0.0",
    )


settings = get_settings()
