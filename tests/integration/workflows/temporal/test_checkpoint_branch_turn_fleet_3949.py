"""Artifacts-fleet handoff ownership journeys (MoonLadderStudios/MoonMind#3949).

Time-skipping Temporal journeys that execute checkpoint persistence through
the production artifacts-worker registration: the activity handlers come
from ``build_worker_activity_bindings(fleet="artifacts")`` and the worker
uses the production slot limit from the artifacts topology, exactly as
``worker_runtime`` constructs the ``temporal-worker-artifacts`` service.
Only collaborators outside the persistence handoff (the AgentRun child,
workspace capture and checkpoint capture) are test doubles. Faults are
injected at the database session seam, not by wrapping activities.

The journeys prove that new persistence runs on the artifacts worker, that
duplicate delivery reuses the owned row, divergent replays cannot overwrite
newer evidence, stale running claims are rejected, a database outage fails
closed without partial writes or premature cleanup, and that control stays
schedulable while the artifacts worker's slots are saturated.

Each journey takes 1-3s on a time-skipping server, so the module is part of
required CI (``integration_ci``) like ``test_checkpoint_branch_turn_execution``.
Shared database/input helpers live in ``test_checkpoint_branch_turn_execution``.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Callable

import pytest
from sqlalchemy import func, select
from temporalio import activity, workflow
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import WorkflowCheckpointBranch, WorkflowCheckpointBranchTurn
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.worker_runtime import _worker_concurrency_kwargs
from moonmind.workflows.temporal.workers import (
    build_worker_activity_bindings,
    build_worker_topology,
)
from moonmind.workflows.temporal.workflow_registry import (
    checkpoint_branch_activity_handlers,
)
from moonmind.workflows.temporal.workflows import checkpoint_branch_turn
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    MoonMindCheckpointBranchTurnWorkflow,
    mark_checkpoint_branch_turn_running,
    persist_checkpoint_branch_turn_terminal,
)
from tests.integration.workflows.temporal.test_checkpoint_branch_turn_execution import (
    _input,
    _seed_terminal_turn,
    _terminal_activity_database,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_PERSISTENCE_PREFIX = "checkpoint_branch.turn."
_CALL_KINDS = {
    "checkpoint_branch.turn.mark_running": "mark_running",
    "checkpoint_branch.turn.persist_terminal": "terminal",
    "checkpoint_branch.turn.persist_terminal_rejection": "terminal_rejection",
}

#: Per-turn seeded evidence refs, keyed by branch turn id.
FLEET3949_REFS: dict[str, dict[str, str]] = {}
#: AgentRun correlation id -> branch turn id (default ``turn-1``).
FLEET3949_TURNS: dict[str, str] = {}
#: (kind, serving task queue, attempt, workflow id) per persistence attempt,
#: recorded at the database session seam inside the running activity.
FLEET3949_CALLS: list[tuple[str, str, int, str]] = []
#: Optional database fault: called with the activity info of every session
#: opened by a persistence handler; return True to fail that session.
FLEET3949_DB_FAULT: list[Callable[[activity.Info], bool]] = []
#: Optional hold for the checkpoint-capture double (slot saturation).
FLEET3949_CHECKPOINT_HOLD: dict[str, object] = {}


def _turn_for(correlation_id: str) -> str:
    return FLEET3949_TURNS.get(correlation_id, "turn-1")


class _TaskReentrantLock:
    """One SQLite writer at a time; nested sessions in the same task re-enter."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None

    @asynccontextmanager
    async def hold(self):
        task = asyncio.current_task()
        if self._owner is task:
            yield
            return
        async with self._lock:
            self._owner = task
            try:
                yield
            finally:
                self._owner = None


def _install_session_seam(
    monkeypatch: pytest.MonkeyPatch, *, serialize_sqlite_writers: bool = False
) -> None:
    """Observe and optionally fail persistence at the database session seam.

    The fixture binds ``checkpoint_branch_turn.async_session_maker`` to the
    test database; this wraps it so every session a production persistence
    handler opens records which worker queue served it, and a configured
    fault raises exactly as an unavailable database would.

    ``serialize_sqlite_writers`` is for concurrent journeys only: the test
    database is SQLite, which admits one writer and fails a concurrent
    read-to-write upgrade with ``database is locked`` instead of waiting the
    way the deployed PostgreSQL database does. Handlers open nested sessions
    within one task, so the serialization is per task and re-entrant.
    """

    sessions = checkpoint_branch_turn.async_session_maker
    seen: set[tuple[str, str, int]] = set()
    writer = _TaskReentrantLock()

    @asynccontextmanager
    async def _serialized(*args, **kwargs):
        async with writer.hold():
            async with sessions(*args, **kwargs) as session:
                yield session

    def _session_maker(*args, **kwargs):
        if activity.in_activity():
            info = activity.info()
            if info.activity_type.startswith(_PERSISTENCE_PREFIX):
                key = (info.workflow_id or "", info.activity_type, info.attempt)
                if key not in seen:
                    seen.add(key)
                    FLEET3949_CALLS.append(
                        (
                            _CALL_KINDS[info.activity_type],
                            info.task_queue,
                            info.attempt,
                            info.workflow_id or "",
                        )
                    )
                if any(fault(info) for fault in FLEET3949_DB_FAULT):
                    raise RuntimeError(
                        "database unavailable (fleet3949 injected outage)"
                    )
        if serialize_sqlite_writers:
            return _serialized(*args, **kwargs)
        return sessions(*args, **kwargs)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.checkpoint_branch_turn."
        "async_session_maker",
        _session_maker,
    )


@workflow.defn(name="MoonMind.AgentRun")
class _Fleet3949AgentRun:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        if request.correlation_id == "canceled":
            await workflow.wait_condition(lambda: False)
        turn = _turn_for(request.correlation_id)
        refs = FLEET3949_REFS[turn]
        if request.correlation_id == "provider-failure":
            return AgentRunResult(
                outputRefs=[refs["output"]],
                diagnosticsRef=refs["diagnostics"],
                summary="provider failed safely",
                failureClass="execution_error",
                providerErrorCode="provider_terminal_failed",
            )
        return AgentRunResult(
            outputRefs=[refs["output"]],
            diagnosticsRef=refs["diagnostics"],
            summary="branch turn completed",
            metadata={
                "omnigentCheckpointCapture": {
                    "omnigentSessionId": f"fresh-session-{turn}",
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
        )


@activity.defn(name="workspace.capture_checkpoint")
async def _fleet3949_capture_workspace(payload: dict) -> dict:
    turn = str(payload["artifactNamespace"]).rsplit("/", 1)[-1]
    refs = FLEET3949_REFS[turn]
    return {
        "status": "captured",
        "workspace": {
            "kind": "worktree_archive",
            "baseCommit": payload["baseCommit"],
            "archiveRef": refs["workspace"],
            "archiveDigest": "sha256:" + "c" * 64,
        },
        "diagnosticRefs": [refs["diagnostics"]],
    }


@activity.defn(name="step_checkpoint.create_v2")
async def _fleet3949_create_checkpoint(payload: dict) -> dict:
    output_ref = payload["stepOutputs"]["outputRefs"][0]
    turn = next(
        turn for turn, refs in FLEET3949_REFS.items() if refs["output"] == output_ref
    )
    hold = FLEET3949_CHECKPOINT_HOLD
    if hold:
        hold["active"] = int(hold["active"]) + 1
        hold["peak"] = max(int(hold["peak"]), int(hold["active"]))
        hold.setdefault("holding", []).append(turn)
        try:
            await hold["release"].wait()
        finally:
            hold["active"] = int(hold["active"]) - 1
    return {
        "checkpointRef": FLEET3949_REFS[turn]["checkpoint"],
        "idempotencyKey": payload["idempotencyKey"],
    }


def _production_artifacts_worker(client: Client) -> tuple[Worker, int]:
    """Build the artifacts worker the way ``worker_runtime`` does.

    Activity handlers come from the production fleet binding and the slot
    limit from the production artifacts topology. The only substitution is
    ``step_checkpoint.create_v2`` (checkpoint capture, upstream of the
    persistence handoff), whose real implementation needs a workspace.
    """

    topology = build_worker_topology(fleet=ARTIFACTS_FLEET)
    bindings = build_worker_activity_bindings(
        fleet=ARTIFACTS_FLEET,
        artifact_activities=TemporalArtifactActivities(object()),  # type: ignore[arg-type]
    )
    persistence = {
        binding.activity_type: binding
        for binding in bindings
        if binding.activity_type.startswith(_PERSISTENCE_PREFIX)
    }
    assert set(persistence) == set(_CALL_KINDS)
    assert {binding.handler for binding in persistence.values()} == set(
        checkpoint_branch_activity_handlers()
    )
    (task_queue,) = {binding.task_queue for binding in bindings}
    assert task_queue == ARTIFACTS_TASK_QUEUE
    concurrency = _worker_concurrency_kwargs(topology)
    activities = [
        binding.handler
        for binding in bindings
        if binding.activity_type != "step_checkpoint.create_v2"
    ]
    activities.append(_fleet3949_create_checkpoint)
    worker = Worker(
        client,
        task_queue=task_queue,
        activities=activities,
        **concurrency,
    )
    return worker, int(concurrency["max_concurrent_activities"])


async def _start_fleet3949_workers(
    stack: AsyncExitStack, env: WorkflowEnvironment, queue: str
) -> int:
    """Start workflow, sandbox and production artifacts workers.

    No ``checkpoint_branch.turn.*`` handler is bound on the workflow test
    queue: if new histories stopped routing to the artifacts worker, every
    persistence call would time out instead of silently landing elsewhere.
    """

    await stack.enter_async_context(
        Worker(
            env.client,
            task_queue=queue,
            workflows=[MoonMindCheckpointBranchTurnWorkflow, _Fleet3949AgentRun],
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
    artifacts_worker, slot_limit = _production_artifacts_worker(env.client)
    await stack.enter_async_context(artifacts_worker)
    return slot_limit


def _reset_fleet3949_state() -> None:
    FLEET3949_REFS.clear()
    FLEET3949_TURNS.clear()
    FLEET3949_CALLS.clear()
    FLEET3949_DB_FAULT.clear()
    FLEET3949_CHECKPOINT_HOLD.clear()


async def _wait_for(predicate: Callable[[], bool], *, attempts: int = 500) -> None:
    for _attempt in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()


async def _run_fleet3949(
    correlation_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    cancel: bool = False,
    db_fault: Callable[[activity.Info], bool] | None = None,
):
    """Run one turn whose persistence is served by the production artifacts worker."""

    from uuid import uuid4 as _uuid4

    _reset_fleet3949_state()
    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    _install_session_seam(monkeypatch)
    FLEET3949_REFS["turn-1"] = refs
    if db_fault is not None:
        FLEET3949_DB_FAULT.append(db_fault)
    queue = f"checkpoint-branch-fleet3949-{_uuid4()}"
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with AsyncExitStack() as stack:
                await _start_fleet3949_workers(stack, env, queue)
                handle = await env.client.start_workflow(
                    MoonMindCheckpointBranchTurnWorkflow.run,
                    _input(correlation_id, agent_run_workflow_id="agent-run-turn-1"),
                    id=f"checkpoint-branch-fleet3949-{correlation_id}-{_uuid4()}",
                    task_queue=queue,
                )
                if cancel:
                    await _wait_for(
                        lambda: any(
                            name == "mark_running" for name, *_rest in FLEET3949_CALLS
                        )
                    )
                    await handle.cancel()
                    with pytest.raises(WorkflowFailureError):
                        await handle.result()
                    await _wait_for(
                        lambda: any(
                            name == "terminal" for name, *_rest in FLEET3949_CALLS
                        )
                    )
                    result = None
                else:
                    result = await handle.result()
                history = await handle.fetch_history()
        return result, history, list(FLEET3949_CALLS), sessions
    finally:
        _reset_fleet3949_state()
        await engine.dispose()


def _fleet3949_scheduled_persistence(history):
    """Return (activity_type, task_queue, start_close, schedule_close, attempts, input)."""

    scheduled = []
    for event in history.events:
        if not event.HasField("activity_task_scheduled_event_attributes"):
            continue
        attrs = event.activity_task_scheduled_event_attributes
        name = attrs.activity_type.name
        if not name.startswith(_PERSISTENCE_PREFIX):
            continue
        scheduled.append(
            (
                name,
                attrs.task_queue.name,
                attrs.start_to_close_timeout,
                attrs.schedule_to_close_timeout,
                attrs.retry_policy.maximum_attempts,
                json.loads(attrs.input.payloads[0].data),
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
    for name, task_queue, start_close, schedule_close, attempts, _payload in scheduled:
        assert task_queue == ARTIFACTS_TASK_QUEUE, name
        expected_start, expected_close = _FLEET3949_TIMEOUTS[name]
        assert start_close.seconds == expected_start, name
        assert schedule_close.seconds == expected_close, name
        assert attempts == 3, name


def _assert_served_by_artifacts_worker(fleet_calls) -> None:
    assert fleet_calls
    assert all(
        task_queue == ARTIFACTS_TASK_QUEUE
        for _name, task_queue, _attempt, _workflow_id in fleet_calls
    )


async def _replay(history) -> None:
    await Replayer(
        workflows=[MoonMindCheckpointBranchTurnWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_new_success_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, history, fleet_calls, sessions = await _run_fleet3949(
        "fleet3949-success", monkeypatch, tmp_path
    )

    assert result["status"] == "checking"
    assert result["verificationPending"] is True
    assert [name for name, *_rest in fleet_calls] == ["mark_running", "terminal"]
    _assert_served_by_artifacts_worker(fleet_calls)
    _assert_fleet3949_operation_identity(history)
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.completed_at is not None
        assert turn.diagnostics["deliveryStage"] == "delivered_verification_pending"
        assert turn.diagnostics["verificationPending"] is True
    await _replay(history)


async def test_new_failure_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, history, fleet_calls, sessions = await _run_fleet3949(
        "provider-failure", monkeypatch, tmp_path
    )

    assert result["status"] in ("failed", "blocked")
    assert result["verificationPending"] is False
    assert [name for name, *_rest in fleet_calls] == ["mark_running", "terminal"]
    _assert_served_by_artifacts_worker(fleet_calls)
    _assert_fleet3949_operation_identity(history)
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.completed_at is not None
    await _replay(history)


async def test_new_cancellation_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, history, fleet_calls, sessions = await _run_fleet3949(
        "canceled", monkeypatch, tmp_path, cancel=True
    )

    assert result is None
    assert [name for name, *_rest in fleet_calls] == ["mark_running", "terminal"]
    _assert_served_by_artifacts_worker(fleet_calls)
    _assert_fleet3949_operation_identity(history)
    terminal_inputs = [
        payload
        for name, *_rest, payload in _fleet3949_scheduled_persistence(history)
        if name == "checkpoint_branch.turn.persist_terminal"
    ]
    assert [payload["outcome"] for payload in terminal_inputs] == ["canceled"]
    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, "turn-1")
        assert turn is not None
        assert turn.status == "canceled"
        assert turn.completed_at is not None
        assert turn.diagnostics["deliveryStage"] == "canceled"
    await _replay(history)


async def test_transient_terminal_retry_reuses_owned_row_on_artifacts_fleet_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient database outage on the first terminal attempt retries on
    the artifacts worker without authoring a second terminal: the retry
    reuses the same owned turn row."""

    def _first_terminal_attempt(info: activity.Info) -> bool:
        return (
            info.activity_type == "checkpoint_branch.turn.persist_terminal"
            and info.attempt == 1
        )

    result, history, fleet_calls, sessions = await _run_fleet3949(
        "fleet3949-retry", monkeypatch, tmp_path, db_fault=_first_terminal_attempt
    )

    assert result["status"] == "checking"
    terminal_calls = [call for call in fleet_calls if call[0] == "terminal"]
    assert [attempt for _n, _q, attempt, _w in terminal_calls] == [1, 2]
    _assert_served_by_artifacts_worker(fleet_calls)
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
    await _replay(history)


def _fleet3949_terminal_payload(refs: dict[str, str]) -> dict:
    return {
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
        # Two fail-closed layers reject divergent evidence: the activity-level
        # retained-artifact guard ("owned terminal artifact ... changed across
        # retry") fires before the service-level owned-row guard
        # ("immutable terminal field ..."). Accept either wording; both prove
        # the replay cannot overwrite newer evidence (MoonMind#3949).
        with pytest.raises(
            ValueError, match="(immutable terminal field|changed across retry)"
        ):
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

    The database is unavailable to every ``persist_terminal`` attempt, so the
    workflow's ``_persist_terminal`` fallback schedules
    ``checkpoint_branch.turn.persist_terminal_rejection``. Every attempt and
    the rejection are served by the production artifacts worker, the
    rejection digest is a canonical sha256, and the durable turn terminalizes
    as blocked with ``retained_evidence_rejected`` without advancing the
    branch head to a terminal checkpoint for this turn.
    """

    import re

    def _every_terminal_attempt(info: activity.Info) -> bool:
        return info.activity_type == "checkpoint_branch.turn.persist_terminal"

    result, history, fleet_calls, sessions = await _run_fleet3949(
        "fleet3949-rejected", monkeypatch, tmp_path, db_fault=_every_terminal_attempt
    )

    assert result["status"] == "blocked"
    assert result["verificationPending"] is False
    assert result["terminalDisposition"] == "retained_evidence_rejected"

    kinds = [name for name, *_rest in fleet_calls]
    assert kinds[0] == "mark_running"
    terminal_calls = [call for call in fleet_calls if call[0] == "terminal"]
    assert [attempt for _n, _q, attempt, _w in terminal_calls] == [1, 2, 3]
    assert kinds.count("terminal_rejection") == 1
    _assert_served_by_artifacts_worker(fleet_calls)
    _assert_fleet3949_operation_identity(history)
    scheduled = _fleet3949_scheduled_persistence(history)
    rejection_inputs = [
        payload
        for name, *_rest, payload in scheduled
        if name == "checkpoint_branch.turn.persist_terminal_rejection"
    ]
    assert len(rejection_inputs) == 1
    digest = rejection_inputs[0]["terminalPayloadDigest"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None
    assert "checkpoint_branch.turn.persist_terminal" in {
        name for name, *_rest in scheduled
    }

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
    await _replay(history)


async def test_control_progresses_while_artifacts_slots_are_saturated_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent turns saturate the artifacts worker's production slot limit.

    ``limit + 2`` turns run concurrently against the production artifacts
    worker. Checkpoint capture holds every artifacts slot, so the bound is
    observable: it never exceeds the configured limit. While saturated, the
    workflow worker still answers state queries and processes a
    cancellation, and the canceled turn's row stays non-terminal until its
    terminal persistence actually gets a slot (no premature terminal or
    cleanup authority). After release, the canceled turn persists
    ``canceled`` and its late checkpoint success cannot overwrite it, while
    every other turn completes with exactly one owned row.
    """

    from uuid import uuid4 as _uuid4

    slot_limit = int(
        _worker_concurrency_kwargs(build_worker_topology(fleet=ARTIFACTS_FLEET))[
            "max_concurrent_activities"
        ]
    )
    turns = [f"turn-{index}" for index in range(1, slot_limit + 3)]
    _reset_fleet3949_state()
    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    FLEET3949_REFS["turn-1"] = refs
    for index, turn in enumerate(turns[1:], start=2):
        FLEET3949_REFS[turn] = await _seed_terminal_turn(
            sessions,
            checkpoint_branch_turn.get_checkpoint_branch_artifact_service,
            branch_id=f"branch-{index}",
            branch_turn_id=turn,
        )
    _install_session_seam(monkeypatch, serialize_sqlite_writers=True)
    release = asyncio.Event()
    FLEET3949_CHECKPOINT_HOLD.update({"active": 0, "peak": 0, "release": release})
    queue = f"checkpoint-branch-fleet3949-load-{_uuid4()}"
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with AsyncExitStack() as stack:
                assert await _start_fleet3949_workers(stack, env, queue) == slot_limit
                handles: dict[str, WorkflowHandle] = {}
                for index, turn in enumerate(turns, start=1):
                    correlation_id = f"load-{turn}"
                    FLEET3949_TURNS[correlation_id] = turn
                    handles[turn] = await env.client.start_workflow(
                        MoonMindCheckpointBranchTurnWorkflow.run,
                        _input(
                            correlation_id,
                            agent_run_workflow_id=f"agent-run-{turn}",
                            branch_id=f"branch-{index}",
                            branch_turn_id=turn,
                        ),
                        id=f"checkpoint-branch-fleet3949-load-{turn}-{_uuid4()}",
                        task_queue=queue,
                    )
                try:
                    await _wait_for(
                        lambda: FLEET3949_CHECKPOINT_HOLD["active"] == slot_limit,
                        attempts=2000,
                    )
                    victim = FLEET3949_CHECKPOINT_HOLD["holding"][0]
                    states = await asyncio.gather(
                        *(
                            handle.query(MoonMindCheckpointBranchTurnWorkflow.state)
                            for handle in handles.values()
                        )
                    )
                    assert len(states) == len(turns)
                    victim_state = await handles[victim].query(
                        MoonMindCheckpointBranchTurnWorkflow.state
                    )
                    assert victim_state["phase"] == "persisting_checkpoint"

                    await handles[victim].cancel()
                    for _attempt in range(500):
                        victim_state = await handles[victim].query(
                            MoonMindCheckpointBranchTurnWorkflow.state
                        )
                        if victim_state["phase"] == "canceled":
                            break
                        await asyncio.sleep(0.01)
                    assert victim_state["phase"] == "canceled"
                    assert FLEET3949_CHECKPOINT_HOLD["active"] == slot_limit
                    async with sessions() as session:
                        row = await session.get(WorkflowCheckpointBranchTurn, victim)
                        assert row is not None
                        assert row.completed_at is None
                        assert row.status != "canceled"
                finally:
                    release.set()

                with pytest.raises(WorkflowFailureError):
                    await handles[victim].result()
                results = {
                    turn: await handle.result()
                    for turn, handle in handles.items()
                    if turn != victim
                }
                histories = {
                    turn: await handle.fetch_history()
                    for turn, handle in handles.items()
                }
        peak = FLEET3949_CHECKPOINT_HOLD["peak"]
        fleet_calls = list(FLEET3949_CALLS)
        _reset_fleet3949_state()

        assert peak == slot_limit
        assert all(result["status"] == "checking" for result in results.values())
        _assert_served_by_artifacts_worker(fleet_calls)
        for turn, history in histories.items():
            _assert_fleet3949_operation_identity(history)
            workflow_id = handles[turn].id
            kinds = [name for name, _q, _a, wf in fleet_calls if wf == workflow_id]
            assert kinds[0] == "mark_running", turn
            assert kinds.count("terminal") == 1, turn
            await _replay(history)
        victim_terminal = [
            payload["outcome"]
            for name, *_rest, payload in _fleet3949_scheduled_persistence(
                histories[victim]
            )
            if name == "checkpoint_branch.turn.persist_terminal"
        ]
        assert victim_terminal == ["canceled"]
        async with sessions() as session:
            for turn in turns:
                owned_count = await session.scalar(
                    select(func.count())
                    .select_from(WorkflowCheckpointBranchTurn)
                    .where(WorkflowCheckpointBranchTurn.branch_turn_id == turn)
                )
                assert owned_count == 1, turn
                row = await session.get(WorkflowCheckpointBranchTurn, turn)
                assert row is not None and row.completed_at is not None, turn
                if turn == victim:
                    assert row.status == "canceled"
                    assert row.diagnostics["deliveryStage"] == "canceled"
                else:
                    assert (
                        row.diagnostics["deliveryStage"]
                        == "delivered_verification_pending"
                    ), turn
    finally:
        _reset_fleet3949_state()
        await engine.dispose()
