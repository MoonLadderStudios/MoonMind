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

from datetime import UTC, datetime
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
    LIMITING_RESOURCE_MEMORY,
    LIMITING_RESOURCE_RECONCILIATION,
    STATE_ADOPTED,
    WORKLOAD_CLASS_GENERIC_HOST,
    MachineCapacityLedger,
    MachineResourceBudget,
    MachineTotals,
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
        backend_ref=BACKEND, live_containers=None
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
        backend_ref=BACKEND, live_containers=None
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

    assert observed == {
        "mm-omnigent-host-1": ResourceDemand(
            cpu_millis=1500, memory_mib=2048, processes=256
        ),
        "mm-job-1": ResourceDemand(cpu_millis=1000, memory_mib=1024, processes=128),
    }
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
        return {"mm-job-orphan": ResourceDemand(cpu_millis=500, memory_mib=1024)}

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
