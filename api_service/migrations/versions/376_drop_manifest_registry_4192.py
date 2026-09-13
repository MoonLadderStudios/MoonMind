"""Drop the retired native Manifest registry table.

Revision ID: 376_drop_manifest_registry_4192
Revises: 376_merge_375_heads
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

Execution-time drain enforcement (MoonLadderStudios/MoonMind#4191 MR4):
``upgrade()`` consults ``moonmind.gates.manifest_registry_migration_4191``
(``require_registry_drop_approval``) before dropping. An empty ``manifest``
table (fresh install) proceeds without approval; a populated or unreadable
table requires ``MOONMIND_MANIFEST_REGISTRY_DRAIN_APPROVED=1`` after the
protected export verifies and the drain gate reports ``may_apply_destructive``.
The gate module is stdlib-only: this migration never imports removed Manifest
runtime modules.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Alembic discovers migration identity from these module attributes (it
# reads ``branch_labels``/``depends_on`` via getattr with None defaults, so
# only the non-null chain links are declared here); ``__all__`` keeps that
# external contract explicit for static analysis.
__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

revision: str = "376_drop_manifest_registry_4192"
down_revision: Union[str, None] = "376_merge_375_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _manifest_row_count_or_none() -> int | None:
    """Count live ``manifest`` rows, or None when the table is unreadable.

    A missing/unreadable registry dimension is not a clean drain: the
    caller fails closed and requires explicit operator approval.
    """
    from sqlalchemy import text

    try:
        bind = op.get_bind()
        return int(bind.execute(text("SELECT COUNT(*) FROM manifest")).scalar() or 0)
    except Exception:
        return None


def upgrade() -> None:
    from moonmind.gates.manifest_registry_migration_4191 import (
        is_drain_approved,
        require_registry_drop_approval,
    )

    require_registry_drop_approval(
        manifest_row_count=_manifest_row_count_or_none(),
        approved=is_drain_approved(),
    )
    op.drop_index(op.f("ix_manifest_id"), table_name="manifest")
    op.drop_table("manifest")


def downgrade() -> None:
    raise RuntimeError(
        "376_drop_manifest_registry_4192 is irreversible: the native Manifest "
        "registry was retired by MoonLadderStudios/MoonMind#4192. Restore "
        "retained rows from a pre-upgrade export instead of recreating an "
        "empty table."
    )
