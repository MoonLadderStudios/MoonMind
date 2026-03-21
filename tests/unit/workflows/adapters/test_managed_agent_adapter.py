"""Unit tests for ManagedAgentAdapter and auth_profile_list activity.

Tests cover:
- Profile resolution (auto, by ID, missing)
- Environment shaping (OAuth clears sensitive vars, API-key sets ref)
- Slot request / release signalling
- 429 cooldown reporting
- auth_profile_list activity method (happy path + empty DB)

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
    ManagedAgentAuthMode,
    ManagedAgentAuthProfile,
    ManagedAgentRateLimitPolicy,
)
from moonmind.workflows.adapters.managed_agent_adapter import (
    ManagedAgentAdapter,
    ProfileResolutionError,
    _OAUTH_CLEARED_VARS,
    _shape_environment_for_api_key,
    _shape_environment_for_oauth,
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


def test_shape_environment_for_oauth_clears_sensitive_vars():
    base = {
        "HOME": "/home/user",
        "GOOGLE_API_KEY": "secret-key",
        "GEMINI_API_KEY": "gemini-key",
        "OPENAI_API_KEY": "openai-key",
        "PATH": "/usr/bin",
    }
    shaped = _shape_environment_for_oauth(base, volume_mount_path="/mnt/auth")
    # Sensitive keys must be absent.
    for key in _OAUTH_CLEARED_VARS:
        assert key not in shaped, f"Expected {key} to be cleared"
    assert shaped["HOME"] == "/home/user"
    assert shaped["MANAGED_AUTH_VOLUME_PATH"] == "/mnt/auth"


def test_shape_environment_for_oauth_without_mount_path():
    base = {"HOME": "/home/user", "GEMINI_API_KEY": "secret"}
    shaped = _shape_environment_for_oauth(base, volume_mount_path=None)
    assert "MANAGED_AUTH_VOLUME_PATH" not in shaped
    assert "GEMINI_API_KEY" not in shaped


def test_shape_environment_for_oauth_preserves_github_cli_tokens():
    base = {
        "HOME": "/home/user",
        "GH_TOKEN": "ghp-token",
        "GITHUB_TOKEN": "github-token",
        "OPENAI_API_KEY": "secret",
    }
    shaped = _shape_environment_for_oauth(base, volume_mount_path=None)
    assert shaped["GH_TOKEN"] == "ghp-token"
    assert shaped["GITHUB_TOKEN"] == "github-token"
    assert "OPENAI_API_KEY" not in shaped


def test_shape_environment_for_api_key_sets_ref():
    base = {"HOME": "/home/user"}
    shaped = _shape_environment_for_api_key(
        base, api_key_ref="secrets/my-api-key", account_label="ci-bot"
    )
    assert shaped["MANAGED_API_KEY_REF"] == "secrets/my-api-key"
    assert shaped["MANAGED_ACCOUNT_LABEL"] == "ci-bot"
    assert shaped["HOME"] == "/home/user"


def test_shape_environment_for_api_key_without_ref():
    base = {"HOME": "/home/user"}
    shaped = _shape_environment_for_api_key(base, api_key_ref=None, account_label=None)
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
    assert ("slot_request", "wf-123", "gemini_cli") in calls


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


async def test_start_preserves_github_tokens_in_launch_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = [
        {
            "profile_id": "oauth-prof",
            "auth_mode": "oauth",
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
    assert env_overrides["GH_TOKEN"] == "ghp-direct"
    assert env_overrides["GITHUB_TOKEN"] == "ghp-legacy"
    assert "OPENAI_API_KEY" not in env_overrides


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
    with pytest.raises(ProfileResolutionError, match="No enabled auth profiles"):
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
# auth_profile_list activity tests (integration against in-memory SQLite DB)
# ---------------------------------------------------------------------------


async def test_auth_profile_list_returns_enabled_profiles(tmp_path: Path):
    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            session.add(
                ManagedAgentAuthProfile(
                    profile_id="gprofile-1",
                    runtime_id="gemini_cli",
                    auth_mode=ManagedAgentAuthMode.OAUTH,
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
                ManagedAgentAuthProfile(
                    profile_id="gprofile-disabled",
                    runtime_id="gemini_cli",
                    auth_mode=ManagedAgentAuthMode.API_KEY,
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

            result = await activities.auth_profile_list(runtime_id="gemini_cli")

        assert "profiles" in result
        profiles = result["profiles"]
        assert len(profiles) == 1
        assert profiles[0]["profile_id"] == "gprofile-1"
        assert profiles[0]["auth_mode"] == "oauth"
        assert profiles[0]["enabled"] is True
        assert profiles[0]["max_parallel_runs"] == 2


async def test_auth_profile_list_returns_empty_for_unknown_runtime(tmp_path: Path):
    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)
            result = await activities.auth_profile_list(runtime_id="nonexistent_runtime")

        assert result == {"profiles": []}


async def test_auth_profile_list_filters_by_runtime_id(tmp_path: Path):
    async with _in_memory_db(tmp_path) as session_factory:
        async with session_factory() as session:
            for runtime, pid in [("gemini_cli", "g1"), ("claude_code", "c1")]:
                session.add(
                    ManagedAgentAuthProfile(
                        profile_id=pid,
                        runtime_id=runtime,
                        auth_mode=ManagedAgentAuthMode.API_KEY,
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
            result = await activities.auth_profile_list(runtime_id="claude_code")

        profiles = result["profiles"]
        assert len(profiles) == 1
        assert profiles[0]["profile_id"] == "c1"


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
