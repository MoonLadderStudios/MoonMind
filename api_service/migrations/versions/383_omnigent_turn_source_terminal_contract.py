"""Admit terminal-contract continuation to the closed turn-source vocabulary.

Revision ID: 383_omnigent_turn_source_terminal_contract
Revises: 382_issue_claim_ownership_end
Create Date: 2026-09-15

MoonLadderStudios/MoonMind#4281 added
``TurnSource.TERMINAL_CONTRACT_CONTINUATION`` (vocabulary v2) without updating
the durable ``CHECK`` constraint from revision 366. Any Skill terminal-contract
continuation then failed with a check violation that the control-plane
repository masked as ``Idempotency key ... already exists``
(``OMNIGENT_GENERIC_DISPATCH_FAILED`` with ``contact_administrator``), stranding
e.g. resolver workflows on their ``:terminal-contract:1`` turn.

MoonMind is pre-release, so the superseded constraint is replaced in place
rather than aliased (Compatibility Policy).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "383_omnigent_turn_source_terminal_contract"
down_revision: Union[str, None] = "382_issue_claim_ownership_end"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TURN_SOURCES = (
    "initial",
    "repository_continuation",
    "terminal_contract_continuation",
    "remediation",
    "workflow_chat",
    "steering",
    "approval_response",
    "checkpoint_resume",
    "linked_branch",
)

_PREVIOUS_TURN_SOURCES = (
    "initial",
    "repository_continuation",
    "remediation",
    "workflow_chat",
    "steering",
    "approval_response",
    "checkpoint_resume",
    "linked_branch",
)

_CHECK_NAME = "ck_omnigent_turn_attempts_lineage_kind"


def _allowed_sql(values: Sequence[str]) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    return f"lineage_kind IN ({listed})"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("omnigent_turn_attempts"):
        return
    op.drop_constraint(_CHECK_NAME, "omnigent_turn_attempts", type_="check")
    op.create_check_constraint(
        _CHECK_NAME, "omnigent_turn_attempts", _allowed_sql(_TURN_SOURCES)
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("omnigent_turn_attempts"):
        return
    op.drop_constraint(_CHECK_NAME, "omnigent_turn_attempts", type_="check")
    op.create_check_constraint(
        _CHECK_NAME, "omnigent_turn_attempts", _allowed_sql(_PREVIOUS_TURN_SOURCES)
    )
