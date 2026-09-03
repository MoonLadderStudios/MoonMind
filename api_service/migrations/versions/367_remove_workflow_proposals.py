"""Remove the retired follow-up workflow proposal persistence model.

Revision ID: 367_remove_workflow_proposals
Revises: 366_omnigent_turn_source
Create Date: 2026-09-03

This migration is intentionally irreversible. Operators must inventory and
export retained records before upgrading; recreating empty tables on downgrade
would imply that the deleted review and provider-delivery evidence was restored.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "367_remove_workflow_proposals"
down_revision: Union[str, None] = "366_omnigent_turn_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_POSTGRES_ENUM_TYPES = (
    "workflowproposalstatus",
    "workflowproposalpriority",
    "workflowproposaloriginsource",
)
_WORKFLOW_STATE_ENUM = "moonmindworkflowstate"
_RETIRED_WORKFLOW_STATE_ENUM = "moonmindworkflowstate_with_proposals"
_WORKFLOW_STATE_TABLES = (
    "temporal_execution_sources",
    "temporal_executions",
)
_WORKFLOW_STATE_VALUES = (
    "scheduled",
    "initializing",
    "waiting_on_dependencies",
    "planning",
    "awaiting_slot",
    "executing",
    "awaiting_external",
    "finalizing",
    "no_commit",
    "completed",
    "failed",
    "canceled",
)


def _remove_proposals_workflow_state(bind: sa.Connection) -> None:
    for table_name in _WORKFLOW_STATE_TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET state = 'finalizing' "
                "WHERE state = 'proposals'"
            )
        )
        op.execute(sa.text(f"ALTER TABLE {table_name} ALTER COLUMN state DROP DEFAULT"))

    op.execute(
        sa.text(
            f"ALTER TYPE {_WORKFLOW_STATE_ENUM} "
            f"RENAME TO {_RETIRED_WORKFLOW_STATE_ENUM}"
        )
    )
    current_enum = postgresql.ENUM(
        *_WORKFLOW_STATE_VALUES,
        name=_WORKFLOW_STATE_ENUM,
    )
    current_enum.create(bind, checkfirst=False)

    for table_name in _WORKFLOW_STATE_TABLES:
        op.execute(
            sa.text(
                f"ALTER TABLE {table_name} ALTER COLUMN state "
                f"TYPE {_WORKFLOW_STATE_ENUM} "
                f"USING state::text::{_WORKFLOW_STATE_ENUM}"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {table_name} ALTER COLUMN state "
                "SET DEFAULT 'initializing'"
            )
        )

    postgresql.ENUM(name=_RETIRED_WORKFLOW_STATE_ENUM).drop(bind, checkfirst=False)


def upgrade() -> None:
    op.drop_table("workflow_proposal_notifications")
    op.drop_table("workflow_proposals")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _remove_proposals_workflow_state(bind)
        for enum_name in _POSTGRES_ENUM_TYPES:
            postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError(
        "367_remove_workflow_proposals cannot restore deleted proposal records; "
        "restore a pre-upgrade database backup instead"
    )
