"""N-way authoring and admission at the production boundaries.

Source issue: MoonLadderStudios/MoonMind#3885 (hermetic layer, AC1).

The N-way realizer journey lives in
``tests/unit/omnigent/test_generic_plane_n_way_concurrency.py``. It starts from
a plan the test authored and an admission the test granted, which leaves the two
boundaries *before* the realizer unproven at concurrency:

* the **execution-plan compiler**, which has to turn ``N`` simultaneous
  submissions into ``N`` immutable plans that each select the expected generic
  Omnigent combination; and
* the **Temporal dispatch boundary**, where ``MoonMindAgentRun`` requests
  Provider Profile capacity from the manager's ledger and waits durably when it
  is full — before any long-running execution Activity starts.

Both are exercised here through the production code that owns them:
:func:`~moonmind.omnigent.harness_platform.planner.compile_execution_plan`
behind :class:`~moonmind.omnigent.harness_platform.stores.InMemoryExecutionPlanStore`
— the hermetic store, so ``load_or_compile`` idempotency is proven here while
the DB-backed ``persist`` race stays with the plan-store tests — and
``MoonMindAgentRun._admit_omnigent_capacity_before_execution`` against a real
:class:`~moonmind.workflows.temporal.workflows.provider_profile_manager.ProfileSlotState`
ledger and the production
:class:`~moonmind.omnigent.host_capacity.GenericHostCapacityAdmission`. No
in-memory admission stub decides who runs: the ledger does.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.host_classes import OmnigentHostClassSelector
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
from moonmind.omnigent.host_capacity import GenericHostCapacityAdmission
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.omnigent_session_models import OmnigentSessionAdmissionDecision
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    ProfileSlotState,
)

#: The deployment-selectable ceilings the generic plane must stay correct for.
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]

ZEN_PROFILE = "opencode-zen-free"
ZEN_MODEL = "opencode/muse-spark-1.2-contributor-free"
RUNTIME_ID = "opencode"
CAPACITY_SCOPE = "opencode-zen:contributor-free"
CREDENTIAL_GENERATION = 4
HOST_CLASS_REF = "omnigent-opencode@1"
LAUNCH_POLICY_REF = "omnigent-on-demand@1"
GENERIC_REALIZER_REF = "generic-omnigent-host@1"

_SERVER_IMAGE_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "b" * 64
_OPENCODE_IMAGE_REF = (
    "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
)
_IMPL_DIGEST = "sha256:" + "a" * 64

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. N simultaneous submissions compile into N immutable plans.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ready_opencode_image_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish the exact resolved image pair the host-class selector reads."""

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF)
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", _OPENCODE_IMAGE_REF)
    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: SimpleNamespace(
            server_image_ref=_SERVER_IMAGE_REF,
            opencode_host_image_ref=_OPENCODE_IMAGE_REF,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_IMAGE_REF,
                    "hostImageRef": _OPENCODE_IMAGE_REF,
                }
            },
        ),
    )


def _catalog():
    return create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": "opencode-native",
                "aliases": [],
                "label": "opencode-native",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": _IMPL_DIGEST,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "own-auth",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
        observedAt=datetime.now(UTC),
    )


def _implementation() -> HarnessImplementationIdentity:
    return HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": _IMPL_DIGEST,
            "pluginEntryPoint": None,
        }
    )


def _agent_profile(catalog) -> OmnigentAgentProfileV2:
    return OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "opencode-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": _implementation().implementation_ref(),
            },
            "requirements": {
                "harness": {"required": [], "preferred": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    # Credentialless: the Zen contributor-free route carries no
                    # shared mutable authentication home, so the slot accepts
                    # the ``none`` auth model rather than an own-auth home.
                    "acceptedAuthModels": ["none"],
                    "acceptedProviderIds": ["opencode"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": [LAUNCH_POLICY_REF],
        }
    )


def _credentialless_binding_set():
    """The credentialless Zen binding: no shared mutable authentication home."""

    return create_binding_set(
        bindingSetId="zen-contributor-free",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": ZEN_PROFILE,
                "materializerRef": "none@1",
            }
        },
    )


def _resolved_skills(run: str) -> ResolvedSkillSet:
    """A distinct resolved skill set per submission, as production resolves."""

    return ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": f"artifact:skills:{run}",
            "resolvedSkillSetDigest": "sha256:" + f"{int(run):064x}",
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )


def _compile_kwargs(catalog, run: str) -> dict[str, Any]:
    host_class = OmnigentHostClassSelector(
        environment={
            "OMNIGENT_IMAGE_REF": _SERVER_IMAGE_REF,
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": _OPENCODE_IMAGE_REF,
        }
    ).select(
        harness=catalog.harnesses[0],
        omnigent_version=catalog.omnigentVersion,
        omnigent_build_digest=catalog.omnigentBuildDigest,
        integration_mode="native-server",
        materializer_refs=["none@1"],
    )
    return dict(
        agent_profile=_agent_profile(catalog),
        harness_catalog=catalog,
        trust_record=classify_harness_trust(
            harnessId="opencode-native",
            implementation=_implementation(),
            trustState=TrustState.core_trusted,
        ),
        resolved_skills=_resolved_skills(run),
        credential_binding_set=_credentialless_binding_set(),
        host_class_ref=HOST_CLASS_REF,
        host_class=host_class,
        launch_policy_ref=LAUNCH_POLICY_REF,
        model_qualified_id=ZEN_MODEL,
        model_effort=None,
        model_route_ref="opencode",
        model_normalized_options={},
    )


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_n_submissions_compile_into_n_immutable_generic_plans(
    concurrency: int,
) -> None:
    """``N`` simultaneous submissions produce ``N`` distinct committed plans.

    The realizer refuses a plan whose ``executionRealizerRef`` is not its own,
    so the combination each plan *selects* is what decides whether the generic
    Omnigent plane runs at all. Compiling ``N`` at once through the production
    compiler behind the hermetic in-memory store is where a shared plan ref, a
    shared skill projection, or a realizer selection that varied under
    concurrency would appear. The DB-backed store's ``persist`` race is owned
    by the plan-store tests, not by this layer.
    """

    catalog = _catalog()
    store = InMemoryExecutionPlanStore()

    plans = await asyncio.gather(
        *(
            store.load_or_compile(
                compile_fn=compile_execution_plan,
                compile_kwargs=_compile_kwargs(catalog, str(index)),
            )
            for index in range(concurrency)
        )
    )

    assert len({plan.planRef for plan in plans}) == concurrency
    for plan in plans:
        payload = plan.payload
        # The expected generic Omnigent combination, selected by the compiler
        # rather than authored by the caller.
        assert payload.executionRealizerRef == GENERIC_REALIZER_REF
        assert payload.harnessId == "opencode-native"
        assert payload.hostClassRef == HOST_CLASS_REF
        assert payload.launchPolicyRef == LAUNCH_POLICY_REF
        assert (
            payload.credentialBindings["primary-model"].materializerRef == "none@1"
        )
        assert payload.modelConfig.qualifiedId == ZEN_MODEL


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_a_concurrent_retry_reloads_its_committed_plan(
    concurrency: int,
) -> None:
    """A retry under load reloads its plan; it never compiles a second one.

    Two plans for one submission would give the same execution two host
    classes, two skill projections, and two cleanup authorities.
    """

    catalog = _catalog()
    store = InMemoryExecutionPlanStore()
    first = await asyncio.gather(
        *(
            store.load_or_compile(
                compile_fn=compile_execution_plan,
                compile_kwargs=_compile_kwargs(catalog, str(index)),
            )
            for index in range(concurrency)
        )
    )

    retried = await asyncio.gather(
        *(
            store.load_or_compile(
                compile_fn=compile_execution_plan,
                compile_kwargs=_compile_kwargs(catalog, str(index)),
            )
            for index in range(concurrency)
        )
    )

    assert [plan.planRef for plan in retried] == [plan.planRef for plan in first]
    assert list(retried) == list(first)


# ---------------------------------------------------------------------------
# 2. N-way admission and durable waiting at the Temporal dispatch boundary.
# ---------------------------------------------------------------------------


class _ProviderLedger:
    """One real Provider Profile slot ledger shared by every dispatching run.

    This is the manager's own :class:`ProfileSlotState`, not a counter: the
    grant, the refusal, and the release below are the production accounting a
    deployed ProviderProfileManager performs.
    """

    def __init__(self, capacity: int, hosts: "_HostLedger") -> None:
        #: Releasing a completed run returns both the provider slot it held and
        #: the host it was running on, which is the order the workflow releases
        #: them in: host first, provider capacity last.
        self.hosts = hosts
        self.profile = ProfileSlotState(
            profile_id=ZEN_PROFILE,
            max_parallel_runs=capacity,
            cooldown_after_429_seconds=300,
            rate_limit_policy="backoff",
            enabled=True,
            launch_ready=True,
            credential_source="none",
            purpose_aware_capacity=True,
        )
        #: Requesters still queued, in arrival order.
        self.queue: list[str] = []
        self.granted: list[str] = []
        self.waiters: dict[str, Any] = {}

    def request(self, run: "_DispatchingRun") -> None:
        owner = run.workflow_id
        if owner in self.queue or owner in self.granted:
            return
        self.waiters[owner] = run
        self.queue.append(owner)
        self._drain()

    def cancel(self, owner: str) -> None:
        if owner in self.queue:
            self.queue.remove(owner)
        self.waiters.pop(owner, None)

    def release(self, owner: str) -> bool:
        released = self.profile.release(owner)
        if released:
            self.granted.remove(owner)
            self.hosts.active_hosts = max(0, self.hosts.active_hosts - 1)
            self._drain()
        return released

    def _drain(self) -> None:
        while self.queue:
            owner = self.queue[0]
            if not self.profile.reserve(
                owner, NOW, purpose="execution_omnigent"
            ):
                return
            self.queue.pop(0)
            self.granted.append(owner)
            run = self.waiters.pop(owner, None)
            if run is not None:
                run.grant()


class _HostLedger(GenericHostCapacityAdmission):
    """Aggregate host admission over an observable in-memory host count."""

    def __init__(self, *, host_capacity: int) -> None:
        super().__init__(
            session_factory=None,
            host_capacity=host_capacity,
            cold_launch_burst=1024,
            cold_launch_window_seconds=30,
        )
        self.active_hosts = 0

    async def observe(self, *, now=None) -> tuple[int, int]:
        return self.active_hosts, 0


class _DispatchingRun(MoonMindAgentRun):
    """An AgentRun whose manager boundary is the shared ledger, not a stub."""

    def __init__(
        self,
        workflow_id: str,
        ledger: _ProviderLedger,
        hosts: _HostLedger,
    ) -> None:
        super().__init__()
        self.workflow_id = workflow_id
        self._ledger = ledger
        self._hosts = hosts
        self.parent_states: list[tuple[str, str]] = []
        self.activity_calls: list[str] = []

    def grant(self) -> None:
        self._assigned_profile_id = ZEN_PROFILE
        self.slot_assigned_event.set()

    async def _ensure_manager_and_signal(
        self, manager_id, runtime_id, *, request_slot=True, **kwargs
    ):
        if request_slot:
            self._ledger.request(self)
        return SimpleNamespace(signal=self._noop_signal)

    async def _noop_signal(self, name, payload=None):
        return None

    async def _sync_manager_profiles(self, **kwargs) -> int:
        return 1

    async def _signal_parent_child_state_changed(self, parent_info, state, reason):
        self.parent_states.append((state, reason))

    async def _inspected_provider_slot_waiting_reason(self, **kwargs) -> str:
        return "Waiting for provider capacity."

    async def _manager_state_for_slot_wait(self, **kwargs) -> dict[str, Any]:
        return {"requester_pending": True}

    async def _execute_routed_activity(self, name, payload=None, **kwargs):
        self.activity_calls.append(name)
        if name == "omnigent.admit_generic_host_capacity":
            decision = await self._hosts.evaluate()
            if decision.admitted:
                self._hosts.active_hosts += 1
            return decision.as_payload()
        return {}

    def _get_logger(self):
        return SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        )


def _admission_decision() -> Any:
    return OmnigentSessionAdmissionDecision.model_validate(
        {
            "admitted": True,
            "reasonCode": "enabled",
            "admissionMode": "enabled",
            "executionRealizerRef": GENERIC_REALIZER_REF,
            "providerProfileRef": ZEN_PROFILE,
            "providerRuntimeId": RUNTIME_ID,
            "capacityScopeRef": CAPACITY_SCOPE,
            "capacityProfiles": [
                {
                    "providerProfileRef": ZEN_PROFILE,
                    "providerRuntimeId": RUNTIME_ID,
                    "capacityScopeRef": CAPACITY_SCOPE,
                    "credentialGeneration": CREDENTIAL_GENERATION,
                }
            ],
            "hostClassRef": HOST_CLASS_REF,
            "capacityAcquisitionOwner": "workflow",
        }
    )


def _dispatch_request(run: str) -> AgentExecutionRequest:
    plan_ref = "omnigent-execution-plan:sha256:" + f"{int(run):064x}"
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": ZEN_PROFILE,
            "correlationId": f"workflow-{run}",
            "idempotencyKey": f"idem-{run}",
            "omnigentExecutionPlan": {
                "planRef": plan_ref,
                "planDigest": "sha256:" + f"{int(run):064x}",
                "planArtifactRef": "artifact:omnigent-execution-plan",
                "taskInputSnapshotRef": "artifact:omnigent-task-input",
                "taskInputSnapshotDigest": "sha256:" + "2" * 64,
            },
            "parameters": {"publishMode": "none", "executionPlanRef": plan_ref},
            "workspaceSpec": {},
        }
    )


@pytest.fixture
def dispatch_runtime(monkeypatch: pytest.MonkeyPatch):
    """Install a deterministic workflow context for the dispatch boundary.

    ``wait_condition`` is the durable wait under test: a run that is not
    admitted stays parked here, holding a workflow timer and no execution
    Activity, until the ledger grants it or the run is cancelled.
    """

    slept: list[float] = []
    workflow_ids: dict[str, str] = {"current": "agent-run-0"}

    monkeypatch.setattr(
        agent_run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id=workflow_ids["current"],
            run_id=f"run-{workflow_ids['current']}",
            search_attributes={},
            parent=None,
        ),
    )
    monkeypatch.setattr(
        agent_run_module.workflow,
        "logger",
        SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
    )
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _pid: True)
    monkeypatch.setattr(
        agent_run_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    async def fake_sleep(duration: timedelta) -> None:
        slept.append(duration.total_seconds())
        await asyncio.sleep(0)

    async def fake_wait_condition(predicate, timeout=None):
        while not predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(agent_run_module.workflow, "sleep", fake_sleep)
    monkeypatch.setattr(
        agent_run_module.workflow, "wait_condition", fake_wait_condition
    )
    return SimpleNamespace(slept=slept, workflow_ids=workflow_ids)


async def _admit(run: _DispatchingRun, dispatch_runtime) -> AgentExecutionRequest:
    """Drive one run through the production pre-Activity admission path."""

    dispatch_runtime.workflow_ids["current"] = run.workflow_id
    return await run._admit_omnigent_capacity_before_execution(
        request=_dispatch_request(run.workflow_id.rsplit("-", 1)[-1]),
        admission=_admission_decision(),
        parent_info=None,
    )


def _dispatch_wave(
    submissions: int, *, capacity: int, host_capacity: int | None = None
):
    hosts = _HostLedger(host_capacity=host_capacity or capacity)
    ledger = _ProviderLedger(capacity, hosts)
    runs = [
        _DispatchingRun(f"agent-run-{index}", ledger, hosts)
        for index in range(submissions)
    ]
    return ledger, hosts, runs


@pytest.mark.parametrize("capacity", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_only_n_of_n_plus_two_receive_provider_admission(
    capacity: int, dispatch_runtime
) -> None:
    """Exactly ``N`` are admitted; the surplus waits durably, holding nothing.

    The wait is the property: a queued run must not start the long execution
    Activity, must not hold a host, and must not have taken a provider lease.
    """

    ledger, hosts, runs = _dispatch_wave(capacity + 2, capacity=capacity)

    tasks = [
        asyncio.create_task(_admit(run, dispatch_runtime)) for run in runs
    ]
    # Let every submission reach either its grant or its durable wait.
    for _ in range(200):
        await asyncio.sleep(0)

    admitted = [task for task in tasks if task.done()]
    waiting = [task for task in tasks if not task.done()]
    assert len(admitted) == capacity
    assert len(waiting) == 2
    assert ledger.profile.execution_lease_count == capacity
    assert ledger.profile.available_slots == 0
    assert ledger.profile.is_available() is False
    assert hosts.active_hosts == capacity

    waiting_runs = [runs[index] for index, task in enumerate(tasks) if not task.done()]
    for run in waiting_runs:
        assert run.workflow_id in ledger.queue
        # No execution Activity, and no host, for work that was never admitted.
        assert run.activity_calls == []
        assert ("awaiting_slot", "Waiting for provider capacity.") in run.parent_states

    for task in waiting:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("capacity", [2, 4, 8])
@pytest.mark.asyncio
async def test_releasing_one_lease_grants_exactly_one_waiter(
    capacity: int, dispatch_runtime
) -> None:
    """One release admits one waiter, in arrival order, and no more."""

    ledger, _hosts, runs = _dispatch_wave(capacity + 2, capacity=capacity)
    tasks = [
        asyncio.create_task(_admit(run, dispatch_runtime)) for run in runs
    ]
    for _ in range(200):
        await asyncio.sleep(0)
    first_waiter = ledger.queue[0]

    assert ledger.release(ledger.granted[0]) is True
    for _ in range(200):
        await asyncio.sleep(0)

    assert first_waiter in ledger.granted
    assert len(ledger.queue) == 1
    assert ledger.profile.execution_lease_count == capacity
    assert sum(1 for task in tasks if task.done()) == capacity + 1

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_increasing_capacity_drains_exactly_the_eligible_waiters(
    dispatch_runtime,
) -> None:
    """Raising the ceiling admits the waiters it can carry, and no others."""

    ledger, _hosts, runs = _dispatch_wave(8, capacity=2, host_capacity=8)
    tasks = [
        asyncio.create_task(_admit(run, dispatch_runtime)) for run in runs
    ]
    for _ in range(200):
        await asyncio.sleep(0)
    assert sum(1 for task in tasks if task.done()) == 2

    ledger.profile.max_parallel_runs = 5
    ledger._drain()
    for _ in range(200):
        await asyncio.sleep(0)

    assert sum(1 for task in tasks if task.done()) == 5
    assert len(ledger.queue) == 3

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_reducing_capacity_below_usage_evicts_nothing(
    dispatch_runtime,
) -> None:
    """A lowered ceiling blocks new admission; it never terminates active work."""

    ledger, _hosts, runs = _dispatch_wave(6, capacity=4)
    tasks = [
        asyncio.create_task(_admit(run, dispatch_runtime)) for run in runs
    ]
    for _ in range(200):
        await asyncio.sleep(0)
    admitted_before = list(ledger.granted)

    ledger.profile.max_parallel_runs = 2
    ledger._drain()
    for _ in range(200):
        await asyncio.sleep(0)

    assert ledger.granted == admitted_before
    assert ledger.profile.execution_lease_count == 4
    # Releasing back to the new ceiling admits nobody until usage falls below it.
    assert ledger.release(admitted_before[0]) is True
    for _ in range(200):
        await asyncio.sleep(0)
    assert len(ledger.granted) == 3
    assert len(ledger.queue) == 2

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelling_queued_work_leaves_no_provider_or_host_side_effect(
    dispatch_runtime,
) -> None:
    """A cancelled waiter took no lease, no host, and no execution Activity."""

    ledger, hosts, runs = _dispatch_wave(6, capacity=4)
    tasks = [
        asyncio.create_task(_admit(run, dispatch_runtime)) for run in runs
    ]
    for _ in range(200):
        await asyncio.sleep(0)

    queued = list(ledger.queue)
    assert len(queued) == 2
    for index, task in enumerate(tasks):
        if runs[index].workflow_id in queued:
            task.cancel()
            ledger.cancel(runs[index].workflow_id)
    await asyncio.gather(*tasks, return_exceptions=True)

    for owner in queued:
        assert owner not in ledger.profile.current_leases
        assert owner not in ledger.profile.lease_metadata
        assert owner not in ledger.granted
    assert ledger.profile.execution_lease_count == 4
    assert hosts.active_hosts == 4
    cancelled_runs = [run for run in runs if run.workflow_id in queued]
    assert all(run.activity_calls == [] for run in cancelled_runs)


@pytest.mark.parametrize("capacity", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_every_admitted_run_carries_its_own_capacity_ticket(
    capacity: int, dispatch_runtime
) -> None:
    """Each grant binds its own plan, step, request and credential generation.

    A ticket that named another run's plan or lease owner would let the
    execution Activity establish the wrong identity by inspection.
    """

    _ledger, _hosts, runs = _dispatch_wave(capacity, capacity=capacity)

    admitted = await asyncio.gather(
        *(_admit(run, dispatch_runtime) for run in runs)
    )

    tickets = [item.admitted_provider_capacity for item in admitted]
    assert all(ticket is not None for ticket in tickets)
    assert len({ticket.lease_owner_id for ticket in tickets}) == capacity
    assert len({ticket.execution_plan_ref for ticket in tickets}) == capacity
    assert len({ticket.idempotency_key for ticket in tickets}) == capacity
    for ticket in tickets:
        assert ticket.profile_refs == (ZEN_PROFILE,)
        assert (
            ticket.profiles[0].credential_generation == CREDENTIAL_GENERATION
        )
