"""Add bounded live logs and compact container-job observations.

Revision ID: 341_container_job_observations
Revises: 340_container_job_registry_auth
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "341_container_job_observations"
down_revision: Union[str, None] = "340_container_job_registry_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("container_jobs", sa.Column("workspace_observation_json", sa.JSON(), nullable=True))
    op.add_column("container_jobs", sa.Column("timing_observation_json", sa.JSON(), nullable=True))
    op.add_column("container_jobs", sa.Column("live_log_events_json", sa.JSON(), nullable=False, server_default="[]"))

def downgrade() -> None:
    op.drop_column("container_jobs", "live_log_events_json")
    op.drop_column("container_jobs", "timing_observation_json")
    op.drop_column("container_jobs", "workspace_observation_json")
