"""Aggregate generic-host capacity and cold-launch admission.

Source issue: MoonLadderStudios/MoonMind#3878 (invariant 7).

Provider Profile capacity governs how many workflows one provider credential
admits. It says nothing about how many on-demand Docker hosts the machine can
carry, and nothing about how many of those hosts may begin a cold launch at
once. A deployment-selected provider ceiling of 8 or 16 must therefore be
admitted against two further limits before a container is created:

* an aggregate ceiling on concurrently allocated generic hosts, and
* a separately bounded cold-launch rate.

Both limits are evaluated against the durable host-lease ledger, so every
worker, every Activity retry, and every restarted process observes the same
counts. An in-process semaphore could not: two agent-runtime workers would each
admit up to their own limit.

MoonLadderStudios/MoonMind#3881 adds the two limits host counting cannot
express, in the same decision and the same transaction:

* the machine's CPU, memory, process and temporary-storage budget, shared with
  container jobs through ``moonmind.capacity`` so neither class can spend the
  budget the other is about to take, and
* a concurrent-initialization permit, which bounds how many launches may be
  *initializing* at once. A rate window bounds how many launches *start*; a
  launch slow enough to span several windows is only bounded by the permit.

Waiting is the caller's job. This module only reports a decision and how long
to wait, so the AgentRun workflow can hold the wait as durable workflow state
instead of occupying a long-running execution Activity slot (invariant 6).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from moonmind.capacity import (
    LIMITING_RESOURCE_RECONCILIATION,
    WORKLOAD_CLASS_GENERIC_HOST,
    MachineCapacityLedger,
    MachineResourceBudget,
    MachineUsage,
    ReservationOutcome,
    ReservationRequest,
    ResourceAdmissionDecision,
)

#: Host lease statuses that still own a container or its cleanup authority.
ACTIVE_HOST_LEASE_STATUSES = ("allocating", "ready", "cleanup_pending")
#: A lease row exists only because a launch was started for it, so the
#: cold-launch window counts rows by age and never by current status. Counting
#: only live statuses let a launch that failed or finished cleanup inside the
#: window stop being evidence, so repeated short-lived failures each observed
#: an empty window and bypassed the burst limit entirely (#3878).

#: Serializes the count-and-reserve pair for generic host admission. Two
#: allocators that only read counts both observe a free slot and both insert,
#: so neither the aggregate ceiling nor the burst limit is actually enforced
#: under the concurrency this change enables (#3878). The key is a fixed
#: constant: it names the machine-wide generic-host domain and never carries a
#: provider, credential, host, or repository identity.
GENERIC_HOST_ADMISSION_LOCK_KEY = 3878001

#: Layer names are low-cardinality by construction: they never carry a
#: provider, credential, host, or repository identity (#3878 AC11).
LIMITING_LAYER_HOST_CAPACITY = "generic_host_capacity"
LIMITING_LAYER_COLD_LAUNCH_RATE = "generic_host_cold_launch_rate"
#: The machine cannot carry this launch's requested resources, or the shared
#: accounting is not currently provable. The exact resource is reported
#: separately as ``limitingResource``; both names are fixed, low-cardinality
#: strings and never carry a plan, lease, host or credential identity.
LIMITING_LAYER_MACHINE_RESOURCES = "machine_resources"


@dataclass(frozen=True)
class GenericHostCapacityDecision:
    """One admission verdict for a generic host allocation."""

    admitted: bool
    limiting_layer: str | None
    active_hosts: int
    host_capacity: int
    recent_cold_launches: int
    cold_launch_burst: int
    cold_launch_window_seconds: int
    retry_after_seconds: int
    #: The shared machine-resource verdict, when a machine budget participated.
    #: ``None`` means no budget was resolved for this evaluation, which is the
    #: case for the advisory read-only precheck; the durable allocation always
    #: carries one.
    machine: ResourceAdmissionDecision | None = None
    #: What the shared ledger currently accounts for. Available without a
    #: budget, so the advisory precheck can still report oldest-waiter age and
    #: reconciliation health, and can still refuse to admit against an
    #: unprovable backend.
    machine_usage: MachineUsage | None = None
    #: The durable reservation this decision established or reused, when the
    #: decision was made inside the allocating transaction.
    reservation_id: str | None = None
    #: The request cannot fit inside the configured ceilings at all. The caller
    #: must reject it: queueing it would wait forever and clamping it would
    #: silently change a billing-relevant resource value.
    unsatisfiable: bool = False

    def as_payload(self) -> dict[str, Any]:
        """Return the compact, identity-free projection for workflow history."""

        payload: dict[str, Any] = {
            "admitted": self.admitted,
            "limitingLayer": self.limiting_layer,
            "activeHosts": self.active_hosts,
            "hostCapacity": self.host_capacity,
            "recentColdLaunches": self.recent_cold_launches,
            "coldLaunchBurst": self.cold_launch_burst,
            "coldLaunchWindowSeconds": self.cold_launch_window_seconds,
            "retryAfterSeconds": self.retry_after_seconds,
            "unsatisfiable": self.unsatisfiable,
        }
        usage = self.machine_usage or (
            self.machine.usage if self.machine is not None else None
        )
        if usage is not None:
            payload["oldestWaiterAgeSeconds"] = usage.oldest_waiter_age_seconds
            payload["reconciliationHealthy"] = not (
                usage.reconciliation_blocked or usage.reconciliation_faults
            )
        if self.machine is not None:
            machine = self.machine.as_payload()
            payload["limitingResource"] = machine.pop("limitingResource")
            # Reservation identities are opaque digests, never metric labels,
            # and they stay out of this payload entirely: a retry re-derives
            # its own allocation id from the lease ref it already holds
            # (``moonmind/omnigent/realizers/generic_host.py``).
            payload["machine"] = machine
        elif usage is not None:
            payload["limitingResource"] = self.limiting_resource_without_budget
            payload["machine"] = usage.as_payload()
        return payload

    @property
    def limiting_resource_without_budget(self) -> str | None:
        """Return the ledger-only limiting resource for the advisory precheck."""

        usage = self.machine_usage
        if usage is not None and usage.reconciliation_blocked:
            return LIMITING_RESOURCE_RECONCILIATION
        return None

    @property
    def waiting_reason(self) -> str:
        """Return an operator-facing reason naming the actual limiting layer."""

        if self.admitted:
            return "Generic host capacity is available."
        if self.limiting_layer == LIMITING_LAYER_MACHINE_RESOURCES:
            if self.machine is not None:
                return self.machine.reason
            return (
                "Waiting for machine capacity reconciliation; "
                f"missing_condition={LIMITING_RESOURCE_RECONCILIATION}; "
                "the container backend inventory is not currently provable."
            )
        if self.limiting_layer == LIMITING_LAYER_COLD_LAUNCH_RATE:
            return (
                "Waiting for generic host cold-launch capacity; "
                f"missing_condition={LIMITING_LAYER_COLD_LAUNCH_RATE}; "
                f"launches_in_window={self.recent_cold_launches}; "
                f"cold_launch_burst={self.cold_launch_burst}; "
                f"window_seconds={self.cold_launch_window_seconds}."
            )
        return (
            "Waiting for generic host capacity; "
            f"missing_condition={LIMITING_LAYER_HOST_CAPACITY}; "
            f"active_hosts={self.active_hosts}; "
            f"host_capacity={self.host_capacity}."
        )


def evaluate_generic_host_capacity(
    *,
    active_hosts: int,
    recent_cold_launches: int,
    host_capacity: int,
    cold_launch_burst: int,
    cold_launch_window_seconds: int,
) -> GenericHostCapacityDecision:
    """Return the admission verdict for one generic host allocation.

    The aggregate ceiling is checked first: when the machine is already full,
    reporting the cold-launch layer would send the operator after the wrong
    limit.
    """

    if active_hosts >= host_capacity:
        return GenericHostCapacityDecision(
            admitted=False,
            limiting_layer=LIMITING_LAYER_HOST_CAPACITY,
            active_hosts=active_hosts,
            host_capacity=host_capacity,
            recent_cold_launches=recent_cold_launches,
            cold_launch_burst=cold_launch_burst,
            cold_launch_window_seconds=cold_launch_window_seconds,
            # A host is released by cleanup, not by a clock, so re-check on the
            # cold-launch window rather than inventing a longer backoff.
            retry_after_seconds=cold_launch_window_seconds,
        )
    if recent_cold_launches >= cold_launch_burst:
        return GenericHostCapacityDecision(
            admitted=False,
            limiting_layer=LIMITING_LAYER_COLD_LAUNCH_RATE,
            active_hosts=active_hosts,
            host_capacity=host_capacity,
            recent_cold_launches=recent_cold_launches,
            cold_launch_burst=cold_launch_burst,
            cold_launch_window_seconds=cold_launch_window_seconds,
            retry_after_seconds=cold_launch_window_seconds,
        )
    return GenericHostCapacityDecision(
        admitted=True,
        limiting_layer=None,
        active_hosts=active_hosts,
        host_capacity=host_capacity,
        recent_cold_launches=recent_cold_launches,
        cold_launch_burst=cold_launch_burst,
        cold_launch_window_seconds=cold_launch_window_seconds,
        retry_after_seconds=0,
    )


class GenericHostCapacityAdmission:
    """Evaluate aggregate host and cold-launch limits against the durable ledger."""

    def __init__(
        self,
        *,
        session_factory: Any,
        host_capacity: int,
        cold_launch_burst: int,
        cold_launch_window_seconds: int,
        machine_capacity: MachineCapacityLedger | None = None,
        backend_ref: str | None = None,
    ) -> None:
        if host_capacity < 1:
            raise ValueError("host_capacity must be positive")
        if cold_launch_burst < 1:
            raise ValueError("cold_launch_burst must be positive")
        if cold_launch_window_seconds < 1:
            raise ValueError("cold_launch_window_seconds must be positive")
        self._session_factory = session_factory
        self._host_capacity = host_capacity
        self._cold_launch_burst = cold_launch_burst
        self._cold_launch_window_seconds = cold_launch_window_seconds
        # The shared machine ledger. Generic hosts and container jobs run on the
        # same Docker daemon, so they must reserve against the same backend
        # identity or the "shared" budget is two independent budgets (#3881).
        self._machine_capacity = machine_capacity
        self._backend_ref = str(backend_ref or "").strip() or None

    @property
    def backend_ref(self) -> str | None:
        """Return the exact Docker backend these reservations are scoped to."""

        return self._backend_ref

    @property
    def machine_capacity(self) -> MachineCapacityLedger | None:
        return self._machine_capacity

    @classmethod
    def from_environment(
        cls,
        *,
        session_factory: Any,
        env: Mapping[str, Any] | None = None,
        machine_capacity: MachineCapacityLedger | None = None,
    ) -> "GenericHostCapacityAdmission":
        from moonmind.config.container_backend_settings import (
            resolve_container_backend_settings,
        )
        from moonmind.omnigent.settings import (
            generic_host_capacity,
            generic_host_cold_launch_burst,
            generic_host_cold_launch_window_seconds,
        )

        return cls(
            session_factory=session_factory,
            host_capacity=generic_host_capacity(env=env),
            cold_launch_burst=generic_host_cold_launch_burst(env=env),
            cold_launch_window_seconds=(
                generic_host_cold_launch_window_seconds(env=env)
            ),
            machine_capacity=(
                machine_capacity
                if machine_capacity is not None
                else MachineCapacityLedger(session_factory)
            ),
            # Generic hosts launch on the deployment's container backend, so the
            # machine budget is scoped to that exact backend ref.
            backend_ref=resolve_container_backend_settings(
                dict(env) if env is not None else None
            ).default_backend_ref,
        )

    async def observe(
        self, *, now: datetime | None = None
    ) -> tuple[int, int]:
        """Return (active hosts, cold launches inside the current window)."""

        from sqlalchemy import func, select

        from api_service.db.models import OmnigentHostLeaseRecordV2

        async with self._session_factory() as session:
            return await self.observe_within(session, now=now)

    async def observe_within(
        self, session: Any, *, now: datetime | None = None
    ) -> tuple[int, int]:
        """Count active hosts and in-window launches inside ``session``."""

        from sqlalchemy import func, select

        from api_service.db.models import OmnigentHostLeaseRecordV2

        observed_at = now or datetime.now(UTC)
        window_start = observed_at - timedelta(
            seconds=self._cold_launch_window_seconds
        )
        active = await session.execute(
            select(func.count())
            .select_from(OmnigentHostLeaseRecordV2)
            .where(
                OmnigentHostLeaseRecordV2.status.in_(
                    ACTIVE_HOST_LEASE_STATUSES
                )
            )
        )
        recent = await session.execute(
            select(func.count())
            .select_from(OmnigentHostLeaseRecordV2)
            .where(
                # Status-independent: a row inside the window is launch
                # evidence even after it failed or finished cleanup.
                OmnigentHostLeaseRecordV2.created_at >= window_start,
            )
        )
        return int(active.scalar() or 0), int(recent.scalar() or 0)

    async def lock_for_admission(self, session: Any) -> None:
        """Serialize concurrent admissions for the caller's transaction.

        PostgreSQL takes a transaction-scoped advisory lock, so a second
        allocator blocks until the first has inserted its lease row and
        therefore counts it. SQLite serializes writes and has no advisory
        locks, so the surrounding transaction already provides the guarantee.
        """

        from sqlalchemy import text

        dialect = getattr(getattr(session, "bind", None), "dialect", None)
        name = getattr(dialect, "name", "") or ""
        if not name:
            engine = getattr(session, "get_bind", None)
            if callable(engine):
                try:
                    name = getattr(engine().dialect, "name", "") or ""
                except Exception:
                    name = ""
        if name.startswith("postgres"):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": GENERIC_HOST_ADMISSION_LOCK_KEY},
            )

    async def evaluate(
        self,
        *,
        already_allocated: bool = False,
        now: datetime | None = None,
        demand: Any | None = None,
        budget: MachineResourceBudget | None = None,
    ) -> GenericHostCapacityDecision:
        """Return the current admission verdict.

        ``already_allocated`` is set when this execution's own host lease
        already exists — a retry or a resumed attempt must never be refused by
        the capacity it is already counted in.

        This read reserves nothing and is therefore advisory (#3881): passing it
        does not hold the machine, and the durable allocation may still refuse a
        race the caller must return to waiting for. When ``demand`` and
        ``budget`` are supplied the machine-resource layer is reported too, so
        the workflow waits on the limit that is actually holding it.
        """

        active_hosts, recent_cold_launches = await self.observe(now=now)
        machine: ResourceAdmissionDecision | None = None
        usage: MachineUsage | None = None
        if self._machine_capacity is not None and self._backend_ref is not None:
            usage = await self._machine_capacity.usage(
                backend_ref=self._backend_ref, now=now
            )
            if demand is not None and budget is not None:
                from moonmind.capacity import evaluate_resource_admission

                machine = evaluate_resource_admission(
                    demand=demand,
                    budget=budget,
                    usage=usage,
                    workload_class=WORKLOAD_CLASS_GENERIC_HOST,
                )
        return self._verdict(
            active_hosts=active_hosts,
            recent_cold_launches=recent_cold_launches,
            already_allocated=already_allocated,
            machine=machine,
            machine_usage=usage,
        )

    async def evaluate_within(
        self,
        session: Any,
        *,
        already_allocated: bool = False,
        now: datetime | None = None,
        reservation: ReservationRequest | None = None,
        budget: MachineResourceBudget | None = None,
    ) -> GenericHostCapacityDecision:
        """Return the admission verdict inside the caller's transaction.

        The caller must insert its durable reservation in this same
        transaction: counting in one transaction and reserving in another is
        the race this method exists to close (#3878).

        When ``reservation`` and ``budget`` are supplied the shared machine
        reservation is taken here too, in this same transaction (#3881). Lock
        order is fixed — the generic-host key first, then the shared machine
        key — so a container job, which only ever takes the machine key, can
        never deadlock against a host allocation.
        """

        await self.lock_for_admission(session)
        active_hosts, recent_cold_launches = await self.observe_within(
            session, now=now
        )
        machine_usage: MachineUsage | None = None
        if self._machine_capacity is not None and self._backend_ref is not None:
            machine_usage = await self._machine_capacity.usage_within(
                session, backend_ref=self._backend_ref, now=now
            )
        decision = self._verdict(
            active_hosts=active_hosts,
            recent_cold_launches=recent_cold_launches,
            already_allocated=already_allocated,
            machine_usage=machine_usage,
        )
        if reservation is None or budget is None:
            return decision
        if self._machine_capacity is None:
            raise ValueError(
                "a machine reservation was requested without a capacity ledger"
            )
        if not decision.admitted:
            # The host-count layers already refused, so do not take a machine
            # reservation this allocation cannot use.
            return decision
        outcome: ReservationOutcome = await self._machine_capacity.reserve_within(
            session, request=reservation, budget=budget, now=now
        )
        return replace(
            decision,
            admitted=outcome.admitted,
            limiting_layer=(
                None if outcome.admitted else LIMITING_LAYER_MACHINE_RESOURCES
            ),
            retry_after_seconds=(
                decision.retry_after_seconds
                if outcome.admitted
                else outcome.decision.retry_after_seconds
            ),
            machine=outcome.decision,
            reservation_id=outcome.reservation_id,
            unsatisfiable=outcome.unsatisfiable,
        )

    def _verdict(
        self,
        *,
        active_hosts: int,
        recent_cold_launches: int,
        already_allocated: bool,
        machine: ResourceAdmissionDecision | None = None,
        machine_usage: MachineUsage | None = None,
    ) -> GenericHostCapacityDecision:
        if already_allocated:
            # An execution counted in the ledger already holds its machine
            # accounting too, so it is never re-admitted against it.
            return GenericHostCapacityDecision(
                admitted=True,
                limiting_layer=None,
                active_hosts=active_hosts,
                host_capacity=self._host_capacity,
                recent_cold_launches=recent_cold_launches,
                cold_launch_burst=self._cold_launch_burst,
                cold_launch_window_seconds=self._cold_launch_window_seconds,
                retry_after_seconds=0,
                machine=machine,
                machine_usage=machine_usage,
            )
        decision = replace(
            evaluate_generic_host_capacity(
                active_hosts=active_hosts,
                recent_cold_launches=recent_cold_launches,
                host_capacity=self._host_capacity,
                cold_launch_burst=self._cold_launch_burst,
                cold_launch_window_seconds=self._cold_launch_window_seconds,
            ),
            machine_usage=machine_usage,
        )
        if machine is not None:
            if decision.admitted and not machine.admitted:
                return replace(
                    decision,
                    admitted=False,
                    limiting_layer=LIMITING_LAYER_MACHINE_RESOURCES,
                    retry_after_seconds=machine.retry_after_seconds,
                    machine=machine,
                    unsatisfiable=machine.unsatisfiable,
                )
            return replace(decision, machine=machine)
        if (
            decision.admitted
            and machine_usage is not None
            and machine_usage.reconciliation_blocked
        ):
            # The backend's own state is not currently provable, so this read
            # must not report free capacity it cannot establish.
            return replace(
                decision,
                admitted=False,
                limiting_layer=LIMITING_LAYER_MACHINE_RESOURCES,
                retry_after_seconds=self._cold_launch_window_seconds,
            )
        return decision


__all__ = [
    "ACTIVE_HOST_LEASE_STATUSES",
    "LIMITING_LAYER_COLD_LAUNCH_RATE",
    "LIMITING_LAYER_HOST_CAPACITY",
    "LIMITING_LAYER_MACHINE_RESOURCES",
    "GenericHostCapacityAdmission",
    "GenericHostCapacityDecision",
    "evaluate_generic_host_capacity",
]
