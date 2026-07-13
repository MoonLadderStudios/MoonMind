from __future__ import annotations

import importlib

import sqlalchemy as sa


def test_migration_deletes_only_untouched_generated_stubs(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.338_remove_legacy_provider_setup_stubs"
    )
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE managed_agent_provider_profiles (
                profile_id TEXT PRIMARY KEY, runtime_id TEXT, provider_id TEXT,
                account_label TEXT, enabled BOOLEAN, is_default BOOLEAN,
                credential_source TEXT, runtime_materialization_mode TEXT,
                auth_state TEXT, disabled_reason TEXT, secret_refs TEXT,
                volume_ref TEXT, volume_mount_path TEXT, last_auth_method TEXT
            )
        """)
        base = {
            "runtime_id": "codex_cli",
            "provider_id": "openai",
            "account_label": "Codex CLI (setup required)",
            "enabled": False,
            "is_default": False,
            "credential_source": "none",
            "runtime_materialization_mode": "api_key_env",
            "auth_state": "not_configured",
            "disabled_reason": "missing_credentials",
            "secret_refs": None,
            "volume_ref": None,
            "volume_mount_path": None,
            "last_auth_method": None,
        }
        table = sa.table(
            "managed_agent_provider_profiles",
            *[sa.column(key) for key in ("profile_id", *base)],
        )
        connection.execute(
            table.insert(),
            [
                {"profile_id": "codex_default", **base},
                {
                    "profile_id": "codex_openai_default",
                    **base,
                    "secret_refs": '{"key":"configured"}',
                },
            ],
        )

        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        migration.upgrade()

        rows = (
            connection.execute(
                sa.text(
                    "SELECT profile_id FROM managed_agent_provider_profiles ORDER BY profile_id"
                )
            )
            .scalars()
            .all()
        )
    assert rows == ["codex_openai_default"]
