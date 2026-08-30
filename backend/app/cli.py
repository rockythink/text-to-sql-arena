from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from backend.app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-first Text-to-SQL benchmark")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("serve", help="启动本地 Web 应用")
    export_parser = subcommands.add_parser("export-evidence", help="导出全部公开证据")
    export_parser.add_argument(
        "--output", type=Path, default=settings.root_dir / "evidence"
    )
    verify_parser = subcommands.add_parser("verify-evidence", help="校验证据文件哈希")
    verify_parser.add_argument(
        "--input", type=Path, default=settings.root_dir / "evidence"
    )
    args = parser.parse_args()

    if args.command == "export-evidence":
        from backend.app.services.evidence import export_all_evidence

        result = asyncio.run(export_all_evidence(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    if args.command == "verify-evidence":
        from backend.app.services.evidence import verify_evidence

        result = verify_evidence(args.input)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    uvicorn.run("backend.app.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
