"""add immutable evidence snapshots

Revision ID: c4e8b9f7a102
Revises: 7584fd247605
Create Date: 2026-08-29 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8b9f7a102"
down_revision: str | None = "7584fd247605"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {
        table: {column["name"] for column in sa.inspect(connection).get_columns(table)}
        for table in ("comparison_runs", "model_runs", "case_runs")
    }

    def add(table: str, column: sa.Column[object]) -> None:
        if column.name not in columns[table]:
            op.add_column(table, column)

    add(
        "comparison_runs",
        sa.Column(
            "app_version_snapshot",
            sa.String(length=32),
            nullable=False,
            server_default="0.1.0",
        ),
    )
    add(
        "comparison_runs",
        sa.Column(
            "scorer_version_snapshot",
            sa.String(length=32),
            nullable=False,
            server_default="1.0.0",
        ),
    )
    add(
        "comparison_runs",
        sa.Column(
            "duckdb_version_snapshot",
            sa.String(length=32),
            nullable=False,
            server_default="1.5.5",
        ),
    )
    add(
        "comparison_runs",
        sa.Column(
            "sqlglot_version_snapshot",
            sa.String(length=32),
            nullable=False,
            server_default="30.17.0",
        ),
    )
    add(
        "comparison_runs",
        sa.Column(
            "output_contract_snapshot",
            sa.String(length=40),
            nullable=False,
            server_default="query-plan-v1",
        ),
    )
    add(
        "model_runs",
        sa.Column("profile_name_snapshot", sa.String(length=120), nullable=True),
    )
    op.execute(
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
    add("case_runs", sa.Column("generation_ms", sa.Float(), nullable=True))
    add(
        "case_runs",
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("case_runs", "provider_request_id")
    op.drop_column("case_runs", "generation_ms")
    op.drop_column("model_runs", "profile_name_snapshot")
    op.drop_column("comparison_runs", "output_contract_snapshot")
    op.drop_column("comparison_runs", "sqlglot_version_snapshot")
    op.drop_column("comparison_runs", "duckdb_version_snapshot")
    op.drop_column("comparison_runs", "scorer_version_snapshot")
    op.drop_column("comparison_runs", "app_version_snapshot")
