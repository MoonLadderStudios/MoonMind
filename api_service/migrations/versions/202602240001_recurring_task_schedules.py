"""Add recurring task definitions and run history tables."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602240001"
down_revision: Union[str, None] = "202602220002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _create_enum_if_postgres(enum_type: sa.Enum) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        enum_type.create(bind, checkfirst=True)


def _drop_enum_if_postgres(enum_type: sa.Enum) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        enum_type.drop(bind, checkfirst=True)


def upgrade() -> None:
    schedule_type_enum = sa.Enum(
        "cron",
        name="recurringtaskscheduletype",
    )
    scope_type_enum = sa.Enum(
        "personal",
        "team",
        "global",
        name="recurringtaskscopetype",
    )
    run_outcome_enum = sa.Enum(
        "pending_dispatch",
        "enqueued",
        "skipped",
        "dispatch_error",
        name="recurringtaskrunoutcome",
    )
    run_trigger_enum = sa.Enum(
        "schedule",
        "manual",
        name="recurringtaskruntrigger",
    )

    _create_enum_if_postgres(schedule_type_enum)
    _create_enum_if_postgres(scope_type_enum)
    _create_enum_if_postgres(run_outcome_enum)
    _create_enum_if_postgres(run_trigger_enum)

    op.create_table(
        "recurring_task_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "schedule_type",
            postgresql.ENUM(name="recurringtaskscheduletype", create_type=False),
            nullable=False,
            server_default=sa.text("'cron'"),
        ),
        sa.Column("cron", sa.String(length=128), nullable=False),
        sa.Column("timezone", sa.String(length=128), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_dispatch_status", sa.String(length=32), nullable=True),
        sa.Column("last_dispatch_error", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "scope_type",
            postgresql.ENUM(name="recurringtaskscopetype", create_type=False),
            nullable=False,
            server_default=sa.text("'personal'"),
        ),
        sa.Column("scope_ref", sa.String(length=255), nullable=True),
        sa.Column("target", _json_variant(), nullable=False),
        sa.Column("policy", _json_variant(), nullable=False),
        sa.Column(
            "version", sa.BigInteger(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user.id"],
            name="fk_recurring_task_definitions_owner_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recurring_task_definitions"),
    )
    op.create_index(
        "ix_recurring_task_definitions_enabled_next_run_at",
        "recurring_task_definitions",
        ["enabled", "next_run_at"],
        unique=False,
    )
    op.create_index(
        "ix_recurring_task_definitions_owner_enabled",
        "recurring_task_definitions",
        ["owner_user_id", "enabled"],
        unique=False,
    )

    op.create_table(
        "recurring_task_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "trigger",
            postgresql.ENUM(name="recurringtaskruntrigger", create_type=False),
            nullable=False,
            server_default=sa.text("'schedule'"),
        ),
        sa.Column(
            "outcome",
            postgresql.ENUM(name="recurringtaskrunoutcome", create_type=False),
            nullable=False,
            server_default=sa.text("'pending_dispatch'"),
        ),
        sa.Column(
            "dispatch_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queue_job_id", sa.Uuid(), nullable=True),
        sa.Column("queue_job_type", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["recurring_task_definitions.id"],
            name="fk_recurring_task_runs_definition_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recurring_task_runs"),
        sa.UniqueConstraint(
            "definition_id",
            "scheduled_for",
            name="uq_recurring_task_runs_definition_scheduled_for",
        ),
    )
    op.create_index(
        "ix_recurring_task_runs_definition_created_at",
        "recurring_task_runs",
        ["definition_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_recurring_task_runs_outcome_dispatch_after",
        "recurring_task_runs",
        ["outcome", "dispatch_after"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recurring_task_runs_outcome_dispatch_after",
        table_name="recurring_task_runs",
    )
    op.drop_index(
        "ix_recurring_task_runs_definition_created_at",
        table_name="recurring_task_runs",
    )
    op.drop_table("recurring_task_runs")

    op.drop_index(
        "ix_recurring_task_definitions_owner_enabled",
        table_name="recurring_task_definitions",
    )
    op.drop_index(
        "ix_recurring_task_definitions_enabled_next_run_at",
        table_name="recurring_task_definitions",
    )
    op.drop_table("recurring_task_definitions")

    run_trigger_enum = sa.Enum(name="recurringtaskruntrigger")
    run_outcome_enum = sa.Enum(name="recurringtaskrunoutcome")
    scope_type_enum = sa.Enum(name="recurringtaskscopetype")
    schedule_type_enum = sa.Enum(name="recurringtaskscheduletype")

    _drop_enum_if_postgres(run_trigger_enum)
    _drop_enum_if_postgres(run_outcome_enum)
    _drop_enum_if_postgres(scope_type_enum)
    _drop_enum_if_postgres(schedule_type_enum)
