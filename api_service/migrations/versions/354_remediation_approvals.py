"""Persist exact remediation reviewer approval authority.

MoonLadderStudios/MoonMind#3620.

Revision ID: 354_remediation_approvals
Revises: 353_omnigent_chat_binding
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "354_remediation_approvals"
down_revision = "353_omnigent_chat_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("approval_state", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_remediation_links", "approval_state")
