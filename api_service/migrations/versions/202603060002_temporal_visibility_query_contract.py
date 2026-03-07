"""Align temporal execution projection with visibility query contract."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202603060002"
down_revision: str | None = "202603060001"
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    temporal_executions = sa.Table(
        "temporal_executions",
        metadata,
        sa.Column("workflow_id", sa.String(length=64), primary_key=True),
        sa.Column("search_attributes", sa.JSON(), nullable=False),
        sa.Column("awaiting_external", sa.Boolean(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("waiting_reason", sa.String(length=32), nullable=True),
    )

    with op.batch_alter_table("temporal_executions") as batch_op:
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
            temporal_executions.c.search_attributes,
            temporal_executions.c.awaiting_external,
            temporal_executions.c.paused,
        )
    ).mappings()

    for row in rows:
        attrs = dict(row["search_attributes"] or {})

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
                search_attributes=attrs,
                waiting_reason=waiting_reason,
                attention_required=attention_required,
            )
        )

    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.alter_column(
            "attention_required",
            existing_type=sa.Boolean(),
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("temporal_executions") as batch_op:
        batch_op.drop_column("attention_required")
        batch_op.drop_column("waiting_reason")
