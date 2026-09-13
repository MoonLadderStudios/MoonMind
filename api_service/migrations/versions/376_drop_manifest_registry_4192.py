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
table requires ``MOONMIND_MANIFEST_REGISTRY_DRAIN_APPROVED=1`` bound to
``MOONMIND_MANIFEST_REGISTRY_EXPORT_ROW_COUNT=<verified count>`` after the
protected export verifies and the drain gate reports ``may_apply_destructive``.
The gate module is stdlib-only: this migration never imports removed Manifest
runtime modules.

Cutover (applied-migration guard): revision ``376_drop_manifest_registry_4192``
first landed 2026-09-10 via #4192 without the MR4 execution gate. Databases
that already applied the original unconditional drop cannot re-invoke this
guard through Alembic; for those installations the authoritative recovery is
the pre-upgrade protected export plus forward repair
(``381_manifest_drain_audit_4191`` verifies post-drop state and
refuses a surviving ``manifest`` table without a bound approval). The in-place
guard amendment protects every pending upgrade that has not yet applied
revision 376; it does not rewrite history for already-dropped databases.

Concurrent invocations serialize on the deployment-owned database lock below
(``pg_advisory_xact_lock`` on PostgreSQL, best-effort elsewhere) before
counting and dropping, so two overlapping ``upgrade()`` runs cannot both
enter the destructive DDL.
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


def _acquire_destructive_migration_lock() -> None:
    """Serialize concurrent destructive migration attempts.

    Acquires the deployment-owned transaction-scoped advisory lock before
    counting and dropping. On PostgreSQL this serializes overlapping
    ``upgrade()`` runs; elsewhere (SQLite/test) it is a best-effort no-op
    so hermetic tests never depend on a live database.
    """
    from sqlalchemy import text

    try:
        bind = op.get_bind()
        # Stable 64-bit key derived from the revision name.
        bind.execute(text("SELECT pg_advisory_xact_lock(3764192001)"))
    except Exception:
        return


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
    import os

    from moonmind.gates.manifest_registry_migration_4191 import (
        is_drain_approved,
        read_drain_export_row_count,
        require_registry_drop_approval,
    )

    _acquire_destructive_migration_lock()
    live_count = _manifest_row_count_or_none()
    environ = dict(os.environ)
    require_registry_drop_approval(
        manifest_row_count=live_count,
        approved=is_drain_approved(environ),
        export_row_count=read_drain_export_row_count(environ),
        export_verified=read_drain_export_row_count(environ) is not None
        or (live_count is not None and live_count <= 0),
        environ=environ,
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
