"""Add awaiting to moonmindworkflowstate enum

Revision ID: e5b6c7d8e9fa
Revises: d4a5b6c7e8f9
Create Date: 2026-03-24 01:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5b6c7d8e9fa'
down_revision: Union[str, None] = 'd4a5b6c7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL ALTER TYPE ... ADD VALUE cannot run inside a transaction block
    # that has already performed other DDL. The IF NOT EXISTS guard makes this
    # idempotent for safe re-running.
    op.execute(
        "ALTER TYPE moonmindworkflowstate ADD VALUE IF NOT EXISTS 'awaiting'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values once added.
    # The value is inert if unused, so downgrade is a safe no-op.
    pass
