"""Exercise the static allocation upgrade with retained lease rows."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def migration_database():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    leases = sa.Table(
        "omnigent_oauth_host_leases",
        metadata,
        sa.Column("lease_id", sa.String, primary_key=True),
        sa.Column("provider_lease_id", sa.String, nullable=False),
        sa.Column("idempotency_key", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("cleanup_completed_at", sa.DateTime),
        sa.Column("retained_evidence", sa.String),
        sa.UniqueConstraint(
            "provider_lease_id", name="uq_omnigent_oauth_host_provider_lease"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_oauth_host_idempotency"
        ),
    )
    metadata.create_all(engine)
    path = (
        Path(__file__).resolve().parents[3]
        / "api_service/migrations/versions/409_static_host_allocations.py"
    )
    spec = importlib.util.spec_from_file_location("static_allocation_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            connection.execute(
                leases.insert().values(
                    lease_id="retained",
                    provider_lease_id="grant",
                    idempotency_key="first",
                    status="stopped",
                    retained_evidence="immutable-original",
                )
            )
            yield migration, connection
    finally:
        engine.dispose()


def test_upgrade_preserves_history_and_excludes_concurrent_physical_owners(
    migration_database,
):
    migration, connection = migration_database
    migration.upgrade()
    rows = connection.execute(
        sa.text(
            "SELECT lease_id, retained_evidence, static_scope_ref FROM omnigent_oauth_host_leases"
        )
    ).all()
    assert rows == [("retained", "immutable-original", None)]
    connection.execute(
        sa.text(
            "INSERT INTO omnigent_oauth_host_leases "
            "(lease_id, provider_lease_id, idempotency_key, status, static_scope_ref) "
            "VALUES ('successor','grant','second','ready','physical-host')"
        )
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            sa.text(
                "INSERT INTO omnigent_oauth_host_leases "
                "(lease_id, provider_lease_id, idempotency_key, status, static_scope_ref) "
                "VALUES ('sibling','other-grant','third','allocating','physical-host')"
            )
        )
    with pytest.raises(RuntimeError, match="without losing retained"):
        migration.downgrade()
    assert (
        connection.execute(
            sa.text(
                "SELECT retained_evidence FROM omnigent_oauth_host_leases WHERE lease_id='retained'"
            )
        ).scalar_one()
        == "immutable-original"
    )


def test_compatible_downgrade_retains_rows_and_restores_old_uniqueness(
    migration_database,
):
    migration, connection = migration_database
    migration.upgrade()
    migration.downgrade()
    assert (
        connection.execute(
            sa.text("SELECT retained_evidence FROM omnigent_oauth_host_leases")
        ).scalar_one()
        == "immutable-original"
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            sa.text(
                "INSERT INTO omnigent_oauth_host_leases "
                "(lease_id, provider_lease_id, idempotency_key, status) "
                "VALUES ('other','grant','second','stopped')"
            )
        )
