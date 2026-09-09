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

revision: str = "376_merge_375_heads"
down_revision: Union[str, Sequence[str], None] = (
    "375_artifact_principal_text",
    "375_session_authority_4121",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
