"""add priority_override_reason to task_proposals

Revision ID: 202602200001
Revises: 202602190004
Create Date: 2026-02-20 00:01:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "202602200001"
down_revision: Union[str, None] = "202602190004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_proposals",
        sa.Column("priority_override_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("task_proposals", "priority_override_reason")
