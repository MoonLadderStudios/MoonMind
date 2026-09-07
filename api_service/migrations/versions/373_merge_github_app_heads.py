"""Merge migration heads before the GitHub App delivery inbox.

Revision ID: 373_merge_github_app_heads
Revises: 372_machine_reservations, 338_remove_legacy_provider_stubs

Source issue: MoonLadderStudios/MoonMind#3967.
"""

from __future__ import annotations

from typing import Sequence, Union

revision: str = "373_merge_github_app_heads"
down_revision: Union[str, Sequence[str], None] = (
    "372_machine_reservations",
    "338_remove_legacy_provider_stubs",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
