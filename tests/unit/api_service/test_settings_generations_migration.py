"""Preserve old values and fail closed when reverting retained generations."""

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import util
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

MIGRATION = "api_service.migrations.versions.414_settings_generations"


def _run(connection, direction):
    migration = importlib.import_module(MIGRATION)
    with patch.object(
        migration, "op", Operations(MigrationContext.configure(connection))
    ):
        getattr(migration, direction)()


@pytest.fixture
def database(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path}/migration.db")
    with engine.begin() as connection:
        connection.execute(
            sa.text("""CREATE TABLE settings_overrides (
            id TEXT PRIMARY KEY, key TEXT, scope TEXT, value_json JSON,
            schema_version INTEGER DEFAULT 1, value_version INTEGER DEFAULT 1,
            created_by TEXT, updated_by TEXT, created_at TEXT, updated_at TEXT
        )""")
        )
        connection.execute(
            sa.text("""INSERT INTO settings_overrides VALUES
            ('a', 'setting', NULL, '"value"', 1, 9, 'author', 'editor', 'created', 'updated'),
            ('b', 'null-setting', NULL, 'null', 2, 1, NULL, NULL, 'created', 'updated'),
            ('c', 'setting', 'user', '"retained"', 3, 7, 'person', 'person', 'created', 'updated')
        """)
        )
    yield engine
    engine.dispose()


def test_upgrade_and_unused_downgrade_preserve_every_old_column(database):
    with database.begin() as connection:
        before = connection.execute(
            sa.text("SELECT * FROM settings_overrides ORDER BY id")
        ).all()
        _run(connection, "upgrade")
        after = connection.execute(
            sa.text("SELECT * FROM settings_overrides ORDER BY id")
        ).all()
        assert [tuple(row[:-1]) for row in after] == [tuple(row) for row in before]
        assert [row[-1] for row in after] == [False, False, False]
        _run(connection, "downgrade")
        assert (
            connection.execute(
                sa.text("SELECT * FROM settings_overrides ORDER BY id")
            ).all()
            == before
        )


def test_downgrade_refuses_reset_without_destroying_values_or_generation(database):
    with database.begin() as connection:
        _run(connection, "upgrade")
        connection.execute(
            sa.text(
                "UPDATE settings_overrides SET is_reset=true, value_json=NULL, value_version=10 WHERE id='a'"
            )
        )
        before = connection.execute(
            sa.text("SELECT * FROM settings_overrides ORDER BY id")
        ).all()
        with pytest.raises(
            RuntimeError, match="Cannot discard retained settings generations"
        ):
            _run(connection, "downgrade")
        assert (
            connection.execute(
                sa.text("SELECT * FROM settings_overrides ORDER BY id")
            ).all()
            == before
        )


def test_predecessor_alembic_graph_refuses_new_database_revision(tmp_path):
    # The selected-image init-db uses this same Alembic upgrade resolution.
    # An older image must fail before Compose starts dependent API/workers.
    from shutil import copyfile

    source = Path(__file__).resolve().parents[3] / "api_service/migrations/versions"
    versions = tmp_path / "versions"
    versions.mkdir()
    for migration in source.glob("*.py"):
        if migration.name != "414_settings_generations.py":
            copyfile(migration, versions / migration.name)
    script = ScriptDirectory(str(tmp_path))
    with pytest.raises(util.CommandError, match="Can't locate revision"):
        script._upgrade_revs("head", "414_settings_generations")
