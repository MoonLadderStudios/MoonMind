"""Replay the persisted discovery regression through the real Alembic migration."""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_profile_cutover_preserves_identity_credentials_and_operator_state(tmp_path):
    module = importlib.import_module(
        "api_service.migrations.versions.371_profile_execution_config"
    )
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'profiles.db'}")
    metadata = sa.MetaData()
    table = sa.Table(
        "managed_agent_provider_profiles",
        metadata,
        sa.Column("profile_id", sa.String(), primary_key=True),
        sa.Column("auth_state", sa.String()),
        sa.Column("enabled", sa.Boolean()),
        sa.Column("command_behavior", sa.JSON()),
        sa.Column("secret_refs", sa.JSON()),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        for name, state, enabled, reason in (
            ("zen", "connected", True, "Pinned OpenCode runtime validation failed."),
            (
                "go",
                "connected",
                True,
                "The selected model was not observed by the pinned OpenCode runtime.",
            ),
            (
                "disabled",
                "connected",
                False,
                "Pinned OpenCode runtime validation failed.",
            ),
            ("invalid", "error", True, "Authentication rejected."),
        ):
            connection.execute(
                table.insert().values(
                    profile_id=name,
                    auth_state=state,
                    enabled=enabled,
                    command_behavior={
                        "auth_readiness": {
                            "launch_ready": False,
                            "failure_reason": reason,
                        }
                    },
                    secret_refs={"api_key": "db://existing-binding"},
                )
            )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        migrated = sa.Table(table.name, sa.MetaData(), autoload_with=connection)
        rows = {
            row["profile_id"]: row
            for row in connection.execute(sa.select(migrated)).mappings()
        }
        assert set(rows) == {"zen", "go", "disabled", "invalid"}
        for name in ("zen", "go", "disabled"):
            assert (
                "launch_ready" not in rows[name]["command_behavior"]["auth_readiness"]
            )
        assert rows["disabled"]["enabled"] is False
        assert (
            rows["invalid"]["command_behavior"]["auth_readiness"]["launch_ready"]
            is False
        )
        assert all(row["execution_configuration"] is None for row in rows.values())
        assert all(
            row["secret_refs"] == {"api_key": "db://existing-binding"}
            for row in rows.values()
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        assert "execution_configuration" not in {
            column["name"] for column in sa.inspect(connection).get_columns(table.name)
        }
    engine.dispose()
