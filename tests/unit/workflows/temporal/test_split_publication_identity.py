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
