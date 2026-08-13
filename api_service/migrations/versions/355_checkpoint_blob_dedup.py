"""Allow logical checkpoint artifacts to share content-addressed blobs.

Revision ID: 355_checkpoint_blob_dedup
Revises: 354_workflow_linked_continuation
"""

from __future__ import annotations

from alembic import op


revision = "355_checkpoint_blob_dedup"
down_revision = "354_workflow_linked_continuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "temporal_artifacts_storage_key_key",
        "temporal_artifacts",
        type_="unique",
    )
    op.create_index(
        "ix_temporal_artifacts_storage_key",
        "temporal_artifacts",
        ["storage_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_temporal_artifacts_storage_key",
        table_name="temporal_artifacts",
    )
    op.create_unique_constraint(
        "temporal_artifacts_storage_key_key",
        "temporal_artifacts",
        ["storage_key"],
    )
