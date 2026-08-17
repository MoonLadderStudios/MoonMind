"""Separate canonical sessions, turn attempts, observations, commands, decisions.

Issue MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]). Adds the
durable control-plane aggregates that decompose the overloaded
``omnigent_bridge_sessions`` row:

* ``omnigent_sessions`` -- one canonical provider-session authority per
  Workflow/provider-session scope, with a single opaque chat-binding identity;
* ``omnigent_turn_attempts`` -- one row per logical instruction/continuation/
  steering/remediation turn, owning request idempotency but never chat authority;
* ``omnigent_observations`` -- append-only bounded index over authoritative
  observations (full payloads live in artifacts);
* ``omnigent_commands`` -- durable command/idempotency journal for logical side
  effects;
* ``omnigent_reconciliation_decisions`` -- append-only reconciliation record;
* ``omnigent_chat_binding_aliases`` -- safe resolution of previously issued
  (possibly duplicate) chat-binding ids.

This migration is purely additive: it creates the new tables and their
uniqueness invariants and never modifies or deletes the legacy bridge rows.
Backfill from legacy rows is a separate, idempotent, dry-run-capable routine in
``moonmind.omnigent.control_plane.backfill``.

Revision ID: 356_omnigent_control_plane
Revises: 355_checkpoint_blob_dedup
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "356_omnigent_control_plane"
down_revision = "355_checkpoint_blob_dedup"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), "postgresql"
    )


def upgrade() -> None:
    op.create_table(
        "omnigent_sessions",
        sa.Column("session_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("moonmind_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("moonmind_run_id", sa.String(length=255), nullable=True),
        sa.Column("step_execution_id", sa.String(length=255), nullable=True),
        sa.Column("moonmind_agent_run_id", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("compatibility_profile", sa.String(length=128), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("authority_scope", sa.String(length=512), nullable=False),
        sa.Column("chat_binding_id", sa.String(length=255), nullable=True),
        sa.Column("intent_ref", sa.String(length=1024), nullable=True),
        sa.Column("intent_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "desired_state", sa.String(length=64), nullable=False, server_default="active"
        ),
        sa.Column(
            "observed_state", sa.String(length=64), nullable=False, server_default="unknown"
        ),
        sa.Column(
            "reconciled_state",
            sa.String(length=64),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("active_turn_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("provider_event_cursor", sa.String(length=255), nullable=True),
        sa.Column("snapshot_frontier", sa.String(length=255), nullable=True),
        sa.Column("provider_profile_id", sa.String(length=128), nullable=True),
        sa.Column("provider_profile_generation", sa.Integer(), nullable=True),
        sa.Column("host_binding_ref", sa.String(length=255), nullable=True),
        sa.Column("host_lease_ref", sa.String(length=255), nullable=True),
        sa.Column("host_lease_generation", sa.Integer(), nullable=True),
        sa.Column("credential_generation", sa.Integer(), nullable=True),
        sa.Column("compatibility_ref", sa.String(length=1024), nullable=True),
        sa.Column("image_manifest_ref", sa.String(length=1024), nullable=True),
        sa.Column("terminal_state", sa.String(length=64), nullable=True),
        sa.Column(
            "cleanup_state", sa.String(length=64), nullable=False, server_default="none"
        ),
        sa.Column(
            "historical_read_state",
            sa.String(length=64),
            nullable=False,
            server_default="live",
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "fencing_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "next_reconciliation_deadline",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_decision_ref", sa.String(length=255), nullable=True),
        sa.Column("metadata", _json(), nullable=False, server_default="{}"),
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
        sa.UniqueConstraint(
            "authority_scope", name="uq_omnigent_sessions_authority_scope"
        ),
    )
    op.create_index(
        "uq_omnigent_sessions_chat_binding",
        "omnigent_sessions",
        ["chat_binding_id"],
        unique=True,
    )
    op.create_index(
        "ix_omnigent_sessions_workflow",
        "omnigent_sessions",
        ["moonmind_workflow_id"],
    )
    op.create_index(
        "ix_omnigent_sessions_provider_session",
        "omnigent_sessions",
        ["provider_session_id"],
    )
    op.create_index(
        "ix_omnigent_sessions_reconcile_deadline",
        "omnigent_sessions",
        ["next_reconciliation_deadline"],
    )

    op.create_table(
        "omnigent_turn_attempts",
        sa.Column("turn_attempt_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("step_execution_id", sa.String(length=255), nullable=True),
        sa.Column(
            "turn_kind",
            sa.String(length=32),
            nullable=False,
            server_default="instruction",
        ),
        sa.Column("continuation_of_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("remediation_of_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("instruction_digest", sa.String(length=128), nullable=True),
        sa.Column("provider_marker", sa.String(length=255), nullable=True),
        sa.Column("provider_turn_id", sa.String(length=255), nullable=True),
        sa.Column("provider_item_id", sa.String(length=255), nullable=True),
        sa.Column(
            "state", sa.String(length=32), nullable=False, server_default="prepared"
        ),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("terminal_evidence_ref", sa.String(length=1024), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "fencing_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("metadata", _json(), nullable=False, server_default="{}"),
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
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_turn_attempts_idempotency_key"
        ),
    )
    op.create_index(
        "ix_omnigent_turn_attempts_session",
        "omnigent_turn_attempts",
        ["session_id"],
    )
    op.create_index(
        "ix_omnigent_turn_attempts_state",
        "omnigent_turn_attempts",
        ["state"],
    )

    op.create_table(
        "omnigent_observations",
        sa.Column("observation_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("observation_kind", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=True),
        sa.Column("source_digest", sa.String(length=128), nullable=True),
        sa.Column("dedup_identity", sa.String(length=128), nullable=False),
        sa.Column("artifact_ref", sa.String(length=1024), nullable=True),
        sa.Column("bounded_index", _json(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_omnigent_observations_dedup",
        "omnigent_observations",
        ["session_id", "dedup_identity"],
        unique=True,
    )
    op.create_index(
        "uq_omnigent_observations_sequence",
        "omnigent_observations",
        ["session_id", "source", "source_sequence"],
        unique=True,
    )
    op.create_index(
        "ix_omnigent_observations_session_kind",
        "omnigent_observations",
        ["session_id", "observation_kind"],
    )

    op.create_table(
        "omnigent_commands",
        sa.Column("command_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("command_idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("expected_session_revision", sa.Integer(), nullable=True),
        sa.Column(
            "fencing_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("payload_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
        sa.Column("provider_receipt_id", sa.String(length=255), nullable=True),
        sa.Column(
            "delivery_ambiguous",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("result_ref", sa.String(length=1024), nullable=True),
        sa.Column("retry_policy", _json(), nullable=False, server_default="{}"),
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
        sa.UniqueConstraint(
            "command_idempotency_key",
            name="uq_omnigent_commands_idempotency_key",
        ),
    )
    op.create_index(
        "ix_omnigent_commands_session",
        "omnigent_commands",
        ["session_id"],
    )
    op.create_index(
        "ix_omnigent_commands_status",
        "omnigent_commands",
        ["status"],
    )

    op.create_table(
        "omnigent_reconciliation_decisions",
        sa.Column("decision_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("input_state_digest", sa.String(length=128), nullable=True),
        sa.Column("observation_frontier_digest", sa.String(length=128), nullable=True),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column(
            "fencing_generation", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("resulting_command_id", sa.String(length=255), nullable=True),
        sa.Column("next_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("product_visible_transition", sa.String(length=128), nullable=True),
        sa.Column("trace_ref", sa.String(length=255), nullable=True),
        sa.Column("diagnostics_ref", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_omnigent_reconciliation_decisions_session",
        "omnigent_reconciliation_decisions",
        ["session_id", "created_at"],
    )

    op.create_table(
        "omnigent_chat_binding_aliases",
        sa.Column("chat_binding_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "canonical_session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=True,
        ),
        sa.Column(
            "resolution", sa.String(length=32), nullable=False, server_default="alias"
        ),
        sa.Column("diagnostic_code", sa.String(length=128), nullable=True),
        sa.Column("source_bridge_session_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_omnigent_chat_binding_aliases_session",
        "omnigent_chat_binding_aliases",
        ["canonical_session_id"],
    )


def downgrade() -> None:
    op.drop_table("omnigent_chat_binding_aliases")
    op.drop_table("omnigent_reconciliation_decisions")
    op.drop_table("omnigent_commands")
    op.drop_table("omnigent_observations")
    op.drop_table("omnigent_turn_attempts")
    op.drop_index(
        "ix_omnigent_sessions_reconcile_deadline",
        table_name="omnigent_sessions",
    )
    op.drop_index(
        "ix_omnigent_sessions_provider_session",
        table_name="omnigent_sessions",
    )
    op.drop_index(
        "ix_omnigent_sessions_workflow",
        table_name="omnigent_sessions",
    )
    op.drop_index(
        "uq_omnigent_sessions_chat_binding",
        table_name="omnigent_sessions",
    )
    op.drop_table("omnigent_sessions")
