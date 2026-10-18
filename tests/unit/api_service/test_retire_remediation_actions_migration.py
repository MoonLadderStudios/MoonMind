from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

RETIRED_COLUMNS = {
    "active_lock_scope",
    "active_lock_holder",
    "outcome",
    "verification_outcome",
    "resolution",
    "mutation_guard_lock_state",
    "mutation_guard_ledger_state",
    "approval_state",
}


def _columns(connection) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(connection).get_columns("execution_remediation_links")
    }


def test_retire_remediation_actions_drops_only_typed_action_columns(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.396_retire_remediation_actions"
    )
    assert migration.down_revision == "395_remediation_link_target"
    assert len(migration.revision) <= 32
    config = Config("api_service/migrations/alembic.ini")
    config.set_main_option("script_location", "api_service/migrations")
    assert tuple(ScriptDirectory.from_config(config).get_heads()) == (
        migration.revision,
    )

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE execution_remediation_links ("
                "remediation_workflow_id VARCHAR(255) PRIMARY KEY, "
                "latest_action_summary TEXT, "
                + ", ".join(f"{name} TEXT" for name in sorted(RETIRED_COLUMNS))
                + ")"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO execution_remediation_links "
                "(remediation_workflow_id, latest_action_summary, approval_state) "
                "VALUES ('mm:remediation', 'Created checkpoint branch b1.', '{}')"
            )
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        assert _columns(connection) == {
            "remediation_workflow_id",
            "latest_action_summary",
        }
        # The link and the corrective-branch summary it still records survive.
        assert connection.execute(
            sa.text(
                "SELECT remediation_workflow_id, latest_action_summary "
                "FROM execution_remediation_links"
            )
        ).all() == [("mm:remediation", "Created checkpoint branch b1.")]

        migration.downgrade()
        assert RETIRED_COLUMNS <= _columns(connection)
