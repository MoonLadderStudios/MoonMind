"""Add persisted task source mapping table."""

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
    source_enum = sa.Enum(
        "queue",
        "orchestrator",
        "temporal",
        name="tasksourcekind",
    )
    if bind.dialect.name == "postgresql":
        source_enum.create(bind, checkfirst=True)
        source_column = sa.dialects.postgresql.ENUM(
            name="tasksourcekind",
            create_type=False,
        )
    else:
        source_column = source_enum

    op.create_table(
        "task_source_mappings",
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("source", source_column, nullable=False),
        sa.Column("entry", sa.String(length=32), nullable=True),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=True),
        sa.Column("owner_type", sa.String(length=32), nullable=True),
        sa.Column("owner_id", sa.String(length=128), nullable=True),
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
        sa.PrimaryKeyConstraint("task_id", name="pk_task_source_mappings"),
    )
    op.create_index(
        "ix_task_source_mappings_source_entry",
        "task_source_mappings",
        ["source", "entry"],
        unique=False,
    )
    op.create_index(
        "ix_task_source_mappings_source_record_id",
        "task_source_mappings",
        ["source", "source_record_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_source_mappings_source_record_id",
        table_name="task_source_mappings",
    )
    op.drop_index(
        "ix_task_source_mappings_source_entry",
        table_name="task_source_mappings",
    )
    op.drop_table("task_source_mappings")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="tasksourcekind").drop(bind, checkfirst=True)
