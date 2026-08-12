"""Hermetic Temporal boundary for MoonLadderStudios/MoonMind#3621."""

from __future__ import annotations

from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    MoonMindCheckpointBranchTurnWorkflow,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

CALLS: list[tuple[str, object]] = []


@workflow.defn(name="MoonMind.AgentRun")
class _FakeAgentRun:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
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


async def _run(correlation_id: str):
    queue = f"checkpoint-branch-turn-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindCheckpointBranchTurnWorkflow, _FakeAgentRun],
                    activities=[_mark_running, _persist_terminal],
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
                    activities=[_create_checkpoint],
                )
            )
            handle = await env.client.start_workflow(
                MoonMindCheckpointBranchTurnWorkflow.run,
                _input(correlation_id),
                id=f"checkpoint-branch-owner-{correlation_id}-{uuid4()}",
                task_queue=queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()
    return result, history


async def test_checkpoint_branch_turn_runs_agent_captures_checkpoint_and_hands_off() -> None:
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


async def test_checkpoint_branch_turn_provider_failure_skips_false_checkpoint_success() -> None:
    CALLS.clear()
    result, _history = await _run("provider-failure")

    assert result["status"] == "failed"
    assert result["verificationPending"] is False
    assert result["checkpointRef"] is None
    assert [name for name, _value in CALLS] == ["mark_running", "terminal"]
