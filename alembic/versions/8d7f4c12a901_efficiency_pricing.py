"""add efficiency pricing snapshots

Revision ID: 8d7f4c12a901
Revises: c4e8b9f7a102
Create Date: 2026-08-30 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d7f4c12a901"
down_revision: str | None = "c4e8b9f7a102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {
        table: {column["name"] for column in sa.inspect(connection).get_columns(table)}
        for table in ("model_profiles", "model_runs")
    }
    if "pricing_json" not in columns["model_profiles"]:
        op.add_column("model_profiles", sa.Column("pricing_json", sa.JSON(), nullable=True))
    if "pricing_snapshot_json" not in columns["model_runs"]:
        op.add_column("model_runs", sa.Column("pricing_snapshot_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_runs", "pricing_snapshot_json")
    op.drop_column("model_profiles", "pricing_json")
