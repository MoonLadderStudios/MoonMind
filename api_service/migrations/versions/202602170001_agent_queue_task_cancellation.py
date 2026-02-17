"""Add cancellation request metadata fields for agent queue jobs."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202602170001"
down_revision: Union[str, None] = "202602140001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply queue cancellation metadata schema updates."""

    op.add_column(
        "agent_jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_jobs",
        sa.Column("cancel_requested_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "agent_jobs",
        sa.Column("cancel_reason", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_jobs_cancel_requested_by_user_id",
        "agent_jobs",
        "user",
        ["cancel_requested_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Revert queue cancellation metadata schema updates."""

    op.drop_constraint(
        "fk_agent_jobs_cancel_requested_by_user_id",
        "agent_jobs",
        type_="foreignkey",
    )
    op.drop_column("agent_jobs", "cancel_reason")
    op.drop_column("agent_jobs", "cancel_requested_by_user_id")
    op.drop_column("agent_jobs", "cancel_requested_at")
