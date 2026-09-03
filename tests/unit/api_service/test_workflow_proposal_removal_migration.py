"""Unit coverage for the workflow proposal removal migration."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, call

import pytest


def test_removal_migration_drops_dependents_before_parent(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.367_remove_workflow_proposals"
    )
    assert migration.down_revision == "366_omnigent_turn_source"

    operations = MagicMock()
    operations.get_bind.return_value.dialect.name = "sqlite"
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert operations.drop_table.call_args_list == [
        call("workflow_proposal_notifications"),
        call("workflow_proposals"),
    ]


def test_removal_migration_drops_postgres_enum_types(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.367_remove_workflow_proposals"
    )
    operations = MagicMock()
    bind = operations.get_bind.return_value
    bind.dialect.name = "postgresql"
    monkeypatch.setattr(migration, "op", operations)

    enum_types: dict[str, MagicMock] = {}

    def enum_factory(*values, name: str):
        enum_type = enum_types.setdefault(name, MagicMock())
        enum_type.values = values
        return enum_type

    monkeypatch.setattr(migration.postgresql, "ENUM", enum_factory)

    migration.upgrade()

    assert (
        enum_types["moonmindworkflowstate"].values == migration._WORKFLOW_STATE_VALUES
    )
    enum_types["moonmindworkflowstate"].create.assert_called_once_with(
        bind, checkfirst=False
    )
    enum_types["moonmindworkflowstate_with_proposals"].drop.assert_called_once_with(
        bind, checkfirst=False
    )
    for enum_name in [
        "workflowproposalstatus",
        "workflowproposalpriority",
        "workflowproposaloriginsource",
    ]:
        enum_types[enum_name].drop.assert_called_once_with(bind, checkfirst=True)

    executed_sql = [str(item.args[0]) for item in operations.execute.call_args_list]
    assert executed_sql[:4] == [
        "UPDATE temporal_execution_sources SET state = 'finalizing' WHERE state = 'proposals'",
        "ALTER TABLE temporal_execution_sources ALTER COLUMN state DROP DEFAULT",
        "UPDATE temporal_executions SET state = 'finalizing' WHERE state = 'proposals'",
        "ALTER TABLE temporal_executions ALTER COLUMN state DROP DEFAULT",
    ]
    assert (
        "ALTER TYPE moonmindworkflowstate RENAME TO moonmindworkflowstate_with_proposals"
        in executed_sql
    )


def test_removal_migration_downgrade_requires_backup_restore() -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.367_remove_workflow_proposals"
    )

    with pytest.raises(RuntimeError, match="restore a pre-upgrade database backup"):
        migration.downgrade()
