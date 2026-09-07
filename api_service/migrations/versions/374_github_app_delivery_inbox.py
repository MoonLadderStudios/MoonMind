"""Durable inbox for inbound GitHub App webhook deliveries.

Revision ID: 374_github_app_delivery_inbox
Revises: 373_merge_github_app_heads
Create Date: 2026-09-07

Source issue: MoonLadderStudios/MoonMind#3967. The receipt endpoint persists
one row per scoped delivery (installation + GitHub delivery GUID) before
acknowledging GitHub, so a crash after acknowledgment never loses the
delivery. Redeliveries of identical bytes dedupe on the primary key; a
duplicate with changed content is held as a conflict for operator review.
Only digests, identities, reason codes, and safe summaries are stored.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "374_github_app_delivery_inbox"
down_revision: Union[str, None] = "373_merge_github_app_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_app_deliveries",
        sa.Column("scoped_delivery_id", sa.String(length=255), primary_key=True),
        sa.Column("delivery_guid", sa.String(length=128), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column(
            "action",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "repository",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "actor_login",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("safe_summary", sa.String(length=1000), nullable=True),
        sa.Column("preset_slug", sa.String(length=255), nullable=True),
        sa.Column("dispatch_key", sa.String(length=255), nullable=True),
        sa.Column("workflow_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("payload_excerpt", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_github_app_deliveries_state",
        "github_app_deliveries",
        ["state"],
    )
    op.create_index(
        "ix_github_app_deliveries_repository",
        "github_app_deliveries",
        ["repository"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_app_deliveries_repository", table_name="github_app_deliveries"
    )
    op.drop_index("ix_github_app_deliveries_state", table_name="github_app_deliveries")
    op.drop_table("github_app_deliveries")
