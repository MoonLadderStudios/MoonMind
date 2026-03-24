"""Align moonmindworkflowstate enum values with Python model

Revision ID: f2a3b4c5d6e7
Revises: e5b6c7d8e9fa
Create Date: 2026-03-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e5b6c7d8e9fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE moonmindworkflowstate RENAME VALUE 'succeeded' TO 'completed'")
    op.execute("ALTER TYPE moonmindworkflowstate RENAME VALUE 'awaiting' TO 'awaiting_slot'")


def downgrade() -> None:
    op.execute("ALTER TYPE moonmindworkflowstate RENAME VALUE 'completed' TO 'succeeded'")
    op.execute("ALTER TYPE moonmindworkflowstate RENAME VALUE 'awaiting_slot' TO 'awaiting'")
