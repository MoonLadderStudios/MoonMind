"""add Spec Automation persistence tables

Revision ID: b3c0bb1d69d7
Revises: 909263807406
Create Date: 2025-11-05 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b3c0bb1d69d7"
down_revision: Union[str, None] = "909263807406"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SPEC_AUTOMATION_RUN_STATUS = postgresql.ENUM(
    "queued",
    "in_progress",
    "succeeded",
    "failed",
    "no_changes",
    name="specautomationrunstatus",
)

SPEC_AUTOMATION_PHASE = postgresql.ENUM(
    "prepare_job",
    "start_job_container",
    "git_clone",
    "speckit_specify",
    "speckit_plan",
    "speckit_tasks",
    "commit_push",
    "open_pr",
    "cleanup",
    name="specautomationphase",
)

SPEC_AUTOMATION_TASK_STATUS = postgresql.ENUM(
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "retrying",
    name="specautomationtaskstatus",
)

SPEC_AUTOMATION_ARTIFACT_TYPE = postgresql.ENUM(
    "stdout_log",
    "stderr_log",
    "diff_summary",
    "commit_status",
    "metrics_snapshot",
    "environment_info",
    name="specautomationartifacttype",
)


def upgrade() -> None:  # noqa: D401
    """Create Spec Automation tables and supporting enums."""

    bind = op.get_bind()
    SPEC_AUTOMATION_RUN_STATUS.create(bind, checkfirst=True)
    SPEC_AUTOMATION_PHASE.create(bind, checkfirst=True)
    SPEC_AUTOMATION_TASK_STATUS.create(bind, checkfirst=True)
    SPEC_AUTOMATION_ARTIFACT_TYPE.create(bind, checkfirst=True)

    op.create_table(
        "spec_automation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column(
            "base_branch", sa.String(length=128), nullable=False, server_default="main"
        ),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("pull_request_url", sa.String(length=512), nullable=True),
        sa.Column(
            "status",
            SPEC_AUTOMATION_RUN_STATUS,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("requested_spec_input", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_hostname", sa.String(length=255), nullable=True),
        sa.Column("job_container_id", sa.String(length=255), nullable=True),
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
            server_onupdate=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_spec_automation_runs_status",
        "spec_automation_runs",
        ["status"],
    )
    op.create_index(
        "ix_spec_automation_runs_repository",
        "spec_automation_runs",
        ["repository"],
    )
    op.create_index(
        "ix_spec_automation_runs_created_at",
        "spec_automation_runs",
        ["created_at"],
    )
    op.create_index(
        "ix_spec_automation_runs_external_ref",
        "spec_automation_runs",
        ["external_ref"],
    )

    op.create_table(
        "spec_automation_task_states",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_automation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", SPEC_AUTOMATION_PHASE, nullable=False),
        sa.Column("status", SPEC_AUTOMATION_TASK_STATUS, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stdout_path", sa.String(length=1024), nullable=True),
        sa.Column("stderr_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
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
            server_onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "run_id",
            "phase",
            "attempt",
            name="uq_spec_automation_task_state_attempt",
        ),
    )

    op.create_index(
        "ix_spec_automation_task_states_run_id",
        "spec_automation_task_states",
        ["run_id"],
    )

    op.create_table(
        "spec_automation_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_automation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_state_id",
            sa.Uuid(),
            sa.ForeignKey("spec_automation_task_states.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("artifact_type", SPEC_AUTOMATION_ARTIFACT_TYPE, nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_phase", SPEC_AUTOMATION_PHASE, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "run_id",
            "artifact_type",
            "storage_path",
            name="uq_spec_automation_artifact_path",
        ),
    )

    op.create_index(
        "ix_spec_automation_artifacts_run_id",
        "spec_automation_artifacts",
        ["run_id"],
    )

    op.create_table(
        "spec_automation_agent_configs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_automation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_backend", sa.String(length=128), nullable=False),
        sa.Column("agent_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_pack_version", sa.String(length=128), nullable=True),
        sa.Column(
            "runtime_env",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "run_id",
            name="uq_spec_automation_agent_config_run",
        ),
    )


def downgrade() -> None:  # noqa: D401
    """Drop Spec Automation tables and enums."""

    op.drop_table("spec_automation_agent_configs")
    op.drop_index(
        "ix_spec_automation_artifacts_run_id",
        table_name="spec_automation_artifacts",
    )
    op.drop_table("spec_automation_artifacts")
    op.drop_index(
        "ix_spec_automation_task_states_run_id",
        table_name="spec_automation_task_states",
    )
    op.drop_table("spec_automation_task_states")
    op.drop_index(
        "ix_spec_automation_runs_external_ref",
        table_name="spec_automation_runs",
    )
    op.drop_index(
        "ix_spec_automation_runs_created_at",
        table_name="spec_automation_runs",
    )
    op.drop_index(
        "ix_spec_automation_runs_repository",
        table_name="spec_automation_runs",
    )
    op.drop_index(
        "ix_spec_automation_runs_status",
        table_name="spec_automation_runs",
    )
    op.drop_table("spec_automation_runs")

    bind = op.get_bind()
    SPEC_AUTOMATION_ARTIFACT_TYPE.drop(bind, checkfirst=True)
    SPEC_AUTOMATION_TASK_STATUS.drop(bind, checkfirst=True)
    SPEC_AUTOMATION_PHASE.drop(bind, checkfirst=True)
    SPEC_AUTOMATION_RUN_STATUS.drop(bind, checkfirst=True)
