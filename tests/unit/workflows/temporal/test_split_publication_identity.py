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
@pytest.mark.parametrize("remediation", [False, True], ids=["ordinary", "remediation"])
@pytest.mark.parametrize(
    "commit_message", [None, "", " \t ", "  Admitted commit message  "],
    ids=["absent", "empty", "whitespace", "admitted"],
)
async def test_split_session_publication_keeps_admitted_identity(
    monkeypatch, remediation, commit_message
):
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
    if commit_message is not None:
        request.parameters["commitMessage"] = commit_message
    expected_locator = dict(request.workspace_spec["workspaceLocator"])
    if remediation:
        expected_locator = {
            "kind": "sandbox", "workspaceId": "retained-remediation", "relativePath": "candidate",
        }
        request.remediation_workspace = {"destinationWorkspaceLocator": expected_locator}
    original_parameters = dict(request.parameters)
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
        activities,
        "_repository_github_token",
        AsyncMock(return_value="fixture-credential"),
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.OmnigentWorkspacePublicationService",
        lambda _workspace_root: SimpleNamespace(publish_workspace=publish),
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
    assert observed.get("github_token") == "fixture-credential"
    # Message normalization/fallback belongs to the publisher. This port must
    # carry the admitted string rather than inventing or replacing a message.
    assert observed.get("commit_message") == commit_message
    assert observed["workspace_locator"] == expected_locator
    assert observed["publish_mode"] == "pr"
    assert observed["base_branch"] == "main"
    assert request.parameters == original_parameters
