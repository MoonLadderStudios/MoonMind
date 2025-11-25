"""Rebuild Spec workflow tables for Celery chain orchestration."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202403150001"
down_revision = "202402150001"

__all__ = [
    "revision",
    "down_revision",
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

SPEC_WORKFLOW_RUN_PHASE = postgresql.ENUM(
    "discover",
    "submit",
    "apply",
    "publish",
    "complete",
    name="specworkflowrunphase",
    create_type=False,
)

SPEC_WORKFLOW_TASK_STATUS = postgresql.ENUM(
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    name="specworkflowtaskstatus",
    create_type=False,
)

CODEX_PREFLIGHT_STATUS = postgresql.ENUM(
    "pending",
    "passed",
    "failed",
    "skipped",
    name="codexpreflightstatus",
    create_type=False,
)

WORKFLOW_CODEX_CREDENTIAL_STATUS = postgresql.ENUM(
    "valid",
    "invalid",
    "expires_soon",
    name="workflowcodexcredentialstatus",
    create_type=False,
)

WORKFLOW_GITHUB_CREDENTIAL_STATUS = postgresql.ENUM(
    "valid",
    "invalid",
    "scope_missing",
    name="workflowgithubcredentialstatus",
    create_type=False,
)

WORKFLOW_ARTIFACT_TYPE = postgresql.ENUM(
    "codex_logs",
    "codex_patch",
    "gh_push_log",
    "gh_pr_response",
    "apply_output",
    "pr_payload",
    "retry_context",
    name="workflowartifacttype",
    create_type=False,
)

SPEC_UPDATED_AT_TRIGGER_FUNCTION = "spec_workflow_set_updated_at"

LEGACY_WORKFLOW_TABLES: tuple[str, ...] = (
    "workflow_artifacts",
    "workflow_credential_audits",
    "spec_workflow_task_states",
    "spec_workflow_runs",
)

LEGACY_ENUM_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "legacy_spec_workflow_runs": (
        ("status", "specworkflowrunstatus"),
        ("phase", "specworkflowrunphase"),
        ("codex_preflight_status", "codexpreflightstatus"),
    ),
    "legacy_spec_workflow_task_states": (("status", "specworkflowtaskstatus"),),
    "legacy_workflow_credential_audits": (
        ("codex_status", "workflowcodexcredentialstatus"),
        ("github_status", "workflowgithubcredentialstatus"),
    ),
    "legacy_workflow_artifacts": (("artifact_type", "workflowartifacttype"),),
}

# Keep enum drop order stable by deduplicating with an order-preserving dict.
LEGACY_ENUM_TYPES: tuple[str, ...] = tuple(
    dict.fromkeys(
        enum_type
        for columns in LEGACY_ENUM_COLUMNS.values()
        for _, enum_type in columns
    )
)

LEGACY_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "spec_workflow_runs": (
        "id",
        "feature_key",
        "celery_chain_id",
        "status",
        "phase",
        "branch_name",
        "pr_url",
        "codex_task_id",
        "codex_queue",
        "codex_volume",
        "codex_preflight_status",
        "codex_preflight_message",
        "codex_logs_path",
        "codex_patch_path",
        "artifacts_path",
        "created_by",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ),
    "spec_workflow_task_states": (
        "id",
        "workflow_run_id",
        "task_name",
        "status",
        "attempt",
        "payload",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
        "orchestrator_run_id",
        "plan_step",
        "plan_step_status",
        "celery_state",
        "celery_task_id",
        "message",
        "artifact_refs",
    ),
    "workflow_credential_audits": (
        "id",
        "workflow_run_id",
        "codex_status",
        "github_status",
        "checked_at",
        "notes",
    ),
    "workflow_artifacts": (
        "id",
        "workflow_run_id",
        "artifact_type",
        "path",
        "created_at",
    ),
}


def _backup_existing_tables() -> None:
    """Rename legacy Spec workflow tables to preserve their contents."""

    for table_name in LEGACY_WORKFLOW_TABLES:
        backup_name = f"legacy_{table_name}"
        quoted_table = sa.sql.elements.quoted_name(table_name, quote=True)
        quoted_backup = sa.sql.elements.quoted_name(backup_name, quote=True)

        op.execute(f"DROP TABLE IF EXISTS {quoted_backup} CASCADE")
        op.execute(f"ALTER TABLE IF EXISTS {quoted_table} RENAME TO {quoted_backup}")


def _copy_legacy_table_data(target_table: str) -> None:
    """Copy data from a legacy backup table into the new schema."""

    columns = LEGACY_TABLE_COLUMNS.get(target_table)
    if not columns:
        return

    source_table = f"legacy_{target_table}"
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT to_regclass(:name) IS NOT NULL"),
        {"name": source_table},
    ).scalar()
    if not exists:
        return

    column_csv = ", ".join(
        str(sa.sql.elements.quoted_name(column, quote=True)) for column in columns
    )
    quoted_source = sa.sql.elements.quoted_name(source_table, quote=True)
    quoted_target = sa.sql.elements.quoted_name(target_table, quote=True)

    bind.execute(
        sa.text(
            f"INSERT INTO {quoted_target} ({column_csv}) "
            f"SELECT {column_csv} FROM {quoted_source}"
        )
    )


def _drop_legacy_backups() -> None:
    """Remove legacy_* tables after their contents have been restored."""

    for table_name in LEGACY_WORKFLOW_TABLES:
        quoted_backup = sa.sql.elements.quoted_name(f"legacy_{table_name}", quote=True)
        op.execute(f"DROP TABLE IF EXISTS {quoted_backup} CASCADE")


def _detach_legacy_tables_from_enums() -> None:
    """Cast legacy enum columns to text so types can be dropped safely."""

    for table_name, columns in LEGACY_ENUM_COLUMNS.items():
        quoted_table = sa.sql.elements.quoted_name(table_name, quote=True)
        for column, _ in columns:
            quoted_column = sa.sql.elements.quoted_name(column, quote=True)
            op.execute(
                f"ALTER TABLE IF EXISTS {quoted_table} "
                f"ALTER COLUMN {quoted_column} TYPE TEXT "
                f"USING {quoted_column}::text"
            )


def _drop_legacy_enums() -> None:
    """Drop enums installed by prior Spec workflow migrations."""

    for type_name in LEGACY_ENUM_TYPES:
        quoted_type = sa.sql.elements.quoted_name(type_name, quote=True)
        op.execute(f"DROP TYPE IF EXISTS {quoted_type}")


def _ensure_updated_at_function() -> None:
    """Install the trigger function used to auto-update timestamps."""

    quoted_name = sa.sql.elements.quoted_name(
        SPEC_UPDATED_AT_TRIGGER_FUNCTION, quote=True
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {quoted_name}()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def _create_updated_at_trigger(table_name: str) -> None:
    """Attach an auto-updating trigger to the given table."""

    _ensure_updated_at_function()
    quoted_table = sa.sql.elements.quoted_name(table_name, quote=True)
    trigger_name = sa.sql.elements.quoted_name(
        f"trg_{table_name}_updated_at", quote=True
    )
    function_name = sa.sql.elements.quoted_name(
        SPEC_UPDATED_AT_TRIGGER_FUNCTION, quote=True
    )

    op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {quoted_table}")
    op.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE ON {quoted_table}
        FOR EACH ROW EXECUTE FUNCTION {function_name}();
        """
    )


def _drop_updated_at_function() -> None:
    """Remove the helper trigger function if this migration is downgraded."""

    quoted_name = sa.sql.elements.quoted_name(
        SPEC_UPDATED_AT_TRIGGER_FUNCTION, quote=True
    )
    op.execute(f"DROP FUNCTION IF EXISTS {quoted_name}()")


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
    _detach_legacy_tables_from_enums()
    _drop_legacy_enums()

    _create_enum_if_missing(SPEC_WORKFLOW_RUN_STATUS)
    _create_enum_if_missing(SPEC_WORKFLOW_RUN_PHASE)
    _create_enum_if_missing(SPEC_WORKFLOW_TASK_STATUS)
    _create_enum_if_missing(SPEC_WORKFLOW_TASK_NAME)
    _create_enum_if_missing(CODEX_PREFLIGHT_STATUS)
    _create_enum_if_missing(WORKFLOW_CODEX_CREDENTIAL_STATUS)
    _create_enum_if_missing(WORKFLOW_GITHUB_CREDENTIAL_STATUS)
    _create_enum_if_missing(WORKFLOW_ARTIFACT_TYPE)

    op.create_table(
        "spec_workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("feature_key", sa.String(length=255), nullable=False),
        sa.Column("celery_chain_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            SPEC_WORKFLOW_RUN_STATUS,
            nullable=False,
            server_default=sa.text("'pending'::specworkflowrunstatus"),
        ),
        sa.Column(
            "phase",
            SPEC_WORKFLOW_RUN_PHASE,
            nullable=False,
            server_default=sa.text("'discover'::specworkflowrunphase"),
        ),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("repository", sa.String(length=255), nullable=True),
        sa.Column(
            "requested_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("codex_task_id", sa.String(length=255), nullable=True),
        sa.Column("codex_queue", sa.String(length=64), nullable=True),
        sa.Column("codex_volume", sa.String(length=64), nullable=True),
        sa.Column(
            "codex_preflight_status",
            CODEX_PREFLIGHT_STATUS,
            nullable=True,
        ),
        sa.Column("codex_preflight_message", sa.Text(), nullable=True),
        sa.Column("codex_logs_path", sa.String(length=1024), nullable=True),
        sa.Column("codex_patch_path", sa.String(length=1024), nullable=True),
        sa.Column("artifacts_path", sa.String(length=512), nullable=True),
        sa.Column(
            "current_task_name",
            SPEC_WORKFLOW_TASK_NAME,
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        "ix_spec_workflow_runs_requested_by",
        "spec_workflow_runs",
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_spec_workflow_runs_created_by",
        "spec_workflow_runs",
        ["created_by"],
    )

    _copy_legacy_table_data("spec_workflow_runs")

    _create_updated_at_trigger("spec_workflow_runs")

    op.create_table(
        "spec_workflow_task_states",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "workflow_run_id",
            sa.Uuid(),
            sa.ForeignKey("spec_workflow_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "orchestrator_run_id",
            sa.Uuid(),
            sa.ForeignKey("orchestrator_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("task_name", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            SPEC_WORKFLOW_TASK_STATUS,
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
        sa.Column(
            "plan_step",
            postgresql.ENUM(name="orchestratorplanstep", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "plan_step_status",
            postgresql.ENUM(name="orchestratorplanstepstatus", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "celery_state",
            postgresql.ENUM(name="orchestratortaskstate", create_type=False),
            nullable=True,
        ),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "artifact_refs",
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
        sa.UniqueConstraint(
            "orchestrator_run_id",
            "plan_step",
            "attempt",
            name="uq_orchestrator_task_state_attempt",
        ),
        sa.CheckConstraint(
            "(workflow_run_id IS NOT NULL AND orchestrator_run_id IS NULL) OR "
            "(workflow_run_id IS NULL AND orchestrator_run_id IS NOT NULL)",
            name="ck_spec_workflow_task_state_run_id_exclusive",
        ),
        sa.CheckConstraint(
            "(orchestrator_run_id IS NULL) OR (plan_step IS NOT NULL)",
            name="ck_spec_workflow_task_state_orchestrator_plan_step",
        ),
        sa.CheckConstraint(
            "(workflow_run_id IS NULL) OR (task_name IS NOT NULL)",
            name="ck_spec_workflow_task_state_task_name_required",
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
    op.create_index(
        "ix_spec_workflow_task_states_orchestrator_run_id",
        "spec_workflow_task_states",
        ["orchestrator_run_id"],
    )

    _create_updated_at_trigger("spec_workflow_task_states")

    _copy_legacy_table_data("spec_workflow_task_states")

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

    _copy_legacy_table_data("workflow_artifacts")

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
            WORKFLOW_CODEX_CREDENTIAL_STATUS,
            nullable=False,
        ),
        sa.Column(
            "github_status",
            WORKFLOW_GITHUB_CREDENTIAL_STATUS,
            nullable=False,
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("codex_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("codex_message", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "workflow_run_id",
            name="uq_workflow_credential_audit_run",
        ),
    )

    op.create_index(
        "ix_workflow_credential_audits_run",
        "workflow_credential_audits",
        ["workflow_run_id"],
    )

    _copy_legacy_table_data("workflow_credential_audits")


def downgrade() -> None:  # noqa: D401
    """Drop Spec workflow chain tables and restore legacy schema."""

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

    _drop_updated_at_function()

    bind = op.get_bind()
    for enum_type in (
        SPEC_WORKFLOW_TASK_NAME,
        WORKFLOW_ARTIFACT_TYPE,
        SPEC_WORKFLOW_TASK_STATUS,
        SPEC_WORKFLOW_RUN_PHASE,
        SPEC_WORKFLOW_RUN_STATUS,
        WORKFLOW_CODEX_CREDENTIAL_STATUS,
        WORKFLOW_GITHUB_CREDENTIAL_STATUS,
        CODEX_PREFLIGHT_STATUS,
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

    _copy_legacy_table_data("spec_workflow_runs")

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

    _copy_legacy_table_data("workflow_credential_audits")

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

    _copy_legacy_table_data("workflow_artifacts")

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

    _copy_legacy_table_data("spec_workflow_task_states")

    _drop_legacy_backups()
