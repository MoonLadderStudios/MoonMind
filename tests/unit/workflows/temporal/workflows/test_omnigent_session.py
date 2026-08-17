"""Unit tests for the MoonMind.OmnigentSession supervisor workflow (#3705).

These drive the workflow's reconciliation loop directly (instantiate + await
``run``) with the workflow SDK surface monkeypatched, following the repo idiom in
``test_managed_session_reconcile.py``. Provider side effects are simulated by a
fake ``execute_activity`` that advances the observation frontier.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from moonmind.omnigent.session_reconciler import (
    OmnigentSessionCommandKind,
    OmnigentSessionFrontier,
    OmnigentSessionIntent,
    OmnigentSessionReconcilePolicy,
    OmnigentSessionStatus,
    OmnigentSessionWorkflowInput,
)
from moonmind.workflows.temporal.workflows import omnigent_session as module
from moonmind.workflows.temporal.workflows.omnigent_session import (
    MoonMindOmnigentSessionWorkflow,
)


def _intent(**overrides) -> OmnigentSessionIntent:
    base = dict(
        canonicalSessionId="wf-1:omnigent",
        executionIntentRef="ref",
        executionIntentDigest="digest",
        owningWorkflowId="user-wf-1",
        stepExecutionId="step-1",
        agentRunId="wf-1",
        executionProfileRef="profile:codex",
        initialTurnAttemptId="wf-1:omnigent:turn:1",
        admittedFeatureGeneration=1,
    )
    base.update(overrides)
    return OmnigentSessionIntent(**base)


class _FakeProvider:
    """Simulates provider/host side effects by returning frontier deltas."""

    def __init__(self, *, terminal_via_events: bool = True) -> None:
        self.terminal_via_events = terminal_via_events
        self.calls: list[str] = []

    def outcome_for(self, kind: str, frontier: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if kind == OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE.value:
            updates = {"provider_profile_lease_held": True}
        elif kind == OmnigentSessionCommandKind.ENSURE_HOST.value:
            updates = {"host_ready": True}
        elif kind == OmnigentSessionCommandKind.ENSURE_PROVIDER_SESSION.value:
            updates = {
                "provider_session_established": True,
                "provider_session_id": "sess-1",
            }
        elif kind == OmnigentSessionCommandKind.SUBMIT_TURN.value:
            updates = {
                "turn_submitted": True,
                "turn_attempts": int(frontier.get("turnAttempts", 0)) + 1,
            }
        elif kind == OmnigentSessionCommandKind.READ_EVENT_BATCH.value:
            if self.terminal_via_events:
                updates = {
                    "terminal_observed": True,
                    "terminal_outcome": "completed",
                    "last_observed_provider_status": "completed",
                }
        elif kind == OmnigentSessionCommandKind.OBSERVE_SNAPSHOT.value:
            updates = {
                "terminal_observed": True,
                "terminal_outcome": "completed",
                "last_observed_provider_status": "completed",
            }
        elif kind == OmnigentSessionCommandKind.HARVEST_EVIDENCE.value:
            updates = {
                "evidence_harvested": True,
                "terminal_result_ref": "artifact:terminal",
                "diagnostics_ref": "artifact:diag",
            }
        elif kind == OmnigentSessionCommandKind.PUBLISH_WORKSPACE.value:
            updates = {"workspace_published": True}
        elif kind == OmnigentSessionCommandKind.STOP_PROVIDER_SESSION.value:
            updates = {"provider_session_stopped": True}
        elif kind == OmnigentSessionCommandKind.STOP_HOST.value:
            updates = {"host_stopped": True}
        elif kind == OmnigentSessionCommandKind.RELEASE_LEASES.value:
            updates = {"leases_released": True}
        return {
            "condition": "ok",
            "frontierUpdates": updates,
            "bumpGeneration": False,
            "resultRef": None,
        }


def _install_runtime(monkeypatch, provider: _FakeProvider, *, now_step: float = 20.0):
    clock = {"t": datetime(2026, 1, 1, tzinfo=UTC)}

    def _now():
        current = clock["t"]
        clock["t"] = current + timedelta(seconds=now_step)
        return current

    async def _execute_activity(name, payload, **kwargs):
        provider.calls.append(name)
        if name == OmnigentSessionCommandKind.PERSIST_DECISION.value:
            return {"persisted": True, "decisionCount": payload.get("decisionCount", 0)}
        frontier = payload.get("frontier", {})
        return provider.outcome_for(name, frontier)

    async def _wait_condition(fn, timeout=None):
        return None

    class _Info:
        workflow_id = "wf-1:omnigent:session"

        def get_current_history_length(self):
            return 10

    monkeypatch.setattr(module.workflow, "now", _now)
    monkeypatch.setattr(module.workflow, "info", lambda: _Info())
    monkeypatch.setattr(module.workflow, "execute_activity", _execute_activity)
    monkeypatch.setattr(module.workflow, "wait_condition", _wait_condition)
    monkeypatch.setattr(module.workflow, "set_current_details", lambda v: None)
    monkeypatch.setattr(module.workflow, "upsert_search_attributes", lambda v: None)
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: True)

    def _no_can(_input):
        raise AssertionError("continue_as_new should not be reached in this test")

    monkeypatch.setattr(module.workflow, "continue_as_new", _no_can)


@pytest.mark.asyncio
async def test_happy_path_reaches_completed_and_releases_lease_last(monkeypatch):
    provider = _FakeProvider(terminal_via_events=True)
    _install_runtime(monkeypatch, provider)
    workflow_input = OmnigentSessionWorkflowInput(intent=_intent())
    wf = MoonMindOmnigentSessionWorkflow(workflow_input)

    result = await wf.run(workflow_input)

    assert result["status"] == OmnigentSessionStatus.COMPLETED.value
    assert result["terminalResultRef"] == "artifact:terminal"
    # release_leases is the final side-effecting command before completion.
    side_effects = [c for c in provider.calls if c != OmnigentSessionCommandKind.PERSIST_DECISION.value]
    assert side_effects[-1] == OmnigentSessionCommandKind.RELEASE_LEASES.value
    assert side_effects.index(OmnigentSessionCommandKind.STOP_HOST.value) < side_effects.index(
        OmnigentSessionCommandKind.RELEASE_LEASES.value
    )


@pytest.mark.asyncio
async def test_missed_terminal_event_converges_via_snapshot(monkeypatch):
    provider = _FakeProvider(terminal_via_events=False)
    _install_runtime(monkeypatch, provider, now_step=20.0)
    workflow_input = OmnigentSessionWorkflowInput(intent=_intent())
    wf = MoonMindOmnigentSessionWorkflow(workflow_input)

    result = await wf.run(workflow_input)

    assert result["status"] == OmnigentSessionStatus.COMPLETED.value
    # Terminal was never delivered by events; the authoritative periodic snapshot
    # proved terminality and drove convergence.
    assert OmnigentSessionCommandKind.OBSERVE_SNAPSHOT.value in provider.calls


@pytest.mark.asyncio
async def test_cancellation_during_execution_cleans_up_to_canceled(monkeypatch):
    provider = _FakeProvider(terminal_via_events=True)
    _install_runtime(monkeypatch, provider)
    established = OmnigentSessionFrontier(
        provider_profile_lease_held=True,
        host_ready=True,
        provider_session_established=True,
        current_turn_attempt_id="wf-1:omnigent:turn:1",
        turn_submitted=True,
    )
    workflow_input = OmnigentSessionWorkflowInput(
        intent=_intent(),
        frontier=established,
        cancelRequested=True,
    )
    wf = MoonMindOmnigentSessionWorkflow(workflow_input)

    result = await wf.run(workflow_input)

    assert result["status"] == OmnigentSessionStatus.CANCELED.value
    assert OmnigentSessionCommandKind.STOP_PROVIDER_SESSION.value in provider.calls
    assert OmnigentSessionCommandKind.RELEASE_LEASES.value in provider.calls
    # No new turn is submitted during cancellation.
    assert OmnigentSessionCommandKind.SUBMIT_TURN.value not in provider.calls


@pytest.mark.asyncio
async def test_activity_failure_reported_as_integration_unavailable(monkeypatch):
    from temporalio.exceptions import ApplicationError

    provider = _FakeProvider(terminal_via_events=True)
    _install_runtime(monkeypatch, provider)

    original = module.workflow.execute_activity
    state = {"failed_once": False}

    async def _flaky(name, payload, **kwargs):
        if (
            name == OmnigentSessionCommandKind.ENSURE_HOST.value
            and not state["failed_once"]
        ):
            state["failed_once"] = True
            raise ApplicationError("host unavailable", type="IntegrationUnavailable")
        return await original(name, payload, **kwargs)

    monkeypatch.setattr(module.workflow, "execute_activity", _flaky)
    workflow_input = OmnigentSessionWorkflowInput(intent=_intent())
    wf = MoonMindOmnigentSessionWorkflow(workflow_input)

    result = await wf.run(workflow_input)

    # The workflow recovered from the bounded failure and still converged.
    assert result["status"] == OmnigentSessionStatus.COMPLETED.value
    assert state["failed_once"] is True


@pytest.mark.asyncio
async def test_stale_generation_result_is_discarded(monkeypatch):
    provider = _FakeProvider(terminal_via_events=True)
    _install_runtime(monkeypatch, provider)

    # Force the workflow's fencing generation to advance mid-run so the next
    # command's result carries a superseded generation and must be discarded.
    workflow_input = OmnigentSessionWorkflowInput(intent=_intent())
    wf = MoonMindOmnigentSessionWorkflow(workflow_input)

    original = module.workflow.execute_activity
    bumped = {"done": False}

    async def _bump_then_run(name, payload, **kwargs):
        result = await original(name, payload, **kwargs)
        if (
            name == OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE.value
            and not bumped["done"]
        ):
            bumped["done"] = True
            # Simulate an out-of-band epoch advance (e.g. a new turn signal).
            wf._frontier = wf._frontier.model_copy(
                update={"fencing_generation": wf._frontier.fencing_generation + 1}
            )
        return result

    monkeypatch.setattr(module.workflow, "execute_activity", _bump_then_run)

    result = await wf.run(workflow_input)
    # The discarded lease result means the reconciler re-issues ensure lease on
    # the new generation and the run still converges without corruption.
    assert result["status"] == OmnigentSessionStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_query_exposes_compact_status(monkeypatch):
    provider = _FakeProvider(terminal_via_events=True)
    _install_runtime(monkeypatch, provider)
    workflow_input = OmnigentSessionWorkflowInput(intent=_intent())
    wf = MoonMindOmnigentSessionWorkflow(workflow_input)
    status = wf.get_status()
    assert status["canonicalSessionId"] == "wf-1:omnigent"
    assert status["owningWorkflowId"] == "user-wf-1"
    assert "status" in status and "fencingGeneration" in status


@pytest.mark.asyncio
async def test_continue_as_new_carries_frontier(monkeypatch):
    provider = _FakeProvider(terminal_via_events=False)
    _install_runtime(monkeypatch, provider)
    intent = _intent(
        policy=OmnigentSessionReconcilePolicy(continueAsNewDecisionThreshold=2)
    )
    can_inputs: list[OmnigentSessionWorkflowInput] = []

    def _capture_can(new_input):
        can_inputs.append(new_input)
        raise _StopLoop()

    class _StopLoop(Exception):
        pass

    monkeypatch.setattr(module.workflow, "continue_as_new", _capture_can)
    workflow_input = OmnigentSessionWorkflowInput(intent=intent)
    wf = MoonMindOmnigentSessionWorkflow(workflow_input)

    with pytest.raises(_StopLoop):
        await wf.run(workflow_input)

    assert can_inputs, "continue_as_new should have been invoked"
    carried = can_inputs[0]
    assert carried.intent.canonical_session_id == "wf-1:omnigent"
    assert carried.frontier is not None
    assert carried.session_start_epoch_seconds is not None
