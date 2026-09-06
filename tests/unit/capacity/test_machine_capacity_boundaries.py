"""Machine-capacity accounting at its real production boundaries.

Source: MoonLadderStudios/MoonMind#3881 (remaining implementation 2-8;
AC1, AC3-AC8).

The ledger's own contract is covered in ``test_machine_reservations.py``. These
tests exercise the boundaries that consume it — the generic-host admission
decision, the durable host-lease allocation, the container-job launch, the
owned-container inventory and the janitor — because a correct ledger wired to
nothing enforces nothing.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    MachineCapacityReservation,
    OmnigentHostBindingRecordV2,
    OmnigentHostLeaseRecordV2,
)
from moonmind.capacity import (
    COVERED_WORKLOAD_CLASSES,
    LIMITING_RESOURCE_CPU,
    LIMITING_RESOURCE_INITIALIZING,
    LIMITING_RESOURCE_MEMORY,
    LIMITING_RESOURCE_PROCESSES,
    LIMITING_RESOURCE_RECONCILIATION,
    OWNED_CONTAINER_LABEL_FILTERS,
    OWNED_LAUNCH_CLASSES,
    STATE_ACTIVE,
    STATE_ADOPTED,
    STATE_BLOCKED,
    STATE_STORAGE_RETAINED,
    STATE_WAITING,
    WORKLOAD_CLASS_CONTAINER_JOB,
    WORKLOAD_CLASS_GENERIC_HOST,
    MachineCapacityLedger,
    MachineResourceBudget,
    MachineTotals,
    OwnedContainer,
    OwnedContainerInventory,
    OwnedLaunchClass,
    ReservationRequest,
    ResourceDemand,
    machine_reservation_id,
    probe_owned_containers,
)
from moonmind.omnigent.generic_host_janitor import GenericOmnigentHostJanitor
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_capacity import (
    LIMITING_LAYER_HOST_CAPACITY,
    LIMITING_LAYER_MACHINE_RESOURCES,
    GenericHostCapacityAdmission,
)
from moonmind.omnigent.host_leases import (
    DbOmnigentHostLeaseRepository,
    generic_host_lease_ref,
)

BACKEND = "system"
TOTALS = MachineTotals(
    cpu_millis=16000,
    memory_mib=10000,
    processes=16384,
    temporary_storage_mib=10000,
)
#: The trusted Launch Policy limits admission resolves demand from.
LAUNCH_POLICY_LIMITS = {
    "cpuMillis": 1000,
    "memoryMiB": 3000,
    "processes": 256,
    "timeoutSeconds": 1800,
    "temporaryStorageMiB": 256,
}


HOST_LAUNCH_CLASS = OwnedLaunchClass(
    "generic_omnigent_host",
    "moonmind.owner=generic-omnigent-host",
    WORKLOAD_CLASS_GENERIC_HOST,
)
JOB_LAUNCH_CLASS = OwnedLaunchClass(
    "container_job", "moonmind.container_job", WORKLOAD_CLASS_CONTAINER_JOB
)


def _owned_inventory(
    containers: dict[str, ResourceDemand] | None = None,
    *,
    launch_class: OwnedLaunchClass = HOST_LAUNCH_CLASS,
) -> OwnedContainerInventory:
    return OwnedContainerInventory(
        containers={
            ref: OwnedContainer(demand=demand, launch_class=launch_class)
            for ref, demand in (containers or {}).items()
        },
        label_selectors=OWNED_CONTAINER_LABEL_FILTERS,
    )


def _budget(**env: str) -> MachineResourceBudget:
    return MachineResourceBudget.from_totals(
        TOTALS,
        env={"MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING": "8", **env},
    )


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/boundaries.db")
    async with engine.begin() as connection:
        for table in (
            MachineCapacityReservation.__table__,
            OmnigentHostBindingRecordV2.__table__,
            OmnigentHostLeaseRecordV2.__table__,
        ):
            await connection.run_sync(table.create, checkfirst=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


def _admission(session_factory, *, host_capacity: int = 8):
    return GenericHostCapacityAdmission(
        session_factory=session_factory,
        host_capacity=host_capacity,
        cold_launch_burst=8,
        cold_launch_window_seconds=30,
        machine_capacity=MachineCapacityLedger(session_factory),
        backend_ref=BACKEND,
    )


def _repository(session_factory, admission):
    async def budget():
        return _budget()

    return DbOmnigentHostLeaseRepository(
        session_factory,
        capacity_admission=admission,
        machine_budget_provider=budget,
    )


async def _acquire(repository, binding_id: str):
    return await repository.acquire(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        runtime_binding_id=binding_id,
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="omnigent-launch@1",
        harness_id="opencode-native",
        harness_implementation_ref="opencode-native@1",
        provider_profile_refs=("opencode-zen-free",),
        resource_limits=LAUNCH_POLICY_LIMITS,
        ttl_seconds=3600,
    )


def _host_reservation_id(binding_id: str) -> str:
    """Return the machine reservation id one host allocation owns.

    Derived exactly the way the allocation derives it, so the row this names
    is the row ``DbOmnigentHostLeaseRepository.acquire`` writes rather than a
    test-local identity that happens to look similar.
    """

    return machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="omnigent_host_lease",
        owner_ref=generic_host_lease_ref(
            runtime_binding_id=binding_id, host_class_ref="omnigent-opencode@1"
        ),
        generation=1,
    )


async def _reservation_row(session_factory, binding_id: str):
    async with session_factory() as session:
        return await session.get(
            MachineCapacityReservation, _host_reservation_id(binding_id)
        )


# ------------------------------------------------- host allocation boundary


@pytest.mark.asyncio
async def test_the_lease_transaction_reserves_the_machine_it_counted(
    session_factory,
) -> None:
    """AC1: the durable allocation spends CPU, memory, processes and storage."""

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)

    lease = await _acquire(repository, "binding-a")

    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000
    assert usage.reserved_cpu_millis == 1000
    assert usage.reserved_processes == 256
    assert usage.reserved_temporary_storage_mib == 256
    # The reservation names the exact lease it belongs to.
    assert lease.leaseRef == generic_host_lease_ref(
        runtime_binding_id="binding-a", host_class_ref="omnigent-opencode@1"
    )


@pytest.mark.asyncio
async def test_the_machine_budget_refuses_a_host_the_host_count_would_admit(
    session_factory,
) -> None:
    """AC1: host counting is intact but is no longer the only limit."""

    admission = _admission(session_factory, host_capacity=8)
    repository = _repository(session_factory, admission)

    for index in range(2):
        await _acquire(repository, f"binding-{index}")

    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-2")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )
    assert "missing_condition=machine_memory" in str(raised.value)


@pytest.mark.asyncio
async def test_a_machine_refused_host_allocation_persists_its_waiter(
    session_factory,
) -> None:
    """Implementation 8: host contention has to reach oldest-waiter age.

    The waiter marker is written inside the allocating transaction, and that
    transaction unwinds when the machine layer refuses. Unwinding the refused
    *reservation* is what protects the machine; unwinding the marker with it
    made oldest waiter age structurally zero for the generic-host class, the
    only class whose durable wait this layer exists to report.
    """

    admission = _admission(session_factory, host_capacity=8)
    repository = _repository(session_factory, admission)
    for index in range(2):
        await _acquire(repository, f"binding-{index}")

    refused_at = datetime.now(UTC)
    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-2")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )
    ledger = MachineCapacityLedger(session_factory)
    usage = await ledger.usage(
        backend_ref=BACKEND, now=refused_at + timedelta(seconds=45)
    )
    assert usage.waiting == 1
    assert usage.oldest_waiter_age_seconds >= 30
    # The recorded wait is the marker's own age, so it advances with the clock
    # rather than being re-stamped by whatever last read it.
    later = await ledger.usage(
        backend_ref=BACKEND, now=refused_at + timedelta(seconds=105)
    )
    assert later.oldest_waiter_age_seconds - usage.oldest_waiter_age_seconds == 60
    # The refusal still takes nothing: only the two admitted hosts account.
    assert usage.reserved_memory_mib == 6000
    # The operator-facing advisory precheck reads the same durable marker, so
    # the contention a healthy host count hides is now visible there too.
    advisory = await admission.evaluate(now=refused_at + timedelta(seconds=45))
    assert advisory.as_payload()["oldestWaiterAgeSeconds"] >= 30
    refused = await _reservation_row(session_factory, "binding-2")
    assert refused is not None
    assert refused.state == STATE_WAITING
    # A waiter marker is an observation, never an allocation: the refused
    # attempt must not leave a prelaunch reservation or a lease behind.
    async with session_factory() as session:
        lease = await session.get(
            OmnigentHostLeaseRecordV2,
            generic_host_lease_ref(
                runtime_binding_id="binding-2",
                host_class_ref="omnigent-opencode@1",
            ),
        )
    assert lease is None


@pytest.mark.asyncio
async def test_a_host_refused_at_the_host_count_layer_records_no_waiter(
    session_factory,
) -> None:
    """A layer that never reached the machine must not fabricate a waiter.

    The waiter marker names a wait on machine resources. Writing one for a
    refusal the host-count ceiling produced would report machine contention
    that never happened.
    """

    admission = _admission(session_factory, host_capacity=1)
    repository = _repository(session_factory, admission)
    await _acquire(repository, "binding-a")

    with pytest.raises(HarnessPlatformError):
        await _acquire(repository, "binding-b")

    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.waiting == 0
    assert await _reservation_row(session_factory, "binding-b") is None


@pytest.mark.asyncio
async def test_an_unreadable_machine_budget_refuses_with_a_typed_code(
    session_factory,
) -> None:
    """Implementation 6: an unreadable daemon is a typed, routable refusal.

    ``machine_budget_from_runner`` is the exact provider production wires at
    ``moonmind/omnigent/production.py``, so this drives the real probe over a
    daemon that cannot be read rather than a stand-in that raises on cue.
    """

    from moonmind.capacity import machine_budget_from_runner

    async def runner(argv):
        assert tuple(argv)[0] == "info"
        return 1, b"", b"Cannot connect to the Docker daemon"

    async def budget():
        return await machine_budget_from_runner(runner)

    repository = DbOmnigentHostLeaseRepository(
        session_factory,
        capacity_admission=_admission(session_factory),
        machine_budget_provider=budget,
    )

    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-a")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )
    assert "Cannot connect to the Docker daemon" in str(raised.value)
    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0
    assert usage.waiting == 0


@pytest.mark.asyncio
async def test_a_launch_policy_larger_than_the_ceiling_is_rejected_not_queued(
    session_factory,
) -> None:
    """The remediation is a compatible policy, not an unbounded wait."""

    admission = _admission(session_factory)

    async def budget():
        return _budget(MOONMIND_MACHINE_MEMORY_MIB="1024")

    repository = DbOmnigentHostLeaseRepository(
        session_factory,
        capacity_admission=admission,
        machine_budget_provider=budget,
    )

    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-big")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE.value
    )


@pytest.mark.asyncio
async def test_the_host_count_ceiling_still_reports_its_own_layer(
    session_factory,
) -> None:
    """AC1: the admission already on main is preserved, not replaced."""

    admission = _admission(session_factory, host_capacity=1)
    repository = _repository(session_factory, admission)
    await _acquire(repository, "binding-a")

    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-b")

    assert f"missing_condition={LIMITING_LAYER_HOST_CAPACITY}" in str(raised.value)
    # A refusal at the host-count layer must not take a machine reservation.
    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_a_lease_retry_reuses_its_own_machine_reservation(
    session_factory,
) -> None:
    """AC4: duplicate retry reconciles rather than taking a second allocation."""

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)

    first = await _acquire(repository, "binding-a")
    second = await _acquire(repository, "binding-a")

    assert second.leaseRef == first.leaseRef
    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_the_admission_decision_reports_the_machine_view(
    session_factory,
) -> None:
    """Implementation 8: utilization, ceilings, waiter age and health."""

    ledger = MachineCapacityLedger(session_factory)
    await ledger.reserve(
        request=ReservationRequest(
            backend_ref=BACKEND,
            workload_class=WORKLOAD_CLASS_GENERIC_HOST,
            owner_kind="omnigent_host_lease",
            owner_ref="lease-a",
            demand=ResourceDemand.from_launch_policy_limits(LAUNCH_POLICY_LIMITS),
        ),
        budget=_budget(),
    )
    admission = _admission(session_factory)

    decision = await admission.evaluate(
        demand=ResourceDemand.from_launch_policy_limits(LAUNCH_POLICY_LIMITS),
        budget=_budget(),
    )
    payload = decision.as_payload()

    assert payload["admitted"] is True
    assert payload["limitingResource"] is None
    assert payload["reconciliationHealthy"] is True
    assert payload["oldestWaiterAgeSeconds"] == 0
    assert payload["machine"]["utilizationPercent"]["memory"] == 42
    assert payload["machine"]["ceilings"]["memoryMiB"] == 7000
    flattened = repr(payload)
    assert "lease-a" not in flattened
    assert "omnigent-opencode" not in flattened


@pytest.mark.asyncio
async def test_the_advisory_precheck_refuses_an_unprovable_backend(
    session_factory,
) -> None:
    """AC7: daemon uncertainty must not read as free capacity anywhere."""

    await MachineCapacityLedger(session_factory).reconcile(
        backend_ref=BACKEND, inventory=None
    )
    admission = _admission(session_factory)

    decision = await admission.evaluate()

    assert decision.admitted is False
    assert decision.limiting_layer == LIMITING_LAYER_MACHINE_RESOURCES
    assert decision.as_payload()["limitingResource"] == (
        LIMITING_RESOURCE_RECONCILIATION
    )
    assert decision.as_payload()["reconciliationHealthy"] is False


@pytest.mark.asyncio
async def test_an_allocation_without_policy_limits_fails_closed(
    session_factory,
) -> None:
    """Accounting that is wired must not be silently skipped.

    Implementation 2: demand is resolved from trusted policy. A caller that
    cannot name its Launch Policy limits has no policy-resolved demand, so it
    is refused rather than admitted without accounting.
    """

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)

    with pytest.raises(HarnessPlatformError) as raised:
        await repository.acquire(
            execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
            runtime_binding_id="binding-a",
            host_class_ref="omnigent-opencode@1",
            launch_policy_ref="omnigent-launch@1",
            harness_id="opencode-native",
            harness_implementation_ref="opencode-native@1",
            provider_profile_refs=("opencode-zen-free",),
            ttl_seconds=3600,
        )

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE.value
    )
    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0


@pytest.mark.asyncio
async def test_the_durable_allocation_refuses_an_unprovable_backend(
    session_factory,
) -> None:
    """AC7: the enforcing path, not only the advisory read, blocks."""

    await MachineCapacityLedger(session_factory).reconcile(
        backend_ref=BACKEND, inventory=None
    )
    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)

    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-a")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )
    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0


# --------------------------------------------------------- realizer boundary


def _realizer(admission):
    """Construct the real realizer with inert dependencies.

    Only the capacity admission participates in the reservation fence, so the
    remaining dependencies are sentinels. The methods under test are the real
    production methods, not reimplementations.
    """

    from moonmind.omnigent.realizers.generic_host import (
        GenericOmnigentHostRealizer,
    )

    async def _unused(*_args, **_kwargs):  # pragma: no cover - never invoked
        raise AssertionError("realizer dependency was not expected to run")

    return GenericOmnigentHostRealizer(
        runtime_binding_store=object(),
        provider_lease_coordinator=object(),
        credential_provisioning_service=object(),
        host_lease_repository=object(),
        host_runtime=object(),
        planned_host_resolver=_unused,
        session_driver=_unused,
        session_cleanup_service=object(),
        workspace_publisher=object(),
        host_capacity_admission=admission,
        deployment_validator=lambda _payload: None,
    )


@pytest.mark.asyncio
async def test_the_realizer_refuses_to_mutate_docker_without_its_reservation(
    session_factory,
) -> None:
    """Implementation 3: the fence is re-verified before the Docker mutation."""

    admission = _admission(session_factory)
    realizer = _realizer(admission)

    with pytest.raises(HarnessPlatformError) as raised:
        await realizer._assert_machine_reservation_holds("lease-never-reserved")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )


@pytest.mark.asyncio
async def test_the_realizer_confirms_and_releases_on_cleanup_evidence(
    session_factory,
) -> None:
    """Implementation 6/7: confirm binds the consumer; release needs evidence."""

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    lease = await _acquire(repository, "binding-a")
    realizer = _realizer(admission)
    ledger = MachineCapacityLedger(session_factory)

    await realizer._assert_machine_reservation_holds(lease.leaseRef)
    await realizer._confirm_machine_reservation(lease.leaseRef, "mm-omnigent-host-a")
    assert (await ledger.usage(backend_ref=BACKEND)).initializing == 0

    # A cleanup that proved nothing releases nothing.
    await realizer._release_machine_reservation(lease.leaseRef, None)
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000

    # The cleanup service's own evidence releases compute and storage.
    await realizer._release_machine_reservation(
        lease.leaseRef,
        {
            "daemonObserved": True,
            "containerRemoved": True,
            "stateVolumeRemoved": True,
        },
    )
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0
    assert usage.reserved_temporary_storage_mib == 0


@pytest.mark.asyncio
async def test_the_realizer_does_not_fail_a_launch_that_outlived_its_window(
    session_factory,
) -> None:
    """#3881 FINDING-3: a slow cold launch must still confirm.

    ``realize()`` pulls the image, creates the container and then polls for
    registration, which can outlast the prelaunch window. The allocation named
    its container before mutating Docker, so the fence still holds and the
    successful launch confirms instead of failing.
    """

    from moonmind.omnigent.host_ports import host_correlation_identity

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    lease = await _acquire(repository, "binding-a")
    realizer = _realizer(admission)
    ledger = MachineCapacityLedger(session_factory)
    container_name = host_correlation_identity(lease.leaseRef)

    async with session_factory() as session:
        row = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="omnigent_host_lease",
                owner_ref=lease.leaseRef,
                generation=1,
            ),
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    # Another admission sweeps expiries long after the prelaunch TTL elapsed.
    await _acquire(repository, "binding-b")

    await realizer._assert_machine_reservation_holds(lease.leaseRef)
    await realizer._confirm_machine_reservation(lease.leaseRef, container_name)

    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 6000
    async with session_factory() as session:
        row = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="omnigent_host_lease",
                owner_ref=lease.leaseRef,
                generation=1,
            ),
        )
    assert row.state == STATE_ACTIVE
    assert row.container_ref == container_name


@pytest.mark.asyncio
async def test_the_realizer_is_fenced_when_its_capacity_was_reclaimed(
    session_factory,
) -> None:
    """A reclaimed reservation may not be restored into an over-committed machine.

    ``realize()`` can outlive the prelaunch window, and reconciliation that
    observes nothing running under the reserved name releases its compute. An
    intervening launch may then be admitted against exactly that capacity, so a
    silent restore at confirmation would push accounted usage past every
    configured ceiling. The live container is accounted again — it is running,
    and free capacity is the worse lie — and the fenced launch is refused with
    the waitable capacity code so cleanup tears it down.
    """

    from moonmind.omnigent.host_ports import host_correlation_identity

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    lease = await _acquire(repository, "binding-a")
    realizer = _realizer(admission)
    ledger = MachineCapacityLedger(session_factory)
    container_name = host_correlation_identity(lease.leaseRef)

    async with session_factory() as session:
        row = await session.get(
            MachineCapacityReservation, _host_reservation_id("binding-a")
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        row.container_ref = container_name
        await session.commit()
    # The janitor sees nothing running under the reserved name and reclaims it.
    await ledger.reconcile(backend_ref=BACKEND, inventory=_owned_inventory())
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 0
    # Another launch takes the capacity that freed.
    await _acquire(repository, "binding-b")

    with pytest.raises(HarnessPlatformError) as raised:
        await realizer._confirm_machine_reservation(lease.leaseRef, container_name)

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )
    # The container that did start is accounted, so it never reads as free.
    async with session_factory() as session:
        row = await session.get(
            MachineCapacityReservation, _host_reservation_id("binding-a")
        )
    assert row.state == STATE_ACTIVE
    assert row.container_ref == container_name

    # Cleanup returns exactly what confirmation restored.
    await realizer._release_machine_reservation(
        lease.leaseRef,
        {
            "daemonObserved": True,
            "containerRemoved": True,
            "stateVolumeRemoved": True,
        },
    )
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_a_retained_state_volume_keeps_storage_accounted(
    session_factory,
) -> None:
    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    lease = await _acquire(repository, "binding-a")
    realizer = _realizer(admission)

    await realizer._confirm_machine_reservation(lease.leaseRef, "mm-omnigent-host-a")
    await realizer._release_machine_reservation(
        lease.leaseRef,
        {"daemonObserved": True, "containerRemoved": True},
    )

    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0
    assert usage.reserved_temporary_storage_mib == 256


@pytest.mark.asyncio
async def test_the_realizer_is_inert_without_machine_accounting_wired() -> None:
    """A harness with no ledger keeps the pre-#3881 behaviour."""

    realizer = _realizer(None)

    await realizer._assert_machine_reservation_holds("lease-a")
    await realizer._confirm_machine_reservation("lease-a", "mm-a")
    await realizer._release_machine_reservation("lease-a", {"containerRemoved": True})


@pytest.mark.asyncio
async def test_the_durable_allocation_records_the_machine_capacity_view(
    session_factory, monkeypatch
) -> None:
    """#3881 FINDING-5, implementation 8: the observation must actually emit.

    The metric family's label vocabulary is covered elsewhere; what was missing
    is evidence that the enforcing allocation path emits it at all, on both the
    admitted and the refused outcome.
    """

    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    recorded: list[dict[str, object]] = []
    monkeypatch.setattr(
        control_plane_metrics,
        "record_machine_capacity",
        lambda **kwargs: recorded.append(kwargs),
    )
    vocabulary = control_plane_metrics.BOUNDED_LABEL_VALUES["limiting_resource"]
    admission = _admission(session_factory, host_capacity=1)
    repository = _repository(session_factory, admission)

    await _acquire(repository, "binding-a")

    assert len(recorded) == 1
    admitted = recorded[-1]
    assert admitted["reconciliation_health"] == "healthy"
    assert admitted["ceilings"]["memoryMiB"] == 7000
    assert admitted["utilization_percent"]["memory"] == 0
    # ``None`` is the admitted outcome's limiting resource; the recorder maps
    # it onto the bounded ``none`` label.
    assert admitted["limiting_resource"] is None

    with pytest.raises(HarnessPlatformError):
        await _acquire(repository, "binding-b")

    assert len(recorded) == 2
    refused = recorded[-1]
    assert refused["limiting_resource"] in vocabulary
    assert refused["limiting_resource"] == LIMITING_LAYER_HOST_CAPACITY
    assert refused["oldest_waiter_age_seconds"] is not None

    # A machine-layer refusal is the case oldest waiter age exists to report,
    # so the emitted view has to carry a real wait rather than the structural
    # zero a rolled-back marker used to guarantee. The first refusal persists
    # the marker; back-dating it advances the durable clock without sleeping.
    machine_admission = _admission(session_factory, host_capacity=8)
    machine_repository = _repository(session_factory, machine_admission)
    await _acquire(machine_repository, "binding-c")
    with pytest.raises(HarnessPlatformError):
        await _acquire(machine_repository, "binding-d")

    async with session_factory() as session:
        marker = await session.get(
            MachineCapacityReservation, _host_reservation_id("binding-d")
        )
        assert marker.state == STATE_WAITING
        marker.created_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    with pytest.raises(HarnessPlatformError):
        await _acquire(machine_repository, "binding-d")

    waiting = recorded[-1]
    assert waiting["limiting_resource"] == LIMITING_RESOURCE_MEMORY
    assert waiting["oldest_waiter_age_seconds"] >= 120


# ------------------------------------------------------ inventory boundary


@pytest.mark.asyncio
async def test_the_inventory_reports_only_moonmind_owned_running_containers() -> None:
    calls: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        calls.append(args)
        if args[0] == "ps":
            label = args[args.index("--filter") + 1]
            if "generic-omnigent-host" in label:
                return 0, b"mm-omnigent-host-1\n", b""
            return 0, b"mm-job-1\n", b""
        return (
            0,
            b"/mm-omnigent-host-1\t2147483648\t1500000000\t256\n"
            b"/mm-job-1\t1073741824\t1000000000\t128\n",
            b"",
        )

    observed = await probe_owned_containers(runner)

    assert {ref: owned.demand for ref, owned in observed.containers.items()} == {
        "mm-omnigent-host-1": ResourceDemand(
            cpu_millis=1500, memory_mib=2048, processes=256
        ),
        "mm-job-1": ResourceDemand(cpu_millis=1000, memory_mib=1024, processes=128),
    }
    # The scope travels with the result: a full enumeration is what makes
    # absence from it evidence that a consumer is gone.
    assert observed.covers_every_owned_launch_class is True
    assert (
        observed.containers["mm-omnigent-host-1"].launch_class.workload_class
        == WORKLOAD_CLASS_GENERIC_HOST
    )
    # Only MoonMind's own owner labels are ever queried, so a foreign container
    # can never appear in the inventory or be acted on.
    assert all(
        "moonmind." in args[args.index("--filter") + 1]
        for args in calls
        if args[0] == "ps"
    )
    assert all("status=running" in args for args in calls if args[0] == "ps")


@pytest.mark.asyncio
async def test_an_unreadable_inventory_is_not_an_empty_inventory() -> None:
    async def runner(_args):
        return 1, b"", b"cannot connect to the docker daemon"

    assert await probe_owned_containers(runner) is None


def _real_daemon_runner(lines: bytes, *, label_match: str, name: str):
    """A runner shaped like the daemon: one owned container, one inspect line."""

    async def runner(args):
        args = tuple(args)
        if args[0] == "ps":
            label = args[args.index("--filter") + 1]
            return 0, (name.encode() + b"\n") if label_match in label else b"", b""
        return 0, lines, b""

    return runner


@pytest.mark.asyncio
async def test_a_container_that_declared_no_limits_is_still_a_full_enumeration() -> (
    None
):
    """#3881 FINDING-1: an unbounded container is not an unreadable daemon.

    ``HostConfig.PidsLimit`` is a pointer, so any container created without
    ``--pids-limit`` — an ordinary managed session, session Docker sidecar,
    unprofiled workload or OAuth auth runner — renders as Go's nil placeholder
    instead of a number. Parsing that as a failure discarded the whole
    enumeration, which is the "daemon unreadable" value: every container job
    then failed to start and reconciliation wrote the blocked marker that
    refuses generic-host admission too.
    """

    for rendering in (b"<no value>", b"<nil>", b"", b"0", b"-1"):
        runner = _real_daemon_runner(
            b"/mm-session-1\t0\t0\t" + rendering + b"\n",
            label_match="managed-session",
            name="mm-session-1",
        )

        observed = await probe_owned_containers(runner)

        assert observed is not None, rendering
        # The enumeration still speaks for every launch class, so
        # reconciliation may still release the accounting of a vanished
        # consumer instead of stalling behind one unbounded container.
        assert observed.covers_every_owned_launch_class is True
        owned = observed.containers["mm-session-1"]
        assert owned.launch_class.name == "managed_session"
        assert owned.launch_class.reserves is False


@pytest.mark.asyncio
async def test_a_daemon_answer_that_is_not_a_limit_is_still_unreadable() -> None:
    """The fix must not turn a garbled daemon into an empty machine.

    "Declared no limit" and "answered something I cannot read" stay different
    values: the first is an ordinary container, the second blocks admission.
    """

    runner = _real_daemon_runner(
        b"/mm-session-1\t0\t0\tunexpected\n",
        label_match="managed-session",
        name="mm-session-1",
    )

    assert await probe_owned_containers(runner) is None


@pytest.mark.asyncio
async def test_an_owned_container_is_accounted_from_the_limits_it_declared() -> None:
    """#3881 FINDING-1 (second point): pins the accounting that was chosen.

    An observed container contributes the limits it declared, because a
    resource it declared no bound for has no bound to subtract. That is
    narrower than "the capacity they consume is subtracted", so the gap is
    named on the container rather than silently read as zero, and both design
    docs say so.
    """

    runner = _real_daemon_runner(
        # Declares memory only: 512 MiB, no --cpus, no --pids-limit.
        b"/mm-session-1\t536870912\t0\t<no value>\n",
        label_match="managed-session",
        name="mm-session-1",
    )

    observed = await probe_owned_containers(runner)

    owned = observed.containers["mm-session-1"]
    assert owned.demand == ResourceDemand(cpu_millis=0, memory_mib=512, processes=0)
    assert owned.undeclared_limits == frozenset(
        {LIMITING_RESOURCE_CPU, LIMITING_RESOURCE_PROCESSES}
    )
    assert owned.declares_every_limit is False
    assert observed.undeclared_limit_containers == ("mm-session-1",)


@pytest.mark.asyncio
async def test_a_fully_bounded_container_declares_no_gap() -> None:
    runner = _real_daemon_runner(
        b"/mm-job-1\t1073741824\t1000000000\t128\n",
        label_match="container_job",
        name="mm-job-1",
    )

    observed = await probe_owned_containers(runner)

    owned = observed.containers["mm-job-1"]
    assert owned.demand == ResourceDemand(
        cpu_millis=1000, memory_mib=1024, processes=128
    )
    assert owned.declares_every_limit is True
    assert observed.undeclared_limit_containers == ()


# ------------------------------------------- owned launch-class registry


#: Static ``moonmind.*`` label literals the deployment attaches that name
#: something other than a container launch class. Every entry is a decision:
#: adding a label here says "this does not need a place in the registry", which
#: is exactly the judgement the assertion below exists to force.
NON_LAUNCH_CLASS_LABELS = frozenset(
    {
        # MoonMind-owned **volumes**. They consume no CPU, memory or
        # processes, so machine accounting never enumerates them.
        "moonmind.kind=container-job-cache",
        "moonmind.kind=session-docker-sidecar-volume",
        "moonmind.owner=generic-omnigent-github-credential",
        # Attributes of a container some other label on the same launch
        # already classifies.
        "moonmind.oauth_session_transport=tmate",
        "moonmind.object_kind=container",
        "moonmind.ownership_schema=container-job/v1",
        "moonmind.workflow_id=activity-owned",
    }
)

#: ``moonmind.*`` label keys whose value is computed per container. A label
#: whose value names one instance cannot name a class of containers, so it can
#: never be a launch-class marker.
INSTANCE_VALUED_LABEL_KEYS = frozenset(
    {
        "moonmind.agent_run_id",
        "moonmind.attempt",
        "moonmind.backend_ref",
        "moonmind.cache_owner",
        "moonmind.cache_ref",
        "moonmind.capture_required",
        "moonmind.capture_retention_days",
        "moonmind.cleanup_mode",
        "moonmind.control_capabilities",
        "moonmind.correlation",
        "moonmind.credential_generation",
        "moonmind.credential_runtime_ref",
        "moonmind.effective_launch_ref",
        "moonmind.egress.applied_rule_digest",
        "moonmind.egress.profile",
        "moonmind.egress.profile_digest",
        "moonmind.execution_plan_ref",
        "moonmind.expires_at",
        "moonmind.helper_ttl_seconds",
        "moonmind.host_lease_generation",
        "moonmind.host_lease_id",
        "moonmind.host_lease_ref",
        "moonmind.job_id",
        "moonmind.oauth_session_id",
        "moonmind.owner_digest",
        "moonmind.ownership",
        "moonmind.provider_lease_id",
        "moonmind.provider_profile_id",
        "moonmind.repository",
        "moonmind.runtime_binding_id",
        "moonmind.runtime_id",
        "moonmind.session_epoch",
        "moonmind.session_id",
        "moonmind.step_id",
        "moonmind.timeout_seconds",
        "moonmind.tool_name",
        "moonmind.volume_role",
        "moonmind.workload_mode",
        "moonmind.workload_profile",
        # The one launch site that labels containers from a type rather than a
        # literal. Every value the type admits is pinned separately by
        # ``test_every_workload_ownership_kind_is_a_registered_launch_class``.
        "moonmind.kind",
    }
)

#: Stands in for a value the source computes at run time.
_COMPUTED = "\x00"


def _module_string_constants(tree: "ast.Module") -> dict[str, str]:
    """Module-level ``NAME = "literal"`` bindings, so ``LABEL_*`` resolves."""

    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        value = node.value
        if targets and isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                constants[target.id] = value.value
    return constants


def _render(node, constants: dict[str, str]) -> str | None:
    """Render a string expression, marking computed pieces as ``_COMPUTED``."""

    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        rendered: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                rendered.append(piece.value)
            else:
                inner = _render(getattr(piece, "value", None), constants)
                rendered.append(inner if inner is not None else _COMPUTED)
        return "".join(rendered)
    return None


def _names_a_label_mapping(node) -> bool:
    identifier = getattr(node, "id", None) or getattr(node, "attr", None) or ""
    return "label" in identifier.lower()


def _label_mappings(tree: "ast.Module"):
    """Dict literals a launch site expands into ``--label`` arguments.

    Only mappings the source itself calls labels are read. Scanning every dict
    literal instead would sweep in billing metrics and tool-name maps, whose
    ``moonmind.*`` keys never reach a container.
    """

    def dicts(node):
        if isinstance(node, ast.Dict):
            yield node

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(_names_a_label_mapping(t) for t in node.targets):
                yield from dicts(node.value)
        elif isinstance(node, ast.AnnAssign) and _names_a_label_mapping(node.target):
            yield from dicts(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "label" in node.name.lower():
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Return) and inner.value is not None:
                        yield from dicts(inner.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and "label" in key.value.lower()
                ):
                    yield from dicts(value)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg and "label" in keyword.arg.lower():
                    yield from dicts(keyword.value)


def _declared_container_labels(path: pathlib.Path) -> set[str]:
    """Every ``moonmind.*`` label one production module attaches.

    Two shapes reach a container: a value in the argument position right after
    a literal ``--label``, and an entry in a label mapping the launch site
    expands. Both are read here, because a launch class that used only the
    second one would otherwise be invisible to this assertion.
    """

    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    constants = _module_string_constants(tree)
    labels: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            elements = node.elts
        elif isinstance(node, ast.Call):
            elements = node.args
        else:
            continue
        for index, element in enumerate(elements[:-1]):
            if _render(element, constants) != "--label":
                continue
            label = _render(elements[index + 1], constants)
            if label and label.startswith("moonmind."):
                labels.add(label)
    for mapping in _label_mappings(tree):
        for key, value in zip(mapping.keys, mapping.values):
            rendered_key = _render(key, constants)
            if not rendered_key or not rendered_key.startswith("moonmind."):
                continue
            rendered_value = _render(value, constants)
            labels.add(
                f"{rendered_key}="
                f"{rendered_value if rendered_value is not None else _COMPUTED}"
            )
    return labels


def test_every_owned_container_label_the_deployment_creates_is_classified() -> None:
    """#3881 FINDING-2: an unregistered launch class reads as free capacity.

    Reconciliation only sees the owner labels the registry names, so a launch
    class MoonMind can create but the registry does not know spends the machine
    invisibly. The assertion this replaced matched the two literal keys
    ``moonmind.kind`` and ``moonmind.owner``, which meant an owner label under
    any other key — ``moonmind.oauth_session=true``, as it turned out — could
    never fail it. Every ``moonmind.*`` label the deployment attaches is read
    here instead, and each one must be explicitly classified: a registered
    launch class, a named non-launch-class label, or a key whose value names
    one instance rather than a class.
    """

    root = pathlib.Path(__file__).resolve().parents[3] / "moonmind"
    registry = set(OWNED_CONTAINER_LABEL_FILTERS)
    unclassified: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if path.parts[-2:] == ("capacity", "docker_inventory.py"):
            continue
        for label in _declared_container_labels(path):
            key = label.split("=", 1)[0]
            if _COMPUTED in key:
                # The key itself is computed, so no static reading of this
                # source can name the label. Nothing to classify.
                continue
            if _COMPUTED in label:
                classified = key in registry or key in INSTANCE_VALUED_LABEL_KEYS
            else:
                classified = label in registry or label in NON_LAUNCH_CLASS_LABELS
            if not classified:
                unclassified.setdefault(label, str(path))

    assert unclassified == {}


def test_the_classification_cannot_be_evaded_by_an_unexpected_label_key(
    tmp_path,
) -> None:
    """#3881 FINDING-2: the guard must fail on the label that got past it.

    ``moonmind.oauth_session=true`` is a real owner label under neither of the
    two keys the previous assertion matched, so it read as free capacity while
    that assertion passed. This pins the reading, not the outcome: an owner
    label under a novel key is seen, and it is only classified because the
    registry now names it.
    """

    source = tmp_path / "launch_site.py"
    source.write_text(
        """
LABEL_KEY = "moonmind.novel_owner"


async def start() -> None:
    await run(
        "run",
        "-d",
        "--label",
        "moonmind.novel_owner=true",
        "--label",
        f"{LABEL_KEY}_id={session_id}",
        image,
    )
""",
        encoding="utf-8",
    )

    declared = _declared_container_labels(source)

    assert "moonmind.novel_owner=true" in declared
    assert f"moonmind.novel_owner_id={_COMPUTED}" in declared
    # Neither is registered nor named, so the assertion above would fail on it.
    assert "moonmind.novel_owner=true" not in set(OWNED_CONTAINER_LABEL_FILTERS)
    assert "moonmind.novel_owner=true" not in NON_LAUNCH_CLASS_LABELS
    # The label that actually escaped is classified now, and only because the
    # registry names it.
    assert "moonmind.oauth_session=true" in set(OWNED_CONTAINER_LABEL_FILTERS)


def test_every_workload_ownership_kind_is_a_registered_launch_class() -> None:
    """The one launch site that labels containers from a type, not a literal."""

    from typing import get_args

    from moonmind.schemas.workload_models import WorkloadOwnershipKind

    registry = set(OWNED_CONTAINER_LABEL_FILTERS)
    for kind in get_args(WorkloadOwnershipKind):
        assert f"moonmind.kind={kind}" in registry


def test_the_oauth_host_launch_classes_are_accounted_but_never_refused() -> None:
    """#3881 FINDING-2: the documented policy and the inventory must agree.

    OAuth credential-authority hosts do not reserve: refusing one on resource
    grounds would break authentication rather than protect the machine. The
    policy is only honest if reconciliation still enumerates them, so their
    capacity is subtracted from what reserving launches may take.
    """

    by_selector = {
        launch_class.label_selector: launch_class
        for launch_class in OWNED_LAUNCH_CLASSES
    }
    for selector in (
        "moonmind.kind=omnigent-oauth-host",
        "moonmind.kind=omnigent-oauth-credential-validator",
        # #3881 FINDING-2: the terminal-bridge auth runner is the third
        # credential-authority class. It is a live default-path launch
        # (``oauth_session.start_auth_runner`` on both transports) that the
        # registry did not name, so its capacity read as free.
        "moonmind.oauth_session=true",
    ):
        launch_class = by_selector[selector]
        assert launch_class.reserves is False
        assert selector in OWNED_CONTAINER_LABEL_FILTERS
    assert by_selector["moonmind.oauth_session=true"].name == "oauth_auth_runner"
    # Exactly the classes that reserve are the ones a caller may reserve as.
    assert {
        launch_class.workload_class
        for launch_class in OWNED_LAUNCH_CLASSES
        if launch_class.reserves
    } == set(COVERED_WORKLOAD_CLASSES)


@pytest.mark.asyncio
async def test_a_live_oauth_host_reduces_what_reserving_launches_may_take(
    session_factory,
) -> None:
    """#3881 FINDING-2, AC3: an unreserved owned host is not free capacity."""

    ledger = MachineCapacityLedger(session_factory)
    oauth_class = OwnedLaunchClass(
        "omnigent_oauth_host", "moonmind.kind=omnigent-oauth-host"
    )
    runtime_bindings = AsyncMock()
    runtime_bindings.list_recoverable.return_value = ()
    host_leases = AsyncMock()
    host_leases.list_recoverable.return_value = ()

    async def inventory():
        return _owned_inventory(
            {
                "mm-oauth-host-a": ResourceDemand(
                    cpu_millis=4000, memory_mib=6800, processes=512
                )
            },
            launch_class=oauth_class,
        )

    result = await GenericOmnigentHostJanitor(
        host_leases=host_leases,
        runtime_bindings=runtime_bindings,
        realizer=AsyncMock(),
        machine_capacity=ledger,
        machine_backend_ref=BACKEND,
        container_inventory=inventory,
    ).run()

    # Accounted, but not a reconciliation fault: nothing was meant to reserve it.
    assert result["machineCapacity"]["observed"] == 1
    assert result["machineCapacity"]["adopted"] == 0
    assert result["machineCapacity"]["reconciliationFaults"] == 0

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-a")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )


# --------------------------------------------------------- janitor boundary


@pytest.mark.asyncio
async def test_the_janitor_adopts_an_unaccounted_owned_live_container(
    session_factory,
) -> None:
    """AC7: the existing janitor owns reconciliation; no second coordinator."""

    ledger = MachineCapacityLedger(session_factory)
    runtime_bindings = AsyncMock()
    runtime_bindings.list_recoverable.return_value = ()
    host_leases = AsyncMock()
    host_leases.list_recoverable.return_value = ()

    async def inventory():
        return _owned_inventory(
            {"mm-job-orphan": ResourceDemand(cpu_millis=500, memory_mib=1024)},
            launch_class=JOB_LAUNCH_CLASS,
        )

    result = await GenericOmnigentHostJanitor(
        host_leases=host_leases,
        runtime_bindings=runtime_bindings,
        realizer=AsyncMock(),
        machine_capacity=ledger,
        machine_backend_ref=BACKEND,
        container_inventory=inventory,
    ).run()

    assert result["machineCapacity"]["adopted"] == 1
    assert result["machineCapacity"]["reconciliationFaults"] == 1
    async with session_factory() as session:
        row = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="adopted_container",
                owner_ref="mm-job-orphan",
                generation=1,
            ),
        )
    assert row.state == STATE_ADOPTED
    assert row.memory_mib == 1024


@pytest.mark.asyncio
async def test_a_failing_inventory_blocks_admission_rather_than_freeing_it(
    session_factory,
) -> None:
    """AC7: an inventory that raised proves nothing about the backend."""

    ledger = MachineCapacityLedger(session_factory)
    runtime_bindings = AsyncMock()
    runtime_bindings.list_recoverable.return_value = ()
    host_leases = AsyncMock()
    host_leases.list_recoverable.return_value = ()

    async def inventory():
        raise RuntimeError("docker daemon is unreachable")

    result = await GenericOmnigentHostJanitor(
        host_leases=host_leases,
        runtime_bindings=runtime_bindings,
        realizer=AsyncMock(),
        machine_capacity=ledger,
        machine_backend_ref=BACKEND,
        container_inventory=inventory,
    ).run()

    assert result["machineCapacity"]["backendObserved"] is False
    assert result["machineCapacity"]["admissionBlocked"] is True
    admission = _admission(session_factory)
    assert (await admission.evaluate()).admitted is False


@pytest.mark.asyncio
async def test_the_janitor_reconciles_across_a_container_that_declared_no_limits(
    session_factory,
) -> None:
    """#3881 FINDING-1 at the janitor boundary.

    A managed session with no ``--pids-limit`` used to make the inventory
    unreadable, so ``reconcile`` wrote the blocked marker and every generic
    host was then refused with ``reconciliation_health`` until the session
    ended. The daemon shape here is the real one.
    """

    ledger = MachineCapacityLedger(session_factory)
    runtime_bindings = AsyncMock()
    runtime_bindings.list_recoverable.return_value = ()
    host_leases = AsyncMock()
    host_leases.list_recoverable.return_value = ()

    async def inventory():
        return await probe_owned_containers(
            _real_daemon_runner(
                b"/mm-session-1\t0\t0\t<no value>\n",
                label_match="managed-session",
                name="mm-session-1",
            )
        )

    blocked_id = machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="reconciliation",
        owner_ref=BACKEND,
        generation=1,
    )
    # Start from the state the defect produced: a genuinely unreadable pass has
    # left the blocked marker behind, so the assertion below is about clearing
    # it rather than about a row that was never written.
    await ledger.reconcile(backend_ref=BACKEND, inventory=None)
    async with session_factory() as session:
        assert (
            await session.get(MachineCapacityReservation, blocked_id)
        ).state == STATE_BLOCKED

    result = await GenericOmnigentHostJanitor(
        host_leases=host_leases,
        runtime_bindings=runtime_bindings,
        realizer=AsyncMock(),
        machine_capacity=ledger,
        machine_backend_ref=BACKEND,
        container_inventory=inventory,
    ).run()

    machine = result["machineCapacity"]
    assert machine["backendObserved"] is True
    assert machine["admissionBlocked"] is False
    assert machine["scopeComplete"] is True
    # Accounted by observation, never a fault: nothing was meant to reserve it.
    assert machine["observed"] == 1
    assert machine["reconciliationFaults"] == 0
    # The accounting gap is reported rather than silently read as zero.
    assert machine["undeclaredLimits"] == 1

    async with session_factory() as session:
        blocked = await session.get(MachineCapacityReservation, blocked_id)
    assert blocked.state != STATE_BLOCKED

    # Admission is decided on resources again, not on reconciliation health.
    admission = _admission(session_factory)
    decision = await admission.evaluate()
    assert decision.admitted is True
    assert decision.limiting_layer is None
    assert decision.machine_usage is not None
    assert decision.machine_usage.reconciliation_blocked is False
    # And the durable allocation the advisory read stands in for completes.
    lease = await _acquire(_repository(session_factory, admission), "binding-a")
    assert lease is not None


@pytest.mark.asyncio
async def test_a_live_auth_runner_is_adopted_as_observed_capacity(
    session_factory,
) -> None:
    """#3881 FINDING-2 at the janitor boundary.

    The auth runner holds real CPU and memory for as long as an operator's
    provider authentication lasts. Before it was registered, reconciliation
    never enumerated it, so reserving launches read its capacity as free.
    """

    ledger = MachineCapacityLedger(session_factory)
    runtime_bindings = AsyncMock()
    runtime_bindings.list_recoverable.return_value = ()
    host_leases = AsyncMock()
    host_leases.list_recoverable.return_value = ()

    async def inventory():
        return await probe_owned_containers(
            _real_daemon_runner(
                # Bounded here so the adoption's effect on the shared budget is
                # observable; the launch site itself declares no limits, which
                # ``test_an_owned_container_is_accounted_from_the_limits_it_declared``
                # pins separately.
                b"/moonmind_auth_s1\t7130316800\t4000000000\t512\n",
                label_match="oauth_session=true",
                name="moonmind_auth_s1",
            )
        )

    result = await GenericOmnigentHostJanitor(
        host_leases=host_leases,
        runtime_bindings=runtime_bindings,
        realizer=AsyncMock(),
        machine_capacity=ledger,
        machine_backend_ref=BACKEND,
        container_inventory=inventory,
    ).run()

    machine = result["machineCapacity"]
    assert machine["observed"] == 1
    assert machine["adopted"] == 0
    assert machine["reconciliationFaults"] == 0

    async with session_factory() as session:
        row = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="adopted_container",
                owner_ref="moonmind_auth_s1",
                generation=1,
            ),
        )
    assert row.state == STATE_ADOPTED
    assert row.memory_mib == 6800

    # Its capacity is subtracted from what reserving launches may take.
    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-a")
    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )


@pytest.mark.asyncio
async def test_the_janitor_is_unchanged_without_machine_accounting_wired(
    session_factory,
) -> None:
    runtime_bindings = AsyncMock()
    runtime_bindings.list_recoverable.return_value = ()
    host_leases = AsyncMock()
    host_leases.list_recoverable.return_value = ()

    result = await GenericOmnigentHostJanitor(
        host_leases=host_leases,
        runtime_bindings=runtime_bindings,
        realizer=AsyncMock(),
    ).run()

    assert "machineCapacity" not in result
    assert result["examined"] == 0


# --------------------------------------------------- container-job boundary


def _container_job_backend(tmp_path, *, ledger, runner):
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    lock = AsyncMock()
    lock.acquire.return_value = object()
    return DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        capacity_lock=lock,
        backend_ref=BACKEND,
        machine_capacity=ledger,
    )


def _container_job_request(tmp_path):
    from tests.unit.workflows.temporal.test_container_job_backend import _request

    return _request(
        tmp_path,
        resources={"cpuMillis": 1000, "memoryMiB": 3000, "pids": 256},
    )


@pytest.mark.asyncio
async def test_a_container_job_cannot_spend_a_generic_hosts_reservation(
    session_factory, tmp_path
) -> None:
    """AC3: hosts and jobs collectively respect one machine budget."""

    ledger = MachineCapacityLedger(session_factory)
    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    # Two generic hosts fill the 7000 MiB ceiling.
    await _acquire(repository, "binding-a")
    await _acquire(repository, "binding-b")

    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            return 0, b"", b""
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
    )

    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.start_container(_container_job_request(tmp_path))

    assert (
        raised.value.failure_class is ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED
    )
    # The shared memory ceiling is what refused it, and the message says so.
    assert f"missing_condition={LIMITING_RESOURCE_MEMORY}" in str(raised.value)
    assert not any(command[0] == "start" for command in commands)


@pytest.mark.asyncio
async def test_a_container_job_starts_beside_a_container_that_declared_no_limits(
    session_factory, tmp_path
) -> None:
    """#3881 FINDING-1 at the container-job launch boundary.

    ``_reserve_machine_capacity`` awaits ``_owned_inventory`` unconditionally,
    and an unreadable inventory raises ``INFRASTRUCTURE`` before the ledger is
    consulted. With an ordinary managed session running — no ``--pids-limit``
    — that made every container job unstartable on the default deployment
    path. The runner here answers exactly as the daemon does.
    """

    import json as _json

    from moonmind.schemas.container_job_models import ContainerJobBackendError
    from moonmind.workflows.temporal.container_job_backend import LABEL_OWNERSHIP

    ledger = MachineCapacityLedger(session_factory)
    request = _container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            label = args[args.index("--filter") + 1]
            if "managed-session" in label:
                return 0, b"mm-session-1\n", b""
            return 0, b"", b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return (
                0,
                _json.dumps({LABEL_OWNERSHIP: request.ownership_token}).encode(),
                b"",
            )
        if args[0] == "inspect" and "HostConfig" in args[2]:
            return 0, b"/mm-session-1\t0\t0\t<no value>\n", b""
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    try:
        await backend.start_container(request)
    except ContainerJobBackendError as exc:  # pragma: no cover - regression guard
        pytest.fail(f"the job was refused rather than admitted: {exc}")

    assert any(command[0] == "start" for command in commands)
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_a_container_job_reserves_and_confirms_in_the_shared_ledger(
    session_factory, tmp_path
) -> None:
    import json as _json

    from moonmind.workflows.temporal.container_job_backend import LABEL_OWNERSHIP

    ledger = MachineCapacityLedger(session_factory)
    commands: list[tuple[str, ...]] = []
    request = _container_job_request(tmp_path)

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            return 0, b"", b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return (
                0,
                _json.dumps({LABEL_OWNERSHIP: request.ownership_token}).encode(),
                b"",
            )
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    await backend.start_container(request)

    assert any(command[0] == "start" for command in commands)
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000
    # The confirmed job is no longer initializing, so it holds no permit.
    assert usage.initializing == 0

    # Capacity accounting observes the removal the cleanup path proved.
    await backend.remove_container(request)

    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 0


@pytest.mark.asyncio
async def test_a_container_job_reserves_the_shared_memory_it_will_realize(
    session_factory, tmp_path
) -> None:
    """Every container job gets ``--shm-size``, and that is RAM-backed tmpfs.

    The machine temporary-storage budget exists for exactly that resource, so a
    job whose reservation left it at zero could pass the ceiling no matter how
    much shared memory it asked for.
    """

    import json as _json

    from moonmind.workflows.temporal.container_job_backend import LABEL_OWNERSHIP
    from tests.unit.workflows.temporal.test_container_job_backend import _request

    ledger = MachineCapacityLedger(session_factory)
    commands: list[tuple[str, ...]] = []
    request = _request(
        tmp_path,
        resources={
            "cpuMillis": 1000,
            "memoryMiB": 3000,
            "pids": 256,
            "shmSize": "2048m",
        },
    )

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            return 0, b"", b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return (
                0,
                _json.dumps({LABEL_OWNERSHIP: request.ownership_token}).encode(),
                b"",
            )
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    await backend.start_container(request)

    assert any(command[0] == "start" for command in commands)
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_temporary_storage_mib == 2048

    await backend.remove_container(request)
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_temporary_storage_mib == 0


@pytest.mark.asyncio
async def test_a_container_job_reserves_the_deployment_default_shared_memory(
    session_factory, tmp_path
) -> None:
    """An omitted ``shmSize`` still gets one, so it is still accounted."""

    import json as _json

    from moonmind.config.container_backend_settings import (
        resolve_container_backend_settings,
    )
    from moonmind.workflows.temporal.container_job_backend import LABEL_OWNERSHIP

    ledger = MachineCapacityLedger(session_factory)
    request = _container_job_request(tmp_path)

    async def runner(args):
        args = tuple(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            return 0, b"", b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return (
                0,
                _json.dumps({LABEL_OWNERSHIP: request.ownership_token}).encode(),
                b"",
            )
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    await backend.start_container(request)

    default_shm = resolve_container_backend_settings({}).shm_size_mib
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_temporary_storage_mib == default_shm


@pytest.mark.asyncio
async def test_an_unreadable_daemon_never_releases_a_container_jobs_accounting(
    session_factory, tmp_path
) -> None:
    """AC7: a daemon that did not answer is not proof the container is gone.

    ``remove_container`` releases the machine accounting on the evidence that
    the container is absent. A connection failure or timeout on the ownership
    inspect proves nothing about the container, so it must fail closed rather
    than free capacity a live job is still spending.
    """

    import json as _json

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
    )
    from moonmind.workflows.temporal.container_job_backend import LABEL_OWNERSHIP

    ledger = MachineCapacityLedger(session_factory)
    request = _container_job_request(tmp_path)
    daemon_reachable = True

    async def runner(args):
        args = tuple(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            return 0, b"", b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            if not daemon_reachable:
                return (
                    1,
                    b"",
                    b"Cannot connect to the Docker daemon at unix:///var/run/"
                    b"docker.sock. Is the docker daemon running?",
                )
            return (
                0,
                _json.dumps({LABEL_OWNERSHIP: request.ownership_token}).encode(),
                b"",
            )
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)
    await backend.start_container(request)
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000

    daemon_reachable = False
    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.remove_container(request)

    assert raised.value.failure_class is ContainerJobFailureClass.INFRASTRUCTURE
    # The live job keeps its accounting, so the next admission cannot spend it
    # a second time.
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000

    # Once the daemon answers again, the same removal releases exactly once.
    daemon_reachable = True
    await backend.remove_container(request)
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 0


@pytest.mark.asyncio
async def test_a_container_job_start_keeps_a_confirmed_hosts_accounting(
    session_factory, tmp_path
) -> None:
    """#3881 FINDING-1: a job start must not free a live host's compute.

    The container-job path reconciles before it reserves. Feeding that
    reconciliation a container-job-label-only inventory made every live generic
    Omnigent host look like a vanished consumer, so its CPU, memory and
    processes were released on every job start and the machine was
    oversubscribed by a whole live host.
    """

    import json as _json

    from moonmind.omnigent.host_ports import host_correlation_identity
    from moonmind.workflows.temporal.container_job_backend import LABEL_OWNERSHIP

    ledger = MachineCapacityLedger(session_factory)
    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    lease = await _acquire(repository, "binding-a")
    host_container = host_correlation_identity(lease.leaseRef)
    host_reservation = machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="omnigent_host_lease",
        owner_ref=lease.leaseRef,
        generation=1,
    )
    await ledger.confirm(
        reservation_id=host_reservation,
        generation=1,
        container_ref=host_container,
    )
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000

    request = _container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            label = args[args.index("--filter") + 1]
            # The host carries only its own owner label; a container-job-scoped
            # enumeration would never see it.
            if "generic-omnigent-host" in label:
                return 0, f"{host_container}\n".encode(), b""
            return 0, b"", b""
        if args[:2] == ("inspect", "--format") and args[2].startswith("{{.Name}}"):
            return (
                0,
                f"/{host_container}\t{3000 * 1024 * 1024}\t1000000000\t256\n".encode(),
                b"",
            )
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return (
                0,
                _json.dumps({LABEL_OWNERSHIP: request.ownership_token}).encode(),
                b"",
            )
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    await backend.start_container(request)

    assert any(command[0] == "start" for command in commands)
    usage = await ledger.usage(backend_ref=BACKEND)
    # The live host kept every unit it was accounted for, and the job added its
    # own on top rather than replacing it.
    assert usage.reserved_memory_mib == 6000
    assert usage.reserved_cpu_millis == 2000
    assert usage.reserved_processes == 512
    # The live host was already accounted, so it is not a reconciliation fault.
    assert usage.reconciliation_faults == 0
    async with session_factory() as session:
        row = await session.get(MachineCapacityReservation, host_reservation)
    assert row.state == STATE_ACTIVE


@pytest.mark.asyncio
async def test_the_container_job_memory_override_only_lowers_the_ceiling(
    tmp_path,
) -> None:
    """#3881 FINDING-4: a setting documented as lowering may not raise."""

    from moonmind.config.container_backend_settings import (
        resolve_container_backend_settings,
    )
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    async def runner(args):
        argv = tuple(args)
        if argv and argv[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        return 0, b"", b""

    def _backend(configured: str) -> DockerContainerJobBackend:
        settings = resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_MEMORY_MIB": configured}
        )
        return DockerContainerJobBackend(
            workspace_root=tmp_path,
            command_runner=runner,
            backend_ref=BACKEND,
            settings=settings,
        )

    # Above the 70% share: clamped, so the documented control-plane and cleanup
    # headroom survives an operator setting that promises to lower the ceiling.
    raised = await _backend("9500")._machine_budget()
    assert raised.memory_mib == 7000
    assert raised.headroom().memory_mib == 3000
    # Below the share: still honored, because lowering is what it is for.
    lowered = await _backend("2048")._machine_budget()
    assert lowered.memory_mib == 2048


@pytest.mark.asyncio
async def test_a_container_job_start_refuses_when_the_inventory_is_unreadable(
    session_factory, tmp_path
) -> None:
    """AC7: an unreadable backend blocks the launch instead of admitting it."""

    ledger = MachineCapacityLedger(session_factory)
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 0, f"{10000 * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            return 1, b"", b"cannot connect to the docker daemon"
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
    )

    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.start_container(_container_job_request(tmp_path))
    assert raised.value.failure_class is ContainerJobFailureClass.INFRASTRUCTURE
    assert not any(command[0] == "start" for command in commands)
    # Nothing was reserved against a machine that could not be established.
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 0


@pytest.mark.asyncio
async def test_a_container_job_start_refuses_when_the_daemon_is_unreadable(
    session_factory, tmp_path
) -> None:
    """AC7: an unreadable daemon is not an empty machine."""

    ledger = MachineCapacityLedger(session_factory)
    commands: list[tuple[str, ...]] = []

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 1, b"", b"cannot connect to the docker daemon"
        return 0, b"", b""

    backend = _container_job_backend(tmp_path, ledger=ledger, runner=runner)

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
    )

    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.start_container(_container_job_request(tmp_path))
    assert raised.value.failure_class is ContainerJobFailureClass.INFRASTRUCTURE
    assert not any(command[0] == "start" for command in commands)


@pytest.mark.asyncio
async def test_saturation_still_leaves_control_plane_and_cleanup_headroom(
    session_factory,
) -> None:
    """AC8: a fully saturated workload never reserves the whole machine."""

    admission = _admission(session_factory, host_capacity=64)
    repository = _repository(session_factory, admission)
    admitted = 0
    for index in range(64):
        try:
            await _acquire(repository, f"binding-{index}")
        except HarnessPlatformError:
            break
        admitted += 1

    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    budget = _budget()
    assert admitted > 0
    assert usage.reserved_memory_mib <= budget.memory_mib
    assert budget.headroom().memory_mib == TOTALS.memory_mib - budget.memory_mib
    assert TOTALS.memory_mib - usage.reserved_memory_mib >= budget.headroom().memory_mib


@pytest.mark.asyncio
async def test_admission_never_pools_two_docker_backends(session_factory) -> None:
    ledger = MachineCapacityLedger(session_factory)
    request = ReservationRequest(
        backend_ref="second-daemon",
        workload_class=WORKLOAD_CLASS_GENERIC_HOST,
        owner_kind="omnigent_host_lease",
        owner_ref="lease-elsewhere",
        demand=ResourceDemand(memory_mib=6999),
    )
    await ledger.reserve(request=request, budget=_budget())

    admission = _admission(session_factory)
    decision = await admission.evaluate(
        demand=ResourceDemand.from_launch_policy_limits(LAUNCH_POLICY_LIMITS),
        budget=_budget(),
        now=datetime.now(UTC),
    )

    assert decision.admitted is True
    assert decision.machine is not None
    assert decision.machine.limiting_resource is None
    assert decision.machine.usage.reserved_memory_mib == 0
    assert LIMITING_RESOURCE_MEMORY not in str(decision.waiting_reason)


# ------------------------------- concurrent-initialization permit boundary


def _default_budget() -> MachineResourceBudget:
    """The documented default: every ``MOONMIND_MACHINE_*`` setting omitted.

    ``_budget`` raises the permit out of the way so the other boundaries can be
    exercised in isolation. These tests need the value a deployment that
    configured nothing actually gets.
    """

    return MachineResourceBudget.from_totals(TOTALS, env={})


def _default_repository(session_factory, admission):
    async def budget():
        return _default_budget()

    return DbOmnigentHostLeaseRepository(
        session_factory,
        capacity_admission=admission,
        machine_budget_provider=budget,
    )


def _small_container_job_request(tmp_path, *, job_id: str | None = None):
    """A container job small enough to fit beside two cold host launches."""

    from tests.unit.workflows.temporal.test_container_job_backend import _request

    request = _request(
        tmp_path,
        resources={"cpuMillis": 250, "memoryMiB": 512, "pids": 64},
    )
    if job_id is None:
        return request
    # One job id owns exactly one reservation, so a second *distinct* job needs
    # its own identity rather than reusing the first job's allocation.
    payload = request.model_dump(by_alias=True, mode="json")
    payload["jobId"] = job_id
    payload["ownershipToken"] = f"{job_id}:v1"
    return type(request).model_validate(payload)


def _container_job_runner(request, commands, *, running: tuple[str, ...] = ()):
    """A stub daemon reporting ``running`` as this backend's owned containers."""

    import json as _json

    from moonmind.workflows.temporal.container_job_backend import LABEL_OWNERSHIP

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "info":
            return 0, f"{TOTALS.memory_mib * 1024 * 1024}\t16".encode(), b""
        if args[0] == "ps":
            selector = args[args.index("--filter") + 1]
            if "container_job" in selector and running:
                return 0, "".join(f"{ref}\n" for ref in running).encode(), b""
            return 0, b"", b""
        if args[:2] == ("inspect", "--format") and args[2].startswith("{{.Name}}"):
            lines = [f"/{ref}\t{512 * 1024 * 1024}\t250000000\t64" for ref in args[3:]]
            return 0, ("\n".join(lines) + "\n").encode(), b""
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return (
                0,
                _json.dumps({LABEL_OWNERSHIP: request.ownership_token}).encode(),
                b"",
            )
        return 0, b"", b""

    return runner


@pytest.mark.asyncio
async def test_a_cold_host_launch_still_spends_an_initialization_permit(
    session_factory,
) -> None:
    """#3881 IMPL-5: the permit the layer exists for is unchanged.

    Two hosts cold-launching at the documented default consume both permits, so
    the third waits on the permit rather than on a resource ceiling it has room
    in.
    """

    admission = _admission(session_factory)
    repository = _default_repository(session_factory, admission)
    await _acquire(repository, "binding-a")
    await _acquire(repository, "binding-b")

    usage = await MachineCapacityLedger(session_factory).usage(backend_ref=BACKEND)
    assert usage.initializing == _default_budget().max_concurrent_initializing

    with pytest.raises(HarnessPlatformError) as raised:
        await _acquire(repository, "binding-c")

    assert (
        raised.value.code
        == HarnessPlatformFailure.OMNIGENT_HOST_CAPACITY_UNAVAILABLE.value
    )
    assert f"missing_condition={LIMITING_RESOURCE_INITIALIZING}" in str(raised.value)


@pytest.mark.asyncio
async def test_a_container_job_is_not_held_by_a_cold_host_launchs_permit(
    session_factory, tmp_path
) -> None:
    """#3881 FINDING-A: the permit bounds cold host launches, not every class.

    Under the documented defaults two hosts cold-launching consume every
    initialization permit for the whole of ``realize()`` — an image pull, a
    create/start and up to 91 registration polls. Charging a container job for
    that permit terminally failed every job start in that multi-minute window
    while the machine had ample CPU and memory to run it.
    """

    ledger = MachineCapacityLedger(session_factory)
    admission = _admission(session_factory)
    repository = _default_repository(session_factory, admission)
    await _acquire(repository, "binding-a")
    await _acquire(repository, "binding-b")
    assert (await ledger.usage(backend_ref=BACKEND)).initializing == 2

    request = _small_container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )

    await backend.start_container(request)

    assert any(command[0] == "start" for command in commands)
    usage = await ledger.usage(backend_ref=BACKEND)
    # The job spent the machine budget it asked for and no permit at all: the
    # two hosts still hold every permit the layer bounds.
    assert usage.reserved_memory_mib == 6512
    assert usage.initializing == 2


@pytest.mark.asyncio
async def test_a_container_job_refusal_names_the_resource_that_refused_it(
    session_factory, tmp_path
) -> None:
    """#3881 FINDING-A/FINDING-C: the raised message must not name a guess."""

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
    )

    ledger = MachineCapacityLedger(session_factory)
    admission = _admission(session_factory)
    repository = _default_repository(session_factory, admission)
    # Two hosts hold every permit and 6000 MiB of the 7000 MiB ceiling.
    await _acquire(repository, "binding-a")
    await _acquire(repository, "binding-b")

    request = _container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )

    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.start_container(request)

    assert (
        raised.value.failure_class is ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED
    )
    # Memory is the resource that actually refused it. The permit is not, and
    # the message must not claim another container job is holding the machine.
    assert f"missing_condition={LIMITING_RESOURCE_MEMORY}" in str(raised.value)
    assert LIMITING_RESOURCE_INITIALIZING not in str(raised.value)
    assert not any(command[0] == "start" for command in commands)


@pytest.mark.asyncio
async def test_the_container_job_boundary_records_the_machine_capacity_view(
    session_factory, tmp_path, monkeypatch
) -> None:
    """#3881 FINDING-A, implementation 8: the enforcing boundary must emit.

    Without this the limiting resource is unrecoverable from telemetry as well
    as from the error, so an operator cannot tell which layer refused the job.
    """

    from moonmind.omnigent.control_plane import metrics as control_plane_metrics
    from moonmind.schemas.container_job_models import ContainerJobBackendError

    recorded: list[dict[str, object]] = []
    monkeypatch.setattr(
        control_plane_metrics,
        "record_machine_capacity",
        lambda **kwargs: recorded.append(kwargs),
    )
    vocabulary = control_plane_metrics.BOUNDED_LABEL_VALUES["limiting_resource"]
    ledger = MachineCapacityLedger(session_factory)

    admitted_request = _small_container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend = _container_job_backend(
        tmp_path,
        ledger=ledger,
        runner=_container_job_runner(admitted_request, commands),
    )
    await backend.start_container(admitted_request)

    assert len(recorded) == 1
    admitted = recorded[-1]
    assert admitted["limiting_resource"] is None
    assert admitted["reconciliation_health"] == "healthy"
    assert admitted["ceilings"]["memoryMiB"] == 7000
    assert admitted["oldest_waiter_age_seconds"] == 0

    # Fill the ceiling so the next start is refused, and assert the refusal
    # reports the resource that refused it.
    admission = _admission(session_factory)
    repository = _default_repository(session_factory, admission)
    await _acquire(repository, "binding-a")
    await _acquire(repository, "binding-b")
    refused_request = _small_container_job_request(
        tmp_path, job_id="container-job:ffffffffffffffffffffffffffffffff"
    )
    backend = _container_job_backend(
        tmp_path,
        ledger=ledger,
        runner=_container_job_runner(
            refused_request,
            commands,
            running=(backend._name(admitted_request),),
        ),
    )
    with pytest.raises(ContainerJobBackendError):
        await backend.start_container(refused_request)

    refused = recorded[-1]
    assert refused["limiting_resource"] == LIMITING_RESOURCE_MEMORY
    assert refused["limiting_resource"] in vocabulary
    # Identity never reaches a metric label.
    flattened = repr(recorded)
    assert admitted_request.job_id not in flattened
    assert "binding-a" not in flattened


# --------------------------- inventory scope at the container-job boundary


def test_a_filtered_inventory_can_never_speak_for_every_launch_class() -> None:
    """#3881 FINDING-B: absence from a filtered view proves nothing.

    Reconciliation releases the accounting of consumers that are absent from a
    *complete* enumeration. A view that deliberately removed a running
    container while still reporting complete coverage would release that live
    container's compute, so the scope is dropped along with the container.
    """

    inventory = _owned_inventory(
        {
            "mm-host-a": ResourceDemand(memory_mib=3000),
            "mm-job-a": ResourceDemand(memory_mib=512),
        }
    )
    assert inventory.covers_every_owned_launch_class is True

    filtered = inventory.excluding("mm-job-a")

    assert "mm-job-a" not in filtered.containers
    assert filtered.covers_every_owned_launch_class is False
    # Removing nothing removes no evidence either.
    assert inventory.excluding("mm-absent") is inventory


@pytest.mark.asyncio
async def test_a_container_job_retry_keeps_its_own_live_containers_accounting(
    session_factory, tmp_path
) -> None:
    """#3881 FINDING-B: a job's own running container is not a vanished one.

    The launch path reconciles before it reserves. Handing that reconciliation
    an inventory with this job's own live container removed — while still
    claiming complete enumeration — released the container's own compute, after
    which the pre-Docker fence re-verification refused the attempt.
    """

    ledger = MachineCapacityLedger(session_factory)
    request = _small_container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )

    await backend.start_container(request)
    container_ref = backend._name(request)
    reservation_id = machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="container_job",
        owner_ref=request.job_id,
        generation=1,
    )
    async with session_factory() as session:
        row = await session.get(MachineCapacityReservation, reservation_id)
    assert row.state == STATE_ACTIVE

    # The same attempt runs again against its own already-running container.
    commands.clear()
    backend = _container_job_backend(
        tmp_path,
        ledger=ledger,
        runner=_container_job_runner(request, commands, running=(container_ref,)),
    )

    await backend.start_container(request)

    assert any(command[0] == "start" for command in commands)
    usage = await ledger.usage(backend_ref=BACKEND)
    # One live container, accounted exactly once and never released.
    assert usage.reserved_memory_mib == 512
    assert usage.reserved_cpu_millis == 250
    assert usage.reserved_processes == 64
    assert usage.reconciliation_faults == 0
    async with session_factory() as session:
        row = await session.get(MachineCapacityReservation, reservation_id)
    assert row.state == STATE_ACTIVE


@pytest.mark.asyncio
async def test_a_job_whose_record_was_lost_reclaims_its_adopted_accounting(
    session_factory, tmp_path
) -> None:
    """#3881 IMPL-6: one live container is accounted once, by its real owner.

    Handing reconciliation the unfiltered inventory means it can observe a job's
    own live container while that job holds no accounting record, and adopt it.
    Adoption is right — a running container is never free capacity — but the
    reservation that owns that exact container must then take the accounting
    over rather than add a second copy of it.
    """

    ledger = MachineCapacityLedger(session_factory)
    request = _small_container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )
    await backend.start_container(request)
    container_ref = backend._name(request)
    reservation_id = machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="container_job",
        owner_ref=request.job_id,
        generation=1,
    )
    adopted_id = machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="adopted_container",
        owner_ref=container_ref,
        generation=1,
    )

    # The job's own record is lost while its container keeps running.
    async with session_factory() as session:
        row = await session.get(MachineCapacityReservation, reservation_id)
        await session.delete(row)
        await session.commit()

    backend = _container_job_backend(
        tmp_path,
        ledger=ledger,
        runner=_container_job_runner(request, commands, running=(container_ref,)),
    )
    await backend.start_container(request)

    usage = await ledger.usage(backend_ref=BACKEND)
    # One container, accounted once — not once as an orphan and once as a
    # reservation — and no longer an outstanding reconciliation fault.
    assert usage.reserved_memory_mib == 512
    assert usage.reserved_cpu_millis == 250
    assert usage.reconciliation_faults == 0
    async with session_factory() as session:
        owner = await session.get(MachineCapacityReservation, reservation_id)
        orphan = await session.get(MachineCapacityReservation, adopted_id)
    assert owner.state == STATE_ACTIVE
    assert owner.container_ref == container_ref
    assert orphan.state == "released"


@pytest.mark.asyncio
async def test_a_container_job_restarts_its_own_stopped_container(
    session_factory, tmp_path
) -> None:
    """#3881 FINDING-D: the branch the container-job workflow actually takes.

    ``reconcile_container`` reports the container exists and is *not* running,
    and only then does the workflow call ``start_container``. The launch path
    reconciles first, and a stopped container is correctly absent from an
    enumeration of running owned containers, so this job's own reservation
    legitimately loses its compute a moment before the job asks for it back.
    Reporting that row as an admitted reuse left the pre-Docker fence refusing
    a launch on a machine with nothing reserved, and ``start_container`` has no
    retry, so a recoverable job was terminally failed.
    """

    ledger = MachineCapacityLedger(session_factory)
    request = _small_container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )
    await backend.start_container(request)
    container_ref = backend._name(request)
    reservation_id = machine_reservation_id(
        backend_ref=BACKEND,
        owner_kind="container_job",
        owner_ref=request.job_id,
        generation=1,
    )

    # The attempt runs again while its own container exists but is stopped, so
    # the daemon reports no running owned container at all.
    commands.clear()
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )

    await backend.start_container(request)

    assert ("start", container_ref) in commands
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 512
    assert usage.reserved_cpu_millis == 250
    assert usage.reserved_processes == 64
    assert usage.reconciliation_faults == 0
    async with session_factory() as session:
        row = await session.get(MachineCapacityReservation, reservation_id)
    assert row.state == STATE_ACTIVE
    assert row.container_ref == container_ref


@pytest.mark.asyncio
async def test_a_stopped_container_jobs_retry_reports_the_resource_that_refused(
    session_factory, tmp_path
) -> None:
    """#3881 FINDING-D: re-admission is a real admission, refused for a reason.

    Reclaiming a stopped consumer's compute is correct, and so is refusing the
    retry when other launches took the machine in the meantime. What the retry
    must never get is the fixed "reservation no longer holds" fence failure,
    which names no resource and tells an operator to wait for a managed launch
    that is not the one holding the machine.
    """

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        ContainerJobFailureClass,
    )

    ledger = MachineCapacityLedger(session_factory)
    request = _container_job_request(tmp_path)
    commands: list[tuple[str, ...]] = []
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )
    await backend.start_container(request)
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000

    # The job's container stops, and the next reconciliation of this backend
    # releases the compute it is provably no longer spending.
    await ledger.reconcile(backend_ref=BACKEND, inventory=_owned_inventory({}))
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 0
    # Two cold host launches take the machine while it is stopped.
    repository = _default_repository(session_factory, _admission(session_factory))
    await _acquire(repository, "binding-a")
    await _acquire(repository, "binding-b")

    commands.clear()
    backend = _container_job_backend(
        tmp_path, ledger=ledger, runner=_container_job_runner(request, commands)
    )
    with pytest.raises(ContainerJobBackendError) as raised:
        await backend.start_container(request)

    assert (
        raised.value.failure_class is ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED
    )
    assert f"missing_condition={LIMITING_RESOURCE_MEMORY}" in str(raised.value)
    assert "reservation no longer holds" not in str(raised.value)
    assert not any(command[0] == "start" for command in commands)
    async with session_factory() as session:
        row = await session.get(
            MachineCapacityReservation,
            machine_reservation_id(
                backend_ref=BACKEND,
                owner_kind="container_job",
                owner_ref=request.job_id,
                generation=1,
            ),
        )
    # A refusal frees nothing: the row keeps the state its own evidence set.
    assert row.state == STATE_STORAGE_RETAINED


@pytest.mark.asyncio
async def test_a_host_lease_reacquires_the_reservation_its_cleanup_retained(
    session_factory,
) -> None:
    """#3881 FINDING-D/FINDING-E: the same invariant at the other reserver.

    Both reservers share ``reserve_within``. When a host's cleanup proved the
    container gone while its state volume was deliberately retained, the
    reservation accounts storage and no compute. The next allocation under that
    exact lease ref must therefore be re-admitted against the machine, because
    the realizer re-verifies the same fence before it mutates Docker.
    """

    from moonmind.omnigent.host_ports import host_correlation_identity

    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    realizer = _realizer(admission)
    ledger = MachineCapacityLedger(session_factory)
    lease = await _acquire(repository, "binding-a")
    container_name = host_correlation_identity(lease.leaseRef)

    await realizer._confirm_machine_reservation(lease.leaseRef, container_name)
    await realizer._release_machine_reservation(
        lease.leaseRef,
        {"daemonObserved": True, "containerRemoved": True},
    )
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0
    assert usage.reserved_temporary_storage_mib == 256

    reservation, budget = await repository._machine_reservation(
        lease_ref=lease.leaseRef,
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="omnigent-launch@1",
        resource_limits=LAUNCH_POLICY_LIMITS,
    )
    async with session_factory() as session:
        decision = await admission.evaluate_within(
            session, reservation=reservation, budget=budget
        )
        await session.commit()

    assert decision.admitted is True
    # The fence the realizer takes before the Docker mutation must hold.
    await realizer._assert_machine_reservation_holds(lease.leaseRef)
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 3000
    # The storage the retained volume is still spending is counted once.
    assert usage.reserved_temporary_storage_mib == 256


# --------------------------------------------- realizer cleanup ordering


class _FlakyReleaseLedger:
    """The real ledger with a bounded transient failure on the release write."""

    def __init__(self, ledger, *, failures: int = 1) -> None:
        self._ledger = ledger
        self._failures = failures
        self.release_attempts = 0

    def __getattr__(self, name):
        return getattr(self._ledger, name)

    async def release(self, **kwargs):
        self.release_attempts += 1
        if self.release_attempts <= self._failures:
            raise RuntimeError("capacity ledger write failed transiently")
        return await self._ledger.release(**kwargs)


def _cleanup_realizer(*, admission, host_leases, host_runtime, bindings):
    from moonmind.omnigent.realizers.generic_host import (
        GenericOmnigentHostRealizer,
    )

    async def _unused(*_args, **_kwargs):  # pragma: no cover - never invoked
        raise AssertionError("realizer dependency was not expected to run")

    credentials = AsyncMock()
    credentials.cleanup_all.return_value = []
    return GenericOmnigentHostRealizer(
        runtime_binding_store=bindings,
        provider_lease_coordinator=AsyncMock(),
        credential_provisioning_service=credentials,
        host_lease_repository=host_leases,
        host_runtime=host_runtime,
        planned_host_resolver=_unused,
        session_driver=_unused,
        session_cleanup_service=AsyncMock(),
        workspace_publisher=object(),
        host_capacity_admission=admission,
        deployment_validator=lambda _payload: None,
    )


@pytest.mark.asyncio
async def test_a_failed_capacity_release_leaves_the_lease_retryable(
    session_factory,
) -> None:
    """Cleanup evidence is consumed before the lease becomes terminal.

    A lease that is already ``cleaned`` skips the whole host-cleanup block on
    the next attempt, so releasing the machine accounting after ``mark_cleaned``
    committed made a transient ledger failure permanent: reconciliation only
    ever moves the absent consumer to ``storage_retained``, and that state is
    not revisited, so the temporary-storage capacity leaked even though cleanup
    had proved the volumes were removed.
    """

    import types

    from moonmind.omnigent.runtime_bindings import (
        InMemoryStableRuntimeBindingStore,
        RuntimeBindingState,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    ledger = MachineCapacityLedger(session_factory)
    admission = _admission(session_factory)
    repository = _repository(session_factory, admission)
    lease = await _acquire(repository, "binding-a")
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000

    flaky = _FlakyReleaseLedger(ledger)
    host_runtime = AsyncMock()
    host_runtime.cleanup.return_value = {
        "daemonObserved": True,
        "containerRemoved": True,
        "stateVolumeRemoved": True,
    }
    bindings = InMemoryStableRuntimeBindingStore()
    binding = await bindings.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        idempotency_key="idem-1",
        provider_leases={},
    )
    binding = await bindings.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState.credentials_acquired,
    )
    realizer = _cleanup_realizer(
        admission=types.SimpleNamespace(machine_capacity=flaky, backend_ref=BACKEND),
        host_leases=repository,
        host_runtime=host_runtime,
        bindings=bindings,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )
    cleanup_kwargs = dict(
        request=request,
        binding=binding,
        host_context={"containerName": "mm-omnigent-host-a"},
        prepared=None,
        credential_handles=(),
        acquired=(),
    )

    with pytest.raises(RuntimeError, match="transiently"):
        await realizer._cleanup(host_lease=lease, **cleanup_kwargs)

    # The lease is still claimable, so the release is still reachable.
    retried_lease = await repository.get(lease.leaseRef)
    assert retried_lease.status != "cleaned"
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 3000

    cleanup_kwargs["binding"] = await bindings.get(binding.bindingId)
    binding, retried_lease = await realizer._cleanup(
        host_lease=retried_lease, **cleanup_kwargs
    )

    assert flaky.release_attempts == 2
    assert retried_lease.status == "cleaned"
    assert binding.state is RuntimeBindingState.cleaned
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 0
    assert usage.reserved_temporary_storage_mib == 0
