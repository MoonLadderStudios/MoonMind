import inspect
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent import execute as omnigent_execute_module
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointIdentity,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities import (
    omnigent_activities as omnigent_activities_module,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    OMNIGENT_RECOVERY_DIRECTIVE_PARAM,
    _dispatch_omnigent_execution,
    encode_omnigent_recovery_directive,
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


def _checkpoint_directive_payload(kind: str) -> dict:
    checkpoint = OmnigentCheckpointIdentity(
        providerProfileId="pp-1",
        credentialGeneration=3,
        providerLeaseRef="lease-1",
        hostBindingRef="hb-1",
        hostLeaseRef="hl-1",
        endpointRef="ep-1",
        omnigentHostId="host-1",
        omnigentSessionId="sess-1",
        bridgeSessionId="bridge-1",
        externalStateRef="artifact://ext/1",
        idempotencyKey="idem-1",
        effectiveLaunchRef="omnigent-launch:sha256:" + "a" * 64,
    )
    candidate = CandidateWorkspaceAuthority(
        loopId="loop-1",
        attemptOrdinal=1,
        headRef="artifact://head/1",
        headDigest="sha256:" + "b" * 64,
        checkpointRef="artifact://ck/1",
        checkpointDigest="sha256:" + "c" * 64,
    )
    return {
        "kind": kind,
        "checkpoint": checkpoint.model_dump(by_alias=True, mode="json"),
        "candidateWorkspace": candidate.model_dump(by_alias=True, mode="json"),
        "currentCredentialGeneration": 3,
        "providerLease": {"active": True, "lease_id": "lease-1"},
        "hostLease": {"status": "ready", "lease_id": "hl-1"},
        "hostRegistered": True,
        "sessionValid": True,
        "firstMessageConsistent": True,
    }


def _directive_request(kind: str | None) -> AgentExecutionRequest:
    parameters = {}
    if kind is not None:
        parameters[OMNIGENT_RECOVERY_DIRECTIVE_PARAM] = (
            encode_omnigent_recovery_directive(_checkpoint_directive_payload(kind))
        )
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey="idem-new",
        executionProfileRef="pp-1",
        parameters=parameters,
    )


@pytest.mark.asyncio
async def test_dispatch_without_directive_calls_execute() -> None:
    coordinator = AsyncMock()
    request = _directive_request(None)

    await _dispatch_omnigent_execution(coordinator, request)

    coordinator.execute.assert_awaited_once_with(request)
    coordinator.recover_from_checkpoint.assert_not_called()
    coordinator.branch_from_checkpoint.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_recover_directive_routes_to_recover_from_checkpoint() -> None:
    coordinator = AsyncMock()
    request = _directive_request("recover")

    await _dispatch_omnigent_execution(coordinator, request)

    coordinator.execute.assert_not_called()
    coordinator.branch_from_checkpoint.assert_not_called()
    coordinator.recover_from_checkpoint.assert_awaited_once()
    kwargs = coordinator.recover_from_checkpoint.await_args.kwargs
    assert isinstance(kwargs["checkpoint"], OmnigentCheckpointIdentity)
    assert kwargs["checkpoint"].provider_profile_id == "pp-1"
    assert isinstance(kwargs["candidate_workspace"], CandidateWorkspaceAuthority)
    assert kwargs["current_credential_generation"] == 3
    assert kwargs["host_registered"] is True
    assert kwargs["session_valid"] is True
    assert kwargs["first_message_consistent"] is True
    assert kwargs["provider_lease"] == {"active": True, "lease_id": "lease-1"}


@pytest.mark.asyncio
async def test_dispatch_branch_directive_routes_to_branch_from_checkpoint() -> None:
    coordinator = AsyncMock()
    request = _directive_request("branch")

    await _dispatch_omnigent_execution(coordinator, request)

    coordinator.execute.assert_not_called()
    coordinator.recover_from_checkpoint.assert_not_called()
    coordinator.branch_from_checkpoint.assert_awaited_once()
    kwargs = coordinator.branch_from_checkpoint.await_args.kwargs
    assert isinstance(kwargs["checkpoint"], OmnigentCheckpointIdentity)
    assert isinstance(kwargs["candidate_workspace"], CandidateWorkspaceAuthority)
    assert kwargs["current_credential_generation"] == 3
    assert kwargs["request"] is request


@pytest.mark.asyncio
async def test_dispatch_rejects_credential_like_lease_values() -> None:
    import json

    coordinator = AsyncMock()
    payload = _checkpoint_directive_payload("recover")
    payload["providerLease"] = {"authorization": "Bearer secret-token"}
    # Encode the raw payload directly (bypassing the encoder's validation) so
    # the dispatch-time model validation is the boundary under test.
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey="idem-new",
        executionProfileRef="pp-1",
        parameters={OMNIGENT_RECOVERY_DIRECTIVE_PARAM: json.dumps(payload)},
    )

    with pytest.raises(ValueError):
        await _dispatch_omnigent_execution(coordinator, request)
    coordinator.recover_from_checkpoint.assert_not_called()


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
