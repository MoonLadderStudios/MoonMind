"""Retain durable admission and terminal observations for recurring owners."""

import sqlalchemy as sa
from alembic import op

revision = "405_recurring_owner_terminal"
down_revision = "404_merge_index_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recurring_workflow_runs",
        sa.Column("terminal_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "recurring_workflow_runs",
        sa.Column(
            "implementation_admitted_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "ix_recurring_runs_definition_terminal",
        "recurring_workflow_runs",
        ["definition_id", "terminal_observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recurring_runs_definition_terminal", table_name="recurring_workflow_runs"
    )
    op.drop_column("recurring_workflow_runs", "terminal_observed_at")
    op.drop_column("recurring_workflow_runs", "implementation_admitted_at")
