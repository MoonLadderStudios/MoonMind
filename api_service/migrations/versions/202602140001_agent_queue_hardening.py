"""Add queue hardening schema objects for Milestone 5."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602140001"
down_revision: Union[str, None] = "202602130002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGENT_JOB_EVENT_LEVEL = postgresql.ENUM(
    "info",
    "warn",
    "error",
    name="agentjobeventlevel",
    create_type=False,
)


def upgrade() -> None:
    """Apply queue hardening schema updates."""

    bind = op.get_bind()

    # Extend queue status enum with dead-letter terminal value.
    op.execute("ALTER TYPE agentjobstatus ADD VALUE IF NOT EXISTS 'dead_letter'")

    op.add_column(
        "agent_jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_jobs_next_attempt_at",
        "agent_jobs",
        ["next_attempt_at"],
        unique=False,
    )

    AGENT_JOB_EVENT_LEVEL.create(bind, checkfirst=True)

    op.create_table(
        "agent_job_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "level",
            postgresql.ENUM(name="agentjobeventlevel", create_type=False),
            nullable=False,
            server_default=sa.text("'info'::agentjobeventlevel"),
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
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
            ["job_id"],
            ["agent_jobs.id"],
            name="fk_agent_job_events_job_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_job_events"),
    )
    op.create_index(
        "ix_agent_job_events_job_id_created_at",
        "agent_job_events",
        ["job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_job_events_level_created_at",
        "agent_job_events",
        ["level", "created_at"],
        unique=False,
    )

    op.create_table(
        "agent_worker_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column(
            "allowed_repositories",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "allowed_job_types",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "capabilities",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_agent_worker_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_agent_worker_tokens_token_hash"),
    )
    op.create_index(
        "ix_agent_worker_tokens_worker_id",
        "agent_worker_tokens",
        ["worker_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_worker_tokens_is_active",
        "agent_worker_tokens",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        "ix_agent_worker_tokens_created_at",
        "agent_worker_tokens",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Revert queue hardening schema updates."""

    op.drop_index("ix_agent_worker_tokens_created_at", table_name="agent_worker_tokens")
    op.drop_index("ix_agent_worker_tokens_is_active", table_name="agent_worker_tokens")
    op.drop_index("ix_agent_worker_tokens_worker_id", table_name="agent_worker_tokens")
    op.drop_table("agent_worker_tokens")

    op.drop_index("ix_agent_job_events_level_created_at", table_name="agent_job_events")
    op.drop_index(
        "ix_agent_job_events_job_id_created_at", table_name="agent_job_events"
    )
    op.drop_table("agent_job_events")

    bind = op.get_bind()
    AGENT_JOB_EVENT_LEVEL.drop(bind, checkfirst=True)

    op.drop_index("ix_agent_jobs_next_attempt_at", table_name="agent_jobs")
    op.drop_column("agent_jobs", "next_attempt_at")

    # PostgreSQL does not support dropping enum values in-place.
    op.execute(
        "UPDATE agent_jobs SET status='failed'::agentjobstatus WHERE status='dead_letter'::agentjobstatus"
    )
