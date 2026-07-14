"""Record non-sensitive private-image authorization for MoonLadderStudios/MoonMind#3257.

Revision ID: 340_container_job_registry_auth
Revises: 339_merge_migration_heads
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "340_container_job_registry_auth"
down_revision: Union[str, None] = "339_merge_migration_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "container_jobs",
        sa.Column("authorization_observation_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("container_jobs", "authorization_observation_json")
