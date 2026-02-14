"""Add agent queue MVP table and supporting enum."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602130001"
down_revision: Union[str, None] = "202403150001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGENT_JOB_STATUS = postgresql.ENUM(
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    name="agentjobstatus",
    create_type=False,
)


def upgrade() -> None:
    """Create queue MVP schema objects."""

    bind = op.get_bind()
    AGENT_JOB_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "agent_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="agentjobstatus", create_type=False),
            nullable=False,
            server_default=sa.text("'queued'::agentjobstatus"),
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("affinity_key", sa.String(length=255), nullable=True),
        sa.Column("claimed_by", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("artifacts_path", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_agent_jobs_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["user.id"],
            name="fk_agent_jobs_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_jobs"),
    )
    op.create_index(
        "ix_agent_jobs_status_priority_created_at",
        "agent_jobs",
        ["status", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_jobs_type_status_created_at",
        "agent_jobs",
        ["type", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_jobs_lease_expires_at",
        "agent_jobs",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop queue MVP schema objects."""

    op.drop_index("ix_agent_jobs_lease_expires_at", table_name="agent_jobs")
    op.drop_index("ix_agent_jobs_type_status_created_at", table_name="agent_jobs")
    op.drop_index("ix_agent_jobs_status_priority_created_at", table_name="agent_jobs")
    op.drop_table("agent_jobs")

    bind = op.get_bind()
    AGENT_JOB_STATUS.drop(bind, checkfirst=True)
