from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import Any

from backend.app.adapters.base import AdapterHealth, safe_subprocess_env


async def probe_cli(
    command: str,
    required_help_flags: tuple[str, ...],
    version_args: tuple[str, ...] = ("--version",),
    help_args: tuple[str, ...] = ("--help",),
) -> AdapterHealth:
    executable = shutil.which(command)
    if executable is None:
        return AdapterHealth(status="unavailable", message=f"command not found: {command}")
    try:
        version_process = await asyncio.create_subprocess_exec(
            executable,
            *version_args,
            env=safe_subprocess_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        version_bytes, _ = await asyncio.wait_for(version_process.communicate(), timeout=10)
        help_process = await asyncio.create_subprocess_exec(
            executable,
            *help_args,
            env=safe_subprocess_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        help_bytes, _ = await asyncio.wait_for(help_process.communicate(), timeout=10)
    except (OSError, TimeoutError) as exc:
        return AdapterHealth(status="error", message=str(exc))
    version = version_bytes.decode(errors="replace").strip().splitlines()[0]
    help_text = help_bytes.decode(errors="replace")
    missing = [flag for flag in required_help_flags if flag not in help_text]
    if missing:
        return AdapterHealth(
            status="incompatible",
            message=f"CLI 缺少强制隔离参数: {', '.join(missing)}",
            version=version,
            details={"executable": executable, "missing_flags": missing},
        )
    return AdapterHealth(
        status="healthy",
        message="CLI 与隔离参数可用",
        version=version,
        details={"executable": executable, "required_flags": list(required_help_flags)},
    )


def file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def isolation_snapshot(
    *, command: str, version: str | None, flags: list[str], policy_hash: str
) -> dict[str, Any]:
    return {
        "command": command,
        "version": version,
        "flags": flags,
        "policy_hash": policy_hash,
    }


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
