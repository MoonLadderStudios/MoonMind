"""Extend manifest registry columns."""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602190003"
down_revision: Union[str, None] = "202602190002"


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "manifest",
        sa.Column("version", sa.String(length=32), nullable=False, server_default="v0"),
    )
    op.add_column(
        "manifest",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "manifest",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "manifest",
        sa.Column("last_run_job_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("last_run_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("last_run_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("last_run_finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("state_json", _json_variant(), nullable=True),
    )
    op.add_column(
        "manifest",
        sa.Column("state_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("manifest", "state_updated_at")
    op.drop_column("manifest", "state_json")
    op.drop_column("manifest", "last_run_finished_at")
    op.drop_column("manifest", "last_run_started_at")
    op.drop_column("manifest", "last_run_status")
    op.drop_column("manifest", "last_run_job_id")
    op.drop_column("manifest", "updated_at")
    op.drop_column("manifest", "created_at")
    op.drop_column("manifest", "version")
