"""Add Temporal runtime metadata columns to manifest registry records."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202603060001"
down_revision: Union[str, None] = "202603050002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
