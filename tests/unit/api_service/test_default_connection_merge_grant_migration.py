"""Migration coverage for granting merge_request to the migrated default.

Deployments that applied revision 391 before it listed ``merge_request``
recorded ``repository-connection:git-default`` (and any full-grant assignment
an operator made for it) without merge authority, so every merging
pr-resolver launch on the default connection failed with
``BOUND_DENIED: explicit connection does not admit merge_request``.
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
MIGRATION = "api_service.migrations.versions.393_default_conn_merge_grant"
DEFAULT_REF = "repository-connection:git-default"
REPOSITORY = "MoonLadderStudios/MoonMind"
PRE_MERGE_OPERATIONS = ["read", "write", "branch_write", "review_request"]
RESOLVER_OPERATIONS = (*PRE_MERGE_OPERATIONS, "merge_request")
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


def _map_pre_merge_default(engine) -> None:
    """Record the default exactly as revision 391 wrote it before #4733."""

    with engine.begin() as connection:
        importlib.import_module(MIGRATION_391).migrate_legacy_github_connection(
            connection, {"GITHUB_TOKEN": "fixture-only"}
        )
        connection.execute(
            sa.text(
                "UPDATE repository_connection_records SET allowed_operations = :ops"
            ),
            {"ops": json.dumps(PRE_MERGE_OPERATIONS)},
        )


def _assign(engine, operations: list[str], repo_key: str = "id:916785816") -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO repository_connection_assignments (id, connection_id, "
                "endpoint_normalized, repo_key, provider_repo_id, display_name, "
                "operations, revision, verified, created_at, updated_at) VALUES "
                "(:id, :connection_id, 'https://github.com', :repo_key, :provider_id, "
                ":display_name, :operations, 1, 1, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "id": uuid.uuid4().hex,
                "connection_id": DEFAULT_REF,
                "repo_key": repo_key,
                "provider_id": repo_key.removeprefix("id:"),
                "display_name": REPOSITORY,
                "operations": json.dumps(operations),
            },
        )


def _upgrade(engine, migration) -> None:
    with engine.begin() as connection:
        migration.grant_default_connection_merge(connection)


def _admit_resolver(db_path):
    """Admit a merging resolver's collaboration slot through the real owners."""

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
                    requested_operations=RESOLVER_OPERATIONS,
                    policy_revision=connection.policy_revision,
                    explicit_connection=connection,
                    explicit_assignment=assignment,
                    **principal,
                )
        finally:
            await async_engine.dispose()

    return asyncio.run(_run())


def test_migration_chains_off_current_head(migration):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    assert migration.down_revision == "392_ephemeral_runtime_retention"
    assert len(migration.revision) <= 32
    config = Config("api_service/migrations/alembic.ini")
    config.set_main_option("script_location", "api_service/migrations")
    assert tuple(ScriptDirectory.from_config(config).get_heads()) == (
        migration.revision,
    )


def test_pre_merge_default_with_full_assignment_admits_a_merging_resolver(
    engine, db_path, migration
):
    from moonmind.auth.bound_acquisition import BoundAccessError

    _map_pre_merge_default(engine)
    _assign(engine, PRE_MERGE_OPERATIONS)
    with pytest.raises(BoundAccessError, match="does not admit merge_request"):
        _admit_resolver(db_path)

    _upgrade(engine, migration)

    snapshot = _admit_resolver(db_path)
    assert "merge_request" in snapshot.operations
    [record] = _rows(engine, "repository_connection_records")
    [assignment] = _rows(engine, "repository_connection_assignments")
    assert _json(record["allowed_operations"]) == list(RESOLVER_OPERATIONS)
    assert _json(assignment["operations"]) == list(RESOLVER_OPERATIONS)
    # Widening adds no authority an admitted snapshot relies on, so in-flight
    # runs keep their recorded revisions instead of going stale.
    assert record["policy_revision"] == 1
    assert assignment["revision"] == 1
    grants = [
        row
        for row in _rows(engine, "repository_connection_audit_events")
        if row["request_id"] == migration.MIGRATION_REQUEST_ID
    ]
    assert [row["action"] for row in grants] == ["connection.update"]


def test_unscoped_pre_merge_default_admits_a_merging_resolver(
    engine, db_path, migration
):
    _map_pre_merge_default(engine)

    _upgrade(engine, migration)

    assert "merge_request" in _admit_resolver(db_path).operations


def test_narrowed_assignment_keeps_the_operator_scope(engine, migration):
    _map_pre_merge_default(engine)
    _assign(engine, ["read"])

    _upgrade(engine, migration)

    [record] = _rows(engine, "repository_connection_records")
    [assignment] = _rows(engine, "repository_connection_assignments")
    assert "merge_request" in _json(record["allowed_operations"])
    assert _json(assignment["operations"]) == ["read"]


@pytest.mark.parametrize(
    "operations",
    [["read", "write"], PRE_MERGE_OPERATIONS],
    ids=["narrowed", "kept-without-merge"],
)
def test_operator_edited_connection_is_unchanged(engine, migration, operations):
    # An operator update (revision 2) that keeps the original four operations
    # is an explicit choice to leave merge disabled, not the stale mapping.
    _map_pre_merge_default(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE repository_connection_records "
                "SET allowed_operations = :ops, policy_revision = 2"
            ),
            {"ops": json.dumps(operations)},
        )
    _assign(engine, operations)

    _upgrade(engine, migration)

    [record] = _rows(engine, "repository_connection_records")
    [assignment] = _rows(engine, "repository_connection_assignments")
    assert _json(record["allowed_operations"]) == operations
    assert _json(assignment["operations"]) == operations


def test_operator_recorded_default_is_unchanged(engine, migration):
    _map_pre_merge_default(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM repository_connection_audit_events"))

    _upgrade(engine, migration)

    [record] = _rows(engine, "repository_connection_records")
    assert _json(record["allowed_operations"]) == PRE_MERGE_OPERATIONS


def test_rerun_is_a_no_op(engine, migration):
    _map_pre_merge_default(engine)
    _assign(engine, PRE_MERGE_OPERATIONS)

    _upgrade(engine, migration)
    _upgrade(engine, migration)

    [assignment] = _rows(engine, "repository_connection_assignments")
    assert _json(assignment["operations"]) == list(RESOLVER_OPERATIONS)
    assert (
        sum(
            row["request_id"] == migration.MIGRATION_REQUEST_ID
            for row in _rows(engine, "repository_connection_audit_events")
        )
        == 1
    )
