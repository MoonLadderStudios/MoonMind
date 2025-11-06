"""Add Codex shard tables and metadata columns."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d2ad8d5f6b6d"
down_revision: Union[str, None] = "1f2c8c5d4d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CODEX_AUTH_VOLUME_STATUS = postgresql.ENUM(
    "ready",
    "needs_auth",
    "error",
    name="codexauthvolumestatus",
)

CODEX_WORKER_SHARD_STATUS = postgresql.ENUM(
    "active",
    "draining",
    "offline",
    name="codexworkershardstatus",
)

CODEX_PREFLIGHT_STATUS = postgresql.ENUM(
    "passed",
    "failed",
    "skipped",
    name="codexpreflightstatus",
)


def upgrade() -> None:
    bind = op.get_bind()
    CODEX_AUTH_VOLUME_STATUS.create(bind, checkfirst=True)
    CODEX_WORKER_SHARD_STATUS.create(bind, checkfirst=True)
    CODEX_PREFLIGHT_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "codex_auth_volumes",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("worker_affinity", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                name="codexauthvolumestatus", create_type=False
            ),
            nullable=False,
            server_default=sa.text("'needs_auth'::codexauthvolumestatus"),
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            server_onupdate=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("name", name="pk_codex_auth_volumes"),
        sa.UniqueConstraint(
            "worker_affinity", name="uq_codex_auth_volumes_worker_affinity"
        ),
    )

    op.create_table(
        "codex_worker_shards",
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("volume_name", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                name="codexworkershardstatus", create_type=False
            ),
            nullable=False,
            server_default=sa.text("'active'::codexworkershardstatus"),
        ),
        sa.Column(
            "hash_modulo", sa.Integer(), nullable=False, server_default=sa.text("3")
        ),
        sa.Column("worker_hostname", sa.String(length=255), nullable=True),
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
            server_onupdate=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["volume_name"],
            ["codex_auth_volumes.name"],
            name="fk_codex_worker_shards_volume_name",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("queue_name", name="pk_codex_worker_shards"),
        sa.UniqueConstraint(
            "volume_name", name="uq_codex_worker_shards_volume_name"
        ),
    )

    op.add_column(
        "spec_workflow_runs",
        sa.Column("codex_queue", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "spec_workflow_runs",
        sa.Column("codex_volume", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "spec_workflow_runs",
        sa.Column(
            "codex_preflight_status",
            postgresql.ENUM(
                name="codexpreflightstatus", create_type=False
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "spec_workflow_runs",
        sa.Column("codex_preflight_message", sa.Text(), nullable=True),
    )

    op.create_foreign_key(
        "fk_spec_workflow_runs_codex_queue",
        "spec_workflow_runs",
        "codex_worker_shards",
        ["codex_queue"],
        ["queue_name"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_spec_workflow_runs_codex_volume",
        "spec_workflow_runs",
        "codex_auth_volumes",
        ["codex_volume"],
        ["name"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_spec_workflow_runs_codex_volume", "spec_workflow_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_spec_workflow_runs_codex_queue", "spec_workflow_runs", type_="foreignkey"
    )

    op.drop_column("spec_workflow_runs", "codex_preflight_message")
    op.drop_column("spec_workflow_runs", "codex_preflight_status")
    op.drop_column("spec_workflow_runs", "codex_volume")
    op.drop_column("spec_workflow_runs", "codex_queue")

    op.drop_table("codex_worker_shards")
    op.drop_table("codex_auth_volumes")

    bind = op.get_bind()
    CODEX_PREFLIGHT_STATUS.drop(bind, checkfirst=True)
    CODEX_WORKER_SHARD_STATUS.drop(bind, checkfirst=True)
    CODEX_AUTH_VOLUME_STATUS.drop(bind, checkfirst=True)
