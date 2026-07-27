"""Persist replay-safe remediation approval requests and decisions.

Revision ID: 348_remediation_approval_state
Revises: 347_control_stop_continuations
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "348_remediation_approval_state"
down_revision = "347_control_stop_continuations"
branch_labels = None
depends_on = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), "postgresql"
    )


def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("approval_state", _json_type(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_remediation_links", "approval_state")
