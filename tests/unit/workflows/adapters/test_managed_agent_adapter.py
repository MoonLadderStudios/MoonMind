"""Unit tests for ManagedAgentAdapter and provider_profile_list activity.

Tests cover:
- Profile resolution (auto, by ID, missing)
- Environment shaping (OAuth clears sensitive vars, API-key sets ref)
- Slot request / release signalling
- 429 cooldown reporting
- provider_profile_list activity method (happy path + empty DB)

Marked with pytest.mark.asyncio to align with the existing test suite
convention (see test_activity_runtime.py).
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
from moonmind.auth.env_shaping import (
    OAUTH_CLEARED_VARS,
    shape_environment_for_api_key,
    shape_environment_for_oauth,
)
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    ProfileResolutionError,
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
    db_url = f"sqlite+aiosqlite:///{tmp_path}/managed_adapter.db"
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


def _fake_profiles(profiles: list[dict[str, Any]]):
    """Return an async callable that always yields the given profiles list."""

    async def _fetcher(*, runtime_id: str):
        return {"profiles": profiles}

    return _fetcher


def _noop_slot_requester(**_kwargs):
    pass


async def _async_noop(**_kwargs):
    pass


# ---------------------------------------------------------------------------
# Environment shaping tests
# ---------------------------------------------------------------------------


async def test_shape_environment_for_oauth_clears_sensitive_vars():
    base = {
        "HOME": "/home/user",
        "GOOGLE_API_KEY": "secret-key",
        "GEMINI_API_KEY": "gemini-key",
        "OPENAI_API_KEY": "openai-key",
        "PATH": "/usr/bin",
    }
    shaped = shape_environment_for_oauth(base, volume_mount_path="/mnt/auth")
    # Sensitive keys must be absent.
    for key in OAUTH_CLEARED_VARS:
        assert key not in shaped, f"Expected {key} to be cleared"
    assert shaped["HOME"] == "/home/user"
    assert shaped["MANAGED_AUTH_VOLUME_PATH"] == "/mnt/auth"


async def test_shape_environment_for_oauth_without_mount_path():
    base = {"HOME": "/home/user", "GEMINI_API_KEY": "secret"}
    shaped = shape_environment_for_oauth(base, volume_mount_path=None)
    assert "MANAGED_AUTH_VOLUME_PATH" not in shaped
    assert "GEMINI_API_KEY" not in shaped


async def test_shape_environment_for_oauth_clears_github_cli_tokens():
    base = {
        "HOME": "/home/user",
        "GH_TOKEN": "ghp-token",
        "GITHUB_TOKEN": "github-token",
        "OPENAI_API_KEY": "secret",
    }
    shaped = shape_environment_for_oauth(base, volume_mount_path=None)
    assert "GH_TOKEN" not in shaped
    assert "GITHUB_TOKEN" not in shaped
    assert "OPENAI_API_KEY" not in shaped


async def test_shape_environment_for_api_key_sets_ref():
    base = {"HOME": "/home/user"}
    shaped = shape_environment_for_api_key(
        base, api_key_ref="secrets/my-api-key", account_label="ci-bot"
    )
    assert shaped["MANAGED_API_KEY_REF"] == "secrets/my-api-key"
    assert shaped["MANAGED_ACCOUNT_LABEL"] == "ci-bot"
    assert shaped["HOME"] == "/home/user"


async def test_shape_environment_for_api_key_without_ref():
    base = {"HOME": "/home/user"}
    shaped = shape_environment_for_api_key(base, api_key_ref=None, account_label=None)
    assert "MANAGED_API_KEY_REF" not in shaped
    assert "MANAGED_ACCOUNT_LABEL" not in shaped


# ---------------------------------------------------------------------------
# Profile resolution tests
# ---------------------------------------------------------------------------


async def test_resolve_profile_by_id():
    profiles = [
        {"profile_id": "prof-A", "auth_mode": "api_key"},
        {"profile_id": "prof-B", "auth_mode": "oauth"},
    ]
    calls: list[tuple] = []

    async def _slot_req(*, requester_workflow_id: str, runtime_id: str):
        calls.append(("slot_request", requester_workflow_id, runtime_id))

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_slot_req,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-123",
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        executionProfileRef="prof-B",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )
    handle = await adapter.start(request)
    assert handle.agent_kind == "managed"
    assert handle.metadata["profile_id"] == "prof-B"
    assert handle.metadata["auth_mode"] == "oauth"
    # Slot acquisition is now handled by AgentRun before adapter.start(),
    # so the adapter should NOT send a redundant slot request.
    assert ("slot_request", "wf-123", "gemini_cli") not in calls


async def test_resolve_profile_auto_picks_first():
    profiles = [
        {"profile_id": "first", "auth_mode": "api_key"},
        {"profile_id": "second", "auth_mode": "oauth"},
    ]

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-auto",
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        executionProfileRef="auto",
        correlationId="corr-auto",
        idempotencyKey="idem-auto",
    )
    handle = await adapter.start(request)
    assert handle.metadata["profile_id"] == "first"


async def test_resolve_profile_selector_filters():
    profiles = [
        {
            "profile_id": "prof-a",
            "auth_mode": "api_key",
            "provider_id": "anthropic",
            "runtime_materialization_mode": "env",
            "tags": ["premium", "fast"],
            "priority": 10,
        },
        {
            "profile_id": "prof-b",
            "auth_mode": "oauth",
            "provider_id": "anthropic",
            "runtime_materialization_mode": "oauth",
            "tags": ["standard"],
            "priority": 20,
        },
        {
            "profile_id": "prof-c",
            "auth_mode": "api_key",
            "provider_id": "openai",
            "runtime_materialization_mode": "env",
            "tags": ["premium"],
            "priority": 5,
            "available_slots": 5,
        },
        {
            "profile_id": "prof-d",
            "auth_mode": "api_key",
            "provider_id": "openai",
            "runtime_materialization_mode": "env",
            "tags": ["premium"],
            "priority": 5,
            "available_slots": 10,
        },
    ]

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-selectors",
    )

    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        ProfileSelector,
    )

    # 1. Match by exact providerId and runtimeMaterializationMode
    req1 = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        correlationId="corr",
        idempotencyKey="idem",
        profileSelector=ProfileSelector(
            providerId="anthropic", runtimeMaterializationMode="oauth"
        ),
    )
    h1 = await adapter.start(req1)
    assert h1.metadata["profile_id"] == "prof-b"

    # 2. Match by tagsAny
    req2 = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        correlationId="corr",
        idempotencyKey="idem",
        profileSelector=ProfileSelector(tagsAny=["premium"]),
    )
    h2 = await adapter.start(req2)
    assert h2.metadata["profile_id"] == "prof-a"

    # 3. Match by tagsAll
    req3 = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        correlationId="corr",
        idempotencyKey="idem",
        profileSelector=ProfileSelector(tagsAll=["premium", "fast"]),
    )
    h3 = await adapter.start(req3)
    assert h3.metadata["profile_id"] == "prof-a"

    # 4. Tie-breaking by available slots when priority is equal
    req4 = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        correlationId="corr",
        idempotencyKey="idem",
        profileSelector=ProfileSelector(providerId="openai"),
    )
    h4 = await adapter.start(req4)
    assert h4.metadata["profile_id"] == "prof-d"

    # 5. Invalid selector causing error
    req5 = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        correlationId="corr",
        idempotencyKey="idem",
        profileSelector=ProfileSelector(tagsAll=["nonexistent"]),
    )
    with pytest.raises(
        ProfileResolutionError, match="No eligible provider profiles"
    ):
        await adapter.start(req5)



async def test_start_uses_passthrough_keys_for_github_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = [
        {
            "profile_id": "oauth-prof",
            "credential_source": ProviderCredentialSource.OAUTH_VOLUME,
            "volume_mount_path": "/mnt/auth",
            "command_template": ["gemini"],
        }
    ]
    captured_payload: dict[str, Any] = {}

    async def _run_launcher(**kwargs: Any):
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            captured_payload.update(payload)
        return {"status": "launching"}

    monkeypatch.setenv("GH_TOKEN", "ghp-direct")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-legacy")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-gh-token",
        run_launcher=_run_launcher,
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        executionProfileRef="oauth-prof",
        correlationId="corr-gh",
        idempotencyKey="idem-gh",
    )
    await adapter.start(request)

    assert captured_payload["workflow_id"] == "wf-gh-token"

    profile_payload = (
        captured_payload.get("profile")
        if isinstance(captured_payload.get("profile"), dict)
        else {}
    )
    env_overrides = (
        profile_payload.get("env_overrides")
        if isinstance(profile_payload.get("env_overrides"), dict)
        else profile_payload.get("envOverrides")
        if isinstance(profile_payload.get("envOverrides"), dict)
        else {}
    )
    passthrough_env_keys = (
        profile_payload.get("passthrough_env_keys")
        if isinstance(profile_payload.get("passthrough_env_keys"), list)
        else profile_payload.get("passthroughEnvKeys")
        if isinstance(profile_payload.get("passthroughEnvKeys"), list)
        else []
    )
    assert set(passthrough_env_keys) == {"GH_TOKEN", "GITHUB_TOKEN"}
    assert "GH_TOKEN" not in env_overrides
    assert "GITHUB_TOKEN" not in env_overrides
    assert "OPENAI_API_KEY" not in env_overrides


async def test_start_applies_runtime_env_overrides_and_key_target() -> None:
    profiles = [
        {
            "profile_id": "minimax",
            "auth_mode": "api_key",
            "api_key_ref": "MINIMAX_API_KEY",
            "api_key_env_var": "ANTHROPIC_AUTH_TOKEN",
            "runtime_env_overrides": {
                "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
                "ANTHROPIC_MODEL": "MiniMax-M2.7",
            },
            "command_template": ["claude"],
        }
    ]
    captured_payload: dict[str, Any] = {}

    async def _run_launcher(**kwargs: Any):
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            captured_payload.update(payload)
        return {"status": "launching"}

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-mm",
        runtime_id="claude_code",
        run_launcher=_run_launcher,
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        executionProfileRef="minimax",
        correlationId="corr-mm",
        idempotencyKey="idem-mm",
    )
    await adapter.start(request)

    profile_payload = captured_payload.get("profile") or {}
    env_overrides = profile_payload.get("env_overrides") or profile_payload.get(
        "envOverrides"
    )
    assert isinstance(env_overrides, dict)
    assert env_overrides.get("MANAGED_API_KEY_REF") is None
    assert env_overrides.get("MANAGED_API_KEY_TARGET_ENV") is None
    assert env_overrides.get("ANTHROPIC_BASE_URL") == "https://api.minimax.io/anthropic"
    assert env_overrides.get("ANTHROPIC_MODEL") == "MiniMax-M2.7"


async def test_start_passes_profile_default_model_to_launcher() -> None:
    profiles = [
        {
            "profile_id": "claude-minimax",
            "default_model": "MiniMax-M2.7",
            "model_overrides": {"small_fast": "MiniMax-M2.7"},
            "command_template": ["claude"],
        }
    ]
    captured_payload: dict[str, Any] = {}

    async def _run_launcher(**kwargs: Any):
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            captured_payload.update(payload)
        return {"status": "launching"}

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-profile-default-model",
        runtime_id="claude_code",
        run_launcher=_run_launcher,
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        executionProfileRef="claude-minimax",
        correlationId="corr-profile-default-model",
        idempotencyKey="idem-profile-default-model",
    )
    await adapter.start(request)

    profile_payload = captured_payload.get("profile") or {}
    assert profile_payload.get("defaultModel") == "MiniMax-M2.7"
    assert profile_payload.get("modelOverrides") == {"small_fast": "MiniMax-M2.7"}


async def test_start_falls_back_to_runtime_default_model_when_profile_blank() -> None:
    profiles = [
        {
            "profile_id": "codex-defaults",
            "command_template": ["codex", "exec"],
        }
    ]
    captured_payload: dict[str, Any] = {}

    async def _run_launcher(**kwargs: Any):
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            captured_payload.update(payload)
        return {"status": "launching"}

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-runtime-default-model",
        runtime_id="codex_cli",
        run_launcher=_run_launcher,
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        executionProfileRef="codex-defaults",
        correlationId="corr-runtime-default-model",
        idempotencyKey="idem-runtime-default-model",
    )
    await adapter.start(request)

    profile_payload = captured_payload.get("profile") or {}
    assert profile_payload.get("defaultModel") == "gpt-5.4"
    assert profile_payload.get("defaultEffort") == "high"


async def test_start_applies_proxy_mode_when_tagged_proxy_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION", "1")
    profiles = [
        {
            "profile_id": "proxy-prof",
            "auth_mode": "api_key",
            "api_key_ref": "db://123", # Not evaluated in proxy
            "command_template": ["claude"],
            "tags": ["proxy-first"],
            "provider_id": "anthropic",
            "secret_refs": {"anthropic_api_key": "db://123"},
        }
    ]
    captured_payload: dict[str, Any] = {}

    async def _run_launcher(**kwargs: Any):
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            captured_payload.update(payload)
        return {"status": "launching"}

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-proxy",
        runtime_id="claude_code",
        run_launcher=_run_launcher,
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        executionProfileRef="proxy-prof",
        correlationId="corr-proxy",
        idempotencyKey="idem-proxy",
    )
    await adapter.start(request)

    profile_payload = captured_payload.get("profile") or {}
    env_overrides = profile_payload.get("env_overrides") or profile_payload.get("envOverrides")
    
    assert isinstance(env_overrides, dict)
    # The proxy token must be generated but db_encrypted should not leak
    assert "db://123" not in env_overrides.values()
    assert "MANAGED_API_KEY_REF" not in env_overrides
    assert "MOONMIND_PROXY_TOKEN" in env_overrides
    assert "ANTHROPIC_BASE_URL" in env_overrides
    assert "proxy/anthropic" in env_overrides["ANTHROPIC_BASE_URL"]


async def test_resolve_profile_raises_when_not_found():
    profiles = [{"profile_id": "exists", "auth_mode": "api_key"}]

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-404",
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="gemini_cli",
        executionProfileRef="does-not-exist",
        correlationId="corr-x",
        idempotencyKey="idem-x",
    )
    with pytest.raises(ProfileResolutionError, match="not found"):
        await adapter.start(request)


async def test_resolve_profile_raises_when_no_profiles():
    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-empty",
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        executionProfileRef="auto",
        correlationId="corr-none",
        idempotencyKey="idem-none",
    )
    with pytest.raises(ProfileResolutionError, match="No enabled provider profiles"):
        await adapter.start(request)


async def test_start_rejects_non_managed_agent_kind():
    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([{"profile_id": "p", "auth_mode": "api_key"}]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-bad",
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="auto",
        correlationId="corr-ext",
        idempotencyKey="idem-ext",
    )
    with pytest.raises(ValueError, match="managed"):
        await adapter.start(request)


# ---------------------------------------------------------------------------
# Slot release and 429 cooldown tests
# ---------------------------------------------------------------------------


async def test_release_slot_signals_manager():
    released: list[dict] = []

    async def _releaser(*, requester_workflow_id: str, profile_id: str):
        released.append(
            {"wf": requester_workflow_id, "profile_id": profile_id}
        )

    profiles = [{"profile_id": "prof-release", "auth_mode": "api_key"}]
    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_releaser,
        cooldown_reporter=_async_noop,
        workflow_id="wf-release",
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    await adapter.start(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="gemini_cli",
            executionProfileRef="prof-release",
            correlationId="corr-rel",
            idempotencyKey="idem-rel",
        )
    )
    assert adapter._active_profile_id == "prof-release"

    await adapter.release_slot()
    assert adapter._active_profile_id is None
    assert len(released) == 1
    assert released[0]["profile_id"] == "prof-release"
    assert released[0]["wf"] == "wf-release"


async def test_report_429_cooldown_uses_active_profile():
    reported: list[dict] = []

    async def _reporter(*, profile_id: str, cooldown_seconds: int):
        reported.append({"profile_id": profile_id, "secs": cooldown_seconds})

    profiles = [{"profile_id": "prof-429", "auth_mode": "api_key"}]
    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_reporter,
        workflow_id="wf-429",
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    await adapter.start(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="gemini_cli",
            executionProfileRef="prof-429",
            correlationId="corr-429",
            idempotencyKey="idem-429",
        )
    )
    await adapter.report_429_cooldown(cooldown_seconds=600)
    assert len(reported) == 1
    assert reported[0]["profile_id"] == "prof-429"
    assert reported[0]["secs"] == 600


async def test_report_429_cooldown_raises_without_active_profile():
    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-429-noactive",
    )
    with pytest.raises(ValueError, match="profile_id is required"):
        await adapter.report_429_cooldown(cooldown_seconds=300)


# ---------------------------------------------------------------------------
# provider_profile_list activity tests (integration against in-memory SQLite DB)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _patched_session_context(session_factory):
    """Yield a session from *session_factory* as an async-context-manager.

    Used to monkeypatch ``get_async_session_context`` so that the
    ``provider_profile_list`` activity method hits the in-memory SQLite DB
    instead of the real Postgres connection string.
    """
    async with session_factory() as session:
        yield session


async def test_provider_profile_list_returns_enabled_profiles(tmp_path: Path):
    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="gprofile-1",
                    runtime_id="gemini_cli",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref=None,
                    volume_mount_path="/mnt/auth",
                    account_label="primary",
                    max_parallel_runs=2,
                    cooldown_after_429_seconds=300,
                    rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
                    enabled=True,
                )
            )
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="gprofile-disabled",
                    runtime_id="gemini_cli",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.ENV_BUNDLE,
                    max_parallel_runs=1,
                    cooldown_after_429_seconds=300,
                    rate_limit_policy=ManagedAgentRateLimitPolicy.BACKOFF,
                    enabled=False,
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
        _db_base_mod.get_async_session_context = lambda: _patched_session_context(session_factory)
        try:
            result = await activities.provider_profile_list(runtime_id="gemini_cli")
        finally:
            _db_base_mod.get_async_session_context = orig

        assert "profiles" in result
        profiles = result["profiles"]
        assert len(profiles) == 1
        assert profiles[0]["profile_id"] == "gprofile-1"
        assert profiles[0]["credential_source"] == "oauth_volume"
        assert profiles[0]["enabled"] is True
        assert profiles[0]["max_parallel_runs"] == 2


async def test_provider_profile_list_returns_empty_for_unknown_runtime(tmp_path: Path):
    async with _in_memory_db(tmp_path) as session_factory:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session_factory()),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        activities = TemporalArtifactActivities(service)

        import api_service.db.base as _db_base_mod

        orig = _db_base_mod.get_async_session_context
        _db_base_mod.get_async_session_context = lambda: _patched_session_context(session_factory)
        try:
            result = await activities.provider_profile_list(runtime_id="nonexistent_runtime")
        finally:
            _db_base_mod.get_async_session_context = orig

        assert result == {"profiles": []}


async def test_provider_profile_list_filters_by_runtime_id(tmp_path: Path):
    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            for runtime, pid in [("gemini_cli", "g1"), ("claude_code", "c1")]:
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=pid,
                        runtime_id=runtime,
                        credential_source=ProviderCredentialSource.SECRET_REF,
                        runtime_materialization_mode=RuntimeMaterializationMode.ENV_BUNDLE,
                        max_parallel_runs=1,
                        cooldown_after_429_seconds=300,
                        rate_limit_policy=ManagedAgentRateLimitPolicy.QUEUE,
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
        _db_base_mod.get_async_session_context = lambda: _patched_session_context(session_factory)
        try:
            result = await activities.provider_profile_list(runtime_id="claude_code")
        finally:
            _db_base_mod.get_async_session_context = orig

        profiles = result["profiles"]
        assert len(profiles) == 1
        assert profiles[0]["profile_id"] == "c1"


async def test_provider_profile_list_preserves_secret_ref_materialization_fields(
    tmp_path: Path,
):
    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="claude-minimax",
                    runtime_id="claude_code",
                    credential_source=ProviderCredentialSource.SECRET_REF,
                    runtime_materialization_mode=RuntimeMaterializationMode.ENV_BUNDLE,
                    secret_refs={"ANTHROPIC_AUTH_TOKEN": "MINIMAX_API_KEY"},
                    clear_env_keys=["ANTHROPIC_API_KEY", "OPENAI_API_KEY"],
                    env_template={
                        "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
                        "ANTHROPIC_MODEL": "MiniMax-M2.7",
                    },
                    max_parallel_runs=1,
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
            result = await activities.provider_profile_list(runtime_id="claude_code")
        finally:
            _db_base_mod.get_async_session_context = orig

        profiles = result["profiles"]
        assert len(profiles) == 1
        assert profiles[0]["profile_id"] == "claude-minimax"
        assert profiles[0]["secret_refs"] == {
            "ANTHROPIC_AUTH_TOKEN": "MINIMAX_API_KEY"
        }
        assert profiles[0]["clear_env_keys"] == [
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
        ]
        assert profiles[0]["runtime_env_overrides"] == {
            "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
            "ANTHROPIC_MODEL": "MiniMax-M2.7",
        }


# ---------------------------------------------------------------------------
# Store-backed status / fetch_result tests
# ---------------------------------------------------------------------------


async def test_status_reads_from_store(tmp_path: Path):
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-status-1",
            agent_id="gemini_cli",
            runtime_id="gemini_cli",
            status="completed",
            started_at=datetime.now(tz=UTC),
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-store",
        run_store=store,
    )

    status = await adapter.status("run-status-1")
    assert status.status == "completed"
    assert status.agent_id == "gemini_cli"


async def test_status_falls_back_to_stub_without_store():
    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-nostub",
    )

    status = await adapter.status("nonexistent")
    assert status.status == "running"  # stub default


async def test_fetch_result_reads_from_store(tmp_path: Path):
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-result-1",
            agent_id="gemini_cli",
            runtime_id="gemini_cli",
            status="completed",
            started_at=datetime.now(tz=UTC),
            log_artifact_ref="artifact://logs/stdout",
            diagnostics_ref="artifact://diag/123",
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-result",
        run_store=store,
    )

    result = await adapter.fetch_result("run-result-1")
    assert result.failure_class is None
    assert "artifact://logs/stdout" in result.output_refs
    assert "artifact://diag/123" in result.output_refs


async def test_fetch_result_marks_failed_pr_resolver_artifact_as_failure(
    tmp_path: Path,
):
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    workspace_path = tmp_path / "workspace"
    artifacts_path = workspace_path / "artifacts"
    artifacts_path.mkdir(parents=True)
    (artifacts_path / "pr_resolver_result.json").write_text(
        (
            "{\n"
            '  "status": "failed",\n'
            '  "final_reason": "no pull requests found for branch",\n'
            '  "next_step": "manual_review"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-result-pr-failure",
            agent_id="gemini_cli",
            runtime_id="gemini_cli",
            status="completed",
            started_at=datetime.now(tz=UTC),
            workspace_path=str(workspace_path),
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-result-pr-failure",
        run_store=store,
    )

    result = await adapter.fetch_result(
        "run-result-pr-failure", pr_resolver_expected=True
    )
    assert result.failure_class == "execution_error"
    assert result.summary is not None
    assert "pr-resolver reported status 'failed'" in result.summary
    assert "manual_review" in result.summary


async def test_fetch_result_maps_blocked_pr_resolver_result_to_user_error(
    tmp_path: Path,
):
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "attempts_exhausted",\n'
            '  "final_reason": "actionable_comments",\n'
            '  "next_step": "run_fix_comments_skill"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-result-pr-blocked",
            agent_id="gemini_cli",
            runtime_id="gemini_cli",
            status="completed",
            started_at=datetime.now(tz=UTC),
            workspace_path=str(workspace_path),
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-result-pr-blocked",
        run_store=store,
    )

    result = await adapter.fetch_result(
        "run-result-pr-blocked", pr_resolver_expected=True
    )
    assert result.failure_class == "user_error"
    assert result.summary is not None
    assert "pr-resolver reported status 'attempts_exhausted'" in result.summary
    assert "run_fix_comments_skill" in result.summary


async def test_fetch_result_upgrades_generic_failed_exit_with_pr_result(
    tmp_path: Path,
):
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "attempts_exhausted",\n'
            '  "final_reason": "actionable_comments",\n'
            '  "next_step": "run_fix_comments_skill"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-result-pr-generic-failed",
            agent_id="gemini_cli",
            runtime_id="gemini_cli",
            status="failed",
            started_at=datetime.now(tz=UTC),
            workspace_path=str(workspace_path),
            failure_class="execution_error",
            error_message="Process exited with code 1",
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-result-pr-generic-failed",
        run_store=store,
    )

    result = await adapter.fetch_result(
        "run-result-pr-generic-failed", pr_resolver_expected=True
    )
    assert result.failure_class == "user_error"
    assert result.summary is not None
    assert "pr-resolver reported status 'attempts_exhausted'" in result.summary
    assert "run_fix_comments_skill" in result.summary


async def test_fetch_result_ignores_merged_pr_resolver_artifact(tmp_path: Path):
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    workspace_path = tmp_path / "workspace"
    artifacts_path = workspace_path / "artifacts"
    artifacts_path.mkdir(parents=True)
    (artifacts_path / "pr_resolver_result.json").write_text(
        "{\n  \"status\": \"merged\",\n  \"next_step\": \"done\"\n}\n",
        encoding="utf-8",
    )

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-result-pr-merged",
            agent_id="gemini_cli",
            runtime_id="gemini_cli",
            status="completed",
            started_at=datetime.now(tz=UTC),
            workspace_path=str(workspace_path),
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-result-pr-merged",
        run_store=store,
    )

    result = await adapter.fetch_result(
        "run-result-pr-merged", pr_resolver_expected=True
    )
    assert result.failure_class is None


async def test_fetch_result_returns_empty_for_non_terminal(tmp_path: Path):
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-running",
            agent_id="gemini_cli",
            runtime_id="gemini_cli",
            status="running",
            started_at=datetime.now(tz=UTC),
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-running",
        run_store=store,
    )

    result = await adapter.fetch_result("run-running")
    # Non-terminal: should return default empty result
    assert result.failure_class is None
    assert result.output_refs == []


async def test_fetch_result_ignores_pr_resolver_artifact_when_not_expected(
    tmp_path: Path,
):
    """When pr_resolver_expected=False (default), pr-resolver artifacts
    in the workspace must NOT override the run result.  This protects
    against agents that autonomously invoke pr-resolver for tasks that
    were not PR-resolution tasks."""
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    workspace_path = tmp_path / "workspace"
    result_dir = workspace_path / "var" / "pr_resolver"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        (
            "{\n"
            '  "status": "attempts_exhausted",\n'
            '  "final_reason": "merge_conflicts",\n'
            '  "next_step": "run_fix_merge_conflicts_skill"\n'
            "}\n"
        ),
        encoding="utf-8",
    )

    store = ManagedRunStore(tmp_path / "run_store")
    store.save(
        ManagedRunRecord(
            run_id="run-result-pr-unexpected",
            agent_id="claude_code",
            runtime_id="claude_code",
            status="completed",
            started_at=datetime.now(tz=UTC),
            workspace_path=str(workspace_path),
        )
    )

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles([]),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-result-pr-unexpected",
        run_store=store,
    )

    # Default: pr_resolver_expected=False
    result = await adapter.fetch_result("run-result-pr-unexpected")
    # The pr-resolver artifact should be ignored — no override
    assert result.failure_class is None
    assert result.summary is not None
    assert "pr-resolver" not in result.summary


# ---------------------------------------------------------------------------
# Regression: env_overrides must be delta-only (not full shaped env)
# ---------------------------------------------------------------------------


async def test_start_with_sensitive_runtime_env_overrides_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for WorkflowTaskFailed loop caused by passing the full
    shaped environment as env_overrides to ManagedRuntimeProfile.

    The claude_minimax profile has ``runtime_env_overrides`` that include a
    key like ``ANTHROPIC_AUTH_TOKEN``, which is flagged as sensitive by the
    ManagedRuntimeProfile validator.  The adapter MUST only put safe,
    profile-specific delta keys (not the full base env) in env_overrides.
    Sensitive runtime_env_overrides entries are excluded from env_overrides and
    must be routed through secret_refs instead.

    Previously this raised:
      ValidationError: envOverrides must not contain raw credential keys
    which caused the AgentRun child workflow to fail every WorkflowTaskFailed
    retry cycle (including cancel handling), making the workflow impossible to
    cancel or terminate gracefully.
    """
    profiles = [
        {
            "profile_id": "claude_minimax",
            "auth_mode": "api_key",
            "api_key_ref": "MINIMAX_API_KEY",
            "api_key_env_var": "ANTHROPIC_AUTH_TOKEN",
            # Sensitive-keyed override: previously triggered ValidationError
            "runtime_env_overrides": {
                "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
                "ANTHROPIC_MODEL": "MiniMax-M2.7",
                # This key has a sensitive fragment ("token") and previously
                # caused the ManagedRuntimeProfile validator to reject the whole
                # env_overrides dict when it was passed as shaped_env.
                "ANTHROPIC_AUTH_TOKEN": "dummy-token-value",
            },
            "secret_refs": {
                "MINIMAX_API_KEY": "secrets/minimax-api-key",
            },
            "command_template": ["claude"],
        }
    ]
    captured_payload: dict = {}

    async def _run_launcher(**kwargs):
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            captured_payload.update(payload)
        return {"status": "launching"}

    # Simulate the ambient worker environment having the MINIMAX key set.
    monkeypatch.setenv("MINIMAX_API_KEY", "raw-minimax-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "raw-anthropic-token")

    adapter = ManagedAgentAdapter(
        profile_fetcher=_fake_profiles(profiles),
        slot_requester=_async_noop,
        slot_releaser=_async_noop,
        cooldown_reporter=_async_noop,
        workflow_id="wf-minimax-regression",
        runtime_id="claude_code",
        run_launcher=_run_launcher,
    )

    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        executionProfileRef="claude_minimax",
        correlationId="corr-minimax",
        idempotencyKey="idem-minimax",
    )
    # This must NOT raise ValidationError (regression guard).
    handle = await adapter.start(request)
    assert handle.metadata["profile_id"] == "claude_minimax"

    profile_payload = captured_payload.get("profile") or {}
    env_overrides = profile_payload.get("envOverrides") or profile_payload.get(
        "env_overrides"
    ) or {}

    # Sensitive-keyed entries must NOT appear in env_overrides.
    assert "ANTHROPIC_AUTH_TOKEN" not in env_overrides, (
        "Sensitive runtime_env_override key must not be in env_overrides"
    )
    assert "MINIMAX_API_KEY" not in env_overrides, (
        "Raw API key must not appear in env_overrides"
    )
    assert "MANAGED_API_KEY_TARGET_ENV" not in env_overrides
    assert "MANAGED_API_KEY_REF" not in env_overrides
    # Non-sensitive runtime_env_overrides keys are passed through.
    assert env_overrides.get("ANTHROPIC_BASE_URL") == "https://api.minimax.io/anthropic"
    assert env_overrides.get("ANTHROPIC_MODEL") == "MiniMax-M2.7"
