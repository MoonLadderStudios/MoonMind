"""Convert legacy task source mappings to workflow execution source mappings.

Revision ID: 312_workflow_execution_source_mapping_cutover
Revises: 311_proposal_delivery_records
Create Date: 2026-05-21

"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "312_workflow_execution_source_mapping_cutover"
down_revision: Union[str, None] = "311_proposal_delivery_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_TABLE = "task_source_mappings"
_WORKFLOW_TABLE = "workflow_execution_source_mappings"


def _workflow_source_mapping_table(metadata: sa.MetaData) -> sa.Table:
    table = sa.Table(
        _WORKFLOW_TABLE,
        metadata,
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("entry", sa.String(length=32), nullable=True),
        sa.Column("source_record_id", sa.String(length=128), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=True),
        sa.Column("owner_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("workflow_id"),
    )
    sa.Index(
        "ix_workflow_execution_source_mappings_source_entry",
        table.c.source,
        table.c.entry,
    )
    sa.Index(
        "ix_workflow_execution_source_mappings_source_record_id",
        table.c.source,
        table.c.source_record_id,
    )
    return table


def _coerce_required(value: Any, *, legacy_column: str, workflow_id: str | None = None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        target = f" for workflow_id {workflow_id!r}" if workflow_id else ""
        raise RuntimeError(
            f"task_source_mappings contains a row with blank {legacy_column}{target}; "
            "cleanup required before workflow execution source mapping cutover"
        )
    return normalized


def _prepare_legacy_source_mapping_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, row in enumerate(rows, start=1):
        workflow_id = str(row.get("task_id") or "").strip()
        if not workflow_id:
            raise RuntimeError(
                f"task_source_mappings contains {index} row(s) before a blank task_id; "
                "cleanup required because a valid workflow_id cannot be established"
            )
        if workflow_id in seen:
            raise RuntimeError(
                f"task_source_mappings contains duplicate workflow_id {workflow_id!r}; "
                "cleanup required before workflow execution source mapping cutover"
            )
        seen.add(workflow_id)
        prepared.append(
            {
                "workflow_id": workflow_id,
                "source": _coerce_required(
                    row.get("source"),
                    legacy_column="source",
                    workflow_id=workflow_id,
                ),
                "entry": (str(row.get("entry")).strip() if row.get("entry") is not None else None) or None,
                "source_record_id": _coerce_required(
                    row.get("source_record_id"),
                    legacy_column="source_record_id",
                    workflow_id=workflow_id,
                ),
                "owner_type": (
                    str(row.get("owner_type")).strip()
                    if row.get("owner_type") is not None
                    else None
                )
                or None,
                "owner_id": (
                    str(row.get("owner_id")).strip()
                    if row.get("owner_id") is not None
                    else None
                )
                or None,
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )

    return prepared


def _table_names(connection: sa.Connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def _ensure_workflow_source_mapping_table(connection: sa.Connection) -> None:
    if _WORKFLOW_TABLE in _table_names(connection):
        return
    metadata = sa.MetaData()
    _workflow_source_mapping_table(metadata)
    metadata.create_all(bind=connection)


def _assert_no_conflicting_current_rows(
    connection: sa.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> None:
    for row in rows:
        existing = connection.execute(
            sa.text(
                "SELECT workflow_id, source, entry, source_record_id, owner_type, owner_id "
                f"FROM {_WORKFLOW_TABLE} WHERE workflow_id = :workflow_id"
            ),
            {"workflow_id": row["workflow_id"]},
        ).mappings().first()
        if existing is None:
            continue
        comparable = {
            "workflow_id": existing["workflow_id"],
            "source": existing["source"],
            "entry": existing["entry"],
            "source_record_id": existing["source_record_id"],
            "owner_type": existing["owner_type"],
            "owner_id": existing["owner_id"],
        }
        expected = {
            "workflow_id": row["workflow_id"],
            "source": row["source"],
            "entry": row["entry"],
            "source_record_id": row["source_record_id"],
            "owner_type": row["owner_type"],
            "owner_id": row["owner_id"],
        }
        if comparable != expected:
            raise RuntimeError(
                f"task_source_mappings row for workflow_id {row['workflow_id']!r} "
                "conflicts with an existing workflow_execution_source_mappings row; "
                "cleanup required before cutover"
            )


def _insert_workflow_source_mapping_rows(
    connection: sa.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> None:
    for row in rows:
        connection.execute(
            sa.text(
                f"INSERT INTO {_WORKFLOW_TABLE} "
                "(workflow_id, source, entry, source_record_id, owner_type, owner_id, created_at, updated_at) "
                "SELECT :workflow_id, :source, :entry, :source_record_id, :owner_type, :owner_id, "
                "COALESCE(:created_at, CURRENT_TIMESTAMP), COALESCE(:updated_at, CURRENT_TIMESTAMP) "
                f"WHERE NOT EXISTS (SELECT 1 FROM {_WORKFLOW_TABLE} WHERE workflow_id = :workflow_id)"
            ),
            dict(row),
        )


def _migrate_legacy_task_source_mappings(connection: sa.Connection) -> None:
    if _LEGACY_TABLE not in _table_names(connection):
        return

    legacy_rows = connection.execute(
        sa.text(
            "SELECT task_id, source, entry, source_record_id, owner_type, owner_id, created_at, updated_at "
            f"FROM {_LEGACY_TABLE} ORDER BY task_id"
        )
    ).mappings().all()
    prepared_rows = _prepare_legacy_source_mapping_rows(legacy_rows)

    _ensure_workflow_source_mapping_table(connection)
    _assert_no_conflicting_current_rows(connection, prepared_rows)
    _insert_workflow_source_mapping_rows(connection, prepared_rows)
    connection.execute(sa.text(f"DROP TABLE {_LEGACY_TABLE}"))


def upgrade() -> None:
    _migrate_legacy_task_source_mappings(op.get_bind())


def downgrade() -> None:
    # The cutover intentionally removes the legacy task-shaped table. Recreating
    # it would reintroduce the superseded internal contract and cannot restore
    # dropped legacy-only metadata.
    raise NotImplementedError(
        "workflow execution source mapping cutover cannot be downgraded to task_source_mappings"
    )
