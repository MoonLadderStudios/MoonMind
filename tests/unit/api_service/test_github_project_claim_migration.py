"""Project receipts upgrade without changing existing issue ownership."""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_preserves_claims_and_downgrade_preserves_project_evidence(monkeypatch):
    migration = importlib.import_module(
        "api_service.migrations.versions.410_github_project_claims"
    )
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE github_issue_claims (owner TEXT PRIMARY KEY, issue_number INTEGER, released BOOLEAN)"
        )
        connection.exec_driver_sql(
            "INSERT INTO github_issue_claims VALUES ('active', 7, 0)"
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        assert connection.exec_driver_sql(
            "SELECT owner, issue_number, released, project_binding_json FROM github_issue_claims"
        ).one() == ("active", 7, 0, None)
        connection.execute(
            sa.text("UPDATE github_issue_claims SET project_binding_json = :binding"),
            {"binding": '{"itemId":"saved"}'},
        )
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
        assert (
            connection.exec_driver_sql(
                "SELECT project_binding_json FROM github_issue_claims"
            ).scalar()
            == '{"itemId":"saved"}'
        )
    engine.dispose()
