"""Integration tests for multi-profile OpenRouter support (T006).

Tests verify:
1. Two distinct OpenRouter profiles are independently resolvable (FR-001, FR-014).
2. Priority-based selection among two OpenRouter profiles (FR-009).
3. Profile data round-trip through provider_profile_list activity (FR-010).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
    ManagedAgentRateLimitPolicy,
)
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _in_memory_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/multi_profile.db"
    engine = create_async_engine(db_url, future=True)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield factory
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _patched_session_context(session_factory):
    async with session_factory() as session:
        yield session


def _fake_profiles(profiles: list[dict[str, Any]]):
    async def _fetcher(*, runtime_id: str):
        return {"profiles": profiles}
    return _fetcher


async def _async_noop(**_kwargs):
    pass


# ---------------------------------------------------------------------------
# T006-1: Two distinct OpenRouter profiles independently resolvable
# ---------------------------------------------------------------------------


async def test_two_openrouter_profiles_independently_resolvable(tmp_path: Path) -> None:
    """Create two OpenRouter profiles with different default_model; verify
    each is independently resolvable by exact execution_profile_ref."""

    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex_openrouter_qwen36_plus",
                    runtime_id="codex_cli",
                    provider_id="openrouter",
                    provider_label="OpenRouter",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                    default_model="qwen/qwen3.6-plus:free",
                    priority=100,
                    max_parallel_runs=4,
                    cooldown_after_429_seconds=300,
                    rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
                    enabled=True,
                )
            )
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex_openrouter_claude_sonnet",
                    runtime_id="codex_cli",
                    provider_id="openrouter",
                    provider_label="OpenRouter",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                    default_model="anthropic/claude-sonnet-4-20250514",
                    priority=50,
                    max_parallel_runs=2,
                    cooldown_after_429_seconds=300,
                    rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
                    enabled=True,
                )
            )
            await session.commit()

            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            import api_service.db.base as _db_base_mod

            orig = _db_base_mod.get_async_session_context
            _db_base_mod.get_async_session_context = lambda: _patched_session_context(
                session_factory
            )
            try:
                result = await activities.provider_profile_list(runtime_id="codex_cli")
            finally:
                _db_base_mod.get_async_session_context = orig

            profiles = result["profiles"]
            assert len(profiles) == 2

            profile_map = {p["profile_id"]: p for p in profiles}
            assert (
                profile_map["codex_openrouter_qwen36_plus"]["default_model"]
                == "qwen/qwen3.6-plus:free"
            )
            assert (
                profile_map["codex_openrouter_claude_sonnet"]["default_model"]
                == "anthropic/claude-sonnet-4-20250514"
            )

            # Verify both profiles have credential_source and runtime_materialization_mode
            for p in profiles:
                assert p["credential_source"] == "secret_ref"
                assert p["runtime_materialization_mode"] == "composite"


# ---------------------------------------------------------------------------
# T006-2: Priority-based selection among two OpenRouter profiles (FR-009)
# ---------------------------------------------------------------------------


async def test_priority_based_selection_selects_higher_priority() -> None:
    """When two profiles match provider_id selector, the higher-priority one wins."""

    profiles = [
        {
            "profile_id": "or-low",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "composite",
            "provider_id": "openrouter",
            "priority": 50,
            "default_model": "qwen/qwen3.6-plus:free",
        },
        {
            "profile_id": "or-high",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "composite",
            "provider_id": "openrouter",
            "priority": 150,
            "default_model": "anthropic/claude-sonnet-4-20250514",
        },
    ]

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-priority",
        runtime_id="codex_cli",
    )

    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        ProfileSelector,
    )

    # Request by provider_id — should pick higher priority (or-high)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="corr-priority",
        idempotencyKey="idem-priority",
        profileSelector=ProfileSelector(providerId="openrouter"),
    )
    handle = await adapter.start(request)
    assert handle.metadata["profile_id"] == "or-high"
    assert handle.metadata["credential_source"] == "secret_ref"


async def test_priority_based_selection_falls_back_when_higher_disabled() -> None:
    """When higher-priority profile is disabled, lower-priority one is selected."""

    profiles = [
        {
            "profile_id": "or-low",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "composite",
            "provider_id": "openrouter",
            "priority": 50,
            "default_model": "qwen/qwen3.6-plus:free",
            "enabled": True,
        },
        {
            "profile_id": "or-high",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "composite",
            "provider_id": "openrouter",
            "priority": 150,
            "default_model": "anthropic/claude-sonnet-4-20250514",
            "enabled": False,
        },
    ]

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-priority-fallback",
        runtime_id="codex_cli",
    )

    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        ProfileSelector,
    )

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="corr-fallback",
        idempotencyKey="idem-fallback",
        profileSelector=ProfileSelector(providerId="openrouter"),
    )
    handle = await adapter.start(request)
    assert handle.metadata["profile_id"] == "or-low"


# ---------------------------------------------------------------------------
# T006-3: Profile data round-trip through provider_profile_list
# ---------------------------------------------------------------------------


async def test_profile_roundtrip_via_provider_profile_list(tmp_path: Path) -> None:
    """Verify that a full OpenRouter profile with rich fields round-trips
    correctly through the provider_profile_list activity."""

    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex_openrouter_test",
                    runtime_id="codex_cli",
                    provider_id="openrouter",
                    provider_label="OpenRouter (Test)",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                    default_model="qwen/qwen3.6-plus:free",
                    secret_refs={"provider_api_key": "env://OPENROUTER_API_KEY"},
                    clear_env_keys=["OPENAI_API_KEY", "OPENROUTER_API_KEY"],
                    env_template={
                        "OPENROUTER_API_KEY": {
                            "from_secret_ref": "provider_api_key"
                        }
                    },
                    file_templates=[
                        {
                            "path": "{{runtime_support_dir}}/codex-home/config.toml",
                            "format": "toml",
                            "merge_strategy": "replace",
                            "content_template": {
                                "model_provider": "openrouter"
                            },
                        }
                    ],
                    home_path_overrides={
                        "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
                    },
                    command_behavior={"suppress_default_model_flag": True},
                    priority=200,
                    tags=["test", "openrouter"],
                    max_parallel_runs=2,
                    cooldown_after_429_seconds=120,
                    rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
                    enabled=True,
                )
            )
            await session.commit()

            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            import api_service.db.base as _db_base_mod

            orig = _db_base_mod.get_async_session_context
            _db_base_mod.get_async_session_context = lambda: _patched_session_context(
                session_factory
            )
            try:
                result = await activities.provider_profile_list(runtime_id="codex_cli")
            finally:
                _db_base_mod.get_async_session_context = orig

            profiles = result["profiles"]
            assert len(profiles) == 1
            p = profiles[0]

            assert p["profile_id"] == "codex_openrouter_test"
            assert p["provider_id"] == "openrouter"
            assert p["provider_label"] == "OpenRouter (Test)"
            assert p["credential_source"] == "secret_ref"
            assert p["runtime_materialization_mode"] == "composite"
            assert p["default_model"] == "qwen/qwen3.6-plus:free"
            assert p["secret_refs"] == {"provider_api_key": "env://OPENROUTER_API_KEY"}
            assert p["clear_env_keys"] == ["OPENAI_API_KEY", "OPENROUTER_API_KEY"]
            assert p["env_template"] == {
                "OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"}
            }
            assert p["file_templates"] == [
                {
                    "path": "{{runtime_support_dir}}/codex-home/config.toml",
                    "format": "toml",
                    "merge_strategy": "replace",
                    "content_template": {"model_provider": "openrouter"},
                }
            ]
            assert p["home_path_overrides"] == {
                "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
            }
            assert p["command_behavior"] == {"suppress_default_model_flag": True}
            assert p["priority"] == 200
            assert p["tags"] == ["test", "openrouter"]
            assert p["max_parallel_runs"] == 2
            assert p["cooldown_after_429_seconds"] == 120
