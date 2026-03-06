"""Align temporal execution projection with visibility query contract."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202603060001"
down_revision: str | None = "202603050002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    temporal_executions = sa.Table(
        "temporal_executions",
        metadata,
        sa.Column("workflow_id", sa.String(length=64), primary_key=True),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=True),
        sa.Column("search_attributes", sa.JSON(), nullable=False),
        sa.Column("awaiting_external", sa.Boolean(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("waiting_reason", sa.String(length=32), nullable=True),
        sa.Column("attention_required", sa.Boolean(), nullable=False),
        sa.Column("owner_type", sa.String(length=16), nullable=True),
    )

    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "owner_type",
                sa.String(length=16),
                nullable=True,
                server_default=sa.text("'user'"),
            )
        )
        batch_op.add_column(
            sa.Column("waiting_reason", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "attention_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )

    rows = bind.execute(
        sa.select(
            temporal_executions.c.workflow_id,
            temporal_executions.c.owner_id,
            temporal_executions.c.search_attributes,
            temporal_executions.c.awaiting_external,
            temporal_executions.c.paused,
        )
    ).mappings()

    for row in rows:
        attrs = dict(row["search_attributes"] or {})
        existing_owner_id = str(row["owner_id"]).strip() if row["owner_id"] else ""
        search_owner_type = str(attrs.get("mm_owner_type") or "").strip()
        search_owner_id = str(attrs.get("mm_owner_id") or "").strip()

        if search_owner_type in {"user", "system", "service"}:
            resolved_owner_type = search_owner_type
        elif existing_owner_id and existing_owner_id != "unknown":
            resolved_owner_type = "user"
        else:
            resolved_owner_type = "system"

        resolved_owner_id = existing_owner_id or search_owner_id
        if not resolved_owner_id or resolved_owner_id == "unknown":
            resolved_owner_id = "system" if resolved_owner_type == "system" else ""
        if not resolved_owner_id:
            resolved_owner_type = "system"
            resolved_owner_id = "system"

        attrs["mm_owner_type"] = resolved_owner_type
        attrs["mm_owner_id"] = resolved_owner_id

        waiting_reason = None
        attention_required = False
        if row["awaiting_external"]:
            if row["paused"]:
                waiting_reason = "operator_paused"
                attention_required = True

        bind.execute(
            temporal_executions.update()
            .where(temporal_executions.c.workflow_id == row["workflow_id"])
            .values(
                owner_type=resolved_owner_type,
                owner_id=resolved_owner_id,
                search_attributes=attrs,
                waiting_reason=waiting_reason,
                attention_required=attention_required,
            )
        )

    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.drop_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            type_="unique",
        )
        batch_op.alter_column(
            "owner_type",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "owner_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.alter_column(
            "attention_required",
            existing_type=sa.Boolean(),
            server_default=None,
        )
        batch_op.create_unique_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            ["create_idempotency_key", "owner_type", "owner_id", "workflow_type"],
        )


def downgrade() -> None:
    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.drop_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            type_="unique",
        )
        batch_op.alter_column(
            "owner_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch_op.create_unique_constraint(
            "uq_temporal_executions_create_idempotency_owner_type",
            ["create_idempotency_key", "owner_id", "workflow_type"],
        )
        batch_op.drop_column("attention_required")
        batch_op.drop_column("waiting_reason")
        batch_op.drop_column("owner_type")
