"""Merge the two 338 migration heads into a single linear head.

Two migrations branched from ``337_mm1207_oauth_hosts`` in parallel:
``338_container_jobs_contract`` (MoonLadderStudios/MoonMind#3252) and
``338_remove_legacy_provider_stubs``. Both are already shipped, so the graph is
reconciled with an empty merge revision rather than reparenting either branch.
This keeps a single Alembic head so ``tools/check_alembic_graph.py`` passes.

Revision ID: 339_merge_338_heads
Revises: 338_container_jobs_contract, 338_remove_legacy_provider_stubs
"""

from __future__ import annotations

from typing import Sequence, Union

revision: str = "339_merge_338_heads"
down_revision: Union[str, Sequence[str], None] = (
    "338_container_jobs_contract",
    "338_remove_legacy_provider_stubs",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: this revision only reconciles two parallel migration branches."""


def downgrade() -> None:
    """No-op: reverting the merge restores the two independent heads."""
