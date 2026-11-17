"""Hermetic Temporal boundary for MoonLadderStudios/MoonMind#3621."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import (
    Base,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactPin,
    TemporalArtifactRetentionClass,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    CheckpointBranchTurnLaunchError,
)
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.temporal_models import StepExecutionCheckpointModel
from moonmind.workflows import get_temporal_artifact_repository
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    CheckpointBranchRetainedEvidenceError,
    MoonMindCheckpointBranchTurnWorkflow,
    persist_checkpoint_branch_turn_terminal,
    persist_checkpoint_branch_turn_terminal_rejection,
)
from tests.artifact_access import access_for_test

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

CALLS: list[tuple[str, object]] = []
TRANSIENT_FAILURES: dict[str, int] = {}
EFFECT_IDENTITIES: dict[str, set[str]] = {}
WORKFLOW_BOUNDARY: tuple[str, str] | None = None
WORKFLOW_BOUNDARY_REACHED: asyncio.Event | None = None
WORKFLOW_BOUNDARY_RELEASE: asyncio.Event | None = None
CHECKPOINT_PAYLOADS: list[dict] = []
TERMINAL_PAYLOADS: list[dict] = []


async def _pause_workflow_boundary(stage: str, position: str) -> None:
    """Hold a real Activity call until Temporal cancellation reaches it."""

    if WORKFLOW_BOUNDARY != (stage, position):
        return
    assert WORKFLOW_BOUNDARY_REACHED is not None
    assert WORKFLOW_BOUNDARY_RELEASE is not None
    WORKFLOW_BOUNDARY_REACHED.set()
    await WORKFLOW_BOUNDARY_RELEASE.wait()


def _record_effect_and_inject(stage: str, identity: str) -> None:
    """Emulate a crash on either side of one retry-stable Activity effect."""

    before_key = f"before:{stage}"
    remaining = TRANSIENT_FAILURES.get(before_key, 0)
    if remaining > 0:
        TRANSIENT_FAILURES[before_key] = remaining - 1
        raise RuntimeError(f"injected retry before {stage}")
    EFFECT_IDENTITIES.setdefault(stage, set()).add(identity)
    after_key = f"after:{stage}"
    remaining = TRANSIENT_FAILURES.get(after_key, 0)
    if remaining > 0:
        TRANSIENT_FAILURES[after_key] = remaining - 1
        raise RuntimeError(f"injected retry after {stage}")


@workflow.defn(name="MoonMind.AgentRun")
class _FakeAgentRun:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        if request.correlation_id == "canceled":
            await workflow.wait_condition(lambda: False)
        if request.correlation_id == "worker-failure":
            raise ApplicationError(
                "worker failed before terminal delivery",
                non_retryable=True,
            )
        if request.correlation_id == "provider-failure":
            return AgentRunResult(
                summary="provider failed",
                failureClass="execution_error",
                providerErrorCode="provider_terminal_failed",
                diagnosticsRef="artifact://diagnostics/provider-failed",
            )
        return AgentRunResult(
            outputRefs=["artifact://output/branch-result"],
            summary="branch turn completed",
            diagnosticsRef="artifact://diagnostics/runtime",
            metadata={
                "omnigentCheckpointCapture": {
                    "bridgeSessionId": "fresh-bridge-session",
                    "omnigentSessionId": "fresh-provider-session",
                    "terminalRef": "artifact://terminal/fresh-session",
                }
            },
        )


@activity.defn(name="checkpoint_branch.turn.mark_running")
async def _mark_running(payload: dict) -> None:
    CALLS.append(("mark_running", payload["agentRunWorkflowId"]))
    _record_effect_and_inject("mark_running", payload["agentRunWorkflowId"])


@activity.defn(name="workspace.capture_checkpoint")
async def _capture_workspace(payload: dict) -> dict:
    await _pause_workflow_boundary("parent_capture", "before")
    CALLS.append(("capture", payload["idempotencyKey"]))
    _record_effect_and_inject("capture", payload["idempotencyKey"])
    return {
        "status": "captured",
        "workspace": {
            "kind": "worktree_archive",
            "baseCommit": payload["baseCommit"],
            "archiveRef": "artifact://workspace/branch-turn",
            "archiveDigest": "sha256:" + "c" * 64,
        },
        "diagnosticRefs": ["artifact://diagnostics/capture"],
    }


@activity.defn(name="step_checkpoint.create_v2")
async def _create_checkpoint(payload: dict) -> dict:
    CHECKPOINT_PAYLOADS.append(copy.deepcopy(payload))
    CALLS.append(("checkpoint", payload["idempotencyKey"]))
    _record_effect_and_inject("checkpoint", payload["idempotencyKey"])
    return {
        "checkpointRef": "artifact://checkpoint/branch-turn-result",
        "idempotencyKey": payload["idempotencyKey"],
    }


@activity.defn(name="checkpoint_branch.turn.persist_terminal")
async def _persist_terminal(payload: dict) -> dict:
    TERMINAL_PAYLOADS.append(copy.deepcopy(payload))
    CALLS.append(("terminal", payload["outcome"]))
    _record_effect_and_inject(
        "terminal",
        f"{payload['branchTurnId']}:{payload['outcome']}",
    )
    result = AgentRunResult.model_validate(payload["agentResult"])
    succeeded = payload["outcome"] == "succeeded" and not result.failure_class
    canceled = payload["outcome"] == "canceled"
    status = "checking" if succeeded else "canceled" if canceled else "failed"
    disposition = (
        "verification_pending"
        if succeeded
        else "canceled"
        if canceled
        else "provider_failure"
    )
    return {
        "branchId": payload["branchId"],
        "branchTurnId": payload["branchTurnId"],
        "status": status,
        "deliveryOutcome": status,
        "terminalDisposition": disposition,
        "verificationPending": succeeded,
        "checkpointRef": (
            payload.get("checkpoint", {}).get("checkpointRef") if succeeded else None
        ),
    }


@activity.defn(name="checkpoint_branch.turn.persist_terminal_rejection")
async def _persist_terminal_rejection(payload: dict) -> dict:
    CALLS.append(("terminal_rejection", payload["terminalPayloadDigest"]))
    return {
        "branchId": payload["branchId"],
        "branchTurnId": payload["branchTurnId"],
        "status": "blocked",
        "deliveryOutcome": "blocked",
        "terminalDisposition": "retained_evidence_rejected",
        "verificationPending": False,
    }


def _checkpoint_branch_policy_snapshot() -> dict:
    """Return the validated on-demand policy the source checkpoint names."""

    document = {
        "schemaVersion": 1,
        "endpoint": {"ref": "default", "bridgeModes": ["embedded"]},
        "execution": {
            "profileRef": "omnigent-codex@1",
            "harness": "codex-native",
            "agentIdentities": ["codex-native-ui"],
        },
        "host": {
            "mode": "on_demand_docker",
            "backendRef": "container-backend",
            "architectures": ["amd64"],
            "serverImageRef": "images/omnigent@sha256:" + "1" * 64,
            "hostImageRef": "images/host@sha256:" + "2" * 64,
        },
        "resources": {
            "cpuMillis": 2000,
            "memoryMiB": 4096,
            "processes": 256,
            "timeoutSeconds": 5400,
            "temporaryStorageMiB": 256,
            "concurrency": 1,
        },
        "network": {
            "attachmentRef": "control-plane-network",
            "egressProfileRef": "egress-default",
        },
        "workspace": {
            "allowedClasses": ["workflow"],
            "repositoryMutation": True,
            "mountClasses": ["workspace", "oauth_home"],
            "runtimeUid": 1000,
            "runtimeGid": 1000,
        },
        "providerProfile": {
            "compatibleProviders": ["codex"],
            "queueWhenBusy": True,
        },
        "session": {
            "create": True,
            "firstMessage": "required",
            "continuation": True,
            "interruption": True,
            "cancellation": True,
            "cleanup": "remove",
        },
        "capture": {
            "required": True,
            "artifactClasses": ["events", "snapshot"],
            "maxLogBytes": 1_000_000,
            "redaction": "required",
        },
        "checkpoint": {
            "capture": True,
            "resume": True,
            "branch": True,
            "publication": "approval",
            "promotion": "verified",
        },
        "remediation": {
            "actions": ["retry"],
            "riskTiers": {"retry": "low"},
            "locks": True,
            "maxActions": 3,
            "autonomous": False,
        },
        "rag": {
            "initialScope": "workflow",
            "followupScope": "session",
            "collectionRefs": ["default"],
            "tokenBudget": 4000,
            "fallback": "deny",
            "credentialRef": "retrieval-default",
        },
        "approvals": {
            "actions": {
                "read": {"decision": "allow", "reason": "read-only"},
                "publish": {
                    "decision": "approval_required",
                    "approvalClass": "release",
                    "reviewerRule": "owner",
                    "reason": "publication",
                },
            }
        },
        "retention": {"days": 30, "deletion": "after-expiry"},
        "rollout": {"cohort": "default", "gate": "ready", "diagnostics": True},
    }
    return compile_policy_snapshot(
        policy_id="codex-on-demand",
        version=1,
        document=document,
        validation={"valid": True, "diagnostics": []},
    )


def _request(
    correlation_id: str, *, publish_mode: str = "none"
) -> AgentExecutionRequest:
    policy = _checkpoint_branch_policy_snapshot()
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile-1",
        correlationId=correlation_id,
        idempotencyKey=f"branch-turn:{correlation_id}",
        instructionRef="artifact://instruction/branch-turn",
        inputRefs=[
            "artifact://instruction/branch-turn",
            "artifact://checkpoint/source",
        ],
        stepExecution={
            "schemaVersion": "v1",
            "workflowId": "checkpoint-branch-turn:turn-1",
            "runId": "branch-turn-turn-1",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
            "stepExecutionId": (
                "checkpoint-branch-turn:turn-1:branch-turn-turn-1:"
                "implement:execution:1"
            ),
            "reason": "checkpoint_branch",
            "runtimeContextPolicy": "fresh_agent_run",
            "contextBundleRef": "artifact://context/turn-1",
            "contextBundleDigest": "sha256:" + "a" * 64,
            "preparedInputRefs": ["artifact://instruction/branch-turn"],
        },
        checkpointRecovery={
            "recoveryAction": "branch_required",
            "omnigentCheckpoint": {
                "schemaVersion": "v2",
                "workflowId": "source-workflow",
                "runId": "source-run",
                "logicalStepId": "implement",
                "stepExecutionId": "source-step-execution",
                "attemptOrdinal": 1,
                "boundary": "after_execution",
                "providerProfileId": "profile-1",
                "credentialRef": "credential://profile-1",
                "credentialGeneration": 1,
                "providerLeaseRef": "source-provider-lease",
                "hostBindingRef": "source-binding",
                "hostLeaseRef": "source-host-lease",
                "endpointRef": "default",
                "omnigentHostId": "source-host",
                "omnigentSessionId": "source-provider-session",
                "bridgeSessionId": "source-bridge-session",
                "externalStateRef": "artifact://checkpoint/external-state",
                "externalStateDigest": "sha256:" + "d" * 64,
                "idempotencyKey": "source-first-message",
                "executionProfileRef": "profile-1",
                "launchPolicyRef": "codex-on-demand@1",
                "policyId": policy["policyId"],
                "policyVersion": policy["policyVersion"],
                "policyRef": policy["policyRef"],
                "policyDigest": policy["policyDigest"],
                "policySnapshotRef": policy["snapshotRef"],
                "policyValidation": policy["validation"],
                "lastBridgeEventCursor": "4",
                "firstMessageId": "source-message",
                "firstMessageDigest": "sha256:" + "e" * 64,
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "source-workspace",
                    "relativePath": "repo",
                },
                "baselineCommit": "abc123",
                "headCommit": "def456",
                "headRef": "artifact://checkpoint/head",
                "headDigest": "sha256:" + "f" * 64,
                "workspaceCheckpointRef": "artifact://checkpoint/workspace",
                "workspaceCheckpointDigest": "sha256:" + "0" * 64,
                "sourceBranch": "main",
                "publicationState": "none",
                "capturedAt": "2026-08-12T00:00:00Z",
                "producerVersion": "moonmind-test",
                "validation": {
                    "valid": True,
                    "liveReattachAvailable": True,
                    "workspaceColdRestoreAvailable": True,
                    "branchCreationAvailable": True,
                },
            },
            "immutableSource": {"instructionDigest": "sha256:" + "b" * 64},
            "immutableRequested": {"instructionDigest": "sha256:" + "c" * 64},
        },
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "branch-turn-workspace",
                "relativePath": "repo",
            },
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "mm/source/branch-1",
            "checkoutCommit": "abc123",
        },
        parameters={
            "repository": "MoonLadderStudios/MoonMind",
            "startingBranch": "main",
            "targetBranch": "mm/source/branch-1",
            "publishMode": publish_mode,
        },
    )


def _input(
    correlation_id: str,
    *,
    publish_mode: str = "none",
    agent_run_workflow_id: str | None = None,
) -> dict:
    request = _request(correlation_id, publish_mode=publish_mode)
    return {
        "schemaVersion": "checkpoint-branch-turn-execution/v1",
        "workflowId": "source-workflow",
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "principal": "service:test",
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "agentRunWorkflowId": (
            agent_run_workflow_id
            or f"checkpoint-branch-agent:{correlation_id}"
        ),
        "agentRequest": request.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        "sourceCheckpointRef": "artifact://checkpoint/source",
        "instructionRef": "artifact://instruction/branch-turn",
        "workspaceLocator": request.workspace_spec["workspaceLocator"],
        "baseCommit": "abc123",
    }


async def _run(
    correlation_id: str,
    *,
    cancel: bool = False,
    cancel_ready: asyncio.Event | None = None,
    transient_failures: dict[str, int] | None = None,
):
    TRANSIENT_FAILURES.clear()
    TRANSIENT_FAILURES.update(transient_failures or {})
    EFFECT_IDENTITIES.clear()
    CHECKPOINT_PAYLOADS.clear()
    TERMINAL_PAYLOADS.clear()
    queue = f"checkpoint-branch-turn-{uuid4()}"
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=queue,
                        workflows=[
                            MoonMindCheckpointBranchTurnWorkflow,
                            _FakeAgentRun,
                        ],
                        activities=[
                            _mark_running,
                            _persist_terminal,
                            _persist_terminal_rejection,
                        ],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=SANDBOX_TASK_QUEUE,
                        activities=[_capture_workspace],
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=ARTIFACTS_TASK_QUEUE,
                        activities=[
                            _create_checkpoint,
                            _mark_running,
                            _persist_terminal,
                            _persist_terminal_rejection,
                        ],
                    )
                )
                handle = await env.client.start_workflow(
                    MoonMindCheckpointBranchTurnWorkflow.run,
                    _input(correlation_id),
                    id=f"checkpoint-branch-owner-{correlation_id}-{uuid4()}",
                    task_queue=queue,
                )
                if cancel:
                    if cancel_ready is not None:
                        await asyncio.wait_for(cancel_ready.wait(), timeout=10)
                    else:
                        for _attempt in range(100):
                            if any(name == "mark_running" for name, _value in CALLS):
                                break
                            await asyncio.sleep(0.01)
                        assert any(name == "mark_running" for name, _value in CALLS)
                    await handle.cancel()
                    with pytest.raises(WorkflowFailureError):
                        await handle.result()
                    # ABANDON keeps the retrying terminal Activity alive after
                    # the workflow cancellation is acknowledged. Keep the
                    # worker boundary open until that durable side effect is
                    # observable instead of racing worker shutdown against it.
                    for _attempt in range(200):
                        if any(name == "terminal" for name, _value in CALLS):
                            break
                        await asyncio.sleep(0.01)
                    assert any(name == "terminal" for name, _value in CALLS)
                    result = None
                else:
                    result = await handle.result()
                history = await handle.fetch_history()
        return result, history
    finally:
        TRANSIENT_FAILURES.clear()


async def test_checkpoint_branch_turn_runs_agent_captures_checkpoint_and_hands_off(
) -> None:
    CALLS.clear()
    result, history = await _run("success")

    assert result["status"] == "checking"
    assert result["verificationPending"] is True
    assert result["checkpointRef"] == "artifact://checkpoint/branch-turn-result"
    assert [name for name, _value in CALLS] == [
        "mark_running",
        "capture",
        "checkpoint",
        "terminal",
    ]
    assert CALLS[0][1] == "checkpoint-branch-agent:success"
    assert CHECKPOINT_PAYLOADS[0]["planDigest"] == "sha256:" + "a" * 64
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.parametrize(
    ("stage", "position"),
    [
        (stage, position)
        for stage in ("mark_running", "capture", "checkpoint", "terminal")
        for position in ("before", "after")
    ],
)
async def test_checkpoint_branch_turn_activity_restart_reuses_owned_identity(
    stage: str,
    position: str,
) -> None:
    """A crash around an Activity side effect cannot allocate a second identity."""

    CALLS.clear()
    result, history = await _run(
        f"retry-{position}-{stage}",
        transient_failures={f"{position}:{stage}": 1},
    )

    assert result["status"] == "checking"
    assert len(EFFECT_IDENTITIES[stage]) == 1
    assert [name for name, _value in CALLS].count(stage) == 2
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_checkpoint_branch_turn_provider_failure_skips_false_checkpoint_success(
) -> None:
    CALLS.clear()
    result, _history = await _run("provider-failure")

    assert result["status"] == "failed"
    assert result["verificationPending"] is False
    assert result["checkpointRef"] is None
    assert [name for name, _value in CALLS] == ["mark_running", "terminal"]


async def test_checkpoint_branch_turn_worker_failure_persists_failed_handoff() -> None:
    CALLS.clear()
    result, _history = await _run("worker-failure")

    assert result["status"] == "failed"
    assert result["verificationPending"] is False
    assert result["checkpointRef"] is None
    assert [name for name, _value in CALLS] == ["mark_running", "terminal"]


async def test_checkpoint_branch_turn_cancellation_persists_terminal_handoff() -> None:
    CALLS.clear()
    result, history = await _run("canceled", cancel=True)

    assert result is None
    assert [name for name, _value in CALLS] == [
        "mark_running",
        "terminal",
    ]
    assert CALLS[-1][1] == "canceled"
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_cancel_after_successful_child_keeps_actual_result_canceled() -> None:
    """Cancellation during parent capture keeps the real child candidate.

    The child already computed, so its outputs and evidence are preserved
    for the finalization owner, but the turn stays canceled: preservation
    never upgrades the outcome.
    """

    global WORKFLOW_BOUNDARY
    global WORKFLOW_BOUNDARY_REACHED
    global WORKFLOW_BOUNDARY_RELEASE

    CALLS.clear()
    WORKFLOW_BOUNDARY = ("parent_capture", "before")
    WORKFLOW_BOUNDARY_REACHED = asyncio.Event()
    WORKFLOW_BOUNDARY_RELEASE = asyncio.Event()
    try:
        result, history = await _run(
            "success", cancel=True, cancel_ready=WORKFLOW_BOUNDARY_REACHED
        )
    finally:
        WORKFLOW_BOUNDARY_RELEASE.set()
        WORKFLOW_BOUNDARY = None
        WORKFLOW_BOUNDARY_REACHED = None
        WORKFLOW_BOUNDARY_RELEASE = None

    assert result is None
    terminal = TERMINAL_PAYLOADS[-1]
    assert terminal["outcome"] == "canceled"
    agent_result = terminal["agentResult"]
    assert agent_result["failureClass"] == "canceled"
    assert agent_result["outputRefs"] == ["artifact://output/branch-result"]
    assert agent_result["diagnosticsRef"] == "artifact://diagnostics/runtime"
    assert agent_result["metadata"]["omnigentCheckpointCapture"]["terminalRef"] == (
        "artifact://terminal/fresh-session"
    )
    assert terminal["saveCommit"] == {
        "status": "incomplete",
        "reason": "canceled-before-save",
        "orphanAction": "reconcile-with-finalization-owner",
    }
    assert "checkpoint" not in [name for name, _value in CALLS]
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


def _terminal_activity_environment() -> ActivityEnvironment:
    """Use the actual workflow owner persisted by the terminal launch fixture."""
    env = ActivityEnvironment()
    env.info = replace(
        env.info,
        namespace="default",
        workflow_namespace="default",
        workflow_id="checkpoint-branch-turn:turn-1",
        workflow_run_id="7da1c334-3430-4625-8a8a-9f9aa87f6975",
        workflow_type="MoonMind.CheckpointBranchTurn",
    )
    return env


async def _terminal_activity_database(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/terminal.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")

    def _artifact_service(session: AsyncSession) -> TemporalArtifactService:
        return TemporalArtifactService(
            get_temporal_artifact_repository(session), store=store
        )

    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.checkpoint_branch_turn.async_session_maker",
        sessions,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.checkpoint_branch_turn."
        "get_checkpoint_branch_artifact_service",
        _artifact_service,
    )

    async with sessions() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="source-workflow",
                run_id="source-run",
                namespace="default",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        await CheckpointBranchService(session).create_branch_graph(
            {
                "branchId": "branch-1",
                "label": "Retry-safe terminal activity",
                "branchTurnId": "turn-1",
                "source": {
                    "workflowId": "source-workflow",
                    "runId": "source-run",
                    "logicalStepId": "implement",
                    "sourceExecutionOrdinal": 1,
                    "checkpointBoundary": "after_execution",
                    "checkpointRef": "artifact://source/checkpoint",
                    "checkpointDigest": "sha256:" + "a" * 64,
                },
                "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
                "runtimeContextPolicy": "fresh_agent_run",
                "instructionRef": "artifact://source/instruction",
                "instructionDigest": "sha256:" + "b" * 64,
                "idempotencyKey": "create-turn-1",
            }
        )
        await CheckpointBranchService(session).claim_turn_execution(
            workflow_id="source-workflow",
            branch_id="branch-1",
            branch_turn_id="turn-1",
            context_bundle_ref="artifact://launch/context",
            step_execution_manifest_ref="artifact://launch/manifest",
            diagnostics_ref="artifact://launch/diagnostics",
            launch_idempotency_key=build_branch_turn_launch_idempotency_key(
                workflow_id="source-workflow",
                branch_id="branch-1",
                branch_turn_id="turn-1",
            ),
            created_step_execution_id="branch-owner:run:implement:execution:1",
            runtime_agent_run_id="agent-run-turn-1",
            agent_request_ref="artifact://launch/request",
            execution_workflow_id="checkpoint-branch-turn:turn-1",
        )
        await session.commit()
        artifacts = _artifact_service(session)

        async def _seed(
            kind: str,
            body: bytes | None = None,
            *,
            content_type: str = "text/plain",
        ) -> str:
            body = body if body is not None else f"durable-{kind}".encode()
            artifact, _upload = await artifacts.create(
                principal="service:test",
                content_type=content_type,
                size_bytes=len(body),
                sha256=None,
                retention_class=TemporalArtifactRetentionClass.EPHEMERAL,
                link=ExecutionRef(
                    namespace="default",
                    workflow_id="checkpoint-branch-turn:turn-1",
                    run_id="branch-turn-turn-1",
                    link_type=f"output.integration.{kind}",
                ),
                metadata_json={"kind": f"seed.{kind}"},
            )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal="service:test",
                payload=body,
                content_type=content_type,
            )
            return f"artifact://{artifact.artifact_id}"

        external_ref = await _seed("checkpoint-external-state")
        head_ref = await _seed("checkpoint-head")
        workspace_ref = await _seed("checkpoint-workspace")
        instruction_ref = await _seed("checkpoint-instruction")
        checkpoint_model = StepExecutionCheckpointModel(
            checkpointId=(
                "checkpoint-branch-turn:turn-1:branch-turn-turn-1:implement:"
                "execution:1:checkpoint:after_execution"
            ),
            boundary="after_execution",
            source={
                "workflowId": "checkpoint-branch-turn:turn-1",
                "runId": "branch-turn-turn-1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            taskInputSnapshotRef=instruction_ref,
            planDigest="sha256:" + "d" * 64,
            workspace={"kind": "git_commit", "headCommit": "def456"},
            omnigentCheckpoint={
                "workflowId": "checkpoint-branch-turn:turn-1",
                "runId": "branch-turn-turn-1",
                "logicalStepId": "implement",
                "stepExecutionId": "branch-owner:run:implement:execution:1",
                "attemptOrdinal": 1,
                "boundary": "after_execution",
                "providerProfileId": "profile-1",
                "credentialRef": "credential://profile-1",
                "credentialGeneration": 1,
                "hostBindingRef": "omnigent-oauth:profile-1",
                "endpointRef": "default",
                "bridgeSessionId": "fresh-bridge-turn-1",
                "externalStateRef": external_ref,
                "externalStateDigest": "sha256:" + "e" * 64,
                "idempotencyKey": "branch-turn-terminal-turn-1",
                "executionProfileRef": "profile-1",
                "launchPolicyRef": "policy-1@1",
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "branch-workspace-turn-1",
                    "relativePath": "repo",
                },
                "baselineCommit": "abc123",
                "headCommit": "def456",
                "headRef": head_ref,
                "headDigest": "sha256:" + "f" * 64,
                "workspaceCheckpointRef": workspace_ref,
                "workspaceCheckpointDigest": "sha256:" + "0" * 64,
                "sourceBranch": "main",
                "publicationState": "none",
                "capturedAt": "2026-08-12T00:00:00Z",
                "producerVersion": "moonmind-test",
                "validation": {
                    "valid": True,
                    "liveReattachAvailable": False,
                    "workspaceColdRestoreAvailable": True,
                    "branchCreationAvailable": True,
                },
            },
            createdAt="2026-08-12T00:00:00Z",
        )
        checkpoint_ref = await _seed(
            "terminal-checkpoint",
            checkpoint_model.model_dump_json(
                by_alias=True, exclude_none=True
            ).encode(),
            content_type="application/json",
        )
        refs = {
            "external": external_ref,
            "head": head_ref,
            "workspace": workspace_ref,
            "instruction": instruction_ref,
            "output": await _seed("output"),
            "diagnostics": await _seed("diagnostics"),
            "terminal": await _seed("terminal"),
            "capture": await _seed("capture-manifest"),
            "cleanup": await _seed("resource-manifest"),
            "publication": await _seed("publication"),
            "checkpoint": checkpoint_ref,
        }
    return engine, sessions, refs


async def test_preclaim_artifact_retry_reuses_exact_owned_ref(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, _refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    async with sessions() as session:
        owner = CheckpointBranchTurnExecutionOwner(
            session,
            principal="service:test",
            access=access_for_test("service:test"),
        )
        first = await owner._write_artifact(
            content_type="application/json",
            payload=b'{"stable":true}',
            kind="runtime.branch_turn.context_bundle.json",
            branch_turn_id="turn-1",
        )
        replay = await owner._write_artifact(
            content_type="application/json",
            payload=b'{"stable":true}',
            kind="runtime.branch_turn.context_bundle.json",
            branch_turn_id="turn-1",
        )
        with pytest.raises(CheckpointBranchTurnLaunchError):
            await owner._write_artifact(
                content_type="application/json",
                payload=b'{"stable":false}',
                kind="runtime.branch_turn.context_bundle.json",
                branch_turn_id="turn-1",
            )

    assert replay == first
    async with sessions() as session:
        owned_count = await session.scalar(
            select(func.count())
            .select_from(TemporalArtifact)
            .where(
                TemporalArtifact.created_by_principal == "service:test",
                TemporalArtifact.metadata_json["kind"].as_string()
                == "runtime.branch_turn.context_bundle.json",
                TemporalArtifact.metadata_json["branchTurnId"].as_string()
                == "turn-1",
            )
        )
    assert owned_count == 1
    await engine.dispose()


async def test_terminal_activity_retry_reuses_exact_owned_artifacts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    payload = {
        "workflowId": "source-workflow",
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "principal": "service:test",
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "outcome": "failed",
        "agentResult": {
            "outputRefs": [refs["output"]],
            "diagnosticsRef": refs["diagnostics"],
            "summary": "provider failed safely",
            "failureClass": "execution_error",
            "providerErrorCode": "provider_terminal_failed",
            "metadata": {
                "omnigentCheckpointCapture": {
                    "omnigentSessionId": "fresh-session-turn-1",
                    "terminalRef": refs["terminal"],
                },
                "authorityChain": {
                    "schemaVersion": "omnigent-authority-chain-v1",
                    "terminal": {
                        "cleanupCompleted": True,
                        "leaseReleased": True,
                        "janitorRequired": False,
                    },
                },
            },
        },
        "checkpoint": {},
    }

    first = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal, payload
    )
    replay = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal, payload
    )

    assert replay == first
    assert first["terminalDisposition"] == "provider_failure"
    async with sessions() as session:
        owned_count = await session.scalar(
            select(func.count())
            .select_from(TemporalArtifact)
            .where(
                TemporalArtifact.created_by_principal == "service:test",
                TemporalArtifact.metadata_json["branchTurnId"].as_string()
                == "turn-1",
            )
        )
    assert owned_count == 2
    await engine.dispose()


async def test_terminal_activity_persists_successful_verification_handoff(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    payload = {
        "workflowId": "source-workflow",
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "principal": "service:test",
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "outcome": "succeeded",
        "agentResult": {
            "outputRefs": [refs["output"]],
            "diagnosticsRef": refs["diagnostics"],
            "summary": "branch result delivered for verification",
            "metadata": {
                "omnigentCheckpointCapture": {
                    "omnigentSessionId": "fresh-session-turn-1",
                    "terminalRef": refs["terminal"],
                },
                "authorityChain": {
                    "schemaVersion": "omnigent-authority-chain-v1",
                    "terminal": {
                        "cleanupCompleted": True,
                        "leaseReleased": True,
                        "janitorRequired": False,
                        "releaseOrdering": "release_last",
                    },
                },
            },
        },
        "checkpoint": {"checkpointRef": refs["checkpoint"]},
    }

    first = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal, payload
    )
    replay = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal, payload
    )

    assert replay == first
    assert first["status"] == "checking"
    assert first["terminalDisposition"] == "verification_pending"
    assert first["verificationPending"] is True
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        branch = await session.get(WorkflowCheckpointBranch, "branch-1")
        assert turn is not None and turn.provider_session_id == "fresh-session-turn-1"
        assert branch is not None
        assert branch.current_head_checkpoint_ref == refs["checkpoint"]
        assert branch.current_head_version == 1
    await engine.dispose()


async def test_terminal_activity_keeps_child_saved_candidate_on_parent_capture_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child's own saved checkpoint survives a failed parent capture.

    The turn terminalizes failed with save-incomplete evidence, and the
    retained agent result still references the child's verified saved
    candidate so the finalization owner can restore it without a rerun.
    """

    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    saved = {
        "kind": "worktree_archive",
        "baseCommit": "def456",
        "archiveRef": refs["workspace"],
        "archiveDigest": "sha256:" + "1" * 64,
        "manifestRef": refs["head"],
        "manifestDigest": "sha256:" + "2" * 64,
        "checkpointRef": refs["checkpoint"],
        "recoveryEvidence": {"reasonCode": "repository_unavailable"},
    }
    payload = {
        "workflowId": "source-workflow",
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "principal": "service:test",
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "outcome": "failed",
        "agentResult": {
            "outputRefs": [refs["output"]],
            "diagnosticsRef": refs["diagnostics"],
            "summary": "child computed before the parent capture failed",
            "metadata": {
                "savedWorkspaceCheckpoint": saved,
                "workPreserved": True,
            },
        },
        "checkpoint": {},
        "saveCommit": {
            "status": "incomplete",
            "reason": "workspace-capture-failed",
            "orphanAction": "reconcile-with-finalization-owner",
        },
    }

    result = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal, payload
    )

    assert result["status"] == "failed"
    assert result["checkpointRef"] is None
    assert result["saveCommit"]["status"] == "incomplete"
    async with sessions() as session:
        artifacts = TemporalArtifactService(
            get_temporal_artifact_repository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        _artifact, body = await artifacts.read(
            artifact_id=result["agentResultRef"].removeprefix("artifact://"),
            access=access_for_test(
                "service:checkpoint-branch-turn",
                admitted_principal="workflow:source-workflow",
            ),
        )
        stored = json.loads(body)
        assert "failureClass" not in stored
        candidate = stored["metadata"]["savedWorkspaceCheckpoint"]
        assert stored["metadata"]["workPreserved"] is True
        assert candidate == {
            key: saved[key]
            for key in (
                "kind",
                "baseCommit",
                "archiveRef",
                "archiveDigest",
                "manifestRef",
                "manifestDigest",
                "checkpointRef",
            )
        }
        branch = await session.get(WorkflowCheckpointBranch, "branch-1")
        assert branch is not None
        assert branch.current_head_checkpoint_ref == "artifact://source/checkpoint"
    await engine.dispose()


async def test_terminal_activity_retains_saved_manifest_dependencies(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restoring a saved candidate needs the artifacts its manifest names.

    The saved manifest embeds the Git history bundle and staged index
    patch. Both must be retained with the candidate and projected beside
    it, so they cannot expire while the top-level refs stay pinned.
    """

    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")

    async with sessions() as session:
        artifacts = TemporalArtifactService(
            get_temporal_artifact_repository(session), store=store
        )

        async def _seed(body: bytes, content_type: str) -> str:
            artifact, _upload = await artifacts.create(
                principal="service:test",
                content_type=content_type,
                size_bytes=len(body),
                sha256=None,
                retention_class=TemporalArtifactRetentionClass.EPHEMERAL,
                link=ExecutionRef(
                    namespace="default",
                    workflow_id="checkpoint-branch-turn:turn-1",
                    run_id="branch-turn-turn-1",
                    link_type="output.integration.saved-dependency",
                ),
                metadata_json={"kind": "seed.saved-dependency"},
            )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal="service:test",
                payload=body,
                content_type=content_type,
            )
            return f"artifact://{artifact.artifact_id}"

        history_ref = await _seed(b"bundle", "application/x-git-bundle")
        index_ref = await _seed(
            b"index", "application/vnd.moonmind.git-index-patch"
        )
        manifest_ref = await _seed(
            json.dumps(
                {
                    "schemaVersion": "v1",
                    "kind": "worktree_archive",
                    "archiveRef": refs["workspace"],
                    "gitHistory": {"ref": history_ref, "headCommit": "def456"},
                    "git": {"indexPatch": {"ref": index_ref, "paths": ["a.py"]}},
                }
            ).encode(),
            "application/json",
        )
        await session.commit()

    saved = {
        "kind": "worktree_archive",
        "baseCommit": "def456",
        "archiveRef": refs["workspace"],
        "manifestRef": manifest_ref,
        "checkpointRef": refs["checkpoint"],
    }
    result = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal,
        {
            "workflowId": "source-workflow",
            "branchId": "branch-1",
            "branchTurnId": "turn-1",
            "principal": "service:test",
            "sourceNamespace": "default",
            "sourceRunId": "source-run",
            "outcome": "failed",
            "agentResult": {
                "summary": "child saved before the parent capture failed",
                "metadata": {"savedWorkspaceCheckpoint": saved},
            },
            "checkpoint": {},
            "saveCommit": {
                "status": "incomplete",
                "reason": "workspace-capture-failed",
                "orphanAction": "reconcile-with-finalization-owner",
            },
        }
    )

    async with sessions() as session:
        artifacts = TemporalArtifactService(
            get_temporal_artifact_repository(session), store=store
        )
        _artifact, body = await artifacts.read(
            artifact_id=result["agentResultRef"].removeprefix("artifact://"),
            access=access_for_test(
                "service:checkpoint-branch-turn",
                admitted_principal="workflow:source-workflow",
            ),
        )
        candidate = json.loads(body)["metadata"]["savedWorkspaceCheckpoint"]
        assert candidate["dependencyRefs"] == [history_ref, index_ref]
        for ref in (manifest_ref, history_ref, index_ref):
            _metadata, _links, pinned, _policy = await artifacts.get_metadata(
                artifact_id=ref.removeprefix("artifact://"),
                access=access_for_test(
                    "service:checkpoint-branch-turn",
                    admitted_principal="workflow:source-workflow",
                ),
            )
            assert pinned, ref
    await engine.dispose()


@pytest.mark.parametrize(
    ("agent_result", "expected_disposition"),
    [
        (
            {
                "failureClass": "system_error",
                "providerErrorCode": "omnigent_embedded_control_delivery_unknown",
            },
            "delivery_unknown",
        ),
        (
            {
                "failureClass": "system_error",
                "providerErrorCode": "checkpoint_resume_unavailable",
            },
            "resume_unavailable",
        ),
        (
            {
                "metadata": {
                    "authorityChain": {
                        "terminal": {
                            "cleanupCompleted": False,
                            "leaseReleased": False,
                            "janitorRequired": True,
                        }
                    }
                }
            },
            "cleanup_failure",
        ),
        ({}, "terminal_checkpoint_missing"),
    ],
)
async def test_terminal_activity_persists_failure_dispositions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    agent_result: dict,
    expected_disposition: str,
) -> None:
    engine, _sessions, _refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    result = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal,
        {
            "workflowId": "source-workflow",
            "branchId": "branch-1",
            "branchTurnId": "turn-1",
            "principal": "service:test",
            "sourceNamespace": "default",
            "sourceRunId": "source-run",
            "outcome": "failed",
            "agentResult": agent_result,
            "checkpoint": {},
        }
    )

    expected_status = (
        "failed"
        if expected_disposition == "terminal_checkpoint_missing"
        else "blocked"
    )
    assert result["status"] == expected_status
    assert result["terminalDisposition"] == expected_disposition
    await engine.dispose()


@pytest.mark.parametrize(
    "unsafe_result",
    [
        {"outputRefs": ["/tmp/provider-result.json"], "failureClass": "system_error"},
        {
            "outputRefs": ["https://provider.example/result"],
            "failureClass": "system_error",
        },
        {
            "failureClass": "system_error",
            "metadata": {"accessToken": "raw-provider-grant"},
        },
        {
            "failureClass": "system_error",
            "summary": "provider leaked /work/runtime/terminal.json",
        },
        {
            "failureClass": "system_error",
            "summary": "provider token=raw-provider-grant",
        },
    ],
)
async def test_terminal_activity_rejects_secret_and_local_path_evidence(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_result: dict,
) -> None:
    engine, _sessions, _refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    with pytest.raises(CheckpointBranchRetainedEvidenceError):
        await _terminal_activity_environment().run(
            persist_checkpoint_branch_turn_terminal,
            {
                "workflowId": "source-workflow",
                "branchId": "branch-1",
                "branchTurnId": "turn-1",
                "principal": "service:test",
                "sourceNamespace": "default",
                "sourceRunId": "source-run",
                "outcome": "failed",
                "agentResult": unsafe_result,
                "checkpoint": {},
            }
        )
    await engine.dispose()


async def test_terminal_activity_rejects_unresolvable_durable_ref(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _sessions, _refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    with pytest.raises(CheckpointBranchRetainedEvidenceError, match="not resolvable"):
        await _terminal_activity_environment().run(
            persist_checkpoint_branch_turn_terminal,
            {
                "workflowId": "source-workflow",
                "branchId": "branch-1",
                "branchTurnId": "turn-1",
                "principal": "service:test",
                "sourceNamespace": "default",
                "sourceRunId": "source-run",
                "outcome": "failed",
                "agentResult": {
                    "outputRefs": ["artifact://missing-terminal-evidence"],
                    "failureClass": "system_error",
                },
                "checkpoint": {},
            }
        )
    await engine.dispose()


async def test_terminal_rejection_activity_is_retry_stable_and_terminalizes_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, _refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    payload = {
        "workflowId": "source-workflow",
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "principal": "service:test",
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "requestedOutcome": "succeeded",
        "terminalPayloadDigest": "sha256:" + "9" * 64,
    }

    first = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal_rejection, payload
    )
    replay = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal_rejection, payload
    )

    assert replay == first
    assert first["status"] == "blocked"
    assert first["terminalDisposition"] == "retained_evidence_rejected"
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        branch = await session.get(WorkflowCheckpointBranch, "branch-1")
        assert turn is not None and turn.status == "blocked"
        assert branch is not None and branch.state == "blocked"
        assert turn.diagnostics["terminalDisposition"] == (
            "retained_evidence_rejected"
        )
        artifacts = TemporalArtifactService(
            get_temporal_artifact_repository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        rejection_id = first["agentResultRef"].removeprefix("artifact://")
        _artifact, body = await artifacts.read(
            artifact_id=rejection_id,
            access=access_for_test(
                "service:checkpoint-branch-turn",
                admitted_principal="workflow:source-workflow",
            ),
        )
        assert b"terminalPayloadDigest" in body
        assert b"unsafe" not in body
    await engine.dispose()


async def test_terminal_handoff_pins_every_retained_ref_across_lifecycle_sweep(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    result = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal,
        {
            "workflowId": "source-workflow",
            "branchId": "branch-1",
            "branchTurnId": "turn-1",
            "principal": "service:test",
            "sourceNamespace": "default",
            "sourceRunId": "source-run",
            "outcome": "succeeded",
            "agentResult": {
                "outputRefs": [refs["output"]],
                "diagnosticsRef": refs["diagnostics"],
                "summary": "durable branch evidence",
                "metadata": {
                    "omnigentCheckpointCapture": {
                        "omnigentSessionId": "fresh-session-turn-1",
                        "externalStateRef": refs["external"],
                        "terminalRef": refs["terminal"],
                        "diagnosticsRef": refs["diagnostics"],
                        "captureManifestRef": refs["capture"],
                        "resourceManifestRef": refs["cleanup"],
                        "headRef": refs["head"],
                        "workspaceCheckpointRef": refs["workspace"],
                    },
                    "authorityChain": {
                        "schemaVersion": "omnigent-authority-chain-v1",
                        "workspace": {"restoreInputRefs": [refs["workspace"]]},
                        "publication": {
                            "declaredOutputRefs": [refs["output"]],
                            "evidenceRefs": {
                                "commitRef": refs["publication"]
                            },
                        },
                        "terminal": {
                            "cleanupCompleted": True,
                            "leaseReleased": True,
                            "janitorRequired": False,
                        },
                    },
                },
            },
            "checkpoint": {"checkpointRef": refs["checkpoint"]},
        }
    )

    retained_refs = {
        *refs.values(),
        result["agentResultRef"],
        result["diagnosticsRef"],
        result["checkpointRef"],
        result["terminalRef"],
    }
    retained_ids = {
        ref.removeprefix("artifact://") for ref in retained_refs if ref
    }
    async with sessions() as session:
        pins = set(
            (
                await session.execute(
                    select(TemporalArtifactPin.artifact_id).where(
                        TemporalArtifactPin.artifact_id.in_(retained_ids)
                    )
                )
            ).scalars()
        )
        links = set(
            (
                await session.execute(
                    select(TemporalArtifactLink.artifact_id).where(
                        TemporalArtifactLink.artifact_id.in_(retained_ids),
                        TemporalArtifactLink.link_type
                        == "checkpoint_branch.retained_evidence",
                    )
                )
            ).scalars()
        )
        assert pins == retained_ids
        assert links == retained_ids

        artifacts = TemporalArtifactService(
            get_temporal_artifact_repository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        swept = await artifacts.sweep_lifecycle(
            principal="service:lifecycle",
            now=datetime.now(UTC) + timedelta(days=3650),
        )
        assert swept.soft_deleted_count == 0
        for artifact_id in retained_ids:
            _artifact, body = await artifacts.read(
                artifact_id=artifact_id,
                access=access_for_test(
                    "service:checkpoint-branch-turn",
                    admitted_principal="workflow:source-workflow",
                ),
            )
            assert body
    await engine.dispose()


async def test_terminal_handoff_promotes_omnigent_refs_before_recording(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, _refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    payloads = {
        "artifact://omnigent/turn/output.txt": b"branch output",
        "artifact://omnigent/turn/diagnostics.json": b'{"ok":true}',
        "artifact://omnigent/turn/terminal.json": b'{"state":"failed"}',
    }

    class _Gateway:
        async def read_bytes(self, ref: str) -> bytes:
            return payloads[ref]

    monkeypatch.setattr(
        "moonmind.omnigent.bridge_artifacts.LocalOmnigentArtifactGateway",
        _Gateway,
    )
    result = await _terminal_activity_environment().run(
        persist_checkpoint_branch_turn_terminal,
        {
            "workflowId": "source-workflow",
            "branchId": "branch-1",
            "branchTurnId": "turn-1",
            "principal": "service:test",
            "sourceNamespace": "default",
            "sourceRunId": "source-run",
            "outcome": "failed",
            "agentResult": {
                "outputRefs": ["artifact://omnigent/turn/output.txt"],
                "diagnosticsRef": "artifact://omnigent/turn/diagnostics.json",
                "summary": "provider failed",
                "failureClass": "execution_error",
                "metadata": {
                    "omnigentCheckpointCapture": {
                        "terminalRef": "artifact://omnigent/turn/terminal.json"
                    }
                },
            },
            "checkpoint": {},
        }
    )

    async with sessions() as session:
        artifacts = TemporalArtifactService(
            get_temporal_artifact_repository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        _artifact, body = await artifacts.read(
            artifact_id=result["agentResultRef"].removeprefix("artifact://"),
            access=access_for_test(
                "service:checkpoint-branch-turn",
                admitted_principal="workflow:source-workflow",
            ),
        )
        stored = json.loads(body)
        retained = [
            *stored["outputRefs"],
            stored["diagnosticsRef"],
            stored["metadata"]["omnigentCheckpointCapture"]["terminalRef"],
        ]
        assert all(ref.startswith("artifact://art_") for ref in retained)
        assert all("artifact://omnigent/" not in ref for ref in retained)
    await engine.dispose()


@pytest.mark.parametrize("terminal", ["canceled", "rejected"])
async def test_pre_cutover_pending_persistence_invocations_keep_real_handlers(
    tmp_path,
    monkeypatch,
    terminal,
):
    """Old workflow-queue payloads still cross the retained persistence owners."""
    from pathlib import Path

    from temporalio.client import WorkflowHistory
    from temporalio.converter import DataConverter

    from moonmind.workflows.temporal.workflow_registry import (
        workflow_fleet_activity_handlers,
    )

    engine, sessions, _refs = await _terminal_activity_database(tmp_path, monkeypatch)
    fixture = (
        Path(__file__).resolve().parents[3]
        / "fixtures/temporal/checkpoint_before_artifacts_fleet"
        / f"{terminal}.json"
    )
    history = WorkflowHistory.from_json(
        f"old-{terminal}", json.loads(fixture.read_text())
    )
    handlers = {
        activity._Definition.must_from_callable(handler).name: handler
        for handler in workflow_fleet_activity_handlers()
    }
    try:
        async with sessions() as session:
            turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
            expected_agent = turn.runtime_agent_run_id
        for event in history.events:
            if not event.HasField("activity_task_scheduled_event_attributes"):
                continue
            command = event.activity_task_scheduled_event_attributes
            name = command.activity_type.name
            if name not in handlers or not name.startswith("checkpoint_branch.turn."):
                continue
            if terminal == "rejected" and name.endswith("persist_terminal"):
                continue  # Invalid evidence is tested by the production rejection owner.
            (payload,) = await DataConverter.default.decode(command.input.payloads)
            if name.endswith("mark_running"):
                # Replay payload values are scenario data. Resolve the current
                # durable row identity through the production branch service.
                payload["agentRunWorkflowId"] = expected_agent
            result = await _terminal_activity_environment().run(handlers[name], payload)
            if name.endswith("persist_terminal_rejection"):
                assert result["status"] == "blocked"
            elif name.endswith("persist_terminal"):
                assert result["status"] == "canceled"
        async with sessions() as session:
            turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
            assert turn.status == ("canceled" if terminal == "canceled" else "blocked")
            assert turn.completed_at is not None
    finally:
        await engine.dispose()
