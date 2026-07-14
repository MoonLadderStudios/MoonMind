"""Merge container jobs and provider stub migration heads.

Revision ID: 339_merge_migration_heads
Revises: 338_container_jobs_contract, 338_remove_legacy_provider_stubs
"""

from __future__ import annotations

from typing import Sequence, Union

revision: str = "339_merge_migration_heads"
down_revision: Union[str, Sequence[str], None] = (
    "338_container_jobs_contract",
    "338_remove_legacy_provider_stubs",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
