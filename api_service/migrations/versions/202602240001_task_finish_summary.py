"""Add queue job finish outcome and summary columns."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602240001"
down_revision: str | None = "202602220002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "agent_jobs",
        sa.Column("finish_outcome_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_jobs",
        sa.Column("finish_outcome_stage", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_jobs",
        sa.Column("finish_outcome_reason", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "agent_jobs",
        sa.Column("finish_summary_json", _json_variant(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_jobs", "finish_summary_json")
    op.drop_column("agent_jobs", "finish_outcome_reason")
    op.drop_column("agent_jobs", "finish_outcome_stage")
    op.drop_column("agent_jobs", "finish_outcome_code")
