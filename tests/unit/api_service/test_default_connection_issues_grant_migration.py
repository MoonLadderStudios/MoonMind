"""Migration coverage for granting issues to the migrated default connection.

Issue collaboration now requires the default connection's ``issues``
operation instead of an ambient token (MoonLadderStudios/MoonMind#4003). The
deployment credential revision 391 mapped already performed it, so the
unedited mapping must keep admitting it.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import uuid

import pytest
import sqlalchemy as sa

from api_service.db.models import Base

MIGRATION_391 = "api_service.migrations.versions.391_legacy_github_cred_4023"
MIGRATION_393 = "api_service.migrations.versions.393_default_conn_merge_grant"
MIGRATION = "api_service.migrations.versions.394_default_conn_issues_grant"
DEFAULT_REF = "repository-connection:git-default"
REPOSITORY = "MoonLadderStudios/MoonMind"
MAPPED_OPERATIONS = ["read", "write", "branch_write", "review_request", "merge_request"]
_TABLES = (
    "managed_secrets",
    "repository_connection_records",
    "repository_connection_assignments",
    "repository_route_defaults",
    "repository_connection_audit_events",
)


@pytest.fixture
def migration():
    return importlib.import_module(MIGRATION)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "migration.db"


@pytest.fixture
def engine(db_path):
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[name] for name in _TABLES]
    )
    yield engine
    engine.dispose()


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _rows(engine, table: str) -> list[dict]:
    with engine.connect() as connection:
        return [
            dict(row._mapping)
            for row in connection.execute(sa.text(f"SELECT * FROM {table}"))
        ]


def _map_default(engine, operations=None) -> None:
    """Record the default exactly as revision 391 writes it."""

    with engine.begin() as connection:
        importlib.import_module(MIGRATION_391).migrate_legacy_github_connection(
            connection, {"GITHUB_TOKEN": "fixture-only"}
        )
        if operations is not None:
            connection.execute(
                sa.text(
                    "UPDATE repository_connection_records SET allowed_operations = :ops"
                ),
                {"ops": json.dumps(operations)},
            )


def _assign(engine, operations: list[str]) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO repository_connection_assignments (id, connection_id, "
                "endpoint_normalized, repo_key, provider_repo_id, display_name, "
                "operations, revision, verified, created_at, updated_at) VALUES "
                "(:id, :connection_id, 'https://github.com', 'id:916785816', "
                "'916785816', :display_name, :operations, 1, 1, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "id": uuid.uuid4().hex,
                "connection_id": DEFAULT_REF,
                "display_name": REPOSITORY,
                "operations": json.dumps(operations),
            },
        )


def _upgrade(engine, migration) -> None:
    with engine.begin() as connection:
        migration.grant_default_connection_issues(connection)


def _admit_issues(db_path):
    """Admit an issue collaboration use through the real connection owners."""

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from api_service.services.repository_connections import (
        RepositoryConnectionService,
    )
    from moonmind.auth.bound_acquisition import (
        AccessMode,
        select_repository_authority,
    )

    async def _run():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            async with AsyncSession(async_engine) as session:
                service = RepositoryConnectionService(session)
                principal = {
                    "principal_ref": "system:deployment",
                    "principal_scope": ("system", None),
                }
                connection = await service.get_connection(DEFAULT_REF, **principal)
                assignment = await service.launch_assignment(connection, REPOSITORY)
                return select_repository_authority(
                    access_mode=AccessMode.EXPLICIT,
                    identity=assignment.identity,
                    role="collaboration",
                    requested_operations=("issues",),
                    policy_revision=connection.policy_revision,
                    explicit_connection=connection,
                    explicit_assignment=assignment,
                    **principal,
                )
        finally:
            await async_engine.dispose()

    return asyncio.run(_run())


def test_migration_is_the_single_head(migration):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert migration.down_revision == "393_default_conn_merge_grant"
    assert len(migration.revision) <= 32
    config = Config("api_service/migrations/alembic.ini")
    config.set_main_option("script_location", "api_service/migrations")
    assert tuple(ScriptDirectory.from_config(config).get_heads()) == (
        migration.revision,
    )


def test_mapped_default_with_full_assignment_admits_issue_collaboration(
    engine, db_path, migration
):
    from moonmind.auth.bound_acquisition import BoundAccessError

    _map_default(engine)
    _assign(engine, MAPPED_OPERATIONS)
    with pytest.raises(BoundAccessError, match="does not admit issues"):
        _admit_issues(db_path)

    _upgrade(engine, migration)

    assert "issues" in _admit_issues(db_path).operations
    [record] = _rows(engine, "repository_connection_records")
    [assignment] = _rows(engine, "repository_connection_assignments")
    assert _json(record["allowed_operations"]) == [*MAPPED_OPERATIONS, "issues"]
    assert _json(assignment["operations"]) == [*MAPPED_OPERATIONS, "issues"]
    assert record["policy_revision"] == 1
    assert assignment["revision"] == 1


def test_unscoped_default_mapped_before_merge_grant_admits_issues(
    engine, db_path, migration
):
    # A deployment that applied 391 before merge_request, then 393, then this.
    _map_default(engine, MAPPED_OPERATIONS[:4])
    with engine.begin() as connection:
        importlib.import_module(MIGRATION_393).grant_default_connection_merge(
            connection
        )

    _upgrade(engine, migration)

    assert "issues" in _admit_issues(db_path).operations


def test_narrowed_assignment_keeps_the_operator_scope(engine, migration):
    _map_default(engine)
    _assign(engine, ["read"])

    _upgrade(engine, migration)

    [assignment] = _rows(engine, "repository_connection_assignments")
    assert _json(assignment["operations"]) == ["read"]


def test_operator_edited_connection_is_unchanged(engine, migration):
    _map_default(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE repository_connection_records SET policy_revision = 2")
        )

    _upgrade(engine, migration)

    [record] = _rows(engine, "repository_connection_records")
    assert _json(record["allowed_operations"]) == MAPPED_OPERATIONS


def test_operator_recorded_default_is_unchanged(engine, migration):
    _map_default(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM repository_connection_audit_events"))

    _upgrade(engine, migration)

    [record] = _rows(engine, "repository_connection_records")
    assert _json(record["allowed_operations"]) == MAPPED_OPERATIONS


def test_rerun_is_a_no_op(engine, migration):
    _map_default(engine)
    _assign(engine, MAPPED_OPERATIONS)

    _upgrade(engine, migration)
    _upgrade(engine, migration)

    [assignment] = _rows(engine, "repository_connection_assignments")
    assert _json(assignment["operations"]) == [*MAPPED_OPERATIONS, "issues"]
    assert (
        sum(
            row["request_id"] == migration.MIGRATION_REQUEST_ID
            for row in _rows(engine, "repository_connection_audit_events")
        )
        == 1
    )
