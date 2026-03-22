"""Add waiting_on_dependencies to moonmindworkflowstate enum

Revision ID: a1b2c3d4e5f7
Revises: 3b27177de0fe
Create Date: 2026-03-22 12:42:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = '3b27177de0fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL ALTER TYPE ... ADD VALUE cannot run inside a transaction block
    # that has already performed other DDL. The IF NOT EXISTS guard makes this
    # idempotent for safe re-running.
    op.execute(
        "ALTER TYPE moonmindworkflowstate ADD VALUE IF NOT EXISTS 'waiting_on_dependencies'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values once added.
    # The value is inert if unused, so downgrade is a safe no-op.
    pass
