"""Repository membership survives retirement of per-assignment permissions."""

import importlib
import uuid

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from api_service.db.models import RepositoryConnectionAssignment


MIGRATION = "api_service.migrations.versions.413_assignment_permissions"


def test_current_assignments_do_not_store_operations():
    assert "operations" not in RepositoryConnectionAssignment.__table__.c


def test_upgrade_preserves_membership_and_downgrade_inherits_current_permissions(
    monkeypatch,
):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        operations = Operations(MigrationContext.configure(connection))
        old = importlib.import_module(
            "api_service.migrations.versions.377_repository_connections_4005"
        )
        monkeypatch.setattr(old, "op", operations)
        old.upgrade()
        metadata = sa.MetaData()
        metadata.reflect(connection)
        records = metadata.tables["repository_connection_records"]
        assignments = metadata.tables["repository_connection_assignments"]
        defaults = metadata.tables["repository_route_defaults"]
        connection.execute(
            records.insert().values(
                connection_id="connection",
                display_name="Example",
                provider="git",
                hosting_service="github",
                endpoint_normalized="https://github.com",
                endpoint_ref="https://github.com",
                allowed_operations=["read", "write"],
                client_policy={},
                credential_config={},
                owner_ref="operator",
                scope_type="system",
                allowed_principal_refs=[],
            )
        )
        for index, verified in enumerate((True, False)):
            connection.execute(
                assignments.insert().values(
                    id=uuid.uuid4().hex,
                    connection_id="connection",
                    endpoint_normalized="https://github.com",
                    repo_key=f"id:{index}",
                    provider_repo_id=str(index),
                    display_name=f"owner/repo-{index}",
                    operations=["read"],
                    revision=7,
                    verified=verified,
                )
            )
        connection.execute(
            defaults.insert().values(
                id=uuid.uuid4().hex,
                scope_type="system",
                scope_key="system",
                endpoint_normalized="https://github.com",
                repo_key="id:0",
                capability_bundle="read,write",
                connection_id="connection",
                policy_revision=3,
                request_id="saved-default",
            )
        )

        def rows(table):
            return [
                dict(row)
                for row in connection.execute(
                    sa.text(f"SELECT * FROM {table}")
                ).mappings()
            ]

        before = rows(assignments.name)
        retained = {name: rows(name) for name in (records.name, defaults.name)}
        migration = importlib.import_module(MIGRATION)
        assert migration.down_revision == "412_assessment_occurrences"
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        assert "operations" not in {
            c["name"] for c in sa.inspect(connection).get_columns(assignments.name)
        }
        assert rows(assignments.name) == [
            {key: value for key, value in row.items() if key != "operations"}
            for row in before
        ]
        assert {name: rows(name) for name in retained} == retained
        inspector = sa.inspect(connection)
        assert (
            inspector.get_foreign_keys(assignments.name)[0]["referred_table"]
            == records.name
        )
        assert {
            item["name"] for item in inspector.get_unique_constraints(assignments.name)
        } == {"uq_repo_assignment_connection_repo"}
        assert {item["name"] for item in inspector.get_indexes(assignments.name)} == {
            "ix_repo_assignment_repo",
            "ix_repo_assignment_connection",
        }

        connection.execute(
            records.update().values(allowed_operations=["read", "issues"])
        )
        migration.downgrade()
        restored = sa.Table(assignments.name, sa.MetaData(), autoload_with=connection)
        assert list(connection.execute(sa.select(restored.c.operations)).scalars()) == [
            ["read", "issues"],
            ["read", "issues"],
        ]
        assert all(row["revision"] == 7 for row in rows(assignments.name))
        assert rows(defaults.name) == retained[defaults.name]
        assert not next(
            c
            for c in sa.inspect(connection).get_columns(assignments.name)
            if c["name"] == "operations"
        )["nullable"]
    engine.dispose()
