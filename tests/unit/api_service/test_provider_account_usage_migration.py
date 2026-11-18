"""Forward migration for cached provider account usage (#5362)."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from api_service.db.models import ProviderAccountUsageObservation

MIGRATION = "api_service.migrations.versions.415_provider_account_usage"
REVISION = "415_provider_account_usage"


def _run(connection, direction: str) -> None:
    migration = importlib.import_module(MIGRATION)
    with patch.object(
        migration, "op", Operations(MigrationContext.configure(connection))
    ):
        getattr(migration, direction)()


@pytest.fixture
def database(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path}/migration.db")

    @sa.event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """CREATE TABLE managed_agent_provider_profiles (
                profile_id VARCHAR(128) PRIMARY KEY, runtime_id TEXT,
                provider_id TEXT, account_label TEXT, credential_generation INTEGER,
                enabled BOOLEAN, is_default BOOLEAN, priority INTEGER,
                command_behavior JSON, model_tiers JSON, auth_state TEXT,
                updated_at TEXT
            )"""
            )
        )
        connection.execute(
            sa.text(
                """INSERT INTO managed_agent_provider_profiles VALUES
                ('codex-oauth', 'codex_cli', 'openai', 'person@example.com', 3,
                 1, 1, 200, '{"auth_status_label": "Connected"}', '[]',
                 'connected', 'before'),
                ('claude-key', 'claude_code', 'anthropic', NULL, 1,
                 0, 0, 100, NULL, '[]', 'not_configured', 'before')"""
            )
        )
    yield engine
    engine.dispose()


def _profiles(connection):
    return connection.execute(
        sa.text("SELECT * FROM managed_agent_provider_profiles ORDER BY profile_id")
    ).all()


def test_upgrade_preserves_profiles_and_starts_with_unknown_observations(database):
    with database.begin() as connection:
        before = _profiles(connection)
        _run(connection, "upgrade")
        assert _profiles(connection) == before
        # No row is manufactured: no email from account_label, no usage.
        assert (
            connection.execute(
                sa.text("SELECT COUNT(*) FROM provider_account_usage_observations")
            ).scalar_one()
            == 0
        )
        migrated = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "provider_account_usage_observations"
            )
        }
    assert migrated == set(ProviderAccountUsageObservation.__table__.columns.keys())


def test_observations_cascade_with_their_profile_and_downgrade_keeps_profiles(database):
    with database.begin() as connection:
        before = _profiles(connection)
        _run(connection, "upgrade")
        connection.execute(
            sa.text(
                """INSERT INTO provider_account_usage_observations
                (profile_id, credential_generation, provider_id, runtime_id)
                VALUES ('codex-oauth', 3, 'openai', 'codex_cli'),
                       ('claude-key', 1, 'anthropic', 'claude_code')"""
            )
        )
        connection.execute(
            sa.text(
                "DELETE FROM managed_agent_provider_profiles WHERE profile_id='claude-key'"
            )
        )
        remaining = connection.execute(
            sa.text(
                "SELECT profile_id, refresh_state, snapshot_revision, row_version "
                "FROM provider_account_usage_observations"
            )
        ).all()
        assert remaining == [("codex-oauth", "idle", 0, 1)]
        _run(connection, "downgrade")
        assert (
            "provider_account_usage_observations"
            not in sa.inspect(connection).get_table_names()
        )
        assert _profiles(connection) == [before[1]]


def test_migration_chains_onto_the_single_alembic_head():
    script = ScriptDirectory(
        str(Path(__file__).resolve().parents[3] / "api_service/migrations")
    )
    heads = script.get_heads()
    # Later migrations may extend the chain past this revision.
    assert len(heads) == 1
    assert REVISION in {rev.revision for rev in script.walk_revisions(head=heads[0])}
    assert script.get_revision(REVISION).down_revision == "414_settings_generations"
