"""Add list-optimized indexes for agent queue jobs."""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "202602210001"
down_revision: Union[str, None] = "e1c2d0f1a9f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create indexes used by queue list/status queries."""

    op.create_index(
        "ix_agent_jobs_status_created_at_id",
        "agent_jobs",
        ["status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_jobs_created_at_id",
        "agent_jobs",
        ["created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop list indexes added for queue performance."""

    op.drop_index("ix_agent_jobs_created_at_id", table_name="agent_jobs")
    op.drop_index("ix_agent_jobs_status_created_at_id", table_name="agent_jobs")
