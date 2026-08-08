"""Persist terminal linked-continuation lineage and idempotent reservation.

MoonLadderStudios/MoonMind#3641: the explicit **Continue in a new workflow**
action from a terminal source Workflow Execution. Binds one client idempotency
key to exactly one destination Workflow, pins the authorized source identity and
evidence refs, and exposes a durable bidirectional ``linked_continuation``
relationship. Distinct from control-stop continuations, remediation links, and
checkpoint branches.

Revision ID: 354_workflow_linked_continuation
Revises: 353_omnigent_chat_binding
"""

from alembic import op
import sqlalchemy as sa

revision = "354_workflow_linked_continuation"
down_revision = "353_omnigent_chat_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_linked_continuations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_workflow_id", sa.String(255), nullable=False),
        sa.Column("source_run_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("request_digest", sa.String(128), nullable=False),
        sa.Column(
            "relationship_type",
            sa.String(64),
            server_default=sa.text("'linked_continuation'"),
            nullable=False,
        ),
        sa.Column("source_logical_step_id", sa.String(255), nullable=True),
        sa.Column("source_step_execution_id", sa.String(255), nullable=True),
        sa.Column("pinned_source_refs", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("bounded_purpose", sa.String(2000), nullable=True),
        sa.Column("destination_workflow_id", sa.String(255), nullable=True),
        sa.Column("destination_run_id", sa.String(64), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_workflow_id",
            "source_run_id",
            "idempotency_key",
            name="uq_workflow_linked_continuations_source_key",
        ),
        sa.UniqueConstraint(
            "destination_workflow_id",
            name="uq_workflow_linked_continuations_destination",
        ),
    )
    op.create_index(
        "ix_workflow_linked_continuations_source_workflow",
        "workflow_linked_continuations",
        ["source_workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_linked_continuations_source_workflow",
        table_name="workflow_linked_continuations",
    )
    op.drop_table("workflow_linked_continuations")
