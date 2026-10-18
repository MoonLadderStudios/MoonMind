"""Drop the remediation link columns that only typed remediation actions used.

A remediation no longer acts on its target, so nothing reads or writes the
approval, mutation-lock, action-ledger, delivery, verification, or resolution
state that typed actions kept on ``execution_remediation_links``. The action,
approval, and verification records those actions produced were published as
``remediation.*`` artifacts, which stay readable; these columns held only
in-flight state and projections of them. ``latest_action_summary`` stays
because creating a corrective checkpoint branch still records it.

Downgrade restores the columns empty.

Revision ID: 396_retire_remediation_actions
Revises: 395_remediation_link_target
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "396_retire_remediation_actions"
down_revision: Union[str, None] = "395_remediation_link_target"
branch_labels = None
depends_on = None

_TABLE = "execution_remediation_links"


def _jsonb() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _retired_columns() -> list[sa.Column]:
    return [
        sa.Column("active_lock_scope", sa.String(length=64), nullable=True),
        sa.Column("active_lock_holder", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("verification_outcome", sa.String(length=64), nullable=True),
        sa.Column("resolution", sa.String(length=64), nullable=True),
        sa.Column("mutation_guard_lock_state", _jsonb(), nullable=True),
        sa.Column("mutation_guard_ledger_state", _jsonb(), nullable=True),
        sa.Column("approval_state", sa.JSON(), nullable=True),
    ]


def upgrade() -> None:
    for column in _retired_columns():
        op.drop_column(_TABLE, column.name)


def downgrade() -> None:
    for column in _retired_columns():
        op.add_column(_TABLE, column)
