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

    enum_type = MagicMock()
    enum_factory = MagicMock(return_value=enum_type)
    monkeypatch.setattr(migration.postgresql, "ENUM", enum_factory)

    migration.upgrade()

    assert [item.kwargs["name"] for item in enum_factory.call_args_list] == [
        "workflowproposalstatus",
        "workflowproposalpriority",
        "workflowproposaloriginsource",
    ]
    assert enum_type.drop.call_args_list == [
        call(bind, checkfirst=True),
        call(bind, checkfirst=True),
        call(bind, checkfirst=True),
    ]


def test_removal_migration_downgrade_requires_backup_restore() -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.367_remove_workflow_proposals"
    )

    with pytest.raises(RuntimeError, match="restore a pre-upgrade database backup"):
        migration.downgrade()
