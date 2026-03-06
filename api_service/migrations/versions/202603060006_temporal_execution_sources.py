"""Add canonical Temporal execution source table."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202603060006"
down_revision: str | None = "202603060005"
__all__: Sequence[str] = ("revision", "down_revision")


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    workflow_type_column = (
        postgresql.ENUM(name="temporalworkflowtype", create_type=False)
        if is_postgres
        else sa.Enum("MoonMind.Run", "MoonMind.ManifestIngest")
    )
    workflow_state_column = (
        postgresql.ENUM(name="moonmindworkflowstate", create_type=False)
        if is_postgres
        else sa.Enum(
            "initializing",
            "planning",
            "executing",
            "awaiting_external",
            "finalizing",
            "succeeded",
            "failed",
            "canceled",
        )
    )
    close_status_column = (
        postgresql.ENUM(name="temporalexecutionclosestatus", create_type=False)
        if is_postgres
        else sa.Enum(
            "completed",
            "failed",
            "canceled",
            "terminated",
            "timed_out",
            "continued_as_new",
        )
    )
    owner_type_column = (
        postgresql.ENUM(name="temporalexecutionownertype", create_type=False)
        if is_postgres
        else sa.Enum("user", "system", "service")
    )

    op.create_table(
        "temporal_execution_sources",
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
            "owner_type",
            owner_type_column,
            nullable=False,
            server_default=sa.text("'user'"),
        ),
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
        sa.PrimaryKeyConstraint("workflow_id", name="pk_temporal_execution_sources"),
        sa.UniqueConstraint(
            "create_idempotency_key",
            "owner_id",
            "owner_type",
            "workflow_type",
            name="uq_temporal_execution_sources_create_idempotency_owner_type",
        ),
    )

    op.create_index(
        "ix_temporal_execution_sources_state_updated_at",
        "temporal_execution_sources",
        ["state", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_execution_sources_owner_state",
        "temporal_execution_sources",
        ["owner_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_temporal_execution_sources_type_updated_at",
        "temporal_execution_sources",
        ["workflow_type", "updated_at"],
        unique=False,
    )

    temporal_executions = sa.table(
        "temporal_executions",
        sa.column("workflow_id", sa.String(length=64)),
        sa.column("run_id", sa.String(length=64)),
        sa.column("namespace", sa.String(length=128)),
        sa.column("workflow_type", sa.String(length=64)),
        sa.column("owner_id", sa.String(length=64)),
        sa.column("owner_type", sa.String(length=16)),
        sa.column("state", sa.String(length=64)),
        sa.column("close_status", sa.String(length=32)),
        sa.column("entry", sa.String(length=16)),
        sa.column("search_attributes", sa.JSON()),
        sa.column("memo", sa.JSON()),
        sa.column("artifact_refs", sa.JSON()),
        sa.column("input_ref", sa.String(length=512)),
        sa.column("plan_ref", sa.String(length=512)),
        sa.column("manifest_ref", sa.String(length=512)),
        sa.column("parameters", sa.JSON()),
        sa.column("pending_parameters_patch", sa.JSON()),
        sa.column("paused", sa.Boolean()),
        sa.column("awaiting_external", sa.Boolean()),
        sa.column("step_count", sa.Integer()),
        sa.column("wait_cycle_count", sa.Integer()),
        sa.column("rerun_count", sa.Integer()),
        sa.column("create_idempotency_key", sa.String(length=128)),
        sa.column("last_update_idempotency_key", sa.String(length=128)),
        sa.column("last_update_response", sa.JSON()),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("closed_at", sa.DateTime(timezone=True)),
    )
    temporal_execution_sources = sa.table(
        "temporal_execution_sources",
        sa.column("workflow_id", sa.String(length=64)),
        sa.column("run_id", sa.String(length=64)),
        sa.column("namespace", sa.String(length=128)),
        sa.column("workflow_type", sa.String(length=64)),
        sa.column("owner_id", sa.String(length=64)),
        sa.column("owner_type", sa.String(length=16)),
        sa.column("state", sa.String(length=64)),
        sa.column("close_status", sa.String(length=32)),
        sa.column("entry", sa.String(length=16)),
        sa.column("search_attributes", sa.JSON()),
        sa.column("memo", sa.JSON()),
        sa.column("artifact_refs", sa.JSON()),
        sa.column("input_ref", sa.String(length=512)),
        sa.column("plan_ref", sa.String(length=512)),
        sa.column("manifest_ref", sa.String(length=512)),
        sa.column("parameters", sa.JSON()),
        sa.column("pending_parameters_patch", sa.JSON()),
        sa.column("paused", sa.Boolean()),
        sa.column("awaiting_external", sa.Boolean()),
        sa.column("step_count", sa.Integer()),
        sa.column("wait_cycle_count", sa.Integer()),
        sa.column("rerun_count", sa.Integer()),
        sa.column("create_idempotency_key", sa.String(length=128)),
        sa.column("last_update_idempotency_key", sa.String(length=128)),
        sa.column("last_update_response", sa.JSON()),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("closed_at", sa.DateTime(timezone=True)),
    )

    bind.execute(
        temporal_execution_sources.insert().from_select(
            [
                "workflow_id",
                "run_id",
                "namespace",
                "workflow_type",
                "owner_id",
                "owner_type",
                "state",
                "close_status",
                "entry",
                "search_attributes",
                "memo",
                "artifact_refs",
                "input_ref",
                "plan_ref",
                "manifest_ref",
                "parameters",
                "pending_parameters_patch",
                "paused",
                "awaiting_external",
                "step_count",
                "wait_cycle_count",
                "rerun_count",
                "create_idempotency_key",
                "last_update_idempotency_key",
                "last_update_response",
                "started_at",
                "updated_at",
                "closed_at",
            ],
            sa.select(
                temporal_executions.c.workflow_id,
                temporal_executions.c.run_id,
                temporal_executions.c.namespace,
                temporal_executions.c.workflow_type,
                temporal_executions.c.owner_id,
                temporal_executions.c.owner_type,
                temporal_executions.c.state,
                temporal_executions.c.close_status,
                temporal_executions.c.entry,
                temporal_executions.c.search_attributes,
                temporal_executions.c.memo,
                temporal_executions.c.artifact_refs,
                temporal_executions.c.input_ref,
                temporal_executions.c.plan_ref,
                temporal_executions.c.manifest_ref,
                temporal_executions.c.parameters,
                temporal_executions.c.pending_parameters_patch,
                temporal_executions.c.paused,
                temporal_executions.c.awaiting_external,
                temporal_executions.c.step_count,
                temporal_executions.c.wait_cycle_count,
                temporal_executions.c.rerun_count,
                temporal_executions.c.create_idempotency_key,
                temporal_executions.c.last_update_idempotency_key,
                temporal_executions.c.last_update_response,
                temporal_executions.c.started_at,
                temporal_executions.c.updated_at,
                temporal_executions.c.closed_at,
            ),
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_temporal_execution_sources_type_updated_at",
        table_name="temporal_execution_sources",
    )
    op.drop_index(
        "ix_temporal_execution_sources_owner_state",
        table_name="temporal_execution_sources",
    )
    op.drop_index(
        "ix_temporal_execution_sources_state_updated_at",
        table_name="temporal_execution_sources",
    )
    op.drop_table("temporal_execution_sources")
