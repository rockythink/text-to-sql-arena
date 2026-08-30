#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

uv sync --frozen
uv run ruff check .
uv run mypy backend tests
uv run pytest -q

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/llm-test-migration.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT
LLM_TEST_VAR_DIR="$tmp_dir" \
LLM_TEST_DATABASE_URL="sqlite+aiosqlite:///$tmp_dir/app.db" \
  uv run alembic upgrade head

uv run python -m backend.app.cli verify-evidence --input evidence

cd frontend
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm build
