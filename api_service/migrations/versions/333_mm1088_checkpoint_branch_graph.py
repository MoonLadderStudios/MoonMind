"""Persist checkpoint branch graph records for MM-1088.

Source traceability: MM-1087 Checkpoint Branch System design.

Revision ID: 333_mm1088_checkpoint_branch_graph
Revises: 332_mm1024_no_commit_status
Create Date: 2026-07-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "333_mm1088_checkpoint_branch_graph"
down_revision: Union[str, None] = "332_mm1024_no_commit_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


branch_state = sa.Enum(
    "created",
    "preparing",
    "active",
    "blocked",
    "failed",
    "succeeded",
    "promotable",
    "promoted",
    "archived",
    "superseded",
    name="checkpointbranchstate",
)
turn_state = sa.Enum(
    "created",
    "preparing",
    "running",
    "checking",
    "succeeded",
    "failed",
    "blocked",
    "canceled",
    "superseded",
    name="checkpointbranchturnstate",
)
branch_kind = sa.Enum("root", "child_fork", name="checkpointbranchkind")
workspace_policy = sa.Enum(
    "continue_from_previous_execution",
    "restore_pre_execution",
    "apply_previous_execution_diff_to_clean_baseline",
    "start_from_last_passed_commit",
    "fresh_branch_from_source",
    name="checkpointbranchworkspacepolicy",
)
runtime_context_policy = sa.Enum(
    "fresh_agent_run",
    "reuse_session_new_epoch",
    "reuse_session_same_epoch",
    "external_provider_continuation",
    name="checkpointbranchruntimecontextpolicy",
)
publish_status = sa.Enum(
    "unpublished",
    "preparing",
    "published",
    "failed",
    "archived",
    name="checkpointbranchpublishstatus",
)


def upgrade() -> None:
    op.create_table(
        "workflow_checkpoint_branches",
        sa.Column("branch_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("root_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("source_run_id", sa.String(length=64), nullable=False),
        sa.Column("logical_step_id", sa.String(length=255), nullable=True),
        sa.Column("source_execution_ordinal", sa.Integer(), nullable=True),
        sa.Column("source_checkpoint_boundary", sa.String(length=128), nullable=True),
        sa.Column("source_checkpoint_ref", sa.String(length=512), nullable=True),
        sa.Column("source_checkpoint_digest", sa.String(length=128), nullable=True),
        sa.Column("source_state_kind", sa.String(length=64), nullable=True),
        sa.Column("source_state_ref", sa.String(length=512), nullable=True),
        sa.Column("source_state_digest", sa.String(length=128), nullable=True),
        sa.Column("parent_branch_id", sa.String(length=128), nullable=True),
        sa.Column("parent_turn_id", sa.String(length=128), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column(
            "state",
            branch_state,
            server_default="created",
            nullable=False,
        ),
        sa.Column(
            "branch_kind",
            branch_kind,
            server_default="root",
            nullable=False,
        ),
        sa.Column("workspace_policy", workspace_policy, nullable=False),
        sa.Column("runtime_context_policy", runtime_context_policy, nullable=False),
        sa.Column("git_repository", sa.String(length=512), nullable=True),
        sa.Column("git_base_branch", sa.String(length=255), nullable=True),
        sa.Column("git_base_commit", sa.String(length=64), nullable=True),
        sa.Column("git_work_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "current_head_step_execution_id",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column("current_head_checkpoint_ref", sa.String(length=512), nullable=True),
        sa.Column("current_head_commit", sa.String(length=64), nullable=True),
        sa.Column("pull_request_url", sa.String(length=1024), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint(
            "(source_checkpoint_ref IS NOT NULL "
            "AND source_checkpoint_boundary IS NOT NULL) "
            "OR (source_state_kind IS NOT NULL AND source_state_ref IS NOT NULL)",
            name="ck_checkpoint_branch_requires_source_ref",
        ),
        sa.CheckConstraint(
            "branch_id NOT LIKE '%:execution:%'",
            name="ck_checkpoint_branch_not_step_execution_id",
        ),
        sa.CheckConstraint(
            "branch_kind != 'child_fork' "
            "OR (parent_branch_id IS NOT NULL AND parent_turn_id IS NOT NULL)",
            name="ck_checkpoint_branch_child_has_parent_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["parent_branch_id"],
            ["workflow_checkpoint_branches.branch_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("branch_id"),
    )
    op.create_index(
        "ix_checkpoint_branches_workflow",
        "workflow_checkpoint_branches",
        ["workflow_id"],
    )
    op.create_index(
        "ix_checkpoint_branches_root_workflow",
        "workflow_checkpoint_branches",
        ["root_workflow_id"],
    )
    op.create_index(
        "ix_checkpoint_branches_source",
        "workflow_checkpoint_branches",
        ["workflow_id", "source_run_id"],
    )

    op.create_table(
        "workflow_checkpoint_branch_turns",
        sa.Column("branch_turn_id", sa.String(length=128), nullable=False),
        sa.Column("branch_id", sa.String(length=128), nullable=False),
        sa.Column("parent_turn_id", sa.String(length=128), nullable=True),
        sa.Column("source_checkpoint_ref", sa.String(length=512), nullable=True),
        sa.Column("source_checkpoint_digest", sa.String(length=128), nullable=True),
        sa.Column("source_state_kind", sa.String(length=64), nullable=True),
        sa.Column("source_state_ref", sa.String(length=512), nullable=True),
        sa.Column("source_state_digest", sa.String(length=128), nullable=True),
        sa.Column("instruction_ref", sa.String(length=512), nullable=False),
        sa.Column("instruction_digest", sa.String(length=128), nullable=False),
        sa.Column("context_bundle_ref", sa.String(length=512), nullable=True),
        sa.Column("created_step_execution_id", sa.String(length=512), nullable=True),
        sa.Column("runtime_agent_run_id", sa.String(length=255), nullable=True),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", turn_state, server_default="created", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "source_checkpoint_ref IS NOT NULL "
            "OR (source_state_kind IS NOT NULL AND source_state_ref IS NOT NULL)",
            name="ck_checkpoint_branch_turn_requires_source_ref",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["workflow_checkpoint_branches.branch_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_turn_id"],
            ["workflow_checkpoint_branch_turns.branch_turn_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("branch_turn_id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_checkpoint_branch_turn_idempotency",
        ),
    )
    op.create_index(
        "ix_checkpoint_branch_turns_branch",
        "workflow_checkpoint_branch_turns",
        ["branch_id"],
    )

    op.create_table(
        "workflow_checkpoint_branch_git_bindings",
        sa.Column("branch_id", sa.String(length=128), nullable=False),
        sa.Column("repository", sa.String(length=512), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("base_commit", sa.String(length=64), nullable=False),
        sa.Column("work_branch", sa.String(length=255), nullable=False),
        sa.Column("worktree_ref", sa.String(length=512), nullable=True),
        sa.Column("head_commit", sa.String(length=64), nullable=True),
        sa.Column("patch_ref", sa.String(length=512), nullable=True),
        sa.Column("pull_request_url", sa.String(length=1024), nullable=True),
        sa.Column(
            "publish_status",
            publish_status,
            server_default="unpublished",
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "work_branch NOT IN ('main', 'master', 'HEAD', '')",
            name="ck_checkpoint_branch_git_work_branch_safe",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["workflow_checkpoint_branches.branch_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("branch_id"),
    )

    op.create_table(
        "workflow_checkpoint_branch_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("branch_id", sa.String(length=128), nullable=False),
        sa.Column("branch_turn_id", sa.String(length=128), nullable=True),
        sa.Column("artifact_ref", sa.String(length=512), nullable=False),
        sa.Column("artifact_kind", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checkpoint_branch_artifacts_branch",
        "workflow_checkpoint_branch_artifacts",
        ["branch_id"],
    )
    op.create_index(
        "ix_checkpoint_branch_artifacts_turn",
        "workflow_checkpoint_branch_artifacts",
        ["branch_turn_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_checkpoint_branch_artifacts_turn",
        table_name="workflow_checkpoint_branch_artifacts",
    )
    op.drop_index(
        "ix_checkpoint_branch_artifacts_branch",
        table_name="workflow_checkpoint_branch_artifacts",
    )
    op.drop_table("workflow_checkpoint_branch_artifacts")
    op.drop_table("workflow_checkpoint_branch_git_bindings")
    op.drop_index(
        "ix_checkpoint_branch_turns_branch",
        table_name="workflow_checkpoint_branch_turns",
    )
    op.drop_table("workflow_checkpoint_branch_turns")
    op.drop_index(
        "ix_checkpoint_branches_source",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_index(
        "ix_checkpoint_branches_root_workflow",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_index(
        "ix_checkpoint_branches_workflow",
        table_name="workflow_checkpoint_branches",
    )
    op.drop_table("workflow_checkpoint_branches")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        publish_status.drop(bind, checkfirst=True)
        runtime_context_policy.drop(bind, checkfirst=True)
        workspace_policy.drop(bind, checkfirst=True)
        branch_kind.drop(bind, checkfirst=True)
        turn_state.drop(bind, checkfirst=True)
        branch_state.drop(bind, checkfirst=True)
