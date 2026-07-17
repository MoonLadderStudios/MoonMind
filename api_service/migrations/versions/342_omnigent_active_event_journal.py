"""Make Omnigent active events retry-safe for MoonLadderStudios/MoonMind#3362.

Revision ID: 342_omnigent_event_journal
Revises: 341_container_job_observations
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "342_omnigent_event_journal"
down_revision: Union[str, None] = "341_container_job_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "omnigent_bridge_session_events",
        sa.Column("deduplication_key", sa.String(128), nullable=True),
    )
    op.execute(
        "UPDATE omnigent_bridge_session_events "
        "SET deduplication_key = SUBSTR('legacy:' || event_id, 1, 128)"
    )
    op.alter_column(
        "omnigent_bridge_session_events", "deduplication_key", nullable=False
    )
    op.drop_index(
        "ix_omnigent_bridge_session_events_sequence",
        table_name="omnigent_bridge_session_events",
    )
    op.create_index(
        "ix_omnigent_bridge_session_events_sequence",
        "omnigent_bridge_session_events",
        ["bridge_session_id", "sequence"],
        unique=True,
    )
    op.create_index(
        "ix_omnigent_bridge_session_events_dedup",
        "omnigent_bridge_session_events",
        ["bridge_session_id", "deduplication_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_omnigent_bridge_session_events_dedup",
        table_name="omnigent_bridge_session_events",
    )
    op.drop_index(
        "ix_omnigent_bridge_session_events_sequence",
        table_name="omnigent_bridge_session_events",
    )
    op.create_index(
        "ix_omnigent_bridge_session_events_sequence",
        "omnigent_bridge_session_events",
        ["bridge_session_id", "sequence"],
    )
    op.drop_column("omnigent_bridge_session_events", "deduplication_key")
