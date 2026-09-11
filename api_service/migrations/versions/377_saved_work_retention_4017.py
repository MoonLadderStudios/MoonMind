"""Add saved-work use-claim and deletion-intent retention tables.

Revision ID: 377_saved_work_retention_4017
Revises: 376_drop_manifest_registry_4192
Create Date: 2026-09-11

MoonLadderStudios/MoonMind#4017: operation-scoped multi-owner retention
(``temporal_artifact_use_claims``) and recoverable DB/object-store deletion
intents (``temporal_artifact_deletion_intents``). Both extend the existing
Temporal artifact lifecycle owner; no second store or parallel GC authority.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# Alembic discovers migration identity from these module attributes (it
# reads ``branch_labels``/``depends_on`` via getattr with None defaults, so
# only the non-null chain links are declared here); ``__all__`` keeps that
# external contract explicit for static analysis.
__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

revision: str = "377_saved_work_retention_4017"
down_revision: Union[str, None] = "376_drop_manifest_registry_4192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "temporal_artifact_use_claims",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("owner_principal", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(255), nullable=False),
        sa.Column("operation_kind", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["temporal_artifacts.artifact_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id",
            "owner_principal",
            "request_id",
            name="uq_temporal_artifact_use_claims_owner_request",
        ),
    )
    op.create_index(
        "ix_temporal_artifact_use_claims_artifact_id",
        "temporal_artifact_use_claims",
        ["artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_artifact_use_claims_expires_at",
        "temporal_artifact_use_claims",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "temporal_artifact_deletion_intents",
        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("initiated_by_principal", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["temporal_artifacts.artifact_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index(
        "ix_temporal_artifact_deletion_intents_created_at",
        "temporal_artifact_deletion_intents",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_temporal_artifact_deletion_intents_created_at",
        table_name="temporal_artifact_deletion_intents",
    )
    op.drop_table("temporal_artifact_deletion_intents")
    op.drop_index(
        "ix_temporal_artifact_use_claims_expires_at",
        table_name="temporal_artifact_use_claims",
    )
    op.drop_index(
        "ix_temporal_artifact_use_claims_artifact_id",
        table_name="temporal_artifact_use_claims",
    )
    op.drop_table("temporal_artifact_use_claims")
