"""Migration coverage for MoonLadderStudios/MoonMind#3701."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, call


def test_session_execution_authority_migration_upgrade_and_downgrade(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.360_omnigent_session_execution_authority"
    )
    assert migration.down_revision == "359_generic_host"
    operations = MagicMock()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert [item.args[1].name for item in operations.add_column.call_args_list] == [
        "execution_plan_ref",
        "runtime_binding_ref",
    ]
    operations.create_index.assert_has_calls(
        [
            call(
                "ix_omnigent_sessions_execution_plan",
                "omnigent_sessions",
                ["execution_plan_ref"],
            ),
            call(
                "ix_omnigent_sessions_runtime_binding",
                "omnigent_sessions",
                ["runtime_binding_ref"],
            ),
        ]
    )
    assert operations.create_foreign_key.call_count == 2
    operations.create_foreign_key.assert_any_call(
        "fk_omnigent_sessions_runtime_binding",
        "omnigent_sessions",
        "omnigent_runtime_bindings",
        ["runtime_binding_ref"],
        ["binding_id"],
        ondelete="RESTRICT",
    )

    operations.reset_mock()
    migration.downgrade()

    assert operations.drop_constraint.call_count == 2
    operations.drop_index.assert_has_calls(
        [
            call(
                "ix_omnigent_sessions_runtime_binding",
                table_name="omnigent_sessions",
            ),
            call(
                "ix_omnigent_sessions_execution_plan",
                table_name="omnigent_sessions",
            ),
        ]
    )
    operations.drop_column.assert_has_calls(
        [
            call("omnigent_sessions", "runtime_binding_ref"),
            call("omnigent_sessions", "execution_plan_ref"),
        ]
    )


def test_execution_scoped_runtime_authority_migration(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.361_omnigent_execution_authority"
    )
    assert migration.down_revision == "360_omnigent_authority"
    operations = MagicMock()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert [item.args[1].name for item in operations.add_column.call_args_list] == [
        "execution_scope_ref",
        "runner_ref",
        "chat_binding_ref",
    ]
    operations.create_unique_constraint.assert_called_once_with(
        "uq_omnigent_runtime_binding_plan_scope",
        "omnigent_runtime_bindings",
        ["execution_plan_ref", "execution_scope_ref"],
    )

    operations.reset_mock()
    migration.downgrade()

    operations.drop_constraint.assert_called_once_with(
        "uq_omnigent_runtime_binding_plan_scope",
        "omnigent_runtime_bindings",
        type_="unique",
    )
    assert [item.args[1] for item in operations.drop_column.call_args_list] == [
        "chat_binding_ref",
        "runner_ref",
        "execution_scope_ref",
    ]
