"""Persist exact remediation reviewer approvals.

MoonLadderStudios/MoonMind#3620.

Revision ID: 353_remediation_approvals
Revises: 352_remediation_verification
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "353_remediation_approvals"
down_revision = "352_remediation_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remediation_approvals",
        sa.Column("approval_id", sa.String(255), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("remediation_workflow_id", sa.String(255), nullable=False),
        sa.Column("remediation_run_id", sa.String(64), nullable=False),
        sa.Column("target_workflow_id", sa.String(255), nullable=False),
        sa.Column("target_run_id", sa.String(64), nullable=False),
        sa.Column("action_kind", sa.String(128), nullable=False),
        sa.Column("risk_tier", sa.String(32), nullable=False),
        sa.Column("redacted_parameters", sa.JSON(), nullable=False),
        sa.Column("parameter_digest", sa.String(71), nullable=False),
        sa.Column("authority_binding", sa.JSON(), nullable=False),
        sa.Column("approval_class", sa.String(64), nullable=False),
        sa.Column("reviewer_rule", sa.String(128), nullable=False),
        sa.Column("requesting_actor", sa.String(255), nullable=False),
        sa.Column("decision_actor", sa.String(255), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumption_key", sa.String(255), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["remediation_workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "remediation_workflow_id",
            "idempotency_key",
            name="uq_remediation_approvals_workflow_idempotency",
        ),
    )
    op.create_index(
        "ix_remediation_approvals_target",
        "remediation_approvals",
        ["target_workflow_id"],
    )
    op.create_index(
        "ix_remediation_approvals_status_expiry",
        "remediation_approvals",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_remediation_approvals_status_expiry", table_name="remediation_approvals")
    op.drop_index("ix_remediation_approvals_target", table_name="remediation_approvals")
    op.drop_table("remediation_approvals")
