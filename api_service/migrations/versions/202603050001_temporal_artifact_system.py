"""Add Temporal artifact index tables for local-dev artifact system."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202603050001"
down_revision: Union[str, None] = "202603010001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    """Create Temporal artifact metadata, execution links, and pin tables."""

    op.create_table(
        "temporal_artifacts",
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by_principal", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=128), nullable=True),
        sa.Column(
            "storage_backend",
            sa.Enum(
                "s3",
                "local_fs",
                name="temporalartifactstoragebackend",
                native_enum=True,
                validate_strings=True,
            ),
            nullable=False,
            server_default="s3",
        ),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column(
            "encryption",
            sa.Enum(
                "sse-kms",
                "sse-s3",
                "none",
                "envelope",
                name="temporalartifactencryption",
                native_enum=True,
                validate_strings=True,
            ),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_upload",
                "complete",
                "failed",
                "deleted",
                name="temporalartifactstatus",
                native_enum=True,
                validate_strings=True,
            ),
            nullable=False,
            server_default="pending_upload",
        ),
        sa.Column(
            "retention_class",
            sa.Enum(
                "ephemeral",
                "standard",
                "long",
                "pinned",
                name="temporalartifactretentionclass",
                native_enum=True,
                validate_strings=True,
            ),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "redaction_level",
            sa.Enum(
                "none",
                "preview_only",
                "restricted",
                name="temporalartifactredactionlevel",
                native_enum=True,
                validate_strings=True,
            ),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "upload_mode",
            sa.Enum(
                "single_put",
                "multipart",
                name="temporalartifactuploadmode",
                native_enum=True,
                validate_strings=True,
            ),
            nullable=False,
            server_default="single_put",
        ),
        sa.Column("upload_id", sa.String(length=255), nullable=True),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hard_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_lifecycle_run_id", sa.String(length=255), nullable=True),
        sa.Column("metadata", _json_variant(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint("storage_key", name="uq_temporal_artifacts_storage_key"),
    )
    op.create_index(
        "ix_temporal_artifacts_created_at",
        "temporal_artifacts",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_artifacts_status",
        "temporal_artifacts",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_artifacts_expires_at",
        "temporal_artifacts",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_artifacts_deleted_at",
        "temporal_artifacts",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_artifacts_hard_deleted_at",
        "temporal_artifacts",
        ["hard_deleted_at"],
        unique=False,
    )

    op.create_table(
        "temporal_artifact_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.Column("link_type", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by_activity_type", sa.String(length=255), nullable=True),
        sa.Column("created_by_worker", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["temporal_artifacts.artifact_id"],
            name="fk_temporal_artifact_links_artifact_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_temporal_artifact_links_execution",
        "temporal_artifact_links",
        ["namespace", "workflow_id", "run_id", "link_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_artifact_links_artifact_id",
        "temporal_artifact_links",
        ["artifact_id"],
        unique=False,
    )

    op.create_table(
        "temporal_artifact_pins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("pinned_by_principal", sa.String(length=255), nullable=False),
        sa.Column(
            "pinned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["temporal_artifacts.artifact_id"],
            name="fk_temporal_artifact_pins_artifact_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", name="uq_temporal_artifact_pins_artifact_id"),
    )


def downgrade() -> None:
    """Drop Temporal artifact index tables and enum types."""

    op.drop_table("temporal_artifact_pins")
    op.drop_index(
        "ix_temporal_artifact_links_artifact_id",
        table_name="temporal_artifact_links",
    )
    op.drop_index(
        "ix_temporal_artifact_links_execution",
        table_name="temporal_artifact_links",
    )
    op.drop_table("temporal_artifact_links")
    op.drop_index(
        "ix_temporal_artifacts_hard_deleted_at",
        table_name="temporal_artifacts",
    )
    op.drop_index(
        "ix_temporal_artifacts_deleted_at",
        table_name="temporal_artifacts",
    )
    op.drop_index(
        "ix_temporal_artifacts_expires_at",
        table_name="temporal_artifacts",
    )
    op.drop_index(
        "ix_temporal_artifacts_status",
        table_name="temporal_artifacts",
    )
    op.drop_index(
        "ix_temporal_artifacts_created_at",
        table_name="temporal_artifacts",
    )
    op.drop_table("temporal_artifacts")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS temporalartifactredactionlevel")
        op.execute("DROP TYPE IF EXISTS temporalartifactuploadmode")
        op.execute("DROP TYPE IF EXISTS temporalartifactretentionclass")
        op.execute("DROP TYPE IF EXISTS temporalartifactstatus")
        op.execute("DROP TYPE IF EXISTS temporalartifactencryption")
        op.execute("DROP TYPE IF EXISTS temporalartifactstoragebackend")
