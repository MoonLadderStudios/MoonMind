"""Provider and host capacity are admitted before the long execution Activity.

Source issues: MoonLadderStudios/MoonMind#3878 (AC4, AC6, AC7, AC10, AC12,
invariants 6, 7, 10, 12) and MoonLadderStudios/MoonMind#3880 (remaining
implementation 1, 3-5; AC1, AC3-AC5).

The behaviour under test is the target flow's ordering: request Provider
Profile capacity, wait durably when unavailable, reserve provisional
generic-host capacity, and only then start ``integration.omnigent.execute``.
The wait must be workflow state — a timer and a signal — never an Activity that
holds an execution slot open.

The ticket that crosses into the Activity is the second half of the contract.
It must bind the committed plan, this run's step and request identity, and the
credential generation admitted against, and the manager must record that same
fence — otherwise the Activity has nothing to inspect and would be back to
acquiring capacity inside the execution slot.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.runtime_bindings import stable_binding_id
from moonmind.schemas.agent_runtime_models import (
    ADMITTED_PROVIDER_CAPACITY_SCHEMA_V2,
    AgentExecutionRequest,
    AgentRunResult,
)
from moonmind.schemas.omnigent_session_models import (
    OmnigentSessionAdmissionDecision,
)
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _plan_capacity_authority,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    OMNIGENT_MULTI_PROFILE_ADMISSION_REJECTION_PATCH_ID,
    OMNIGENT_PRE_ACTIVITY_CAPACITY_ADMISSION_PATCH_ID,
    MoonMindAgentRun,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow




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


PLAN_REF = "omnigent-execution-plan:sha256:" + "1" * 64
OTHER_PLAN_REF = "omnigent-execution-plan:sha256:" + "3" * 64


def _plan_binding(plan_ref: str = PLAN_REF) -> dict[str, Any]:
    """The execution-plan binding the production start path actually authors."""

    digest = "sha256:" + plan_ref.rsplit(":", 1)[-1]
    return {
        "planRef": plan_ref,
        "planDigest": digest,
        "planArtifactRef": "artifact:omnigent-execution-plan",
        "taskInputSnapshotRef": "artifact:omnigent-task-input",
        "taskInputSnapshotDigest": "sha256:" + "2" * 64,
    }


def _omnigent_request(
    *,
    plan_binding: str | None = None,
    plan_ref_parameter: str | None = PLAN_REF,
    **parameters: Any,
) -> AgentExecutionRequest:
    """Build a request in either committed-plan shape.

    ``plan_binding`` is the authority the plan-bound generic-host lane is
    selected by and the only one the API and scheduler start paths author.
    ``plan_ref_parameter`` is the optional workflow-authored parameter that
    ``MoonMindRunWorkflow`` writes only when the caller supplied a
    workflow-level ``executionPlanRef``. Both shapes reach this admission code,
    so both are exercised.
    """

    binding = _plan_binding(plan_binding) if plan_binding else None
    payload: dict[str, Any] = {"publishMode": "none"}
    if plan_ref_parameter:
        payload["executionPlanRef"] = plan_ref_parameter
    if binding is not None:
        # run.py keeps the authored binding in ``parameters`` as well.
        payload["omnigentExecutionPlan"] = dict(binding)
    payload.update(parameters)
    return AgentExecutionRequest(
        agentKind="external" if binding is not None else "managed",
        agentId="omnigent",
        executionProfileRef="opencode-zen-free",
        omnigentExecutionPlan=binding,
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="artifact:instructions",
        parameters=payload,
        workspaceSpec={},
    )


def _admission(
    *,
    profile_ref: str | None = "opencode-zen-free",
    runtime_id: str | None = "opencode",
    capacity_scope_ref: str | None = "opencode-zen:contributor-free",
    credential_generation: int | None = 7,
    extra_profiles: tuple[tuple[str, str], ...] = (),
    host_class_ref: str | None = "omnigent-opencode@1",
) -> Any:
    """The frozen admission decision, in the production model's own shape."""

    profiles = []
    if profile_ref and runtime_id:
        profiles.append(
            {
                "providerProfileRef": profile_ref,
                "providerRuntimeId": runtime_id,
                "capacityScopeRef": capacity_scope_ref,
                "credentialGeneration": credential_generation,
            }
        )
    profiles.extend(
        {
            "providerProfileRef": ref,
            "providerRuntimeId": runtime,
            "capacityScopeRef": None,
            "credentialGeneration": 1,
        }
        for ref, runtime in extra_profiles
    )
    return OmnigentSessionAdmissionDecision.model_validate(
        {
            "admitted": True,
            "reasonCode": "enabled",
            "admissionMode": "enabled",
            "executionRealizerRef": "generic-omnigent-host@1",
            "providerProfileRef": profile_ref,
            "providerRuntimeId": runtime_id,
            "capacityScopeRef": capacity_scope_ref,
            "capacityProfiles": profiles,
            "hostClassRef": host_class_ref,
            "capacityAcquisitionOwner": "workflow",
        }
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

        recorder = self

        class _Handle:
            async def signal(self, name, payload=None):
                recorder.signals.append((name, dict(payload or {})))

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
    """Invariant 6 / #3880 requirement 1: the ticket binds the whole authority."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    admitted = await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )

    capacity = admitted.admitted_provider_capacity
    assert capacity is not None
    assert capacity.schema_version == ADMITTED_PROVIDER_CAPACITY_SCHEMA_V2
    assert capacity.lease_owner_id == "agent-run-1"
    assert capacity.profile_refs == ("opencode-zen-free",)
    assert capacity.profiles[0].provider_runtime_id == "opencode"
    assert capacity.profiles[0].capacity_scope_ref == "opencode-zen:contributor-free"
    assert capacity.profiles[0].credential_generation == 7
    assert capacity.execution_plan_ref == PLAN_REF
    assert capacity.agent_run_workflow_id == "agent-run-1"
    assert capacity.agent_run_run_id == "run-1"
    assert capacity.step_execution_id == "idem-1"
    assert capacity.idempotency_key == "idem-1"
    assert capacity.admission_epoch == 1
    # The ticket carries identity only: no secret, credential or host detail.
    payload = capacity.model_dump(mode="json", by_alias=True)
    assert "secret" not in str(payload).lower()


@pytest.mark.asyncio
async def test_the_manager_records_the_same_fence_the_activity_inspects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#3880 requirement 2: without a recorded fence there is nothing to inspect."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )

    request_slot = next(
        payload for name, payload in run.signals if name == "request_slot"
    )
    assert request_slot["lease_metadata"] == {
        "stepExecutionId": "idem-1",
        "idempotencyKey": "idem-1",
        "executionPlanRef": PLAN_REF,
        "credentialGeneration": 7,
    }


@pytest.mark.asyncio
async def test_a_plan_without_its_committed_plan_ref_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbound ticket could be replayed against another plan."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    request = _omnigent_request()
    request = request.model_copy(update={"parameters": {"publishMode": "none"}})

    with pytest.raises(ApplicationError) as exc_info:
        await run._admit_omnigent_capacity_before_execution(
            request=request, admission=_admission(), parent_info=None
        )

    assert exc_info.value.type == "ProviderProfileCapacityAuthorityMissing"
    assert run.signals == []


@pytest.mark.asyncio
async def test_a_multi_profile_plan_is_rejected_before_any_capacity_is_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#3880 requirement 4 / AC4: rejected explicitly, not routed to a side path.

    The ProviderProfileManager ledger holds at most one lease per requester
    workflow, so a workflow-owned all-required admission across several
    profiles cannot be expressed without a second capacity ledger. Saying so
    before execution is the supported behaviour; silently falling back to
    Activity-side queueing is not.

    This is the defense-in-depth guard inside the admission helper. The branch
    a production multi-profile plan actually takes is the workflow gate — see
    ``test_a_new_activity_owned_plan_is_rejected_before_execution`` — because
    ``_plan_capacity_authority`` classifies such a plan as Activity-owned and
    returns no profile refs at all.
    """

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    with pytest.raises(ApplicationError) as exc_info:
        await run._admit_omnigent_capacity_before_execution(
            request=_omnigent_request(),
            admission=_admission(
                extra_profiles=(("second-profile", "opencode"),)
            ),
            parent_info=None,
        )

    assert exc_info.value.type == "MultiProfileCapacityAdmissionUnsupported"
    assert exc_info.value.non_retryable is True
    assert run.signals == []
    assert run.activity_calls == []


@pytest.mark.asyncio
async def test_a_history_recorded_without_capacity_profiles_still_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retained histories carry the flat #3878 authority and must keep working."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    admission = SimpleNamespace(
        provider_profile_ref="opencode-zen-free",
        provider_runtime_id="opencode",
        capacity_scope_ref="opencode-zen:contributor-free",
    )

    admitted = await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=admission, parent_info=None
    )

    capacity = admitted.admitted_provider_capacity
    assert capacity.profile_refs == ("opencode-zen-free",)
    assert capacity.profiles[0].credential_generation is None


@pytest.mark.asyncio
async def test_host_admission_names_the_reservation_not_a_bare_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#3880 requirement 5: pre-admission and allocation resolve one host lease."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )

    _, payload = next(
        item
        for item in run.activity_calls
        if item[0] == "omnigent.admit_generic_host_capacity"
    )
    assert payload["executionPlanRef"] == PLAN_REF
    assert payload["idempotencyKey"] == "idem-1"
    assert payload["hostClassRef"] == "omnigent-opencode@1"


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


class _ExecutingRun(_RecordingRun):
    """A run whose execution Activity returns a scripted sequence of results."""

    def __init__(self, results: list[Any]) -> None:
        super().__init__()
        self.results = list(results)
        self.executions: list[AgentExecutionRequest] = []
        self.capacity_states: list[str] = []

    async def _execute_routed_activity(self, name, payload=None, **kwargs):
        if name.startswith("integration.omnigent."):
            self.executions.append(payload)
            self.capacity_states.append(self._omnigent_capacity_state)
            return self.results.pop(0)
        return await super()._execute_routed_activity(name, payload, **kwargs)


async def _run_execution(run: _ExecutingRun) -> tuple[Any, Any]:
    return await run._execute_omnigent_with_admitted_capacity(
        act_name="integration.omnigent.execute",
        request=_omnigent_request(),
        admission=_admission(),
        parent_info=None,
        stc_seconds=600,
        admit_capacity_before_activity=True,
    )


def _capacity_failure(code: str) -> dict[str, Any]:
    return {
        "summary": f"failed ({code})",
        "failureClass": "integration_error",
        "providerErrorCode": code,
    }


def test_only_recoverable_capacity_codes_return_a_run_to_waiting() -> None:
    """AC5: a lost slot is recoverable; a real failure must stay terminal."""

    for code in (
        "OMNIGENT_HOST_CAPACITY_UNAVAILABLE",
        "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE",
        "OMNIGENT_CREDENTIAL_GENERATION_FENCED",
    ):
        assert (
            MoonMindAgentRun._omnigent_capacity_requeue_reason(
                _capacity_failure(code)
            )
            is not None
        )
    for code in (
        "OMNIGENT_HOST_LAUNCH_FAILED",
        "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE",
        "OMNIGENT_MODEL_UNAVAILABLE",
        "",
    ):
        assert (
            MoonMindAgentRun._omnigent_capacity_requeue_reason(
                _capacity_failure(code)
            )
            is None
        )
    assert (
        MoonMindAgentRun._omnigent_capacity_requeue_reason(
            AgentRunResult(
                summary="lost the slot",
                failureClass="integration_error",
                providerErrorCode="OMNIGENT_HOST_CAPACITY_UNAVAILABLE",
            )
        )
        is not None
    )
    assert MoonMindAgentRun._omnigent_capacity_requeue_reason({}) is None


@pytest.mark.asyncio
async def test_a_host_slot_lost_after_admission_requeues_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: durable requeue, no leaked provider capacity, no duplicate host."""

    _configure_workflow_runtime(monkeypatch)
    run = _ExecutingRun(
        [
            _capacity_failure("OMNIGENT_HOST_CAPACITY_UNAVAILABLE"),
            {"summary": "done"},
        ]
    )
    _capture_release_signals(monkeypatch, run)

    result, admitted_at = await _run_execution(run)

    assert result == {"summary": "done"}
    assert admitted_at is not None
    assert len(run.executions) == 2
    # The lost attempt released its provider capacity before requeueing, and the
    # retry queued for a fresh grant under the same owner.
    releases = [name for name, _ in run.signals if name == "release_slot"]
    assert len(releases) == 2
    requests = [payload for name, payload in run.signals if name == "request_slot"]
    assert len(requests) == 2
    assert {payload["execution_profile_ref"] for payload in requests} == {
        "opencode-zen-free"
    }
    # Both attempts name the same stable host reservation, so the retry reuses
    # the run's host lease rather than racing for a second one.
    host_payloads = [
        payload
        for name, payload in run.activity_calls
        if name == "omnigent.admit_generic_host_capacity"
    ]
    assert len({payload["idempotencyKey"] for payload in host_payloads}) == 1
    waiting_reasons = [
        reason for state, reason in run.parent_states if state == "awaiting_slot"
    ]
    assert len(waiting_reasons) == 1
    assert "generic host capacity was lost after admission" in waiting_reasons[0]


@pytest.mark.asyncio
async def test_a_re_admission_is_a_fresh_ticket_not_the_stale_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 2: re-admission returns to the workflow owner, epoch bumped."""

    _configure_workflow_runtime(monkeypatch)
    run = _ExecutingRun(
        [
            _capacity_failure("OMNIGENT_CREDENTIAL_GENERATION_FENCED"),
            {"summary": "done"},
        ]
    )
    _capture_release_signals(monkeypatch, run)

    await _run_execution(run)

    epochs = [
        item.admitted_provider_capacity.admission_epoch for item in run.executions
    ]
    assert epochs == [1, 2]


@pytest.mark.asyncio
async def test_requeueing_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanently oversubscribed machine reports; it does not loop forever."""

    _configure_workflow_runtime(monkeypatch)
    failure = _capacity_failure("OMNIGENT_HOST_CAPACITY_UNAVAILABLE")
    run = _ExecutingRun([failure] * 12)
    _capture_release_signals(monkeypatch, run)

    result, _ = await _run_execution(run)

    assert result == failure
    assert len(run.executions) == (
        agent_run_module._MAX_OMNIGENT_CAPACITY_REQUEUE_ATTEMPTS + 1
    )


@pytest.mark.asyncio
async def test_capacity_is_marked_consumed_while_the_activity_may_hold_a_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 3: queued, granted-unconsumed and consumed are distinguished."""

    _configure_workflow_runtime(monkeypatch)
    run = _ExecutingRun([{"summary": "done"}])
    _capture_release_signals(monkeypatch, run)

    assert run._omnigent_capacity_state == "none"
    await _run_execution(run)

    assert run.capacity_states == ["consumed"]
    assert run._omnigent_capacity_state == "released"


@pytest.mark.asyncio
async def test_release_is_refused_while_an_activity_attempt_may_hold_a_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing a consumed lease would hand a live host to the next run."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    _capture_release_signals(monkeypatch, run)
    await run._admit_omnigent_capacity_before_execution(
        request=_omnigent_request(), admission=_admission(), parent_info=None
    )
    run._omnigent_capacity_state = "consumed"

    await run._release_omnigent_provider_capacity(request=_omnigent_request())

    assert [name for name, _ in run.signals if name == "release_slot"] == []
    assert run._omnigent_capacity_profile_id == "opencode-zen-free"


class _TimeoutRecordingRun(_ExecutingRun):
    """Records the timeouts the execution Activity is actually scheduled with."""

    def __init__(self, results: list[Any]) -> None:
        super().__init__(results)
        self.timeouts: list[dict[str, Any]] = []

    async def _execute_routed_activity(self, name, payload=None, **kwargs):
        if name.startswith("integration.omnigent."):
            self.timeouts.append(kwargs)
        return await super()._execute_routed_activity(name, payload, **kwargs)


@pytest.mark.asyncio
async def test_worker_handoff_is_bounded_separately_from_the_execution_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 7: queue time must not be charged to the execution window.

    Temporal's StartToClose already excludes worker-queue wait, but
    ScheduleToClose does not. Setting them equal spends the run's execution
    budget on waiting for a worker, so a saturated fleet produces timeouts that
    look like slow runs.
    """

    _configure_workflow_runtime(monkeypatch)
    run = _TimeoutRecordingRun([{"summary": "done"}])
    _capture_release_signals(monkeypatch, run)

    await run._execute_omnigent_with_admitted_capacity(
        act_name="integration.omnigent.execute",
        request=_omnigent_request(),
        admission=_admission(),
        parent_info=None,
        stc_seconds=3600,
        admit_capacity_before_activity=True,
    )

    scheduled = run.timeouts[0]
    assert scheduled["start_to_close_timeout"] == timedelta(seconds=3600)
    assert scheduled["schedule_to_close_timeout"] == timedelta(
        seconds=3600 + agent_run_module._OMNIGENT_EXECUTION_HANDOFF_SECONDS
    )


@pytest.mark.asyncio
async def test_a_run_without_admitted_capacity_keeps_its_recorded_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retained histories must keep scheduling the shape they recorded."""

    _configure_workflow_runtime(monkeypatch)
    run = _TimeoutRecordingRun([{"summary": "done"}])

    await run._execute_omnigent_with_admitted_capacity(
        act_name="integration.omnigent.execute",
        request=_omnigent_request(),
        admission=_admission(),
        parent_info=None,
        stc_seconds=3600,
        admit_capacity_before_activity=False,
    )

    scheduled = run.timeouts[0]
    assert scheduled["schedule_to_close_timeout"] == timedelta(seconds=3600)
    assert run.signals == []


@pytest.mark.asyncio
async def test_the_wait_the_run_is_actually_in_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 7: provider wait, host wait and worker wait are distinct."""

    _configure_workflow_runtime(monkeypatch)

    async def wait_condition(_predicate, timeout=None):
        # The provider slot is not free yet, so the durable wait times out and
        # the workflow re-inspects the manager rather than holding a slot.
        raise TimeoutError()

    monkeypatch.setattr(
        agent_run_module.workflow, "wait_condition", wait_condition
    )
    run = _ExecutingRun([{"summary": "done"}])
    run.grant_on_signal = False
    run.host_decisions = [
        {
            "admitted": False,
            "retryAfterSeconds": 5,
            "waitingReason": (
                "Waiting for generic host capacity; "
                "missing_condition=generic_host_capacity."
            ),
        },
        {"admitted": True},
    ]
    _capture_release_signals(monkeypatch, run)

    async def grant_after_first_wait(**kwargs):
        run._assigned_profile_id = "opencode-zen-free"
        run.slot_assigned_event.set()
        return {}

    run._manager_state_for_slot_wait = grant_after_first_wait  # type: ignore

    await _run_execution(run)

    reasons = [reason for _, reason in run.parent_states]
    assert any("Waiting for provider capacity." == reason for reason in reasons)
    assert any("generic_host_capacity" in reason for reason in reasons)
    assert any("waiting for an execution worker" in reason for reason in reasons)


# ---------------------------------------------------------------------------
# The committed plan reference the whole hand-off is fenced on
# (MoonLadderStudios/MoonMind#3880 remaining work 1 and 2).
# ---------------------------------------------------------------------------


def _production_started_omnigent_request() -> AgentExecutionRequest:
    """Build the request the production start path actually produces.

    The API (``api_service/api/routers/executions.py``) and the scheduler
    (``api_service/services/recurring_workflows_service.py``) author only
    ``omnigentExecutionPlan``. ``MoonMindRunWorkflow`` — the real builder used
    here — writes ``parameters['executionPlanRef']`` only when the caller also
    supplied a workflow-level ``executionPlanRef``, so the default run reaches
    admission with the binding and no parameter.
    """

    info = SimpleNamespace(
        namespace="default",
        workflow_id="mm:omnigent-plan-bound",
        run_id="run-1",
        parent=None,
    )
    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=info,
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            return_value=True,
        ),
    ):
        return MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={
                "runtime": {
                    "mode": "omnigent",
                    "executionProfileRef": "opencode-zen-free",
                }
            },
            node_id="omnigent",
            tool_name="auto",
            workflow_parameters={"omnigentExecutionPlan": _plan_binding()},
        )


@pytest.mark.asyncio
async def test_the_production_start_shape_admits_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: the default plan-bound run must actually admit capacity.

    The plan-bound generic-host lane is selected by
    ``request.omnigent_execution_plan``, so the committed plan reference the
    ticket, the manager fence and the host reservation are all keyed on has to
    come from that same authority. Reading the optional
    ``parameters['executionPlanRef']`` alone left this shape with no plan
    authority and failed admission before any capacity was requested.
    """

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()
    request = _production_started_omnigent_request()

    # The shape under test: binding present, parameter absent.
    assert request.omnigent_execution_plan is not None
    assert request.parameters.get("executionPlanRef") is None

    admitted = await run._admit_omnigent_capacity_before_execution(
        request=request, admission=_admission(), parent_info=None
    )

    capacity = admitted.admitted_provider_capacity
    assert capacity is not None
    assert capacity.execution_plan_ref == PLAN_REF
    # The manager records the same fence the Activity inspects.
    request_slot = next(
        payload for name, payload in run.signals if name == "request_slot"
    )
    assert request_slot["lease_metadata"]["executionPlanRef"] == PLAN_REF
    # The pre-admission reservation resolves to the runtime binding the
    # realizer derives from the plan it loads for this binding.
    host_payload = next(
        payload
        for name, payload in run.activity_calls
        if name == "omnigent.admit_generic_host_capacity"
    )
    assert host_payload["executionPlanRef"] == PLAN_REF
    assert host_payload["idempotencyKey"] == request.idempotency_key
    assert stable_binding_id(
        execution_plan_ref=host_payload["executionPlanRef"],
        idempotency_key=host_payload["idempotencyKey"],
    ) == stable_binding_id(
        execution_plan_ref=request.omnigent_execution_plan.plan_ref,
        idempotency_key=request.idempotency_key,
    )


@pytest.mark.asyncio
async def test_both_committed_plan_shapes_resolve_the_same_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workflow-authored executionPlanRef stays a supported input."""

    _configure_workflow_runtime(monkeypatch)

    for request in (
        _omnigent_request(),  # parameter only
        _omnigent_request(plan_binding=PLAN_REF, plan_ref_parameter=None),
        _omnigent_request(plan_binding=PLAN_REF),  # both, in agreement
    ):
        run = _RecordingRun()
        admitted = await run._admit_omnigent_capacity_before_execution(
            request=request, admission=_admission(), parent_info=None
        )
        assert admitted.admitted_provider_capacity.execution_plan_ref == PLAN_REF


@pytest.mark.asyncio
async def test_two_disagreeing_plan_authorities_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Picking either one would fence the Activity against an unadmitted plan."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    with pytest.raises(ApplicationError) as exc_info:
        await run._admit_omnigent_capacity_before_execution(
            request=_omnigent_request(
                plan_binding=PLAN_REF, plan_ref_parameter=OTHER_PLAN_REF
            ),
            admission=_admission(),
            parent_info=None,
        )

    assert exc_info.value.type == "OmnigentExecutionPlanAuthorityConflict"
    assert exc_info.value.non_retryable is True
    assert run.signals == []
    assert run.activity_calls == []


# ---------------------------------------------------------------------------
# Multi-profile plans: rejected before execution for new runs, retained
# histories keep their classified Activity acquisition
# (MoonLadderStudios/MoonMind#3880 remaining work 3 and 4).
# ---------------------------------------------------------------------------


def _plan_selecting(*profile_refs: str) -> Any:
    """A real generic-host plan envelope selecting the given Provider Profiles."""

    model = "opencode-go/model"
    return create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
            "harnessImplementationRef": (
                "omnigent-harness-implementation:sha256:" + "3" * 64
            ),
            "agentSource": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": "sha256:" + "4" * 64,
            },
            "credentialBindingSetRef": (
                "omnigent-credential-bindings:primary@1#sha256:" + "5" * 64
            ),
            "credentialBindings": {
                f"slot-{index}": {
                    "providerProfileRef": profile_ref,
                    "materializerRef": "none@1",
                }
                for index, profile_ref in enumerate(profile_refs)
            },
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": model,
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": compute_model_config_digest(
                    qualifiedId=model,
                    effort=None,
                    routeRef="opencode-go",
                    normalizedOptions={},
                ),
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": "artifact:skills",
                "resolvedSkillSetDigest": "sha256:" + "6" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "7" * 64,
            },
            "classAdmissionDecision": {
                "allowed": True,
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "preferredMissing": [],
                "reasons": [],
            },
            "runtimeValidationRequirements": ["live-model-option"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
            "workspaceMutation": "read_only",
            "capturePolicyRef": None,
            "capturePolicy": {"stream": False, "evidence": False},
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": (
                "omnigent-support-combination:sha256:" + "0" * 64
            ),
        }
    )


@pytest.mark.asyncio
async def test_a_multi_profile_plan_names_the_activity_as_capacity_owner() -> None:
    """The production admission Activity classifies the plan, not the test.

    ``_plan_capacity_authority`` returns this before touching the database, so
    the value the workflow gate reacts to is the real one.
    """

    authority = await _plan_capacity_authority(
        _plan_selecting("opencode-zen-free", "second-profile"),
        execution_profile_ref="opencode-zen-free",
    )

    assert authority == {"capacityAcquisitionOwner": "activity"}


def _owner_gate_admission(capacity_acquisition_owner: str) -> Any:
    """The frozen admission decision the workflow gate actually reads."""

    return OmnigentSessionAdmissionDecision.model_validate(
        {
            "admitted": True,
            "reasonCode": "enabled",
            "admissionMode": "enabled",
            "executionRealizerRef": "generic-omnigent-host@1",
            "capacityAcquisitionOwner": capacity_acquisition_owner,
        }
    )


def test_a_new_activity_owned_plan_is_rejected_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: rejected before any slot, host or execution Activity is committed."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    with pytest.raises(ApplicationError) as exc_info:
        run._omnigent_admits_capacity_before_activity(
            recorded_plan_realizer="generic-omnigent-host@1",
            admission=_owner_gate_admission("activity"),
        )

    assert exc_info.value.type == "CapacityAdmissionOwnerUnsupported"
    assert exc_info.value.non_retryable is True
    assert run.signals == []
    assert run.activity_calls == []


def test_a_retained_history_keeps_its_activity_owned_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8 / Impl 7: replay must reach the ActivityTaskScheduled it recorded.

    Under MoonLadderStudios/MoonMind#3878 the capacity owner was one term of
    the same boolean as the pre-Activity admission marker, so a multi-profile
    run recorded that marker and then scheduled its Activity-owned execution.
    Raising on replay would be non-deterministic against that history, so the
    rejection carries its own marker, which such a history never recorded.
    """

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id != OMNIGENT_MULTI_PROFILE_ADMISSION_REJECTION_PATCH_ID
        ),
    )
    run = _RecordingRun()

    assert (
        run._omnigent_admits_capacity_before_activity(
            recorded_plan_realizer="generic-omnigent-host@1",
            admission=_owner_gate_admission("activity"),
        )
        is False
    )


def test_a_workflow_owned_plan_admits_before_the_execution_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supported single-profile lane still takes pre-Activity admission."""

    _configure_workflow_runtime(monkeypatch)
    run = _RecordingRun()

    assert (
        run._omnigent_admits_capacity_before_activity(
            recorded_plan_realizer="generic-omnigent-host@1",
            admission=_owner_gate_admission("workflow"),
        )
        is True
    )
    # Another realizer never enters this path at all.
    assert (
        run._omnigent_admits_capacity_before_activity(
            recorded_plan_realizer="codex-profile-bound@1",
            admission=_owner_gate_admission("workflow"),
        )
        is False
    )


def test_a_pre_patch_history_never_admits_before_the_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history recorded before pre-Activity admission keeps its recorded lane."""

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        lambda patch_id: (
            patch_id != OMNIGENT_PRE_ACTIVITY_CAPACITY_ADMISSION_PATCH_ID
        ),
    )
    run = _RecordingRun()

    assert (
        run._omnigent_admits_capacity_before_activity(
            recorded_plan_realizer="generic-omnigent-host@1",
            admission=_owner_gate_admission("workflow"),
        )
        is False
    )
