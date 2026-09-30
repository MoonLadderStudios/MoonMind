"""Managed launcher uses the admitted connection, not ambient GitHub discovery.

MoonLadderStudios/MoonMind#4023: ``repository-connection:git-default`` is the
proven legacy identity recorded by the one migration, bound to this worker's
own Git client policy. Readiness and the clone credential acquire only the
selected connection's SecretRef; a failed selected source never falls back to
ambient ``GITHUB_TOKEN``, and scratch work resolves no GitHub credential.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.executions.repository_contract import (
    DEFAULT_GIT_CONNECTION_REF,
    RepositoryClientEvidence,
    RepositoryClientPolicy,
    RepositoryConnection,
    RepositoryContractError,
)
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

_AMBIENT_A = "ambient-token-a"
_SELECTED_B = "selected-token-b"
_OTHER_C = "explicit-token-c"

_EVIDENCE = RepositoryClientEvidence(
    toolBundleRef="repository-client:git-system",
    clientVersion="2.46.0",
    executableSha256="sha256:worker-git",
)
_WORKER_POLICY = RepositoryClientPolicy(
    pinnedVersion=_EVIDENCE.client_version,
    toolBundleRef=_EVIDENCE.tool_bundle_ref,
    executableSha256=_EVIDENCE.executable_sha256,
)


def _connection(
    connection_id: str = DEFAULT_GIT_CONNECTION_REF,
    *,
    secret_key: str = "SELECTED_GITHUB_PAT",
    lifecycle: str = "active",
    client_policy: RepositoryClientPolicy | None = None,
) -> RepositoryConnection:
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": "GitHub",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read", "write", "branch_write", "review_request"],
            # The recorded policy came from another host: a different Git SHA
            # is not an incompatible worker for the deployment default.
            "clientPolicy": (
                client_policy
                or RepositoryClientPolicy(
                    pinnedVersion="2.39.5",
                    toolBundleRef="repository-client:git-system",
                    executableSha256="sha256:migration-host-git",
                )
            ).model_dump(by_alias=True),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "env", "key": secret_key},
            },
            "lifecycle": lifecycle,
            "ownership": {"ownerRef": "owner:operator", "scopeType": "system"},
            "hostingService": "github",
        }
    )


def _request(
    *, connection_ref: str | None = DEFAULT_GIT_CONNECTION_REF, publish_mode="none"
) -> AgentExecutionRequest:
    workspace_spec: dict = {}
    if connection_ref is not None:
        workspace_spec = {
            "repository": "MoonLadderStudios/MoonMind",
            "repositoryTarget": {
                "provider": "git",
                "connectionRef": connection_ref,
                "repository": {"name": "MoonLadderStudios/MoonMind"},
                "branch": {"name": "main"},
            },
        }
    return AgentExecutionRequest(
        agent_kind="managed",
        agent_id="agent-1",
        execution_profile_ref="default-managed",
        correlation_id="corr-4023",
        idempotency_key="run-4023",
        workspace_spec=workspace_spec,
        parameters={"publishMode": publish_mode},
        skill={"requiredCapabilities": ["gh"]},
    )


def _launcher(tmp_path, loader) -> ManagedRuntimeLauncher:
    launcher = ManagedRuntimeLauncher(
        ManagedRunStore(tmp_path / "managed_runs"),
        repository_client_policy=_WORKER_POLICY,
        default_git_connection_loader=loader,
    )
    launcher._observe_git_client = AsyncMock(return_value=_EVIDENCE)
    launcher._observe_git_remote_tip = AsyncMock(return_value="abcdef0123456789")
    return launcher


@pytest.fixture(autouse=True)
def _ambient_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", _AMBIENT_A)
    monkeypatch.setenv("SELECTED_GITHUB_PAT", _SELECTED_B)
    monkeypatch.delenv("MISSING_GITHUB_PAT", raising=False)


@pytest.fixture
def ambient_resolver():
    resolver = AsyncMock(side_effect=AssertionError("ambient discovery is retired"))
    with patch("moonmind.auth.github_credentials.resolve_github_credential", resolver):
        yield resolver


@pytest.mark.asyncio
async def test_migrated_default_b_wins_over_ambient_a(tmp_path, ambient_resolver):
    launcher = _launcher(tmp_path, AsyncMock(return_value=_connection()))

    resolved = await launcher._ensure_repository_ready_for_launch(_request(), None)
    token = await launcher._selected_repository_github_token(_request())

    assert resolved is not None
    assert resolved.connection_ref == DEFAULT_GIT_CONNECTION_REF
    assert resolved.client_evidence == _EVIDENCE
    assert token == _SELECTED_B
    ambient_resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_source_failure_never_falls_back_to_ambient(
    tmp_path, ambient_resolver
):
    launcher = _launcher(
        tmp_path,
        AsyncMock(return_value=_connection(secret_key="MISSING_GITHUB_PAT")),
    )

    with pytest.raises(RepositoryContractError, match="REPOSITORY_CREDENTIAL_UNAVAILABLE"):
        await launcher._ensure_repository_ready_for_launch(_request(), None)
    with pytest.raises(RepositoryContractError, match="REPOSITORY_CREDENTIAL_UNAVAILABLE"):
        await launcher._selected_repository_github_token(_request())
    ambient_resolver.assert_not_awaited()
    launcher._observe_git_remote_tip.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_connections_is_actionable_without_ambient_lookup(
    tmp_path, ambient_resolver
):
    launcher = _launcher(tmp_path, AsyncMock(return_value=None))

    with pytest.raises(RepositoryContractError) as unavailable:
        await launcher._selected_repository_github_token(_request())

    assert unavailable.value.code == "REPOSITORY_CONNECTION_UNAVAILABLE"
    assert "GITHUB_TOKEN" in str(unavailable.value)
    ambient_resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_default_is_not_reacquired(tmp_path, ambient_resolver):
    launcher = _launcher(
        tmp_path, AsyncMock(return_value=_connection(lifecycle="disabled"))
    )

    with pytest.raises(RepositoryContractError, match="REPOSITORY_CONNECTION_UNAVAILABLE"):
        await launcher._ensure_repository_ready_for_launch(_request(), None)
    ambient_resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_connection_among_several_uses_only_its_own_secret(
    tmp_path, monkeypatch, ambient_resolver
):
    monkeypatch.setenv("EXPLICIT_GITHUB_PAT", _OTHER_C)
    registry = tmp_path / "repository_connections"
    registry.mkdir()
    explicit = _connection(
        "repository-connection:explicit",
        secret_key="EXPLICIT_GITHUB_PAT",
        client_policy=_WORKER_POLICY,
    )
    (registry / "explicit.json").write_text(
        json.dumps(explicit.model_dump(by_alias=True, mode="json")), encoding="utf-8"
    )
    monkeypatch.setenv("MOONMIND_REPOSITORY_CONNECTIONS_DIR", str(registry))
    default_loader = AsyncMock(return_value=_connection())
    launcher = _launcher(tmp_path, default_loader)
    request = _request(connection_ref="repository-connection:explicit")

    resolved = await launcher._ensure_repository_ready_for_launch(request, None)
    token = await launcher._selected_repository_github_token(request)

    assert resolved is not None
    assert resolved.connection_ref == "repository-connection:explicit"
    assert token == _OTHER_C
    default_loader.assert_not_awaited()
    ambient_resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_scratch_work_resolves_no_github_credential(tmp_path, ambient_resolver):
    loader = AsyncMock(return_value=_connection())
    launcher = _launcher(tmp_path, loader)
    launch_resolver = AsyncMock(side_effect=AssertionError("no GitHub for scratch"))

    with patch(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        launch_resolver,
    ):
        token = await launcher._launch_clone_github_token(
            _request(connection_ref=None), None
        )

    assert token is None
    loader.assert_not_awaited()
    launch_resolver.assert_not_awaited()
    ambient_resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_target_clone_token_comes_from_selected_connection(
    tmp_path, ambient_resolver
):
    launcher = _launcher(tmp_path, AsyncMock(return_value=_connection()))
    launch_resolver = AsyncMock(return_value=_AMBIENT_A)

    with patch(
        "moonmind.workflows.temporal.runtime.launcher.resolve_github_token_for_launch",
        launch_resolver,
    ):
        token = await launcher._launch_clone_github_token(_request(), None)

    assert token == _SELECTED_B
    launch_resolver.assert_not_awaited()
    ambient_resolver.assert_not_awaited()
