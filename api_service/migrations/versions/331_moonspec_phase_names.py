"""Rename workflow automation phases to MoonSpec names.

Revision ID: 331_moonspec_phase_names
Revises: 330_mm992_omnigent_external_runs
Create Date: 2026-06-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "331_moonspec_phase_names"
down_revision: Union[str, None] = "330_mm992_omnigent_external_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPE_NAME = "specautomationphase"
_RENAMES = (
    ("speckit_specify", "moonspec_specify"),
    ("speckit_plan", "moonspec_plan"),
    ("speckit_tasks", "moonspec_tasks"),
    ("speckit_analyze", "moonspec_align"),
    ("speckit_implement", "moonspec_implement"),
)
_NEW_PHASES = (
    "moonspec_verify",
    "moonspec_doc_reconcile",
    "moonspec_orchestrate",
)


def _enum_labels(connection: sa.Connection) -> set[str]:
    rows = connection.execute(
        sa.text(
            """
            select enumlabel
            from pg_enum
            join pg_type on pg_enum.enumtypid = pg_type.oid
            where pg_type.typname = :type_name
            """
        ),
        {"type_name": _TYPE_NAME},
    )
    return {str(row[0]) for row in rows}


def _rename_enum_value_if_needed(
    connection: sa.Connection,
    *,
    old_value: str,
    new_value: str,
) -> None:
    labels = _enum_labels(connection)
    if old_value not in labels or new_value in labels:
        return
    op.execute(
        sa.text(
            f"alter type {_TYPE_NAME} rename value "
            f"{old_value!r} to {new_value!r}"
        )
    )


def _add_enum_value_if_needed(connection: sa.Connection, value: str) -> None:
    if value in _enum_labels(connection):
        return
    op.execute(sa.text(f"alter type {_TYPE_NAME} add value {value!r}"))


def _upgrade_postgresql(connection: sa.Connection) -> None:
    for old_value, new_value in _RENAMES:
        _rename_enum_value_if_needed(
            connection,
            old_value=old_value,
            new_value=new_value,
        )
    for value in _NEW_PHASES:
        _add_enum_value_if_needed(connection, value)


def _upgrade_sqlite(connection: sa.Connection) -> None:
    for old_value, new_value in _RENAMES:
        for table_name, column_name in (
            ("automation_task_states", "phase"),
            ("automation_artifacts", "source_phase"),
        ):
            connection.execute(
                sa.text(
                    f"update {table_name} set {column_name} = :new_value "
                    f"where {column_name} = :old_value"
                ),
                {"old_value": old_value, "new_value": new_value},
            )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        _upgrade_postgresql(connection)
    elif connection.dialect.name == "sqlite":
        _upgrade_sqlite(connection)


def downgrade() -> None:
    # Pre-release hard rename. Downgrading would reintroduce the old active
    # phase identity while current code writes only MoonSpec phase values.
    return
