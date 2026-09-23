"""Unit coverage for the machine-capacity table retirement migration.

MoonLadderStudios/MoonMind#4459 ([Plan A] retire the unused
machine-capacity table after the rollback window): the executable resource
accounting was removed while the old ``machine_capacity_reservations``
table was intentionally retained for upgrade/rollback safety. A forward
migration now drops only that table; this module pins the migration chain,
the drop-only upgrade, the honest empty-recreate downgrade, and the removal
of the table from current ORM metadata.
"""

from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from api_service.db.models import Base

MIGRATION_MODULE = "api_service.migrations.versions.386_drop_machine_capacity_4459"


def _migration():
    return importlib.import_module(MIGRATION_MODULE)


def _operations_for(connection, monkeypatch):
    migration = _migration()
    monkeypatch.setattr(
        migration,
        "op",
        Operations(MigrationContext.configure(connection)),
    )
    return migration


def test_migration_chains_off_current_head() -> None:
    migration = _migration()
    assert migration.down_revision == "385_runtime_issuance"
    assert len(migration.revision) <= 32


def test_migration_graph_keeps_single_head_at_new_revision() -> None:
    config = Config("api_service/migrations/alembic.ini")
    config.set_main_option("script_location", "api_service/migrations")
    script = ScriptDirectory.from_config(config)
    # PR #4461 merged the two 386 branches (machine-capacity drain and the
    # single-user conversion ledger) into 387_merge_386_heads_4461; the GitHub
    # event delivery receipts migration (#3967) now extends that chain as the
    # single head.
    assert tuple(script.get_heads()) == ("388_github_event_receipts_3967",)


def test_upgrade_drops_only_retired_table_and_keeps_unrelated_rows(
    monkeypatch,
) -> None:
    create_migration = importlib.import_module(
        "api_service.migrations.versions.372_machine_reservations"
    )
    drop_migration = _migration()

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(
            create_migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        create_migration.upgrade()
        connection.execute(
            sa.text(
                "CREATE TABLE managed_hosts "
                "(host_id VARCHAR(255) PRIMARY KEY, state VARCHAR(32) NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO machine_capacity_reservations "
                "(reservation_id, backend_ref, workload_class, owner_kind, "
                "owner_ref, state) VALUES "
                "('res-1', 'backend-a', 'job', 'workflow', 'wf-1', 'active')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO managed_hosts (host_id, state) VALUES ('host-1', 'active')"
            )
        )

        _operations_for(connection, monkeypatch)
        drop_migration.upgrade()

        remaining = set(sa.inspect(connection).get_table_names())
        assert "machine_capacity_reservations" not in remaining
        assert "managed_hosts" in remaining
        assert (
            connection.execute(sa.text("SELECT COUNT(*) FROM managed_hosts")).scalar()
            == 1
        )


def test_downgrade_recreates_empty_table_without_restoring_rows(monkeypatch) -> None:
    create_migration = importlib.import_module(
        "api_service.migrations.versions.372_machine_reservations"
    )
    drop_migration = _migration()

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        monkeypatch.setattr(
            create_migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        create_migration.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO machine_capacity_reservations "
                "(reservation_id, backend_ref, workload_class, owner_kind, "
                "owner_ref, state) VALUES "
                "('res-1', 'backend-a', 'job', 'workflow', 'wf-1', 'active')"
            )
        )

        monkeypatch.setattr(
            drop_migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        drop_migration.upgrade()
        assert "machine_capacity_reservations" not in set(
            sa.inspect(connection).get_table_names()
        )

        drop_migration.downgrade()
        assert "machine_capacity_reservations" in set(
            sa.inspect(connection).get_table_names()
        )
        # An empty-schema downgrade does not restore historical rows:
        # old-code rollback that needs data must restore a pre-upgrade backup.
        assert (
            connection.execute(
                sa.text("SELECT COUNT(*) FROM machine_capacity_reservations")
            ).scalar()
            == 0
        )
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "machine_capacity_reservations"
            )
        }
        assert {
            "reservation_id",
            "backend_ref",
            "workload_class",
            "owner_kind",
            "owner_ref",
            "state",
        } <= columns


def test_current_orm_metadata_omits_retired_table() -> None:
    assert "machine_capacity_reservations" not in Base.metadata.tables
