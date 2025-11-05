"""Create tables for Spec Kit workflow runs and task state tracking."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "add_spec_workflow_tables"
down_revision: Union[str, None] = "cb32e6509d1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SPEC_WORKFLOW_RUN_STATUS = postgresql.ENUM(
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    name="specworkflowrunstatus",
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
    name="workflowartifacttype",
    create_type=False,
)


def upgrade() -> None:  # noqa: D401
    """Create workflow tables and supporting enums."""

    def _create_enum_if_missing(enum_type: postgresql.ENUM) -> None:
        literal_values = ", ".join(f"'{value}'" for value in enum_type.enums)
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_type WHERE typname = '{enum_type.name}'
                ) THEN
                    CREATE TYPE {enum_type.name} AS ENUM ({literal_values});
                END IF;
            END $$;
            """
        )

    _create_enum_if_missing(SPEC_WORKFLOW_RUN_STATUS)
    _create_enum_if_missing(SPEC_WORKFLOW_RUN_PHASE)
    _create_enum_if_missing(SPEC_WORKFLOW_TASK_STATUS)
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
            server_default="pending",
        ),
        sa.Column(
            "phase",
            SPEC_WORKFLOW_RUN_PHASE,
            nullable=False,
            server_default="discover",
        ),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("codex_task_id", sa.String(length=255), nullable=True),
        sa.Column("artifacts_path", sa.String(length=512), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
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
        sa.Column("status", SPEC_WORKFLOW_TASK_STATUS, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
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
        sa.Column("artifact_type", WORKFLOW_ARTIFACT_TYPE, nullable=False),
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


def downgrade() -> None:  # noqa: D401
    """Drop workflow tables and supporting enums."""

    op.drop_index("ix_workflow_artifacts_run_id", table_name="workflow_artifacts")
    op.drop_table("workflow_artifacts")

    op.drop_table("workflow_credential_audits")

    op.drop_index(
        "ix_spec_workflow_task_states_failed",
        table_name="spec_workflow_task_states",
    )
    op.drop_index(
        "ix_spec_workflow_task_states_run_id",
        table_name="spec_workflow_task_states",
    )
    op.drop_table("spec_workflow_task_states")

    op.drop_index("ix_spec_workflow_runs_created_by", table_name="spec_workflow_runs")
    op.drop_index("ix_spec_workflow_runs_status", table_name="spec_workflow_runs")
    op.drop_index("ix_spec_workflow_runs_feature_key", table_name="spec_workflow_runs")
    op.drop_table("spec_workflow_runs")

    bind = op.get_bind()
    WORKFLOW_ARTIFACT_TYPE.drop(bind, checkfirst=True)
    WORKFLOW_GITHUB_CREDENTIAL_STATUS.drop(bind, checkfirst=True)
    WORKFLOW_CODEX_CREDENTIAL_STATUS.drop(bind, checkfirst=True)
    SPEC_WORKFLOW_TASK_STATUS.drop(bind, checkfirst=True)
    SPEC_WORKFLOW_RUN_PHASE.drop(bind, checkfirst=True)
    SPEC_WORKFLOW_RUN_STATUS.drop(bind, checkfirst=True)
