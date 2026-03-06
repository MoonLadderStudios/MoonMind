"""Align temporal execution projections with the source-of-truth contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202603060001"
down_revision: str | None = "202603050002"
__all__: Sequence[str] = ("revision", "down_revision")


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

    owner_type_enum = sa.Enum(
        "user",
        "system",
        "service",
        name="temporalexecutionownertype",
    )
    sync_state_enum = sa.Enum(
        "fresh",
        "stale",
        "repair_pending",
        "orphaned",
        name="temporalexecutionprojectionsyncstate",
    )
    source_mode_enum = sa.Enum(
        "projection_only",
        "mixed",
        "temporal_authoritative",
        name="temporalexecutionprojectionsourcemode",
    )

    _create_enum_if_postgres(owner_type_enum)
    _create_enum_if_postgres(sync_state_enum)
    _create_enum_if_postgres(source_mode_enum)

    owner_type_column = (
        postgresql.ENUM(name="temporalexecutionownertype", create_type=False)
        if is_postgres
        else owner_type_enum
    )
    sync_state_column = (
        postgresql.ENUM(name="temporalexecutionprojectionsyncstate", create_type=False)
        if is_postgres
        else sync_state_enum
    )
    source_mode_column = (
        postgresql.ENUM(name="temporalexecutionprojectionsourcemode", create_type=False)
        if is_postgres
        else source_mode_enum
    )

    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.add_column(sa.Column("owner_type", owner_type_column, nullable=True))
        batch_op.add_column(
            sa.Column(
                "projection_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("sync_state", sync_state_column, nullable=True))
        batch_op.add_column(sa.Column("sync_error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("source_mode", source_mode_column, nullable=True))
        batch_op.drop_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            [
                "create_idempotency_key",
                "owner_id",
                "owner_type",
                "workflow_type",
            ],
        )

    temporal_executions = sa.table(
        "temporal_executions",
        sa.column("workflow_id", sa.String(length=64)),
        sa.column("workflow_type", sa.String(length=64)),
        sa.column("owner_id", sa.String(length=64)),
        sa.column("owner_type", sa.String(length=16)),
        sa.column("state", sa.String(length=64)),
        sa.column("entry", sa.String(length=16)),
        sa.column("search_attributes", sa.JSON()),
        sa.column("create_idempotency_key", sa.String(length=128)),
        sa.column("projection_version", sa.Integer()),
        sa.column("last_synced_at", sa.DateTime(timezone=True)),
        sa.column("sync_state", sa.String(length=32)),
        sa.column("sync_error", sa.Text()),
        sa.column("source_mode", sa.String(length=32)),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    owner_priority = sa.case(
        (temporal_executions.c.owner_id == "system", 0),
        else_=1,
    )
    rows = bind.execution_options(stream_results=True).execute(
        sa.select(
            temporal_executions.c.workflow_id,
            temporal_executions.c.workflow_type,
            temporal_executions.c.owner_id,
            temporal_executions.c.state,
            temporal_executions.c.entry,
            temporal_executions.c.search_attributes,
            temporal_executions.c.create_idempotency_key,
            temporal_executions.c.started_at,
            temporal_executions.c.updated_at,
        ).order_by(owner_priority, temporal_executions.c.workflow_id)
    ).mappings()

    now = datetime.now(UTC)
    seen_idempotency_scopes: set[tuple[str, str, str, str]] = set()
    for row in rows:
        raw_owner_id = str(row["owner_id"] or "").strip()
        if not raw_owner_id or raw_owner_id == "unknown":
            owner_type = "system"
            owner_id = "system"
        elif raw_owner_id == "system":
            owner_type = "system"
            owner_id = "system"
        else:
            owner_type = "user"
            owner_id = raw_owner_id

        create_idempotency_key = row["create_idempotency_key"]
        if create_idempotency_key:
            scope = (
                str(create_idempotency_key),
                owner_id,
                owner_type,
                str(row["workflow_type"]),
            )
            if scope in seen_idempotency_scopes:
                create_idempotency_key = None
            else:
                seen_idempotency_scopes.add(scope)

        synced_at = row["updated_at"] or row["started_at"] or now
        search_attributes = dict(row["search_attributes"] or {})
        search_attributes["mm_owner_type"] = owner_type
        search_attributes["mm_owner_id"] = owner_id
        if row["state"]:
            search_attributes["mm_state"] = str(row["state"])
        if row["entry"]:
            search_attributes["mm_entry"] = str(row["entry"])
        search_attributes["mm_updated_at"] = synced_at.isoformat()

        bind.execute(
            sa.update(temporal_executions)
            .where(
                temporal_executions.c.workflow_id == row["workflow_id"],
            )
            .values(
                create_idempotency_key=create_idempotency_key,
                owner_id=owner_id,
                owner_type=owner_type,
                search_attributes=search_attributes,
                projection_version=1,
                last_synced_at=synced_at,
                sync_state="fresh",
                sync_error=None,
                source_mode="projection_only",
            )
        )

    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.alter_column("owner_type", nullable=False)
        batch_op.alter_column(
            "last_synced_at",
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        )
        batch_op.alter_column(
            "sync_state",
            nullable=False,
            server_default=sa.text("'fresh'"),
        )
        batch_op.alter_column(
            "source_mode",
            nullable=False,
            server_default=sa.text("'projection_only'"),
        )


def downgrade() -> None:
    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.drop_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            [
                "create_idempotency_key",
                "owner_id",
                "workflow_type",
            ],
        )
        batch_op.drop_column("source_mode")
        batch_op.drop_column("sync_error")
        batch_op.drop_column("sync_state")
        batch_op.drop_column("last_synced_at")
        batch_op.drop_column("projection_version")
        batch_op.drop_column("owner_type")

    source_mode_enum = sa.Enum(name="temporalexecutionprojectionsourcemode")
    sync_state_enum = sa.Enum(name="temporalexecutionprojectionsyncstate")
    owner_type_enum = sa.Enum(name="temporalexecutionownertype")

    _drop_enum_if_postgres(source_mode_enum)
    _drop_enum_if_postgres(sync_state_enum)
    _drop_enum_if_postgres(owner_type_enum)
