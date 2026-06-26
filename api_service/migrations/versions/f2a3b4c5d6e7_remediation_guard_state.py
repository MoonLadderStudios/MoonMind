"""add remediation mutation guard state

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
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

def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("mutation_guard_lock_state", _json_type(), nullable=True),
    )
    op.add_column(
        "execution_remediation_links",
        sa.Column("mutation_guard_ledger_state", _json_type(), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("execution_remediation_links", "mutation_guard_ledger_state")
    op.drop_column("execution_remediation_links", "mutation_guard_lock_state")
