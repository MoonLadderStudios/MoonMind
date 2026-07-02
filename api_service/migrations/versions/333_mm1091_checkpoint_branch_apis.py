"""Add checkpoint branch API persistence for MM-1091.

Source issue traceability: MM-1087.

Revision ID: 333_mm1091_checkpoint_branch_apis
Revises: 332_mm1024_no_commit_status
Create Date: 2026-07-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "333_mm1091_checkpoint_branch_apis"
down_revision: Union[str, None] = "332_mm1024_no_commit_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_checkpoint_branches",
        sa.Column("branch_id", sa.String(length=64), primary_key=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("root_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=False),
        sa.Column("logical_step_id", sa.String(length=255), nullable=True),
        sa.Column("source_execution_ordinal", sa.Integer(), nullable=True),
        sa.Column("source_checkpoint_boundary", sa.String(length=64), nullable=False),
        sa.Column("source_checkpoint_ref", sa.String(length=512), nullable=False),
        sa.Column("source_checkpoint_digest", sa.String(length=128), nullable=True),
        sa.Column("parent_branch_id", sa.String(length=64), nullable=True),
        sa.Column("parent_turn_id", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column(
            "state", sa.String(length=32), nullable=False, server_default="draft"
        ),
        sa.Column("branch_kind", sa.String(length=32), nullable=False),
        sa.Column("workspace_policy", sa.String(length=96), nullable=False),
        sa.Column("runtime_context_policy", sa.String(length=64), nullable=False),
        sa.Column("git_repository", sa.String(length=255), nullable=True),
        sa.Column("git_base_branch", sa.String(length=255), nullable=True),
        sa.Column("git_base_commit", sa.String(length=128), nullable=True),
        sa.Column("git_work_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "current_head_step_execution_id", sa.String(length=512), nullable=True
        ),
        sa.Column("current_head_checkpoint_ref", sa.String(length=512), nullable=True),
        sa.Column("current_head_commit", sa.String(length=128), nullable=True),
        sa.Column("pull_request_url", sa.Text(), nullable=True),
        sa.Column("publish_status", sa.String(length=32), nullable=True),
        sa.Column("promotion_evidence", sa.JSON(), nullable=True),
        sa.Column("archive_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "idempotency_key",
            name="uq_workflow_checkpoint_branches_workflow_idempotency",
        ),
    )
    op.create_index(
        "ix_workflow_checkpoint_branches_workflow_state",
        "workflow_checkpoint_branches",
        ["workflow_id", "state"],
    )
    op.create_index(
        "ix_workflow_checkpoint_branches_parent",
        "workflow_checkpoint_branches",
        ["parent_branch_id", "parent_turn_id"],
    )

    op.create_table(
        "workflow_checkpoint_branch_turns",
        sa.Column("branch_turn_id", sa.String(length=64), primary_key=True),
        sa.Column("branch_id", sa.String(length=64), nullable=False),
        sa.Column("parent_turn_id", sa.String(length=64), nullable=True),
        sa.Column("source_checkpoint_ref", sa.String(length=512), nullable=False),
        sa.Column("source_checkpoint_digest", sa.String(length=128), nullable=True),
        sa.Column("instruction_ref", sa.String(length=512), nullable=False),
        sa.Column("instruction_digest", sa.String(length=128), nullable=False),
        sa.Column("context_bundle_ref", sa.String(length=512), nullable=True),
        sa.Column("created_step_execution_id", sa.String(length=512), nullable=True),
        sa.Column("runtime_agent_run_id", sa.String(length=255), nullable=True),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="created"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["workflow_checkpoint_branches.branch_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "branch_id",
            "idempotency_key",
            name="uq_workflow_checkpoint_branch_turns_branch_idempotency",
        ),
    )
    op.create_index(
        "ix_workflow_checkpoint_branch_turns_branch_created",
        "workflow_checkpoint_branch_turns",
        ["branch_id", "created_at"],
    )

    op.create_table(
        "workflow_checkpoint_branch_operations",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("branch_id", sa.String(length=64), nullable=True),
        sa.Column("branch_turn_id", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("request_digest", sa.String(length=128), nullable=False),
        sa.Column(
            "response_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "idempotency_key",
            name="uq_workflow_checkpoint_branch_operations_workflow_idempotency",
        ),
    )
    op.create_index(
        "ix_workflow_checkpoint_branch_operations_branch",
        "workflow_checkpoint_branch_operations",
        ["branch_id", "operation"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_checkpoint_branch_operations_branch",
        table_name="workflow_checkpoint_branch_operations",
    )
    op.drop_table("workflow_checkpoint_branch_operations")
    op.drop_index(
        "ix_workflow_checkpoint_branch_turns_branch_created",
        table_name="workflow_checkpoint_branch_turns",
    )
    op.drop_table("workflow_checkpoint_branch_turns")
    op.drop_index(
        "ix_workflow_checkpoint_branches_parent",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_index(
        "ix_workflow_checkpoint_branches_workflow_state",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_table("workflow_checkpoint_branches")
