"""Add scheduled state and scheduled_for column

Revision ID: b3e7a91c2d4f
Revises: 594fc88de6eb
Create Date: 2026-03-18 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3e7a91c2d4f"
down_revision: Union[str, None] = "594fc88de6eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The existing enum type name used by the state column.
_ENUM_NAME = "moonmindworkflowstate"
_NEW_VALUE = "scheduled"

# Tables that carry the state column using this enum.
_TABLES = ("temporal_executions", "temporal_execution_sources")


def upgrade() -> None:
    # 1. Add the new enum value to the existing PostgreSQL enum type.
    op.execute(f"ALTER TYPE {_ENUM_NAME} ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'")

    # 2. Add the scheduled_for column to both execution tables.
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    # 1. Remove the scheduled_for column.
    for table in _TABLES:
        op.drop_column(table, "scheduled_for")

    # 2. PostgreSQL does not support removing individual enum values directly.
    #    A full enum rebuild would be needed; skip for safety.
    #    The 'scheduled' value will remain in the enum but is harmless.
