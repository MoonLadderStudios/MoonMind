"""Add temporal integrations monitoring projection tables."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202603060003"  # noqa: F401
down_revision: str | None = "202603060002"  # noqa: F401
branch_labels: Union[str, Sequence[str], None] = None  # noqa: F401
depends_on: Union[str, Sequence[str], None] = None  # noqa: F401


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    temporal_execution_columns = {
        column["name"] for column in inspector.get_columns("temporal_executions")
    }

    if "integration_state" not in temporal_execution_columns:
        op.add_column(
            "temporal_executions",
            sa.Column("integration_state", _json_variant(), nullable=True),
        )

    if "temporal_integration_correlations" not in inspector.get_table_names():
        op.create_table(
            "temporal_integration_correlations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("integration_name", sa.String(length=64), nullable=False),
            sa.Column("correlation_id", sa.String(length=128), nullable=False),
            sa.Column("callback_correlation_key", sa.String(length=128), nullable=True),
            sa.Column("external_operation_id", sa.String(length=255), nullable=True),
            sa.Column("workflow_id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
                ["workflow_id"],
                ["temporal_executions.workflow_id"],
                name="fk_temporal_integration_correlations_workflow_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_temporal_integration_correlations"),
            sa.UniqueConstraint(
                "integration_name",
                "callback_correlation_key",
                name="uq_temporal_integration_correlations_callback_key",
            ),
            sa.UniqueConstraint(
                "integration_name",
                "external_operation_id",
                name="uq_temporal_integration_correlations_operation_id",
            ),
        )

    correlation_indexes = {
        index["name"]
        for index in inspector.get_indexes("temporal_integration_correlations")
    }
    if "ix_temporal_integration_correlations_workflow_status" not in correlation_indexes:
        op.create_index(
            "ix_temporal_integration_correlations_workflow_status",
            "temporal_integration_correlations",
            ["workflow_id", "lifecycle_status"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_temporal_integration_correlations_workflow_status",
        table_name="temporal_integration_correlations",
    )
    op.drop_table("temporal_integration_correlations")
    op.drop_column("temporal_executions", "integration_state")
