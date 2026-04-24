"""Unit tests for _auto_seed_provider_profiles startup function."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)

@pytest.fixture()
def _module_db(tmp_path):
    """Create a single in-memory SQLite engine and schema for the test."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/seed_test.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())

    _orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = session_maker
    yield
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = _orig
    asyncio.run(_teardown(engine))

@pytest.mark.asyncio
async def test_auto_seed_creates_default_profiles(_module_db, monkeypatch):
    """When the table is empty, auto-seeding should create 3 default profiles."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"gemini_cli", "codex_cli", "claude_code"}

    # Verify they exist in the DB with correct profile_id values.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 3
    profile_ids = {p.profile_id for p in profiles}
    assert profile_ids == {"gemini_default", "codex_default", "claude_anthropic"}
    # Standard profiles are seeded with default_model=None so they inherit
    # the runtime default (codex_cli→gpt-5.4, gemini_cli→gemini-3.1-pro-preview,
    # claude_code→Sonnet 4.6) rather than storing a duplicate value.
    defaults = {p.profile_id: p.default_model for p in profiles}
    assert defaults["gemini_default"] is None
    assert defaults["codex_default"] is None
    assert defaults["claude_anthropic"] is None
    runtime_defaults = {p.profile_id: p.is_default for p in profiles}
    assert runtime_defaults["gemini_default"] is True
    assert runtime_defaults["codex_default"] is True
    assert runtime_defaults["claude_anthropic"] is True
    provider_ids = {p.profile_id: p.provider_id for p in profiles}
    assert provider_ids["codex_default"] == "openai"
    assert provider_ids["claude_anthropic"] == "anthropic"
    provider_labels = {p.profile_id: p.provider_label for p in profiles}
    assert provider_labels["codex_default"] == "OpenAI"
    assert provider_labels["claude_anthropic"] == "Anthropic"
    claude_profile = next(p for p in profiles if p.profile_id == "claude_anthropic")
    assert claude_profile.credential_source == ProviderCredentialSource.OAUTH_VOLUME
    assert (
        claude_profile.runtime_materialization_mode
        == RuntimeMaterializationMode.OAUTH_HOME
    )
    assert claude_profile.volume_ref == "claude_auth_volume"
    assert claude_profile.volume_mount_path == "/home/app/.claude"
    assert claude_profile.clear_env_keys == [
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    ]

@pytest.mark.asyncio
async def test_auto_seed_is_idempotent(_module_db, monkeypatch):
    """Calling auto-seed twice should not duplicate profiles."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    first = await _auto_seed_provider_profiles()
    assert len(first) == 3

    second = await _auto_seed_provider_profiles()
    assert second == []

    # Still only 3 in DB.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()
    assert len(profiles) == 3

@pytest.mark.asyncio
async def test_auto_seed_skipped_when_env_set(_module_db, monkeypatch):
    """Seeding should be skipped when MOONMIND_SKIP_PROVIDER_PROFILE_SEED is set."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("MOONMIND_SKIP_PROVIDER_PROFILE_SEED", "true")
    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()
    assert len(profiles) == 0

@pytest.mark.asyncio
async def test_auto_seed_includes_minimax_when_env_set(_module_db, monkeypatch):
    """When MINIMAX_API_KEY is set, a 4th 'claude_minimax' profile should be seeded."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert "claude_code" in seeded  # seeded twice (default + minimax)

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 4
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic" in profile_ids
    assert "claude_minimax" in profile_ids

    # Verify MiniMax profile details.
    mm_profile = next(p for p in profiles if p.profile_id == "claude_minimax")
    assert mm_profile.runtime_id == "claude_code"
    assert mm_profile.secret_refs is not None
    assert mm_profile.secret_refs.get("ANTHROPIC_AUTH_TOKEN") == "env://MINIMAX_API_KEY"
    assert mm_profile.env_template is not None
    assert mm_profile.env_template["ANTHROPIC_BASE_URL"] == "https://api.minimax.io/anthropic"
    assert mm_profile.env_template["ANTHROPIC_MODEL"] == "MiniMax-M2.7"
    assert mm_profile.env_template["API_TIMEOUT_MS"] == "3000000"
    assert mm_profile.default_model == "MiniMax-M2.7"
    assert mm_profile.volume_ref is None
    assert mm_profile.volume_mount_path is None
    assert mm_profile.is_default is False

    anthropic_profile = next(p for p in profiles if p.profile_id == "claude_anthropic")
    assert anthropic_profile.is_default is True

@pytest.mark.asyncio
async def test_auto_seed_adds_minimax_after_initial_seed(_module_db, monkeypatch):
    """MINIMAX_API_KEY added after initial seed → claude_minimax is inserted on next call."""
    from api_service.main import _auto_seed_provider_profiles

    # First seed without MiniMax key.
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    first = await _auto_seed_provider_profiles()
    assert len(first) == 3

    # Now the key becomes available.
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    second = await _auto_seed_provider_profiles()
    assert "claude_code" in second  # minimax profile was added

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 4
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic" in profile_ids
    assert "claude_minimax" in profile_ids

@pytest.mark.asyncio
async def test_auto_seed_reconcile_does_not_overwrite_user_default_model(_module_db, monkeypatch):
    """The reconciliation loop must not clear user-set default_model values."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _auto_seed_provider_profiles()

    # Simulate a user setting an explicit model on the seeded profile.
    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex_default")
        assert profile is not None
        profile.default_model = "gpt-user-custom"
        await session.commit()

    # Run auto-seed again — it must not overwrite the user-set value.
    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex_default")
        assert profile is not None
        # User-set value must be preserved; reconciliation loop is a no-op
        # because desired_default_model is None for the standard profiles.
        assert profile.default_model == "gpt-user-custom"

@pytest.mark.asyncio
async def test_auto_seed_reconciles_legacy_codex_default_provider(
    _module_db, monkeypatch
):
    """Legacy codex_default rows should be corrected from MoonLadder to OpenAI."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex_default")
        assert profile is not None
        profile.provider_id = "moonladder"
        profile.provider_label = "MoonLadder"
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "codex_default")
        assert profile is not None
        assert profile.provider_id == "openai"
        assert profile.provider_label == "OpenAI"

@pytest.mark.asyncio
async def test_auto_seed_backfills_claude_api_key_clear_env_for_existing_profile(
    _module_db, monkeypatch
):
    """Existing Claude OAuth profiles should clear the newer Claude API key alias."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "claude_anthropic")
        assert profile is not None
        profile.clear_env_keys = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CUSTOM_ENV"]
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "claude_anthropic")
        assert profile is not None
        assert profile.clear_env_keys == [
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "CUSTOM_ENV",
            "CLAUDE_API_KEY",
        ]

@pytest.mark.asyncio
async def test_auto_seed_excludes_minimax_when_env_unset(_module_db, monkeypatch):
    """When MINIMAX_API_KEY is absent, only the 3 default profiles are seeded."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"gemini_cli", "codex_cli", "claude_code"}

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    profile_ids = {p.profile_id for p in profiles}
    assert "claude_minimax" not in profile_ids
    assert "claude_anthropic" in profile_ids
    assert len(profiles) == 3

@pytest.mark.asyncio
async def test_auto_seed_includes_openrouter_codex_profile_when_env_set(
    _module_db, monkeypatch
):
    """OPENROUTER_API_KEY should seed a composite Codex provider profile."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    seeded = await _auto_seed_provider_profiles()
    assert "codex_cli" in seeded

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 4
    profile_ids = {p.profile_id for p in profiles}
    assert "codex_openrouter_qwen36_plus" in profile_ids

    profile = next(p for p in profiles if p.profile_id == "codex_openrouter_qwen36_plus")
    assert profile.runtime_id == "codex_cli"
    assert profile.provider_id == "openrouter"
    assert profile.is_default is False
    assert profile.default_model == "qwen/qwen3.6-plus"
    assert profile.secret_refs == {"provider_api_key": "env://OPENROUTER_API_KEY"}
    assert profile.env_template == {
        "OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"}
    }
    assert profile.file_templates == [
        {
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml",
            "merge_strategy": "replace",
            "content_template": {
                "model_provider": "openrouter",
                "model_reasoning_effort": "high",
                "model": "qwen/qwen3.6-plus",
                "profile": "openrouter_qwen36_plus",
                "model_providers": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "env_key": "OPENROUTER_API_KEY",
                        "wire_api": "responses",
                    }
                },
                "profiles": {
                    "openrouter_qwen36_plus": {
                        "model_provider": "openrouter",
                        "model": "qwen/qwen3.6-plus",
                    }
                },
            },
            "permissions": "0600",
        }
    ]
    assert profile.home_path_overrides == {
        "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
    }
    assert profile.command_behavior == {"suppress_default_model_flag": True}
    assert profile.max_parallel_runs == 4
    assert profile.cooldown_after_429_seconds == 300

    codex_default = next(p for p in profiles if p.profile_id == "codex_default")
    assert codex_default.is_default is True

@pytest.mark.asyncio
async def test_auto_seed_reconciles_openrouter_codex_config_template_for_existing_profile(
    _module_db, monkeypatch
):
    from api_service.main import (
        _auto_seed_provider_profiles,
        _legacy_codex_openrouter_qwen36_plus_file_templates,
    )

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )
        profile.file_templates = _legacy_codex_openrouter_qwen36_plus_file_templates()
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )

    content_template = profile.file_templates[0]["content_template"]
    assert content_template["model_provider"] == "openrouter"
    assert content_template["model_reasoning_effort"] == "high"
    assert content_template["model"] == "qwen/qwen3.6-plus"

@pytest.mark.asyncio
async def test_auto_seed_reconciles_deprecated_openrouter_codex_seed_model(
    _module_db, monkeypatch
):
    from api_service.main import (
        _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL,
        _auto_seed_provider_profiles,
        _codex_openrouter_qwen36_plus_file_templates,
    )

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )
        profile.default_model = _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL
        profile.file_templates = _codex_openrouter_qwen36_plus_file_templates(
            _LEGACY_CODEX_OPENROUTER_QWEN36_PLUS_FREE_MODEL
        )
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )

    assert profile.default_model == "qwen/qwen3.6-plus"
    content_template = profile.file_templates[0]["content_template"]
    assert content_template["model"] == "qwen/qwen3.6-plus"
    assert (
        content_template["profiles"]["openrouter_qwen36_plus"]["model"]
        == "qwen/qwen3.6-plus"
    )

@pytest.mark.asyncio
async def test_auto_seed_does_not_overwrite_custom_openrouter_codex_config_template(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    await _auto_seed_provider_profiles()

    custom_template = [
        {
            "path": "{{runtime_support_dir}}/codex-home/config.toml",
            "format": "toml",
            "merge_strategy": "replace",
            "content_template": {
                "model_provider": "openrouter",
                "model_reasoning_effort": "medium",
                "model": "openrouter/custom-model",
                "profile": "openrouter_qwen36_plus",
                "model_providers": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "env_key": "OPENROUTER_API_KEY",
                        "wire_api": "responses",
                    }
                },
                "profiles": {
                    "openrouter_qwen36_plus": {
                        "model_provider": "openrouter",
                        "model": "openrouter/custom-model",
                    }
                },
            },
            "permissions": "0600",
        }
    ]

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )
        profile.file_templates = custom_template
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "codex_openrouter_qwen36_plus"
        )

    assert profile.file_templates == custom_template
