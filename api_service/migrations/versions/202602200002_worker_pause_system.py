"""Add worker pause state and audit tables."""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202602200002"
down_revision: Union[str, None] = "202602190004"

WORKER_PAUSE_MODE = postgresql.ENUM(
    "drain",
    "quiesce",
    name="workerpausemode",
)


def upgrade() -> None:
    """Create worker pause persistence tables."""

    bind = op.get_bind()
    WORKER_PAUSE_MODE.create(bind, checkfirst=True)

    op.create_table(
        "system_worker_pause_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "paused", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "mode",
            postgresql.ENUM(name="workerpausemode", create_type=False),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["user.id"],
            name="fk_system_worker_pause_state_requested_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_system_worker_pause_state"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO system_worker_pause_state
                (id, paused, mode, reason, requested_by_user_id, requested_at, updated_at, version)
            VALUES
                (1, false, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, 1)
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    op.create_table(
        "system_control_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "control",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'worker_pause'::text"),
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "mode",
            postgresql.ENUM(name="workerpausemode", create_type=False),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
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
            ["actor_user_id"],
            ["user.id"],
            name="fk_system_control_events_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_system_control_events"),
    )
    op.create_index(
        "ix_system_control_events_control_created_at",
        "system_control_events",
        ["control", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop worker pause persistence tables."""

    op.drop_index(
        "ix_system_control_events_control_created_at",
        table_name="system_control_events",
    )
    op.drop_table("system_control_events")
    op.drop_table("system_worker_pause_state")

    bind = op.get_bind()
    WORKER_PAUSE_MODE.drop(bind, checkfirst=True)
