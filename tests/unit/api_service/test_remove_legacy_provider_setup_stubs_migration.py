"""Regression coverage for conservative legacy provider stub cleanup."""

from __future__ import annotations

import importlib

import sqlalchemy as sa


def test_migration_deletes_only_untouched_generated_stubs(monkeypatch) -> None:
    migration = importlib.import_module(
        "api_service.migrations.versions.338_remove_legacy_provider_setup_stubs"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    profiles = sa.Table(
        "managed_agent_provider_profiles",
        metadata,
        sa.Column("profile_id", sa.String, primary_key=True),
        sa.Column("runtime_id", sa.String),
        sa.Column("provider_id", sa.String),
        sa.Column("account_label", sa.String),
        sa.Column("enabled", sa.Boolean),
        sa.Column("is_default", sa.Boolean),
        sa.Column("credential_source", sa.String),
        sa.Column("runtime_materialization_mode", sa.String),
        sa.Column("auth_state", sa.String),
        sa.Column("disabled_reason", sa.String),
        sa.Column("secret_refs", sa.JSON),
        sa.Column("volume_ref", sa.String),
        sa.Column("volume_mount_path", sa.String),
        sa.Column("last_auth_method", sa.String),
    )
    metadata.create_all(engine)
    base = dict(
        runtime_id="claude_code",
        provider_id="anthropic",
        account_label="Claude Code (setup required)",
        enabled=False,
        is_default=False,
        credential_source="none",
        runtime_materialization_mode="api_key_env",
        auth_state="not_configured",
        disabled_reason="missing_credentials",
        secret_refs=None,
        volume_ref=None,
        volume_mount_path=None,
        last_auth_method=None,
    )
    with engine.begin() as connection:
        connection.execute(
            profiles.insert(),
            [
                {**base, "profile_id": "claude_anthropic_default"},
                {
                    **base,
                    "profile_id": "claude_anthropic",
                    "secret_refs": {"anthropic_api_key": "secret://configured"},
                },
            ],
        )
        monkeypatch.setattr(migration.op, "execute", connection.execute)
        migration.upgrade()
        remaining = set(connection.execute(sa.select(profiles.c.profile_id)).scalars())

    assert remaining == {"claude_anthropic"}
