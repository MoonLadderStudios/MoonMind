"""Add API-owned durable container jobs for MoonLadderStudios/MoonMind#3252.

Revision ID: 338_container_jobs_contract
Revises: 337_mm1207_oauth_hosts
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "338_container_jobs_contract"
down_revision: Union[str, None] = "337_mm1207_oauth_hosts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "container_jobs",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("contract_version", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("owner_type", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("backend_kind", sa.String(64)), sa.Column("backend_ref", sa.String(255)),
        sa.Column("image_observation_json", sa.JSON()), sa.Column("terminal_outcome_json", sa.JSON()),
        sa.Column("publication_outcome_json", sa.JSON(), nullable=False),
        sa.Column("cleanup_outcome_json", sa.JSON(), nullable=False),
        sa.Column("logs_ref", sa.String(1024)), sa.Column("artifacts_ref", sa.String(1024)),
        sa.Column("cancel_idempotency_key", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_container_jobs_owner_idempotency"),
    )
    op.create_index("ix_container_jobs_owner_created", "container_jobs", ["owner_id", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_container_jobs_owner_created", table_name="container_jobs")
    op.drop_table("container_jobs")
