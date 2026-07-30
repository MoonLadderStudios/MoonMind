"""Boundary tests for coordinator-backed Omnigent recovery/branch activities.

Covers MoonLadderStudios/MoonMind#3510: the production Temporal activities that
wire ``OmnigentProfileBoundExecutionCoordinator.recover_from_checkpoint()`` and
``branch_from_checkpoint()`` into the worker binding. These assert the typed
Temporal payload round-trips by alias, that the activity dispatches every field
to the coordinator, and that the ``agent_runtime`` runtime family coerces a raw
JSON activity payload into the typed request before invoking the coordinator.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from moonmind.omnigent.checkpoints import (
    CandidateWorkspaceAuthority,
    OmnigentCheckpointIdentity,
)
from moonmind.omnigent.recovery_activity_models import (
    OmnigentCheckpointBranchRequest,
    OmnigentCheckpointRecoveryRequest,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
)
from moonmind.workflows.temporal.activities import omnigent_activities

UTC = timezone.utc


def _checkpoint() -> OmnigentCheckpointIdentity:
    return OmnigentCheckpointIdentity(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="step-1",
        stepExecutionId="step-execution-1",
        attemptOrdinal=1,
        boundary="after_execution",
        providerProfileId="codex",
        credentialRef="credential://codex",
        credentialGeneration=3,
        providerLeaseRef="provider-lease-1",
        hostBindingRef="omnigent-oauth:codex",
        hostLeaseRef="host-lease-1",
        endpointRef="default",
        omnigentHostId="host-1",
        omnigentSessionId="session-1",
        bridgeSessionId="bridge-1",
        externalStateRef="artifact://external-state",
        externalStateDigest="sha256:" + "0" * 64,
        idempotencyKey="idem-1",
        effectiveLaunchRef="omnigent-launch:sha256:" + "0" * 64,
        executionProfileRef="profile://codex",
        launchPolicyRef="policy://default",
        lastBridgeEventCursor="event-4",
        firstMessageId="message-1",
        firstMessageDigest="sha256:" + "1" * 64,
        workspaceLocator={
            "kind": "sandbox",
            "workspaceId": "workspace-1",
            "relativePath": "repo",
        },
        baselineCommit="abc123",
        headCommit="def456",
        headRef="artifact://head",
        headDigest="sha256:" + "2" * 64,
        workspaceCheckpointRef="artifact://workspace-checkpoint",
        workspaceCheckpointDigest="sha256:" + "3" * 64,
        instructionRefs=["artifact://instructions"],
        contextRefs=["artifact://context"],
        sourceBranch="main",
        publicationState="unpublished",
        capturedAt=datetime(2026, 7, 12, tzinfo=UTC),
        producerVersion="moonmind-test",
        validation={
            "valid": True,
            "liveReattachAvailable": True,
            "workspaceColdRestoreAvailable": True,
            "branchCreationAvailable": True,
        },
    )


def _candidate() -> CandidateWorkspaceAuthority:
    return CandidateWorkspaceAuthority(
        loopId="mm:loop-1",
        attemptOrdinal=2,
        headRef="artifact://candidate-head/2",
        headDigest="sha256:" + "a" * 64,
        checkpointRef="artifact://workspace-checkpoint/2",
        checkpointDigest="sha256:" + "b" * 64,
    )


def _request(
    *, profile: str | None = "codex", idempotency: str = "recovery-attempt"
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=profile,
        correlationId="workflow-1",
        idempotencyKey=idempotency,
        inputRefs=["artifact://context-pack"],
    )


class _RecordingCoordinator:
    def __init__(self) -> None:
        self.recover_calls: list[dict] = []
        self.branch_calls: list[dict] = []

    async def recover_from_checkpoint(self, **kwargs):
        self.recover_calls.append(kwargs)
        return AgentRunResult(summary="recovered")

    async def branch_from_checkpoint(self, **kwargs):
        self.branch_calls.append(kwargs)
        return AgentRunResult(summary="branched")


@pytest.fixture
def patched_coordinator(monkeypatch):
    coordinator = _RecordingCoordinator()

    @asynccontextmanager
    async def _fake_coordinator():
        yield coordinator

    monkeypatch.setattr(
        omnigent_activities, "_omnigent_coordinator", _fake_coordinator
    )
    return coordinator


def test_recovery_request_round_trips_by_alias() -> None:
    request = OmnigentCheckpointRecoveryRequest(
        request=_request(),
        checkpoint=_checkpoint(),
        candidateWorkspace=_candidate(),
        providerLease={"active": True, "leaseId": "provider-lease-1"},
        hostLease={"status": "assigned", "leaseId": "host-lease-1"},
        hostRegistered=True,
        sessionValid=True,
        firstMessageConsistent=True,
        currentCredentialGeneration=3,
    )
    dumped = request.model_dump(by_alias=True, mode="json")
    restored = OmnigentCheckpointRecoveryRequest.model_validate(dumped)
    assert restored.current_credential_generation == 3
    assert restored.host_registered is True
    assert restored.candidate_workspace.checkpoint_ref == (
        "artifact://workspace-checkpoint/2"
    )


@pytest.mark.asyncio
async def test_recover_activity_dispatches_every_field(patched_coordinator) -> None:
    request = OmnigentCheckpointRecoveryRequest(
        request=_request(),
        checkpoint=_checkpoint(),
        candidateWorkspace=_candidate(),
        providerLease={"active": True, "leaseId": "provider-lease-1"},
        hostLease={"status": "assigned", "leaseId": "host-lease-1"},
        hostRegistered=True,
        sessionValid=True,
        firstMessageConsistent=True,
        currentCredentialGeneration=3,
    )

    result = await omnigent_activities.omnigent_recover_from_checkpoint_activity(
        request
    )

    assert result.summary == "recovered"
    assert len(patched_coordinator.recover_calls) == 1
    call = patched_coordinator.recover_calls[0]
    assert call["request"] is request.request
    assert call["checkpoint"] is request.checkpoint
    assert call["candidate_workspace"] is request.candidate_workspace
    assert call["provider_lease"] == {"active": True, "leaseId": "provider-lease-1"}
    assert call["host_registered"] is True
    assert call["session_valid"] is True
    assert call["first_message_consistent"] is True
    assert call["current_credential_generation"] == 3


@pytest.mark.asyncio
async def test_branch_activity_dispatches_to_coordinator(patched_coordinator) -> None:
    request = OmnigentCheckpointBranchRequest(
        request=_request(idempotency="branch-turn-1"),
        checkpoint=_checkpoint(),
        candidateWorkspace=_candidate(),
        currentCredentialGeneration=3,
    )

    result = await omnigent_activities.omnigent_branch_from_checkpoint_activity(request)

    assert result.summary == "branched"
    assert len(patched_coordinator.branch_calls) == 1
    call = patched_coordinator.branch_calls[0]
    assert call["request"] is request.request
    assert call["checkpoint"] is request.checkpoint
    assert call["candidate_workspace"] is request.candidate_workspace
    assert call["current_credential_generation"] == 3


@pytest.mark.asyncio
async def test_recover_activity_requires_execution_profile_ref(
    patched_coordinator,
) -> None:
    request = OmnigentCheckpointRecoveryRequest(
        request=_request(profile=None),
        checkpoint=_checkpoint(),
        candidateWorkspace=_candidate(),
        currentCredentialGeneration=3,
    )
    with pytest.raises(ValueError, match="executionProfileRef"):
        await omnigent_activities.omnigent_recover_from_checkpoint_activity(request)
    assert patched_coordinator.recover_calls == []


@pytest.mark.asyncio
async def test_branch_activity_requires_execution_profile_ref(
    patched_coordinator,
) -> None:
    request = OmnigentCheckpointBranchRequest(
        request=_request(profile=None, idempotency="branch-turn-1"),
        checkpoint=_checkpoint(),
        candidateWorkspace=_candidate(),
        currentCredentialGeneration=3,
    )
    with pytest.raises(ValueError, match="executionProfileRef"):
        await omnigent_activities.omnigent_branch_from_checkpoint_activity(request)
    assert patched_coordinator.branch_calls == []


@pytest.mark.asyncio
async def test_runtime_family_coerces_recovery_json_payload(
    patched_coordinator, monkeypatch
) -> None:
    """The agent_runtime family coerces a raw JSON payload into the typed model."""

    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )

    request = OmnigentCheckpointRecoveryRequest(
        request=_request(),
        checkpoint=_checkpoint(),
        candidateWorkspace=_candidate(),
        currentCredentialGeneration=3,
    )
    payload = request.model_dump(by_alias=True, mode="json")

    family = TemporalAgentRuntimeActivities.__new__(TemporalAgentRuntimeActivities)
    result = await family.integration_omnigent_recover_from_checkpoint(payload)

    assert result.summary == "recovered"
    assert len(patched_coordinator.recover_calls) == 1


@pytest.mark.asyncio
async def test_runtime_family_coerces_branch_json_payload(
    patched_coordinator,
) -> None:
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )

    request = OmnigentCheckpointBranchRequest(
        request=_request(idempotency="branch-turn-1"),
        checkpoint=_checkpoint(),
        candidateWorkspace=_candidate(),
        currentCredentialGeneration=3,
    )
    payload = request.model_dump(by_alias=True, mode="json")

    family = TemporalAgentRuntimeActivities.__new__(TemporalAgentRuntimeActivities)
    result = await family.integration_omnigent_branch_from_checkpoint(payload)

    assert result.summary == "branched"
    assert len(patched_coordinator.branch_calls) == 1
