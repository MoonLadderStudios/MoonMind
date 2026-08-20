"""Omnigent control-plane durable aggregates.

Creates the additive Omnigent control-plane aggregate tables that separate the
overloaded ``omnigent_bridge_sessions`` row into explicit durable concepts:

  * ``omnigent_sessions``                  - canonical provider-session authority
  * ``omnigent_turn_attempts``             - one logical turn/continuation turn
  * ``omnigent_observations``              - append-only bounded observation index
  * ``omnigent_commands``                  - durable logical-side-effect journal
  * ``omnigent_reconciliation_decisions``  - append-only reconciliation record
  * ``omnigent_chat_binding_aliases``      - safe resolution of prior handles

This migration is deliberately additive: the legacy bridge tables are left in
place and the backfill runs as separate idempotent tooling
(``moonmind.omnigent.control_plane.backfill``). Retirement of the legacy tables
is owned by a later Omnigent control-plane rollout issue.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

Revision ID: 356_omnigent_ctrl_plane
Revises: 355_checkpoint_blob_dedup
Create Date: 2026-08-18

The revision id is kept <= 32 characters so it fits Alembic's
``alembic_version.version_num`` column.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "356_omnigent_ctrl_plane"
down_revision: Union[str, None] = "355_checkpoint_blob_dedup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "omnigent_sessions",
        sa.Column("session_id", sa.String(length=255), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("moonmind_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("moonmind_run_id", sa.String(length=255), nullable=True),
        sa.Column("step_execution_id", sa.String(length=255), nullable=True),
        sa.Column("moonmind_agent_run_id", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("compatibility_profile", sa.String(length=128), nullable=True),
        sa.Column("provider_session_ref", sa.String(length=255), nullable=True),
        sa.Column("chat_binding_id", sa.String(length=255), nullable=True),
        sa.Column("intent_ref", sa.String(length=1024), nullable=True),
        sa.Column("intent_digest", sa.String(length=128), nullable=True),
        sa.Column("desired_state", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("observed_state", sa.String(length=64), nullable=True),
        sa.Column("reconciled_state", sa.String(length=64), nullable=True),
        sa.Column("active_turn_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("provider_event_cursor", sa.String(length=255), nullable=True),
        sa.Column("snapshot_frontier", sa.String(length=255), nullable=True),
        sa.Column("provider_profile_id", sa.String(length=128), nullable=True),
        sa.Column("host_binding_ref", sa.String(length=255), nullable=True),
        sa.Column("host_lease_ref", sa.String(length=255), nullable=True),
        sa.Column("provider_profile_generation", sa.Integer(), nullable=True),
        sa.Column("host_lease_generation", sa.Integer(), nullable=True),
        sa.Column("credential_generation", sa.Integer(), nullable=True),
        sa.Column("compatibility_ref", sa.String(length=1024), nullable=True),
        sa.Column("image_manifest_ref", sa.String(length=1024), nullable=True),
        sa.Column("terminal_state", sa.String(length=64), nullable=True),
        sa.Column("terminal_evidence_ref", sa.String(length=1024), nullable=True),
        sa.Column("cleanup_state", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column(
            "historical_read_state", sa.String(length=64), nullable=False, server_default="live"
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fencing_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_reconciliation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_decision_ref", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # At most one canonical authority per (workflow, provider session); one
    # opaque chat binding maps to one canonical session. NULLs stay distinct on
    # both SQLite and Postgres, so unattached rows never collide.
    op.create_index(
        "uq_omnigent_sessions_scope",
        "omnigent_sessions",
        ["moonmind_workflow_id", "provider_session_ref"],
        unique=True,
    )
    op.create_index(
        "uq_omnigent_sessions_chat_binding",
        "omnigent_sessions",
        ["chat_binding_id"],
        unique=True,
    )
    op.create_index(
        "ix_omnigent_sessions_workflow", "omnigent_sessions", ["moonmind_workflow_id"]
    )
    op.create_index(
        "ix_omnigent_sessions_deadline",
        "omnigent_sessions",
        ["next_reconciliation_deadline"],
    )

    op.create_table(
        "omnigent_turn_attempts",
        sa.Column("turn_attempt_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("step_execution_id", sa.String(length=255), nullable=True),
        sa.Column(
            "lineage_kind", sa.String(length=32), nullable=False, server_default="instruction"
        ),
        sa.Column("parent_turn_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("remediation_of_turn_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("instruction_digest", sa.String(length=128), nullable=True),
        sa.Column("provider_marker", sa.Text(), nullable=True),
        sa.Column("provider_turn_id", sa.String(length=255), nullable=True),
        sa.Column("provider_item_id", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="prepared"),
        sa.Column("terminal_state", sa.String(length=32), nullable=True),
        sa.Column("attempt_outcome", sa.String(length=64), nullable=True),
        sa.Column("terminal_evidence_ref", sa.String(length=1024), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fencing_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_turn_attempts_idempotency_key"
        ),
    )
    op.create_index(
        "ix_omnigent_turn_attempts_session", "omnigent_turn_attempts", ["session_id"]
    )
    op.create_index(
        "ix_omnigent_turn_attempts_state", "omnigent_turn_attempts", ["state"]
    )

    op.create_table(
        "omnigent_observations",
        sa.Column("observation_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("observation_type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=True),
        sa.Column("source_digest", sa.String(length=128), nullable=True),
        sa.Column("deduplication_key", sa.String(length=128), nullable=False),
        sa.Column("payload_ref", sa.String(length=1024), nullable=True),
        sa.Column("bounded_index", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "uq_omnigent_observations_dedup",
        "omnigent_observations",
        ["session_id", "deduplication_key"],
        unique=True,
    )
    op.create_index(
        "ix_omnigent_observations_session_observed",
        "omnigent_observations",
        ["session_id", "observed_at"],
    )
    op.create_index(
        "ix_omnigent_observations_type",
        "omnigent_observations",
        ["session_id", "observation_type"],
    )

    op.create_table(
        "omnigent_commands",
        sa.Column("command_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("turn_attempt_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("expected_session_revision", sa.Integer(), nullable=True),
        sa.Column("fencing_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("provider_receipt_id", sa.String(length=255), nullable=True),
        sa.Column(
            "delivery_ambiguous", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("result_ref", sa.String(length=1024), nullable=True),
        sa.Column("retry_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_commands_idempotency_key"
        ),
    )
    op.create_index("ix_omnigent_commands_session", "omnigent_commands", ["session_id"])
    op.create_index("ix_omnigent_commands_status", "omnigent_commands", ["status"])

    op.create_table(
        "omnigent_reconciliation_decisions",
        sa.Column("decision_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_state_digest", sa.String(length=128), nullable=True),
        sa.Column("observation_frontier_digest", sa.String(length=128), nullable=True),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("fencing_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision_code", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("resulting_command_id", sa.String(length=255), nullable=True),
        sa.Column("next_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("product_visible_transition", sa.String(length=128), nullable=True),
        sa.Column("trace_ref", sa.String(length=1024), nullable=True),
        sa.Column("diagnostics_ref", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_omnigent_reconciliation_decisions_session_created",
        "omnigent_reconciliation_decisions",
        ["session_id", "created_at"],
    )

    op.create_table(
        "omnigent_chat_binding_aliases",
        sa.Column("chat_binding_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            nullable=True,
        ),
        sa.Column("alias_state", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("diagnostic_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_omnigent_chat_binding_aliases_session",
        "omnigent_chat_binding_aliases",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_omnigent_chat_binding_aliases_session",
        table_name="omnigent_chat_binding_aliases",
    )
    op.drop_table("omnigent_chat_binding_aliases")

    op.drop_index(
        "ix_omnigent_reconciliation_decisions_session_created",
        table_name="omnigent_reconciliation_decisions",
    )
    op.drop_table("omnigent_reconciliation_decisions")

    op.drop_index("ix_omnigent_commands_status", table_name="omnigent_commands")
    op.drop_index("ix_omnigent_commands_session", table_name="omnigent_commands")
    op.drop_table("omnigent_commands")

    op.drop_index(
        "ix_omnigent_observations_type", table_name="omnigent_observations"
    )
    op.drop_index(
        "ix_omnigent_observations_session_observed", table_name="omnigent_observations"
    )
    op.drop_index(
        "uq_omnigent_observations_dedup", table_name="omnigent_observations"
    )
    op.drop_table("omnigent_observations")

    op.drop_index(
        "ix_omnigent_turn_attempts_state", table_name="omnigent_turn_attempts"
    )
    op.drop_index(
        "ix_omnigent_turn_attempts_session", table_name="omnigent_turn_attempts"
    )
    op.drop_table("omnigent_turn_attempts")

    op.drop_index("ix_omnigent_sessions_deadline", table_name="omnigent_sessions")
    op.drop_index("ix_omnigent_sessions_workflow", table_name="omnigent_sessions")
    op.drop_index("uq_omnigent_sessions_chat_binding", table_name="omnigent_sessions")
    op.drop_index("uq_omnigent_sessions_scope", table_name="omnigent_sessions")
    op.drop_table("omnigent_sessions")
