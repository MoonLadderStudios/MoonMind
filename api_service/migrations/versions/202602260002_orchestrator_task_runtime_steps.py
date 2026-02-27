"""Add orchestrator task runtime step table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602260002"
down_revision: str | None = "202602260001"


def _json_variant() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "orchestrator_task_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("skill_args", _json_variant(), nullable=False, server_default="{}"),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "succeeded",
                "failed",
                "skipped",
                name="orchestratortaskstepstatus",
                native_enum=True,
                validate_strings=True,
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "artifact_refs", _json_variant(), nullable=False, server_default="[]"
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
        ),
        sa.CheckConstraint("step_index >= 0", name="ck_orchestrator_task_step_index"),
        sa.CheckConstraint(
            "attempt >= 1",
            name="ck_orchestrator_task_step_attempt_positive",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["orchestrator_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "step_id", name="uq_orchestrator_task_step_id"),
    )
    op.create_index(
        "ix_orchestrator_task_steps_task_id",
        "orchestrator_task_steps",
        ["task_id", "step_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_orchestrator_task_steps_task_id",
        table_name="orchestrator_task_steps",
    )
    op.drop_table("orchestrator_task_steps")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS orchestratortaskstepstatus")
