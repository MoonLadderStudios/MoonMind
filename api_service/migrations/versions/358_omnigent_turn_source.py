"""Canonical Omnigent turn source and turn-bound immutable execution authority.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane] route all
continuations, remediation, checkpoints, and chat through canonical sessions and
turn attempts).

The free-form ``omnigent_turn_attempts.lineage_kind`` is replaced by
``turn_source``, whose values come from the closed, versioned vocabulary in
``moonmind.omnigent.turn_contracts.OmnigentTurnSource``. Existing rows are mapped
deterministically:

  * ``initial``     -> ``initial``     (unchanged)
  * ``instruction`` -> ``initial``     (the pre-#3707 default for a first turn)
  * ``continuation``-> ``repository_continuation``

Any other historical value is mapped to ``repository_continuation``: it was
written by a same-session follow-up producer, which is exactly what that source
kind means. No historical value maps to a source whose policy would grant
broader authority than the row already had.

Also records the immutable execution authority each turn was admitted against
(``execution_plan_ref``, ``runtime_binding_ref``, ``authority_digest``) plus the
session revision the submitter observed (``expected_session_revision``), so a
changed-authority branch decision is provable from durable evidence rather than
re-derived from live state.

Revision ID: 358_omnigent_turn_source
Revises: 357_omnigent_fencing
Create Date: 2026-08-21

The revision id is kept <= 32 characters so it fits Alembic's
``alembic_version.version_num`` column.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "358_omnigent_turn_source"
down_revision: Union[str, None] = "357_omnigent_fencing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "omnigent_turn_attempts"
_SOURCE_INDEX = "ix_omnigent_turn_attempts_source"


def upgrade() -> None:
    # Rename first, then restate the default on the new name, so the two DDL
    # statements are unambiguous on every supported backend.
    op.alter_column(
        _TABLE,
        "lineage_kind",
        new_column_name="turn_source",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="instruction",
    )
    op.alter_column(
        _TABLE,
        "turn_source",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="initial",
    )
    # Map historical values onto the closed vocabulary. Ordered so the
    # narrower mappings win before the catch-all.
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET turn_source = 'initial' "
            "WHERE turn_source = 'instruction'"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET turn_source = 'repository_continuation' "
            "WHERE turn_source NOT IN ("
            "'initial', 'repository_continuation', 'remediation', "
            "'workflow_chat', 'steering', 'approval_response', "
            "'checkpoint_resume', 'linked_branch')"
        )
    )

    op.add_column(
        _TABLE,
        sa.Column("execution_plan_ref", sa.String(length=255), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("runtime_binding_ref", sa.String(length=255), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("authority_digest", sa.String(length=128), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("expected_session_revision", sa.Integer(), nullable=True),
    )
    op.create_index(_SOURCE_INDEX, _TABLE, ["session_id", "turn_source"])


def downgrade() -> None:
    op.drop_index(_SOURCE_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "expected_session_revision")
    op.drop_column(_TABLE, "authority_digest")
    op.drop_column(_TABLE, "runtime_binding_ref")
    op.drop_column(_TABLE, "execution_plan_ref")
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} SET turn_source = 'continuation' "
            "WHERE turn_source != 'initial'"
        )
    )
    op.alter_column(
        _TABLE,
        "turn_source",
        new_column_name="lineage_kind",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="initial",
    )
    op.alter_column(
        _TABLE,
        "lineage_kind",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default="instruction",
    )
