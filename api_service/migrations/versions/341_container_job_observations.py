"""Record compact container-job observations for MoonLadderStudios/MoonMind#3258.

Adds the durable observability-event journal reference and the compact,
non-sensitive execution observations (workspace visibility probe result and
container start/end/duration timing) to the API-owned container-job record.

Revision ID: 341_container_job_observations
Revises: 340_container_job_registry_auth
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "341_container_job_observations"
down_revision: Union[str, None] = "340_container_job_registry_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "container_jobs",
        sa.Column("events_ref", sa.String(1024), nullable=True),
    )
    op.add_column(
        "container_jobs",
        sa.Column("workspace_probe", sa.String(32), nullable=True),
    )
    op.add_column(
        "container_jobs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "container_jobs",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "container_jobs",
        sa.Column("duration_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("container_jobs", "duration_seconds")
    op.drop_column("container_jobs", "completed_at")
    op.drop_column("container_jobs", "started_at")
    op.drop_column("container_jobs", "workspace_probe")
    op.drop_column("container_jobs", "events_ref")
