"""Prove an ended Claude login on the exact host before cleanup removes it.

Reproduces mm:48057229-...-2026-11-04T09:49:01Z: the Claude Provider Profile
still read "Claude OAuth ready" while its stored login had ended (the refresh
token expired and Claude Code cleared both tokens), so every scheduled run
launched a host that registered ``needs-auth`` and failed at dispatch.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db import base
from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from api_service.services import provider_profile_service
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from moonmind.omnigent import oauth_login
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
    remediation_for,
)
from moonmind.omnigent.host_runtime import (
    GenericOmnigentHostRuntime,
    PreparedHostInputs,
)
from moonmind.omnigent.host_services import attestation
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

NOW_MS = int(time.time() * 1000)
DAY_MS = 86_400_000
ENDED_LOGIN = {
    "accessToken": "",
    "refreshToken": "",
    "expiresAt": 0,
    "refreshTokenExpiresAt": NOW_MS - 13 * DAY_MS,
    "scopes": ["user:inference", "user:sessions:claude_code"],
}
REFRESHABLE_LOGIN = {
    "accessToken": "private-token-access",
    "refreshToken": "private-token-refresh",
    "expiresAt": NOW_MS - DAY_MS,
    "refreshTokenExpiresAt": NOW_MS + DAY_MS,
}


def _write_login(home: Path, oauth: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": oauth}))


def _probe(home: Path) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", oauth_login.CLAUDE_LOGIN_SESSION_PROBE, str(home)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert "private-token" not in completed.stdout + completed.stderr
    return completed.stdout.strip()


@pytest.mark.parametrize(
    ("oauth", "expected"),
    [
        (ENDED_LOGIN, "ended"),
        ({"accessToken": "", "refreshToken": ""}, "ended"),
        (
            {
                "accessToken": "private-token-access",
                "refreshToken": "private-token-refresh",
                "expiresAt": NOW_MS - DAY_MS,
                "refreshTokenExpiresAt": NOW_MS - 1000,
            },
            "ended",
        ),
        (REFRESHABLE_LOGIN, "active"),
        (
            {
                "accessToken": "private-token-access",
                "refreshToken": "private-token-refresh",
                "expiresAt": NOW_MS - DAY_MS,
            },
            "active",
        ),
        (
            {
                "accessToken": "private-token-access",
                "refreshToken": "",
                "expiresAt": NOW_MS + DAY_MS,
            },
            "active",
        ),
        # Second-resolution timestamps are not mistaken for an ended login.
        (
            {
                "accessToken": "private-token-access",
                "refreshToken": "private-token-refresh",
                "expiresAt": NOW_MS // 1000 - 60,
                "refreshTokenExpiresAt": NOW_MS // 1000 + 86_400,
            },
            "active",
        ),
    ],
)
def test_probe_reports_only_whether_the_login_can_still_refresh(
    tmp_path: Path, oauth: dict, expected: str
) -> None:
    home = tmp_path / ".claude"
    _write_login(home, oauth)
    assert _probe(home) == expected


@pytest.mark.parametrize(
    "content", [None, "not json", json.dumps({"other": {}}), json.dumps([])]
)
def test_probe_keeps_unreadable_logins_unknown(tmp_path: Path, content) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    if content is not None:
        (home / ".credentials.json").write_text(content)
    assert _probe(home) == "unknown"


@pytest_asyncio.fixture
async def claude_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/profiles.db")
    async with engine.begin() as connection:
        await connection.run_sync(ManagedAgentProviderProfile.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "async_session_maker", factory)
    sync = AsyncMock()
    monkeypatch.setattr(provider_profile_service, "sync_provider_profile_manager", sync)
    async with factory() as session:
        profile = ManagedAgentProviderProfile(
            profile_id="claude_anthropic_oauth",
            capacity_scope_ref="provider-profile:claude_anthropic_oauth",
            credential_generation=2,
            runtime_id="claude_code",
            provider_id="anthropic",
            credential_source=ProviderCredentialSource.OAUTH_VOLUME,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
            volume_ref="claude_auth_volume",
            volume_mount_path="/home/app/.claude",
            enabled=True,
            auth_state=ProviderProfileAuthState.CONNECTED,
            max_parallel_runs=1,
            last_validated_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        provider_profile_service.apply_oauth_connected_state(
            profile,
            mapping=provider_profile_service.get_first_party_oauth_profile(
                "claude_code", "anthropic"
            ),
            validated_at=profile.last_validated_at,
        )
        session.add(profile)
        await session.commit()
        assert provider_profile_launch_ready(profile)
    try:
        yield factory, sync
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ended_claude_login_asks_for_reconnect_and_stops_routing(
    claude_profile,
) -> None:
    factory, sync = claude_profile

    await oauth_login.record_ended_oauth_login(
        "claude_anthropic_oauth",
        2,
        runtime_id="claude_code",
        reason="claude_login_session_ended",
    )

    async with factory() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "claude_anthropic_oauth"
        )
        assert profile.auth_state == ProviderProfileAuthState.VALIDATION_FAILED
        assert provider_profile_launch_ready(profile) is False
        behavior = profile.command_behavior
        assert behavior["auth_status_label"] == "Claude OAuth validation failed"
        assert (
            behavior["auth_readiness"]["failure_reason"] == "claude_login_session_ended"
        )
        assert "connect_oauth" in behavior["auth_actions"]
        assert profile.volume_ref == "claude_auth_volume"
        assert profile.credential_generation == 2
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["runtime_id"] == "claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("generation", "runtime_id"), [(1, "claude_code"), (2, "codex_cli")]
)
async def test_other_generation_or_runtime_leaves_the_claude_profile(
    claude_profile, generation: int, runtime_id: str
) -> None:
    factory, sync = claude_profile

    await oauth_login.record_ended_oauth_login(
        "claude_anthropic_oauth",
        generation,
        runtime_id=runtime_id,
        reason="claude_login_session_ended",
    )

    async with factory() as session:
        profile = await session.get(
            ManagedAgentProviderProfile, "claude_anthropic_oauth"
        )
        assert profile.auth_state == ProviderProfileAuthState.CONNECTED
        assert provider_profile_launch_ready(profile)
    sync.assert_not_awaited()


def _host_runtime(*, backend, events: list[str], needs_auth: HarnessPlatformError):
    class Launcher:
        server_url = "http://omnigent.invalid"

        async def launch(self, **kwargs):
            events.append("launch")
            return {
                "containerName": "claude-host",
                "stateVolumeRef": "state",
                "hostCleanupRef": "host-cleanup:claude-host",
                "stateCleanupRef": "state-cleanup:claude-host",
            }

    class Registration:
        async def wait_for_registration(self, **kwargs):
            events.append("registration")
            raise needs_auth

    class Cleanup:
        async def cleanup(self, **kwargs):
            events.append("cleanup")
            assert kwargs["container_name"] == "claude-host"
            return {"containerRemoved": True}

    unused = SimpleNamespace()
    return GenericOmnigentHostRuntime(
        launcher=Launcher(),
        workspace_service=unused,
        skill_service=unused,
        tool_service=unused,
        github_credential_service=unused,
        egress_service=unused,
        runtime_environment_service=SimpleNamespace(build=lambda **kwargs: {}),
        registration_waiter=Registration(),
        host_attestor=attestation.DockerOmnigentHostAttestor(
            backend=backend, client=SimpleNamespace(), artifacts=SimpleNamespace()
        ),
        cleanup_service=Cleanup(),
    )


def _realize_inputs(harness_id: str) -> dict:
    materializer = (
        "claude-oauth-home@1" if harness_id == "claude-native" else "codex-oauth-home@1"
    )
    target = (
        "/home/app/.claude" if harness_id == "claude-native" else "/home/app/.codex"
    )
    return {
        "request": AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="claude-run",
            idempotencyKey="claude-run",
        ),
        "plan": SimpleNamespace(
            planRef="plan:claude",
            payload=SimpleNamespace(
                hostClassRef="host@1",
                launchPolicyRef="policy@1",
                endpointRef="endpoint@1",
                harnessId=harness_id,
            ),
        ),
        "runtime_binding_id": "binding:claude",
        "host_lease_ref": "lease:claude",
        "host_lease_generation": 1,
        "host_class": SimpleNamespace(
            ref="host@1", imageRef="example/host@sha256:" + "1" * 64, runtime={}
        ),
        "launch_policy": SimpleNamespace(ref="policy@1", limits={}),
        "prepared": PreparedHostInputs(
            workspace_attachment={
                "sourceRef": "workspace",
                "targetPath": "/workspaces/run",
                "accessMode": "read-write",
            },
            skill_attachment={
                "sourceRef": "skills",
                "targetPath": "/skills",
                "accessMode": "read-only",
            },
            tool_attachments=(),
            egress_attestation={
                "networkRef": "egress",
                "profileRef": "egress@1",
                "profileDigest": "test",
                "appliedRuleDigest": "test",
            },
        ),
        "credential_handles": [
            {
                "credentialRuntimeRef": "credential-runtime:claude",
                "credentialGeneration": 2,
                "providerProfileRef": "claude_anthropic_oauth",
                "materializerRef": materializer,
                "attachments": [
                    {
                        "kind": "volume",
                        "sourceRef": "claude_auth_volume",
                        "targetPath": target,
                        "accessMode": "read-write",
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness_id", "login"),
    [
        ("claude-native", "ended"),
        ("claude-native", "refreshable"),
        ("claude-native", "probe_unavailable"),
        ("codex-native", "ended"),
    ],
)
async def test_needs_auth_host_names_an_ended_claude_login_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness_id: str, login: str
) -> None:
    home = tmp_path / "claude-home"
    _write_login(home, REFRESHABLE_LOGIN if login == "refreshable" else ENDED_LOGIN)
    record = AsyncMock()
    monkeypatch.setattr(attestation, "record_ended_oauth_login", record, raising=False)
    events: list[str] = []
    needs_auth = HarnessPlatformError(
        f"launched host is online but harness {harness_id!r} reports 'needs-auth'",
        code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NEEDS_AUTH,
    )

    class Backend(DockerCommandBackend):
        async def run(self, argv, **kwargs):
            assert argv[:5] == [
                "docker",
                "exec",
                "claude-host",
                "/opt/venv/bin/python",
                "-c",
            ]
            assert argv[-1] == "/home/app/.claude"
            events.append("login_session")
            if login == "probe_unavailable":
                raise HarnessPlatformError(
                    "docker exec failed",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            # Real probe and subprocess; only the Docker transport is replaced.
            return await super().run([sys.executable, *argv[4:-1], str(home)], **kwargs)

    runtime = _host_runtime(backend=Backend(), events=events, needs_auth=needs_auth)

    with pytest.raises(HarnessPlatformError) as raised:
        await runtime.realize(**_realize_inputs(harness_id))

    error = raised.value
    assert error.code == HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NEEDS_AUTH
    assert remediation_for(error.code) == "reauthenticate"
    assert error.host_cleanup_evidence == {"containerRemoved": True}
    assert "private-token" not in str(error)
    if harness_id == "codex-native":
        assert events == ["launch", "registration", "cleanup"]
    else:
        # The exact host is observed while it still runs.
        assert events == ["launch", "registration", "login_session", "cleanup"]
    if harness_id == "claude-native" and login == "ended":
        assert "claude_anthropic_oauth" in str(error)
        assert "has ended" in str(error)
        assert "Reconnect OAuth in Settings" in str(error)
        assert error.__cause__ is needs_auth
        record.assert_awaited_once_with(
            "claude_anthropic_oauth",
            2,
            runtime_id="claude_code",
            reason="claude_login_session_ended",
        )
    else:
        assert error is needs_auth
        record.assert_not_awaited()
