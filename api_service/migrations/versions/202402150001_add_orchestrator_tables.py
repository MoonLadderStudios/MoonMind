"""Add Orchestrator tables and extend task state tracking."""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202402150001"  # noqa: F841
down_revision = "f6f3b3cdf5e2"  # noqa: F841


ORCHESTRATOR_RUN_STATUS = postgresql.ENUM(
    "pending",
    "running",
    "awaiting_approval",
    "succeeded",
    "failed",
    "rolled_back",
    name="orchestratorrunstatus",
    create_type=False,
)

ORCHESTRATOR_PLAN_STEP = postgresql.ENUM(
    "analyze",
    "patch",
    "build",
    "restart",
    "verify",
    "rollback",
    name="orchestratorplanstep",
    create_type=False,
)

ORCHESTRATOR_PLAN_STEP_STATUS = postgresql.ENUM(
    "pending",
    "in_progress",
    "succeeded",
    "failed",
    "skipped",
    name="orchestratorplanstepstatus",
    create_type=False,
)

ORCHESTRATOR_PLAN_ORIGIN = postgresql.ENUM(
    "operator",
    "llm",
    "system",
    name="orchestratorplanorigin",
    create_type=False,
)

ORCHESTRATOR_APPROVAL_REQUIREMENT = postgresql.ENUM(
    "none",
    "pre-run",
    "pre-verify",
    name="orchestratorapprovalrequirement",
    create_type=False,
)

ORCHESTRATOR_RUN_ARTIFACT_TYPE = postgresql.ENUM(
    "patch_diff",
    "build_log",
    "verify_log",
    "rollback_log",
    "metrics",
    "plan_snapshot",
    name="orchestratorrunartifacttype",
    create_type=False,
)

ORCHESTRATOR_RUN_PRIORITY = postgresql.ENUM(
    "normal",
    "high",
    name="orchestratorrunpriority",
    create_type=False,
)

ORCHESTRATOR_TASK_STATE = postgresql.ENUM(
    "PENDING",
    "STARTED",
    "RETRY",
    "SUCCESS",
    "FAILURE",
    name="orchestratortaskstate",
    create_type=False,
)


_SAFE_ENUM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_ENUM_LITERAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _create_enum_if_missing(enum_type: postgresql.ENUM) -> None:
    """Create a PostgreSQL enum type only if it does not already exist."""

    if not _SAFE_ENUM_NAME.match(enum_type.name or ""):
        raise ValueError(f"Unsafe enum name: {enum_type.name}")
    for value in enum_type.enums:
        if not _SAFE_ENUM_LITERAL.match(value):
            raise ValueError(f"Unsafe enum literal: {value}")

    literal_values = ", ".join(
        f"'{value.replace("'", "''")}'" for value in enum_type.enums
    )
    type_name_literal = enum_type.name.replace("'", "''")
    quoted_type_name = sa.sql.elements.quoted_name(enum_type.name, quote=True)

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = '{type_name_literal}'
            ) THEN
                CREATE TYPE {quoted_type_name} AS ENUM ({literal_values});
            END IF;
        END $$;
        """
    )


def upgrade() -> None:  # noqa: D401
    """Create orchestrator tables and extend task state tracking."""

    _create_enum_if_missing(ORCHESTRATOR_RUN_STATUS)
    _create_enum_if_missing(ORCHESTRATOR_PLAN_STEP)
    _create_enum_if_missing(ORCHESTRATOR_PLAN_STEP_STATUS)
    _create_enum_if_missing(ORCHESTRATOR_PLAN_ORIGIN)
    _create_enum_if_missing(ORCHESTRATOR_APPROVAL_REQUIREMENT)
    _create_enum_if_missing(ORCHESTRATOR_RUN_ARTIFACT_TYPE)
    _create_enum_if_missing(ORCHESTRATOR_RUN_PRIORITY)
    _create_enum_if_missing(ORCHESTRATOR_TASK_STATE)

    op.create_table(
        "approval_gates",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("service_name", sa.String(length=255), nullable=False),
        sa.Column(
            "requirement",
            ORCHESTRATOR_APPROVAL_REQUIREMENT,
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "approver_roles",
            postgresql.ARRAY(sa.String(length=128)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("valid_for_minutes", sa.Integer(), nullable=False),
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
        sa.UniqueConstraint(
            "service_name",
            name="uq_approval_gates_service_name",
        ),
        sa.CheckConstraint(
            "valid_for_minutes >= 5",
            name="ck_approval_gates_min_duration",
        ),
        sa.CheckConstraint(
            "requirement = 'none' OR COALESCE(array_length(approver_roles, 1), 0) > 0",
            name="ck_approval_gates_roles_present",
        ),
    )

    op.create_table(
        "orchestrator_action_plans",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "service_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "generated_by",
            ORCHESTRATOR_PLAN_ORIGIN,
            nullable=False,
            server_default="system",
        ),
    )

    op.create_table(
        "orchestrator_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("target_service", sa.String(length=255), nullable=False),
        sa.Column(
            "priority",
            ORCHESTRATOR_RUN_PRIORITY,
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "status",
            ORCHESTRATOR_RUN_STATUS,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_token", sa.LargeBinary(), nullable=True),
        sa.Column(
            "metrics_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("artifact_root", sa.String(length=1024), nullable=True),
        sa.Column("action_plan_id", sa.Uuid(), nullable=False),
        sa.Column("approval_gate_id", sa.Uuid(), nullable=True),
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
            ["action_plan_id"],
            ["orchestrator_action_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_gate_id"],
            ["approval_gates.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_orchestrator_runs_timestamps",
        ),
    )

    op.create_index(
        "ix_orchestrator_runs_status",
        "orchestrator_runs",
        ["status"],
    )
    op.create_index(
        "ix_orchestrator_runs_target_service",
        "orchestrator_runs",
        ["target_service"],
    )

    op.create_table(
        "orchestrator_run_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "artifact_type",
            ORCHESTRATOR_RUN_ARTIFACT_TYPE,
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["orchestrator_runs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id",
            "artifact_type",
            "path",
            name="uq_orchestrator_artifact_path",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_orchestrator_artifacts_size_non_negative",
        ),
    )

    op.create_index(
        "ix_orchestrator_run_artifacts_run_id",
        "orchestrator_run_artifacts",
        ["run_id"],
    )

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
            ORCHESTRATOR_PLAN_STEP,
            nullable=True,
        ),
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column(
            "plan_step_status",
            ORCHESTRATOR_PLAN_STEP_STATUS,
            nullable=True,
        ),
    )
    op.add_column(
        "spec_workflow_task_states",
        sa.Column(
            "celery_state",
            ORCHESTRATOR_TASK_STATE,
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


def downgrade() -> None:  # noqa: D401
    """Drop orchestrator tables and task state extensions."""

    op.drop_constraint(
        "uq_orchestrator_task_state_attempt",
        "spec_workflow_task_states",
        type_="unique",
    )
    op.drop_index(
        "ix_spec_workflow_task_states_orchestrator_run_id",
        table_name="spec_workflow_task_states",
    )
    op.drop_constraint(
        "fk_task_states_orchestrator_run",
        "spec_workflow_task_states",
        type_="foreignkey",
    )
    op.drop_column("spec_workflow_task_states", "artifact_refs")
    op.drop_column("spec_workflow_task_states", "message")
    op.drop_column("spec_workflow_task_states", "celery_task_id")
    op.drop_column("spec_workflow_task_states", "celery_state")
    op.drop_column("spec_workflow_task_states", "plan_step_status")
    op.drop_column("spec_workflow_task_states", "plan_step")
    op.drop_column("spec_workflow_task_states", "orchestrator_run_id")
    op.alter_column(
        "spec_workflow_task_states",
        "workflow_run_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )

    op.drop_index(
        "ix_orchestrator_run_artifacts_run_id",
        table_name="orchestrator_run_artifacts",
    )
    op.drop_table("orchestrator_run_artifacts")

    op.drop_index("ix_orchestrator_runs_target_service", table_name="orchestrator_runs")
    op.drop_index("ix_orchestrator_runs_status", table_name="orchestrator_runs")
    op.drop_table("orchestrator_runs")

    op.drop_table("orchestrator_action_plans")
    op.drop_table("approval_gates")

    bind = op.get_bind()
    ORCHESTRATOR_RUN_ARTIFACT_TYPE.drop(bind, checkfirst=True)
    ORCHESTRATOR_RUN_PRIORITY.drop(bind, checkfirst=True)
    ORCHESTRATOR_RUN_STATUS.drop(bind, checkfirst=True)
    ORCHESTRATOR_PLAN_STEP.drop(bind, checkfirst=True)
    ORCHESTRATOR_PLAN_STEP_STATUS.drop(bind, checkfirst=True)
    ORCHESTRATOR_PLAN_ORIGIN.drop(bind, checkfirst=True)
    ORCHESTRATOR_APPROVAL_REQUIREMENT.drop(bind, checkfirst=True)
    ORCHESTRATOR_TASK_STATE.drop(bind, checkfirst=True)
