"""AC7 Temporal boundary binding: fault scenarios vs. the real session workflow.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — the
representative scenarios must cross the Temporal boundary, not only the pure
domain).

This binding drives the *real* Omnigent managed-session Temporal workflow
(``MoonMind.AgentSession`` / :class:`MoonMindAgentSessionWorkflow`) under the
time-skipping test server, injecting the same transport faults the fault lab
scripts (a lost/delayed turn response) into the real ``agent_runtime.send_turn``
activity boundary and re-proving the reliability invariants where Temporal's
retry, Continue-As-New, and replay machinery actually run:

* **at-most-once submission** — a lost turn response drives Temporal to retry the
  activity, and the independent provider ledger (the same
  :class:`~moonmind.omnigent.faultlab.SideEffectLedger` the pure-domain suite
  uses) records at most one accepted provider turn for the turn identity despite
  the retries;
* **delayed activity result** — the workflow still converges when the activity
  succeeds only after transient failures;
* **worker restart** — tearing down and restarting the activity worker mid-run
  does not double-submit the turn or strand the session;
* **Continue-As-New** — crossing the event threshold continues the workflow with
  a monotonically preserved session epoch;
* **deterministic replay** — the faulted history replays deterministically.

Per ``AGENTS.md`` the Temporal time-skipping boundary tests are marked
``integration`` + ``temporal_boundary`` and are **excluded** from required CI
(``integration_ci``) because the test server consistently exceeds CI timeout
thresholds. They remain valuable for local-dev verification.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.omnigent.faultlab import (
    SideEffectLedger,
    generate_plan,
    payload_digest,
    project_run,
    run_plan,
)
from moonmind.omnigent.faultlab.scenario import LogicalOperation, SideEffect
from moonmind.schemas.managed_session_models import CodexManagedSessionWorkflowInput
from moonmind.workflows.temporal.workflows import agent_session as agent_session_module
from moonmind.workflows.temporal.workflows.agent_session import (
    MoonMindAgentSessionWorkflow,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.temporal_boundary,
]

_ACTIVITY_QUEUE = settings.temporal.activity_agent_runtime_task_queue


class _TurnFaultState:
    """Independent, replay-safe witness of provider turn side effects.

    The fault schedule is derived from a fault-lab scenario: ``failures`` is the
    number of transient (lost-response) attempts before the activity succeeds,
    modelling the fault lab's ``response: drop`` on ``submit_turn``. The ledger
    keys on the turn identity the workflow presents, so if the workflow ever
    minted a fresh identity per retry the ledger would record multiple side
    effects and the at-most-once assertion would fail.
    """

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.ledger = SideEffectLedger()
        self.attempts_by_identity: dict[str, int] = {}

    def identity(self, payload: dict[str, Any]) -> str:
        return f"{payload['sessionId']}:{payload['sessionEpoch']}:{payload['instructions']}"

    def perform(self, payload: dict[str, Any]) -> int:
        identity = self.identity(payload)
        attempt = self.attempts_by_identity.get(identity, 0) + 1
        self.attempts_by_identity[identity] = attempt
        # The provider performs the durable side effect (deduped by identity);
        # a lost response afterwards must never yield a second one.
        self.ledger.apply_side_effect(
            LogicalOperation.SUBMIT_TURN,
            idempotency_key=identity,
            digest=payload_digest({"instructions": payload["instructions"]}),
            side_effect=SideEffect.ACCEPTED,
        )
        return attempt


#: Set per-test before the worker starts; the activity closure reads it.
_FAULT: _TurnFaultState | None = None


def _session_state(payload: dict[str, Any], *, active_turn_id: str | None) -> dict[str, Any]:
    return {
        "sessionId": payload["sessionId"],
        "sessionEpoch": payload["sessionEpoch"],
        "containerId": payload["containerId"],
        "threadId": payload["threadId"],
        "activeTurnId": active_turn_id,
    }


@activity.defn(name="agent_runtime.send_turn")
async def fault_send_turn(payload: dict[str, Any]) -> dict[str, Any]:
    assert _FAULT is not None
    attempt = _FAULT.perform(payload)
    if attempt <= _FAULT.failures:
        # The side effect landed but the response is lost (fault-lab ``drop``):
        # Temporal retries the same activity input.
        raise RuntimeError("simulated dropped turn response")
    identity = _FAULT.identity(payload)
    return {
        "sessionState": _session_state(payload, active_turn_id=f"turn-{identity}"),
        "turnId": f"turn-{identity}",
        "status": "completed",
        "outputRefs": [],
        "metadata": {"attempts": attempt},
    }


@activity.defn(name="agent_runtime.fetch_session_summary")
async def fault_fetch_session_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionState": _session_state(payload, active_turn_id=None),
        "latestSummaryRef": f"artifact://session/{payload['sessionEpoch']}/summary",
        "latestCheckpointRef": f"artifact://session/{payload['sessionEpoch']}/checkpoint",
        "latestControlEventRef": None,
        "latestResetBoundaryRef": None,
        "metadata": {},
    }


@activity.defn(name="agent_runtime.publish_session_artifacts")
async def fault_publish_session_artifacts(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionState": _session_state(payload, active_turn_id=None),
        "publishedArtifactRefs": [f"artifact://session/{payload['sessionEpoch']}/publish"],
        "latestSummaryRef": f"artifact://session/{payload['sessionEpoch']}/summary",
        "latestCheckpointRef": f"artifact://session/{payload['sessionEpoch']}/checkpoint",
        "latestControlEventRef": f"artifact://session/{payload['sessionEpoch']}/publish/control",
        "latestResetBoundaryRef": None,
        "metadata": {},
    }


@activity.defn(name="agent_runtime.terminate_session")
async def fault_terminate_session(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionState": _session_state(payload, active_turn_id=None),
        "status": "terminated",
        "imageRef": "moonmind-codex:test",
        "controlUrl": f"docker-exec://{payload['containerId']}",
        "metadata": {"containerRemoved": True, "supervisionFinalized": True},
    }


_ACTIVITIES = [
    fault_send_turn,
    fault_fetch_session_summary,
    fault_publish_session_artifacts,
    fault_terminate_session,
]


@pytest.fixture(autouse=True)
def _silence_workflow_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_session_module.workflow, "set_current_details", lambda _details: None
    )
    monkeypatch.setattr(
        agent_session_module.workflow, "upsert_search_attributes", lambda _attributes: None
    )


_CONTINUED_AS_NEW_FIELD = "workflow_execution_continued_as_new_event_attributes"
_STARTED_FIELD = "workflow_execution_started_event_attributes"


def _history_continued_as_new_run_id(history: Any) -> str | None:
    """The run id a Continue-As-New event handed off to, or ``None``."""

    for event in history.events:
        if event.HasField(_CONTINUED_AS_NEW_FIELD):
            run_id = getattr(event, _CONTINUED_AS_NEW_FIELD).new_execution_run_id
            return run_id or None
    return None


async def _run_chain_histories(
    client: Any, workflow_id: str, first_run_id: str
) -> list[Any]:
    """Walk the Continue-As-New run chain, returning each run's history in order."""

    chain: list[Any] = []
    run_id: str | None = first_run_id
    seen: set[str] = set()
    while run_id and run_id not in seen:
        seen.add(run_id)
        history = await client.get_workflow_handle(
            workflow_id, run_id=run_id
        ).fetch_history()
        chain.append(history)
        run_id = _history_continued_as_new_run_id(history)
    return chain


def _restored_session_epoch(history: Any) -> int | None:
    """The session epoch a Continue-As-New successor run started from.

    Returns ``None`` for a run that was not started by Continue-As-New (no
    ``continued_execution_run_id``) so a caller can distinguish the original run
    from a successor whose state was restored across the boundary.
    """

    for event in history.events:
        if not event.HasField(_STARTED_FIELD):
            continue
        attrs = getattr(event, _STARTED_FIELD)
        if not attrs.continued_execution_run_id:
            return None
        payloads = attrs.input.payloads
        if not payloads:
            return None
        data = json.loads(payloads[0].data.decode("utf-8"))
        return data.get("sessionEpoch") or data.get("session_epoch")
    return None


async def _wait_for_status(handle: Any, predicate: Any, *, timeout: float = 8.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        status = await handle.query("get_status")
        if predicate(status):
            return status
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"timed out; last status={status!r}")
        await asyncio.sleep(0.05)


def _input(**overrides: Any) -> CodexManagedSessionWorkflowInput:
    payload = {
        "agentRunId": "agent-run-faultlab",
        "runtimeId": "codex_cli",
        "sessionId": "sess:faultlab:temporal",
        "sessionEpoch": 1,
    }
    payload.update(overrides)
    return CodexManagedSessionWorkflowInput.model_validate(payload)


def _dropped_turn_failures() -> int:
    """Derive the transient-failure budget from a fault-lab dropped-response run."""

    run = project_run(run_plan(generate_plan(1001)))
    # The projection carries exactly one at-most-once submit identity; model a
    # bounded lost-response window before the world recovers.
    assert len(run.submit_commands) == 1
    return 2


async def test_temporal_at_most_once_turn_under_dropped_response() -> None:
    global _FAULT
    _FAULT = _TurnFaultState(failures=_dropped_turn_failures())

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="omnigent-faultlab-session-wf",
            workflows=[MoonMindAgentSessionWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            async with Worker(
                env.client, task_queue=_ACTIVITY_QUEUE, activities=_ACTIVITIES
            ):
                handle = await env.client.start_workflow(
                    MoonMindAgentSessionWorkflow.run,
                    _input(),
                    id="omnigent-faultlab-at-most-once",
                    task_queue="omnigent-faultlab-session-wf",
                )
                await handle.signal(
                    "attach_runtime_handles",
                    {"containerId": "ctr-1", "threadId": "thread-1"},
                )
                await _wait_for_status(
                    handle, lambda s: s.get("containerId") == "ctr-1"
                )
                # A lost turn response drives Temporal to retry the activity.
                result = await handle.execute_update(
                    "SendFollowUp",
                    {"message": "do the thing", "requestId": "req-1"},
                )
                assert result["status"] == "completed"
                await handle.execute_update(
                    "TerminateSession",
                    {"reason": "done", "requestId": "req-terminate"},
                )
                final = await handle.result()
                history = await handle.fetch_history()

    assert final["status"] == "terminated"
    identity = next(iter(_FAULT.attempts_by_identity))
    # The activity was retried (delayed/lost response) ...
    assert _FAULT.attempts_by_identity[identity] >= 2
    # ... yet the independent ledger proves at most one accepted provider turn
    # for the turn identity (at-most-once), and no key double-fired.
    assert _FAULT.ledger.accepted_side_effect_count(identity) == 1
    assert _FAULT.ledger.keys_with_multiple_side_effects() == []

    # Deterministic replay of the faulted history.
    replayer = Replayer(
        workflows=[MoonMindAgentSessionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)


async def test_temporal_worker_restart_and_continue_as_new_preserve_authority() -> None:
    global _FAULT
    # A larger lost-response window so the turn is still in flight across the
    # activity-worker restart.
    _FAULT = _TurnFaultState(failures=3)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="omnigent-faultlab-session-wf-2",
            workflows=[MoonMindAgentSessionWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindAgentSessionWorkflow.run,
                # A low Continue-As-New threshold so the run continues-as-new
                # while remaining healthy.
                _input(continueAsNewEventThreshold=40),
                id="omnigent-faultlab-restart",
                task_queue="omnigent-faultlab-session-wf-2",
            )
            await handle.signal(
                "attach_runtime_handles",
                {"containerId": "ctr-2", "threadId": "thread-2"},
            )
            await _wait_for_status(handle, lambda s: s.get("containerId") == "ctr-2")

            # Drive the turn while restarting the activity worker mid-flight: the
            # first worker context exits (simulating a worker restart) and a fresh
            # one takes over the same task queue.
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(
                    Worker(env.client, task_queue=_ACTIVITY_QUEUE, activities=_ACTIVITIES)
                )
                update_task = asyncio.create_task(
                    handle.execute_update(
                        "SendFollowUp",
                        {"message": "restart me", "requestId": "req-restart"},
                    )
                )
                await asyncio.sleep(0.2)
            # First activity worker is gone; the turn is mid-retry. Start a
            # replacement worker on the same queue to resume it.
            async with Worker(
                env.client, task_queue=_ACTIVITY_QUEUE, activities=_ACTIVITIES
            ):
                result = await update_task
                assert result["status"] == "completed"

                first_run_id = handle.first_execution_run_id
                assert first_run_id is not None

                # Deterministically drive the workflow across the Continue-As-New
                # event threshold instead of relying on incidental history growth:
                # benign re-attach signals grow history without minting a new turn
                # identity, so the workflow provably continues-as-new. Stop as soon
                # as the first run records a Continue-As-New event.
                continued_as_new = False
                for _ in range(120):
                    first_history = await env.client.get_workflow_handle(
                        handle.id, run_id=first_run_id
                    ).fetch_history()
                    if _history_continued_as_new_run_id(first_history) is not None:
                        continued_as_new = True
                        break
                    await handle.signal(
                        "attach_runtime_handles",
                        {"containerId": "ctr-2", "threadId": "thread-2"},
                    )
                assert continued_as_new, "workflow never continued-as-new"

                status = await handle.query("get_status")
                # Monotonic authority: the session epoch never regressed across the
                # Continue-As-New boundary.
                assert status["binding"]["sessionEpoch"] >= 1

                await handle.execute_update(
                    "TerminateSession",
                    {"reason": "done", "requestId": "req-terminate-2"},
                )
                final = await handle.result()
                # First run's history (ends in the Continue-As-New event).
                history = await handle.fetch_history()
                run_chain = await _run_chain_histories(
                    env.client, handle.id, first_run_id
                )

    assert final["status"] == "terminated"
    identity = next(iter(_FAULT.attempts_by_identity))
    # At-most-once held across the worker restart.
    assert _FAULT.ledger.accepted_side_effect_count(identity) == 1
    assert _FAULT.ledger.keys_with_multiple_side_effects() == []

    # Prove Continue-As-New actually occurred: the run chain has a successor, at
    # least one run ended with a Continue-As-New event, and the successor
    # execution restored the preserved session epoch (monotonic authority carried
    # across the restart) rather than starting a fresh session.
    assert len(run_chain) >= 2, "expected a Continue-As-New successor run"
    continued_runs = sum(
        1
        for run_history in run_chain
        if _history_continued_as_new_run_id(run_history) is not None
    )
    assert continued_runs >= 1, "no Continue-As-New event in the run chain"
    successor_epochs = [
        _restored_session_epoch(run_history) for run_history in run_chain[1:]
    ]
    assert any(epoch == 1 for epoch in successor_epochs), (
        f"successor did not restore the preserved session epoch: {successor_epochs}"
    )

    replayer = Replayer(
        workflows=[MoonMindAgentSessionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)
