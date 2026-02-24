"""Persist runtime model and effort metadata on worker tokens."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602220001"
down_revision: Union[str, None] = "202602210001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Store available runtime capability metadata in worker tokens."""

    op.add_column(
        "agent_worker_tokens",
        sa.Column(
            "runtime_capabilities",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove runtime capability metadata from worker tokens."""

    op.drop_column("agent_worker_tokens", "runtime_capabilities")
