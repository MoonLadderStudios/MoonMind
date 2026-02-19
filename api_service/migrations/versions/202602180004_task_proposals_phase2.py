"""Task proposal queue phase 2 extensions."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602180004"
down_revision: Union[str, None] = "202602180003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TASK_PROPOSAL_PRIORITY = postgresql.ENUM(
    "low",
    "normal",
    "high",
    "urgent",
    name="taskproposalpriority",
    create_type=False,
)


def _slugify(text: str) -> str:
    """Return canonical slug for deduplication."""

    normalized = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not normalized:
        return "untitled"
    return normalized[:200]


def _compute_dedup_key(repository: str, title: str) -> tuple[str, str]:
    repo = (repository or "").strip().lower()
    if not repo:
        repo = "unknown"
    slug = _slugify(title or "")
    dedup_key = f"{repo}:{slug}"
    dedup_hash = hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()
    return dedup_key[:512], dedup_hash


def upgrade() -> None:
    bind = op.get_bind()
    TASK_PROPOSAL_PRIORITY.create(bind, checkfirst=True)

    op.add_column(
        "task_proposals",
        sa.Column(
            "dedup_key", sa.String(length=512), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "task_proposals",
        sa.Column(
            "dedup_hash", sa.String(length=64), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "task_proposals",
        sa.Column(
            "review_priority",
            postgresql.ENUM(name="taskproposalpriority", create_type=False),
            nullable=False,
            server_default=sa.text("'normal'::taskproposalpriority"),
        ),
    )
    op.add_column(
        "task_proposals",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "task_proposals",
        sa.Column(
            "snoozed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "task_proposals",
        sa.Column("snooze_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "task_proposals",
        sa.Column(
            "snooze_history",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.create_index(
        "ix_task_proposals_dedup_hash_status",
        "task_proposals",
        ["dedup_hash", "status"],
    )
    op.create_index(
        "ix_task_proposals_priority_created",
        "task_proposals",
        ["review_priority", "created_at"],
    )
    op.create_index(
        "ix_task_proposals_snoozed_until",
        "task_proposals",
        ["snoozed_until"],
        postgresql_where=sa.text("snoozed_until IS NOT NULL"),
    )

    op.create_table(
        "task_proposal_notifications",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "proposal_id",
            sa.Uuid(),
            sa.ForeignKey("task_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "proposal_id",
            "target",
            name="uq_task_proposal_notifications_proposal_target",
        ),
    )

    task_proposals = sa.table(
        "task_proposals",
        sa.column("id", sa.Uuid),
        sa.column("repository", sa.String),
        sa.column("title", sa.String),
        sa.column("dedup_key", sa.String),
        sa.column("dedup_hash", sa.String),
    )
    result = bind.execute(
        sa.select(
            task_proposals.c.id, task_proposals.c.repository, task_proposals.c.title
        )
    )
    rows = result.fetchall()
    for row in rows:
        dedup_key, dedup_hash = _compute_dedup_key(
            row.repository or "", row.title or ""
        )
        bind.execute(
            task_proposals.update()
            .where(task_proposals.c.id == row.id)
            .values(dedup_key=dedup_key, dedup_hash=dedup_hash)
        )

    op.alter_column("task_proposals", "dedup_key", server_default=None)
    op.alter_column("task_proposals", "dedup_hash", server_default=None)


def downgrade() -> None:
    op.drop_table("task_proposal_notifications")
    op.drop_index("ix_task_proposals_snoozed_until", table_name="task_proposals")
    op.drop_index("ix_task_proposals_priority_created", table_name="task_proposals")
    op.drop_index("ix_task_proposals_dedup_hash_status", table_name="task_proposals")

    op.drop_column("task_proposals", "snooze_history")
    op.drop_column("task_proposals", "snooze_note")
    op.drop_column("task_proposals", "snoozed_by_user_id")
    op.drop_column("task_proposals", "snoozed_until")
    op.drop_column("task_proposals", "review_priority")
    op.drop_column("task_proposals", "dedup_hash")
    op.drop_column("task_proposals", "dedup_key")

    bind = op.get_bind()
    TASK_PROPOSAL_PRIORITY.drop(bind, checkfirst=True)
