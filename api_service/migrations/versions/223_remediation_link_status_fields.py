"""add remediation link status fields

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-04-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("active_lock_scope", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "execution_remediation_links",
        sa.Column("active_lock_holder", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "execution_remediation_links",
        sa.Column("latest_action_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "execution_remediation_links",
        sa.Column("outcome", sa.String(length=64), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("execution_remediation_links", "outcome")
    op.drop_column("execution_remediation_links", "latest_action_summary")
    op.drop_column("execution_remediation_links", "active_lock_holder")
    op.drop_column("execution_remediation_links", "active_lock_scope")
