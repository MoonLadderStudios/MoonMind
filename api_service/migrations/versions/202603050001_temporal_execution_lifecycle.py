"""Add temporal execution lifecycle projection table."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202603050001"
down_revision: str | None = "202603010001"
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
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    workflow_type_enum = sa.Enum(
        "MoonMind.Run",
        "MoonMind.ManifestIngest",
        name="temporalworkflowtype",
    )
    workflow_state_enum = sa.Enum(
        "initializing",
        "planning",
        "executing",
        "awaiting_external",
        "finalizing",
        "succeeded",
        "failed",
        "canceled",
        name="moonmindworkflowstate",
    )
    close_status_enum = sa.Enum(
        "completed",
        "failed",
        "canceled",
        "terminated",
        "timed_out",
        "continued_as_new",
        name="temporalexecutionclosestatus",
    )

    _create_enum_if_postgres(workflow_type_enum)
    _create_enum_if_postgres(workflow_state_enum)
    _create_enum_if_postgres(close_status_enum)

    workflow_type_column = (
        postgresql.ENUM(name="temporalworkflowtype", create_type=False)
        if is_postgres
        else workflow_type_enum
    )
    workflow_state_column = (
        postgresql.ENUM(name="moonmindworkflowstate", create_type=False)
        if is_postgres
        else workflow_state_enum
    )
    close_status_column = (
        postgresql.ENUM(name="temporalexecutionclosestatus", create_type=False)
        if is_postgres
        else close_status_enum
    )

    op.create_table(
        "temporal_executions",
        sa.Column("workflow_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column(
            "namespace",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("'moonmind'"),
        ),
        sa.Column("workflow_type", workflow_type_column, nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=True),
        sa.Column(
            "state",
            workflow_state_column,
            nullable=False,
            server_default=sa.text("'initializing'"),
        ),
        sa.Column("close_status", close_status_column, nullable=True),
        sa.Column("entry", sa.String(length=16), nullable=False),
        sa.Column("search_attributes", _json_variant(), nullable=False),
        sa.Column("memo", _json_variant(), nullable=False),
        sa.Column("artifact_refs", _json_variant(), nullable=False),
        sa.Column("input_ref", sa.String(length=512), nullable=True),
        sa.Column("plan_ref", sa.String(length=512), nullable=True),
        sa.Column("manifest_ref", sa.String(length=512), nullable=True),
        sa.Column("parameters", _json_variant(), nullable=False),
        sa.Column("pending_parameters_patch", _json_variant(), nullable=True),
        sa.Column(
            "paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "awaiting_external",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "step_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "wait_cycle_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "rerun_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("create_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("last_update_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("last_update_response", _json_variant(), nullable=True),
        sa.Column(
            "started_at",
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
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("workflow_id", name="pk_temporal_executions"),
        sa.UniqueConstraint(
            "create_idempotency_key",
            "owner_id",
            "workflow_type",
            name="uq_temporal_executions_create_idempotency_owner_type",
        ),
    )

    op.create_index(
        "ix_temporal_executions_state_updated_at",
        "temporal_executions",
        ["state", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_executions_owner_state",
        "temporal_executions",
        ["owner_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_executions_type_updated_at",
        "temporal_executions",
        ["workflow_type", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_temporal_executions_type_updated_at", table_name="temporal_executions"
    )
    op.drop_index(
        "ix_temporal_executions_owner_state", table_name="temporal_executions"
    )
    op.drop_index(
        "ix_temporal_executions_state_updated_at", table_name="temporal_executions"
    )
    op.drop_table("temporal_executions")

    close_status_enum = sa.Enum(name="temporalexecutionclosestatus")
    workflow_state_enum = sa.Enum(name="moonmindworkflowstate")
    workflow_type_enum = sa.Enum(name="temporalworkflowtype")

    _drop_enum_if_postgres(close_status_enum)
    _drop_enum_if_postgres(workflow_state_enum)
    _drop_enum_if_postgres(workflow_type_enum)
