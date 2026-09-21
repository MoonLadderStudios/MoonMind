"""Merge the two 386 migration branches (#4461).

Revision ID: 387_merge_386_heads_4461
Revises: 386_drop_machine_capacity_4459, 386_single_user_conversion_4346
Create Date: 2026-09-21

``386_drop_machine_capacity_4459`` landed on main while PR #4461 carried
``386_single_user_conversion_4346``; both branches must remain valid, so
this empty merge revision reunites them into a single head. The two
branches touch disjoint tables (machine-capacity drain vs. the
single-user conversion ledger), so there is no data merge to perform.
"""

from __future__ import annotations

from typing import Sequence, Union

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

revision: str = "387_merge_386_heads_4461"
down_revision: Union[str, None, Sequence[str]] = (
    "386_drop_machine_capacity_4459",
    "386_single_user_conversion_4346",
)


def upgrade() -> None:
    # Disjoint branches: nothing to merge beyond reuniting the heads.
    pass


def downgrade() -> None:
    # Nothing to undo: the merge itself performs no schema change.
    pass
