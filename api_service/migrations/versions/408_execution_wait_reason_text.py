"""Preserve execution wait explanations in sources and projections."""

from alembic import op
import sqlalchemy as sa

revision = "408_execution_wait_reason_text"
down_revision = "407_merge_artifact_chat_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("temporal_execution_sources", "temporal_executions"):
        op.alter_column(
            table,
            "waiting_reason",
            existing_type=sa.String(32),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    for table in ("temporal_execution_sources", "temporal_executions"):
        op.alter_column(
            table,
            "waiting_reason",
            existing_type=sa.Text(),
            type_=sa.String(32),
            existing_nullable=True,
        )
