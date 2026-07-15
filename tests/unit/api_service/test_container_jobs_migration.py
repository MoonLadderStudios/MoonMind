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
            (
                "uq_container_jobs_owner_idempotency",
                ["owner_type", "owner_id", "idempotency_key"],
            )
        ]
        migration.downgrade()
        assert "container_jobs" not in sa.inspect(connection).get_table_names()


def test_registry_authorization_migration_adds_and_drops_column(monkeypatch) -> None:
    base = importlib.import_module(
        "api_service.migrations.versions.338_container_jobs_contract"
    )
    auth = importlib.import_module(
        "api_service.migrations.versions.340_container_job_registry_authorization"
    )
    assert auth.down_revision == "339_merge_migration_heads"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(base, "op", operations)
        monkeypatch.setattr(auth, "op", operations)
        base.upgrade()
        auth.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("container_jobs")
        }
        assert "authorization_observation_json" in columns
        auth.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("container_jobs")
        }
        assert "authorization_observation_json" not in columns


def test_observations_migration_adds_and_drops_columns(monkeypatch) -> None:
    base = importlib.import_module(
        "api_service.migrations.versions.338_container_jobs_contract"
    )
    observations = importlib.import_module(
        "api_service.migrations.versions.341_container_job_observations"
    )
    assert observations.down_revision == "340_container_job_registry_auth"
    added = {
        "events_ref",
        "workspace_probe",
        "started_at",
        "completed_at",
        "duration_seconds",
    }
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(base, "op", operations)
        monkeypatch.setattr(observations, "op", operations)
        base.upgrade()
        observations.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("container_jobs")
        }
        assert added <= columns
        observations.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("container_jobs")
        }
        assert not (added & columns)
