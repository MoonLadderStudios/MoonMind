import inspect
from unittest.mock import AsyncMock, patch

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
    execute_profile_bound_checkpoint_request,
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


def _checkpoint_execution(*, kind: str = "recovery", **overrides):
    payload = {
        "kind": kind,
        "checkpoint": {
            "providerProfileId": "profile-1",
            "credentialGeneration": 3,
            "providerLeaseRef": "provider-lease-1",
            "hostBindingRef": "artifact://host-binding",
            "hostLeaseRef": "host-lease-1",
            "endpointRef": "artifact://endpoint",
            "omnigentHostId": "host-1",
            "omnigentSessionId": "session-1",
            "bridgeSessionId": "bridge-1",
            "externalStateRef": "artifact://external-state",
            "idempotencyKey": "first-message-key",
        },
        "candidateWorkspace": {
            "loopId": "loop-1",
            "attemptOrdinal": 2,
            "headRef": "artifact://head",
            "headDigest": "sha256:" + "a" * 64,
            "checkpointRef": "artifact://workspace-checkpoint",
            "checkpointDigest": "sha256:" + "b" * 64,
        },
        "currentCredentialGeneration": 3,
        "providerLease": {"active": True, "leaseId": "provider-lease-1"},
        "hostLease": {
            "status": "ready",
            "leaseId": "host-lease-1",
            "credentialGeneration": 3,
        },
        "hostRegistered": True,
        "sessionValid": True,
        "firstMessageConsistent": True,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_profile_bound_activity_calls_live_recovery_coordinator() -> None:
    coordinator = AsyncMock()
    coordinator.recover_from_checkpoint.return_value = AgentRunResult(summary="done")
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId="correlation",
        idempotencyKey="recovery-key",
        parameters={"checkpointExecution": _checkpoint_execution()},
    )

    result = await execute_profile_bound_checkpoint_request(
        coordinator=coordinator, request=request
    )

    coordinator.recover_from_checkpoint.assert_awaited_once()
    coordinator.branch_from_checkpoint.assert_not_awaited()
    assert result.metadata["checkpointRecovery"]["mode"] == "live_reattach"
    assert (
        result.metadata["checkpointRecovery"]["sourceCheckpointRef"]
        == "artifact://workspace-checkpoint"
    )


@pytest.mark.asyncio
async def test_profile_bound_activity_calls_cold_restore_when_host_authority_is_stale(
) -> None:
    coordinator = AsyncMock()
    coordinator.recover_from_checkpoint.return_value = AgentRunResult(summary="done")
    checkpoint_execution = _checkpoint_execution(
        hostLease={
            "status": "expired",
            "leaseId": "host-lease-1",
            "credentialGeneration": 3,
        }
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId="correlation",
        idempotencyKey="cold-restore-key",
        parameters={"checkpointExecution": checkpoint_execution},
    )

    result = await execute_profile_bound_checkpoint_request(
        coordinator=coordinator, request=request
    )

    coordinator.recover_from_checkpoint.assert_awaited_once()
    coordinator.branch_from_checkpoint.assert_not_awaited()
    assert result.metadata["checkpointRecovery"]["mode"] == "cold_restore"
    assert (
        result.metadata["checkpointRecovery"]["reason"]
        == "replacement_host_required"
    )


@pytest.mark.asyncio
async def test_profile_bound_activity_routes_changed_input_to_branch() -> None:
    coordinator = AsyncMock()
    coordinator.branch_from_checkpoint.return_value = AgentRunResult(summary="done")
    checkpoint_execution = _checkpoint_execution(
        kind="branch",
        immutableInputMatches=False,
        changedFields=["instructions"],
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId="correlation",
        idempotencyKey="branch-key",
        parameters={"checkpointExecution": checkpoint_execution},
    )

    result = await execute_profile_bound_checkpoint_request(
        coordinator=coordinator, request=request
    )

    coordinator.branch_from_checkpoint.assert_awaited_once()
    coordinator.recover_from_checkpoint.assert_not_awaited()
    assert result.metadata["checkpointRecovery"]["mode"] == "branch_required"


@pytest.mark.asyncio
async def test_profile_bound_activity_blocks_invalid_checkpoint_evidence() -> None:
    coordinator = AsyncMock()
    checkpoint_execution = _checkpoint_execution(
        validationPassed=False,
        validationReason="policy_denied",
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId="correlation",
        idempotencyKey="recovery-key",
        parameters={"checkpointExecution": checkpoint_execution},
    )

    with pytest.raises(ValueError, match="resume_unavailable:policy_denied"):
        await execute_profile_bound_checkpoint_request(
            coordinator=coordinator, request=request
        )

    coordinator.execute.assert_not_awaited()
    coordinator.recover_from_checkpoint.assert_not_awaited()
    coordinator.branch_from_checkpoint.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind_overrides", "idempotency_key"),
    [
        ({}, "stale-recovery-key"),
        (
            {
                "kind": "branch",
                "immutableInputMatches": False,
                "changedFields": ["instructions"],
            },
            "stale-branch-key",
        ),
    ],
)
async def test_profile_bound_activity_blocks_stale_credential_generation(
    kind_overrides: dict[str, object],
    idempotency_key: str,
) -> None:
    coordinator = AsyncMock()
    checkpoint_execution = _checkpoint_execution(
        currentCredentialGeneration=4,
        **kind_overrides,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId="correlation",
        idempotencyKey=idempotency_key,
        parameters={"checkpointExecution": checkpoint_execution},
    )

    with pytest.raises(
        ValueError,
        match="resume_unavailable:credential_generation_mismatch",
    ):
        await execute_profile_bound_checkpoint_request(
            coordinator=coordinator,
            request=request,
        )

    coordinator.execute.assert_not_awaited()
    coordinator.recover_from_checkpoint.assert_not_awaited()
    coordinator.branch_from_checkpoint.assert_not_awaited()
