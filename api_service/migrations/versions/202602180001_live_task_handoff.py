"""Add live task handoff persistence tables for queue task runs."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlalchemy_utils
from alembic import op
from sqlalchemy.dialects import postgresql

from api_service.core.encryption import get_encryption_key

revision: str = "202602180001"
down_revision: Union[str, None] = "202602170001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGENT_JOB_LIVE_SESSION_PROVIDER = postgresql.ENUM(
    "tmate",
    name="agentjoblivesessionprovider",
    create_type=False,
)

AGENT_JOB_LIVE_SESSION_STATUS = postgresql.ENUM(
    "disabled",
    "starting",
    "ready",
    "revoked",
    "ended",
    "error",
    name="agentjoblivesessionstatus",
    create_type=False,
)


def upgrade() -> None:
    """Apply live task handoff schema updates."""

    bind = op.get_bind()
    AGENT_JOB_LIVE_SESSION_PROVIDER.create(bind, checkfirst=True)
    AGENT_JOB_LIVE_SESSION_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "task_run_live_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM(name="agentjoblivesessionprovider", create_type=False),
            nullable=False,
            server_default=sa.text("'tmate'::agentjoblivesessionprovider"),
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="agentjoblivesessionstatus", create_type=False),
            nullable=False,
            server_default=sa.text("'disabled'::agentjoblivesessionstatus"),
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("worker_hostname", sa.String(length=255), nullable=True),
        sa.Column("tmate_session_name", sa.String(length=255), nullable=True),
        sa.Column("tmate_socket_path", sa.String(length=1024), nullable=True),
        sa.Column("attach_ro", sa.Text(), nullable=True),
        sa.Column(
            "attach_rw_encrypted",
            sqlalchemy_utils.EncryptedType(sa.Text, get_encryption_key),
            nullable=True,
        ),
        sa.Column("web_ro", sa.Text(), nullable=True),
        sa.Column(
            "web_rw_encrypted",
            sqlalchemy_utils.EncryptedType(sa.Text, get_encryption_key),
            nullable=True,
        ),
        sa.Column("rw_granted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            ["task_run_id"],
            ["agent_jobs.id"],
            name="fk_task_run_live_sessions_task_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_run_live_sessions"),
        sa.UniqueConstraint("task_run_id", name="uq_task_run_live_sessions_task_run_id"),
    )
    op.create_index(
        "ix_task_run_live_sessions_status_expires_at",
        "task_run_live_sessions",
        ["status", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_run_live_sessions_last_heartbeat_at",
        "task_run_live_sessions",
        ["last_heartbeat_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_run_live_sessions_worker_id",
        "task_run_live_sessions",
        ["worker_id"],
        unique=False,
    )

    op.create_table(
        "task_run_control_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_run_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()),
                "postgresql",
            ),
            nullable=True,
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
            ["task_run_id"],
            ["agent_jobs.id"],
            name="fk_task_run_control_events_task_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user.id"],
            name="fk_task_run_control_events_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task_run_control_events"),
    )
    op.create_index(
        "ix_task_run_control_events_task_run_id_created_at",
        "task_run_control_events",
        ["task_run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_run_control_events_action_created_at",
        "task_run_control_events",
        ["action", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Revert live task handoff schema updates."""

    op.drop_index(
        "ix_task_run_control_events_action_created_at",
        table_name="task_run_control_events",
    )
    op.drop_index(
        "ix_task_run_control_events_task_run_id_created_at",
        table_name="task_run_control_events",
    )
    op.drop_table("task_run_control_events")

    op.drop_index(
        "ix_task_run_live_sessions_worker_id",
        table_name="task_run_live_sessions",
    )
    op.drop_index(
        "ix_task_run_live_sessions_last_heartbeat_at",
        table_name="task_run_live_sessions",
    )
    op.drop_index(
        "ix_task_run_live_sessions_status_expires_at",
        table_name="task_run_live_sessions",
    )
    op.drop_table("task_run_live_sessions")

    bind = op.get_bind()
    AGENT_JOB_LIVE_SESSION_STATUS.drop(bind, checkfirst=True)
    AGENT_JOB_LIVE_SESSION_PROVIDER.drop(bind, checkfirst=True)
