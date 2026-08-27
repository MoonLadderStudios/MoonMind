"""Record the generic GPU resource observation for MoonLadderStudios/MoonMind#3779.

Adds the compact, non-sensitive GPU resource observation to the API-owned
container-job record. It stays NULL for every CPU-only job, so existing records
and in-flight jobs are unaffected.

Revision ID: 364_container_job_gpu_obs
Revises: 363_merge_automation_reviews
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "364_container_job_gpu_obs"
down_revision: Union[str, None] = "363_merge_automation_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "container_jobs",
        sa.Column("gpu_observation_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("container_jobs", "gpu_observation_json")
