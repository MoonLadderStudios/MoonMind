"""Persist control-stop continuation admission and deterministic reservation.

Revision ID: 347_control_stop_continuations
Revises: 346_remediation_workspace_head
"""

from alembic import op
import sqlalchemy as sa

revision = "347_control_stop_continuations"
down_revision = "346_remediation_workspace_head"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "control_stop_continuations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_workflow_id", sa.String(255), nullable=False),
        sa.Column("source_run_id", sa.String(64), nullable=False),
        sa.Column("control_stop_id", sa.String(255), nullable=False),
        sa.Column("contract_payload", sa.JSON(), nullable=False),
        sa.Column("artifact_digests", sa.JSON(), nullable=False),
        sa.Column("deployment_generation", sa.String(255), nullable=False),
        sa.Column(
            "deployment_promoted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("destination_workflow_id", sa.String(255), nullable=True),
        sa.Column("workspace_head_ref", sa.String(1024), nullable=True),
        sa.Column("remaining_work_ref", sa.String(1024), nullable=True),
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
            "control_stop_id",
            name="uq_control_stop_continuations_source_stop",
        ),
        sa.UniqueConstraint(
            "destination_workflow_id",
            name="uq_control_stop_continuations_destination",
        ),
    )
    op.create_index(
        "ix_control_stop_continuations_source_workflow",
        "control_stop_continuations",
        ["source_workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_control_stop_continuations_source_workflow",
        table_name="control_stop_continuations",
    )
    op.drop_table("control_stop_continuations")
