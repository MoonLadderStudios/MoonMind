"""Add missing columns to temporal_execution_sources table."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202603060007"  # noqa: F401
down_revision: str | None = "202603060006"  # noqa: F401
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F401
depends_on: Union[str, Sequence[str], None] = None  # noqa: F401


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"]
        for column in inspector.get_columns("temporal_execution_sources")
    }

    if "integration_state" not in existing_columns:
        op.add_column(
            "temporal_execution_sources",
            sa.Column("integration_state", _json_variant(), nullable=True),
        )

    if "waiting_reason" not in existing_columns:
        op.add_column(
            "temporal_execution_sources",
            sa.Column("waiting_reason", sa.String(length=32), nullable=True),
        )

    if "attention_required" not in existing_columns:
        op.add_column(
            "temporal_execution_sources",
            sa.Column(
                "attention_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"]
        for column in inspector.get_columns("temporal_execution_sources")
    }

    if "attention_required" in existing_columns:
        op.drop_column("temporal_execution_sources", "attention_required")

    if "waiting_reason" in existing_columns:
        op.drop_column("temporal_execution_sources", "waiting_reason")

    if "integration_state" in existing_columns:
        op.drop_column("temporal_execution_sources", "integration_state")
