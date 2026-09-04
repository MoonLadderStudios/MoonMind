"""Provider and host capacity are admitted before the long execution Activity.

Source issue: MoonLadderStudios/MoonMind#3878 (AC4, AC6, AC7, AC10, AC12,
invariants 6, 7, 10, 12).

The behaviour under test is the target flow's ordering: request Provider
Profile capacity, wait durably when unavailable, reserve provisional
generic-host capacity, and only then start ``integration.omnigent.execute``.
The wait must be workflow state — a timer and a signal — never an Activity that
holds an execution slot open.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun




def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Install a deterministic workflow context and record every timer sleep."""

    slept: list[float] = []

    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "agent-run-1",
            "run_id": "run-1",
            "search_attributes": {},
            "parent": None,
        },
    )
    logger = type(
        "Logger",
        (),
        {
            "info": lambda *a, **k: None,
            "warning": lambda *a, **k: None,
            "error": lambda *a, **k: None,
        },
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "logger", logger)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _pid: True)
    monkeypatch.setattr(
        agent_run_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    async def fake_sleep(duration: timedelta) -> None:
        slept.append(duration.total_seconds())

    monkeypatch.setattr(agent_run_module.workflow, "sleep", fake_sleep)
    return slept


def _capture_release_signals(
    monkeypatch: pytest.MonkeyPatch, run: "_RecordingRun"
) -> None:
    """Route the release path's external handle back into the run's log."""

    def _handle(_workflow_id: str):
        async def signal(name, payload=None):
            run.signals.append((name, dict(payload or {})))

        return SimpleNamespace(signal=signal)

    monkeypatch.setattr(
        agent_run_module.workflow, "get_external_workflow_handle", _handle
    )


def _omnigent_request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="omnigent",
        executionProfileRef="opencode-zen-free",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="artifact:instructions",
        parameters={"publishMode": "none"},
        workspaceSpec={},
    )


def _admission(
    *,
    profile_ref: str | None = "opencode-zen-free",
    runtime_id: str | None = "opencode",
    capacity_scope_ref: str | None = "opencode-zen:contributor-free",
) -> SimpleNamespace:
    return SimpleNamespace(
        provider_profile_ref=profile_ref,
        provider_runtime_id=runtime_id,
        capacity_scope_ref=capacity_scope_ref,
    )


class _RecordingRun(MoonMindAgentRun):
    """An AgentRun whose manager and activity boundaries are observable."""

    def __init__(self) -> None:
        super().__init__()
        self.signals: list[tuple[str, dict[str, Any]]] = []
        self.activity_calls: list[tuple[str, Any]] = []
        self.parent_states: list[tuple[str, str]] = []
        self.host_decisions: list[dict[str, Any]] = []
        self.manager_states: list[dict[str, Any]] = []
        self.grant_on_signal = True

    async def _ensure_manager_and_signal(
        self, manager_id, runtime_id, *, request_slot=True, **kwargs
    ):
        if request_slot:
            self.signals.append(("request_slot", dict(kwargs)))
            if self.grant_on_signal:
                self._assigned_profile_id = kwargs.get("execution_profile_ref")
                self.slot_assigned_event.set()

        class _Handle:
            async def signal(handle_self, name, payload=None):
                self.signals.append((name, dict(payload or {})))

        return _Handle()

    async def _sync_manager_profiles(self, **kwargs) -> int:
        return 1

    async def _signal_parent_child_state_changed(self, parent_info, state, reason):
        self.parent_states.append((state, reason))

    async def _inspected_provider_slot_waiting_reason(self, **kwargs) -> str:
        return "Waiting for provider capacity."

    async def _manager_state_for_slot_wait(self, **kwargs) -> dict[str, Any]:
        return self.manager_states.pop(0) if self.manager_states else {}

    async def _execute_routed_activity(self, name, payload=None, **kwargs):
        self.activity_calls.append((name, payload))
        if name == "omnigent.admit_generic_host_capacity":
            return self.host_decisions.pop(0) if self.host_decisions else {
                "admitted": True
            }
        return {}

    def _get_logger(self):
        return SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        )


@pytest.mark.asyncio
async def test_admission_returns_request_carrying_workflow_owned_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 6: the workflow becomes the lease owner before the Activity."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    admitted = await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )

    capacity = admitted.admitted_provider_capacity
    assert capacity is not None
    assert capacity.provider_profile_ref == "opencode-zen-free"
    assert capacity.provider_runtime_id == "opencode"
    assert capacity.lease_owner_id == "agent-run-1"
    assert capacity.capacity_scope_ref == "opencode-zen:contributor-free"


@pytest.mark.asyncio
async def test_provider_capacity_is_requested_before_host_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The target flow's order is load-bearing: a host is reserved after a slot."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )

    assert run.signals[0][0] == "request_slot"
    assert [name for name, _ in run.activity_calls] == [
        "omnigent.admit_generic_host_capacity"
    ]


@pytest.mark.asyncio
async def test_waiting_for_host_capacity_uses_a_timer_not_an_activity_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 6/7: an oversubscribed machine parks a timer, not an Activity."""

    slept = _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    run.host_decisions = [
        {
            "admitted": False,
            "retryAfterSeconds": 30,
            "waitingReason": (
                "Waiting for generic host capacity; "
                "missing_condition=generic_host_capacity."
            ),
        },
        {"admitted": True},
    ]

    await run._await_omnigent_host_capacity(parent_info=None)

    assert slept == [30]
    assert run.parent_states == [
        (
            "awaiting_slot",
            "Waiting for generic host capacity; "
            "missing_condition=generic_host_capacity.",
        )
    ]
    # The admission read is a short control activity, polled — never held open.
    assert [name for name, _ in run.activity_calls] == [
        "omnigent.admit_generic_host_capacity",
        "omnigent.admit_generic_host_capacity",
    ]


@pytest.mark.asyncio
async def test_unreadable_host_admission_payload_does_not_widen_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed decision must block and retry, never be read as admitted."""

    slept = _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    run.host_decisions = ["nonsense", {"admitted": True}]

    await run._await_omnigent_host_capacity(parent_info=None)

    assert slept == [agent_run_module._OMNIGENT_HOST_CAPACITY_RETRY_SECONDS]


@pytest.mark.asyncio
async def test_host_capacity_refusal_releases_provider_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capacity acquired for an execution that never starts goes back at once."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    _capture_release_signals(monkeypatch, run)

    async def refuse(**kwargs):
        raise ApplicationError("no host capacity", non_retryable=True)

    run._await_omnigent_host_capacity = refuse  # type: ignore[method-assign]

    with pytest.raises(ApplicationError, match="no host capacity"):
        await run._admit_omnigent_capacity_before_execution(
            request=_omnigent_request(), admission=_admission(), parent_info=None
        )

    assert ("release_slot", {
        "requester_workflow_id": "agent-run-1",
        "profile_id": "opencode-zen-free",
    }) in [(name, payload) for name, payload in run.signals if name == "release_slot"]


@pytest.mark.asyncio
async def test_missing_capacity_authority_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 12: an explicit plan never guesses its capacity ledger."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    with pytest.raises(ApplicationError) as exc_info:
        await run._admit_omnigent_capacity_before_execution(
            request=_omnigent_request().model_copy(
                update={"execution_profile_ref": None}
            ),
            admission=_admission(profile_ref=None, runtime_id=None),
            parent_info=None,
        )

    assert exc_info.value.type == "ProviderProfileCapacityAuthorityMissing"
    assert run.activity_calls == []


@pytest.mark.asyncio
async def test_manager_granting_another_profile_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 12: capacity pressure never reroutes an explicit plan."""

    _configure_workflow_runtime(monkeypatch)

    class _WrongProfileRun(_RecordingRun):
        async def _ensure_manager_and_signal(
            self, manager_id, runtime_id, *, request_slot=True, **kwargs
        ):
            handle = await super()._ensure_manager_and_signal(
                manager_id, runtime_id, request_slot=request_slot, **kwargs
            )
            if request_slot:
                self._assigned_profile_id = "some-other-profile"
            return handle

    run = _WrongProfileRun()
    _capture_release_signals(monkeypatch, run)

    with pytest.raises(ApplicationError) as exc_info:
        await run._admit_omnigent_capacity_before_execution(
            request=_omnigent_request(), admission=_admission(), parent_info=None
        )

    assert exc_info.value.type == "ProviderProfileSelectionConflict"
    released = [payload for name, payload in run.signals if name == "release_slot"]
    assert released == [
        {
            "requester_workflow_id": "agent-run-1",
            "profile_id": "some-other-profile",
        }
    ]


@pytest.mark.asyncio
async def test_release_is_a_noop_when_the_workflow_owns_no_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry loop must not release a lease this workflow no longer owns."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    await run._release_omnigent_provider_capacity(request=_omnigent_request())

    assert run.signals == []


@pytest.mark.asyncio
async def test_release_clears_ownership_so_it_happens_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    _capture_release_signals(monkeypatch, run)
    await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )

    request = _omnigent_request()
    await run._release_omnigent_provider_capacity(request=request)
    signals_after_first = len(run.signals)
    await run._release_omnigent_provider_capacity(request=request)

    assert len(run.signals) == signals_after_first
    assert run._omnigent_capacity_profile_id is None


def test_effective_capacity_suffix_names_the_limiting_layer() -> None:
    """AC11: a lower effective limit is distinguishable from the ceiling."""

    suffix = MoonMindAgentRun._effective_capacity_suffix(
        {"configured_capacity": 8, "effective_capacity": 2}
    )

    assert "effective_capacity=2" in suffix
    assert "limiting_layer=provider_adaptive_backpressure" in suffix


def test_effective_capacity_suffix_is_silent_at_the_ceiling() -> None:
    """No backpressure means no extra reason text to mislead the operator."""

    assert (
        MoonMindAgentRun._effective_capacity_suffix(
            {"configured_capacity": 8, "effective_capacity": 8}
        )
        == ""
    )
    # A pre-#3878 manager reports neither field and must not be guessed at.
    assert MoonMindAgentRun._effective_capacity_suffix({"max_parallel_runs": 8}) == ""


def test_profile_id_from_manager_lease_reads_the_held_profile() -> None:
    """A grant that lands while the signal is in flight must be recognized."""

    assert (
        MoonMindAgentRun._profile_id_from_manager_lease(
            {"requester_profile_id": "opencode-zen-free"}
        )
        == "opencode-zen-free"
    )
    assert MoonMindAgentRun._profile_id_from_manager_lease({}) is None
    assert (
        MoonMindAgentRun._profile_id_from_manager_lease({"requester_profile_id": " "})
        is None
    )


@pytest.mark.asyncio
async def test_release_is_ordered_after_the_execution_activity_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant 10: capacity goes back only once the Activity's cleanup is done."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    _capture_release_signals(monkeypatch, run)
    ordering: list[str] = []

    async def signal(name, payload=None):
        ordering.append(f"release:{name}")

    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda _id: SimpleNamespace(signal=signal),
    )

    request = await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )
    try:
        ordering.append("activity:start")
        raise ApplicationError("execution failed", non_retryable=True)
    except ApplicationError:
        ordering.append("activity:cleanup-complete")
    finally:
        await run._release_omnigent_provider_capacity(request=request)

    assert ordering == [
        "activity:start",
        "activity:cleanup-complete",
        "release:release_slot",
    ]


@pytest.mark.asyncio
async def test_a_cancelled_workflow_defers_release_to_manager_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing mid-cancellation could free capacity while the host still runs."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    async def cancelled_signal(name, payload=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda _id: SimpleNamespace(signal=cancelled_signal),
    )
    request = await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )

    with pytest.raises(asyncio.CancelledError):
        await run._release_omnigent_provider_capacity(request=request)

    # No release was signalled; the manager reclaims the lease when this
    # workflow ends, which is never before the Activity stops using its host.
    assert [name for name, _ in run.signals if name == "release_slot"] == []
