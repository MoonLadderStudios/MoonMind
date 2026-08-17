"""Enforce revisions and fencing across the Omnigent control plane.

MoonLadderStudios/MoonMind#3704.

Adds a monotonic optimistic-concurrency ``revision`` and a session-supervisor
fencing generation to the canonical session aggregate, and introduces the
universal control-plane substrate: turn-attempt authority, the logical command
ledger, the fencing-generation registry, and durable cleanup authority.

Revision ID: 356_omnigent_control_plane_fencing
Revises: 355_checkpoint_blob_dedup
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "356_omnigent_control_plane_fencing"
down_revision = "355_checkpoint_blob_dedup"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # Session-state aggregate: monotonic revision + supervisor fencing generation.
    op.add_column(
        "omnigent_bridge_sessions",
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "omnigent_bridge_sessions",
        sa.Column(
            "supervisor_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_omnigent_bridge_sessions_revision",
        "omnigent_bridge_sessions",
        "revision >= 1",
    )
    op.create_check_constraint(
        "ck_omnigent_bridge_sessions_supervisor_generation",
        "omnigent_bridge_sessions",
        "supervisor_generation >= 1",
    )

    # Turn-attempt authority.
    op.create_table(
        "omnigent_turn_attempts",
        sa.Column("turn_attempt_id", sa.String(length=255), nullable=False),
        sa.Column("bridge_session_id", sa.String(length=255), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=64),
            nullable=False,
            server_default="preparing",
        ),
        sa.Column("session_revision_observed", sa.Integer(), nullable=False),
        sa.Column(
            "fencing_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "observation_frontier",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "terminal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("metadata", _json(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["bridge_session_id"],
            ["omnigent_bridge_sessions.bridge_session_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("turn_attempt_id"),
        sa.UniqueConstraint(
            "bridge_session_id",
            "attempt_index",
            name="uq_omnigent_turn_attempt_index",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_omnigent_turn_attempt_revision"),
        sa.CheckConstraint(
            "fencing_generation >= 1",
            name="ck_omnigent_turn_attempt_fencing_generation",
        ),
        sa.CheckConstraint(
            "observation_frontier >= 0",
            name="ck_omnigent_turn_attempt_observation_frontier",
        ),
    )
    op.create_index(
        "ix_omnigent_turn_attempts_session",
        "omnigent_turn_attempts",
        ["bridge_session_id"],
    )
    op.create_index(
        "ix_omnigent_turn_attempts_status",
        "omnigent_turn_attempts",
        ["status"],
    )

    # Logical command ledger.
    op.create_table(
        "omnigent_commands",
        sa.Column("command_id", sa.String(length=255), nullable=False),
        sa.Column("bridge_session_id", sa.String(length=255), nullable=False),
        sa.Column("turn_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("payload_digest", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("expected_session_revision", sa.Integer(), nullable=False),
        sa.Column("expected_turn_revision", sa.Integer(), nullable=True),
        sa.Column("fencing_generations", _json(), nullable=False),
        sa.Column("owner_class", sa.String(length=64), nullable=False),
        sa.Column("claim_owner", sa.String(length=255), nullable=True),
        sa.Column(
            "claim_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "delivery_state",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("provider_receipt", sa.String(length=512), nullable=True),
        sa.Column("outcome", sa.String(length=48), nullable=True),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
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
        sa.ForeignKeyConstraint(
            ["bridge_session_id"],
            ["omnigent_bridge_sessions.bridge_session_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_attempt_id"],
            ["omnigent_turn_attempts.turn_attempt_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("command_id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_commands_idempotency_key"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_omnigent_commands_revision"),
        sa.CheckConstraint(
            "delivery_state IN ('pending','claimed','dispatched','delivered',"
            "'delivery_unknown','reconciled')",
            name="ck_omnigent_commands_delivery_state",
        ),
    )
    op.create_index(
        "ix_omnigent_commands_session", "omnigent_commands", ["bridge_session_id"]
    )
    op.create_index(
        "ix_omnigent_commands_turn", "omnigent_commands", ["turn_attempt_id"]
    )
    op.create_index(
        "ix_omnigent_commands_delivery_state",
        "omnigent_commands",
        ["delivery_state"],
    )

    # Universal fencing-generation registry.
    op.create_table(
        "omnigent_fencing_generations",
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("scope_kind", sa.String(length=64), nullable=False),
        sa.Column(
            "generation", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("scope_key"),
        sa.CheckConstraint(
            "generation >= 1", name="ck_omnigent_fencing_generation_positive"
        ),
    )
    op.create_index(
        "ix_omnigent_fencing_generations_kind",
        "omnigent_fencing_generations",
        ["scope_kind"],
    )

    # Durable cleanup authority.
    op.create_table(
        "omnigent_cleanup_authority",
        sa.Column("cleanup_id", sa.String(length=255), nullable=False),
        sa.Column("bridge_session_id", sa.String(length=255), nullable=False),
        sa.Column("host_generation", sa.Integer(), nullable=True),
        sa.Column("provider_session_epoch", sa.Integer(), nullable=True),
        sa.Column("workspace_generation", sa.Integer(), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("claim_owner", sa.String(length=255), nullable=True),
        sa.Column(
            "claim_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["bridge_session_id"],
            ["omnigent_bridge_sessions.bridge_session_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("cleanup_id"),
        sa.UniqueConstraint(
            "bridge_session_id", name="uq_omnigent_cleanup_authority_session"
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_omnigent_cleanup_authority_revision"
        ),
        sa.CheckConstraint(
            "status IN ('pending','claimed','completed','abandoned')",
            name="ck_omnigent_cleanup_authority_status",
        ),
    )
    op.create_index(
        "ix_omnigent_cleanup_authority_status",
        "omnigent_cleanup_authority",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_omnigent_cleanup_authority_status",
        table_name="omnigent_cleanup_authority",
    )
    op.drop_table("omnigent_cleanup_authority")

    op.drop_index(
        "ix_omnigent_fencing_generations_kind",
        table_name="omnigent_fencing_generations",
    )
    op.drop_table("omnigent_fencing_generations")

    op.drop_index(
        "ix_omnigent_commands_delivery_state", table_name="omnigent_commands"
    )
    op.drop_index("ix_omnigent_commands_turn", table_name="omnigent_commands")
    op.drop_index("ix_omnigent_commands_session", table_name="omnigent_commands")
    op.drop_table("omnigent_commands")

    op.drop_index(
        "ix_omnigent_turn_attempts_status", table_name="omnigent_turn_attempts"
    )
    op.drop_index(
        "ix_omnigent_turn_attempts_session", table_name="omnigent_turn_attempts"
    )
    op.drop_table("omnigent_turn_attempts")

    op.drop_constraint(
        "ck_omnigent_bridge_sessions_supervisor_generation",
        "omnigent_bridge_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_omnigent_bridge_sessions_revision",
        "omnigent_bridge_sessions",
        type_="check",
    )
    op.drop_column("omnigent_bridge_sessions", "supervisor_generation")
    op.drop_column("omnigent_bridge_sessions", "revision")
