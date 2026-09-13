"""Audit post-drop Manifest registry state (forward cutover for #4191).

Revision ID: 380_manifest_drain_audit_4191
Revises: 379_durable_issue_claims
Create Date: 2026-09-13

MoonLadderStudios/MoonMind#4191 (MR4): revision
``376_drop_manifest_registry_4192`` first landed via #4192 without the MR4
execution gate, so databases that already applied the original unconditional
drop cannot re-invoke that guard through Alembic. This forward audit covers
those installations: when the ``manifest`` table is already gone it records
a successful audit (recovery for those databases is the pre-upgrade
protected export plus forward repair, never schema downgrade); when the
table still survives it enforces the same bound drain approval as revision
376 before allowing the chain to proceed.

This revision never recreates the registry and never imports removed
Manifest runtime modules.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

revision: str = "380_manifest_drain_audit_4191"
down_revision: Union[str, None] = "379_durable_issue_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _manifest_table_exists() -> bool:
    from sqlalchemy import inspect as sa_inspect

    try:
        bind = op.get_bind()
        return sa_inspect(bind).has_table("manifest")
    except Exception:
        # Unobservable schema: fail closed via the approval path below.
        return True


def _manifest_row_count_or_none() -> int | None:
    from sqlalchemy import text

    try:
        bind = op.get_bind()
        return int(bind.execute(text("SELECT COUNT(*) FROM manifest")).scalar() or 0)
    except Exception:
        return None


def upgrade() -> None:
    import os

    if not _manifest_table_exists():
        # Already dropped (including databases that applied the original
        # unconditional 376): audit passes; recovery uses the protected
        # export, never downgrade.
        return
    from moonmind.gates.manifest_registry_migration_4191 import (
        is_drain_approved,
        read_drain_export_row_count,
        require_registry_drop_approval,
    )

    environ = dict(os.environ)
    require_registry_drop_approval(
        manifest_row_count=_manifest_row_count_or_none(),
        approved=is_drain_approved(environ),
        export_row_count=read_drain_export_row_count(environ),
        export_verified=read_drain_export_row_count(environ) is not None,
        environ=environ,
    )


def downgrade() -> None:
    # Audit-only revision: nothing to undo.
    return
