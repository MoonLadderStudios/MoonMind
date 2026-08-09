"""Add the canonical remediation lifecycle operator projection.

Revision ID: 355_remediation_lifecycle_projection
Revises: 354_workflow_linked_continuation
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "355_remediation_lifecycle_projection"
down_revision = "354_workflow_linked_continuation"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("lifecycle_projection", _json_type(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_remediation_links", "lifecycle_projection")
