"""Backfill uniqueness for task template recents upsert path."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202602190001"
down_revision: Union[str, None] = "202602180004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_NAME = "task_step_template_recents"
_CONSTRAINT_NAME = "uq_task_template_recent_user_version"


def _has_table(bind: sa.engine.Connection, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in set(inspector.get_table_names())


def _has_unique_constraint(
    bind: sa.engine.Connection,
    table_name: str,
    constraint_name: str,
) -> bool:
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints(table_name):
        if constraint.get("name") == constraint_name:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE_NAME):
        return
    if _has_unique_constraint(bind, _TABLE_NAME, _CONSTRAINT_NAME):
        return

    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY user_id, template_version_id
                        ORDER BY applied_at DESC, id DESC
                    ) AS row_num
                FROM task_step_template_recents
            )
            DELETE FROM task_step_template_recents recents
            USING ranked
            WHERE recents.id = ranked.id
              AND ranked.row_num > 1
            """
        )
    )
    op.create_unique_constraint(
        _CONSTRAINT_NAME,
        _TABLE_NAME,
        ["user_id", "template_version_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE_NAME):
        return
    if not _has_unique_constraint(bind, _TABLE_NAME, _CONSTRAINT_NAME):
        return
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE_NAME, type_="unique")
