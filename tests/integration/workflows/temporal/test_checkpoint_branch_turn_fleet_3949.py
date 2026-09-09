"""Artifacts-fleet handoff ownership journeys (MoonLadderStudios/MoonMind#3949).

Time-skipping Temporal journeys proving the production wiring end to end:
the real handler objects serve new persistence exclusively from the
artifacts fleet, duplicate delivery reuses the owned row, divergent replays
cannot overwrite newer evidence, stale running claims are rejected, and a
database outage fails closed without partial writes or premature cleanup.

These journeys each start a fresh time-skipping Temporal server, so this
module carries only ``integration`` (not ``integration_ci``): time-skipping
tests under ``tests/integration/workflows/temporal/**`` are excluded from
required CI because they consistently exceed CI timeout thresholds.
Shared database/input helpers live in ``test_checkpoint_branch_turn_execution``.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest
from sqlalchemy import func, select
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import (
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchTurn,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
)
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    MoonMindCheckpointBranchTurnWorkflow,
    mark_checkpoint_branch_turn_running,
    persist_checkpoint_branch_turn_terminal,
    persist_checkpoint_branch_turn_terminal_rejection,
)
from tests.integration.workflows.temporal.test_checkpoint_branch_turn_execution import (
    _input,
    _terminal_activity_database,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
# --- MoonMind#3949: artifacts-fleet handoff ownership -------------------------
#
# The durable matrix tests in test_checkpoint_branch_turn_execution.py bind the
# real persistence handlers on every test queue, so a routing regression would
# still pass. These tests prove the
# production wiring end to end: the real handler objects serve new
# persistence exclusively from the artifacts fleet, duplicate delivery
# reuses the owned row, divergent replays cannot overwrite newer evidence,
# stale running claims are rejected, and a database outage fails closed
# without partial writes or premature cleanup.

FLEET3949_REFS: dict[str, str] = {}
FLEET3949_CALLS: list[tuple[str, str, int, object]] = []
FLEET3949_FAIL_TERMINAL_ONCE = False
FLEET3949_FAIL_TERMINAL_ALWAYS = False


@activity.defn(name="checkpoint_branch.turn.mark_running")
async def _fleet3949_mark_running(payload: dict) -> None:
    info = activity.info()
    FLEET3949_CALLS.append(
        ("mark_running", info.task_queue, info.attempt, payload["agentRunWorkflowId"])
    )
    await mark_checkpoint_branch_turn_running(payload)


@activity.defn(name="checkpoint_branch.turn.persist_terminal")
async def _fleet3949_persist_terminal(payload: dict) -> dict:
    info = activity.info()
    FLEET3949_CALLS.append(
        ("terminal", info.task_queue, info.attempt, payload["outcome"])
    )
    global FLEET3949_FAIL_TERMINAL_ONCE
    if FLEET3949_FAIL_TERMINAL_ALWAYS:
        raise RuntimeError("injected persistent fleet3949 terminal failure")
    if FLEET3949_FAIL_TERMINAL_ONCE and info.attempt == 1:
        FLEET3949_FAIL_TERMINAL_ONCE = False
        raise RuntimeError("injected transient fleet3949 terminal failure")
    return await persist_checkpoint_branch_turn_terminal(payload)


@activity.defn(name="checkpoint_branch.turn.persist_terminal_rejection")
async def _fleet3949_persist_terminal_rejection(payload: dict) -> dict:
    info = activity.info()
    FLEET3949_CALLS.append(
        (
            "terminal_rejection",
            info.task_queue,
            info.attempt,
            payload["terminalPayloadDigest"],
        )
    )
    return await persist_checkpoint_branch_turn_terminal_rejection(payload)


@workflow.defn(name="MoonMind.AgentRun")
class _Fleet3949AgentRun:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        if request.correlation_id == "canceled":
            await workflow.wait_condition(lambda: False)
        if request.correlation_id == "provider-failure":
            return AgentRunResult(
                outputRefs=[FLEET3949_REFS["output"]],
                diagnosticsRef=FLEET3949_REFS["diagnostics"],
                summary="provider failed safely",
                failureClass="execution_error",
                providerErrorCode="provider_terminal_failed",
            )
        return AgentRunResult(
            outputRefs=[FLEET3949_REFS["output"]],
            diagnosticsRef=FLEET3949_REFS["diagnostics"],
            summary="branch turn completed",
            metadata={
                "omnigentCheckpointCapture": {
                    "omnigentSessionId": "fresh-session-turn-1",
                    "terminalRef": FLEET3949_REFS["terminal"],
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
        )


@activity.defn(name="workspace.capture_checkpoint")
async def _fleet3949_capture_workspace(payload: dict) -> dict:
    return {
        "status": "captured",
        "workspace": {
            "kind": "worktree_archive",
            "baseCommit": payload["baseCommit"],
            "archiveRef": FLEET3949_REFS["workspace"],
            "archiveDigest": "sha256:" + "c" * 64,
        },
        "diagnosticRefs": [FLEET3949_REFS["diagnostics"]],
    }


@activity.defn(name="step_checkpoint.create_v2")
async def _fleet3949_create_checkpoint(payload: dict) -> dict:
    return {
        "checkpointRef": FLEET3949_REFS["checkpoint"],
        "idempotencyKey": payload["idempotencyKey"],
    }


async def _run_fleet3949(
    correlation_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    cancel: bool = False,
):
    """Run one turn with real persistence served only by the artifacts fleet.

    No ``checkpoint_branch.turn.*`` handler is bound on the workflow test
    queue: if new histories stopped routing to the artifacts fleet, every
    persistence call would fail instead of silently landing elsewhere.
    """

    from uuid import uuid4 as _uuid4

    FLEET3949_REFS.clear()
    FLEET3949_CALLS.clear()
    engine, sessions, refs = await _terminal_activity_database(
        tmp_path, monkeypatch
    )
    FLEET3949_REFS.update(refs)
    queue = f"checkpoint-branch-fleet3949-{_uuid4()}"
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=queue,
                        workflows=[
                            MoonMindCheckpointBranchTurnWorkflow,
                            _Fleet3949AgentRun,
                        ],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=SANDBOX_TASK_QUEUE,
                        activities=[_fleet3949_capture_workspace],
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=ARTIFACTS_TASK_QUEUE,
                        activities=[
                            _fleet3949_create_checkpoint,
                            _fleet3949_mark_running,
                            _fleet3949_persist_terminal,
                            _fleet3949_persist_terminal_rejection,
                        ],
                    )
                )
                handle = await env.client.start_workflow(
                    MoonMindCheckpointBranchTurnWorkflow.run,
                    _input(
                        correlation_id,
                        agent_run_workflow_id="agent-run-turn-1",
                    ),
                    id=f"checkpoint-branch-fleet3949-{correlation_id}-{_uuid4()}",
                    task_queue=queue,
                )
                if cancel:
                    for _attempt in range(100):
                        if any(
                            name == "mark_running" for name, *_rest in FLEET3949_CALLS
                        ):
                            break
                        await asyncio.sleep(0.01)
                    assert any(
                        name == "mark_running" for name, *_rest in FLEET3949_CALLS
                    )
                    await handle.cancel()
                    with pytest.raises(WorkflowFailureError):
                        await handle.result()
                    for _attempt in range(200):
                        if any(name == "terminal" for name, *_rest in FLEET3949_CALLS):
                            break
                        await asyncio.sleep(0.01)
                    assert any(
                        name == "terminal" for name, *_rest in FLEET3949_CALLS
                    )
                    result = None
                else:
                    result = await handle.result()
                history = await handle.fetch_history()
        return result, history, list(FLEET3949_CALLS), sessions
    finally:
        FLEET3949_REFS.clear()
        await engine.dispose()


def _fleet3949_scheduled_persistence(history):
    """Return (activity_type, task_queue, start_close, schedule_close, attempts)."""

    scheduled = []
    for event in history.events:
        if not event.HasField("activity_task_scheduled_event_attributes"):
            continue
        attrs = event.activity_task_scheduled_event_attributes
        name = attrs.activity_type.name
        if not name.startswith("checkpoint_branch.turn."):
            continue
        scheduled.append(
            (
                name,
                attrs.task_queue.name,
                attrs.start_to_close_timeout,
                attrs.schedule_to_close_timeout,
                attrs.retry_policy.maximum_attempts,
            )
        )
    return scheduled


_FLEET3949_TIMEOUTS = {
    "checkpoint_branch.turn.mark_running": (60, 180),
    "checkpoint_branch.turn.persist_terminal": (120, 300),
    "checkpoint_branch.turn.persist_terminal_rejection": (120, 300),
}


def _assert_fleet3949_operation_identity(history) -> None:
    scheduled = _fleet3949_scheduled_persistence(history)
    assert scheduled, "no checkpoint persistence was scheduled"
    for name, task_queue, start_close, schedule_close, attempts in scheduled:
        assert task_queue == ARTIFACTS_TASK_QUEUE, name
        expected_start, expected_close = _FLEET3949_TIMEOUTS[name]
        assert start_close.seconds == expected_start, name
        assert schedule_close.seconds == expected_close, name
        assert attempts == 3, name


async def test_new_success_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, history, fleet_calls, sessions = await _run_fleet3949(
        "fleet3949-success", monkeypatch, tmp_path
    )

    assert result["status"] == "checking"
    assert result["verificationPending"] is True
    assert [name for name, _task_queue, _attempt, _value in fleet_calls] == [
        "mark_running",
        "terminal",
    ]
    assert all(
        task_queue == ARTIFACTS_TASK_QUEUE
        for _name, task_queue, _attempt, _value in fleet_calls
    )
    _assert_fleet3949_operation_identity(history)
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.completed_at is not None
        assert turn.diagnostics["deliveryStage"] == "delivered_verification_pending"
        assert turn.diagnostics["verificationPending"] is True
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_new_failure_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, history, fleet_calls, sessions = await _run_fleet3949(
        "provider-failure", monkeypatch, tmp_path
    )

    assert result["status"] in ("failed", "blocked")
    assert result["verificationPending"] is False
    assert [name for name, _task_queue, _attempt, _value in fleet_calls] == [
        "mark_running",
        "terminal",
    ]
    assert all(
        task_queue == ARTIFACTS_TASK_QUEUE
        for _name, task_queue, _attempt, _value in fleet_calls
    )
    _assert_fleet3949_operation_identity(history)
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.completed_at is not None
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_new_cancellation_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, history, fleet_calls, sessions = await _run_fleet3949(
        "canceled", monkeypatch, tmp_path, cancel=True
    )

    assert result is None
    assert [name for name, _task_queue, _attempt, _value in fleet_calls] == [
        "mark_running",
        "terminal",
    ]
    assert fleet_calls[-1][3] == "canceled"
    assert all(
        task_queue == ARTIFACTS_TASK_QUEUE
        for _name, task_queue, _attempt, _value in fleet_calls
    )
    _assert_fleet3949_operation_identity(history)
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.status == "canceled"
        assert turn.completed_at is not None
        assert turn.diagnostics["deliveryStage"] == "canceled"
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_transient_terminal_retry_reuses_owned_row_on_artifacts_fleet_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient terminal failure retries on the artifacts fleet without
    authoring a second terminal: the first attempt raises, the retry
    reuses the same owned turn row, and cleanup authority is released once."""

    global FLEET3949_FAIL_TERMINAL_ONCE
    FLEET3949_FAIL_TERMINAL_ONCE = True
    try:
        result, history, fleet_calls, sessions = await _run_fleet3949(
            "fleet3949-retry", monkeypatch, tmp_path
        )
    finally:
        FLEET3949_FAIL_TERMINAL_ONCE = False

    assert result["status"] == "checking"
    terminal_calls = [call for call in fleet_calls if call[0] == "terminal"]
    assert len(terminal_calls) == 2
    assert [attempt for _n, _q, attempt, _v in terminal_calls] == [1, 2]
    assert all(
        task_queue == ARTIFACTS_TASK_QUEUE
        for _n, task_queue, _a, _v in terminal_calls
    )
    _assert_fleet3949_operation_identity(history)
    async with sessions() as session:
        owned_count = await session.scalar(
            select(func.count())
            .select_from(WorkflowCheckpointBranchTurn)
            .where(WorkflowCheckpointBranchTurn.branch_turn_id == "turn-1")
        )
        assert owned_count == 1
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.completed_at is not None
        assert turn.diagnostics["deliveryStage"] == "delivered_verification_pending"
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


def _fleet3949_terminal_payload(refs: dict[str, str]) -> dict:    return {
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


async def test_duplicate_terminal_delivery_reuses_owned_row_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate delivery of the same turn cannot author a second terminal."""

    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    try:
        payload = _fleet3949_terminal_payload(refs)
        first = await persist_checkpoint_branch_turn_terminal(payload)
        replay = await persist_checkpoint_branch_turn_terminal(payload)

        assert replay == first
        assert first["status"] == "checking"
        async with sessions() as session:
            owned_count = await session.scalar(
                select(func.count())
                .select_from(WorkflowCheckpointBranchTurn)
                .where(WorkflowCheckpointBranchTurn.branch_turn_id == "turn-1")
            )
        assert owned_count == 1
    finally:
        await engine.dispose()


async def test_divergent_terminal_replay_cannot_overwrite_newer_evidence_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale replay with different evidence is rejected, never merged."""

    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    try:
        first = await persist_checkpoint_branch_turn_terminal(
            _fleet3949_terminal_payload(refs)
        )
        assert first["status"] == "checking"
        stale = _fleet3949_terminal_payload(refs)
        stale["agentResult"] = {
            **stale["agentResult"],
            "diagnosticsRef": refs["output"],
        }
        with pytest.raises(ValueError, match="immutable terminal field"):
            await persist_checkpoint_branch_turn_terminal(stale)
        async with sessions() as session:
            turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
            assert turn is not None
            assert turn.diagnostics["agentResultRef"] == first["agentResultRef"]
            assert turn.diagnostics["diagnosticsRef"] == first["diagnosticsRef"]
    finally:
        await engine.dispose()


async def test_stale_running_claim_is_rejected_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running claim for a superseded AgentRun identity cannot steal the turn."""

    engine, sessions, _refs = await _terminal_activity_database(tmp_path, monkeypatch)
    try:
        async with sessions() as session:
            turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
            assert turn is not None
            status_before = turn.status
        with pytest.raises(ValueError, match="does not match claim"):
            await mark_checkpoint_branch_turn_running(
                {
                    "workflowId": "source-workflow",
                    "branchId": "branch-1",
                    "branchTurnId": "turn-1",
                    "agentRunWorkflowId": "stale-agent-run",
                }
            )
        async with sessions() as session:
            turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
            assert turn is not None
            assert turn.status == status_before
    finally:
        await engine.dispose()


async def test_terminal_persistence_fails_closed_when_database_unavailable_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database outage stays visible: the handoff raises, writes nothing
    partial, and never releases cleanup authority."""

    engine, sessions, _refs = await _terminal_activity_database(tmp_path, monkeypatch)
    try:
        async with sessions() as session:
            turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
            assert turn is not None
            status_before = turn.status
            assert turn.completed_at is None

        def _outage_session_maker(*args, **kwargs):
            raise RuntimeError("database unavailable (fleet3949 outage probe)")

        monkeypatch.setattr(
            "moonmind.workflows.temporal.workflows.checkpoint_branch_turn."
            "async_session_maker",
            _outage_session_maker,
        )
        with pytest.raises(RuntimeError, match="database unavailable"):
            await mark_checkpoint_branch_turn_running(
                {
                    "workflowId": "source-workflow",
                    "branchId": "branch-1",
                    "branchTurnId": "turn-1",
                    "agentRunWorkflowId": "agent-run-turn-1",
                }
            )
        async with sessions() as session:
            turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
            assert turn is not None
            assert turn.status == status_before
            assert turn.completed_at is None
            assert not (turn.diagnostics or {}).get("agentResultRef")
    finally:
        await engine.dispose()


async def test_new_rejection_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausted terminal retries fall back to the artifacts-fleet rejection.

    The real ``persist_terminal`` fails on every attempt, so the workflow's
    ``_persist_terminal`` fallback schedules
    ``checkpoint_branch.turn.persist_terminal_rejection``. Both the failed
    terminal attempts and the rejection must be served by the artifacts
    fleet (the workflow queue carries no ``checkpoint_branch.turn.*``
    handler), the rejection digest must be a canonical sha256, and the
    durable turn must terminalize as blocked with
    ``retained_evidence_rejected`` without advancing the branch head to a
    terminal checkpoint for this turn.
    """

    import re

    global FLEET3949_FAIL_TERMINAL_ALWAYS
    FLEET3949_FAIL_TERMINAL_ALWAYS = True
    try:
        result, history, fleet_calls, sessions = await _run_fleet3949(
            "fleet3949-rejected", monkeypatch, tmp_path
        )
    finally:
        FLEET3949_FAIL_TERMINAL_ALWAYS = False

    assert result["status"] == "blocked"
    assert result["verificationPending"] is False
    assert result["terminalDisposition"] == "retained_evidence_rejected"

    kinds = [name for name, _queue, _attempt, _value in fleet_calls]
    assert kinds[0] == "mark_running"
    terminal_calls = [call for call in fleet_calls if call[0] == "terminal"]
    rejection_calls = [
        call for call in fleet_calls if call[0] == "terminal_rejection"
    ]
    assert len(terminal_calls) == 3
    assert [attempt for _n, _q, attempt, _v in terminal_calls] == [1, 2, 3]
    assert len(rejection_calls) == 1
    assert all(
        task_queue == ARTIFACTS_TASK_QUEUE
        for _name, task_queue, _attempt, _value in fleet_calls
    )
    digest = rejection_calls[0][3]
    assert isinstance(digest, str)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None

    _assert_fleet3949_operation_identity(history)
    scheduled_names = {
        name for name, _q, _s, _c, _a in _fleet3949_scheduled_persistence(history)
    }
    assert "checkpoint_branch.turn.persist_terminal" in scheduled_names
    assert "checkpoint_branch.turn.persist_terminal_rejection" in scheduled_names

    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.status == "blocked"
        assert turn.completed_at is not None
        assert turn.diagnostics["terminalDisposition"] == "retained_evidence_rejected"
        assert turn.diagnostics["verificationPending"] is False
        branch = await session.get(WorkflowCheckpointBranch, "branch-1")
        assert branch is not None
        # Rejected evidence never advances the branch head to a terminal
        # checkpoint for this turn; the head stays on the source checkpoint.
        assert branch.current_head_checkpoint_ref == "artifact://source/checkpoint"
        assert "latestBranchTurnCheckpoint" not in (branch.artifact_refs or {})
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
