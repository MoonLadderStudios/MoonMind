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


def test_gpu_observation_migration_adds_and_drops_column(monkeypatch) -> None:
    base = importlib.import_module(
        "api_service.migrations.versions.338_container_jobs_contract"
    )
    gpu = importlib.import_module(
        "api_service.migrations.versions.364_container_job_gpu_observation"
    )
    assert gpu.down_revision == "363_merge_automation_reviews"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(base, "op", operations)
        monkeypatch.setattr(gpu, "op", operations)
        base.upgrade()
        gpu.upgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("container_jobs")
        }
        assert "gpu_observation_json" in columns
        gpu.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("container_jobs")
        }
        assert "gpu_observation_json" not in columns


def test_assessment_authority_migration_retains_reservations(monkeypatch):
    import pytest

    from api_service.db.models import Base

    migration = importlib.import_module(
        "api_service.migrations.versions.411_assessment_job_authority"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        # Exercise the real predecessor shape rather than create_all with new
        # columns already present.
        Base.metadata.tables["settings_overrides"].create(connection)
        connection.execute(
            sa.text(
                "CREATE TABLE container_jobs (job_id TEXT PRIMARY KEY, request_json JSON)"
            )
        )
        connection.execute(
            sa.text("INSERT INTO container_jobs VALUES ('retained', '{}')")
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        row = connection.execute(
            sa.text(
                "SELECT assessment_scope_json, assessment_deadline_at, assessment_review_json FROM container_jobs"
            )
        ).one()
        assert row == (None, None, None)
        assert (
            connection.execute(
                sa.text(
                    "SELECT value_json FROM settings_overrides WHERE key='security.assessment_authorizations'"
                )
            ).scalar()
            == '{"authorizations": []}'
        )
        connection.execute(
            sa.text(
                'UPDATE container_jobs SET assessment_scope_json = \'{"assessmentRef":"saved"}\''
            )
        )
        with pytest.raises(RuntimeError, match="repair forward"):
            migration.downgrade()
        assert (
            connection.execute(
                sa.text("SELECT assessment_scope_json FROM container_jobs")
            ).scalar()
            is not None
        )
        # A retained readable review must also prevent destructive downgrade,
        # even if the corresponding scope cannot currently be recovered.
        connection.execute(
            sa.text(
                "UPDATE container_jobs SET assessment_scope_json = NULL, "
                "assessment_review_json = '{\"state\":\"retained\"}'"
            )
        )
        with pytest.raises(RuntimeError, match="repair forward"):
            migration.downgrade()
        assert (
            connection.execute(
                sa.text("SELECT assessment_review_json FROM container_jobs")
            ).scalar()
            is not None
        )
