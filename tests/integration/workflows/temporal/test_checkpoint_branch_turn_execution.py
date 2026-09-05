"""Hermetic Temporal boundary for MoonLadderStudios/MoonMind#3621."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker
from temporalio.workflow import ActivityCancellationType

from api_service.api.routers.executions import _get_service, router
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentPolicy,
    OmnigentPolicyVersion,
    ProviderCredentialSource,
    ProviderProfileAuthMethod,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactPin,
    TemporalArtifactRetentionClass,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
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
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.omnigent_session_models import OmnigentSessionWorkflowInput
from moonmind.schemas.temporal_models import StepExecutionCheckpointModel
from moonmind.workflows import get_temporal_artifact_repository
from moonmind.workflows.temporal.activities import omnigent_activities
from moonmind.workflows.temporal.activities.omnigent_activities import (
    omnigent_profile_bound_execute_activity,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    ARTIFACTS_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
    get_workflow_task_queue,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    CheckpointBranchRetainedEvidenceError,
    MoonMindCheckpointBranchTurnWorkflow,
    mark_checkpoint_branch_turn_running,
    persist_checkpoint_branch_turn_terminal,
    persist_checkpoint_branch_turn_terminal_rejection,
)
from tests.helpers.checkpoint_branch_turn_runtime import (
    CheckpointBranchRuntimeLedger,
    checkpoint_branch_policy_snapshot,
    execute_checkpoint_branch_request,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

CALLS: list[tuple[str, object]] = []
TRANSIENT_FAILURES: dict[str, int] = {}
EFFECT_IDENTITIES: dict[str, set[str]] = {}
WORKFLOW_BOUNDARY: tuple[str, str] | None = None
WORKFLOW_BOUNDARY_REACHED: asyncio.Event | None = None
WORKFLOW_BOUNDARY_RELEASE: asyncio.Event | None = None
WORKFLOW_FAILURE_BOUNDARY: tuple[str, str] | None = None
DURABLE_CHECKPOINT_REF: str | None = None
CHECKPOINT_PAYLOADS: list[dict] = []
REAL_AGENT_REQUESTS: dict[str, AgentExecutionRequest] = {}


async def _pause_workflow_boundary(stage: str, position: str) -> None:
    """Hold a real Activity call until Temporal cancellation reaches it."""

    if WORKFLOW_BOUNDARY != (stage, position):
        return
    assert WORKFLOW_BOUNDARY_REACHED is not None
    assert WORKFLOW_BOUNDARY_RELEASE is not None
    WORKFLOW_BOUNDARY_REACHED.set()
    await WORKFLOW_BOUNDARY_RELEASE.wait()


def _inject_workflow_boundary_failure(stage: str, position: str) -> None:
    if WORKFLOW_FAILURE_BOUNDARY == (stage, position):
        raise RuntimeError(f"injected worker failure {position} {stage}")


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


@activity.defn(name="integration.resolve_adapter_metadata")
async def _resolve_real_agent_adapter(agent_id: str) -> dict:
    CALLS.append(("resolve_adapter", agent_id))
    return {
        "agent_id": "omnigent",
        "execution_style": "streaming_gateway",
        "supports_callbacks": False,
    }


@activity.defn(name="agent_runtime.publish_artifacts")
async def _publish_real_agent_result(
    result: AgentRunResult | None = None,
) -> AgentRunResult | None:
    CALLS.append(("publish_artifacts", bool(result)))
    return result


@activity.defn(name="omnigent.resolve_intent")
async def _resolve_omnigent_session_intent(payload: dict) -> dict:
    """Test the compact AgentRun-to-session handoff while reusing its ledger."""

    request = AgentExecutionRequest.model_validate(payload["request"])
    authority = ":".join(
        (
            str(payload["workflowId"]),
            str(payload["stepExecutionId"]),
            str(payload["agentRunId"]),
        )
    )
    digest = hashlib.sha256(authority.encode()).hexdigest()
    session_id = f"oms_{digest[:40]}"
    REAL_AGENT_REQUESTS[session_id] = request
    CALLS.append(("resolve_intent", session_id))
    return OmnigentSessionWorkflowInput(
        sessionId=session_id,
        compiledExecutionIntentRef=f"art_intent_{digest[:24]}",
        compiledExecutionIntentDigest="sha256:" + digest,
        workflowId=str(payload["workflowId"]),
        stepExecutionId=str(payload["stepExecutionId"]),
        agentRunId=str(payload["agentRunId"]),
        initialTurnAttemptId=f"ota_{digest[:40]}",
        admittedFeatureGeneration=str(payload["admittedFeatureGeneration"]),
        compatibilityVersion="v1",
    ).model_dump(mode="json", by_alias=True)


@activity.defn(name="omnigent.evaluate_session_admission")
async def _admit_omnigent_session(_payload: dict) -> dict:
    return {
        "admitted": True,
        "reasonCode": "enabled",
        "admissionMode": "enabled",
        "admittedFeatureGeneration": "omnigent-session-v1",
    }


@activity.defn(name="test.omnigent.session_execute")
async def _execute_fake_omnigent_session(payload: dict) -> AgentRunResult:
    request = REAL_AGENT_REQUESTS[str(payload["sessionId"])]
    return await omnigent_activities._omnigent_execute_activity(request)


@workflow.defn(name="MoonMind.OmnigentSession")
class _FakeOmnigentSession:
    def __init__(self) -> None:
        self._activity_handle = None

    @workflow.run
    async def run(
        self, session_input: OmnigentSessionWorkflowInput
    ) -> AgentRunResult:
        self._activity_handle = workflow.start_activity(
            "test.omnigent.session_execute",
            session_input.model_dump(mode="json", by_alias=True),
            task_queue=AGENT_RUNTIME_TASK_QUEUE,
            start_to_close_timeout=timedelta(hours=1),
            heartbeat_timeout=timedelta(seconds=120),
            # Mirror the bounded retry budget the real provider-execution
            # boundary carries (`integration.omnigent.profile_bound_execute`).
            # Without it this stand-in inherits Temporal's unlimited default and
            # an always-failing handoff never exhausts its retries, so the
            # worker-failure matrix can never reach a durable terminal.
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                maximum_interval=timedelta(seconds=1),
            ),
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        return await self._activity_handle

    @workflow.signal(name="cancel_or_interrupt_requested")
    def cancel(self, _payload: dict) -> None:
        if self._activity_handle is not None:
            self._activity_handle.cancel()


@activity.defn(name="checkpoint_branch.turn.mark_running")
async def _mark_running(payload: dict) -> None:
    CALLS.append(("mark_running", payload["agentRunWorkflowId"]))
    _record_effect_and_inject("mark_running", payload["agentRunWorkflowId"])


@activity.defn(name="checkpoint_branch.turn.mark_running")
async def _durable_mark_running(payload: dict) -> None:
    await _pause_workflow_boundary("step_execution_allocation", "before")
    _inject_workflow_boundary_failure("step_execution_allocation", "before")
    await mark_checkpoint_branch_turn_running(payload)
    CALLS.append(("mark_running", payload["agentRunWorkflowId"]))
    _inject_workflow_boundary_failure("step_execution_allocation", "after")
    await _pause_workflow_boundary("step_execution_allocation", "after")


@activity.defn(name="workspace.capture_checkpoint")
async def _capture_workspace(payload: dict) -> dict:
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


@activity.defn(name="workspace.capture_checkpoint")
async def _durable_capture_workspace(payload: dict) -> dict:
    await _pause_workflow_boundary("checkpoint_capture", "before")
    _inject_workflow_boundary_failure("checkpoint_capture", "before")
    result = await _capture_workspace(payload)
    _inject_workflow_boundary_failure("checkpoint_capture", "after")
    await _pause_workflow_boundary("checkpoint_capture", "after")
    return result


@activity.defn(name="step_checkpoint.create_v2")
async def _create_checkpoint(payload: dict) -> dict:
    CHECKPOINT_PAYLOADS.append(copy.deepcopy(payload))
    CALLS.append(("checkpoint", payload["idempotencyKey"]))
    _record_effect_and_inject("checkpoint", payload["idempotencyKey"])
    return {
        "checkpointRef": "artifact://checkpoint/branch-turn-result",
        "idempotencyKey": payload["idempotencyKey"],
    }


@activity.defn(name="step_checkpoint.create_v2")
async def _durable_create_checkpoint(payload: dict) -> dict:
    result = await _create_checkpoint(payload)
    assert DURABLE_CHECKPOINT_REF is not None
    return {**result, "checkpointRef": DURABLE_CHECKPOINT_REF}


@activity.defn(name="checkpoint_branch.turn.persist_terminal")
async def _persist_terminal(payload: dict) -> dict:
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


@activity.defn(name="checkpoint_branch.turn.persist_terminal")
async def _durable_persist_terminal(payload: dict) -> dict:
    result = await persist_checkpoint_branch_turn_terminal(payload)
    CALLS.append(("terminal", payload["outcome"]))
    return result


@activity.defn(name="checkpoint_branch.turn.persist_terminal_rejection")
async def _durable_persist_terminal_rejection(payload: dict) -> dict:
    result = await persist_checkpoint_branch_turn_terminal_rejection(payload)
    CALLS.append(("terminal_rejection", payload["terminalPayloadDigest"]))
    return result


def _request(
    correlation_id: str, *, publish_mode: str = "none"
) -> AgentExecutionRequest:
    policy = checkpoint_branch_policy_snapshot()
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
    durable_terminal: bool = False,
    publish_mode: str = "none",
    real_agent_run: bool = False,
    transient_failures: dict[str, int] | None = None,
):
    TRANSIENT_FAILURES.clear()
    TRANSIENT_FAILURES.update(transient_failures or {})
    EFFECT_IDENTITIES.clear()
    CHECKPOINT_PAYLOADS.clear()
    REAL_AGENT_REQUESTS.clear()
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
                            MoonMindAgentRun if real_agent_run else _FakeAgentRun,
                        ],
                        activities=[
                            (
                                _durable_mark_running
                                if durable_terminal
                                else _mark_running
                            ),
                            (
                                _durable_persist_terminal
                                if durable_terminal
                                else _persist_terminal
                            ),
                            (
                                _durable_persist_terminal_rejection
                                if durable_terminal
                                else _persist_terminal_rejection
                            ),
                        ],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
                if real_agent_run:
                    await stack.enter_async_context(
                        Worker(
                            env.client,
                            task_queue=get_workflow_task_queue(),
                            workflows=[_FakeOmnigentSession],
                            activities=[_resolve_real_agent_adapter],
                            workflow_runner=UnsandboxedWorkflowRunner(),
                        )
                    )
                    await stack.enter_async_context(
                        Worker(
                            env.client,
                            task_queue=AGENT_RUNTIME_TASK_QUEUE,
                            activities=[
                                _admit_omnigent_session,
                                _resolve_omnigent_session_intent,
                                _execute_fake_omnigent_session,
                                omnigent_profile_bound_execute_activity,
                                _publish_real_agent_result,
                            ],
                        )
                    )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=SANDBOX_TASK_QUEUE,
                        activities=[
                            (
                                _durable_capture_workspace
                                if durable_terminal
                                else _capture_workspace
                            )
                        ],
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=ARTIFACTS_TASK_QUEUE,
                        activities=[
                            (
                                _durable_create_checkpoint
                                if durable_terminal
                                else _create_checkpoint
                            )
                        ],
                    )
                )
                handle = await env.client.start_workflow(
                    MoonMindCheckpointBranchTurnWorkflow.run,
                    _input(
                        correlation_id,
                        publish_mode=publish_mode,
                        agent_run_workflow_id=(
                            "agent-run-turn-1" if durable_terminal else None
                        ),
                    ),
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
        REAL_AGENT_REQUESTS.clear()


async def test_checkpoint_branch_turn_uses_real_agent_run_supervisor_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise AgentRun's compact supervisor handoff and execution contract."""

    CALLS.clear()
    ledger = CheckpointBranchRuntimeLedger()

    async def execute_profile_bound(request: AgentExecutionRequest) -> AgentRunResult:
        CALLS.append(("profile_bound_execute", request.execution_profile_ref))
        return await execute_checkpoint_branch_request(request, ledger=ledger)

    monkeypatch.setattr(
        omnigent_activities,
        "_omnigent_execute_activity",
        execute_profile_bound,
    )
    result, _history = await _run("real-agent-run", real_agent_run=True)

    assert result["status"] == "checking"
    assert [name for name, _value in CALLS] == [
        "mark_running",
        "resolve_adapter",
        "resolve_intent",
        "profile_bound_execute",
        "publish_artifacts",
        "capture",
        "checkpoint",
        "terminal",
    ]
    assert len(ledger.requests) == 1
    assert ledger.requests[0].checkpoint_recovery["recoveryAction"] == (
        "branch_required"
    )
    assert ledger.effect_counts["provider_lease"] == 1
    assert ledger.effect_counts["host_lease"] == 1
    assert ledger.effect_counts["provider_session"] == 1
    assert ledger.effect_counts["first_message"] == 1
    assert ledger.effect_counts["cleanup"] == 1
    assert ledger.effect_counts["capacity_release"] == 1


async def test_public_root_continue_and_fork_cross_the_real_execution_owner(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the complete public production boundary with hermetic interfaces."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/public-owner.db")
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = LocalTemporalArtifactStore(tmp_path / "public-owner-artifacts")

    def artifact_service(session: AsyncSession) -> TemporalArtifactService:
        return TemporalArtifactService(
            get_temporal_artifact_repository(session),
            store=store,
        )

    for target in (
        "api_service.api.routers.executions.get_checkpoint_branch_artifact_service",
        "api_service.services.checkpoint_branch_turn_execution."
        "get_checkpoint_branch_artifact_service",
        "moonmind.workflows.temporal.workflows.checkpoint_branch_turn."
        "get_checkpoint_branch_artifact_service",
    ):
        monkeypatch.setattr(target, artifact_service)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.checkpoint_branch_turn."
        "async_session_maker",
        sessions,
    )

    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-owner@example.test",
        is_superuser=True,
        roles=[],
    )
    principal = str(user.id)
    artifact_refs: dict[tuple[str, str], str] = {}
    artifact_digests: dict[str, str] = {}

    async def write_artifact(kind: str, key: str, body: bytes) -> str:
        identity = (kind, key)
        if identity in artifact_refs:
            return artifact_refs[identity]
        async with sessions() as artifact_session:
            artifacts = artifact_service(artifact_session)
            digest = hashlib.sha256(body).hexdigest()
            artifact, _upload = await artifacts.create(
                principal=principal,
                content_type=(
                    "application/json" if kind != "instruction" else "text/markdown"
                ),
                size_bytes=len(body),
                sha256=digest,
                retention_class=TemporalArtifactRetentionClass.LONG,
                metadata_json={
                    "kind": f"integration.checkpoint_branch.{kind}",
                    "identity": key,
                },
            )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal=principal,
                payload=body,
                content_type=(
                    "application/json" if kind != "instruction" else "text/markdown"
                ),
            )
            ref = f"artifact://{artifact.artifact_id}"
            artifact_refs[identity] = ref
            artifact_digests[ref] = f"sha256:{digest}"
            return ref

    policy = checkpoint_branch_policy_snapshot()
    source_external_ref = await write_artifact(
        "source-external", "source", b"source external state"
    )
    source_head_ref = await write_artifact("source-head", "source", b"def456")
    source_workspace_ref = await write_artifact(
        "source-workspace", "source", b"source workspace archive"
    )
    source_instruction_ref = await write_artifact(
        "instruction", "source", b"Original source workflow input"
    )
    source_checkpoint = StepExecutionCheckpointModel(
        checkpointId=(
            "mm:wf-public-owner:run-public-owner:implement:execution:2:"
            "checkpoint:after_execution"
        ),
        boundary="after_execution",
        source={
            "workflowId": "mm:wf-public-owner",
            "runId": "run-public-owner",
            "logicalStepId": "implement",
            "executionOrdinal": 2,
        },
        taskInputSnapshotRef=source_instruction_ref,
        planDigest="sha256:" + "3" * 64,
        workspace={"kind": "git_commit", "headCommit": "def456"},
        omnigentCheckpoint={
            "workflowId": "mm:wf-public-owner",
            "runId": "run-public-owner",
            "logicalStepId": "implement",
            "stepExecutionId": "source-step-execution",
            "attemptOrdinal": 1,
            "boundary": "after_execution",
            "providerProfileId": "profile-1",
            "credentialRef": "credential://profile-1",
            "credentialGeneration": 1,
            "providerLeaseRef": "source-provider-lease",
            "hostBindingRef": "source-host-binding",
            "hostLeaseRef": "source-host-lease",
            "endpointRef": "default",
            "omnigentHostId": "source-host",
            "omnigentSessionId": "source-session",
            "bridgeSessionId": "source-bridge",
            "externalStateRef": source_external_ref,
            "externalStateDigest": artifact_digests[source_external_ref],
            "idempotencyKey": "source-first-message",
            "executionProfileRef": "profile-1",
            "launchPolicyRef": "codex-on-demand@1",
            "policyId": policy["policyId"],
            "policyVersion": policy["policyVersion"],
            "policyRef": policy["policyRef"],
            "policyDigest": policy["policyDigest"],
            "policySnapshotRef": policy["snapshotRef"],
            "policyValidation": policy["validation"],
            "lastBridgeEventCursor": "5",
            "firstMessageId": "source-message",
            "firstMessageDigest": "sha256:" + "4" * 64,
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "source-workspace",
                "relativePath": "repo",
            },
            "baselineCommit": "abc123",
            "headCommit": "def456",
            "headRef": source_head_ref,
            "headDigest": artifact_digests[source_head_ref],
            "workspaceCheckpointRef": source_workspace_ref,
            "workspaceCheckpointDigest": artifact_digests[source_workspace_ref],
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
        createdAt="2026-08-12T00:00:00Z",
    )
    source_checkpoint_bytes = source_checkpoint.model_dump_json(
        by_alias=True, exclude_none=True
    ).encode()
    source_checkpoint_ref = await write_artifact(
        "source-checkpoint", "source", source_checkpoint_bytes
    )
    source_parameters = {
        "repository": {
            "provider": "git",
            "connectionRef": "repository-connection:test",
            "repository": {"name": "MoonLadderStudios/MoonMind"},
            "branch": {"name": "feature/source"},
        },
        "git": {
            "baseCommit": "abc123",
            "resolvedBaseCommit": "abc123",
            "currentRef": "feature/source",
            "knownRefs": ["feature/source"],
        },
        "steps": [
            {
                "logicalStepId": "implement",
                "executionOrdinal": 2,
                "checkpointRefsByBoundary": {
                    "after_execution": {
                        "artifactRef": source_checkpoint_ref,
                        "checkpointDigest": artifact_digests[source_checkpoint_ref],
                    }
                },
                "checkpointRef": source_checkpoint_ref,
                "checkpointDigest": artifact_digests[source_checkpoint_ref],
            }
        ],
        "targetOutcome": {
            "status": "failed",
            "artifactRef": "artifact://source/original-outcome",
        },
    }
    source_memo = {
        "stepCheckpointRef": source_checkpoint_ref,
        "latest_temporal_run_id": "run-public-owner",
        "repository": "MoonLadderStudios/MoonMind",
    }
    now = datetime.now(UTC)
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:wf-public-owner",
        run_id="run-public-owner",
        namespace="default",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=principal,
        owner_type="user",
        state="executing",
        entry="run",
        search_attributes={"mm_owner_id": principal, "mm_owner_type": "user"},
        memo=copy.deepcopy(source_memo),
        parameters=copy.deepcopy(source_parameters),
        artifact_refs=[],
        created_at=now,
        updated_at=now,
    )
    from api_service.api.routers.omnigent_agent_profiles import AgentProfileDocument
    from moonmind.provider_profiles.isolation_policy import derive_isolation_policy

    configuration_document = AgentProfileDocument.model_validate({
        "endpointRef": "default",
        "bridgeMode": "proxy",
        "source": {
            "bundleArtifactRef": "artifact://checkpoint-owner-agent-bundle",
            "bundleDigest": "sha256:" + "b" * 64,
        },
        "harness": "codex-native",
        "requiredCapabilities": ["session.start"],
        "execution": {
            "defaultExecutionProfileRef": "omnigent-codex@1",
            "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
        },
        "providerRequirements": {
            "runtimeId": "codex_cli",
            "providerIds": ["openai"],
            "credentialSource": "oauth_volume",
            "materializationMode": "oauth_home",
        },
        "policyRef": policy["policyRef"],
    }).model_dump(mode="json", by_alias=True, exclude_none=True)
    configuration_digest = "sha256:" + hashlib.sha256(
        json.dumps(configuration_document, sort_keys=True).encode()
    ).hexdigest()
    isolation = derive_isolation_policy(
        runtime_id="codex_cli", provider_id="openai", authentication_method="oauth",
        credential_source="oauth_volume", runtime_materialization_mode="oauth_home",
    )
    assert isolation is not None
    async with sessions() as session:
        session.add_all(
            [
                record,
                OmnigentAgentProfile(
                    profile_id="checkpoint-owner-configuration",
                    display_name="Checkpoint owner execution configuration",
                    visibility="workspace",
                    state="active",
                    active_version=1,
                    default_for_runtime=True,
                ),
                OmnigentAgentProfileVersion(
                    profile_id="checkpoint-owner-configuration",
                    version=1,
                    digest=configuration_digest,
                    document=configuration_document,
                    validation_result={"ready": True},
                    rollout_metadata={"bundleImport": {
                        "status": "succeeded",
                        "upstreamAgent": {"id": "checkpoint-owner-agent"},
                    }},
                ),
                ManagedAgentProviderProfile(
                    profile_id="profile-1",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=1,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                    default_model="gpt-test",
                    default_effort="high",
                    clear_env_keys=list(isolation.keys),
                ),
                OmnigentPolicy(
                    policy_id=policy["policyId"],
                    name="Checkpoint Branch integration policy",
                    visibility="deployment",
                    default_version=1,
                ),
                OmnigentPolicyVersion(
                    policy_id=policy["policyId"],
                    version=1,
                    state="active",
                    document_json=copy.deepcopy(policy["boundaries"]),
                    digest=policy["policyDigest"],
                    created_by="integration-test",
                    activated_by="integration-test",
                    activated_at=now,
                    validation_json=copy.deepcopy(policy["validation"]),
                    compatibility_json={"compatible": True, "diagnostics": []},
                    rollout_json=copy.deepcopy(policy["boundaries"]["rollout"]),
                ),
            ]
        )
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    execution_service = SimpleNamespace(describe_execution=AsyncMock(return_value=record))
    app.dependency_overrides[_get_service] = lambda: execution_service

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = session_override
    user_dependencies = {
        dependency.call
        for route_item in router.routes
        if route_item.dependant is not None
        for dependency in route_item.dependant.dependencies
        if getattr(dependency.call, "__name__", "") == "_current_user_fallback"
    } or {get_current_user()}
    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda: user

    ledger = CheckpointBranchRuntimeLedger(artifact_writer=write_artifact)
    created_checkpoint_ids: set[str] = set()
    created_checkpoint_refs: dict[str, str] = {}

    async def execute_profile_bound(request: AgentExecutionRequest) -> AgentRunResult:
        return await execute_checkpoint_branch_request(
            request,
            ledger=ledger,
            policy_snapshot=policy,
        )

    monkeypatch.setattr(
        omnigent_activities,
        "_omnigent_execute_activity",
        execute_profile_bound,
    )

    @activity.defn(name="workspace.capture_checkpoint")
    async def capture_public_workspace(payload: dict) -> dict:
        capture_key = str(payload["idempotencyKey"])
        archive_ref = await write_artifact(
            "workspace-capture", capture_key, f"archive:{capture_key}".encode()
        )
        manifest_ref = await write_artifact(
            "workspace-manifest",
            capture_key,
            json.dumps({"archiveRef": archive_ref}, sort_keys=True).encode(),
        )
        return {
            "status": "captured",
            "workspace": {
                "kind": "worktree_archive",
                "baseCommit": payload["baseCommit"],
                "archiveRef": archive_ref,
                "archiveDigest": artifact_digests[archive_ref],
                "manifestRef": manifest_ref,
            },
            "diagnosticRefs": [],
        }

    @activity.defn(name="step_checkpoint.create_v2")
    async def create_public_checkpoint(payload: dict) -> dict:
        checkpoint_key = str(payload["idempotencyKey"])
        created_checkpoint_ids.add(checkpoint_key)
        step_execution_id = checkpoint_key.removesuffix(
            ":checkpoint:after_execution"
        )
        identity = dict(payload["identity"])
        capture = dict(payload["omnigentCheckpointCapture"])
        workspace = dict(payload["workspace"])
        head_commit = hashlib.sha1(step_execution_id.encode()).hexdigest()
        head_ref = await write_artifact(
            "branch-head", checkpoint_key, head_commit.encode()
        )
        checkpoint_model = StepExecutionCheckpointModel(
            checkpointId=f"{step_execution_id}:checkpoint:after_execution",
            boundary="after_execution",
            source=identity,
            taskInputSnapshotRef=payload["taskInputSnapshotRef"],
            planDigest=payload["planDigest"],
            workspace=workspace,
            omnigentCheckpoint={
                "workflowId": identity["workflowId"],
                "runId": identity["runId"],
                "logicalStepId": identity["logicalStepId"],
                "stepExecutionId": step_execution_id,
                "attemptOrdinal": 1,
                "boundary": "after_execution",
                "providerProfileId": capture["providerProfileId"],
                "credentialRef": capture["credentialRef"],
                "credentialGeneration": capture["credentialGeneration"],
                "providerLeaseRef": capture["providerLeaseRef"],
                "hostBindingRef": capture["hostBindingRef"],
                "hostLeaseRef": capture["hostLeaseRef"],
                "endpointRef": capture["endpointRef"],
                "omnigentHostId": capture["omnigentHostId"],
                "omnigentSessionId": capture["omnigentSessionId"],
                "bridgeSessionId": capture["bridgeSessionId"],
                "externalStateRef": capture["externalStateRef"],
                "externalStateDigest": artifact_digests[
                    capture["externalStateRef"]
                ],
                "idempotencyKey": capture["idempotencyKey"],
                "terminalRef": capture.get("terminalRef"),
                "diagnosticsRef": capture.get("diagnosticsRef"),
                "effectiveLaunchRef": capture["effectiveLaunchRef"],
                "executionProfileRef": "profile-1",
                "launchPolicyRef": capture["launchPolicyRef"],
                "policyId": capture["policyId"],
                "policyVersion": capture["policyVersion"],
                "policyRef": capture["policyRef"],
                "policyDigest": capture["policyDigest"],
                "policySnapshotRef": capture["policySnapshotRef"],
                "policyValidation": capture["policyValidation"],
                "workspaceLocator": capture["workspaceLocator"],
                "baselineCommit": workspace["baseCommit"],
                "headCommit": head_commit,
                "headRef": head_ref,
                "headDigest": artifact_digests[head_ref],
                "workspaceCheckpointRef": workspace["archiveRef"],
                "workspaceCheckpointDigest": workspace["archiveDigest"],
                "instructionRefs": capture["instructionRefs"],
                "sourceBranch": "main",
                "outputBranch": "checkpoint-branch-output",
                "publicationState": "branch",
                "capturedAt": "2026-08-12T00:00:00Z",
                "producerVersion": "moonmind-integration-test",
                "validation": {
                    "valid": True,
                    "liveReattachAvailable": False,
                    "workspaceColdRestoreAvailable": True,
                    "branchCreationAvailable": True,
                },
            },
            createdAt="2026-08-12T00:00:00Z",
        )
        checkpoint_ref = await write_artifact(
            "branch-checkpoint",
            checkpoint_key,
            checkpoint_model.model_dump_json(
                by_alias=True, exclude_none=True
            ).encode(),
        )
        created_checkpoint_refs[step_execution_id] = checkpoint_ref
        return {"checkpointRef": checkpoint_ref, "idempotencyKey": checkpoint_key}

    async def seed_mismatched_launch(case: str) -> tuple[str, str]:
        """Persist one launch whose named authority fails before dispatch."""

        branch_id = f"cbr-public-mismatch-{case}"
        turn_id = f"cbt-public-mismatch-{case}"
        instruction_body = f"Reject changed {case} authority.".encode()
        instruction_ref = await write_artifact(
            "instruction", f"mismatch-{case}", instruction_body
        )
        async with sessions() as mismatch_session:
            service = CheckpointBranchService(mismatch_session)
            await service.create_branch_graph(
                {
                    "branchId": branch_id,
                    "branchTurnId": turn_id,
                    "source": {
                        "workflowId": "mm:wf-public-owner",
                        "runId": "run-public-owner",
                        "logicalStepId": "implement",
                        "sourceExecutionOrdinal": 2,
                        "checkpointBoundary": "after_execution",
                        "checkpointRef": source_checkpoint_ref,
                        "checkpointDigest": artifact_digests[
                            source_checkpoint_ref
                        ],
                    },
                    "label": f"Public mismatch: {case}",
                    "workspacePolicy": (
                        "apply_previous_execution_diff_to_clean_baseline"
                    ),
                    "runtimeContextPolicy": "fresh_agent_run",
                    "instructionRef": instruction_ref,
                    "instructionDigest": "sha256:"
                    + hashlib.sha256(instruction_body).hexdigest(),
                    "idempotencyKey": f"public-mismatch-graph-{case}",
                }
            )
            await service.configure_server_launch_authority(
                workflow_id="mm:wf-public-owner",
                branch_id=branch_id,
                branch_turn_id=turn_id,
                repository="MoonLadderStudios/MoonMind",
                base_branch="main",
                base_commit="abc123",
                work_branch=f"feature/checkpoint-mismatch-{case}",
                provider_profile_ref="profile-1",
            )
            branch = await mismatch_session.get(
                WorkflowCheckpointBranch, branch_id
            )
            turn = await mismatch_session.get(
                WorkflowCheckpointBranchTurn, turn_id
            )
            assert branch is not None
            assert turn is not None
            branch.diagnostics = {
                **(branch.diagnostics or {}),
                "runtimeSelection": {
                    **dict(
                        (branch.diagnostics or {}).get("runtimeSelection") or {}
                    ),
                    "executionProfileRef": "profile-1",
                    "launchPolicyRef": "codex-on-demand@1",
                    "runtimeId": "codex_cli",
                    "model": "gpt-test",
                    "effort": "high",
                },
            }
            if case == "workflow":
                branch.root_workflow_id = "mm:wf-other"
            elif case == "run":
                branch.source_run_id = "run-other"
            elif case == "step":
                branch.logical_step_id = "other-step"
            elif case == "checkpoint":
                branch.current_head_checkpoint_ref = "artifact://changed/head"
            elif case == "digest":
                turn.source_checkpoint_digest = "sha256:" + "9" * 64
            elif case == "instruction":
                turn.instruction_digest = "sha256:" + "8" * 64
            elif case == "profile":
                branch.diagnostics["runtimeSelection"][
                    "providerProfileRef"
                ] = "profile-other"
            elif case == "policy":
                branch.diagnostics["runtimeSelection"][
                    "launchPolicyRef"
                ] = "policy-other@1"
            elif case not in {"head", "credential"}:
                raise AssertionError(f"unsupported mismatch case {case}")
            await mismatch_session.commit()
        return branch_id, turn_id

    mismatch_codes = {
        "workflow": "root_workflow_mismatch",
        "run": "source_run_stale",
        "step": "checkpoint_lineage_mismatch",
        "checkpoint": "branch_head_checkpoint_changed",
        "digest": "checkpoint_digest_mismatch",
        "instruction": "instruction_digest_mismatch",
        "head": "branch_head_stale",
        "profile": "provider_profile_mismatch",
        "policy": "launch_policy_mismatch",
        "credential": "credential_generation_changed",
    }
    mismatched_launches = {
        case: await seed_mismatched_launch(case) for case in mismatch_codes
    }

    owner_queue = f"checkpoint-branch-public-owner-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async def get_test_client(_adapter):
            return env.client

        monkeypatch.setattr(TemporalClientAdapter, "get_client", get_test_client)
        monkeypatch.setattr(
            TemporalClientAdapter,
            "_get_task_queue",
            lambda _adapter, _workflow_type=None, *, task_queue=None: (
                task_queue or owner_queue
            ),
        )
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=owner_queue,
                    workflows=[
                        MoonMindCheckpointBranchTurnWorkflow,
                        MoonMindAgentRun,
                    ],
                    activities=[
                        mark_checkpoint_branch_turn_running,
                        persist_checkpoint_branch_turn_terminal,
                        persist_checkpoint_branch_turn_terminal_rejection,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=get_workflow_task_queue(),
                    workflows=[_FakeOmnigentSession],
                    activities=[_resolve_real_agent_adapter],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=AGENT_RUNTIME_TASK_QUEUE,
                    activities=[
                        _admit_omnigent_session,
                        _resolve_omnigent_session_intent,
                        _execute_fake_omnigent_session,
                        omnigent_profile_bound_execute_activity,
                        _publish_real_agent_result,
                    ],
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[capture_public_workspace],
                )
            )
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[create_public_checkpoint],
                )
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                for case, expected_code in mismatch_codes.items():
                    if case == "credential":
                        async with sessions() as mismatch_session:
                            profile_row = await mismatch_session.get(
                                ManagedAgentProviderProfile, "profile-1"
                            )
                            assert profile_row is not None
                            profile_row.credential_generation = 2
                            await mismatch_session.commit()
                    branch_id, turn_id = mismatched_launches[case]
                    launch_intent: dict[str, object] = {
                        "idempotencyKey": f"public-mismatch-launch-{case}"
                    }
                    if case == "head":
                        launch_intent["expectedBranchHeadVersion"] = 1
                    launch = await client.post(
                        "/api/executions/mm:wf-public-owner/checkpoint-branches/"
                        f"{branch_id}/turns/{turn_id}/launch",
                        json=launch_intent,
                    )
                    assert launch.status_code == 409, launch.text
                    assert launch.json()["detail"]["code"] == expected_code
                    assert all(
                        count == 0 for count in ledger.effect_counts.values()
                    )
                    async with sessions() as mismatch_session:
                        rejected_turn = await mismatch_session.get(
                            WorkflowCheckpointBranchTurn, turn_id
                        )
                        assert rejected_turn is not None
                        assert rejected_turn.created_step_execution_id is None
                        assert rejected_turn.runtime_agent_run_id is None
                        if case == "credential":
                            profile_row = await mismatch_session.get(
                                ManagedAgentProviderProfile, "profile-1"
                            )
                            assert profile_row is not None
                            profile_row.credential_generation = 1
                            await mismatch_session.commit()

                root_payload = {
                    "source": {
                        "runId": "run-public-owner",
                        "logicalStepId": "implement",
                        "executionOrdinal": 2,
                        "checkpointBoundary": "after_execution",
                        "checkpointRef": source_checkpoint_ref,
                        "checkpointDigest": artifact_digests[source_checkpoint_ref],
                    },
                    "label": "Production-boundary root",
                    "instructions": {"text": "Repair the root branch."},
                    "workspacePolicy": (
                        "apply_previous_execution_diff_to_clean_baseline"
                    ),
                    "runtimeContextPolicy": "fresh_agent_run",
                    "providerProfileRef": "profile-1",
                    "executionProfileRef": "profile-1",
                    "model": "gpt-test",
                    "effort": "high",
                    "publishMode": "branch",
                    "gitWorkBranch": "feature/checkpoint-branch-public-owner",
                    "idempotencyKey": "public-owner-root",
                }
                root = await client.post(
                    "/api/executions/mm:wf-public-owner/checkpoint-branches",
                    json=root_payload,
                )
                assert root.status_code == 201, root.text
                root_branch_id = root.json()["branchId"]
                root_turns = await client.get(
                    f"/api/executions/mm:wf-public-owner/checkpoint-branches/"
                    f"{root_branch_id}/turns"
                )
                root_turn_id = root_turns.json()["items"][0]["branchTurnId"]
                root_result = await env.client.get_workflow_handle(
                    f"checkpoint-branch-turn:{root_turn_id}"
                ).result()
                assert root_result["status"] == "checking"
                replayed_root = await client.post(
                    "/api/executions/mm:wf-public-owner/checkpoint-branches",
                    json=root_payload,
                )
                assert replayed_root.status_code == 201, replayed_root.text
                assert replayed_root.json()["branchId"] == root_branch_id

                continue_payload = {
                    "label": "Production-boundary continuation",
                    "instructions": {"text": "Continue from the accepted head."},
                    "workspacePolicy": "continue_from_previous_execution",
                    "runtimeContextPolicy": "fresh_agent_run",
                    "idempotencyKey": "public-owner-continue",
                }
                continued = await client.post(
                    f"/api/executions/mm:wf-public-owner/checkpoint-branches/"
                    f"{root_branch_id}/continue",
                    json=continue_payload,
                )
                assert continued.status_code == 201, continued.text
                continue_turn_id = continued.json()["branchTurnId"]
                continue_result = await env.client.get_workflow_handle(
                    f"checkpoint-branch-turn:{continue_turn_id}"
                ).result()
                assert continue_result["status"] == "checking"
                replayed_continue = await client.post(
                    f"/api/executions/mm:wf-public-owner/checkpoint-branches/"
                    f"{root_branch_id}/continue",
                    json=continue_payload,
                )
                assert replayed_continue.status_code == 201
                assert replayed_continue.json()["branchTurnId"] == continue_turn_id

                fork_payload = {
                    "label": "Production-boundary fork",
                    "instructions": {"text": "Fork from the continued head."},
                    "workspacePolicy": (
                        "apply_previous_execution_diff_to_clean_baseline"
                    ),
                    "runtimeContextPolicy": "fresh_agent_run",
                    "parentTurnId": continue_turn_id,
                    "idempotencyKey": "public-owner-fork",
                }
                forked = await client.post(
                    f"/api/executions/mm:wf-public-owner/checkpoint-branches/"
                    f"{root_branch_id}/fork",
                    json=fork_payload,
                )
                assert forked.status_code == 201, forked.text
                fork_branch_id = forked.json()["branchId"]
                fork_turns = await client.get(
                    f"/api/executions/mm:wf-public-owner/checkpoint-branches/"
                    f"{fork_branch_id}/turns"
                )
                fork_turn_id = fork_turns.json()["items"][0]["branchTurnId"]
                fork_result = await env.client.get_workflow_handle(
                    f"checkpoint-branch-turn:{fork_turn_id}"
                ).result()
                assert fork_result["status"] == "checking"
                replayed_fork = await client.post(
                    f"/api/executions/mm:wf-public-owner/checkpoint-branches/"
                    f"{root_branch_id}/fork",
                    json=fork_payload,
                )
                assert replayed_fork.status_code == 201
                assert replayed_fork.json()["branchId"] == fork_branch_id

                # Once the fork has its own accepted checkpoint, that parent
                # authority remains valid even if the canonical source workflow
                # advances to a new run.
                async with sessions() as rerun_session:
                    source_row = await rerun_session.get(
                        TemporalExecutionCanonicalRecord,
                        "mm:wf-public-owner",
                    )
                    assert source_row is not None
                    source_row.run_id = "run-public-owner-rerun"
                    await rerun_session.commit()
                fork_continue_payload = {
                    "label": "Continue the independent fork",
                    "instructions": {"text": "Continue from the fork head."},
                    "workspacePolicy": "continue_from_previous_execution",
                    "runtimeContextPolicy": "fresh_agent_run",
                    "idempotencyKey": "public-owner-fork-continue",
                }
                fork_continued = await client.post(
                    f"/api/executions/mm:wf-public-owner/checkpoint-branches/"
                    f"{fork_branch_id}/continue",
                    json=fork_continue_payload,
                )
                assert fork_continued.status_code == 201, fork_continued.text
                fork_continue_turn_id = fork_continued.json()["branchTurnId"]
                fork_continue_result = await env.client.get_workflow_handle(
                    f"checkpoint-branch-turn:{fork_continue_turn_id}"
                ).result()
                assert fork_continue_result["status"] == "checking"

    async with sessions() as session:
        source_after = await session.get(
            TemporalExecutionCanonicalRecord, "mm:wf-public-owner"
        )
        turns = list(
            (
                await session.execute(
                    select(WorkflowCheckpointBranchTurn)
                    .where(
                        WorkflowCheckpointBranchTurn.created_step_execution_id.is_not(
                            None
                        )
                    )
                    .order_by(WorkflowCheckpointBranchTurn.created_at)
                )
            ).scalars()
        )
        checkpoint_rows = list(
            (
                await session.execute(
                    select(WorkflowCheckpointBranchArtifact).where(
                        WorkflowCheckpointBranchArtifact.artifact_kind
                        == "output.branch_turn.checkpoint.json"
                    )
                )
            ).scalars()
        )

    assert source_after is not None
    assert source_after.parameters == source_parameters
    assert source_after.memo == source_memo
    assert str(getattr(source_after.state, "value", source_after.state)) == "executing"
    assert len(turns) == 4
    assert len({turn.created_step_execution_id for turn in turns}) == 4
    assert len({turn.runtime_agent_run_id for turn in turns}) == 4
    assert all(turn.status == "checking" for turn in turns)
    turns_by_id = {turn.branch_turn_id: turn for turn in turns}
    root_turn = turns_by_id[root_turn_id]
    continue_turn = turns_by_id[continue_turn_id]
    fork_turn = turns_by_id[fork_turn_id]
    fork_continue_turn = turns_by_id[fork_continue_turn_id]
    assert root_turn.source_checkpoint_ref == source_checkpoint_ref
    assert continue_turn.source_checkpoint_ref == created_checkpoint_refs[
        root_turn.created_step_execution_id
    ]
    assert fork_turn.source_checkpoint_ref == created_checkpoint_refs[
        continue_turn.created_step_execution_id
    ]
    assert fork_continue_turn.source_checkpoint_ref == created_checkpoint_refs[
        fork_turn.created_step_execution_id
    ]
    assert continue_turn.parent_turn_id == root_turn_id
    assert fork_turn.parent_turn_id == continue_turn_id
    assert fork_continue_turn.parent_turn_id == fork_turn_id
    assert len(checkpoint_rows) == 4
    assert len({row.artifact_ref for row in checkpoint_rows}) == 4
    assert len(created_checkpoint_ids) == 4
    for identity_kind in (
        "provider_lease",
        "host_lease",
        "host",
        "bridge_session",
        "provider_session",
        "first_message",
        "output",
        "branch",
        "commit",
        "publication",
        "cleanup",
        "capacity_release",
    ):
        assert len(ledger.identities[identity_kind]) == 4, identity_kind
        assert ledger.effect_counts[identity_kind] == 4, identity_kind
    assert ledger.identities["pull_request"] == set()
    assert ledger.effect_counts["pull_request"] == 0
    assert "source-provider-lease" not in ledger.identities["provider_lease"]
    assert "source-host-lease" not in ledger.identities["host_lease"]
    assert "source-host" not in ledger.identities["host"]
    assert "source-bridge" not in ledger.identities["bridge_session"]
    assert "source-session" not in ledger.identities["provider_session"]
    assert "source-message" not in ledger.identities["first_message"]
    workspace_ids = {
        locator["workspaceId"] for locator in ledger.workspace_locators
    }
    assert len(workspace_ids) == 4
    assert "source-workspace" not in workspace_ids
    requests_by_turn = {
        request.correlation_id: request for request in ledger.requests
    }
    async with sessions() as session:
        artifacts = artifact_service(session)
        for request in ledger.requests:
            step = request.step_execution
            assert step is not None
            assert "agentProfileSnapshot" not in step.runtime_selection
            assert "agentProfileSnapshot" not in request.parameters
            assert step.runtime_selection["agentProfile"] == {
                "profileId": "checkpoint-owner-configuration",
                "version": 1,
                "digest": configuration_digest,
            }
            assert step.runtime_selection["providerProfileRef"] == "profile-1"
            assert step.runtime_selection["model"] == "gpt-test"
            assert step.runtime_selection["effort"] == "high"
            _artifact, context_bytes = await artifacts.read(
                artifact_id=step.context_bundle_ref.removeprefix("artifact://"),
                principal=principal,
                allow_restricted_raw=True,
            )
            assert "sha256:" + hashlib.sha256(context_bytes).hexdigest() == step.context_bundle_digest
            context = json.loads(context_bytes)
            snapshot = context["runtimeSelection"]["agentProfileSnapshot"]
            assert snapshot["document"]["providerRequirements"]["credentialSource"] == "oauth_volume"
            usage = await session.scalar(select(OmnigentAgentProfileUsage).where(
                OmnigentAgentProfileUsage.consumer_type == "checkpoint",
                OmnigentAgentProfileUsage.consumer_id == context["branchId"],
            ))
            assert usage is not None
            assert usage.effective_snapshot == snapshot
    assert set(requests_by_turn) == {
        root_turn_id,
        continue_turn_id,
        fork_turn_id,
        fork_continue_turn_id,
    }
    assert requests_by_turn[root_turn_id].checkpoint_recovery[
        "omnigentCheckpoint"
    ]["stepExecutionId"] == "source-step-execution"
    assert requests_by_turn[continue_turn_id].checkpoint_recovery[
        "omnigentCheckpoint"
    ]["stepExecutionId"] == root_turn.created_step_execution_id
    assert requests_by_turn[fork_turn_id].checkpoint_recovery[
        "omnigentCheckpoint"
    ]["stepExecutionId"] == continue_turn.created_step_execution_id
    assert requests_by_turn[fork_continue_turn_id].checkpoint_recovery[
        "omnigentCheckpoint"
    ]["stepExecutionId"] == fork_turn.created_step_execution_id
    assert len(
        {request.workspace_spec["targetBranch"] for request in ledger.requests}
    ) == 2

    app.dependency_overrides.clear()
    await engine.dispose()


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


@pytest.mark.parametrize(
    ("stage", "position"),
    [
        (stage, position)
        for stage in (
            "step_execution_allocation",
            "profile_lease",
            "host_start",
            "session_creation",
            "first_message",
            "terminal_harvest",
            "checkpoint_capture",
            "publication",
            "cleanup",
            "capacity_release",
        )
        for position in ("before", "after")
    ],
)
async def test_checkpoint_branch_turn_cancellation_matrix_persists_durable_terminal(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    position: str,
) -> None:
    """Cancel at every authority handoff through the real owner/coordinator path."""

    global WORKFLOW_BOUNDARY
    global WORKFLOW_BOUNDARY_REACHED
    global WORKFLOW_BOUNDARY_RELEASE
    global DURABLE_CHECKPOINT_REF

    engine, sessions, refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    DURABLE_CHECKPOINT_REF = refs["checkpoint"]
    CALLS.clear()
    outer_stage = stage in {
        "step_execution_allocation",
        "checkpoint_capture",
    }
    async def artifact_writer(kind: str, _key: str, _body: bytes) -> str:
        return {
            "output": refs["output"],
            "diagnostics": refs["diagnostics"],
            "external-state": refs["external"],
        }[kind]

    ledger = CheckpointBranchRuntimeLedger(
        pause_stage=None if outer_stage else stage,
        pause_position=position,
        artifact_writer=artifact_writer,
    )
    if outer_stage:
        WORKFLOW_BOUNDARY = (stage, position)
        WORKFLOW_BOUNDARY_REACHED = asyncio.Event()
        WORKFLOW_BOUNDARY_RELEASE = asyncio.Event()
        cancel_ready = WORKFLOW_BOUNDARY_REACHED
    else:
        cancel_ready = ledger.boundary_reached

    async def execute_profile_bound(
        request: AgentExecutionRequest,
    ) -> AgentRunResult:
        return await execute_checkpoint_branch_request(request, ledger=ledger)

    monkeypatch.setattr(
        omnigent_activities,
        "_omnigent_execute_activity",
        execute_profile_bound,
    )
    correlation_id = f"cancel-{position}-{stage}"
    try:
        result, history = await _run(
            correlation_id,
            cancel=True,
            cancel_ready=cancel_ready,
            durable_terminal=True,
            publish_mode="branch",
            real_agent_run=True,
        )
    finally:
        ledger.release_boundary()
        if WORKFLOW_BOUNDARY_RELEASE is not None:
            WORKFLOW_BOUNDARY_RELEASE.set()
        WORKFLOW_BOUNDARY = None
        WORKFLOW_BOUNDARY_REACHED = None
        WORKFLOW_BOUNDARY_RELEASE = None
        DURABLE_CHECKPOINT_REF = None

    assert result is None
    assert CALLS[-1] == ("terminal", "canceled")
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.status == "canceled"
        assert turn.created_step_execution_id == (
            "branch-owner:run:implement:execution:1"
        )
        assert turn.completed_at is not None
        assert turn.diagnostics["deliveryStage"] == "canceled"
        assert turn.diagnostics["verificationPending"] is False
        assert turn.diagnostics["terminalDisposition"] == "canceled"

    for identity_kind, count in ledger.effect_counts.items():
        assert count <= 1, identity_kind
        assert len(ledger.identities[identity_kind]) == count, identity_kind
    if ledger.effect_counts["capacity_release"]:
        assert ledger.effect_counts["cleanup"] == 1
    completed_cleanup = [
        index
        for index, event in enumerate(ledger.lifecycle)
        if event == ("host_cleanup", "completed")
    ]
    completed_release = [
        index
        for index, event in enumerate(ledger.lifecycle)
        if event == ("profile_lease_release", "completed")
    ]
    coordinator_terminal = [
        index
        for index, (event_type, _status) in enumerate(ledger.lifecycle)
        if event_type == "terminal"
    ]
    if completed_release:
        assert completed_cleanup
        assert coordinator_terminal
        assert completed_cleanup[-1] < completed_release[-1] < coordinator_terminal[-1]

    if stage == "capacity_release" and position == "after":
        await Replayer(
            workflows=[MoonMindCheckpointBranchTurnWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
    await engine.dispose()


@pytest.mark.parametrize(
    ("stage", "position"),
    [
        (stage, position)
        for stage in (
            "step_execution_allocation",
            "profile_lease",
            "host_start",
            "session_creation",
            "first_message",
            "terminal_harvest",
            "checkpoint_capture",
            "publication",
            "cleanup",
            "capacity_release",
        )
        for position in ("before", "after")
    ],
)
async def test_checkpoint_branch_turn_worker_failure_matrix_terminalizes_durably(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    position: str,
) -> None:
    """Exhaust worker retries at every handoff without losing turn authority."""

    global WORKFLOW_FAILURE_BOUNDARY
    global DURABLE_CHECKPOINT_REF

    engine, sessions, refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    DURABLE_CHECKPOINT_REF = refs["checkpoint"]
    CALLS.clear()
    outer_stage = stage in {
        "step_execution_allocation",
        "checkpoint_capture",
    }
    if outer_stage:
        WORKFLOW_FAILURE_BOUNDARY = (stage, position)

    async def artifact_writer(kind: str, _key: str, _body: bytes) -> str:
        return {
            "output": refs["output"],
            "diagnostics": refs["diagnostics"],
            "external-state": refs["external"],
        }[kind]

    ledger = CheckpointBranchRuntimeLedger(
        fail_stage=None if outer_stage else stage,
        fail_position=position,
        fail_always=not outer_stage,
        artifact_writer=artifact_writer,
    )

    async def execute_profile_bound(
        request: AgentExecutionRequest,
    ) -> AgentRunResult:
        return await execute_checkpoint_branch_request(request, ledger=ledger)

    monkeypatch.setattr(
        omnigent_activities,
        "_omnigent_execute_activity",
        execute_profile_bound,
    )
    correlation_id = f"worker-failure-{position}-{stage}"
    try:
        result, history = await _run(
            correlation_id,
            durable_terminal=True,
            publish_mode="branch",
            real_agent_run=True,
        )
    finally:
        WORKFLOW_FAILURE_BOUNDARY = None
        DURABLE_CHECKPOINT_REF = None

    assert result["status"] in {"failed", "blocked"}
    assert result["verificationPending"] is False
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.status in {"failed", "blocked"}
        assert turn.created_step_execution_id == (
            "branch-owner:run:implement:execution:1"
        )
        assert turn.completed_at is not None
        assert turn.diagnostics["deliveryStage"] in {"failed", "blocked"}
        assert turn.diagnostics["verificationPending"] is False

    for identity_kind, count in ledger.effect_counts.items():
        assert count <= 1, identity_kind
        assert len(ledger.identities[identity_kind]) == count, identity_kind
    if not outer_stage:
        assert ledger.failure_injected is True
    if ledger.effect_counts["capacity_release"]:
        assert ledger.effect_counts["cleanup"] == 1

    if stage == "step_execution_allocation" and position == "before":
        await Replayer(
            workflows=[MoonMindCheckpointBranchTurnWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
    await engine.dispose()


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

    first = await persist_checkpoint_branch_turn_terminal(payload)
    replay = await persist_checkpoint_branch_turn_terminal(payload)

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

    first = await persist_checkpoint_branch_turn_terminal(payload)
    replay = await persist_checkpoint_branch_turn_terminal(payload)

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
    result = await persist_checkpoint_branch_turn_terminal(
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
        await persist_checkpoint_branch_turn_terminal(
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
        await persist_checkpoint_branch_turn_terminal(
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

    first = await persist_checkpoint_branch_turn_terminal_rejection(payload)
    replay = await persist_checkpoint_branch_turn_terminal_rejection(payload)

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
            principal="service:checkpoint-branch-turn",
            allow_restricted_raw=True,
        )
        assert b"terminalPayloadDigest" in body
        assert b"unsafe" not in body
    await engine.dispose()


async def test_terminal_handoff_pins_every_retained_ref_across_lifecycle_sweep(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    result = await persist_checkpoint_branch_turn_terminal(
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
                principal="service:checkpoint-branch-turn",
                allow_restricted_raw=True,
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
    result = await persist_checkpoint_branch_turn_terminal(
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
            principal="service:checkpoint-branch-turn",
            allow_restricted_raw=True,
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
