"""Add Temporal runtime metadata columns to manifest registry records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers.
revision = "202603060001"
down_revision = "202603050002"


def upgrade() -> None:
    op.add_column(
        "manifest",
        sa.Column("last_run_source", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("last_run_workflow_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("last_run_temporal_run_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("last_run_manifest_ref", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("manifest", "last_run_manifest_ref")
    op.drop_column("manifest", "last_run_temporal_run_id")
    op.drop_column("manifest", "last_run_workflow_id")
    op.drop_column("manifest", "last_run_source")
