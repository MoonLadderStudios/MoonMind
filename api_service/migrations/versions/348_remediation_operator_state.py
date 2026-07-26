"""add durable remediation approval and operator state

Revision ID: 348_remediation_operator_state
Revises: 347_control_stop_continuations
Create Date: 2026-07-26 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "348_remediation_operator_state"
down_revision: Union[str, None] = "347_control_stop_continuations"
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
