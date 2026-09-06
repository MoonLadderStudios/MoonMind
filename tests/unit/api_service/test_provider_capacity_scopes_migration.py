"""Migration coverage for authoritative provider capacity scopes.

MoonLadderStudios/MoonMind#3882 reuses migration 368 (no second scope
aggregate, no migration repeat): the upgrade seeds one equivalent scope per
existing profile without touching lease rows, and the downgrade refuses to
restore 1:1 uniqueness while shared scopes are referenced.
"""

from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event

MIGRATION = "api_service.migrations.versions.368_provider_capacity_scopes"

_PRE_CHANGE_TABLE = sa.text(
    """
    CREATE TABLE managed_agent_provider_profiles (
        profile_id VARCHAR(128) PRIMARY KEY,
        runtime_id VARCHAR(64) NOT NULL,
        provider_id VARCHAR(64),
        capacity_scope_ref VARCHAR(255) NOT NULL,
        max_parallel_runs INTEGER NOT NULL,
        CONSTRAINT uq_provider_profile_capacity_scope UNIQUE (capacity_scope_ref)
    )
    """
)

_LEASE_TABLE = sa.text(
    """
    CREATE TABLE provider_profile_slot_leases (
        lease_id VARCHAR(255) PRIMARY KEY,
        profile_id VARCHAR(128) NOT NULL,
        runtime_id VARCHAR(64) NOT NULL
    )
    """
)


def _connection(monkeypatch):
    migration = importlib.import_module(MIGRATION)
    engine = sa.create_engine("sqlite:///:memory:")
    # The migration's audit columns default to now(); sqlite has no such
    # function, so register it to exercise the real upgrade DDL and seed.
    from datetime import datetime, timezone

    event.listen(
        engine,
        "connect",
        lambda conn, _rec: conn.create_function(
            "now", 0, lambda: datetime.now(timezone.utc).isoformat()
        ),
    )
    connection = engine.connect()
    connection.execute(_PRE_CHANGE_TABLE)
    connection.execute(_LEASE_TABLE)
    connection.execute(
        sa.text(
            "INSERT INTO managed_agent_provider_profiles "
            "(profile_id, runtime_id, provider_id, capacity_scope_ref, "
            "max_parallel_runs) VALUES "
            "('prof-a', 'codex_cli', 'openai', 'scope-a', 8), "
            "('prof-b', 'codex_cli', 'openai', 'scope-b', 4)"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO provider_profile_slot_leases "
            "(lease_id, profile_id, runtime_id) VALUES "
            "('wf-1', 'prof-a', 'codex_cli')"
        )
    )
    monkeypatch.setattr(
        migration, "op", Operations(MigrationContext.configure(connection))
    )
    return migration, connection


def test_upgrade_seeds_one_equivalent_scope_per_profile(monkeypatch) -> None:
    migration, connection = _connection(monkeypatch)
    assert migration.down_revision == "367_remove_workflow_proposals"

    migration.upgrade()

    scopes = dict(
        connection.execute(
            sa.text(
                "SELECT scope_ref, configured_limit, effective_limit "
                "FROM provider_capacity_scopes"
            )
        ).all()
    )
    # One equivalent default scope per existing profile; effective behavior
    # unchanged: configured and effective both equal the profile ceiling.
    assert scopes == {"scope-a": (8, 8), "scope-b": (4, 4)}

    # Existing leases survive the migration untouched.
    leases = list(
        connection.execute(
            sa.text("SELECT lease_id, profile_id FROM provider_profile_slot_leases")
        ).all()
    )
    assert leases == [("wf-1", "prof-a")]


def test_downgrade_rejects_shared_scopes_without_touching_state(
    monkeypatch,
) -> None:
    import pytest

    migration, connection = _connection(monkeypatch)
    migration.upgrade()
    connection.execute(
        sa.text(
            "UPDATE managed_agent_provider_profiles "
            "SET capacity_scope_ref = 'scope-a' WHERE profile_id = 'prof-b'"
        )
    )

    with pytest.raises(RuntimeError, match="shared scopes are referenced"):
        migration.downgrade()

    # The rejected rollback changed nothing: the shared scope, both profile
    # refs, and the existing lease are all still in place.
    refs = sorted(
        row[0]
        for row in connection.execute(
            sa.text("SELECT capacity_scope_ref FROM managed_agent_provider_profiles")
        ).all()
    )
    assert refs == ["scope-a", "scope-a"]
    assert (
        connection.execute(sa.text("SELECT COUNT(*) FROM provider_capacity_scopes")).scalar()
        == 2
    )
    assert (
        connection.execute(
            sa.text("SELECT COUNT(*) FROM provider_profile_slot_leases")
        ).scalar()
        == 1
    )


def test_downgrade_without_shared_scopes_restores_uniqueness(
    monkeypatch,
) -> None:
    migration, connection = _connection(monkeypatch)
    migration.upgrade()

    migration.downgrade()

    assert "provider_capacity_scopes" not in sa.inspect(connection).get_table_names()
    uniques = [
        constraint["name"]
        for constraint in sa.inspect(connection).get_unique_constraints(
            "managed_agent_provider_profiles"
        )
    ]
    assert "uq_provider_profile_capacity_scope" in uniques
