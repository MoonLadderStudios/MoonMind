import inspect
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

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
    dispatch_profile_bound_execution,
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


def _checkpoint_execution(action: str = "resume") -> dict[str, object]:
    return {
        "action": action,
        "checkpoint": {
            "providerProfileId": "codex",
            "credentialGeneration": 3,
            "providerLeaseRef": "provider-lease-1",
            "hostBindingRef": "binding-1",
            "hostLeaseRef": "host-lease-1",
            "endpointRef": "endpoint-1",
            "omnigentHostId": "host-1",
            "omnigentSessionId": "session-1",
            "bridgeSessionId": "bridge-1",
            "externalStateRef": "artifact://state/1",
            "idempotencyKey": "source-attempt",
        },
        "candidateWorkspace": {
            "loopId": "loop-1",
            "attemptOrdinal": 2,
            "headRef": "artifact://head/2",
            "headDigest": "sha256:" + "a" * 64,
            "checkpointRef": "artifact://checkpoint/2",
            "checkpointDigest": "sha256:" + "b" * 64,
        },
        "currentCredentialGeneration": 3,
        "providerLease": {"active": True, "leaseId": "provider-lease-1"},
        "hostLease": {
            "status": "assigned",
            "leaseId": "host-lease-1",
            "credentialGeneration": 3,
        },
        "hostRegistered": True,
        "sessionValid": True,
        "firstMessageConsistent": True,
        "eventCursorValid": True,
        "workspaceAuthorityValid": True,
        "policyValid": True,
        "originalInputUnchanged": action == "resume",
        "validationRef": "artifact://validation/1",
    }


@pytest.mark.asyncio
async def test_checkpoint_resume_dispatches_to_recovery_coordinator() -> None:
    expected = AgentRunResult(summary="resumed", output_refs=[])
    coordinator = SimpleNamespace(
        execute=AsyncMock(),
        recover_from_checkpoint=AsyncMock(return_value=expected),
        branch_from_checkpoint=AsyncMock(),
    )
    request = AgentExecutionRequest(
        agentKind="external", agentId="omnigent", executionProfileRef="codex",
        correlationId="workflow-1", idempotencyKey="resume-2",
        parameters={"checkpointExecution": _checkpoint_execution()},
    )

    assert await dispatch_profile_bound_execution(coordinator, request) == expected
    coordinator.recover_from_checkpoint.assert_awaited_once()
    coordinator.execute.assert_not_awaited()
    coordinator.branch_from_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkpoint_branch_dispatches_to_separate_branch_path() -> None:
    expected = AgentRunResult(summary="branched", output_refs=[])
    coordinator = SimpleNamespace(
        execute=AsyncMock(), recover_from_checkpoint=AsyncMock(),
        branch_from_checkpoint=AsyncMock(return_value=expected),
    )
    request = AgentExecutionRequest(
        agentKind="external", agentId="omnigent", executionProfileRef="codex",
        correlationId="workflow-1", idempotencyKey="branch-2",
        parameters={"checkpointExecution": _checkpoint_execution("branch")},
    )

    assert await dispatch_profile_bound_execution(coordinator, request) == expected
    coordinator.branch_from_checkpoint.assert_awaited_once()
    coordinator.recover_from_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkpoint_resume_fails_closed_on_changed_input() -> None:
    payload = _checkpoint_execution()
    payload["originalInputUnchanged"] = False
    request = AgentExecutionRequest(
        agentKind="external", agentId="omnigent", executionProfileRef="codex",
        correlationId="workflow-1", idempotencyKey="resume-2",
        parameters={"checkpointExecution": payload},
    )

    with pytest.raises(ValueError, match="branch_required:immutable_input_changed"):
        await dispatch_profile_bound_execution(SimpleNamespace(), request)
