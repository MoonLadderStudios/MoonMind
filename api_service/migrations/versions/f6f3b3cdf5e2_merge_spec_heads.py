"""Merge Alembic heads for Spec workflow and automation migrations."""

from __future__ import annotations

from typing import Sequence, Union

revision: str = "f6f3b3cdf5e2"
down_revision: Union[str, Sequence[str], None] = ("d2ad8d5f6b6d", "b3c0bb1d69d7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
