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


def upgrade() -> None:
    op.drop_table("workflow_proposal_notifications")
    op.drop_table("workflow_proposals")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum_name in _POSTGRES_ENUM_TYPES:
            postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError(
        "367_remove_workflow_proposals cannot restore deleted proposal records; "
        "restore a pre-upgrade database backup instead"
    )
