"""Persist exact, expiring remediation reviewer approvals.

Issue MoonLadderStudios/MoonMind#3620.

Revision ID: 354_remediation_approvals
Revises: 353_omnigent_chat_binding
Create Date: 2026-08-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "354_remediation_approvals"
down_revision = "353_omnigent_chat_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remediation_approvals",
        sa.Column("approval_id", sa.String(255), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("remediation_workflow_id", sa.String(255), nullable=False),
        sa.Column("remediation_run_id", sa.String(64), nullable=False),
        sa.Column("target_workflow_id", sa.String(255), nullable=False),
        sa.Column("target_run_id", sa.String(64), nullable=False),
        sa.Column("action_kind", sa.String(128), nullable=False),
        sa.Column("risk_tier", sa.String(32), nullable=False),
        sa.Column("redacted_parameters", sa.JSON(), nullable=False),
        sa.Column("parameter_digest", sa.String(64), nullable=False),
        sa.Column("authority_binding", sa.JSON(), nullable=False),
        sa.Column("approval_class", sa.String(64), nullable=False),
        sa.Column("reviewer_rule", sa.String(128), nullable=False),
        sa.Column("requesting_actor", sa.String(255), nullable=False),
        sa.Column("decision_actor", sa.String(255)),
        sa.Column("rationale", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("action_artifact_ref", sa.String(255)),
        sa.Column("audit_artifact_ref", sa.String(255)),
        sa.Column("verification_artifact_ref", sa.String(255)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_by_action_id", sa.String(255)),
        sa.ForeignKeyConstraint(["remediation_workflow_id"], ["execution_remediation_links.remediation_workflow_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("remediation_workflow_id", "idempotency_key", name="uq_remediation_approvals_workflow_idempotency"),
    )
    op.create_index("ix_remediation_approvals_target", "remediation_approvals", ["target_workflow_id", "target_run_id"])


def downgrade() -> None:
    op.drop_index("ix_remediation_approvals_target", table_name="remediation_approvals")
    op.drop_table("remediation_approvals")
