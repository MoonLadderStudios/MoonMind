"""add durable remediation approval and operator state

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-03 00:00:00.000000

MoonLadderStudios/MoonMind#3512
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("approval_state", _json_type(), nullable=True),
    )
    op.add_column(
        "execution_remediation_links",
        sa.Column("operator_state", _json_type(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_remediation_links", "operator_state")
    op.drop_column("execution_remediation_links", "approval_state")
