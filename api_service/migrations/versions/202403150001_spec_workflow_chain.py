"""Rebuild Spec workflow tables for Celery chain orchestration."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202403150001"
down_revision: Union[str, None] = "202402150001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
]


SPEC_WORKFLOW_RUN_STATUS = postgresql.ENUM(
    "pending",
    "running",
    "succeeded",
    "failed",
    "no_work",
    "cancelled",
    "retrying",
    name="specworkflowrunstatus",
    create_type=False,
)

SPEC_WORKFLOW_TASK_NAME = postgresql.ENUM(
    "discover",
    "submit",
    "apply",
    "publish",
    "finalize",
    "retry-hook",
    name="specworkflowtaskname",
    create_type=False,
)

SPEC_WORKFLOW_TASK_STATE = postgresql.ENUM(
    "waiting",
    "received",
    "started",
    "succeeded",
    "failed",
    "retrying",
    name="specworkflowtaskstate",
    create_type=False,
)

WORKFLOW_CREDENTIAL_STATUS = postgresql.ENUM(
    "passed",
    "failed",
    "skipped",
    name="workflowcredentialstatus",
    create_type=False,
)

WORKFLOW_ARTIFACT_TYPE = postgresql.ENUM(
    "codex_logs",
    "codex_patch",
    "apply_output",
    "pr_payload",
    "retry_context",
    name="workflowartifacttype",
    create_type=False,
)

LEGACY_WORKFLOW_TABLES: tuple[str, ...] = (
    "workflow_artifacts",
    "workflow_credential_audits",
    "spec_workflow_task_states",
    "spec_workflow_runs",
)


def _backup_existing_tables() -> None:
    """Rename legacy Spec workflow tables to preserve their contents."""

    for table_name in LEGACY_WORKFLOW_TABLES:
        backup_name = f"legacy_{table_name}"
        quoted_table = sa.sql.elements.quoted_name(table_name, quote=True)
        quoted_backup = sa.sql.elements.quoted_name(backup_name, quote=True)

        op.execute(f"DROP TABLE IF EXISTS {quoted_backup} CASCADE")
        op.execute(f"ALTER TABLE IF EXISTS {quoted_table} RENAME TO {quoted_backup}")


def _drop_legacy_enums() -> None:
    """Drop enums installed by prior Spec workflow migrations."""

    for type_name in (
        "workflowartifacttype",
        "workflowgithubcredentialstatus",
        "workflowcodexcredentialstatus",
        "specworkflowtaskstatus",
        "specworkflowrunphase",
        "specworkflowrunstatus",
    ):
        op.execute(f"DROP TYPE IF EXISTS {type_name} CASCADE")


def _create_enum_if_missing(enum_type: postgresql.ENUM) -> None:
    """Create a PostgreSQL enum type only if it does not already exist."""

    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :type_name)"),
        {"type_name": enum_type.name},
    ).scalar()

    if not exists:
        enum_type.create(bind, checkfirst=False)


def upgrade() -> None:  # noqa: D401
    """Install Spec workflow tables aligned with the new data model."""

    _backup_existing_tables()
    _drop_legacy_enums()

    _create_enum_if_missing(SPEC_WORKFLOW_RUN_STATUS)
    _create_enum_if_missing(SPEC_WORKFLOW_TASK_NAME)
    _create_enum_if_missing(SPEC_WORKFLOW_TASK_STATE)
    _create_enum_if_missing(WORKFLOW_CREDENTIAL_STATUS)
    _create_enum_if_missing(WORKFLOW_ARTIFACT_TYPE)

    op.create_table(
        "spec_workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("feature_key", sa.String(length=255), nullable=False),
        sa.Column(
            "requested_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("pull_request_url", sa.String(length=512), nullable=True),
        sa.Column("celery_chain_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            SPEC_WORKFLOW_RUN_STATUS,
            nullable=False,
            server_default=sa.text("'pending'::specworkflowrunstatus"),
        ),
        sa.Column(
            "current_task_name",
            SPEC_WORKFLOW_TASK_NAME,
            nullable=True,
        ),
        sa.Column("codex_task_id", sa.String(length=255), nullable=True),
        sa.Column("codex_logs_path", sa.String(length=1024), nullable=True),
        sa.Column("credential_audit_id", sa.Uuid(), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_spec_workflow_runs_feature_key",
        "spec_workflow_runs",
        ["feature_key"],
    )
    op.create_index(
        "ix_spec_workflow_runs_status",
        "spec_workflow_runs",
        ["status"],
    )
    op.create_index(
        "ix_spec_workflow_runs_requested_by",
        "spec_workflow_runs",
        ["requested_by_user_id"],
    )

    op.create_table(
        "spec_workflow_task_states",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_name",
            SPEC_WORKFLOW_TASK_NAME,
            nullable=False,
        ),
        sa.Column(
            "state",
            SPEC_WORKFLOW_TASK_STATE,
            nullable=False,
            server_default=sa.text("'waiting'::specworkflowtaskstate"),
        ),
        sa.Column(
            "message",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "artifact_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
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
            "task_name",
            "retry_count",
            "state",
            name="uq_spec_workflow_task_state_event",
        ),
    )

    op.create_index(
        "ix_spec_workflow_task_states_run_id",
        "spec_workflow_task_states",
        ["run_id"],
    )
    op.create_index(
        "ix_spec_workflow_task_states_task",
        "spec_workflow_task_states",
        ["task_name"],
    )

    op.create_table(
        "workflow_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_type",
            WORKFLOW_ARTIFACT_TYPE,
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("digest", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "run_id",
            "artifact_type",
            "path",
            name="uq_workflow_artifact_path",
        ),
    )

    op.create_index(
        "ix_workflow_artifacts_run_id",
        "workflow_artifacts",
        ["run_id"],
    )

    op.create_table(
        "workflow_credential_audits",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "codex_status",
            WORKFLOW_CREDENTIAL_STATUS,
            nullable=False,
            server_default=sa.text("'skipped'::workflowcredentialstatus"),
        ),
        sa.Column("codex_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("codex_message", sa.Text(), nullable=True),
        sa.Column(
            "github_status",
            WORKFLOW_CREDENTIAL_STATUS,
            nullable=False,
            server_default=sa.text("'skipped'::workflowcredentialstatus"),
        ),
        sa.Column("github_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("github_message", sa.Text(), nullable=True),
        sa.Column(
            "environment_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("run_id", name="uq_workflow_credential_audits_run"),
    )

    op.create_index(
        "ix_workflow_credential_audits_run",
        "workflow_credential_audits",
        ["run_id"],
    )

    op.create_foreign_key(
        "fk_spec_workflow_run_credential_audit",
        "spec_workflow_runs",
        "workflow_credential_audits",
        ["credential_audit_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_unique_constraint(
        "uq_spec_workflow_run_credential_audit",
        "spec_workflow_runs",
        ["credential_audit_id"],
    )


def downgrade() -> None:  # noqa: D401
    """Drop Spec workflow chain tables and restore legacy schema."""

    op.drop_constraint(
        "uq_spec_workflow_run_credential_audit",
        "spec_workflow_runs",
        type_="unique",
    )
    op.drop_constraint(
        "fk_spec_workflow_run_credential_audit",
        "spec_workflow_runs",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_workflow_credential_audits_run", table_name="workflow_credential_audits"
    )
    op.drop_table("workflow_credential_audits")

    op.drop_index("ix_workflow_artifacts_run_id", table_name="workflow_artifacts")
    op.drop_table("workflow_artifacts")

    op.drop_index(
        "ix_spec_workflow_task_states_task", table_name="spec_workflow_task_states"
    )
    op.drop_index(
        "ix_spec_workflow_task_states_run_id", table_name="spec_workflow_task_states"
    )
    op.drop_table("spec_workflow_task_states")

    op.drop_index("ix_spec_workflow_runs_requested_by", table_name="spec_workflow_runs")
    op.drop_index("ix_spec_workflow_runs_status", table_name="spec_workflow_runs")
    op.drop_index("ix_spec_workflow_runs_feature_key", table_name="spec_workflow_runs")
    op.drop_table("spec_workflow_runs")

    bind = op.get_bind()
    for enum_type in (
        SPEC_WORKFLOW_TASK_STATE,
        SPEC_WORKFLOW_TASK_NAME,
        WORKFLOW_ARTIFACT_TYPE,
        WORKFLOW_CREDENTIAL_STATUS,
        SPEC_WORKFLOW_RUN_STATUS,
    ):
        enum_type.drop(bind, checkfirst=True)

    # ------------------------------------------------------------------
    # Recreate the legacy workflow enums and tables that existed before
    # the Celery chain migration. This mirrors the schema installed by
    # historical migrations so that older code paths continue to work.
    # ------------------------------------------------------------------
    # Recreate legacy enums
    legacy_run_status = postgresql.ENUM(
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        name="specworkflowrunstatus",
        create_type=False,
    )
    legacy_run_phase = postgresql.ENUM(
        "discover",
        "submit",
        "apply",
        "publish",
        "complete",
        name="specworkflowrunphase",
        create_type=False,
    )
    legacy_task_status = postgresql.ENUM(
        "queued",
        "running",
        "succeeded",
        "failed",
        "skipped",
        name="specworkflowtaskstatus",
        create_type=False,
    )
    legacy_codex_status = postgresql.ENUM(
        "valid",
        "invalid",
        "expires_soon",
        name="workflowcodexcredentialstatus",
        create_type=False,
    )
    legacy_github_status = postgresql.ENUM(
        "valid",
        "invalid",
        "scope_missing",
        name="workflowgithubcredentialstatus",
        create_type=False,
    )
    legacy_preflight_status = postgresql.ENUM(
        "pending",
        "passed",
        "failed",
        "skipped",
        name="codexpreflightstatus",
        create_type=False,
    )
    legacy_artifact_type = postgresql.ENUM(
        "codex_logs",
        "codex_patch",
        "gh_push_log",
        "gh_pr_response",
        name="workflowartifacttype",
        create_type=False,
    )

    for enum_type in (
        legacy_run_status,
        legacy_run_phase,
        legacy_task_status,
        legacy_codex_status,
        legacy_github_status,
        legacy_preflight_status,
        legacy_artifact_type,
    ):
        _create_enum_if_missing(enum_type)

    op.create_table(
        "spec_workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("feature_key", sa.String(length=255), nullable=False),
        sa.Column("celery_chain_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            legacy_run_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "phase",
            legacy_run_phase,
            nullable=False,
            server_default="discover",
        ),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("codex_task_id", sa.String(length=255), nullable=True),
        sa.Column("codex_queue", sa.String(length=64), nullable=True),
        sa.Column("codex_volume", sa.String(length=64), nullable=True),
        sa.Column(
            "codex_preflight_status",
            legacy_preflight_status,
            nullable=True,
        ),
        sa.Column("codex_preflight_message", sa.Text(), nullable=True),
        sa.Column("codex_logs_path", sa.String(length=1024), nullable=True),
        sa.Column("codex_patch_path", sa.String(length=1024), nullable=True),
        sa.Column("artifacts_path", sa.String(length=512), nullable=True),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["codex_queue"],
            ["codex_worker_shards.queue_name"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["codex_volume"],
            ["codex_auth_volumes.name"],
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_spec_workflow_runs_feature_key",
        "spec_workflow_runs",
        ["feature_key"],
    )
    op.create_index(
        "ix_spec_workflow_runs_status",
        "spec_workflow_runs",
        ["status"],
    )
    op.create_index(
        "ix_spec_workflow_runs_created_by",
        "spec_workflow_runs",
        ["created_by"],
    )

    op.create_table(
        "spec_workflow_task_states",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "workflow_run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            legacy_task_status,
            nullable=False,
        ),
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "workflow_run_id",
            "task_name",
            "attempt",
            name="uq_spec_workflow_task_state_attempt",
        ),
    )

    op.create_index(
        "ix_spec_workflow_task_states_run_id",
        "spec_workflow_task_states",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_spec_workflow_task_states_failed",
        "spec_workflow_task_states",
        ["workflow_run_id"],
        postgresql_where=sa.text("status = 'failed'"),
    )

    op.create_table(
        "workflow_credential_audits",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "workflow_run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "codex_status",
            legacy_codex_status,
            nullable=False,
        ),
        sa.Column(
            "github_status",
            legacy_github_status,
            nullable=False,
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "workflow_run_id",
            name="uq_workflow_credential_audit_run",
        ),
    )

    op.create_table(
        "workflow_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "workflow_run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_type",
            legacy_artifact_type,
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workflow_run_id",
            "artifact_type",
            "path",
            name="uq_workflow_artifact_path",
        ),
    )

    op.create_index(
        "ix_workflow_artifacts_run_id",
        "workflow_artifacts",
        ["workflow_run_id"],
    )

    # ------------------------------------------------------------------
    # Reapply orchestrator-specific columns that were introduced by
    # earlier migrations. Keeping this logic here prevents future
    # downgrades from failing when those migrations are not rerun.
    # ------------------------------------------------------------------
    op.add_column(
        "spec_workflow_task_states",
        sa.Column("orchestrator_run_id", sa.Uuid(), nullable=True),
    )
    op.alter_column(
        "spec_workflow_task_states",
        "workflow_run_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column(
            "plan_step",
            postgresql.ENUM(name="orchestratorplanstep", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column(
            "plan_step_status",
            postgresql.ENUM(name="orchestratorplanstepstatus", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column(
            "celery_state",
            postgresql.ENUM(name="orchestratortaskstate", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column("message", sa.Text(), nullable=True),
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column(
            "artifact_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_task_states_orchestrator_run",
        "spec_workflow_task_states",
        "orchestrator_runs",
        ["orchestrator_run_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_spec_workflow_task_states_orchestrator_run_id",
        "spec_workflow_task_states",
        ["orchestrator_run_id"],
    )

    op.create_unique_constraint(
        "uq_orchestrator_task_state_attempt",
        "spec_workflow_task_states",
        ["orchestrator_run_id", "plan_step", "attempt"],
    )
