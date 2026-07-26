import inspect
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent import execute as omnigent_execute_module
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities import (
    omnigent_activities as omnigent_activities_module,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _checkpoint_execution_from_request,
    omnigent_execute_activity,
)


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_omnigent_execute_activity_delegates(mock_run):
    expected_result = AgentRunResult(summary="done", output_refs=[])
    mock_run.return_value = expected_result

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="123",
        idempotencyKey="key",
    )

    env = ActivityEnvironment()
    result = await env.run(omnigent_execute_activity, req)

    assert result == expected_result
    mock_run.assert_called_once()
    called_req = mock_run.call_args.args[0]
    assert called_req == req
    assert isinstance(mock_run.call_args.kwargs["artifact_gateway"], LocalOmnigentArtifactGateway)
    assert isinstance(mock_run.call_args.kwargs["run_store"], OmnigentBridgeSessionStore)


def test_omnigent_execution_path_does_not_use_managed_github_broker() -> None:
    """Omnigent is an external-agent adapter, not a managed runtime launcher."""

    source = "\n".join(
        [
            inspect.getsource(omnigent_activities_module),
            inspect.getsource(omnigent_execute_module),
        ]
    )

    for disallowed in (
        "github_auth_broker",
        "GitHubAuthBroker",
        "build_github_socket_path",
        "render_gh_wrapper_script",
        "render_git_credential_helper_script",
        "GITHUB_TOKEN",
    ):
        assert disallowed not in source


def test_checkpoint_execution_dispatch_is_typed_and_workflow_scoped() -> None:
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "codex",
            "correlationId": "workflow-1",
            "idempotencyKey": "branch-turn-1",
            "parameters": {
                "metadata": {
                    "moonmind": {
                        "omnigentCheckpointExecution": {
                            "action": "branch",
                            "checkpoint": {
                                "providerProfileId": "codex",
                                "credentialGeneration": 3,
                                "hostBindingRef": "artifact://host-binding",
                                "endpointRef": "omnigent-endpoint:host-1",
                                "bridgeSessionId": "bridge-1",
                                "externalStateRef": "artifact://external-state",
                                "idempotencyKey": "source-message-1",
                            },
                            "candidateWorkspace": {
                                "loopId": "branch:turn-1",
                                "attemptOrdinal": 1,
                                "headRef": "artifact://head/1",
                                "headDigest": "sha256:" + "a" * 64,
                                "checkpointRef": "artifact://checkpoint/1",
                                "checkpointDigest": "sha256:" + "b" * 64,
                            },
                            "currentCredentialGeneration": 3,
                        }
                    }
                }
            },
        }
    )

    dispatch = _checkpoint_execution_from_request(request)

    assert dispatch is not None
    assert dispatch.action.value == "branch"
    assert dispatch.checkpoint.idempotency_key == "source-message-1"
    assert dispatch.candidate_workspace.checkpoint_ref == "artifact://checkpoint/1"
