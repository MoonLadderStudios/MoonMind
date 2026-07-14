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
        sa.Column("provider_label", sa.String),
        sa.Column("account_label", sa.String),
        sa.Column("default_model", sa.String),
        sa.Column("default_effort", sa.String),
        sa.Column("model_overrides", sa.JSON),
        sa.Column("enabled", sa.Boolean),
        sa.Column("is_default", sa.Boolean),
        sa.Column("tags", sa.JSON),
        sa.Column("priority", sa.Integer),
        sa.Column("credential_source", sa.String),
        sa.Column("runtime_materialization_mode", sa.String),
        sa.Column("auth_state", sa.String),
        sa.Column("disabled_reason", sa.String),
        sa.Column("secret_refs", sa.JSON),
        sa.Column("clear_env_keys", sa.JSON),
        sa.Column("env_template", sa.JSON),
        sa.Column("file_templates", sa.JSON),
        sa.Column("home_path_overrides", sa.JSON),
        sa.Column("command_behavior", sa.JSON),
        sa.Column("max_parallel_runs", sa.Integer),
        sa.Column("cooldown_after_429_seconds", sa.Integer),
        sa.Column("rate_limit_policy", sa.String),
        sa.Column("max_lease_duration_seconds", sa.Integer),
        sa.Column("volume_ref", sa.String),
        sa.Column("volume_mount_path", sa.String),
        sa.Column("last_auth_method", sa.String),
    )
    metadata.create_all(engine)
    base = dict(
        runtime_id="claude_code",
        provider_id="anthropic",
        provider_label="Anthropic",
        account_label="Claude Code (setup required)",
        default_model=None,
        default_effort=None,
        model_overrides=None,
        enabled=False,
        is_default=False,
        tags=None,
        priority=100,
        credential_source="none",
        runtime_materialization_mode="api_key_env",
        auth_state="not_configured",
        disabled_reason="missing_credentials",
        secret_refs=None,
        clear_env_keys=None,
        env_template=None,
        file_templates=None,
        home_path_overrides=None,
        command_behavior=None,
        max_parallel_runs=1,
        cooldown_after_429_seconds=900,
        rate_limit_policy="backoff",
        max_lease_duration_seconds=7200,
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
                    "default_model": "operator-selected-model",
                },
            ],
        )
        monkeypatch.setattr(migration.op, "execute", connection.execute)
        migration.upgrade()
        remaining = set(connection.execute(sa.select(profiles.c.profile_id)).scalars())

    assert remaining == {"claude_anthropic"}
