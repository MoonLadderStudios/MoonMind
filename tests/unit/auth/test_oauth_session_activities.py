"""Tests for OAuth session Temporal activities and catalog registration."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import exceptions

from api_service.db.models import (
    Base,
    ManagedAgentOAuthSession,
    ManagedAgentProviderProfile,
    OAuthSessionStatus,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)
from moonmind.workflows.temporal.activities import oauth_session_activities
from moonmind.workflows.temporal.activities.oauth_session_activities import (
    oauth_session_register_profile,
    oauth_session_start_auth_runner,
    oauth_session_stop_auth_runner,
    oauth_session_update_terminal_session,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_catalog import AGENT_RUNTIME_FLEET
from moonmind.workflows.temporal.activity_catalog import ARTIFACTS_FLEET
from moonmind.workflows.temporal.runtime.providers import registry as provider_registry
from moonmind.workflows.temporal.workers import REGISTERED_TEMPORAL_WORKFLOW_TYPES

@pytest_asyncio.fixture
async def _oauth_activity_session_factory(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/oauth-activities.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()

@asynccontextmanager
async def _session_context(session_factory):
    async with session_factory() as session:
        yield session

class TestOAuthSessionCatalogRegistration:
    """Verify OAuth session activities are registered in the catalog."""

    def test_ensure_volume_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.ensure_volume")
        assert route.activity_type == "oauth_session.ensure_volume"
        assert route.fleet == AGENT_RUNTIME_FLEET
        assert route.capability_class == "agent_runtime"

    def test_update_status_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.update_status")
        assert route.activity_type == "oauth_session.update_status"
        assert route.fleet == ARTIFACTS_FLEET

    def test_mark_failed_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.mark_failed")
        assert route.activity_type == "oauth_session.mark_failed"
        assert route.fleet == ARTIFACTS_FLEET

    def test_ensure_volume_timeouts(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.ensure_volume")
        assert route.timeouts.start_to_close_seconds == 60
        assert route.timeouts.schedule_to_close_seconds == 120

    def test_update_status_timeouts(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.update_status")
        assert route.timeouts.start_to_close_seconds == 15
        assert route.timeouts.schedule_to_close_seconds == 30

    def test_update_terminal_session_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.update_terminal_session")
        assert route.activity_type == "oauth_session.update_terminal_session"
        assert route.fleet == ARTIFACTS_FLEET
        assert route.timeouts.start_to_close_seconds == 15
        assert route.timeouts.schedule_to_close_seconds == 30

    def test_verify_volume_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.verify_volume")
        assert route.activity_type == "oauth_session.verify_volume"
        assert route.fleet == AGENT_RUNTIME_FLEET
        assert route.timeouts.start_to_close_seconds == 60
        assert route.timeouts.schedule_to_close_seconds == 120

    def test_register_profile_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.register_profile")
        assert route.activity_type == "oauth_session.register_profile"
        assert route.fleet == "artifacts"
        assert route.timeouts.start_to_close_seconds == 30
        assert route.timeouts.schedule_to_close_seconds == 60

class TestOAuthSessionWorkflowRegistration:
    """Verify the OAuth session workflow is registered."""

    def test_workflow_type_registered(self) -> None:
        assert "MoonMind.OAuthSession" in REGISTERED_TEMPORAL_WORKFLOW_TYPES

    def test_cleanup_stale_in_catalog(self) -> None:
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("oauth_session.cleanup_stale")
        assert route.activity_type == "oauth_session.cleanup_stale"
        assert route.fleet == "artifacts"
        assert route.timeouts.start_to_close_seconds == 60

@pytest.mark.asyncio
async def test_register_profile_activity_persists_oauth_home_codex_profile(
    _oauth_activity_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "oas_activityregister1"
    profile_id = "codex-activity-oauth"

    async with _oauth_activity_session_factory() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.REGISTERING_PROFILE,
                requested_by_user_id="not-a-uuid",
                account_label="codex account",
                metadata_json={
                    "provider_id": "openai",
                    "provider_label": "OpenAI",
                    "max_parallel_runs": 3,
                    "cooldown_after_429_seconds": 120,
                    "rate_limit_policy": "queue",
                },
            )
        )
        await session.commit()

    async def _noop_sync(**_kwargs):
        return None

    monkeypatch.setattr(
        oauth_session_activities,
        "get_async_session_context",
        lambda: _session_context(_oauth_activity_session_factory),
    )
    monkeypatch.setattr(
        "api_service.services.provider_profile_service.sync_provider_profile_manager",
        _noop_sync,
    )

    result = await oauth_session_register_profile({"session_id": session_id})
    assert result["status"] == "registered"

    async with _oauth_activity_session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, profile_id)
        assert profile is not None
        assert profile.runtime_id == "codex_cli"
        assert profile.provider_id == "openai"
        assert profile.provider_label == "OpenAI"
        assert profile.credential_source == ProviderCredentialSource.OAUTH_VOLUME
        assert (
            profile.runtime_materialization_mode
            == RuntimeMaterializationMode.OAUTH_HOME
        )
        assert profile.volume_ref == "codex_auth_volume"
        assert profile.volume_mount_path == "/home/app/.codex"
        assert profile.max_parallel_runs == 3

@pytest.mark.asyncio
async def test_register_profile_activity_rejects_codex_oauth_profile_without_refs(
    _oauth_activity_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "oas_activitymissingrefs"
    profile_id = "codex-activity-missing-refs"

    async with _oauth_activity_session_factory() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="   ",
                volume_mount_path=None,
                status=OAuthSessionStatus.REGISTERING_PROFILE,
                requested_by_user_id="not-a-uuid",
                account_label="codex account",
                metadata_json={
                    "provider_id": "openai",
                    "provider_label": "OpenAI",
                },
            )
        )
        await session.commit()

    monkeypatch.setattr(
        oauth_session_activities,
        "get_async_session_context",
        lambda: _session_context(_oauth_activity_session_factory),
    )

    with pytest.raises(ValueError, match="volume_ref is required"):
        await oauth_session_register_profile({"session_id": session_id})

    async with _oauth_activity_session_factory() as session:
        assert await session.get(ManagedAgentProviderProfile, profile_id) is None

@pytest.mark.asyncio
async def test_register_profile_activity_rejects_failed_verification_metadata(
    _oauth_activity_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "oas_activityfailedverify"
    profile_id = "codex-activity-failed-verify"

    async with _oauth_activity_session_factory() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id=profile_id,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.REGISTERING_PROFILE,
                requested_by_user_id="not-a-uuid",
                account_label="codex account",
                metadata_json={
                    "provider_id": "openai",
                    "provider_label": "OpenAI",
                },
            )
        )
        await session.commit()

    monkeypatch.setattr(
        oauth_session_activities,
        "get_async_session_context",
        lambda: _session_context(_oauth_activity_session_factory),
    )

    with pytest.raises(
        exceptions.ApplicationError,
        match="Volume verification failed: no_credentials_found",
    ) as exc_info:
        await oauth_session_register_profile(
            {
                "session_id": session_id,
                "verification": {
                    "verified": False,
                    "reason": "no_credentials_found",
                    "status": "failed",
                },
            }
        )

    assert exc_info.value.non_retryable is True
    async with _oauth_activity_session_factory() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        assert row.status == OAuthSessionStatus.REGISTERING_PROFILE
        assert row.failure_reason is None
        assert await session.get(ManagedAgentProviderProfile, profile_id) is None

@pytest.mark.asyncio
async def test_update_terminal_session_persists_runner_metadata(
    _oauth_activity_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "oas_activityterminal1"

    async with _oauth_activity_session_factory() as session:
        session.add(
            ManagedAgentOAuthSession(
                session_id=session_id,
                runtime_id="codex_cli",
                profile_id="codex-terminal-activity",
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                status=OAuthSessionStatus.STARTING,
                requested_by_user_id="not-a-uuid",
                account_label="codex account",
            )
        )
        await session.commit()

    monkeypatch.setattr(
        oauth_session_activities,
        "get_async_session_context",
        lambda: _session_context(_oauth_activity_session_factory),
    )

    result = await oauth_session_update_terminal_session(
        {
            "session_id": session_id,
            "terminal_session_id": "term_oas_activityterminal1",
            "terminal_bridge_id": "br_oas_activityterminal1",
            "container_name": "moonmind_auth_oas_activityterminal1",
            "session_transport": "moonmind_pty_ws",
            "expires_at": "2026-04-15T12:30:00+00:00",
        }
    )

    assert result["session_transport"] == "moonmind_pty_ws"
    async with _oauth_activity_session_factory() as session:
        row = await session.get(ManagedAgentOAuthSession, session_id)
        assert row is not None
        assert row.terminal_session_id == "term_oas_activityterminal1"
        assert row.terminal_bridge_id == "br_oas_activityterminal1"
        assert row.container_name == "moonmind_auth_oas_activityterminal1"
        assert row.session_transport == "moonmind_pty_ws"
        assert row.expires_at is not None

@pytest.mark.asyncio
async def test_start_auth_runner_resolves_provider_bootstrap_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_start_terminal_bridge_container(**kwargs):
        observed.update(kwargs)
        return {
            "container_name": "moonmind_auth_oas_activityrunner1",
            "terminal_session_id": "term_oas_activityrunner1",
            "terminal_bridge_id": "br_oas_activityrunner1",
            "session_transport": "moonmind_pty_ws",
        }

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.terminal_bridge.start_terminal_bridge_container",
        fake_start_terminal_bridge_container,
    )

    result = await oauth_session_start_auth_runner(
        {
            "session_id": "oas_activityrunner1",
            "runtime_id": "codex_cli",
            "volume_ref": "codex_auth_volume",
            "volume_mount_path": "/home/app/.codex",
            "session_ttl": 120,
        }
    )

    assert result["container_name"] == "moonmind_auth_oas_activityrunner1"
    assert observed["bootstrap_command"] == ("codex", "login", "--device-auth")
    assert observed["volume_ref"] == "codex_auth_volume"
    assert observed["volume_mount_path"] == "/home/app/.codex"

@pytest.mark.asyncio
async def test_start_auth_runner_resolves_claude_bootstrap_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_start_terminal_bridge_container(**kwargs):
        observed.update(kwargs)
        return {
            "container_name": "moonmind_auth_oas_claude_activityrunner",
            "terminal_session_id": "term_oas_claude_activityrunner",
            "terminal_bridge_id": "br_oas_claude_activityrunner",
            "session_transport": "moonmind_pty_ws",
        }

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.terminal_bridge.start_terminal_bridge_container",
        fake_start_terminal_bridge_container,
    )

    result = await oauth_session_start_auth_runner(
        {
            "session_id": "oas_claude_activityrunner",
            "runtime_id": "claude_code",
            "volume_ref": "claude_auth_volume",
            "volume_mount_path": "/home/app/.claude",
            "session_ttl": 120,
        }
    )

    assert result["container_name"] == "moonmind_auth_oas_claude_activityrunner"
    assert observed["bootstrap_command"] == ("claude", "auth", "login")
    assert observed["volume_ref"] == "claude_auth_volume"
    assert observed["volume_mount_path"] == "/home/app/.claude"

@pytest.mark.asyncio
async def test_start_auth_runner_rejects_missing_provider_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers = dict(provider_registry.OAUTH_PROVIDERS)
    providers["codex_cli"] = dict(providers["codex_cli"], bootstrap_command=[])
    monkeypatch.setattr(provider_registry, "OAUTH_PROVIDERS", providers)

    with pytest.raises(ValueError, match="bootstrap command is not configured"):
        await oauth_session_start_auth_runner(
            {
                "session_id": "oas_activityrunner_missing_command",
                "runtime_id": "codex_cli",
                "volume_ref": "codex_auth_volume",
                "volume_mount_path": "/home/app/.codex",
            }
        )

@pytest.mark.asyncio
async def test_stop_auth_runner_without_container_is_idempotent(
    _oauth_activity_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        oauth_session_activities,
        "get_async_session_context",
        lambda: _session_context(_oauth_activity_session_factory),
    )

    result = await oauth_session_stop_auth_runner(
        {"session_id": "oas_activityrunner_no_container"}
    )

    assert result == {
        "session_id": "oas_activityrunner_no_container",
        "stopped": False,
        "reason": "no_container",
    }
