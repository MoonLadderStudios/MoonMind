"""Preserve acquired repository issuance across runtime binding transitions.

Source: MoonLadderStudios/MoonMind#4409 review (P1: preserve repository
issuance through runtime transitions).

``OmnigentRuntimeBinding.repositoryIssuance`` previously lived only in the
in-memory binding object: every ``update_with_host`` / ``update_with_session``
/ lease-reconciliation transition recreated the binding without the field,
and the database record had no column for it. Once an issuance-bearing
binding advanced past acquisition, the issuance records disappeared from the
new digest and could no longer be refreshed or cleaned up.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "385_runtime_issuance"
down_revision: Union[str, None] = "384_secret_rotation_4006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "omnigent_runtime_bindings",
        sa.Column(
            "repository_issuance_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("omnigent_runtime_bindings", "repository_issuance_json")
