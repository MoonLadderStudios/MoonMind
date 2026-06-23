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
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
    RuntimeMaterializationMode,
)

FIRST_PARTY_SETUP_PROFILE_IDS = {
    "gemini_google_default",
    "gemini_default",
    "codex_openai_default",
    "codex_default",
    "claude_anthropic_default",
    "claude_anthropic",
}

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
    """When the table is empty, auto-seeding should create setup stubs."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"gemini_cli", "codex_cli", "claude_code"}

    # Verify they exist in the DB with correct profile_id values.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == len(FIRST_PARTY_SETUP_PROFILE_IDS)
    profile_ids = {p.profile_id for p in profiles}
    assert profile_ids == FIRST_PARTY_SETUP_PROFILE_IDS
    # Standard profiles are seeded with default_model=None so they inherit
    # the runtime default (codex_cli→gpt-5.5, gemini_cli→gemini-3.1-pro,
    # claude_code→claude-opus-4-8) rather than storing a duplicate value.
    defaults = {p.profile_id: p.default_model for p in profiles}
    assert all(
        defaults[profile_id] is None
        for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS
    )
    runtime_defaults = {p.profile_id: p.is_default for p in profiles}
    assert all(
        runtime_defaults[profile_id] is False
        for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS
    )
    provider_ids = {p.profile_id: p.provider_id for p in profiles}
    assert provider_ids["codex_openai_default"] == "openai"
    assert provider_ids["codex_default"] == "openai"
    assert provider_ids["claude_anthropic_default"] == "anthropic"
    assert provider_ids["claude_anthropic"] == "anthropic"
    provider_labels = {p.profile_id: p.provider_label for p in profiles}
    assert provider_labels["codex_openai_default"] == "OpenAI"
    assert provider_labels["codex_default"] == "OpenAI"
    assert provider_labels["claude_anthropic_default"] == "Anthropic"
    assert provider_labels["claude_anthropic"] == "Anthropic"
    claude_profile = next(
        p for p in profiles if p.profile_id == "claude_anthropic_default"
    )
    assert claude_profile.enabled is False
    assert claude_profile.auth_state == ProviderProfileAuthState.NOT_CONFIGURED
    assert (
        claude_profile.disabled_reason
        == ProviderProfileDisabledReason.MISSING_CREDENTIALS
    )
    assert claude_profile.credential_source == ProviderCredentialSource.NONE
    assert (
        claude_profile.runtime_materialization_mode
        == RuntimeMaterializationMode.API_KEY_ENV
    )
    assert claude_profile.volume_ref is None
    assert claude_profile.volume_mount_path is None
    assert claude_profile.clear_env_keys == [
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    ]
    first_party_clear_keys = {p.profile_id: p.clear_env_keys for p in profiles}
    assert first_party_clear_keys["claude_anthropic"] == [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    ]
    assert first_party_clear_keys["gemini_default"] == [
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ]
    assert first_party_clear_keys["codex_default"] == [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ]

@pytest.mark.asyncio
async def test_auto_seed_is_idempotent(_module_db, monkeypatch):
    """Calling auto-seed twice should not duplicate profiles."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    first = await _auto_seed_provider_profiles()
    assert len(first) == len(FIRST_PARTY_SETUP_PROFILE_IDS)

    second = await _auto_seed_provider_profiles()
    assert second == []

    # Still only 3 in DB.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()
    assert len(profiles) == len(FIRST_PARTY_SETUP_PROFILE_IDS)

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
    """When MINIMAX_API_KEY is set, MiniMax Claude and Codex profiles are seeded."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert "claude_code" in seeded
    assert "codex_cli" in seeded

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == len(FIRST_PARTY_SETUP_PROFILE_IDS) + 2
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic" in profile_ids
    assert "claude_minimax" in profile_ids
    assert "codex_minimax_m27" in profile_ids

    # Verify MiniMax profile details.
    mm_profile = next(p for p in profiles if p.profile_id == "claude_minimax")
    assert mm_profile.runtime_id == "claude_code"
    assert mm_profile.secret_refs is not None
    assert mm_profile.secret_refs.get("provider_api_key") == "env://MINIMAX_API_KEY"
    assert mm_profile.env_template is not None
    assert mm_profile.env_template["ANTHROPIC_BASE_URL"] == "https://api.minimax.io/anthropic"
    assert mm_profile.env_template["ANTHROPIC_AUTH_TOKEN"] == {
        "from_secret_ref": "provider_api_key"
    }
    assert mm_profile.env_template["ANTHROPIC_MODEL"] == "MiniMax-M2.7"
    assert mm_profile.env_template["API_TIMEOUT_MS"] == "3000000"
    assert mm_profile.clear_env_keys == [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ]
    assert mm_profile.default_model == "MiniMax-M2.7"
    assert mm_profile.volume_ref is None
    assert mm_profile.volume_mount_path is None
    assert mm_profile.is_default is True
    assert mm_profile.enabled is True
    assert mm_profile.auth_state == ProviderProfileAuthState.CONNECTED
    assert mm_profile.disabled_reason is None

    anthropic_profile = next(p for p in profiles if p.profile_id == "claude_anthropic")
    assert anthropic_profile.is_default is False

    codex_mm_profile = next(p for p in profiles if p.profile_id == "codex_minimax_m27")
    assert codex_mm_profile.runtime_id == "codex_cli"
    assert (
        codex_mm_profile.runtime_materialization_mode
        == RuntimeMaterializationMode.COMPOSITE
    )
    assert codex_mm_profile.secret_refs == {"provider_api_key": "env://MINIMAX_API_KEY"}
    assert codex_mm_profile.env_template == {
        "MINIMAX_API_KEY": {"from_secret_ref": "provider_api_key"}
    }
    assert codex_mm_profile.clear_env_keys == [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
    ]
    assert codex_mm_profile.file_templates[0]["merge_strategy"] == "deep_merge"
    assert codex_mm_profile.file_templates[0]["permissions"] == "0600"
    assert codex_mm_profile.file_templates[0]["content_template"]["profile"] == "m27"
    assert codex_mm_profile.model_overrides == {"codex_profile_name": "m27"}

@pytest.mark.asyncio
async def test_auto_seed_adds_minimax_after_initial_seed(_module_db, monkeypatch):
    """MINIMAX_API_KEY added after initial seed → claude_minimax is inserted on next call."""
    from api_service.main import _auto_seed_provider_profiles

    # First seed without MiniMax key.
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    first = await _auto_seed_provider_profiles()
    assert len(first) == len(FIRST_PARTY_SETUP_PROFILE_IDS)

    # Now the key becomes available.
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    second = await _auto_seed_provider_profiles()
    assert "claude_code" in second  # minimax profile was added

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == len(FIRST_PARTY_SETUP_PROFILE_IDS) + 2
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic" in profile_ids
    assert "claude_minimax" in profile_ids
    assert "codex_minimax_m27" in profile_ids

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
async def test_auto_seed_reconciles_claude_display_default_model(
    _module_db, monkeypatch
):
    """Known stale Claude display labels should be rewritten to CLI model IDs."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "claude_anthropic")
        assert profile is not None
        profile.default_model = "Sonnet 4.6"
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "claude_anthropic")
        assert profile is not None
        assert profile.default_model == "claude-sonnet-4-6"

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
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_API_KEY",
        ]

@pytest.mark.asyncio
async def test_auto_seed_backfills_first_party_clear_env_for_existing_profiles(
    _module_db, monkeypatch
):
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    await _auto_seed_provider_profiles()

    async with db_base.async_session_maker() as session:
        codex_profile = await session.get(ManagedAgentProviderProfile, "codex_default")
        gemini_profile = await session.get(ManagedAgentProviderProfile, "gemini_default")
        assert codex_profile is not None
        assert gemini_profile is not None
        codex_profile.clear_env_keys = ["OPENAI_API_KEY", "CUSTOM_CODEX_ENV"]
        gemini_profile.clear_env_keys = ["GEMINI_API_KEY", "CUSTOM_GEMINI_ENV"]
        await session.commit()

    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        codex_profile = await session.get(ManagedAgentProviderProfile, "codex_default")
        gemini_profile = await session.get(ManagedAgentProviderProfile, "gemini_default")

    assert codex_profile.clear_env_keys == [
        "OPENAI_API_KEY",
        "CUSTOM_CODEX_ENV",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ]
    assert gemini_profile.clear_env_keys == [
        "GEMINI_API_KEY",
        "CUSTOM_GEMINI_ENV",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ]

@pytest.mark.asyncio
async def test_auto_seed_excludes_minimax_when_env_unset(_module_db, monkeypatch):
    """When MINIMAX_API_KEY is absent, only first-party setup stubs are seeded."""
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
    assert "codex_minimax_m27" not in profile_ids
    assert "claude_anthropic" in profile_ids
    assert len(profiles) == len(FIRST_PARTY_SETUP_PROFILE_IDS)

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

    assert len(profiles) == len(FIRST_PARTY_SETUP_PROFILE_IDS) + 1
    profile_ids = {p.profile_id for p in profiles}
    assert "codex_openrouter_qwen36_plus" in profile_ids

    profile = next(p for p in profiles if p.profile_id == "codex_openrouter_qwen36_plus")
    assert profile.runtime_id == "codex_cli"
    assert profile.provider_id == "openrouter"
    assert profile.is_default is True
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
    assert codex_default.is_default is False

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


@pytest.mark.asyncio
async def test_auto_seed_first_party_stubs_have_default_readiness_labels(
    _module_db, monkeypatch
):
    """First-party setup stubs carry the documented command_behavior labels."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    await _auto_seed_provider_profiles()

    expected_command_behavior = {
        "supported_auth_methods": ["oauth_volume", "secret_ref"],
        "auth_actions": ["connect_oauth", "add_api_key"],
        "auth_status_label": "Not connected",
        "auth_readiness": {
            "connected": False,
            "launch_ready": False,
        },
    }

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = {p.profile_id: p for p in result.scalars().all()}

    for profile_id in FIRST_PARTY_SETUP_PROFILE_IDS:
        profile = profiles[profile_id]
        assert profile.command_behavior == expected_command_behavior, profile_id
        # Stubs stay pre-OAuth: no home_path_overrides until setup succeeds.
        assert not profile.home_path_overrides
