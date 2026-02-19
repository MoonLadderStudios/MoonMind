"""Add task proposals control-plane table."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602180003"
down_revision: Union[str, None] = "202602180002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TASK_PROPOSAL_STATUS = postgresql.ENUM(
    "open",
    "promoted",
    "dismissed",
    "accepted",
    "rejected",
    name="taskproposalstatus",
    create_type=False,
)

TASK_PROPOSAL_ORIGIN = postgresql.ENUM(
    "queue",
    "orchestrator",
    "workflow",
    "manual",
    name="taskproposaloriginsource",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    TASK_PROPOSAL_STATUS.create(bind, checkfirst=True)
    TASK_PROPOSAL_ORIGIN.create(bind, checkfirst=True)

    op.create_table(
        "task_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="taskproposalstatus", create_type=False),
            nullable=False,
            server_default=sa.text("'open'::taskproposalstatus"),
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column(
            "task_create_request",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("proposed_by_worker_id", sa.String(length=255), nullable=True),
        sa.Column(
            "proposed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "origin_source",
            postgresql.ENUM(name="taskproposaloriginsource", create_type=False),
            nullable=False,
            server_default=sa.text("'manual'::taskproposaloriginsource"),
        ),
        sa.Column("origin_id", sa.Uuid(), nullable=True),
        sa.Column(
            "origin_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "promoted_job_id",
            sa.Uuid(),
            sa.ForeignKey("agent_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "promoted_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "promoted_job_id", name="uq_task_proposals_promoted_job_id"
        ),
    )
    op.create_index(
        "ix_task_proposals_status_created",
        "task_proposals",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_task_proposals_origin",
        "task_proposals",
        ["origin_source", "origin_id"],
    )
    op.create_index(
        "ix_task_proposals_repository",
        "task_proposals",
        ["repository"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_proposals_repository", table_name="task_proposals")
    op.drop_index("ix_task_proposals_origin", table_name="task_proposals")
    op.drop_index("ix_task_proposals_status_created", table_name="task_proposals")
    op.drop_table("task_proposals")

    bind = op.get_bind()
    TASK_PROPOSAL_ORIGIN.drop(bind, checkfirst=True)
    TASK_PROPOSAL_STATUS.drop(bind, checkfirst=True)
