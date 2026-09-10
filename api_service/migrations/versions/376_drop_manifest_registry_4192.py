"""Drop the retired native Manifest registry table.

Revision ID: 376_drop_manifest_registry_4192
Revises: 375_artifact_principal_text
Create Date: 2026-09-10

MoonLadderStudios/MoonMind#4192 (MR5): the native Manifest product (registry
API, ManifestIngest Temporal workflow, vector-free pipeline, and RAG
retrieval backend) is retired. The ``manifest`` registry table has no live
writer or reader left; the API router, sync service, and registry service
were removed in the same change.

This migration is intentionally irreversible. Operators must inventory and
export retained registry rows before upgrading; recreating an empty table on
downgrade would imply that the deleted registry and run evidence was
restored. Old-release replay/drain evidence lives in exported artifacts and
the Temporal visibility/memo record fields, never in this table.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "376_drop_manifest_registry_4192"
down_revision: Union[str, None] = "375_artifact_principal_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_manifest_id"), table_name="manifest")
    op.drop_table("manifest")


def downgrade() -> None:
    raise RuntimeError(
        "376_drop_manifest_registry_4192 is irreversible: the native Manifest "
        "registry was retired by MoonLadderStudios/MoonMind#4192. Restore "
        "retained rows from a pre-upgrade export instead of recreating an "
        "empty table."
    )
