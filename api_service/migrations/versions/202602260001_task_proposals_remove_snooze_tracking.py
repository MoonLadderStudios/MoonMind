"""Remove task proposal snooze tracking fields."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602260001"
down_revision: str | None = "202602240002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_task_proposals_snoozed_until", table_name="task_proposals")
    op.drop_column("task_proposals", "snooze_history")
    op.drop_column("task_proposals", "snooze_note")
    op.drop_column("task_proposals", "snoozed_by_user_id")
    op.drop_column("task_proposals", "snoozed_until")


def downgrade() -> None:
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
    op.add_column("task_proposals", sa.Column("snooze_note", sa.Text(), nullable=True))
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
        "ix_task_proposals_snoozed_until",
        "task_proposals",
        ["snoozed_until"],
        postgresql_where=sa.text("snoozed_until IS NOT NULL"),
    )
