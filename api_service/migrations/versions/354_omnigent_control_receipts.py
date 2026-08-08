"""Durable per-mutation control receipts for native Omnigent chat.

Issue MoonLadderStudios/MoonMind#3636: persist a versioned request/result
receipt for every mutating native Omnigent chat/control/approval/terminal/
workspace request. The receipt carries actor, operation, MoonMind request id and
idempotency key, server-side Workflow/run/Step/AgentRun/bridge/provider session
identity, expected-state compare-and-set fields, immutable Agent Profile /
Provider Profile generation / launch-policy / policy-snapshot refs and digests,
timings, normalized outcome, stable reason code, upstream correlation, and a
durable audit ref.

The idempotency key is unique so a duplicate request returns the prior result
and cannot duplicate the provider side effect; ``delivery_unknown`` outcomes are
reconciled onto the same row.

Revision ID: 354_omnigent_control_receipts
Revises: 353_omnigent_chat_binding
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "354_omnigent_control_receipts"
down_revision = "353_omnigent_chat_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "omnigent_control_receipts",
        sa.Column("receipt_id", sa.String(length=255), primary_key=True),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("actor_principal", sa.String(length=255), nullable=False),
        sa.Column("control_type", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column("run_id", sa.String(length=255), nullable=True),
        sa.Column("step_execution_id", sa.String(length=255), nullable=True),
        sa.Column("agent_run_id", sa.String(length=255), nullable=True),
        sa.Column("bridge_session_id", sa.String(length=255), nullable=True),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("expected_session_epoch", sa.Integer(), nullable=True),
        sa.Column("expected_turn_id", sa.String(length=255), nullable=True),
        sa.Column("expected_elicitation_id", sa.String(length=255), nullable=True),
        sa.Column("agent_profile_digest", sa.String(length=128), nullable=True),
        sa.Column("provider_profile_generation", sa.Integer(), nullable=True),
        sa.Column("launch_policy_ref", sa.String(length=255), nullable=True),
        sa.Column("launch_snapshot_ref", sa.String(length=255), nullable=True),
        sa.Column("policy_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("stable_reason_code", sa.String(length=96), nullable=True),
        sa.Column("upstream_correlation", sa.String(length=255), nullable=True),
        sa.Column(
            "result_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("audit_artifact_ref", sa.String(length=1024), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_control_receipts_idempotency_key"
        ),
    )
    op.create_index(
        "ix_omnigent_control_receipts_workflow",
        "omnigent_control_receipts",
        ["workflow_id"],
    )
    op.create_index(
        "ix_omnigent_control_receipts_bridge_session",
        "omnigent_control_receipts",
        ["bridge_session_id"],
    )
    op.create_index(
        "ix_omnigent_control_receipts_agent_run",
        "omnigent_control_receipts",
        ["agent_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_omnigent_control_receipts_agent_run",
        table_name="omnigent_control_receipts",
    )
    op.drop_index(
        "ix_omnigent_control_receipts_bridge_session",
        table_name="omnigent_control_receipts",
    )
    op.drop_index(
        "ix_omnigent_control_receipts_workflow",
        table_name="omnigent_control_receipts",
    )
    op.drop_table("omnigent_control_receipts")
