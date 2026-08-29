"""Close the Omnigent canonical turn-source vocabulary.

``omnigent_turn_attempts.lineage_kind`` was a free-form 32-character column
defaulting to ``instruction`` and populated by substring matching on the command
type. MoonLadderStudios/MoonMind#3707 replaces that with one closed, versioned
source vocabulary:

    initial, repository_continuation, remediation, workflow_chat, steering,
    approval_response, checkpoint_resume, linked_branch

MoonMind is pre-release, so the superseded values are rewritten in place rather
than aliased (Compatibility Policy):

    instruction  -> initial
    continuation -> repository_continuation
    approval     -> approval_response

A CHECK constraint makes the vocabulary closed at the durable boundary, so no
future writer can reintroduce a free-form lineage value.

Revision ID: 366_omnigent_turn_source
Revises: 365_profile_tier_provenance
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "366_omnigent_turn_source"
down_revision: Union[str, None] = "365_profile_tier_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TURN_SOURCES = (
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

_RETIRED_TO_CANONICAL = {
    "instruction": "initial",
    "continuation": "repository_continuation",
    "approval": "approval_response",
}


def _allowed_sql() -> str:
    values = ", ".join(f"'{value}'" for value in _TURN_SOURCES)
    return f"lineage_kind IN ({values})"


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("omnigent_turn_attempts"):
        return
    for retired, canonical in _RETIRED_TO_CANONICAL.items():
        op.execute(
            sa.text(
                "UPDATE omnigent_turn_attempts SET lineage_kind = :canonical "
                "WHERE lineage_kind = :retired"
            ).bindparams(canonical=canonical, retired=retired)
        )
    # Any value still outside the closed vocabulary predates the canonical turn
    # boundary and is an ordinary instruction; converge it rather than leaving a
    # row the CHECK constraint would reject.
    op.execute(
        sa.text(
            "UPDATE omnigent_turn_attempts SET lineage_kind = 'initial' "
            f"WHERE NOT ({_allowed_sql()})"
        )
    )
    op.alter_column(
        "omnigent_turn_attempts",
        "lineage_kind",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="initial",
    )
    op.create_check_constraint(
        _CHECK_NAME, "omnigent_turn_attempts", _allowed_sql()
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("omnigent_turn_attempts"):
        return
    op.drop_constraint(_CHECK_NAME, "omnigent_turn_attempts", type_="check")
    op.alter_column(
        "omnigent_turn_attempts",
        "lineage_kind",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="instruction",
    )
