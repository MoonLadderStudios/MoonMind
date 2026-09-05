"""Migration coverage for the operator runtime-default selection marker.

MoonLadderStudios/MoonMind#3877 needs to tell an explicit operator
``make_default`` apart from the automatic transfer the pre-change controller
performed when ``OPENCODE_API_KEY`` was configured. Revision 370 adds the column
that records the difference and backfills every existing row to "not operator
selected" -- the controlled cutover that lets startup seeding reclaim the
``opencode`` runtime default for the credentialless Zen profile.
"""

from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = "api_service.migrations.versions.370_provider_default_authority"

_PRE_CHANGE_TABLE = sa.text(
    """
    CREATE TABLE managed_agent_provider_profiles (
        profile_id VARCHAR(128) PRIMARY KEY,
        runtime_id VARCHAR(64) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT 0,
        is_default BOOLEAN NOT NULL DEFAULT 0
    )
    """
)


def test_migration_adds_the_operator_selection_marker(monkeypatch) -> None:
    migration = importlib.import_module(MIGRATION)
    assert migration.down_revision == "369_provider_lease_incr_contract"

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(_PRE_CHANGE_TABLE)
        # The exact persisted assignment the pre-change controller produced.
        connection.execute(
            sa.text(
                "INSERT INTO managed_agent_provider_profiles "
                "(profile_id, runtime_id, enabled, is_default) VALUES "
                "('opencode-zen-free', 'opencode', 1, 0), "
                "('opencode-go-default', 'opencode', 1, 1)"
            )
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )

        migration.upgrade()

        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "managed_agent_provider_profiles"
            )
        }
        assert "default_selected_by_operator" in columns

        rows = dict(
            connection.execute(
                sa.text(
                    "SELECT profile_id, default_selected_by_operator "
                    "FROM managed_agent_provider_profiles"
                )
            ).all()
        )
        # Pre-change rows recorded no explicit selection, including the row that
        # currently holds is_default. That is what releases the default back to
        # the Zen profile on the next restart.
        assert rows == {"opencode-zen-free": 0, "opencode-go-default": 0}

        migration.downgrade()

        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "managed_agent_provider_profiles"
            )
        }
        assert "default_selected_by_operator" not in columns
