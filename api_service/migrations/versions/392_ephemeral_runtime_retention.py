"""Apply ephemeral retention to existing runtime evidence artifacts.

Runtime logs, diagnostics, skill-resolution traces and Omnigent SSE journals
are seven-day (ephemeral) artifacts, but that class is assigned when an
artifact is created or linked. Rows written before the mapping covered these
link types kept the 30-day standard class. Each Omnigent journal publish
writes the whole accumulated event prefix, and before superseded prefixes
were reclaimed every prefix was retained, so about 800K superseded journal
copies (~390 GB) held the artifact store at its free-drive threshold.

This revision gives those existing rows the class new rows already receive:
an artifact whose every link is runtime evidence becomes ephemeral and
expires seven days after creation. Rows with any other link, no link, a
non-standard class, a pin, or a deleted status are unchanged. Nothing is
deleted here; the lifecycle sweep still honours pins, live use claims and
active-journal protection before removing a blob.

Downgrade keeps the reclassification: the previous class only delayed
expiry, and rows the sweep has since removed cannot be restored.

Revision ID: 392_ephemeral_runtime_retention
Revises: 391_legacy_github_cred_4023
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "392_ephemeral_runtime_retention"
down_revision: Union[str, None] = "391_legacy_github_cred_4023"
branch_labels = None
depends_on = None

# Frozen copy of the ephemeral link types in the artifact service's
# retention derivation at this revision.
EPHEMERAL_LINK_TYPES: tuple[str, ...] = (
    "output.logs",
    "debug.trace",
    "debug.skill_resolution_trace",
    "runtime.stdout",
    "runtime.stderr",
    "runtime.merged_logs",
    "runtime.diagnostics",
    "runtime.omnigent.sse.raw",
    "runtime.omnigent.sse.normalized",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        ephemeral_expiry = "created_at + interval '7 days'"
    else:
        ephemeral_expiry = "datetime(created_at, '+7 days')"
    link_types = ", ".join(f"'{link_type}'" for link_type in EPHEMERAL_LINK_TYPES)
    op.execute(
        f"""
        UPDATE temporal_artifacts
        SET retention_class = 'ephemeral',
            expires_at = {ephemeral_expiry}
        WHERE retention_class = 'standard'
          AND status <> 'deleted'
          AND EXISTS (
              SELECT 1 FROM temporal_artifact_links AS link
              WHERE link.artifact_id = temporal_artifacts.artifact_id
                AND link.link_type IN ({link_types})
          )
          AND NOT EXISTS (
              SELECT 1 FROM temporal_artifact_links AS link
              WHERE link.artifact_id = temporal_artifacts.artifact_id
                AND link.link_type NOT IN ({link_types})
          )
        """
    )


def downgrade() -> None:
    """Keep reclassified expiries; see the module docstring."""
