"""Add created_at column and make started_at nullable

Revision ID: d4a5b6c7e8f9
Revises: f9d1b627d0eb
Create Date: 2026-03-23 20:56:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4a5b6c7e8f9"
down_revision: Union[str, None] = "f9d1b627d0eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that carry started_at and need the new created_at column.
_TABLES = ("temporal_executions", "temporal_execution_sources")


def upgrade() -> None:
    for table in _TABLES:
        # 1. Add created_at with server default so existing rows get a value.
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

        # 2. Backfill created_at from existing started_at for all rows.
        op.execute(
            f"UPDATE {table} SET created_at = started_at WHERE started_at IS NOT NULL"
        )

        # 3. Make started_at nullable.
        op.alter_column(
            table,
            "started_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )


def downgrade() -> None:
    for table in _TABLES:
        # 1. Backfill started_at from created_at where it is NULL.
        op.execute(
            f"UPDATE {table} SET started_at = created_at WHERE started_at IS NULL"
        )

        # 2. Restore started_at as non-nullable.
        op.alter_column(
            table,
            "started_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

        # 3. Drop created_at.
        op.drop_column(table, "created_at")
