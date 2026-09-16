"""Atomic, revision-fenced secret rotation (#4006).

Revision ID: 384_secret_rotation_4006
Revises: 378_saved_work_retention_4017

Monotonic ``credential_revision`` / ``policy_revision`` on
``managed_secrets`` (timestamps are never generation authority), plus
``secret_mutation_receipts`` (stable request-identity idempotency) and
``secret_invalidation_outbox`` (restart-safe invalidation evidence written
in the same transaction as activation, notifications delivered after
commit). Existing rows backfill to revision 1/1.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

revision: str = "384_secret_rotation_4006"
down_revision: Union[str, None] = "378_saved_work_retention_4017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "managed_secrets",
        sa.Column(
            "credential_revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "managed_secrets",
        sa.Column(
            "policy_revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_table(
        "secret_mutation_receipts",
        sa.Column("request_id", sa.String(length=256), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_table(
        "secret_invalidation_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("credential_revision", sa.Integer(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("cause", sa.String(length=32), nullable=False),
        sa.Column(
            "delivered",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_secret_invalidation_outbox_slug",
        "secret_invalidation_outbox",
        ["slug"],
    )
    op.create_index(
        "ix_secret_invalidation_outbox_delivered",
        "secret_invalidation_outbox",
        ["delivered"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_secret_invalidation_outbox_delivered",
        table_name="secret_invalidation_outbox",
    )
    op.drop_index(
        "ix_secret_invalidation_outbox_slug",
        table_name="secret_invalidation_outbox",
    )
    op.drop_table("secret_invalidation_outbox")
    op.drop_table("secret_mutation_receipts")
    op.drop_column("managed_secrets", "policy_revision")
    op.drop_column("managed_secrets", "credential_revision")
