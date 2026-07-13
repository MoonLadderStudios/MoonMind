from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_container_jobs_migration_upgrade_and_downgrade(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.338_container_jobs_contract"
    )
    assert migration.down_revision == "337_mm1207_oauth_hosts"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        inspector = sa.inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("container_jobs")}
        assert {"job_id", "owner_id", "idempotency_key", "request_json", "state"} <= columns
        assert {index["name"] for index in inspector.get_indexes("container_jobs")} == {
            "ix_container_jobs_owner_created"
        }
        unique = inspector.get_unique_constraints("container_jobs")
        assert [(item["name"], item["column_names"]) for item in unique] == [
            ("uq_container_jobs_owner_idempotency", ["owner_id", "idempotency_key"])
        ]
        migration.downgrade()
        assert "container_jobs" not in sa.inspect(connection).get_table_names()
