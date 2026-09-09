"""Merge the two 375 migration heads.

``375_session_authority_4121`` (#4171) and ``375_artifact_principal_text``
(#4173) each branched from ``374_identity_mapping_k3`` and landed on main
independently, leaving the Alembic graph with two heads. Both revisions are
shipped, so neither can be reparented; this empty merge revision restores the
single deterministic upgrade order the migration gate requires.

Revision ID: 376_merge_375_heads
Revises: 375_artifact_principal_text, 375_session_authority_4121
"""

from __future__ import annotations

from typing import Sequence, Union

# Alembic discovers migration identity from these module attributes (it
# reads ``branch_labels``/``depends_on`` via getattr with None defaults, so
# only the non-null chain links are declared here); ``__all__`` keeps that
# external contract explicit for static analysis.
__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

revision: str = "376_merge_375_heads"
down_revision: Union[str, Sequence[str], None] = (
    "375_artifact_principal_text",
    "375_session_authority_4121",
)


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
