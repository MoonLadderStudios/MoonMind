"""Add task proposal delivery record fields.

Revision ID: 311_proposal_delivery_records
Revises: 268_settings_overrides
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "311_proposal_delivery_records"
down_revision = "268_settings_overrides"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "task_proposals",
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="github"),
    )
    op.add_column("task_proposals", sa.Column("external_key", sa.String(length=255), nullable=True))
    op.add_column("task_proposals", sa.Column("external_url", sa.Text(), nullable=True))
    op.add_column(
        "task_proposals",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_proposals",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("task_proposals", sa.Column("task_snapshot_ref", sa.Text(), nullable=True))
    op.add_column(
        "task_proposals",
        sa.Column(
            "provider_metadata",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "task_proposals",
        sa.Column(
            "resolved_policy",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index(
        "ix_task_proposals_provider_destination_dedup",
        "task_proposals",
        ["provider", "repository", "dedup_hash", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_proposals_provider_destination_dedup",
        table_name="task_proposals",
    )
    op.drop_column("task_proposals", "resolved_policy")
    op.drop_column("task_proposals", "provider_metadata")
    op.drop_column("task_proposals", "task_snapshot_ref")
    op.drop_column("task_proposals", "last_synced_at")
    op.drop_column("task_proposals", "delivered_at")
    op.drop_column("task_proposals", "external_url")
    op.drop_column("task_proposals", "external_key")
    op.drop_column("task_proposals", "provider")
