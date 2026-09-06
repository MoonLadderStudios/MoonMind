"""Real-PostgreSQL evidence for the shared machine capacity reservation.

Source: MoonLadderStudios/MoonMind#3881 (remaining implementation 1-4;
AC2, AC3, AC4).

The count-and-reserve pair is only atomic if it is one serialized operation in
one durable transaction. SQLite serializes every write and mocked lock calls
prove nothing about ``pg_advisory_xact_lock``, so the decisive evidence has to
come from two real PostgreSQL transactions racing the *final* reservation on a
real cluster:

* two workers that both observe the last free slot must produce one winner, and
* the loser must observe the winner's row, not a stale count.

The same cluster also proves that container jobs and generic hosts spend one
shared budget, and that a reservation on a second Docker backend is a separate
budget rather than a pooled one.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import MachineCapacityReservation
from moonmind.capacity import (
    LIMITING_RESOURCE_MEMORY,
    STATE_PRELAUNCH,
    STATE_WAITING,
    WORKLOAD_CLASS_CONTAINER_JOB,
    WORKLOAD_CLASS_GENERIC_HOST,
    WORKLOAD_CLASS_VALIDATION_HOST,
    MachineCapacityLedger,
    MachineResourceBudget,
    MachineTotals,
    ReleaseEvidence,
    ReservationRequest,
    ResourceDemand,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

BACKEND = "system"
OTHER_BACKEND = "second-daemon"

#: A machine with exactly enough headroom-adjusted memory for two launches, so
#: the third is the contended one.
TOTALS = MachineTotals(
    cpu_millis=16000,
    memory_mib=10000,
    processes=16384,
    temporary_storage_mib=10000,
)
#: One launch's demand. Two fit inside the 70% ceiling (7000 MiB); three do not.
DEMAND = ResourceDemand(
    cpu_millis=1000, memory_mib=3000, processes=256, temporary_storage_mib=256
)


def _budget(**env: str) -> MachineResourceBudget:
    """A budget whose only scarce resource is memory.

    The initialization permit is raised out of the way so these tests observe
    the *resource* race rather than the permit; the permit has its own coverage
    in ``tests/unit/capacity/test_machine_reservations.py``.
    """

    return MachineResourceBudget.from_totals(
        TOTALS,
        env={"MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING": "8", **env},
    )


def _request(
    owner: str,
    *,
    backend: str = BACKEND,
    workload_class: str = WORKLOAD_CLASS_GENERIC_HOST,
    owner_kind: str = "omnigent_host_lease",
    demand: ResourceDemand = DEMAND,
) -> ReservationRequest:
    return ReservationRequest(
        backend_ref=backend,
        workload_class=workload_class,
        owner_kind=owner_kind,
        owner_ref=owner,
        demand=demand,
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="omnigent-launch@1",
    )


@pytest_asyncio.fixture()
async def machine_ledger(control_plane_postgres_url):
    """Bind the reservation ledger to an ephemeral PostgreSQL cluster."""

    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(
            MachineCapacityReservation.__table__.create, checkfirst=True
        )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield MachineCapacityLedger(maker), maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(
                MachineCapacityReservation.__table__.drop, checkfirst=True
            )
        await engine.dispose()


async def _reserve_in_own_transaction(
    ledger: MachineCapacityLedger,
    maker,
    request: ReservationRequest,
    *,
    budget: MachineResourceBudget,
    started: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
):
    """Reserve inside one real transaction, optionally holding it open.

    Holding the transaction open between the count and the commit is what makes
    this a race rather than two sequential reservations: the second transaction
    is inside ``reserve_within`` while the first has counted but not committed.
    """

    async with maker() as session:
        outcome = await ledger.reserve_within(session, request=request, budget=budget)
        if started is not None:
            started.set()
        if release is not None:
            await release.wait()
        await session.commit()
        return outcome


@pytest.mark.asyncio
async def test_two_racing_transactions_produce_one_winner(machine_ledger) -> None:
    """AC2: the final reservation is decided by one winner, on real PostgreSQL.

    Both workers see one free slot when they start. The advisory lock forces
    the second to wait until the first has committed its row, so the second
    counts it and is refused instead of overcommitting the machine.
    """

    ledger, maker = machine_ledger
    budget = _budget()
    # Fill the machine to exactly one free slot.
    first = await ledger.reserve(request=_request("lease-a"), budget=budget)
    assert first.admitted is True

    first_counted = asyncio.Event()
    let_first_commit = asyncio.Event()

    winner = asyncio.create_task(
        _reserve_in_own_transaction(
            ledger,
            maker,
            _request("lease-b"),
            budget=budget,
            started=first_counted,
            release=let_first_commit,
        )
    )
    await asyncio.wait_for(first_counted.wait(), timeout=30)

    # The challenger starts while the winner's transaction is still open. It
    # must block on the shared advisory lock rather than read a stale count.
    challenger = asyncio.create_task(
        _reserve_in_own_transaction(ledger, maker, _request("lease-c"), budget=budget)
    )
    await asyncio.sleep(0.5)
    assert not challenger.done(), (
        "the challenger read the machine without waiting for the in-flight "
        "reservation; count-and-reserve is not serialized"
    )

    let_first_commit.set()
    winner_outcome = await asyncio.wait_for(winner, timeout=30)
    loser_outcome = await asyncio.wait_for(challenger, timeout=30)

    assert winner_outcome.admitted is True
    assert loser_outcome.admitted is False
    assert loser_outcome.decision.limiting_resource == LIMITING_RESOURCE_MEMORY
    assert loser_outcome.state == STATE_WAITING

    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(MachineCapacityReservation).where(
                        MachineCapacityReservation.state == STATE_PRELAUNCH
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    usage = await ledger.usage(backend_ref=BACKEND)
    assert usage.reserved_memory_mib == 6000 <= _budget().memory_mib


@pytest.mark.asyncio
async def test_a_container_job_and_a_host_cannot_both_take_the_last_slot(
    machine_ledger,
) -> None:
    """AC3: the shared budget is shared across workload classes, under load."""

    ledger, maker = machine_ledger
    budget = _budget()
    await ledger.reserve(request=_request("lease-a"), budget=budget)

    host_counted = asyncio.Event()
    let_host_commit = asyncio.Event()
    host = asyncio.create_task(
        _reserve_in_own_transaction(
            ledger,
            maker,
            _request("lease-b"),
            budget=budget,
            started=host_counted,
            release=let_host_commit,
        )
    )
    await asyncio.wait_for(host_counted.wait(), timeout=30)

    job = asyncio.create_task(
        _reserve_in_own_transaction(
            ledger,
            maker,
            _request(
                "job-1",
                workload_class=WORKLOAD_CLASS_CONTAINER_JOB,
                owner_kind="container_job",
            ),
            budget=budget,
        )
    )
    await asyncio.sleep(0.5)
    assert not job.done()

    let_host_commit.set()
    host_outcome = await asyncio.wait_for(host, timeout=30)
    job_outcome = await asyncio.wait_for(job, timeout=30)

    assert host_outcome.admitted is True
    assert job_outcome.admitted is False


@pytest.mark.asyncio
async def test_a_validation_host_shares_the_same_budget(machine_ledger) -> None:
    ledger, _maker = machine_ledger
    budget = _budget()
    await ledger.reserve(request=_request("lease-a"), budget=budget)
    await ledger.reserve(
        request=_request(
            "probe-1",
            workload_class=WORKLOAD_CLASS_VALIDATION_HOST,
            owner_kind="validation_host",
        ),
        budget=budget,
    )

    third = await ledger.reserve(request=_request("lease-b"), budget=budget)

    assert third.admitted is False


@pytest.mark.asyncio
async def test_independent_backends_keep_independent_budgets(
    machine_ledger,
) -> None:
    """AC3: two Docker backends must not be accidentally pooled."""

    ledger, _maker = machine_ledger
    budget = _budget()
    await ledger.reserve(request=_request("lease-a"), budget=budget)
    await ledger.reserve(request=_request("lease-b"), budget=budget)

    other = await ledger.reserve(
        request=_request("lease-c", backend=OTHER_BACKEND), budget=budget
    )

    assert other.admitted is True
    assert (await ledger.usage(backend_ref=BACKEND)).reserved_memory_mib == 6000
    assert (await ledger.usage(backend_ref=OTHER_BACKEND)).reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_a_precheck_to_launch_race_is_refused_before_docker_mutation(
    machine_ledger,
) -> None:
    """AC4: an advisory precheck does not license a launch.

    The precheck reads free capacity, another worker wins the machine, and the
    fence re-verification before the Docker mutation refuses the launch.
    """

    ledger, _maker = machine_ledger
    budget = _budget()
    precheck = await ledger.usage(backend_ref=BACKEND)
    assert precheck.reserved_memory_mib == 0

    await ledger.reserve(request=_request("lease-a"), budget=budget)
    await ledger.reserve(request=_request("lease-b"), budget=budget)
    refused = await ledger.reserve(request=_request("lease-c"), budget=budget)

    assert refused.admitted is False
    assert not await ledger.verify(reservation_id=refused.reservation_id, generation=1)


@pytest.mark.asyncio
async def test_an_expired_reservation_is_reclaimed_and_a_retry_is_reconciled(
    machine_ledger,
) -> None:
    """AC4/AC6: expiry frees proven-unused capacity; a retry reuses its own."""

    ledger, _maker = machine_ledger
    budget = _budget(MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60")
    started = datetime.now(UTC)
    lost = await ledger.reserve(
        request=_request("lease-lost"), budget=budget, now=started
    )
    retried = await ledger.reserve(
        request=_request("lease-lost"), budget=budget, now=started
    )
    assert retried.reused is True
    assert retried.reservation_id == lost.reservation_id

    later = started + timedelta(seconds=120)
    recovered = await ledger.reserve(
        request=_request("lease-new"), budget=budget, now=later
    )
    assert recovered.admitted is True
    usage = await ledger.usage(backend_ref=BACKEND, now=later)
    assert usage.reserved_memory_mib == 3000


@pytest.mark.asyncio
async def test_worker_loss_after_container_creation_preserves_ownership(
    machine_ledger,
) -> None:
    """AC6: a live consumer stays discoverable and accounted; then releases once."""

    ledger, _maker = machine_ledger
    budget = _budget(MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS="60")
    started = datetime.now(UTC)
    outcome = await ledger.reserve(
        request=_request("lease-live"), budget=budget, now=started
    )
    await ledger.confirm(
        reservation_id=outcome.reservation_id,
        generation=1,
        container_ref="mm-omnigent-live",
        now=started,
    )

    # The worker is gone. Reconciliation finds the container still running and
    # keeps its accounting rather than reclaiming it on the clock.
    much_later = started + timedelta(hours=6)
    summary = await ledger.reconcile(
        backend_ref=BACKEND,
        live_containers={"mm-omnigent-live": DEMAND},
        now=much_later,
    )
    assert summary["adopted"] == 0
    assert summary["computeReleased"] == 0
    assert (
        await ledger.usage(backend_ref=BACKEND, now=much_later)
    ).reserved_memory_mib == 3000

    released = await ledger.release(
        reservation_id=outcome.reservation_id,
        generation=1,
        evidence=ReleaseEvidence(daemon_observed=True, consumer_removed=True),
        now=much_later,
    )
    assert released is not None
    assert (
        await ledger.release(
            reservation_id=outcome.reservation_id,
            generation=1,
            evidence=ReleaseEvidence(daemon_observed=True, consumer_removed=True),
            now=much_later,
        )
        is None
    )
    assert (
        await ledger.usage(backend_ref=BACKEND, now=much_later)
    ).reserved_memory_mib == 0


@pytest.mark.asyncio
async def test_an_unreadable_backend_blocks_new_admission_on_postgres(
    machine_ledger,
) -> None:
    """AC7: daemon uncertainty blocks admission instead of freeing capacity."""

    ledger, _maker = machine_ledger
    await ledger.reconcile(backend_ref=BACKEND, live_containers=None)

    blocked = await ledger.reserve(request=_request("lease-a"), budget=_budget())
    assert blocked.admitted is False

    await ledger.reconcile(backend_ref=BACKEND, live_containers={})
    assert (
        await ledger.reserve(request=_request("lease-a"), budget=_budget())
    ).admitted is True
