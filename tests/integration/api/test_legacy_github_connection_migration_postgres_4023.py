"""Real-PostgreSQL evidence for the #4023 legacy GitHub credential migration.

The SQLite suite cannot prove the PostgreSQL-specific paths: ``ON CONFLICT``
inserts inside one transaction, the ``secretstatus`` enum comparison, and JSON
bindings through the synchronous driver Alembic uses in production.
"""

from __future__ import annotations

import importlib
import uuid

import pytest
import sqlalchemy as sa

from api_service.db.models import Base

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

MIGRATION = "api_service.migrations.versions.391_legacy_github_cred_4023"
DEFAULT_REF = "repository-connection:git-default"
_TABLES = (
    "managed_secrets",
    "repository_connection_records",
    "repository_connection_assignments",
    "repository_route_defaults",
    "repository_connection_audit_events",
)


@pytest.fixture
def pg_engine(control_plane_postgres_url):
    url = control_plane_postgres_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://", 1
    )
    engine = sa.create_engine(url)
    tables = [Base.metadata.tables[name] for name in _TABLES]
    Base.metadata.create_all(engine, tables=tables)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine, tables=tables)
        engine.dispose()


def _add_managed_secret(engine, slug: str, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO managed_secrets (id, slug, ciphertext, status, details) "
                "VALUES (:id, :slug, 'opaque', CAST(:status AS secretstatus), '{}')"
            ),
            {"id": uuid.uuid4(), "slug": slug, "status": status},
        )


def test_postgres_migration_maps_reruns_and_preserves_newer_work(pg_engine) -> None:
    migration = importlib.import_module(MIGRATION)
    _add_managed_secret(pg_engine, "GITHUB_TOKEN", "disabled")
    _add_managed_secret(pg_engine, "GITHUB_PAT", "active")

    with pg_engine.begin() as connection:
        first = migration.migrate_legacy_github_connection(connection, {})
    with pg_engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM repository_connection_audit_events"))
        second = migration.migrate_legacy_github_connection(connection, {})

    assert (first.outcome, first.credential_ref) == ("mapped", "db://GITHUB_PAT")
    assert second.outcome == "already_mapped"
    with pg_engine.connect() as connection:
        records = connection.execute(
            sa.text("SELECT credential_config FROM repository_connection_records")
        ).all()
        audits = connection.execute(
            sa.text("SELECT count(*) FROM repository_connection_audit_events")
        ).scalar_one()
    assert [row.credential_config["credentialRef"]["key"] for row in records] == [
        "GITHUB_PAT"
    ]
    assert audits == 1

    with pg_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE repository_connection_records SET policy_revision = 2 "
                "WHERE connection_id = :ref"
            ),
            {"ref": DEFAULT_REF},
        )
    with pg_engine.begin() as connection:
        kept = migration.migrate_legacy_github_connection(
            connection, {"GITHUB_TOKEN": "ghp_" + "x" * 36}
        )
        assert migration.remove_untouched_legacy_github_connection(connection) is False

    assert kept.outcome == "kept_existing"
    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                sa.text("SELECT count(*) FROM repository_connection_records")
            ).scalar_one()
            == 1
        )
