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

# Alembic discovers the module-level revision attributes above; reference them
# so static analysis does not flag them as unused globals.
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


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
    # Converge v2 rows before restoring the 8-value constraint: PostgreSQL
    # validates existing rows when adding a CHECK, so dropping the permissive
    # constraint while a ``terminal_contract_continuation`` row exists would
    # fail ``alembic downgrade``. A terminal-contract continuation is a bounded
    # same-session continuation like ``repository_continuation``; converge it
    # there rather than leaving a row the restored constraint would reject
    # (mirrors the 366 convergence of retired values to their canonical form).
    op.execute(
        sa.text(
            "UPDATE omnigent_turn_attempts "
            "SET lineage_kind = 'repository_continuation' "
            "WHERE lineage_kind = 'terminal_contract_continuation'"
        )
    )
    op.drop_constraint(_CHECK_NAME, "omnigent_turn_attempts", type_="check")
    op.create_check_constraint(
        _CHECK_NAME, "omnigent_turn_attempts", _allowed_sql(_PREVIOUS_TURN_SOURCES)
    )
