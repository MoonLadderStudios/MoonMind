"""Add Omnigent external run retry mapping for MM-992.

Revision ID: 330_mm992_omnigent_external_runs
Revises: 329_mm901_name_only_agent_skills
Create Date: 2026-06-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "330_mm992_omnigent_external_runs"
down_revision: Union[str, None] = "329_mm901_name_only_agent_skills"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_omnigent_external_runs_status", table_name="omnigent_external_runs")
    op.drop_index("ix_omnigent_external_runs_session", table_name="omnigent_external_runs")
    op.drop_table("omnigent_external_runs")
