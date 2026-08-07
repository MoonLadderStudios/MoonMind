"""Add remediation link verification_outcome projection.

Issue MoonLadderStudios/MoonMind#3622: post-action verification becomes a
first-class authoritative phase. The remediation link now records the trusted
repair-verification classification separately from the action delivery
``outcome`` so a delivered action never relabels the target as repaired.

Revision ID: 352_remediation_verification
Revises: 351_claude_oauth_capacity
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "352_remediation_verification"
down_revision = "351_claude_oauth_capacity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("verification_outcome", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_remediation_links", "verification_outcome")
