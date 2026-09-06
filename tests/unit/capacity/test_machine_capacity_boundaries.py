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
    LIMITING_RESOURCE_INITIALIZING,
    LIMITING_RESOURCE_MEMORY,
    LIMITING_RESOURCE_RECONCILIATION,
    OWNED_CONTAINER_LABEL_FILTERS,
    OWNED_LAUNCH_CLASSES,
    STATE_ACTIVE,
    STATE_ADOPTED,
    STATE_STORAGE_RETAINED,
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


# ------------------------------------------- owned launch-class registry


#: Label literals that name a MoonMind-owned **volume**, not a container. They
#: consume no CPU, memory or processes, so machine accounting never enumerates
#: them.
NON_CONTAINER_OWNER_LABELS = frozenset(
    {
        "moonmind.kind=container-job-cache",
        "moonmind.owner=generic-omnigent-github-credential",
    }
)


def test_every_owned_container_label_the_deployment_creates_is_classified() -> None:
    """#3881 FINDING-2: an unregistered launch class reads as free capacity.

    Reconciliation only sees the owner labels the registry names. A launch
    class that MoonMind can create but the registry does not know would spend
    the machine invisibly, so every owner-label literal in production source
    must be either a registered launch class or an explicitly named
    non-container label.
    """

    import re

    label_pattern = re.compile(
        r'moonmind\.(kind|owner)(?:=|"\s*:\s*")([A-Za-z0-9_.\-]+)'
    )
    root = pathlib.Path(__file__).resolve().parents[3] / "moonmind"
    registry = set(OWNED_CONTAINER_LABEL_FILTERS)
    unclassified: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if path.parts[-2:] == ("capacity", "docker_inventory.py"):
            continue
        for match in label_pattern.finditer(
            path.read_text(encoding="utf-8", errors="replace")
        ):
            label = f"moonmind.{match.group(1)}={match.group(2)}"
            if label in registry or label in NON_CONTAINER_OWNER_LABELS:
                continue
            unclassified.setdefault(label, str(path))

    assert unclassified == {}


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
    ):
        launch_class = by_selector[selector]
        assert launch_class.reserves is False
        assert selector in OWNED_CONTAINER_LABEL_FILTERS
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
