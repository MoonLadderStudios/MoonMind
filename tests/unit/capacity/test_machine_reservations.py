"""Machine resource reservation contract.

Source: MoonLadderStudios/MoonMind#3881 (remaining implementation 1-7;
AC1, AC3-AC8).

Host counting bounds how many containers exist. These tests cover what it
cannot express: the machine's own CPU/memory/process/temporary-storage budget,
the concurrent-initialization permit, the durable prelaunch reservation and its
bounded expiry, evidence-driven release by resource class, and reconciliation
against owned container state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import MachineCapacityReservation
from moonmind.capacity import (
    COVERED_WORKLOAD_CLASS_POLICY,
    COVERED_WORKLOAD_CLASSES,
    INITIALIZATION_PERMIT_WORKLOAD_CLASSES,
    LIMITING_RESOURCE_CPU,
    LIMITING_RESOURCE_INITIALIZING,
    LIMITING_RESOURCE_MEMORY,
    LIMITING_RESOURCE_RECONCILIATION,
    LIMITING_RESOURCE_STORAGE,
    STATE_ACTIVE,
    STATE_ADOPTED,
    STATE_PRELAUNCH,
    STATE_RELEASED,
    STATE_STORAGE_RETAINED,
    STATE_WAITING,
    WORKLOAD_CLASS_CONTAINER_JOB,
    WORKLOAD_CLASS_GENERIC_HOST,
    WORKLOAD_CLASS_OBSERVED,
    WORKLOAD_CLASS_RECONCILIATION,
    WORKLOAD_CLASS_UNATTRIBUTED,
    MachineCapacityConflict,
    MachineCapacityLedger,
    MachineCapacityUnavailable,
    MachineResourceBudget,
    MachineTotals,
    MachineUsage,
    OwnedContainer,
    OwnedContainerInventory,
    OwnedLaunchClass,
    ReleaseEvidence,
    ReservationRequest,
    ResourceDemand,
    evaluate_resource_admission,
    holds_initialization_permit,
    machine_reservation_id,
    probe_machine_totals,
    release_state_for,
)
from moonmind.capacity.docker_inventory import OWNED_CONTAINER_LABEL_FILTERS

BACKEND = "system"
OTHER_BACKEND = "second-daemon"

TOTALS = MachineTotals(
    cpu_millis=8000,
    memory_mib=16384,
    processes=8192,
    temporary_storage_mib=16384,
)


def _budget(**env: str) -> MachineResourceBudget:
    return MachineResourceBudget.from_totals(TOTALS, env=env)


def _demand(cpu: int = 1000, memory: int = 2048, procs: int = 512, storage: int = 512):
    return ResourceDemand(
        cpu_millis=cpu,
        memory_mib=memory,
        processes=procs,
        temporary_storage_mib=storage,
    )


def _request(
    owner: str,
    *,
    backend: str = BACKEND,
    workload_class: str = WORKLOAD_CLASS_GENERIC_HOST,
    demand: ResourceDemand | None = None,
    generation: int = 1,
    container_ref: str | None = None,
) -> ReservationRequest:
    return ReservationRequest(
        backend_ref=backend,
        workload_class=workload_class,
        owner_kind="omnigent_host_lease",
        owner_ref=owner,
        generation=generation,
        demand=demand or _demand(),
        plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="omnigent-launch@1",
        container_ref=container_ref,
    )


#: The generic-host launch class every reserving production caller belongs to.
HOST_LAUNCH_CLASS = OwnedLaunchClass(
    "generic_omnigent_host",
    "moonmind.owner=generic-omnigent-host",
    WORKLOAD_CLASS_GENERIC_HOST,
)
#: A launch class the documented policy accounts by observation, not reservation.
OBSERVED_LAUNCH_CLASS = OwnedLaunchClass(
    "omnigent_oauth_host", "moonmind.kind=omnigent-oauth-host"
)


def _inventory(
    containers: dict[str, ResourceDemand] | None = None,
    *,
    launch_class: OwnedLaunchClass = HOST_LAUNCH_CLASS,
    label_selectors: tuple[str, ...] = OWNED_CONTAINER_LABEL_FILTERS,
) -> OwnedContainerInventory:
    """Return a full-scope owned inventory of ``containers``."""

    return OwnedContainerInventory(
        containers={
            ref: OwnedContainer(demand=demand, launch_class=launch_class)
            for ref, demand in (containers or {}).items()
        },
        label_selectors=label_selectors,
    )


@pytest_asyncio.fixture()
async def ledger(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/capacity.db")
    async with engine.begin() as connection:
        await connection.run_sync(
            MachineCapacityReservation.__table__.create, checkfirst=True
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield MachineCapacityLedger(maker)
    finally:
        await engine.dispose()


# --------------------------------------------------------------- budget policy


def test_headroom_is_reserved_for_the_control_plane_and_cleanup() -> None:
    """AC8: a saturated workload still leaves documented headroom."""

    budget = _budget()

    assert budget.memory_mib == 16384 * 70 // 100
    headroom = budget.headroom()
    assert headroom.memory_mib == 16384 - budget.memory_mib
    assert headroom.cpu_millis > 0
    assert headroom.processes > 0
    assert headroom.temporary_storage_mib > 0


def test_managed_launches_may_never_reserve_the_whole_machine() -> None:
    with pytest.raises(ValueError, match="whole machine"):
        _budget(MOONMIND_MACHINE_UTILIZATION_PERCENT="100")


def test_an_explicit_ceiling_above_the_daemon_capacity_is_refused() -> None:
    """A configured ceiling the machine cannot honor is a configuration error."""

    with pytest.raises(ValueError, match="exceeds the machine capacity"):
        _budget(MOONMIND_MACHINE_MEMORY_MIB="99999")


def test_an_explicit_ceiling_within_the_daemon_capacity_is_honored() -> None:
    budget = _budget(MOONMIND_MACHINE_MEMORY_MIB="4096")

    assert budget.memory_mib == 4096
    # Unpinned resources still derive from the utilization share.
    assert budget.cpu_millis == 8000 * 70 // 100


@pytest.mark.parametrize(
    "env_name, field",
    [
        ("MOONMIND_MACHINE_CPU_MILLIS", "cpu_millis"),
        ("MOONMIND_MACHINE_MEMORY_MIB", "memory_mib"),
        ("MOONMIND_MACHINE_PROCESSES", "processes"),
        ("MOONMIND_MACHINE_TEMPORARY_STORAGE_MIB", "temporary_storage_mib"),
    ],
)
def test_an_explicit_ceiling_may_only_lower_the_utilization_share(
    env_name: str, field: str
) -> None:
    """AC8: no override may spend the control-plane and cleanup headroom.

    ``MOONMIND_MACHINE_UTILIZATION_PERCENT=100`` is refused precisely to keep
    that headroom, so a per-resource override equal to the daemon's own total
    must not be the way around it.
    """

    total = getattr(TOTALS, field)
    automatic = total * 70 // 100

    budget = _budget(**{env_name: str(total)})

    assert getattr(budget, field) == automatic
    assert getattr(budget.headroom(), field) == total - automatic


@pytest.mark.asyncio
async def test_an_unreadable_daemon_is_not_an_empty_machine() -> None:
    """AC7: daemon uncertainty must not manufacture capacity."""

    async def failing(_args):
        return 1, b"", b"cannot connect to the docker daemon"

    with pytest.raises(MachineCapacityUnavailable):
        await probe_machine_totals(failing)


@pytest.mark.asyncio
async def test_machine_totals_are_probed_from_the_selected_daemon() -> None:
    async def runner(args):
        assert tuple(args)[0] == "info"
        return 0, b"17179869184\t8", b""

    totals = await probe_machine_totals(runner)

    assert totals.cpu_millis == 8000
    assert totals.memory_mib == 16384
    # Temporary storage is RAM-backed tmpfs, so its total is the memory total.
    assert totals.temporary_storage_mib == 16384


# ------------------------------------------------------------ pure evaluation


def test_each_resource_can_be_the_limiting_one() -> None:
    """AC1: CPU, memory, processes and storage all participate in admission."""

    budget = _budget()
    for reserved, limiting in (
        (MachineUsage(reserved_cpu_millis=budget.cpu_millis), LIMITING_RESOURCE_CPU),
        (
            MachineUsage(reserved_memory_mib=budget.memory_mib),
            LIMITING_RESOURCE_MEMORY,
        ),
        (
            MachineUsage(reserved_temporary_storage_mib=budget.temporary_storage_mib),
            LIMITING_RESOURCE_STORAGE,
        ),
    ):
        decision = evaluate_resource_admission(
            demand=_demand(), budget=budget, usage=reserved
        )
        assert decision.admitted is False
        assert decision.limiting_resource == limiting
        assert decision.unsatisfiable is False
        assert decision.retry_after_seconds > 0


def test_a_request_larger_than_the_ceiling_is_rejected_not_queued() -> None:
    """Queueing it would wait forever; clamping would change a billed value."""

    budget = _budget()
    decision = evaluate_resource_admission(
        demand=_demand(memory=budget.memory_mib + 1),
        budget=budget,
        usage=MachineUsage(),
    )

    assert decision.admitted is False
    assert decision.unsatisfiable is True
    assert decision.retry_after_seconds == 0
    assert "exceed the configured ceiling" in decision.reason


def test_the_initialization_permit_is_separate_from_the_resource_ceiling() -> None:
    """AC5: a machine with room but no permit reports the permit."""

    budget = _budget(MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING="2")
    decision = evaluate_resource_admission(
        demand=_demand(), budget=budget, usage=MachineUsage(initializing=2)
    )

    assert decision.admitted is False
    assert decision.limiting_resource == LIMITING_RESOURCE_INITIALIZING


def test_the_permit_registry_is_restrictive_for_an_unmodelled_class() -> None:
    """#3881 FINDING-A: only a class the registry names may skip the permit.

    The permit's scope is a documented per-class policy, not a caller-supplied
    flag. A launch class nobody classified must therefore be treated as holding
    a permit, so a new class can never opt itself out by omission.
    """

    assert {policy.name for policy in COVERED_WORKLOAD_CLASS_POLICY} == set(
        COVERED_WORKLOAD_CLASSES
    )
    assert INITIALIZATION_PERMIT_WORKLOAD_CLASSES == {WORKLOAD_CLASS_GENERIC_HOST}
    assert holds_initialization_permit(WORKLOAD_CLASS_GENERIC_HOST) is True
    assert holds_initialization_permit(WORKLOAD_CLASS_CONTAINER_JOB) is False
    for unmodelled in ("", None, "reconciliation", "some_future_class"):
        assert holds_initialization_permit(unmodelled) is True


def test_a_class_the_permit_does_not_bound_is_not_held_by_it() -> None:
    """#3881 FINDING-A: an exempt class still obeys every resource ceiling."""

    budget = _budget(MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING="2")
    saturated = MachineUsage(initializing=2)

    exempt = evaluate_resource_admission(
        demand=_demand(),
        budget=budget,
        usage=saturated,
        workload_class=WORKLOAD_CLASS_CONTAINER_JOB,
    )
    assert exempt.admitted is True

    bounded = evaluate_resource_admission(
        demand=_demand(),
        budget=budget,
        usage=saturated,
        workload_class=WORKLOAD_CLASS_GENERIC_HOST,
    )
    assert bounded.admitted is False
    assert bounded.limiting_resource == LIMITING_RESOURCE_INITIALIZING

    # Exemption is from the permit only, never from the machine budget.
    full = evaluate_resource_admission(
        demand=_demand(),
        budget=budget,
        usage=MachineUsage(initializing=2, reserved_memory_mib=budget.memory_mib),
        workload_class=WORKLOAD_CLASS_CONTAINER_JOB,
    )
    assert full.admitted is False
    assert full.limiting_resource == LIMITING_RESOURCE_MEMORY


@pytest.mark.asyncio
async def test_an_exempt_classs_prelaunch_row_spends_no_permit(ledger) -> None:
    """The permit count is permits held, not prelaunch rows that exist."""

    outcome = await ledger.reserve(
        request=ReservationRequest(
            backend_ref=BACKEND,
            workload_class=WORKLOAD_CLASS_CONTAINER_JOB,
            owner_kind="container_job",
            owner_ref="job-a",
            demand=_demand(),
            container_ref="moonmind-container-job-a",
        ),
        budget=_budget(),
    )

    assert outcome.state == STATE_PRELAUNCH
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == _demand().memory_mib
    assert usage.initializing == 0


def test_unprovable_reconciliation_blocks_before_any_count_is_trusted() -> None:
    decision = evaluate_resource_admission(
        demand=_demand(),
        budget=_budget(),
        usage=MachineUsage(reconciliation_blocked=True),
    )

    assert decision.admitted is False
    assert decision.limiting_resource == LIMITING_RESOURCE_RECONCILIATION


def test_the_decision_payload_carries_no_identity() -> None:
    payload = evaluate_resource_admission(
        demand=_demand(), budget=_budget(), usage=MachineUsage()
    ).as_payload()

    flattened = repr(payload)
    assert "omnigent-execution-plan" not in flattened
    assert "sha256" not in flattened
    assert payload["utilizationPercent"] == {
        "cpu": 0,
        "memory": 0,
        "processes": 0,
        "temporaryStorage": 0,
    }
    assert payload["oldestWaiterAgeSeconds"] == 0


def test_demand_is_resolved_from_the_trusted_launch_policy_limits() -> None:
    demand = ResourceDemand.from_launch_policy_limits(
        {
            "cpuMillis": 2000,
            "memoryMiB": 4096,
            "processes": 256,
            "timeoutSeconds": 3600,
            "temporaryStorageMiB": 1024,
        }
    )

    assert demand == ResourceDemand(2000, 4096, 256, 1024)


def test_resource_units_are_integers() -> None:
    with pytest.raises(TypeError):
        ResourceDemand(cpu_millis=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ResourceDemand(memory_mib=-1)


def test_only_covered_workload_classes_may_reserve() -> None:
    with pytest.raises(ValueError, match="covered managed launch class"):
        ReservationRequest(
            backend_ref=BACKEND,
            workload_class="something_else",
            owner_kind="k",
            owner_ref="o",
            demand=_demand(),
        )


# ------------------------------------------------------------------- reserving


@pytest.mark.asyncio
async def test_workload_classes_share_one_machine_budget(ledger) -> None:
    """AC3: jobs and hosts cannot each spend the same physical budget."""

    budget = _budget(MOONMIND_MACHINE_MEMORY_MIB="4096")
    demand = _demand(memory=3000, cpu=100, procs=16, storage=16)

    host = await ledger.reserve(
        request=_request("lease-a", demand=demand), budget=budget
    )
    assert host.admitted is True

    job = await ledger.reserve(
        request=ReservationRequest(
            backend_ref=BACKEND,
            workload_class=WORKLOAD_CLASS_CONTAINER_JOB,
            owner_kind="container_job",
            owner_ref="job-a",
            demand=demand,
        ),
        budget=budget,
    )

    assert job.admitted is False
    assert job.decision.limiting_resource == LIMITING_RESOURCE_MEMORY


@pytest.mark.asyncio
async def test_independent_backends_are_not_pooled(ledger) -> None:
    """AC3: a second daemon has its own budget."""

    budget = _budget(MOONMIND_MACHINE_MEMORY_MIB="4096")
    demand = _demand(memory=3000, cpu=100, procs=16, storage=16)

    await ledger.reserve(request=_request("lease-a", demand=demand), budget=budget)
    other = await ledger.reserve(
        request=_request("lease-b", backend=OTHER_BACKEND, demand=demand),
        budget=budget,
    )

    assert other.admitted is True


@pytest.mark.asyncio
async def test_a_retry_reconciles_with_its_existing_allocation(ledger) -> None:
    """AC4: duplicate retry reuses its reservation instead of taking a second."""

    budget = _budget(MOONMIND_MACHINE_MEMORY_MIB="4096")
    demand = _demand(memory=3000, cpu=100, procs=16, storage=16)
    request = _request("lease-a", demand=demand)

    first = await ledger.reserve(request=request, budget=budget)
    second = await ledger.reserve(request=request, budget=budget)

    assert second.admitted is True
    assert second.reused is True
    assert second.reservation_id == first.reservation_id
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_a_refused_request_records_a_waiter_for_oldest_waiter_age(
    ledger,
) -> None:
    budget = _budget(MOONMIND_MACHINE_MEMORY_MIB="4096")
    demand = _demand(memory=3000, cpu=100, procs=16, storage=16)
    started = datetime.now(UTC)

    await ledger.reserve(
        request=_request("lease-a", demand=demand), budget=budget, now=started
    )
    refused = await ledger.reserve(
        request=_request("lease-b", demand=demand), budget=budget, now=started
    )

    assert refused.admitted is False
    assert refused.state == STATE_WAITING
    usage = await ledger.usage(backend_ref=BACKEND, now=started + timedelta(seconds=42))
    assert usage.waiting == 1
    assert usage.oldest_waiter_age_seconds == 42
    # A waiter reserves nothing.
    assert usage.reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_an_unsatisfiable_request_never_becomes_a_waiter(ledger) -> None:
    budget = _budget()
    outcome = await ledger.reserve(
        request=_request("lease-a", demand=_demand(memory=budget.memory_mib + 1)),
        budget=budget,
    )

    assert outcome.admitted is False
    assert outcome.unsatisfiable is True
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.waiting == 0


@pytest.mark.asyncio
async def test_a_slow_launch_holds_exactly_one_initialization_permit(
    ledger,
) -> None:
    """AC5: the permit follows actual launch state, not the clock."""

    budget = _budget(MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING="1")
    demand = _demand(cpu=10, memory=16, procs=8, storage=8)
    started = datetime.now(UTC)

    await ledger.reserve(
        request=_request("lease-slow", demand=demand), budget=budget, now=started
    )
    # Several rate windows later the slow launch is still initializing, so the
    # second launch is still refused for the permit rather than for resources.
    later = started + timedelta(seconds=120)
    blocked = await ledger.reserve(
        request=_request("lease-next", demand=demand), budget=budget, now=later
    )

    assert blocked.admitted is False
    assert blocked.decision.limiting_resource == LIMITING_RESOURCE_INITIALIZING


@pytest.mark.asyncio
async def test_a_launch_slower_than_its_prelaunch_window_keeps_its_permit(
    ledger,
) -> None:
    """#3881 FINDING-3: a slow launch loses its permit to state, not the clock.

    A cold generic-host launch pulls an image, creates the container and then
    polls for registration. That can outlast the prelaunch TTL. Because the
    reservation named its container before mutating Docker, the clock cannot
    reclaim it; the permit is still held because the launch is still
    initializing.
    """

    budget = _budget(
        MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING="1",
        MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="300",
    )
    demand = _demand(cpu=10, memory=16, procs=8, storage=8)
    started = datetime.now(UTC)
    slow = await ledger.reserve(
        request=_request("lease-slow", demand=demand, container_ref="mm-host-slow"),
        budget=budget,
        now=started,
    )

    # Well past the 300s window, the container is still coming up.
    later = started + timedelta(seconds=400)
    blocked = await ledger.reserve(
        request=_request("lease-next", demand=demand, container_ref="mm-host-next"),
        budget=budget,
        now=later,
    )

    assert blocked.admitted is False
    assert blocked.decision.limiting_resource == LIMITING_RESOURCE_INITIALIZING
    # The slow launch still holds its fence, so it may still mutate Docker.
    assert await ledger.verify(
        reservation_id=slow.reservation_id, generation=1, now=later
    )
    usage = await ledger.usage(backend_ref=BACKEND, now=later)
    assert usage.reserved_memory_mib == 16


@pytest.mark.asyncio
async def test_a_launch_that_outlived_its_window_still_confirms(ledger) -> None:
    """#3881 FINDING-3: a successful launch is never failed at confirmation."""

    budget = _budget(MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60")
    started = datetime.now(UTC)
    outcome = await ledger.reserve(
        request=_request("lease-slow", container_ref="mm-host-slow"),
        budget=budget,
        now=started,
    )

    later = started + timedelta(seconds=400)
    # Another admission runs the expiry sweep while the launch is in flight.
    await ledger.reserve(
        request=_request("lease-other", container_ref="mm-host-other"),
        budget=budget,
        now=later,
    )
    await ledger.confirm(
        reservation_id=outcome.reservation_id,
        generation=1,
        container_ref="mm-host-slow",
        now=later,
    )

    usage = await ledger.usage(backend_ref=BACKEND, now=later)
    assert usage.reserved_memory_mib == 2048 * 2
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        row = await session.get(MachineCapacityReservation, outcome.reservation_id)
        assert row.state == STATE_ACTIVE
        assert row.expires_at is None


@pytest.mark.asyncio
async def test_a_reservation_reclaimed_mid_launch_is_restored_and_fenced(
    ledger,
) -> None:
    """A fenced attempt is re-accounted and refused, never silently restored.

    Reconciliation can release an expired prelaunch reservation whose container
    was not running at the moment it looked, and an intervening launch may take
    the capacity that freed. #3881 FINDING-3 still holds — a live consumer with
    no accounting record is the worse outcome, so confirming restores the
    accounting the running container now spends. It does not grant the fenced
    attempt permission to keep it: confirmation refuses, so the caller tears the
    consumer down instead of pushing accounted usage past every ceiling.
    """

    budget = _budget(MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60")
    started = datetime.now(UTC)
    outcome = await ledger.reserve(
        request=_request("lease-slow", container_ref="mm-host-slow"),
        budget=budget,
        now=started,
    )
    later = started + timedelta(seconds=400)
    summary = await ledger.reconcile(
        backend_ref=BACKEND, inventory=_inventory(), now=later
    )
    assert summary["computeReleased"] == 1
    assert (await ledger.usage(backend_ref=BACKEND, now=later)).reserved_memory_mib == 0

    with pytest.raises(MachineCapacityConflict, match="reclaimed"):
        await ledger.confirm(
            reservation_id=outcome.reservation_id,
            generation=1,
            container_ref="mm-host-slow",
            now=later,
        )

    # The live container is accounted again, so it can never read as free
    # capacity while the fenced launch is being torn down.
    assert (
        await ledger.usage(backend_ref=BACKEND, now=later)
    ).reserved_memory_mib == 2048
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        row = await session.get(MachineCapacityReservation, outcome.reservation_id)
        assert row.state == STATE_ACTIVE
        assert row.container_ref == "mm-host-slow"
    # A different attempt still cannot write through this reservation.
    with pytest.raises(MachineCapacityConflict, match="fence does not match"):
        await ledger.confirm(
            reservation_id=outcome.reservation_id,
            generation=2,
            container_ref="mm-host-slow",
            now=later,
        )


@pytest.mark.asyncio
async def test_a_fenced_launchs_teardown_returns_the_accounting_it_restored(
    ledger,
) -> None:
    """The fence self-heals: cleanup releases exactly what confirmation restored."""

    budget = _budget(MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60")
    started = datetime.now(UTC)
    outcome = await ledger.reserve(
        request=_request("lease-slow", container_ref="mm-host-slow"),
        budget=budget,
        now=started,
    )
    later = started + timedelta(seconds=400)
    await ledger.reconcile(backend_ref=BACKEND, inventory=_inventory(), now=later)
    with pytest.raises(MachineCapacityConflict):
        await ledger.confirm(
            reservation_id=outcome.reservation_id,
            generation=1,
            container_ref="mm-host-slow",
            now=later,
        )

    state = await ledger.release(
        reservation_id=outcome.reservation_id,
        generation=1,
        evidence=ReleaseEvidence(daemon_observed=True, consumer_removed=True),
        now=later,
    )

    assert state == STATE_RELEASED
    usage = await ledger.usage(backend_ref=BACKEND, now=later)
    assert usage.reserved_memory_mib == 0
    assert usage.reserved_temporary_storage_mib == 0


@pytest.mark.asyncio
async def test_reconciliation_leaves_an_in_flight_launch_alone(ledger) -> None:
    """A named container inside its launch window is not a vanished consumer."""

    budget = _budget(MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="300")
    started = datetime.now(UTC)
    outcome = await ledger.reserve(
        request=_request("lease-slow", container_ref="mm-host-slow"),
        budget=budget,
        now=started,
    )

    summary = await ledger.reconcile(
        backend_ref=BACKEND,
        inventory=_inventory(),
        now=started + timedelta(seconds=120),
    )

    assert summary["computeReleased"] == 0
    assert await ledger.verify(reservation_id=outcome.reservation_id, generation=1)


@pytest.mark.asyncio
async def test_confirming_a_launch_releases_its_initialization_permit(
    ledger,
) -> None:
    budget = _budget(MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING="1")
    demand = _demand(cpu=10, memory=16, procs=8, storage=8)
    first = await ledger.reserve(
        request=_request("lease-a", demand=demand), budget=budget
    )
    await ledger.confirm(
        reservation_id=first.reservation_id, generation=1, container_ref="mm-host-a"
    )

    second = await ledger.reserve(
        request=_request("lease-b", demand=demand), budget=budget
    )

    assert second.admitted is True
    usage = await ledger.usage(backend_ref=BACKEND)
    # The confirmed launch still spends the machine; only its permit is back.
    assert usage.initializing == 1
    assert usage.reserved_memory_mib == 32


# ----------------------------------------------------------- fence and expiry


@pytest.mark.asyncio
async def test_the_fence_is_reverifiable_before_a_docker_mutation(ledger) -> None:
    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)

    assert await ledger.verify(reservation_id=outcome.reservation_id, generation=1)
    # A different generation is a different attempt and holds nothing.
    assert not await ledger.verify(reservation_id=outcome.reservation_id, generation=2)
    assert not await ledger.verify(reservation_id="not-a-reservation", generation=1)


@pytest.mark.asyncio
async def test_an_expired_unlaunched_reservation_stops_holding_the_fence(
    ledger,
) -> None:
    """AC4: an expired prelaunch reservation must not gate a Docker mutation."""

    budget = _budget(MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60")
    started = datetime.now(UTC)
    outcome = await ledger.reserve(
        request=_request("lease-a"), budget=budget, now=started
    )

    assert not await ledger.verify(
        reservation_id=outcome.reservation_id,
        generation=1,
        now=started + timedelta(seconds=61),
    )


@pytest.mark.asyncio
async def test_an_expired_unlaunched_reservation_is_reclaimed_once(ledger) -> None:
    """AC6: proven-unused resources are eventually released, exactly once."""

    budget = _budget(
        MOONMIND_MACHINE_MEMORY_MIB="4096", MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60"
    )
    demand = _demand(memory=3000, cpu=100, procs=16, storage=16)
    started = datetime.now(UTC)
    await ledger.reserve(
        request=_request("lease-lost", demand=demand), budget=budget, now=started
    )

    # The worker holding it died. A later admission reclaims it and succeeds.
    later = started + timedelta(seconds=120)
    recovered = await ledger.reserve(
        request=_request("lease-next", demand=demand), budget=budget, now=later
    )

    assert recovered.admitted is True
    usage = await ledger.usage(backend_ref=BACKEND, now=later)
    assert usage.reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_a_live_consumer_is_never_reclaimed_by_the_clock(ledger) -> None:
    """AC6: a live consumer keeps its accounting even if its workflow died."""

    budget = _budget(
        MOONMIND_MACHINE_MEMORY_MIB="4096", MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60"
    )
    demand = _demand(memory=3000, cpu=100, procs=16, storage=16)
    started = datetime.now(UTC)
    outcome = await ledger.reserve(
        request=_request("lease-live", demand=demand), budget=budget, now=started
    )
    await ledger.confirm(
        reservation_id=outcome.reservation_id,
        generation=1,
        container_ref="mm-host-live",
        now=started,
    )

    later = started + timedelta(hours=4)
    blocked = await ledger.reserve(
        request=_request("lease-next", demand=demand), budget=budget, now=later
    )

    assert blocked.admitted is False
    assert await ledger.verify(
        reservation_id=outcome.reservation_id, generation=1, now=later
    )


@pytest.mark.asyncio
async def test_a_stale_generation_cannot_confirm_a_newer_attempt(ledger) -> None:
    from moonmind.capacity import MachineCapacityConflict

    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)

    with pytest.raises(MachineCapacityConflict):
        await ledger.confirm(
            reservation_id=outcome.reservation_id,
            generation=99,
            container_ref="mm-host-a",
        )


# ---------------------------------------------------------------- release


def test_release_conditions_are_defined_by_resource_class() -> None:
    """Implementation 7: compute and storage release on different evidence."""

    assert (
        release_state_for(ReleaseEvidence(daemon_observed=True, consumer_removed=True))
        == STATE_RELEASED
    )
    assert (
        release_state_for(
            ReleaseEvidence(
                daemon_observed=True, consumer_removed=True, storage_retained=True
            )
        )
        == STATE_STORAGE_RETAINED
    )
    assert (
        release_state_for(ReleaseEvidence(daemon_observed=True, consumer_stopped=True))
        == STATE_STORAGE_RETAINED
    )
    # Nothing proven: an unreadable daemon and a running consumer both retain.
    assert release_state_for(ReleaseEvidence(daemon_observed=False)) is None
    assert release_state_for(ReleaseEvidence(daemon_observed=True)) is None


@pytest.mark.asyncio
async def test_storage_remains_accounted_while_a_retained_volume_consumes_it(
    ledger,
) -> None:
    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)
    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-a"
    )

    state = await ledger.release(
        reservation_id=outcome.reservation_id,
        generation=1,
        evidence=ReleaseEvidence(
            daemon_observed=True, consumer_removed=True, storage_retained=True
        ),
    )

    assert state == STATE_STORAGE_RETAINED
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0
    assert usage.reserved_cpu_millis == 0
    assert usage.reserved_temporary_storage_mib == 512


@pytest.mark.asyncio
async def test_a_cleanup_that_proved_nothing_releases_nothing(ledger) -> None:
    """AC7: cleanup failure must not create phantom free capacity."""

    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)
    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-a"
    )

    state = await ledger.release(
        reservation_id=outcome.reservation_id,
        generation=1,
        evidence=ReleaseEvidence(daemon_observed=False, consumer_removed=True),
    )

    assert state is None
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 2048


@pytest.mark.asyncio
async def test_releasing_twice_releases_once(ledger) -> None:
    """AC6: proven-unused resources are released once, not twice."""

    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)
    evidence = ReleaseEvidence(daemon_observed=True, consumer_removed=True)

    assert (
        await ledger.release(
            reservation_id=outcome.reservation_id, generation=1, evidence=evidence
        )
        == STATE_RELEASED
    )
    assert (
        await ledger.release(
            reservation_id=outcome.reservation_id, generation=1, evidence=evidence
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_late_cleanup_cannot_release_a_newer_generation(ledger) -> None:
    budget = _budget()
    await ledger.reserve(request=_request("lease-a", generation=2), budget=budget)
    stale_id = machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="omnigent_host_lease",
        owner_ref="lease-a",
        generation=1,
    )

    released = await ledger.release(
        reservation_id=stale_id,
        generation=1,
        evidence=ReleaseEvidence(daemon_observed=True, consumer_removed=True),
    )

    assert released is None
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 2048


# --------------------------------------------------------------- reconciling


@pytest.mark.asyncio
async def test_an_owned_live_container_without_a_record_is_a_fault_not_capacity(
    ledger,
) -> None:
    """AC7: an unaccounted owned live container is never free capacity."""

    summary = await ledger.reconcile(
        backend_ref=BACKEND,
        inventory=_inventory(
            {"mm-job-orphan": _demand(cpu=500, memory=1024, procs=64)}
        ),
    )

    assert summary["adopted"] == 1
    assert summary["reconciliationFaults"] == 1
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 1024
    assert usage.reconciliation_faults == 1


@pytest.mark.asyncio
async def test_an_unreadable_backend_blocks_admission_rather_than_freeing_it(
    ledger,
) -> None:
    """AC7: unknown daemon state blocks unsafe new admission."""

    summary = await ledger.reconcile(backend_ref=BACKEND, inventory=None)

    async with ledger._factory()() as session:  # persisted contract
        marker = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="reconciliation",
                owner_ref=BACKEND,
                generation=1,
            ),
        )
    assert marker.workload_class == WORKLOAD_CLASS_RECONCILIATION
    assert WORKLOAD_CLASS_RECONCILIATION not in COVERED_WORKLOAD_CLASSES
    assert summary["backendObserved"] is False
    assert summary["admissionBlocked"] is True
    outcome = await ledger.reserve(request=_request("lease-a"), budget=_budget())
    assert outcome.admitted is False
    assert outcome.decision.limiting_resource == LIMITING_RESOURCE_RECONCILIATION


@pytest.mark.asyncio
async def test_a_readable_backend_clears_the_admission_block(ledger) -> None:
    await ledger.reconcile(backend_ref=BACKEND, inventory=None)
    await ledger.reconcile(backend_ref=BACKEND, inventory=_inventory())

    outcome = await ledger.reserve(request=_request("lease-a"), budget=_budget())

    assert outcome.admitted is True


@pytest.mark.asyncio
async def test_reconciliation_releases_compute_for_a_vanished_consumer(
    ledger,
) -> None:
    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)
    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-a"
    )

    summary = await ledger.reconcile(backend_ref=BACKEND, inventory=_inventory())

    assert summary["computeReleased"] == 1
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0
    # Storage stays accounted until cleanup proves the volume is gone.
    assert usage.reserved_temporary_storage_mib == 512


@pytest.mark.asyncio
async def test_reconciliation_never_touches_an_initializing_reservation(
    ledger,
) -> None:
    """A prelaunch reservation has no container yet; that is not a fault."""

    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)

    summary = await ledger.reconcile(backend_ref=BACKEND, inventory=_inventory())

    assert summary["adopted"] == 0
    assert summary["computeReleased"] == 0
    assert await ledger.verify(reservation_id=outcome.reservation_id, generation=1)


@pytest.mark.asyncio
async def test_reconciliation_is_scoped_to_one_backend(ledger) -> None:
    budget = _budget()
    outcome = await ledger.reserve(
        request=_request("lease-a", backend=OTHER_BACKEND), budget=budget
    )
    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-a"
    )

    await ledger.reconcile(backend_ref=BACKEND, inventory=_inventory())

    usage = await ledger.usage(backend_ref=OTHER_BACKEND)
    assert usage.reserved_memory_mib == 2048


@pytest.mark.asyncio
async def test_a_container_job_spends_the_same_budget_as_a_host(ledger) -> None:
    budget = _budget(MOONMIND_MACHINE_MEMORY_MIB="4096")
    demand = _demand(memory=3000, cpu=100, procs=16, storage=16)
    await ledger.reserve(
        request=ReservationRequest(
            backend_ref=BACKEND,
            workload_class=WORKLOAD_CLASS_CONTAINER_JOB,
            owner_kind="container_job",
            owner_ref="job-a",
            demand=demand,
        ),
        budget=budget,
    )

    blocked = await ledger.reserve(
        request=_request("lease-a", demand=demand), budget=budget
    )

    assert blocked.admitted is False


@pytest.mark.asyncio
async def test_an_observed_launch_class_spends_the_budget_without_faulting(
    ledger,
) -> None:
    """#3881 FINDING-2: an OAuth host reserves nothing but is not free capacity.

    Resource admission must never refuse a credential-authority launch, so that
    launch class is accounted from daemon evidence instead. Its demand still
    reduces what reserving launches may take, and it is not a reconciliation
    fault, because nothing was ever supposed to reserve it.
    """

    budget = _budget(MOONMIND_MACHINE_MEMORY_MIB="4096")
    demand = _demand(memory=3000, cpu=100, procs=16, storage=0)

    await ledger.reconcile(
        backend_ref=BACKEND,
        inventory=_inventory(
            {"mm-oauth-host-a": demand}, launch_class=OBSERVED_LAUNCH_CLASS
        ),
    )

    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000
    # Accounted, but not a fault: the policy never asked it to reserve.
    assert usage.reconciliation_faults == 0
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        row = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="adopted_container",
                owner_ref="mm-oauth-host-a",
                generation=1,
            ),
        )
    assert row.workload_class == WORKLOAD_CLASS_OBSERVED
    blocked = await ledger.reserve(
        request=_request("lease-a", demand=demand), budget=budget
    )
    assert blocked.admitted is False


@pytest.mark.asyncio
async def test_a_partial_inventory_may_adopt_but_never_release(ledger) -> None:
    """#3881 FINDING-1: absence from an unenumerated scope proves nothing.

    A caller that enumerated only its own launch class must not release the
    accounting of a launch class it never looked for.
    """

    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-live"), budget=budget)
    await ledger.confirm(
        reservation_id=outcome.reservation_id,
        generation=1,
        container_ref="mm-host-live",
    )

    summary = await ledger.reconcile(
        backend_ref=BACKEND,
        inventory=_inventory(
            {"mm-job-orphan": _demand(cpu=10, memory=64, procs=8, storage=0)},
            launch_class=OwnedLaunchClass(
                "container_job", "moonmind.container_job", WORKLOAD_CLASS_CONTAINER_JOB
            ),
            label_selectors=("moonmind.container_job",),
        ),
    )

    assert summary["scopeComplete"] is False
    assert summary["computeReleased"] == 0
    # The unaccounted container it *did* see is still adopted: adding
    # accounting is always safe.
    assert summary["adopted"] == 1
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 2048 + 64


@pytest.mark.asyncio
async def test_a_storage_only_row_never_releases_its_live_containers_adoption(
    ledger,
) -> None:
    """#3881 AC7: a live consumer keeps compute accounting through the hand-off.

    Cleanup reported the consumer removed and its volume retained, so the
    reservation accounts storage and no compute. The container is nevertheless
    still running, so reconciliation adopts it — a live container is never free
    capacity. When the owner then reserves again for that exact container, the
    adopted row may only be released by a reservation that takes the compute
    over: releasing it against a storage-only row would leave a running
    container's CPU, memory and processes unaccounted.
    """

    budget = _budget()
    request = _request("lease-a", container_ref="mm-host-a")
    outcome = await ledger.reserve(request=request, budget=budget)
    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-host-a"
    )
    await ledger.release(
        reservation_id=outcome.reservation_id,
        generation=1,
        evidence=ReleaseEvidence(
            daemon_observed=True, consumer_removed=True, storage_retained=True
        ),
    )
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 0

    summary = await ledger.reconcile(
        backend_ref=BACKEND,
        inventory=_inventory({"mm-host-a": _demand(storage=0)}),
    )
    assert summary["adopted"] == 1
    assert (await ledger.usage(backend_ref=BACKEND)).reconciliation_faults == 1

    reused = await ledger.reserve(request=request, budget=budget)

    assert reused.admitted is True
    assert reused.reused is True
    assert reused.state == STATE_ACTIVE
    usage = await ledger.usage(backend_ref=BACKEND)
    # One live container, accounted exactly once and never dropped to zero.
    assert usage.reserved_memory_mib == 2048
    assert usage.reserved_cpu_millis == 1000
    assert usage.reserved_processes == 512
    assert usage.reconciliation_faults == 0
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        owner = await session.get(MachineCapacityReservation, outcome.reservation_id)
        adopted = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="adopted_container",
                owner_ref="mm-host-a",
                generation=1,
            ),
        )
    assert owner.state == STATE_ACTIVE
    assert owner.container_ref == "mm-host-a"
    assert adopted.state == STATE_RELEASED


@pytest.mark.asyncio
async def test_reservation_state_names_are_the_persisted_contract(ledger) -> None:
    """The states admission counts on are the states actually written."""

    budget = _budget()
    outcome = await ledger.reserve(request=_request("lease-a"), budget=budget)
    assert outcome.state == STATE_PRELAUNCH

    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-a"
    )
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        row = await session.get(MachineCapacityReservation, outcome.reservation_id)
        assert row.state == STATE_ACTIVE
        assert row.container_ref == "mm-a"
        assert row.expires_at is None
        assert row.backend_ref == BACKEND
        assert row.host_class_ref == "omnigent-opencode@1"
        assert row.launch_policy_ref == "omnigent-launch@1"
        assert row.generation == 1

    await ledger.reconcile(
        backend_ref=BACKEND, inventory=_inventory({"mm-orphan": _demand()})
    )
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        adopted = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="adopted_container",
                owner_ref="mm-orphan",
                generation=1,
            ),
        )
        assert adopted.state == STATE_ADOPTED
        # Which managed launch created it is not recoverable from the container
        # alone, so it is recorded as unattributed rather than misattributed.
        assert adopted.workload_class == WORKLOAD_CLASS_UNATTRIBUTED


def test_the_reservation_columns_admit_the_identities_that_reach_them() -> None:
    """A durable reservation must not be narrower than its own inputs.

    ``backend_ref`` is selected by deployment code and already travels through
    ``ResolvedContainerLaunchPlan`` and the ``container_jobs`` projection at the
    existing contract width. A narrower column here would fail an already-valid
    plan — including one an in-flight workflow is carrying — with a
    value-too-long database error at its first durable reservation.
    """

    from moonmind.schemas.container_job_models import ResolvedContainerLaunchPlan

    contract_width = max(
        meta.max_length
        for meta in ResolvedContainerLaunchPlan.model_fields["backend_ref"].metadata
        if getattr(meta, "max_length", None) is not None
    )
    columns = MachineCapacityReservation.__table__.columns

    assert columns["backend_ref"].type.length >= contract_width
    assert columns["owner_ref"].type.length >= contract_width
    assert columns["container_ref"].type.length >= contract_width


def test_the_migration_creates_the_columns_the_model_declares() -> None:
    """The durable width is whatever the migration actually created.

    A model widened without its migration still fails the launch it was widened
    for, because PostgreSQL enforces the column the migration created.
    """

    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[3]
        / "api_service"
        / "migrations"
        / "versions"
        / "372_machine_reservations.py"
    ).read_text()

    created: dict[str, int] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "Column" or not node.args:
            continue
        name = node.args[0]
        column_type = node.args[1] if len(node.args) > 1 else None
        if not isinstance(name, ast.Constant) or not isinstance(column_type, ast.Call):
            continue
        for keyword in column_type.keywords:
            if keyword.arg == "length" and isinstance(keyword.value, ast.Constant):
                created[name.value] = keyword.value.value

    columns = MachineCapacityReservation.__table__.columns
    for name, length in created.items():
        assert columns[name].type.length == length, name


@pytest.mark.asyncio
async def test_an_admitted_reservation_always_survives_its_own_fence(
    ledger,
) -> None:
    """#3881 FINDING-D: reserve must never admit what verify then refuses.

    ``reserve_within`` is advisory only in the sense that another worker may
    win the machine between it and the Docker mutation. It is never allowed to
    hand back an admitted outcome the very next ``verify_within`` refuses on
    the same state: that failure has no waiting path, so the caller can only
    fail a launch nothing is actually refusing.

    Both reservers share this method, so the invariant covers the container-job
    boundary and the generic-host lease boundary at once.
    """

    # The permit layer is raised out of the way: this test is about the
    # reservation the fence sees, not about how many launches may initialize.
    budget = _budget(MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING="8")
    states: list[str] = []
    for owner, prepare in (
        ("lease-fresh", None),
        ("lease-retried", "retry"),
        ("lease-storage-retained", "storage_retained"),
        ("lease-expired", "expired"),
    ):
        request = _request(owner, container_ref=f"mm-{owner}")
        outcome = await ledger.reserve(request=request, budget=budget)
        if prepare == "storage_retained":
            # Reconciliation proved the consumer is not running, so its compute
            # was released while its retained storage stayed accounted.
            await ledger.confirm(
                reservation_id=outcome.reservation_id,
                generation=1,
                container_ref=f"mm-{owner}",
            )
            await ledger.release(
                reservation_id=outcome.reservation_id,
                generation=1,
                evidence=ReleaseEvidence(
                    daemon_observed=True,
                    consumer_stopped=True,
                    storage_retained=True,
                ),
            )
        if prepare == "expired":
            async with ledger._factory()() as session:  # noqa: SLF001 - persisted
                row = await session.get(
                    MachineCapacityReservation, outcome.reservation_id
                )
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
        if prepare is not None:
            outcome = await ledger.reserve(request=request, budget=budget)
        states.append(outcome.state)
        if outcome.admitted:
            assert await ledger.verify(
                reservation_id=outcome.reservation_id, generation=1
            ), f"{owner} was admitted in state {outcome.state} the fence refuses"
        else:
            # A refusal must name the resource that refused it so the caller
            # can wait or report the true cause.
            assert outcome.decision.limiting_resource is not None

    # The storage-retained retry is the case the container-job workflow takes,
    # and it must come back holding compute again rather than storage only.
    assert STATE_STORAGE_RETAINED not in states


@pytest.mark.asyncio
async def test_a_stopped_consumers_retry_is_readmitted_not_reported_reused(
    ledger,
) -> None:
    """#3881 FINDING-D: the row a retry gets back must hold compute again.

    Reconciliation is right to release the compute of a consumer the daemon
    proves is not running. The retry that follows is therefore a real admission
    against the machine, not a reuse of accounting nobody is holding.
    """

    budget = _budget()
    request = _request("lease-a", container_ref="mm-host-a")
    outcome = await ledger.reserve(request=request, budget=budget)
    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-host-a"
    )
    # The consumer exists but is stopped, so a complete enumeration of running
    # owned containers does not list it.
    await ledger.reconcile(backend_ref=BACKEND, inventory=_inventory({}))
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        row = await session.get(MachineCapacityReservation, outcome.reservation_id)
        assert row.state == STATE_STORAGE_RETAINED
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 0

    retried = await ledger.reserve(request=request, budget=budget)

    assert retried.admitted is True
    assert retried.state == STATE_PRELAUNCH
    assert retried.reservation_id == outcome.reservation_id
    assert await ledger.verify(reservation_id=outcome.reservation_id, generation=1)
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 2048
    assert usage.reserved_cpu_millis == 1000
    assert usage.reserved_processes == 512
    # The retained storage this row already held is counted once, not twice.
    assert usage.reserved_temporary_storage_mib == 512
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        row = await session.get(MachineCapacityReservation, outcome.reservation_id)
        assert row.container_ref == "mm-host-a"
        assert row.expires_at is not None


@pytest.mark.asyncio
async def test_a_stopped_consumers_retry_is_refused_by_the_real_resource(
    ledger,
) -> None:
    """#3881 FINDING-D: a genuinely full machine refuses with its own reason.

    The retry's re-admission is a real admission, so when the machine filled up
    while the consumer was stopped it must be refused by the resource that is
    actually holding it — and it must not overwrite the storage the retained
    row is still spending with a waiter marker that accounts nothing.
    """

    budget = _budget(
        MOONMIND_MACHINE_MEMORY_MIB="5120",
        MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING="8",
    )
    request = _request("lease-a", container_ref="mm-host-a")
    outcome = await ledger.reserve(request=request, budget=budget)
    await ledger.confirm(
        reservation_id=outcome.reservation_id, generation=1, container_ref="mm-host-a"
    )
    await ledger.reconcile(backend_ref=BACKEND, inventory=_inventory({}))
    # Two other launches took the machine while this consumer was stopped.
    for owner in ("lease-b", "lease-c"):
        admitted = await ledger.reserve(
            request=_request(owner, container_ref=f"mm-{owner}"), budget=budget
        )
        assert admitted.admitted is True

    refused = await ledger.reserve(request=request, budget=budget)

    assert refused.admitted is False
    assert refused.decision.limiting_resource == LIMITING_RESOURCE_MEMORY
    assert refused.decision.unsatisfiable is False
    assert not await ledger.verify(reservation_id=outcome.reservation_id, generation=1)
    async with ledger._factory()() as session:  # noqa: SLF001 - persisted contract
        row = await session.get(MachineCapacityReservation, outcome.reservation_id)
    # Still accounting the storage its consumer retained: a refusal frees
    # nothing, and a waiter marker here would read as free capacity.
    assert row.state == STATE_STORAGE_RETAINED
    assert (
        await ledger.usage(backend_ref=BACKEND)
    ).reserved_temporary_storage_mib == 512 * 3
