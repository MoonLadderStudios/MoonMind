from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_remediation_approval_state_migration_adds_and_drops_column(
    monkeypatch,
) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.354_remediation_approvals"
    )
    assert migration.down_revision == "353_omnigent_chat_binding"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE execution_remediation_links "
                "(remediation_workflow_id VARCHAR(255) PRIMARY KEY)"
            )
        )
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )

        migration.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "execution_remediation_links"
            )
        }
        assert "approval_state" in columns

        migration.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "execution_remediation_links"
            )
        }
        assert "approval_state" not in columns
