"""The split-session publication boundary carries the admitted attribution."""

from contextlib import asynccontextmanager
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities import (
    omnigent_session_activities as activities,
)


@pytest.mark.asyncio
async def test_split_session_publication_keeps_admitted_identity(monkeypatch):
    identity = {"name": "Admitted operator", "email": "admitted@example.invalid"}
    from tests.helpers.publication_identity import recorded_publication_identity

    recorded_publication_identity(monkeypatch, identity)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow",
        idempotencyKey="step",
        workspaceSpec={
            "repository": "example/repository",
            "startingBranch": "main",
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": hashlib.sha256(b"workflow:step").hexdigest()[:24],
                "relativePath": "repo",
            },
        },
        parameters={
            "repository": "example/repository",
            "publishMode": "pr",
            "publicationGitIdentityAdmission": {
                "schemaVersion": "v1",
                "identity": identity,
            },
        },
    )
    session = SimpleNamespace(
        terminal_state="succeeded",
        terminal_evidence_ref=None,
        moonmind_workflow_id="workflow",
        step_execution_id="step",
        metadata={"harvestedEvidenceRef": "artifact://harvest"},
    )
    repos = SimpleNamespace(
        sessions=SimpleNamespace(get=AsyncMock(return_value=session))
    )

    class Store:
        def __init__(self, *_args):
            pass

        @asynccontextmanager
        async def transaction(self):
            yield repos

    class PublicationReached(Exception):
        pass

    observed = {}

    async def publish(**kwargs):
        observed.update(kwargs)
        # Stop at the actual port boundary before terminal/artifact effects;
        # real publisher recovery is exercised by workspace-publication tests.
        raise PublicationReached()

    http = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        "moonmind.omnigent.control_plane.OmnigentControlPlaneStore", Store
    )
    monkeypatch.setattr(
        activities, "_claim_command", AsyncMock(return_value=(None, True))
    )
    monkeypatch.setattr(
        activities, "_load_intent_request", AsyncMock(return_value=request)
    )
    monkeypatch.setattr(
        activities, "_omnigent_client_context", AsyncMock(return_value=(http, object()))
    )
    monkeypatch.setattr(
        "moonmind.omnigent.profile_bound_execution.OmnigentProfileBoundExecutionCoordinator._github_token",
        AsyncMock(return_value="fixture-credential"),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.oauth_host_runtime.OmnigentOAuthHostRuntime",
        lambda **_kwargs: SimpleNamespace(publish_workspace=publish),
    )
    with pytest.raises(PublicationReached):
        await activities.omnigent_publish_workspace_activity(
            {
                "sessionId": "session",
                "compiledExecutionIntentRef": "artifact://intent",
                "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
                "expectedRevision": 1,
                "fencingGeneration": 1,
            }
        )
    assert observed.get("publication_git_identity") == identity
    http.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_split_session_preparation_keeps_recorded_identity(monkeypatch):
    from tests.helpers.publication_identity import recorded_publication_identity

    identity = {"name": "Admitted", "email": "admitted@example.invalid"}
    recorded_publication_identity(monkeypatch, identity)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow",
        idempotencyKey="step",
        workspaceSpec={
            "repository": "example/repository",
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": hashlib.sha256(b"workflow:step").hexdigest()[:24],
                "relativePath": "repo",
            },
        },
        parameters={"repository": "example/repository"},
    )
    session = SimpleNamespace(
        provider_profile_id="profile",
        host_lease_generation=1,
        metadata={
            "providerLeaseRef": "provider-lease",
            "providerRuntimeId": "codex_cli",
        },
        moonmind_agent_run_id="agent-run",
        moonmind_workflow_id="workflow",
        step_execution_id="step",
    )

    @asynccontextmanager
    async def database():
        yield object()

    class Store:
        def __init__(self, *_args):
            pass

        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(
                sessions=SimpleNamespace(get=AsyncMock(return_value=session))
            )

    launch = {
        "executionProfileRef": "codex-test",
        "snapshotRef": "launch",
        "hostMode": "static",
    }
    binding = SimpleNamespace(
        execution_profile_ref="codex-test",
        launch_policy_ref="launch@1",
        effective_launch_snapshot=launch,
        endpoint_ref="endpoint",
        binding_ref="binding",
    )
    lease = SimpleNamespace(
        status="ready",
        credential_generation=1,
        lease_id="lease",
        omnigent_host_id="host",
    )

    class Hosts:
        def __init__(self, *_args):
            pass

        get_binding_for_profile = AsyncMock(return_value=binding)
        create_or_get_host_lease = AsyncMock(return_value=lease)

    class Bridge:
        def __init__(self, *_args):
            pass

        bind_profile_authorization = AsyncMock(return_value=object())

    class PreparationReached(Exception):
        pass

    observed = {}

    async def prepare(**kwargs):
        observed.update(kwargs)
        raise PreparationReached()

    http = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr("api_service.db.base.async_session_maker", database)
    monkeypatch.setattr(
        "moonmind.omnigent.control_plane.OmnigentControlPlaneStore", Store
    )
    monkeypatch.setattr(
        "moonmind.omnigent.oauth_hosts.OmnigentOAuthHostRepository", Hosts
    )
    monkeypatch.setattr(
        "moonmind.omnigent.bridge_store.OmnigentBridgeSessionStore", Bridge
    )
    monkeypatch.setattr(
        activities, "_claim_command", AsyncMock(return_value=(None, True))
    )
    monkeypatch.setattr(
        activities, "_load_intent_request", AsyncMock(return_value=request)
    )
    monkeypatch.setattr(
        activities,
        "_resolve_legacy_launch_policy_snapshot",
        AsyncMock(return_value=("launch@1", {})),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.profile_bound_execution._compile_persisted_effective_launch",
        lambda *_args, **_kwargs: launch,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.profile_bound_execution.OmnigentProfileBoundExecutionCoordinator._github_token",
        AsyncMock(return_value="fixture-credential"),
    )
    monkeypatch.setattr(
        activities, "_omnigent_client_context", AsyncMock(return_value=(http, object()))
    )
    monkeypatch.setattr(
        "moonmind.omnigent.oauth_host_runtime.OmnigentOAuthHostRuntime",
        lambda **_kwargs: SimpleNamespace(prepare_host=prepare),
    )
    with pytest.raises(PreparationReached):
        await activities.omnigent_ensure_host_activity(
            {
                "sessionId": "session",
                "compiledExecutionIntentRef": "artifact://intent",
                "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
                "expectedRevision": 1,
                "fencingGeneration": 1,
            }
        )
    assert observed.get("publication_git_identity") == identity
    http.aclose.assert_awaited_once()
