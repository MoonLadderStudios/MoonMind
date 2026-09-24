"""Migration coverage for dropping account foreign keys from preset catalog.

MoonLadderStudios/MoonMind#4350: the account-free local operator has no
``user`` row, so preset recents, favorites, and provenance must not reference
``user.id``. Rows, uniqueness, and ``presets.id`` references stay intact.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = "api_service.migrations.versions.390_preset_catalog_account_free"
OPERATOR = "00000000000000000000000000000000"
KNOWN_USER = "11111111111111111111111111111111"

_PRE_CHANGE_SCHEMA = (
    'CREATE TABLE "user" (id CHAR(32) PRIMARY KEY)',
    """
    CREATE TABLE presets (
        id CHAR(32) PRIMARY KEY,
        slug VARCHAR(128) NOT NULL,
        reviewed_by CHAR(32) REFERENCES "user"(id) ON DELETE SET NULL,
        created_by CHAR(32) REFERENCES "user"(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE preset_favorites (
        id INTEGER PRIMARY KEY,
        user_id CHAR(32) NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        template_id CHAR(32) NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
        CONSTRAINT uq_preset_favorite UNIQUE (user_id, template_id)
    )
    """,
    """
    CREATE TABLE preset_recents (
        id INTEGER PRIMARY KEY,
        user_id CHAR(32) NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        template_id CHAR(32) NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
        CONSTRAINT uq_preset_recent_user_template UNIQUE (user_id, template_id)
    )
    """,
)


def _referred(connection, table: str) -> dict[str, str]:
    return {
        fk["constrained_columns"][0]: fk["referred_table"]
        for fk in sa.inspect(connection).get_foreign_keys(table)
    }


def _operations(connection, migration):
    return patch.object(
        migration, "op", Operations(MigrationContext.configure(connection))
    )


def _engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path}/presets.db")

    @sa.event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        for statement in _PRE_CHANGE_SCHEMA:
            connection.execute(sa.text(statement))
    return engine


def _migrate(tmp_path, migration, *steps: str) -> None:
    # Alembic's SQLite connections do not enforce foreign keys, which the
    # batch table rebuild relies on.
    engine = sa.create_engine(f"sqlite:///{tmp_path}/presets.db")
    try:
        with engine.begin() as connection, _operations(connection, migration):
            for step in steps:
                getattr(migration, step)()
    finally:
        engine.dispose()


def _rejects(engine, statement: str) -> None:
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(sa.text(statement))


def test_migration_chains_off_previous_head() -> None:
    migration = importlib.import_module(MIGRATION)
    assert migration.down_revision == "389_opencode_validation_repair"
    assert len(migration.revision) <= 32


def test_upgrade_drops_only_user_references_and_admits_account_free_writes(
    tmp_path,
) -> None:
    migration = importlib.import_module(MIGRATION)
    engine = _engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(sa.text(f"INSERT INTO \"user\" VALUES ('{KNOWN_USER}')"))
        connection.execute(
            sa.text(f"INSERT INTO presets VALUES ('p1', 'kept', NULL, '{KNOWN_USER}')")
        )
        connection.execute(
            sa.text(f"INSERT INTO preset_recents VALUES (1, '{KNOWN_USER}', 'p1')")
        )
    _rejects(engine, f"INSERT INTO preset_recents VALUES (2, '{OPERATOR}', 'p1')")

    # Re-running finds nothing left to drop.
    _migrate(tmp_path, migration, "upgrade", "upgrade")

    with engine.begin() as connection:
        assert _referred(connection, "presets") == {}
        assert _referred(connection, "preset_favorites") == {"template_id": "presets"}
        assert _referred(connection, "preset_recents") == {"template_id": "presets"}
        assert connection.execute(
            sa.text("SELECT slug, created_by FROM presets")
        ).all() == [("kept", KNOWN_USER)]
        connection.execute(
            sa.text(f"INSERT INTO preset_recents VALUES (2, '{OPERATOR}', 'p1')")
        )
        connection.execute(
            sa.text(f"INSERT INTO preset_favorites VALUES (1, '{OPERATOR}', 'p1')")
        )
        connection.execute(
            sa.text(f"UPDATE presets SET created_by = '{OPERATOR}' WHERE id = 'p1'")
        )
    # Uniqueness and the preset reference survive the table rebuild.
    _rejects(engine, f"INSERT INTO preset_recents VALUES (3, '{OPERATOR}', 'p1')")
    _rejects(engine, f"INSERT INTO preset_recents VALUES (3, '{OPERATOR}', 'nope')")

    with pytest.raises(RuntimeError, match="no user row"):
        _migrate(tmp_path, migration, "downgrade")
    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT user_id FROM preset_recents ORDER BY id")
        ).scalars().all() == [KNOWN_USER, OPERATOR]


def test_downgrade_restores_user_references_when_every_value_resolves(
    tmp_path,
) -> None:
    migration = importlib.import_module(MIGRATION)
    engine = _engine(tmp_path)
    _migrate(tmp_path, migration, "upgrade", "downgrade")

    with engine.begin() as connection:
        assert _referred(connection, "presets") == {
            "reviewed_by": "user",
            "created_by": "user",
        }
        assert _referred(connection, "preset_recents") == {
            "user_id": "user",
            "template_id": "presets",
        }


def test_sqlite_rebuild_refuses_to_run_with_cascading_foreign_keys(tmp_path) -> None:
    migration = importlib.import_module(MIGRATION)
    engine = _engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(sa.text(f"INSERT INTO \"user\" VALUES ('{KNOWN_USER}')"))
        connection.execute(
            sa.text("INSERT INTO presets VALUES ('p1', 'kept', NULL, NULL)")
        )
        connection.execute(
            sa.text(f"INSERT INTO preset_recents VALUES (1, '{KNOWN_USER}', 'p1')")
        )
    with pytest.raises(RuntimeError, match="foreign key enforcement off"):
        with engine.begin() as connection, _operations(connection, migration):
            migration.upgrade()
    with engine.begin() as connection:
        assert (
            connection.execute(sa.text("SELECT COUNT(*) FROM preset_recents")).scalar()
            == 1
        )
        assert _referred(connection, "preset_recents") == {
            "user_id": "user",
            "template_id": "presets",
        }
