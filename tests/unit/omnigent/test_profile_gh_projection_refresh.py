"""A profile-bound host's gh login outlives one App issuance (#4011).

The coordinator re-issues the host's attempt-owned ``hosts.yml`` through the
same default-connection selection before an expiring GitHub App token lapses,
under a newer durable reservation, and refuses a selection that changed.
Only Docker and the GitHub App provider are replaced: the bridge store fence,
connection selection, bound acquirer and projection protocol are production.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.omnigent import profile_bound_execution
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_services.github_credentials import (
    github_projection_script,
)
from moonmind.omnigent.oauth_host_runtime import (
    _GITHUB_CONFIG_HOME,
    OmnigentOAuthHostRuntime,
)
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.executions.repository_contract import (
    DEFAULT_GIT_CONNECTION_REF,
)
from tests.helpers.repository_connections import (
    github_app_connection,
    github_app_provider_edge,
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)

_REPO = "MoonLadderStudios/MoonMind"
_ACTIONS = ("reserve", "publish", "inspect", "retire", "retire_legacy")
_LAUNCH = {
    "hostImageRef": "example.invalid/omnigent-host@sha256:" + "a" * 64,
    "runtimeUid": os.getuid(),
    "runtimeGid": os.getgid(),
}


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="gh-refresh",
        idempotencyKey="gh-refresh",
        workspaceSpec={"repository": _REPO},
        parameters={"publishMode": "none", "requiredCapabilities": ["git", "gh"]},
    )


class _LocalProjection:
    """Run the host's projection protocol against a local cache directory."""

    def __init__(self, cache: Path) -> None:
        self.config_dir = cache / "gh"
        self.published: list[bytes] = []

    async def run(self, *argv, check=True, input_bytes=None, env=None):
        if "-ceu" not in argv:
            return 0, "", ""
        script = argv[argv.index("-ceu") + 1]
        action = next(
            (
                name
                for name in _ACTIONS
                if script
                == github_projection_script(f"{_GITHUB_CONFIG_HOME}/gh", action=name)
            ),
            None,
        )
        if action is None:
            return 0, "", ""
        if input_bytes:
            self.published.append(input_bytes)
        completed = subprocess.run(
            [
                "sh",
                "-ceu",
                github_projection_script(str(self.config_dir), action=action),
                "--",
                str(os.getuid()),
                str(os.getgid()),
                *argv[-2:],
            ],
            input=input_bytes or b"",
            capture_output=True,
        )
        if completed.returncode and check:
            raise RuntimeError("projection writer failed")
        return completed.returncode, completed.stdout.decode().strip(), ""

    def projected(self) -> tuple[dict, str]:
        stamp_line, *rest = (self.config_dir / "hosts.yml").read_text().splitlines()
        token = next(line.split(": ", 1)[1] for line in rest if "oauth_token" in line)
        return json.loads(stamp_line.split(": ", 1)[1]), token


async def _host_owner(tmp_path, request):
    """Persist the bridge row and host lease that own gh publication."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = OmnigentBridgeSessionStore(factory)
    await store.get_or_create(
        request=request,
        endpoint_ref="endpoint",
        agent_id=None,
        agent_name=None,
        target_metadata={},
    )
    async with factory() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="profile",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                volume_ref="codex_auth_volume",
                volume_mount_path="/home/app/.codex",
                max_parallel_runs=1,
                credential_generation=1,
                enabled=True,
                auth_state=ProviderProfileAuthState.CONNECTED,
                last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
            )
        )
        await session.commit()
    hosts = OmnigentOAuthHostRepository(factory)
    binding = await hosts.create_or_update_static_binding(
        profile_id="profile", endpoint_ref="endpoint", static_host_id="host"
    )
    lease = await hosts.create_or_get_host_lease(
        binding=binding,
        provider_lease_id="provider",
        holder_workflow_id="corr-1",
        agent_run_id="step",
        idempotency_key=request.idempotency_key,
    )
    async with factory() as session:
        row = await store.get_existing(request.idempotency_key)
        row.host_lease_ref = lease.lease_id
        row.provider_profile_id = "profile"
        row.provider_lease_id = "provider"
        row.host_binding_ref = binding.binding_ref
        row.credential_generation = 1
        await session.merge(row)
        await session.commit()
    return engine, store, lease


async def _projected_host(monkeypatch, tmp_path, connection, provider_tokens):
    """Prepare a host whose gh projection holds the first issuance."""

    for name in ("GITHUB_TOKEN", "GH_TOKEN", "WORKFLOW_GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    issued = github_app_provider_edge(
        monkeypatch, provider_tokens, repositories=(_REPO,), lifetime_seconds=120
    )
    connections = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[
            github_repository_assignment(
                connection.id, _REPO, operations=connection.allowed_operations
            )
        ],
    )
    request = _request()
    bridge_engine, store, lease = await _host_owner(tmp_path, request)
    local = _LocalProjection(tmp_path / "cache")
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime._run = local.run

    async def no_container(_name):
        return False

    runtime.container_exists = no_container
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._run_store = store
    coordinator._host_preparation = runtime
    # Host preparation: reserve, acquire through the selection, publish.
    issuance: dict = {}
    reservation = await store.reserve_github_projection(
        request=request, host_lease_ref=lease.lease_id
    )
    await runtime.reserve_github_projection(
        host_lease=lease, effective_launch=_LAUNCH, reservation=reservation
    )
    token = await coordinator._github_token(request, issuance=issuance)
    await runtime._project_github_credential(
        token,
        github_projection_reservation=reservation,
        cache_volume="cache",
        host_image_ref=_LAUNCH["hostImageRef"],
        runtime_uid=_LAUNCH["runtimeUid"],
        runtime_gid=_LAUNCH["runtimeGid"],
    )
    preflight = {"githubProjectionReservation": dict(reservation)}

    async def close():
        await bridge_engine.dispose()
        await connections.dispose()

    return SimpleNamespace(
        coordinator=coordinator,
        request=request,
        lease=lease,
        store=store,
        local=local,
        issued=issued,
        issuance=issuance,
        preflight=preflight,
        close=close,
    )


def _app(connection_id=DEFAULT_GIT_CONNECTION_REF):
    app = github_app_connection(connection_id, _REPO)
    return app.model_copy(
        update={
            "allowed_operations": ("read", "write", "branch_write", "review_request"),
            "credential": app.credential.model_copy(
                update={"permitted_repositories": (_REPO,)}
            ),
        }
    )


@pytest.mark.asyncio
async def test_live_profile_projection_is_reissued_for_the_same_selection(
    monkeypatch, tmp_path
):
    host = await _projected_host(
        monkeypatch, tmp_path, _app(), ["ghs_first_issuance", "ghs_second_issuance"]
    )
    try:
        first_stamp, first_token = host.local.projected()
        assert first_token == "ghs_first_issuance"
        first_expiry = host.issuance["expiresAt"]
        assert first_expiry is not None
        assert first_expiry - datetime.now(UTC) <= timedelta(seconds=120)

        expires_at = await host.coordinator._refresh_github_projection(
            request=host.request,
            host_lease=host.lease,
            effective_launch=_LAUNCH,
            preflight=host.preflight,
            issuance=host.issuance,
        )

        second_stamp, second_token = host.local.projected()
        assert second_token == "ghs_second_issuance"
        assert second_stamp["ownerRef"] == first_stamp["ownerRef"]
        assert second_stamp["revision"] > first_stamp["revision"]
        # Later recovery is fenced by the reservation the projection holds.
        assert host.preflight["githubProjectionReservation"] == second_stamp
        await host.store.validate_github_projection(
            request=host.request,
            host_lease_ref=host.lease.lease_id,
            reservation=second_stamp,
        )
        assert expires_at is not None and host.issuance["expiresAt"] == expires_at
        assert host.issued[0] == host.issued[1]
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_switched_default_selection_is_refused_without_publishing(
    monkeypatch, tmp_path
):
    host = await _projected_host(monkeypatch, tmp_path, _app(), ["ghs_first_issuance"])
    try:
        live = host.local.projected()
        # The default now derives from another source than the projected one.
        host.issuance["sourceName"] = "repository-connection:another-account"

        with pytest.raises(OmnigentOAuthHostError, match="switch accounts"):
            await host.coordinator._refresh_github_projection(
                request=host.request,
                host_lease=host.lease,
                effective_launch=_LAUNCH,
                preflight=host.preflight,
                issuance=host.issuance,
            )

        assert host.local.projected() == live
        assert len(host.local.published) == 1
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_non_expiring_pat_issuance_has_no_refresh_deadline(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SELECTED_PAT", "ghp_selectedPat")
    host = await _projected_host(
        monkeypatch,
        tmp_path,
        github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "SELECTED_PAT"),
        ["unused"],
    )
    try:
        assert host.local.projected()[1] == "ghp_selectedPat"
        assert host.issuance == {
            "sourceName": DEFAULT_GIT_CONNECTION_REF,
            "expiresAt": None,
        }
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_turn_heartbeat_refreshes_before_expiry_and_survives_failures(
    monkeypatch,
):
    monkeypatch.setattr(
        profile_bound_execution, "HOST_LEASE_HEARTBEAT_INTERVAL_SECONDS", 0.01
    )
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    heartbeats = []

    async def heartbeat_host_lease(lease_ref, **_kwargs):
        heartbeats.append(lease_ref)

    coordinator._hosts = SimpleNamespace(heartbeat_host_lease=heartbeat_host_lease)
    refreshes = []

    async def refresh():
        refreshes.append(datetime.now(UTC))
        if len(refreshes) == 1:
            raise RuntimeError("provider unavailable")
        return datetime.now(UTC) + timedelta(hours=1)

    async def turn():
        await asyncio.sleep(0.2)
        return "turn-complete"

    result = await coordinator._execute_with_host_lease_heartbeat(
        turn(),
        host_lease_ref="lease",
        ttl_seconds=60,
        github_refresh=refresh,
        # Within the refresh lead: due at once, before this issuance lapses.
        github_expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )

    assert result == "turn-complete"
    # The failed refresh retried at the next interval; the hour-long
    # replacement is not refreshed again during this turn.
    assert len(refreshes) == 2
    assert heartbeats
