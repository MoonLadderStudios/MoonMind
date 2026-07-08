"""Canonical omnigent_bridge_sessions store + event index (MM-1152).

Creates the canonical ``omnigent_bridge_sessions`` and
``omnigent_bridge_session_events`` tables (OmnigentBridge design §7.1/§7.2),
migrates the superseded ``omnigent_external_runs`` mapping into them, and drops
``omnigent_external_runs`` in the same cohesive change -- no alias, wrapper, or
parallel table (repository Compatibility Policy).

Source design traceability: OmnigentBridge.md (MM-1152, source issue MM-1140).

Revision ID: 334_mm1152_bridge_sessions
Revises: 333_checkpoint_branch_graph
Create Date: 2026-07-08

The revision id is kept <= 32 characters so it fits Alembic's
``alembic_version.version_num`` column (enforced by
``tests/unit/api_service/test_provider_profile_enums.py``).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "334_mm1152_bridge_sessions"
down_revision: Union[str, None] = "333_checkpoint_branch_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "omnigent_bridge_sessions",
        sa.Column("bridge_session_id", sa.String(length=255), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("compatibility_profile", sa.String(length=128), nullable=False),
        sa.Column("moonmind_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("moonmind_run_id", sa.String(length=255), nullable=True),
        sa.Column("moonmind_agent_run_id", sa.String(length=255), nullable=False),
        sa.Column("step_execution_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("omnigent_endpoint_ref", sa.String(length=255), nullable=False),
        sa.Column("omnigent_session_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_host_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_runner_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_agent_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_agent_name", sa.String(length=255), nullable=True),
        sa.Column("host_type", sa.String(length=32), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=64),
            nullable=False,
            server_default="declared",
        ),
        sa.Column(
            "first_message_state",
            sa.String(length=32),
            nullable=False,
            server_default="not_prepared",
        ),
        sa.Column("first_message_digest", sa.String(length=128), nullable=True),
        sa.Column("first_message_marker", sa.Text(), nullable=True),
        sa.Column("first_message_post_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_message_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_message_pending_id", sa.String(length=255), nullable=True),
        sa.Column("first_message_item_id", sa.String(length=255), nullable=True),
        sa.Column("raw_events_ref", sa.String(length=1024), nullable=True),
        sa.Column("normalized_events_ref", sa.String(length=1024), nullable=True),
        sa.Column("initial_snapshot_ref", sa.String(length=1024), nullable=True),
        sa.Column("final_snapshot_ref", sa.String(length=1024), nullable=True),
        sa.Column("capture_manifest_ref", sa.String(length=1024), nullable=True),
        sa.Column("diagnostics_ref", sa.String(length=1024), nullable=True),
        sa.Column("external_state_ref", sa.String(length=1024), nullable=True),
        sa.Column("terminal_refs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_bridge_sessions_idempotency_key"
        ),
    )
    op.create_index(
        "ix_omnigent_bridge_sessions_session",
        "omnigent_bridge_sessions",
        ["omnigent_session_id"],
    )
    op.create_index(
        "ix_omnigent_bridge_sessions_workflow",
        "omnigent_bridge_sessions",
        ["moonmind_workflow_id"],
    )
    op.create_index(
        "ix_omnigent_bridge_sessions_status",
        "omnigent_bridge_sessions",
        ["status"],
    )

    op.create_table(
        "omnigent_bridge_session_events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("bridge_session_id", sa.String(length=255), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("normalized_status", sa.String(length=64), nullable=True),
        sa.Column("text_preview", sa.Text(), nullable=True),
        sa.Column("artifact_ref", sa.String(length=1024), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "ix_omnigent_bridge_session_events_session",
        "omnigent_bridge_session_events",
        ["bridge_session_id"],
    )
    op.create_index(
        "ix_omnigent_bridge_session_events_sequence",
        "omnigent_bridge_session_events",
        ["bridge_session_id", "sequence"],
    )

    # Data migration: fold the superseded omnigent_external_runs mapping into the
    # canonical store. The old row.status was already the coalesced value (active
    # or a terminal status); re-coalesce defensively so any provider-native alias
    # is normalized (§7.1).
    op.execute(
        """
        INSERT INTO omnigent_bridge_sessions (
            bridge_session_id, provider, compatibility_profile,
            moonmind_workflow_id, moonmind_run_id, moonmind_agent_run_id,
            step_execution_id, idempotency_key, omnigent_endpoint_ref,
            omnigent_session_id, omnigent_host_id, omnigent_runner_id,
            omnigent_agent_id, omnigent_agent_name, host_type, workspace, status,
            first_message_state, first_message_digest, first_message_marker,
            first_message_post_attempted_at, first_message_posted_at,
            first_message_pending_id, first_message_item_id,
            raw_events_ref, normalized_events_ref, initial_snapshot_ref,
            final_snapshot_ref, capture_manifest_ref, diagnostics_ref,
            external_state_ref, terminal_refs, metadata, created_at, updated_at
        )
        SELECT
            'brs_migrated_' || md5(idempotency_key),
            'omnigent',
            'omnigent.server.v1',
            moonmind_workflow_id,
            moonmind_agent_run_id,
            moonmind_agent_run_id,
            NULL,
            idempotency_key,
            omnigent_endpoint_ref,
            omnigent_session_id,
            NULL,
            NULL,
            omnigent_agent_id,
            omnigent_agent_name,
            COALESCE(NULLIF(target_metadata ->> 'hostType', ''), 'managed'),
            NULLIF(target_metadata ->> 'workspace', ''),
            CASE
                WHEN status = 'cancelled' THEN 'canceled'
                WHEN status IN ('timeout') THEN 'timed_out'
                WHEN status IN ('completed', 'failed', 'canceled', 'timed_out') THEN status
                ELSE 'active'
            END,
            first_message_state,
            first_message_digest,
            first_message_marker,
            first_message_post_attempted_at,
            first_message_posted_at,
            first_message_pending_id,
            first_message_item_id,
            sse_events_ref,
            NULL,
            NULL,
            final_snapshot_ref,
            NULL,
            diagnostics_ref,
            NULL,
            terminal_refs,
            target_metadata,
            created_at,
            updated_at
        FROM omnigent_external_runs
        """
    )

    op.drop_index("ix_omnigent_external_runs_status", table_name="omnigent_external_runs")
    op.drop_index("ix_omnigent_external_runs_session", table_name="omnigent_external_runs")
    op.drop_table("omnigent_external_runs")


def downgrade() -> None:
    op.create_table(
        "omnigent_external_runs",
        sa.Column("idempotency_key", sa.String(length=512), primary_key=True),
        sa.Column("moonmind_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("moonmind_agent_run_id", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=512), nullable=False),
        sa.Column("omnigent_endpoint_ref", sa.String(length=255), nullable=False),
        sa.Column("omnigent_session_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_agent_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_agent_name", sa.String(length=255), nullable=True),
        sa.Column("target_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="active"),
        sa.Column(
            "first_message_state",
            sa.String(length=32),
            nullable=False,
            server_default="not_prepared",
        ),
        sa.Column("first_message_digest", sa.String(length=128), nullable=True),
        sa.Column("first_message_marker", sa.Text(), nullable=True),
        sa.Column("first_message_post_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_message_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_message_pending_id", sa.String(length=255), nullable=True),
        sa.Column("first_message_item_id", sa.String(length=255), nullable=True),
        sa.Column("first_message_request_ref", sa.String(length=1024), nullable=True),
        sa.Column("first_message_response_ref", sa.String(length=1024), nullable=True),
        sa.Column("artifact_refs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("terminal_refs", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("final_snapshot_ref", sa.String(length=1024), nullable=True),
        sa.Column("sse_events_ref", sa.String(length=1024), nullable=True),
        sa.Column("diagnostics_ref", sa.String(length=1024), nullable=True),
        sa.Column("result_ref", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_omnigent_external_runs_session",
        "omnigent_external_runs",
        ["omnigent_session_id"],
    )
    op.create_index(
        "ix_omnigent_external_runs_status",
        "omnigent_external_runs",
        ["status"],
    )

    op.execute(
        """
        INSERT INTO omnigent_external_runs (
            idempotency_key, moonmind_workflow_id, moonmind_agent_run_id,
            correlation_id, omnigent_endpoint_ref, omnigent_session_id,
            omnigent_agent_id, omnigent_agent_name, target_metadata, status,
            first_message_state, first_message_digest, first_message_marker,
            first_message_post_attempted_at, first_message_posted_at,
            first_message_pending_id, first_message_item_id, terminal_refs,
            final_snapshot_ref, sse_events_ref, diagnostics_ref,
            created_at, updated_at
        )
        SELECT
            idempotency_key,
            moonmind_workflow_id,
            moonmind_agent_run_id,
            moonmind_agent_run_id,
            omnigent_endpoint_ref,
            omnigent_session_id,
            omnigent_agent_id,
            omnigent_agent_name,
            metadata,
            status,
            first_message_state,
            first_message_digest,
            first_message_marker,
            first_message_post_attempted_at,
            first_message_posted_at,
            first_message_pending_id,
            first_message_item_id,
            terminal_refs,
            final_snapshot_ref,
            raw_events_ref,
            diagnostics_ref,
            created_at,
            updated_at
        FROM omnigent_bridge_sessions
        """
    )

    op.drop_index(
        "ix_omnigent_bridge_session_events_sequence",
        table_name="omnigent_bridge_session_events",
    )
    op.drop_index(
        "ix_omnigent_bridge_session_events_session",
        table_name="omnigent_bridge_session_events",
    )
    op.drop_table("omnigent_bridge_session_events")
    op.drop_index("ix_omnigent_bridge_sessions_status", table_name="omnigent_bridge_sessions")
    op.drop_index("ix_omnigent_bridge_sessions_workflow", table_name="omnigent_bridge_sessions")
    op.drop_index("ix_omnigent_bridge_sessions_session", table_name="omnigent_bridge_sessions")
    op.drop_table("omnigent_bridge_sessions")
