"""Durable receipt for opt-in GitHub event deliveries (#3967).

Revision ID: 388_github_event_delivery_receipts_3967
Revises: 387_merge_386_heads_4461
Create Date: 2026-09-22 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "388_github_event_delivery_receipts_3967"
down_revision = "387_merge_386_heads_4461"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "github_event_delivery_receipts",
        sa.Column("delivery_key", sa.String(255), primary_key=True),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False, server_default=""),
        sa.Column("payload_digest", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("preset_slug", sa.String(255)),
        sa.Column("execution_ref", sa.String(255)),
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
        "ix_github_event_delivery_receipts_repository",
        "github_event_delivery_receipts",
        ["repository"],
    )


def downgrade():
    table = sa.table(
        "github_event_delivery_receipts", sa.column("delivery_key")
    )
    if (
        op.get_bind()
        .execute(sa.select(sa.exists().select_from(table)))
        .scalar()
    ):
        raise RuntimeError(
            "Retained GitHub event delivery receipts must drain before "
            "removing their deduplication owner"
        )
    op.drop_table("github_event_delivery_receipts")
