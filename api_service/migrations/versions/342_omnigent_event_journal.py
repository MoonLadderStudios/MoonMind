"""Persist active Omnigent event deduplication state for #3362.

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
    op.add_column(
        "omnigent_bridge_session_events",
        sa.Column("source_cursor", sa.String(255), nullable=True),
    )
    op.execute(
        "UPDATE omnigent_bridge_session_events "
        "SET deduplication_key = event_id WHERE deduplication_key IS NULL"
    )
    op.alter_column(
        "omnigent_bridge_session_events", "deduplication_key", nullable=False
    )
    op.create_unique_constraint(
        "uq_omnigent_bridge_session_events_sequence",
        "omnigent_bridge_session_events",
        ["bridge_session_id", "sequence"],
    )
    op.create_unique_constraint(
        "uq_omnigent_bridge_session_events_deduplication_key",
        "omnigent_bridge_session_events",
        ["bridge_session_id", "deduplication_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_omnigent_bridge_session_events_deduplication_key",
        "omnigent_bridge_session_events",
        type_="unique",
    )
    op.drop_constraint(
        "uq_omnigent_bridge_session_events_sequence",
        "omnigent_bridge_session_events",
        type_="unique",
    )
    op.drop_column("omnigent_bridge_session_events", "source_cursor")
    op.drop_column("omnigent_bridge_session_events", "deduplication_key")
