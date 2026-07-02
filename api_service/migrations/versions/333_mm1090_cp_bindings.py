"""Add checkpoint branch git binding records for MM-1090.

Revision ID: 333_mm1090_cp_bindings
Revises: 332_mm1024_no_commit_status
Create Date: 2026-07-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "333_mm1090_cp_bindings"
down_revision: Union[str, None] = "332_mm1024_no_commit_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_checkpoint_branches",
        sa.Column("branch_id", sa.String(length=255), primary_key=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("root_workflow_id", sa.String(length=255), nullable=True),
        sa.Column("source_run_id", sa.String(length=255), nullable=True),
        sa.Column("logical_step_id", sa.String(length=255), nullable=True),
        sa.Column("source_execution_ordinal", sa.Integer(), nullable=True),
        sa.Column("source_checkpoint_boundary", sa.String(length=64), nullable=False),
        sa.Column("source_checkpoint_ref", sa.String(length=1024), nullable=False),
        sa.Column("source_checkpoint_digest", sa.String(length=128), nullable=True),
        sa.Column("parent_branch_id", sa.String(length=255), nullable=True),
        sa.Column("parent_turn_id", sa.String(length=255), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=64), nullable=False, server_default="created"),
        sa.Column(
            "branch_kind",
            sa.String(length=64),
            nullable=False,
            server_default="checkpoint",
        ),
        sa.Column("workspace_policy", sa.String(length=64), nullable=False),
        sa.Column("runtime_context_policy", sa.String(length=64), nullable=True),
        sa.Column("git_repository", sa.String(length=512), nullable=True),
        sa.Column("git_base_branch", sa.String(length=255), nullable=True),
        sa.Column("git_base_commit", sa.String(length=128), nullable=True),
        sa.Column("git_work_branch", sa.String(length=255), nullable=True),
        sa.Column("current_head_step_execution_id", sa.String(length=255), nullable=True),
        sa.Column("current_head_checkpoint_ref", sa.String(length=1024), nullable=True),
        sa.Column("current_head_commit", sa.String(length=128), nullable=True),
        sa.Column("pull_request_url", sa.String(length=1024), nullable=True),
        sa.Column("artifact_refs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("diagnostics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["parent_branch_id"],
            ["workflow_checkpoint_branches.branch_id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_checkpoint_branches_workflow",
        "workflow_checkpoint_branches",
        ["workflow_id"],
    )
    op.create_index(
        "ix_checkpoint_branches_checkpoint",
        "workflow_checkpoint_branches",
        ["source_checkpoint_ref"],
    )
    op.create_index(
        "ix_checkpoint_branches_state",
        "workflow_checkpoint_branches",
        ["state"],
    )

    op.create_table(
        "workflow_checkpoint_branch_turns",
        sa.Column("branch_turn_id", sa.String(length=255), primary_key=True),
        sa.Column("branch_id", sa.String(length=255), nullable=False),
        sa.Column("parent_turn_id", sa.String(length=255), nullable=True),
        sa.Column("source_checkpoint_ref", sa.String(length=1024), nullable=False),
        sa.Column("source_checkpoint_digest", sa.String(length=128), nullable=True),
        sa.Column("instruction_ref", sa.String(length=1024), nullable=False),
        sa.Column("instruction_digest", sa.String(length=128), nullable=False),
        sa.Column("context_bundle_ref", sa.String(length=1024), nullable=True),
        sa.Column("workspace_policy", sa.String(length=64), nullable=False),
        sa.Column("git_work_branch", sa.String(length=255), nullable=True),
        sa.Column("workspace_restore_ref", sa.String(length=1024), nullable=True),
        sa.Column("git_binding_ref", sa.String(length=1024), nullable=True),
        sa.Column("step_execution_manifest_ref", sa.String(length=1024), nullable=True),
        sa.Column("created_step_execution_id", sa.String(length=255), nullable=True),
        sa.Column("runtime_agent_run_id", sa.String(length=255), nullable=True),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="preparing"),
        sa.Column("diagnostics", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["parent_turn_id"],
            ["workflow_checkpoint_branch_turns.branch_turn_id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_checkpoint_branch_turn_idempotency_key"
        ),
    )
    op.create_index(
        "ix_checkpoint_branch_turns_branch",
        "workflow_checkpoint_branch_turns",
        ["branch_id"],
    )
    op.create_index(
        "ix_checkpoint_branch_turns_status",
        "workflow_checkpoint_branch_turns",
        ["status"],
    )

    op.create_table(
        "workflow_checkpoint_branch_git_bindings",
        sa.Column("branch_id", sa.String(length=255), primary_key=True),
        sa.Column("repository", sa.String(length=512), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("base_commit", sa.String(length=128), nullable=True),
        sa.Column("work_branch", sa.String(length=255), nullable=False),
        sa.Column("worktree_ref", sa.String(length=1024), nullable=True),
        sa.Column("provider_workspace_ref", sa.String(length=1024), nullable=True),
        sa.Column("head_commit", sa.String(length=128), nullable=True),
        sa.Column("patch_ref", sa.String(length=1024), nullable=True),
        sa.Column("pull_request_url", sa.String(length=1024), nullable=True),
        sa.Column("workspace_policy", sa.String(length=64), nullable=False),
        sa.Column("creation_mode", sa.String(length=64), nullable=False),
        sa.Column(
            "publish_status",
            sa.String(length=64),
            nullable=False,
            server_default="not_published",
        ),
        sa.Column("binding_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
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
            "repository",
            "work_branch",
            name="uq_checkpoint_branch_git_binding_work_branch",
        ),
    )
    op.create_index(
        "ix_checkpoint_branch_git_bindings_repository",
        "workflow_checkpoint_branch_git_bindings",
        ["repository"],
    )

    op.create_table(
        "workflow_checkpoint_branch_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("branch_id", sa.String(length=255), nullable=False),
        sa.Column("branch_turn_id", sa.String(length=255), nullable=True),
        sa.Column("artifact_kind", sa.String(length=128), nullable=False),
        sa.Column("artifact_ref", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("digest", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["workflow_checkpoint_branches.branch_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["branch_turn_id"],
            ["workflow_checkpoint_branch_turns.branch_turn_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "branch_id",
            "branch_turn_id",
            "artifact_kind",
            name="uq_checkpoint_branch_artifact_kind",
        ),
    )
    op.create_index(
        "ix_checkpoint_branch_artifacts_branch",
        "workflow_checkpoint_branch_artifacts",
        ["branch_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_checkpoint_branch_artifacts_branch",
        table_name="workflow_checkpoint_branch_artifacts",
    )
    op.drop_table("workflow_checkpoint_branch_artifacts")
    op.drop_index(
        "ix_checkpoint_branch_git_bindings_repository",
        table_name="workflow_checkpoint_branch_git_bindings",
    )
    op.drop_table("workflow_checkpoint_branch_git_bindings")
    op.drop_index(
        "ix_checkpoint_branch_turns_status",
        table_name="workflow_checkpoint_branch_turns",
    )
    op.drop_index(
        "ix_checkpoint_branch_turns_branch",
        table_name="workflow_checkpoint_branch_turns",
    )
    op.drop_table("workflow_checkpoint_branch_turns")
    op.drop_index(
        "ix_checkpoint_branches_state",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_index(
        "ix_checkpoint_branches_checkpoint",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_index(
        "ix_checkpoint_branches_workflow",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_table("workflow_checkpoint_branches")
