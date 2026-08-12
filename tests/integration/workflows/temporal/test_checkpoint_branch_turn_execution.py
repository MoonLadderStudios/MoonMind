"""Hermetic Temporal boundary for MoonLadderStudios/MoonMind#3621."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
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
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.temporal_models import StepExecutionCheckpointModel
from moonmind.workflows import get_temporal_artifact_repository
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactService,
)
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
    persist_checkpoint_branch_turn_terminal,
    persist_checkpoint_branch_turn_terminal_rejection,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

CALLS: list[tuple[str, object]] = []


@workflow.defn(name="MoonMind.AgentRun")
class _FakeAgentRun:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        if request.correlation_id == "canceled":
            await workflow.wait_condition(lambda: False)
        if request.correlation_id == "worker-failure":
            raise RuntimeError("worker failed before terminal delivery")
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


@activity.defn(name="integration.omnigent.profile_bound_execute")
async def _execute_real_profile_bound_turn(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    CALLS.append(("profile_bound_execute", request.execution_profile_ref))
    assert (request.agent_kind, request.agent_id) == ("external", "omnigent")
    assert request.execution_profile_ref == "profile-1"
    assert request.step_execution is not None
    assert request.step_execution.runtime_context_policy == "fresh_agent_run"
    assert request.checkpoint_recovery is not None
    assert request.checkpoint_recovery["recoveryAction"] == "branch_required"
    return AgentRunResult(
        outputRefs=["artifact://output/profile-bound-result"],
        summary="profile-bound branch turn completed",
        diagnosticsRef="artifact://diagnostics/profile-bound-runtime",
        metadata={
            "omnigentCheckpointCapture": {
                "bridgeSessionId": "fresh-profile-bound-bridge",
                "omnigentSessionId": "fresh-profile-bound-session",
                "terminalRef": "artifact://terminal/profile-bound-session",
            }
        },
    )


@activity.defn(name="agent_runtime.publish_artifacts")
async def _publish_real_agent_result(
    result: AgentRunResult | None = None,
) -> AgentRunResult | None:
    CALLS.append(("publish_artifacts", bool(result)))
    return result


@activity.defn(name="checkpoint_branch.turn.mark_running")
async def _mark_running(payload: dict) -> None:
    CALLS.append(("mark_running", payload["agentRunWorkflowId"]))


@activity.defn(name="workspace.capture_checkpoint")
async def _capture_workspace(payload: dict) -> dict:
    CALLS.append(("capture", payload["idempotencyKey"]))
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
    CALLS.append(("checkpoint", payload["idempotencyKey"]))
    return {
        "checkpointRef": "artifact://checkpoint/branch-turn-result",
        "idempotencyKey": payload["idempotencyKey"],
    }


@activity.defn(name="checkpoint_branch.turn.persist_terminal")
async def _persist_terminal(payload: dict) -> dict:
    CALLS.append(("terminal", payload["outcome"]))
    result = AgentRunResult.model_validate(payload["agentResult"])
    succeeded = payload["outcome"] == "succeeded" and not result.failure_class
    return {
        "branchId": payload["branchId"],
        "branchTurnId": payload["branchTurnId"],
        "status": "checking" if succeeded else "failed",
        "deliveryOutcome": "succeeded" if succeeded else "failed",
        "terminalDisposition": (
            "verification_pending" if succeeded else "provider_failure"
        ),
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


def _request(correlation_id: str) -> AgentExecutionRequest:
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
            "omnigentCheckpoint": {"schemaVersion": "v2"},
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
            "publishMode": "none",
        },
    )


def _input(correlation_id: str) -> dict:
    request = _request(correlation_id)
    return {
        "schemaVersion": "checkpoint-branch-turn-execution/v1",
        "workflowId": "source-workflow",
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "principal": "service:test",
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "agentRunWorkflowId": f"checkpoint-branch-agent:{correlation_id}",
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
    real_agent_run: bool = False,
):
    queue = f"checkpoint-branch-turn-{uuid4()}"
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
                        _mark_running,
                        _persist_terminal,
                        _persist_terminal_rejection,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
            )
            if real_agent_run:
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=get_workflow_task_queue(),
                        activities=[_resolve_real_agent_adapter],
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=AGENT_RUNTIME_TASK_QUEUE,
                        activities=[
                            _execute_real_profile_bound_turn,
                            _publish_real_agent_result,
                        ],
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
                    activities=[_create_checkpoint],
                )
            )
            handle = await env.client.start_workflow(
                MoonMindCheckpointBranchTurnWorkflow.run,
                _input(correlation_id),
                id=f"checkpoint-branch-owner-{correlation_id}-{uuid4()}",
                task_queue=queue,
            )
            if cancel:
                for _attempt in range(100):
                    if any(name == "mark_running" for name, _value in CALLS):
                        break
                    await asyncio.sleep(0.01)
                assert any(name == "mark_running" for name, _value in CALLS)
                await handle.cancel()
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
                result = None
            else:
                result = await handle.result()
            history = await handle.fetch_history()
    return result, history


async def test_checkpoint_branch_turn_uses_real_agent_run_profile_bound_path(
) -> None:
    """Exercise the production AgentRun dispatch and routed Activity contracts."""

    CALLS.clear()
    result, _history = await _run("real-agent-run", real_agent_run=True)

    assert result["status"] == "checking"
    assert [name for name, _value in CALLS] == [
        "mark_running",
        "resolve_adapter",
        "profile_bound_execute",
        "publish_artifacts",
        "capture",
        "checkpoint",
        "terminal",
    ]


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
    result, _history = await _run("canceled", cancel=True)

    assert result is None
    assert [name for name, _value in CALLS] == [
        "mark_running",
        "terminal",
    ]
    assert CALLS[-1][1] == "canceled"


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
        graph = await CheckpointBranchService(session).create_branch_graph(
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
