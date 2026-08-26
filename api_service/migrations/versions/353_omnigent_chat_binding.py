"""Add opaque Workflow Chat binding identity to bridge sessions.

Issue MoonLadderStudios/MoonMind#3633: persist an opaque, unguessable
``chat_binding_id`` on the canonical ``omnigent_bridge_sessions`` store so one
visible Workflow Execution maps to exactly one server-owned Omnigent session
through a browser-safe handle. The id is kept distinct from
``bridge_session_id``, ``idempotency_key``, ``moonmind_workflow_id``,
``moonmind_run_id``, ``moonmind_agent_run_id``, and the provider session id.

Backfill policy (OmnigentBridge.md §7.1/§8.2 step 7): the column is nullable and
no row is rewritten by this migration. Historical rows keep ``NULL`` and are
backfilled deterministically on the next authoritative touch — ``session.created``
persistence or the first chat-binding resolution allocates the id idempotently
only once the durable Workflow-to-provider session binding exists. This keeps
Temporal replay compatibility (no persisted-payload shape changes, no workflow
identity change) and does not break historical projections; the unique index
treats ``NULL`` as distinct so un-backfilled rows never collide.

Revision ID: 353_omnigent_chat_binding
Revises: 352_remediation_verification
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "353_omnigent_chat_binding"
down_revision = "352_remediation_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "omnigent_bridge_sessions",
        sa.Column("chat_binding_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_omnigent_bridge_sessions_chat_binding",
        "omnigent_bridge_sessions",
        ["chat_binding_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_omnigent_bridge_sessions_chat_binding",
        table_name="omnigent_bridge_sessions",
    )
    op.drop_column("omnigent_bridge_sessions", "chat_binding_id")
