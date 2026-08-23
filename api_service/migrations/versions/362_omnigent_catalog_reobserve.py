"""Allow repeated authenticated observations of identical Omnigent inventory.

A harness catalog observation is one immutable snapshot whose ``catalog_ref``
digest includes its ``observed_at`` timestamp, while launch readiness and
planning require the latest observation to be fresh. One row per
``(endpoint_ref, source_digest)`` therefore made every re-synchronization of
unchanged inventory fail on ``uq_omnigent_catalog_endpoint_source``, so the
catalog aged out after its freshness window no matter how often it was synced.

Source: MoonLadderStudios/MoonMind#3451
Revision ID: 362_omnigent_catalog_reobserve
Revises: 361_omnigent_execution_authority
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "362_omnigent_catalog_reobserve"
down_revision: Union[str, None] = "361_omnigent_execution_authority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_omnigent_catalog_endpoint_source",
        "omnigent_harness_catalog_snapshots",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_omnigent_catalog_endpoint_source",
        "omnigent_harness_catalog_snapshots",
        ["endpoint_ref", "source_digest"],
    )
