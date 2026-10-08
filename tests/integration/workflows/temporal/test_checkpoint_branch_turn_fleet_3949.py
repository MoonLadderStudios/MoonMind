"""Artifacts-fleet handoff ownership journeys (MoonLadderStudios/MoonMind#3949).

Time-skipping Temporal journeys that execute checkpoint persistence through
the production artifacts-fleet registration: the artifacts worker's handlers
come from ``build_worker_activity_bindings(fleet="artifacts")`` exactly as
worker startup resolves them, and the workflow queue carries no
``checkpoint_branch.turn.*`` handler. Only external effects (the AgentRun
child and sandbox workspace capture) are test doubles; an activity
interceptor observes deliveries and injects terminal faults without
replacing a handler. The journeys assert durable row state for success,
provider failure, cancellation, terminal rejection and transient retry, and
show that duplicate delivery reuses the owned row, divergent replays cannot
overwrite newer evidence, stale running claims are rejected, a database
outage fails closed without partial writes or premature cleanup, and
artifacts-worker slot saturation does not stall workflow control.

The whole module runs in about twenty seconds, so it is selected for
required CI (``integration_ci``) like ``test_checkpoint_branch_turn_execution``,
which owns the shared database/input helpers.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest
from sqlalchemy import func, select, text
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
    Replayer,
    UnsandboxedWorkflowRunner,
    Worker,
)

from api_service.db.models import (
    TemporalArtifact,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRemediationLink,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
)
from moonmind.workflows import get_temporal_artifact_repository
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalIntegrationActivities,
    TemporalPlanActivities,
    TemporalReviewActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workers import build_worker_activity_bindings
from moonmind.workflows.temporal.workflow_registry import (
    checkpoint_branch_activity_handlers,
)
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    MoonMindCheckpointBranchTurnWorkflow,
    mark_checkpoint_branch_turn_running,
    persist_checkpoint_branch_turn_terminal,
)
from tests.integration.workflows.temporal.test_checkpoint_branch_turn_execution import (
    _input,
    _terminal_activity_database,
)
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

FLEET3949_REFS: dict[str, str] = {}
FLEET3949_CALLS: list[tuple[str, str, int, object]] = []
FLEET3949_FAIL_TERMINAL_ONCE = False
FLEET3949_FAIL_TERMINAL_ALWAYS = False
FLEET3949_HOLD_MARK_RUNNING: asyncio.Event | None = None
FLEET3949_MARK_RUNNING_ENTERED: asyncio.Event | None = None
FLEET3949_RELEASE_MARK_RUNNING: asyncio.Event | None = None

_FLEET3949_OBSERVED = {
    "checkpoint_branch.turn.mark_running": ("mark_running", "agentRunWorkflowId"),
    "checkpoint_branch.turn.persist_terminal": ("terminal", "outcome"),
    "checkpoint_branch.turn.persist_terminal_rejection": (
        "terminal_rejection",
        "terminalPayloadDigest",
    ),
}


class _Fleet3949ActivityObserver(ActivityInboundInterceptor):
    """Records persistence deliveries and injects terminal faults.

    The interceptor wraps whatever handler the production registration
    bound; it never replaces one, so the code under test is the registered
    artifacts-fleet handler itself.
    """

    async def execute_activity(self, input: ExecuteActivityInput) -> object:
        info = activity.info()
        observed = _FLEET3949_OBSERVED.get(info.activity_type)
        if observed is not None:
            kind, value_key = observed
            payload = input.args[0]
            FLEET3949_CALLS.append(
                (kind, info.task_queue, info.attempt, payload.get(value_key))
            )
            if kind == "mark_running" and FLEET3949_MARK_RUNNING_ENTERED is not None:
                FLEET3949_MARK_RUNNING_ENTERED.set()
                assert FLEET3949_RELEASE_MARK_RUNNING is not None
                await FLEET3949_RELEASE_MARK_RUNNING.wait()
            if kind == "terminal":
                global FLEET3949_FAIL_TERMINAL_ONCE
                if FLEET3949_FAIL_TERMINAL_ALWAYS:
                    raise RuntimeError("injected persistent fleet3949 terminal failure")
                if FLEET3949_FAIL_TERMINAL_ONCE and info.attempt == 1:
                    FLEET3949_FAIL_TERMINAL_ONCE = False
                    raise RuntimeError("injected transient fleet3949 terminal failure")
            hold = FLEET3949_HOLD_MARK_RUNNING
            if kind == "mark_running" and hold is not None:
                # Occupy one artifacts slot until the test releases it, then
                # deliver the handoff late through the production handler.
                await hold.wait()
                try:
                    result = await super().execute_activity(input)
                except Exception as exc:
                    FLEET3949_CALLS.append(
                        ("mark_running_late", info.task_queue, info.attempt, repr(exc))
                    )
                    raise
                FLEET3949_CALLS.append(
                    ("mark_running_late", info.task_queue, info.attempt, "applied")
                )
                return result
        return await super().execute_activity(input)


class _Fleet3949Observer(Interceptor):
    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _Fleet3949ActivityObserver(next)


def _production_artifacts_fleet_activities(tmp_path, artifact_service) -> list:
    """Resolve the artifacts worker's handlers exactly as worker startup does.

    ``build_worker_activity_bindings(fleet=artifacts)`` applies the fleet
    capability check and binds the catalog routes, including the three
    checkpoint persistence handlers. Implementations for the fleet's other
    families are constructed like production but never invoked here. The one
    replaced binding is ``step_checkpoint.create_v2``: it returns the
    fixture's seeded, fully formed branch checkpoint instead of rebuilding
    one from a live Omnigent capture (its own behavior is covered by
    ``test_step_checkpoint_activities``).
    """

    bindings = build_worker_activity_bindings(
        fleet=ARTIFACTS_FLEET,
        artifact_activities=TemporalArtifactActivities(artifact_service),
        plan_activities=TemporalPlanActivities(artifact_service=artifact_service),
        skill_activities=TemporalSkillActivities(
            dispatcher=SkillActivityDispatcher(), artifact_service=artifact_service
        ),
        sandbox_activities=TemporalSandboxActivities(
            artifact_service=artifact_service,
            workspace_root=tmp_path / "fleet3949-workspaces",
        ),
        integration_activities=TemporalIntegrationActivities(
            artifact_service=artifact_service
        ),
        review_activities=TemporalReviewActivities(),
    )
    handlers = {binding.activity_type: binding.handler for binding in bindings}
    assert all(binding.task_queue == ARTIFACTS_TASK_QUEUE for binding in bindings)
    for handler in checkpoint_branch_activity_handlers():
        name = activity._Definition.must_from_callable(handler).name
        assert handlers[name] is handler, f"{name} is not the production handler"
    handlers["step_checkpoint.create_v2"] = _fleet3949_create_checkpoint
    return list(handlers.values())


@activity.defn(name="step_checkpoint.create_v2")
async def _fleet3949_create_checkpoint(payload: dict) -> dict:
    return {
        "checkpointRef": FLEET3949_REFS["checkpoint"],
        "idempotencyKey": payload["idempotencyKey"],
    }


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


async def _run_fleet3949(
    correlation_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    *,
    cancel: bool = False,
    cancel_at_activity_entry: bool = False,
    artifacts_slots: int | None = None,
):
    """Run one turn against the production artifacts-fleet registration.

    No ``checkpoint_branch.turn.*`` handler is bound on the workflow test
    queue: if new histories stopped routing to the artifacts fleet, every
    persistence call would fail instead of silently landing elsewhere. Only
    the external effects (AgentRun child and sandbox workspace capture) are
    test doubles.
    """

    from uuid import uuid4 as _uuid4

    global FLEET3949_MARK_RUNNING_ENTERED, FLEET3949_RELEASE_MARK_RUNNING

    FLEET3949_REFS.clear()
    FLEET3949_CALLS.clear()
    if cancel_at_activity_entry:
        FLEET3949_MARK_RUNNING_ENTERED = asyncio.Event()
        FLEET3949_RELEASE_MARK_RUNNING = asyncio.Event()
    engine, sessions, refs = await _terminal_activity_database(tmp_path, monkeypatch)
    FLEET3949_REFS.update(refs)
    queue = f"checkpoint-branch-fleet3949-{_uuid4()}"
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with AsyncExitStack() as stack:
                artifact_session = await stack.enter_async_context(sessions())
                artifact_service = TemporalArtifactService(
                    get_temporal_artifact_repository(artifact_session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
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
                        activities=_production_artifacts_fleet_activities(
                            tmp_path, artifact_service
                        ),
                        interceptors=[_Fleet3949Observer()],
                        # The production slot bound
                        # (TEMPORAL_ARTIFACTS_WORKER_CONCURRENCY).
                        **(
                            {"max_concurrent_activities": artifacts_slots}
                            if artifacts_slots is not None
                            else {}
                        ),
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
                    if cancel_at_activity_entry:
                        assert FLEET3949_MARK_RUNNING_ENTERED is not None
                        await asyncio.wait_for(
                            FLEET3949_MARK_RUNNING_ENTERED.wait(), timeout=1
                        )
                    else:
                        for _attempt in range(100):
                            if any(
                                name == "mark_running"
                                for name, *_rest in FLEET3949_CALLS
                            ):
                                break
                            await asyncio.sleep(0.01)
                    assert any(
                        name == "mark_running" for name, *_rest in FLEET3949_CALLS
                    )
                    if (
                        not cancel_at_activity_entry
                        and FLEET3949_HOLD_MARK_RUNNING is None
                    ):
                        # Cancel the parent after its real persistence handoff.
                        # Racing the test server's
                        # activity completion can reject the cancel command
                        # with ACTIVITY_UNKNOWN. The saturated-slot journey
                        # retains cancellation while the handoff is pending.
                        for _attempt in range(100):
                            state = await handle.query("checkpoint_branch.turn.state")
                            if state["phase"] == "running":
                                break
                            await asyncio.sleep(0.01)
                        assert state["phase"] == "running", state
                    await handle.cancel()
                    if cancel_at_activity_entry:
                        assert FLEET3949_RELEASE_MARK_RUNNING is not None
                        FLEET3949_RELEASE_MARK_RUNNING.set()
                    with pytest.raises(WorkflowFailureError):
                        await handle.result()
                    for _attempt in range(200):
                        if any(name == "terminal" for name, *_rest in FLEET3949_CALLS):
                            break
                        await asyncio.sleep(0.01)
                    assert any(name == "terminal" for name, *_rest in FLEET3949_CALLS)
                    if FLEET3949_HOLD_MARK_RUNNING is not None:
                        FLEET3949_HOLD_MARK_RUNNING.set()
                        for _attempt in range(200):
                            if any(
                                name == "mark_running_late"
                                for name, *_rest in FLEET3949_CALLS
                            ):
                                break
                            await asyncio.sleep(0.01)
                    result = None
                else:
                    result = await handle.result()
                history = await handle.fetch_history()
        return result, history, list(FLEET3949_CALLS), sessions
    finally:
        FLEET3949_REFS.clear()
        FLEET3949_MARK_RUNNING_ENTERED = None
        FLEET3949_RELEASE_MARK_RUNNING = None
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


@pytest.mark.parametrize(
    "cancel_at_activity_entry", [False, True], ids=["post-handoff", "activity-entry"]
)
async def test_new_cancellation_reaches_artifacts_fleet_with_real_handlers_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch, cancel_at_activity_entry: bool
) -> None:
    result, history, fleet_calls, sessions = await _run_fleet3949(
        "canceled",
        monkeypatch,
        tmp_path,
        cancel=True,
        cancel_at_activity_entry=cancel_at_activity_entry,
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
    assert any(
        event.HasField("workflow_execution_cancel_requested_event_attributes")
        for event in history.events
    )
    assert history.events[-1].HasField("workflow_execution_canceled_event_attributes")
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


async def test_saturated_artifacts_slot_keeps_cancellation_and_ordering_3949(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stuck persistence call does not stall control or cancellation evidence.

    The artifacts worker runs with a bounded slot count and one slot stays
    occupied by a ``mark_running`` delivery that has not finished. The
    workflow worker still processes the cancellation request, the canceled
    terminal is persisted through the remaining artifacts slot, and the late
    running handoff delivered afterwards cannot overwrite the cancellation.
    """

    global FLEET3949_HOLD_MARK_RUNNING
    FLEET3949_HOLD_MARK_RUNNING = asyncio.Event()
    try:
        result, history, fleet_calls, sessions = await _run_fleet3949(
            "canceled", monkeypatch, tmp_path, cancel=True, artifacts_slots=2
        )
    finally:
        FLEET3949_HOLD_MARK_RUNNING = None

    assert result is None
    assert [name for name, _queue, _attempt, _value in fleet_calls][:3] == [
        "mark_running",
        "terminal",
        "mark_running_late",
    ]
    assert fleet_calls[1][3] == "canceled"
    assert all(
        task_queue == ARTIFACTS_TASK_QUEUE
        for _name, task_queue, _attempt, _value in fleet_calls
    )
    assert any(
        event.HasField("workflow_execution_cancel_requested_event_attributes")
        for event in history.events
    )
    assert history.events[-1].HasField("workflow_execution_canceled_event_attributes")
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


@pytest.mark.parametrize(
    "outcome,expected_status,expected_branch_state",
    [("canceled", "canceled", "blocked"), ("succeeded", "checking", "active")],
)
async def test_overlapping_running_claim_preserves_terminal_commit_3949(
    control_plane_postgres_url,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    expected_status: str,
    expected_branch_state: str,
) -> None:
    """A real row-lock wait cannot turn accepted terminal evidence into running.

    Unlike the slot-saturation journey, the running transaction starts before
    finalization commits. Preloading its identity map also requires the lock
    boundary to refresh stale ORM objects after the database lock is released.
    """

    monkeypatch.setenv("MOONMIND_TEST_POSTGRES_URL", control_plane_postgres_url)
    tables = [
        model.__table__
        for model in (
            TemporalArtifact,
            TemporalExecutionCanonicalRecord,
            WorkflowCheckpointBranch,
            TemporalExecutionRemediationLink,
            WorkflowCheckpointBranchGitBinding,
            WorkflowCheckpointBranchTurn,
            WorkflowCheckpointBranchArtifact,
        )
    ]
    async with isolated_postgres(tables) as sessions:
        identity = {
            "workflow_id": "source-workflow",
            "branch_id": "branch-1",
            "branch_turn_id": "turn-1",
        }
        async with sessions() as seed:
            seed.add(
                TemporalExecutionCanonicalRecord(
                    workflow_id=identity["workflow_id"],
                    run_id="source-run",
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                    entry="api",
                )
            )
            await seed.commit()
            service = CheckpointBranchService(seed)
            await service.create_branch_graph(
                {
                    "branchId": identity["branch_id"],
                    "label": "Concurrent terminal handoff",
                    "branchTurnId": identity["branch_turn_id"],
                    "source": {
                        "workflowId": identity["workflow_id"],
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
            await service.claim_turn_execution(
                **identity,
                context_bundle_ref="artifact://launch/context",
                step_execution_manifest_ref="artifact://launch/manifest",
                diagnostics_ref="artifact://launch/diagnostics",
                launch_idempotency_key=build_branch_turn_launch_idempotency_key(
                    **identity
                ),
                runtime_agent_run_id="agent-run-turn-1",
                created_step_execution_id="branch-owner:run:implement:execution:1",
                agent_request_ref="artifact://launch/request",
                execution_workflow_id="checkpoint-branch-turn:turn-1",
            )
            await seed.commit()

        async with sessions() as running, sessions() as terminal, sessions() as observer:
            # Hold strong references so SQLAlchemy's identity map keeps the
            # pre-terminal values even after the other transaction commits.
            stale_branch = await running.get(WorkflowCheckpointBranch, "branch-1")
            stale_turn = await running.get(WorkflowCheckpointBranchTurn, "turn-1")
            assert stale_branch.state == "preparing"
            assert stale_turn.completed_at is None
            running_pid = await running.scalar(text("SELECT pg_backend_pid()"))
            terminal_pid = await terminal.scalar(text("SELECT pg_backend_pid()"))
            terminal_service = CheckpointBranchService(terminal)
            branch, turn = await terminal_service.lock_turn_execution(**identity)

            async def deliver_running():
                await CheckpointBranchService(running).mark_turn_running(
                    **identity, runtime_agent_run_id="agent-run-turn-1"
                )
                await running.commit()

            handoff = asyncio.create_task(deliver_running())
            try:
                # Observe an actual PostgreSQL lock wait, rather than assuming
                # the transactions overlap after a fixed scheduling delay.
                async with asyncio.timeout(10):
                    while True:
                        if handoff.done():
                            # Propagate a completed task's exception before failing.
                            handoff.result()
                            pytest.fail("running handoff escaped the terminal row lock")
                        blockers = await observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": running_pid}
                        )
                        if terminal_pid in blockers:
                            break
                        await asyncio.sleep(0.01)

                await terminal_service.finalize_turn_execution(
                    **identity,
                    outcome=outcome,
                    agent_result_ref="artifact://terminal/result",
                    diagnostics_ref="artifact://terminal/diagnostics",
                    checkpoint_ref="artifact://terminal/checkpoint",
                    checkpoint_digest="sha256:" + "c" * 64,
                    terminal_ref="artifact://terminal/evidence",
                )
                await terminal.commit()
                expected_diagnostics = dict(turn.diagnostics)
                expected_artifacts = dict(branch.artifact_refs)
                expected_completed_at = turn.completed_at
                expected_version = branch.current_head_version
                await asyncio.wait_for(handoff, timeout=10)
            finally:
                if not handoff.done():
                    handoff.cancel()
                await asyncio.gather(handoff, return_exceptions=True)

        async with sessions() as check:
            saved_turn = await check.get(WorkflowCheckpointBranchTurn, "turn-1")
            saved_branch = await check.get(WorkflowCheckpointBranch, "branch-1")
            assert saved_turn.status == expected_status
            assert saved_turn.completed_at == expected_completed_at
            assert saved_turn.started_at is None
            assert saved_turn.runtime_agent_run_id == "agent-run-turn-1"
            assert saved_turn.diagnostics == expected_diagnostics
            assert saved_branch.state == expected_branch_state
            assert (
                saved_branch.current_head_checkpoint_ref
                == "artifact://terminal/checkpoint"
            )
            assert saved_branch.current_head_checkpoint_digest == "sha256:" + "c" * 64
            assert saved_branch.current_head_version == expected_version
            assert saved_branch.artifact_refs == expected_artifacts


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
        task_queue == ARTIFACTS_TASK_QUEUE for _n, task_queue, _a, _v in terminal_calls
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
    rejection_calls = [call for call in fleet_calls if call[0] == "terminal_rejection"]
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
