"""Durable machine resource reservations shared by every managed launch.

Source issue: MoonLadderStudios/MoonMind#3881.

Counting host rows bounds *how many* containers exist. It says nothing about
whether the machine can carry their CPU, memory, process and temporary-storage
demand, and it says nothing about the container jobs spending the same physical
budget from a different code path. This module is the one deployment-owned
accounting authority those demands are reserved in:

* generic Omnigent hosts reserve through the existing host-lease allocation
  transaction (``moonmind.omnigent.host_leases``), and
* container jobs reserve through the container-job backend's launch boundary,

so the two cannot each spend the machine independently.

Every other MoonMind-owned container launch class — OAuth credential-authority
hosts, credential validators, managed sessions and workload containers — is
accounted from daemon evidence instead of reserving, because resource admission
must never refuse a credential-authority launch or an already-admitted run.
Their observed demand still reduces what reserving launches may take; the exact
registry is ``moonmind.capacity.docker_inventory``.

It adds no lifecycle owner and no always-on service: the rows live in the
control-plane database the callers already write, the existing cleanup services
still perform teardown, and this module only observes their evidence.

Layering, kept deliberately distinct:

``machine resource ceilings``
    How much CPU/memory/process/temporary storage may be reserved at once.
``concurrent initialization permits``
    How many launches may be *initializing* at once. A slow launch holds its
    permit for as long as it is actually initializing, so a launch that spans
    several rate windows is still bounded.
``recent-launch rate limits``
    How many launches may *start* per window (owned by
    ``moonmind.omnigent.host_capacity``; unchanged here).

Reservations are scoped by exact Docker backend identity. Two independent
backends have two independent budgets and are never pooled.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from moonmind.capacity.docker_inventory import OwnedContainerInventory

#: Serializes count-and-reserve for *every* covered workload class. Generic
#: hosts and container jobs must block on the same key or the shared budget is
#: not actually shared: each class would observe free capacity the other is
#: about to take. The key is a fixed constant naming the machine-wide
#: accounting domain; it never carries a plan, lease, host, job or credential
#: identity.
MACHINE_CAPACITY_ADMISSION_LOCK_KEY = 3881001

WORKLOAD_CLASS_GENERIC_HOST = "generic_host"
WORKLOAD_CLASS_CONTAINER_JOB = "container_job"
#: Every managed launch class that reserves the machine budget before it
#: launches. A launch class missing from this tuple would spend the budget
#: without reserving it, so it must instead be accounted from daemon evidence
#: as ``WORKLOAD_CLASS_OBSERVED`` (see ``moonmind.capacity.docker_inventory``).
COVERED_WORKLOAD_CLASSES = (
    WORKLOAD_CLASS_GENERIC_HOST,
    WORKLOAD_CLASS_CONTAINER_JOB,
)
#: Rows reconciliation writes on its own behalf. They are deliberately not in
#: ``COVERED_WORKLOAD_CLASSES``: no caller may reserve as one, and recording a
#: reconciliation marker under a real launch class would misattribute it.
WORKLOAD_CLASS_RECONCILIATION = "reconciliation"
#: An owned live container adopted without its accounting record, from a launch
#: class that was supposed to reserve. Which managed launch created it is not
#: recoverable from the container alone, and guessing would be worse than
#: saying so. This is a reconciliation fault.
WORKLOAD_CLASS_UNATTRIBUTED = "unattributed"
#: An owned live container from a launch class the deployment's documented
#: resource-class policy accounts by observation rather than reservation. Its
#: demand is spent exactly like an active reservation's, so it reduces what
#: reserving launches may take; it is not a fault, because nothing was supposed
#: to reserve it.
WORKLOAD_CLASS_OBSERVED = "observed"

#: A waiter marker: the owner asked and was refused. It reserves nothing; it
#: exists so ``oldest waiter age`` is observable without a second ledger.
STATE_WAITING = "waiting"
#: A short-lived reservation held across the bounded hand-off to the
#: complementary provider reservation (#3880) and the Docker create/start. It
#: is fully accounted and it holds a concurrent-initialization permit.
STATE_PRELAUNCH = "prelaunch"
#: The consumer exists on the backend. Fully accounted, never clock-reclaimed.
STATE_ACTIVE = "active"
#: Reconciliation found an owned live container with no accounting record. It
#: is accounted exactly like ``active`` so it can never read as free capacity.
#: A row adopted for a *reserving* launch class is additionally counted as a
#: reconciliation fault; one adopted for an observed launch class is not.
STATE_ADOPTED = "adopted"
#: Compute is released because the consumer is proven stopped or removed, but
#: retained volumes still consume storage.
STATE_STORAGE_RETAINED = "storage_retained"
#: Nothing is accounted.
STATE_RELEASED = "released"
#: The backend's state is unknown (daemon unreachable, inventory unreadable).
#: Blocks new admission on this backend rather than inventing free capacity.
STATE_BLOCKED = "blocked"

#: States whose CPU, memory and process demand is still spent.
COMPUTE_ACCOUNTED_STATES = (STATE_PRELAUNCH, STATE_ACTIVE, STATE_ADOPTED)
#: States whose temporary storage is still spent. Storage outlives compute:
#: MoonMind's temporary storage is tmpfs and retained volumes, and both survive
#: a stopped container.
STORAGE_ACCOUNTED_STATES = COMPUTE_ACCOUNTED_STATES + (STATE_STORAGE_RETAINED,)
#: Any state that consumes something.
ACCOUNTED_STATES = STORAGE_ACCOUNTED_STATES
#: States that hold a concurrent-initialization permit. The permit follows the
#: actual launch state, not a clock: a launch that is still initializing after
#: several rate windows still holds exactly one permit.
INITIALIZING_STATES = (STATE_PRELAUNCH,)
#: States a launch may confirm from. The released states are included because a
#: launch that outlived its prelaunch window can still succeed, and a live
#: consumer with no accounting record is worse than a restored reservation.
CONFIRMABLE_STATES = (
    STATE_PRELAUNCH,
    STATE_ACTIVE,
    STATE_STORAGE_RETAINED,
    STATE_RELEASED,
)

#: Limiting-resource names are low-cardinality by construction: they never
#: carry a plan, lease, host, job or credential identity (#3881 remaining
#: implementation 8).
LIMITING_RESOURCE_CPU = "machine_cpu"
LIMITING_RESOURCE_MEMORY = "machine_memory"
LIMITING_RESOURCE_PROCESSES = "machine_processes"
LIMITING_RESOURCE_STORAGE = "machine_temporary_storage"
LIMITING_RESOURCE_INITIALIZING = "concurrent_initialization"
LIMITING_RESOURCE_RECONCILIATION = "reconciliation_health"

#: Deployment-owned tuning. Every value is namespaced and restrictive by
#: default; omitting all of them exercises the same production path.
MACHINE_UTILIZATION_PERCENT_ENV = "MOONMIND_MACHINE_UTILIZATION_PERCENT"
MACHINE_CPU_MILLIS_ENV = "MOONMIND_MACHINE_CPU_MILLIS"
MACHINE_MEMORY_MIB_ENV = "MOONMIND_MACHINE_MEMORY_MIB"
MACHINE_PROCESSES_ENV = "MOONMIND_MACHINE_PROCESSES"
MACHINE_TEMPORARY_STORAGE_MIB_ENV = "MOONMIND_MACHINE_TEMPORARY_STORAGE_MIB"
MACHINE_MAX_CONCURRENT_INITIALIZING_ENV = "MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING"
MACHINE_PRELAUNCH_TTL_SECONDS_ENV = "MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS"

#: Share of the machine managed launches may reserve. The remainder is the
#: documented control-plane and cleanup headroom: a saturated workload must
#: still leave the worker, the janitors and Docker teardown room to run
#: (#3881 AC8). 70% matches the ceiling the container-job backend already
#: applied to its own memory budget, so adopting the shared authority does not
#: change the admitted volume of the workload that already existed.
DEFAULT_MACHINE_UTILIZATION_PERCENT = 70
#: A cold launch pulls, creates and registers a container. Bounding how many do
#: that at once is a different limit from bounding how many *start* per window.
DEFAULT_MAX_CONCURRENT_INITIALIZING = 2
#: A prelaunch reservation is a bounded hand-off, never an indefinite hold on
#: the machine while a complementary provider reservation is negotiated.
DEFAULT_PRELAUNCH_TTL_SECONDS = 300
#: Docker reports memory and CPU; it has no machine-wide process ceiling. The
#: machine process budget is therefore derived from CPU count at a documented
#: ratio and is overridable by ``MOONMIND_MACHINE_PROCESSES``.
DEFAULT_PROCESSES_PER_CPU = 1024

_MIB = 1024 * 1024


class MachineCapacityUnavailable(RuntimeError):
    """The machine's own capacity could not be established.

    Raised when the daemon or its inventory cannot be read. Callers must fail
    closed: an unreadable machine is not an empty machine.
    """


class MachineCapacityConflict(RuntimeError):
    """A reservation fence did not match the persisted reservation."""


def machine_reservation_id(
    *, backend_ref: str, owner_kind: str, owner_ref: str, generation: int
) -> str:
    """Return the deterministic reservation identity for one owner attempt.

    Derived from the exact backend, owner and generation so a retry of the same
    attempt names the reservation it already holds instead of taking a second
    one, and so a later generation can never write through an earlier one's
    reservation.
    """

    digest = hashlib.sha256(
        "\0".join(
            (str(backend_ref), str(owner_kind), str(owner_ref), str(int(generation)))
        ).encode("utf-8")
    ).hexdigest()
    return f"machine-reservation:sha256:{digest}"


def _positive_int_env(
    value: object | None, *, default: int, env_name: str, minimum: int = 1
) -> int:
    cleaned = str(value or "").strip()
    if not cleaned:
        return default
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"invalid {env_name} value {cleaned!r}: expected an integer >= {minimum}"
        ) from exc
    if parsed < minimum:
        raise ValueError(
            f"invalid {env_name} value {cleaned!r}: expected an integer >= {minimum}"
        )
    return parsed


@dataclass(frozen=True)
class ResourceDemand:
    """One launch's requested machine resources, in integer units only.

    Fractional CPU is expressed in millis so accounting never accumulates
    floating point error across dozens of reservations.
    """

    cpu_millis: int = 0
    memory_mib: int = 0
    processes: int = 0
    temporary_storage_mib: int = 0

    def __post_init__(self) -> None:
        for field in (
            "cpu_millis",
            "memory_mib",
            "processes",
            "temporary_storage_mib",
        ):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field} must be an integer number of units")
            if value < 0:
                raise ValueError(f"{field} must not be negative")

    @classmethod
    def zero(cls) -> "ResourceDemand":
        return cls()

    @classmethod
    def from_launch_policy_limits(cls, limits: Mapping[str, Any]) -> "ResourceDemand":
        """Resolve demand from the trusted Launch Policy limits.

        Demand is resolved from policy, never from workflow arguments: a
        caller-supplied number would let one run quietly spend the machine's
        budget on behalf of a Host Class that never authorized it.
        """

        return cls(
            cpu_millis=int(limits.get("cpuMillis", 0) or 0),
            memory_mib=int(limits.get("memoryMiB", 0) or 0),
            processes=int(limits.get("processes", 0) or 0),
            temporary_storage_mib=int(limits.get("temporaryStorageMiB", 0) or 0),
        )

    def as_payload(self) -> dict[str, int]:
        return {
            "cpuMillis": self.cpu_millis,
            "memoryMiB": self.memory_mib,
            "processes": self.processes,
            "temporaryStorageMiB": self.temporary_storage_mib,
        }


@dataclass(frozen=True)
class MachineTotals:
    """What one Docker backend's machine physically has."""

    cpu_millis: int
    memory_mib: int
    processes: int
    temporary_storage_mib: int

    def __post_init__(self) -> None:
        for field in (
            "cpu_millis",
            "memory_mib",
            "processes",
            "temporary_storage_mib",
        ):
            if getattr(self, field) < 1:
                raise MachineCapacityUnavailable(
                    f"machine {field} was not reported as a usable capacity"
                )


async def probe_machine_totals(
    runner: Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]],
) -> MachineTotals:
    """Return the selected backend's machine totals from its own daemon.

    ``runner`` is the trusted Docker command runner; arguments exclude the
    ``docker`` binary itself, matching the container-job backend's runner.

    A daemon that cannot be read raises rather than returning a default: an
    unreadable machine must block admission, not admit against a guess.
    """

    code, stdout, stderr = await runner(
        ("info", "--format", "{{.MemTotal}}\t{{.NCPU}}")
    )
    if code:
        detail = stderr.decode(errors="replace").strip()[:200]
        raise MachineCapacityUnavailable(
            "container backend machine capacity is unavailable"
            + (f": {detail}" if detail else "")
        )
    raw = stdout.decode(errors="replace").strip()
    parts = raw.split("\t")
    if len(parts) != 2:
        raise MachineCapacityUnavailable(
            "container backend did not report a valid machine capacity"
        )
    try:
        memory_bytes = int(parts[0].strip())
        ncpu = int(parts[1].strip())
    except ValueError as exc:
        raise MachineCapacityUnavailable(
            "container backend did not report a valid machine capacity"
        ) from exc
    memory_mib = memory_bytes // _MIB
    if memory_mib < 16 or ncpu < 1:
        raise MachineCapacityUnavailable(
            "container backend machine capacity is unavailable"
        )
    return MachineTotals(
        cpu_millis=ncpu * 1000,
        memory_mib=memory_mib,
        processes=ncpu * DEFAULT_PROCESSES_PER_CPU,
        # MoonMind's temporary storage is tmpfs, which is RAM-backed, so the
        # machine's temporary-storage total is its memory total. Deployments
        # backing /tmp with a disk override it explicitly.
        temporary_storage_mib=memory_mib,
    )


@dataclass(frozen=True)
class MachineResourceBudget:
    """The ceilings managed launches may reserve on one backend.

    ``totals`` is what the machine has; the ceilings are what managed launches
    may take. The difference is the documented control-plane and cleanup
    headroom.
    """

    totals: MachineTotals
    cpu_millis: int
    memory_mib: int
    processes: int
    temporary_storage_mib: int
    max_concurrent_initializing: int
    prelaunch_ttl_seconds: int

    @classmethod
    def from_totals(
        cls,
        totals: MachineTotals,
        *,
        env: Mapping[str, Any] | None = None,
    ) -> "MachineResourceBudget":
        source = env if env is not None else os.environ
        percent = _positive_int_env(
            source.get(MACHINE_UTILIZATION_PERCENT_ENV),
            default=DEFAULT_MACHINE_UTILIZATION_PERCENT,
            env_name=MACHINE_UTILIZATION_PERCENT_ENV,
        )
        if percent > 99:
            # Headroom is not optional: the control plane, the janitors and
            # Docker teardown must still run on a saturated machine.
            raise ValueError(
                f"invalid {MACHINE_UTILIZATION_PERCENT_ENV} value {percent!r}: "
                "managed launches may not reserve the whole machine"
            )
        ceilings: dict[str, int] = {}
        for field, env_name in (
            ("cpu_millis", MACHINE_CPU_MILLIS_ENV),
            ("memory_mib", MACHINE_MEMORY_MIB_ENV),
            ("processes", MACHINE_PROCESSES_ENV),
            ("temporary_storage_mib", MACHINE_TEMPORARY_STORAGE_MIB_ENV),
        ):
            total = getattr(totals, field)
            automatic = max(1, (total * percent) // 100)
            configured = source.get(env_name)
            if not str(configured or "").strip():
                ceilings[field] = automatic
                continue
            explicit = _positive_int_env(
                configured, default=automatic, env_name=env_name
            )
            if explicit > total:
                raise ValueError(
                    f"invalid {env_name} value {explicit!r}: exceeds the "
                    "machine capacity the container backend reports"
                )
            ceilings[field] = explicit
        return cls(
            totals=totals,
            max_concurrent_initializing=_positive_int_env(
                source.get(MACHINE_MAX_CONCURRENT_INITIALIZING_ENV),
                default=DEFAULT_MAX_CONCURRENT_INITIALIZING,
                env_name=MACHINE_MAX_CONCURRENT_INITIALIZING_ENV,
            ),
            prelaunch_ttl_seconds=_positive_int_env(
                source.get(MACHINE_PRELAUNCH_TTL_SECONDS_ENV),
                default=DEFAULT_PRELAUNCH_TTL_SECONDS,
                env_name=MACHINE_PRELAUNCH_TTL_SECONDS_ENV,
            ),
            **ceilings,
        )

    def headroom(self) -> ResourceDemand:
        """Return the machine share reserved for the control plane and cleanup."""

        return ResourceDemand(
            cpu_millis=self.totals.cpu_millis - self.cpu_millis,
            memory_mib=self.totals.memory_mib - self.memory_mib,
            processes=self.totals.processes - self.processes,
            temporary_storage_mib=(
                self.totals.temporary_storage_mib - self.temporary_storage_mib
            ),
        )

    def as_payload(self) -> dict[str, int]:
        return {
            "cpuMillis": self.cpu_millis,
            "memoryMiB": self.memory_mib,
            "processes": self.processes,
            "temporaryStorageMiB": self.temporary_storage_mib,
            "maxConcurrentInitializing": self.max_concurrent_initializing,
        }


async def machine_budget_from_runner(
    runner: Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]],
    *,
    env: Mapping[str, Any] | None = None,
) -> MachineResourceBudget:
    """Return the deployment budget for the backend ``runner`` talks to."""

    return MachineResourceBudget.from_totals(
        await probe_machine_totals(runner), env=env
    )


@dataclass(frozen=True)
class MachineUsage:
    """What one backend's ledger currently accounts for."""

    reserved_cpu_millis: int = 0
    reserved_memory_mib: int = 0
    reserved_processes: int = 0
    reserved_temporary_storage_mib: int = 0
    initializing: int = 0
    waiting: int = 0
    oldest_waiter_age_seconds: int = 0
    reconciliation_faults: int = 0
    reconciliation_blocked: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "reservedCpuMillis": self.reserved_cpu_millis,
            "reservedMemoryMiB": self.reserved_memory_mib,
            "reservedProcesses": self.reserved_processes,
            "reservedTemporaryStorageMiB": self.reserved_temporary_storage_mib,
            "initializing": self.initializing,
            "waiting": self.waiting,
            "oldestWaiterAgeSeconds": self.oldest_waiter_age_seconds,
            "reconciliationFaults": self.reconciliation_faults,
            "reconciliationBlocked": self.reconciliation_blocked,
        }


def _percent(used: int, ceiling: int) -> int:
    if ceiling <= 0:
        return 100
    return min(100, (used * 100) // ceiling)


@dataclass(frozen=True)
class ResourceAdmissionDecision:
    """One verdict for one machine resource reservation."""

    admitted: bool
    limiting_resource: str | None
    #: The request can never fit, even on an empty machine. Waiting for it
    #: would queue forever, and clamping it would silently change a
    #: billing-relevant resource value, so the caller must reject it.
    unsatisfiable: bool
    demand: ResourceDemand
    usage: MachineUsage
    budget: MachineResourceBudget
    retry_after_seconds: int

    @property
    def utilization_percent(self) -> dict[str, int]:
        """Return safe utilization per resource, identity-free."""

        return {
            "cpu": _percent(self.usage.reserved_cpu_millis, self.budget.cpu_millis),
            "memory": _percent(self.usage.reserved_memory_mib, self.budget.memory_mib),
            "processes": _percent(self.usage.reserved_processes, self.budget.processes),
            "temporaryStorage": _percent(
                self.usage.reserved_temporary_storage_mib,
                self.budget.temporary_storage_mib,
            ),
        }

    def as_payload(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "limitingResource": self.limiting_resource,
            "unsatisfiable": self.unsatisfiable,
            "requested": self.demand.as_payload(),
            "ceilings": self.budget.as_payload(),
            "utilizationPercent": self.utilization_percent,
            "retryAfterSeconds": self.retry_after_seconds,
            **self.usage.as_payload(),
        }

    @property
    def reason(self) -> str:
        if self.admitted:
            return "Machine resource capacity is available."
        if self.unsatisfiable:
            return (
                "Requested machine resources exceed the configured ceiling; "
                f"missing_condition={self.limiting_resource}; "
                "reduce the Host Class or Launch Policy request."
            )
        if self.limiting_resource == LIMITING_RESOURCE_RECONCILIATION:
            return (
                "Waiting for machine capacity reconciliation; "
                f"missing_condition={LIMITING_RESOURCE_RECONCILIATION}; "
                "the container backend inventory is not currently provable."
            )
        if self.limiting_resource == LIMITING_RESOURCE_INITIALIZING:
            return (
                "Waiting for a concurrent initialization permit; "
                f"missing_condition={LIMITING_RESOURCE_INITIALIZING}; "
                f"initializing={self.usage.initializing}; "
                f"permits={self.budget.max_concurrent_initializing}."
            )
        return (
            "Waiting for machine resource capacity; "
            f"missing_condition={self.limiting_resource}; "
            f"utilization_percent={self.utilization_percent}."
        )


def evaluate_resource_admission(
    *,
    demand: ResourceDemand,
    budget: MachineResourceBudget,
    usage: MachineUsage,
    retry_after_seconds: int = 30,
) -> ResourceAdmissionDecision:
    """Return the machine-resource verdict for one launch.

    Order matters. Reconciliation health is checked first: when the ledger and
    the daemon disagree, every count below it is untrustworthy. An impossible
    request is rejected next, so it is never queued behind capacity that would
    not satisfy it anyway. The initialization permit is checked before the
    resource ceilings so a machine with room but no permit reports the limit
    that is actually holding the launch.
    """

    def verdict(
        *, admitted: bool, limiting: str | None, unsatisfiable: bool = False
    ) -> ResourceAdmissionDecision:
        return ResourceAdmissionDecision(
            admitted=admitted,
            limiting_resource=limiting,
            unsatisfiable=unsatisfiable,
            demand=demand,
            usage=usage,
            budget=budget,
            retry_after_seconds=0 if admitted or unsatisfiable else retry_after_seconds,
        )

    if usage.reconciliation_blocked:
        return verdict(admitted=False, limiting=LIMITING_RESOURCE_RECONCILIATION)
    for requested, ceiling, limiting in (
        (demand.cpu_millis, budget.cpu_millis, LIMITING_RESOURCE_CPU),
        (demand.memory_mib, budget.memory_mib, LIMITING_RESOURCE_MEMORY),
        (demand.processes, budget.processes, LIMITING_RESOURCE_PROCESSES),
        (
            demand.temporary_storage_mib,
            budget.temporary_storage_mib,
            LIMITING_RESOURCE_STORAGE,
        ),
    ):
        if requested > ceiling:
            return verdict(admitted=False, limiting=limiting, unsatisfiable=True)
    if usage.initializing >= budget.max_concurrent_initializing:
        return verdict(admitted=False, limiting=LIMITING_RESOURCE_INITIALIZING)
    for requested, reserved, ceiling, limiting in (
        (
            demand.cpu_millis,
            usage.reserved_cpu_millis,
            budget.cpu_millis,
            LIMITING_RESOURCE_CPU,
        ),
        (
            demand.memory_mib,
            usage.reserved_memory_mib,
            budget.memory_mib,
            LIMITING_RESOURCE_MEMORY,
        ),
        (
            demand.processes,
            usage.reserved_processes,
            budget.processes,
            LIMITING_RESOURCE_PROCESSES,
        ),
        (
            demand.temporary_storage_mib,
            usage.reserved_temporary_storage_mib,
            budget.temporary_storage_mib,
            LIMITING_RESOURCE_STORAGE,
        ),
    ):
        if reserved + requested > ceiling:
            return verdict(admitted=False, limiting=limiting)
    return verdict(admitted=True, limiting=None)


@dataclass(frozen=True)
class ReservationRequest:
    """Everything one reservation is bound to.

    The identity here is exact by design: a reservation that did not name its
    backend, owner and generation could be reconciled against the wrong
    container, and a reservation that did not name its plan, Host Class and
    Launch Policy could not be audited back to the policy its demand came from.
    """

    backend_ref: str
    workload_class: str
    owner_kind: str
    owner_ref: str
    demand: ResourceDemand
    generation: int = 1
    plan_ref: str | None = None
    host_class_ref: str | None = None
    launch_policy_ref: str | None = None
    #: The deterministic container identity this launch will create. Both
    #: production callers know it before they mutate Docker, and binding it at
    #: reservation time is what keeps a slow launch out of the clock-reclaim
    #: path: expiry may only reclaim a reservation that never named a consumer
    #: (#3881 remaining implementation 4 and 5).
    container_ref: str | None = None

    def __post_init__(self) -> None:
        if not str(self.backend_ref).strip():
            raise ValueError("backend_ref is required")
        if not str(self.owner_ref).strip():
            raise ValueError("owner_ref is required")
        if not str(self.owner_kind).strip():
            raise ValueError("owner_kind is required")
        if self.workload_class not in COVERED_WORKLOAD_CLASSES:
            raise ValueError(
                f"workload_class {self.workload_class!r} is not a covered "
                "managed launch class"
            )
        if int(self.generation) < 1:
            raise ValueError("generation must be positive")

    @property
    def reservation_id(self) -> str:
        return machine_reservation_id(
            backend_ref=self.backend_ref,
            owner_kind=self.owner_kind,
            owner_ref=self.owner_ref,
            generation=self.generation,
        )


@dataclass(frozen=True)
class ReservationOutcome:
    """The durable result of one reservation attempt."""

    admitted: bool
    reservation_id: str
    state: str
    decision: ResourceAdmissionDecision
    #: True when the attempt found the reservation it already holds. A retry
    #: reconciles with its existing allocation instead of taking a second one.
    reused: bool = False

    @property
    def unsatisfiable(self) -> bool:
        return self.decision.unsatisfiable

    def as_payload(self) -> dict[str, Any]:
        return {
            "reservationId": self.reservation_id,
            "state": self.state,
            "reused": self.reused,
            **self.decision.as_payload(),
        }


@dataclass(frozen=True)
class ReleaseEvidence:
    """Observed consumer state at release time.

    Release is driven by evidence, never by a clock or a caller's assertion
    that it is finished. Existing cleanup services perform the teardown; this
    is only what they observed.
    """

    #: Whether the backend was actually readable when the observation was made.
    daemon_observed: bool
    #: The consumer container no longer exists on the backend.
    consumer_removed: bool = False
    #: The consumer container exists but is proven not running.
    consumer_stopped: bool = False
    #: Volumes or other storage the consumer created are deliberately retained.
    storage_retained: bool = False


def release_state_for(evidence: ReleaseEvidence) -> str | None:
    """Return the state this evidence releases to, or ``None`` for no release.

    ``None`` means the evidence does not prove anything is free. An unreadable
    daemon and a still-running consumer both fall here: releasing either would
    manufacture capacity the machine does not have.
    """

    if not evidence.daemon_observed:
        return None
    if evidence.consumer_removed and not evidence.storage_retained:
        return STATE_RELEASED
    if evidence.consumer_removed or evidence.consumer_stopped:
        # Compute is provably free; retained volumes still consume storage, so
        # storage stays accounted until its own cleanup evidence arrives.
        return STATE_STORAGE_RETAINED
    return None


class MachineCapacityLedger:
    """Durable, backend-scoped machine accounting shared by managed launches.

    Every mutating method is written to be usable inside the caller's existing
    transaction (``*_within``) so counting and reserving are one serialized
    operation. The convenience wrappers open and commit their own transaction
    for callers that own no surrounding one.
    """

    def __init__(self, session_factory: Any | None = None) -> None:
        self._session_factory = session_factory

    def _factory(self) -> Any:
        if self._session_factory is None:
            raise MachineCapacityUnavailable(
                "machine capacity ledger has no database session factory"
            )
        return self._session_factory

    # ------------------------------------------------------------- observation

    async def lock_for_admission(self, session: Any) -> None:
        """Serialize count-and-reserve for the caller's transaction.

        PostgreSQL takes a transaction-scoped advisory lock, so a second
        reserver of *any* covered workload class blocks until the first has
        written its row and is therefore counted. SQLite serializes writes and
        has no advisory locks, so the surrounding transaction already provides
        the guarantee.
        """

        from sqlalchemy import text

        if _dialect_name(session).startswith("postgres"):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": MACHINE_CAPACITY_ADMISSION_LOCK_KEY},
            )

    async def usage_within(
        self,
        session: Any,
        *,
        backend_ref: str,
        now: datetime | None = None,
    ) -> MachineUsage:
        """Return the accounted usage for one backend inside ``session``."""

        from sqlalchemy import select

        from api_service.db.models import MachineCapacityReservation

        observed_at = now or datetime.now(UTC)
        rows = (
            (
                await session.execute(
                    select(MachineCapacityReservation).where(
                        MachineCapacityReservation.backend_ref == backend_ref,
                        MachineCapacityReservation.state.in_(
                            ACCOUNTED_STATES + (STATE_WAITING, STATE_BLOCKED)
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        usage = MachineUsage()
        oldest_waiter: datetime | None = None
        for row in rows:
            state = str(row.state)
            if state == STATE_BLOCKED:
                usage = replace(usage, reconciliation_blocked=True)
                continue
            if state == STATE_WAITING:
                created = _as_aware(row.created_at)
                if created is not None and (
                    oldest_waiter is None or created < oldest_waiter
                ):
                    oldest_waiter = created
                usage = replace(usage, waiting=usage.waiting + 1)
                continue
            if state in COMPUTE_ACCOUNTED_STATES:
                usage = replace(
                    usage,
                    reserved_cpu_millis=(
                        usage.reserved_cpu_millis + int(row.cpu_millis or 0)
                    ),
                    reserved_memory_mib=(
                        usage.reserved_memory_mib + int(row.memory_mib or 0)
                    ),
                    reserved_processes=(
                        usage.reserved_processes + int(row.processes or 0)
                    ),
                )
            if state in STORAGE_ACCOUNTED_STATES:
                usage = replace(
                    usage,
                    reserved_temporary_storage_mib=(
                        usage.reserved_temporary_storage_mib
                        + int(row.temporary_storage_mib or 0)
                    ),
                )
            if state in INITIALIZING_STATES:
                usage = replace(usage, initializing=usage.initializing + 1)
            if (
                state == STATE_ADOPTED
                and str(row.workload_class) == WORKLOAD_CLASS_UNATTRIBUTED
            ):
                # A launch class that was supposed to reserve and did not is a
                # fault. A class the policy accounts by observation is not: it
                # is spending the machine exactly as designed.
                usage = replace(
                    usage, reconciliation_faults=usage.reconciliation_faults + 1
                )
        if oldest_waiter is not None:
            usage = replace(
                usage,
                oldest_waiter_age_seconds=max(
                    0, int((observed_at - oldest_waiter).total_seconds())
                ),
            )
        return usage

    async def usage(
        self, *, backend_ref: str, now: datetime | None = None
    ) -> MachineUsage:
        async with self._factory()() as session:
            return await self.usage_within(session, backend_ref=backend_ref, now=now)

    # ------------------------------------------------------------- reservation

    async def reserve_within(
        self,
        session: Any,
        *,
        request: ReservationRequest,
        budget: MachineResourceBudget,
        now: datetime | None = None,
    ) -> ReservationOutcome:
        """Count and reserve in the caller's transaction.

        The caller must commit this transaction: counting in one transaction
        and reserving in another is precisely the race the shared advisory lock
        exists to close.
        """

        from api_service.db.models import MachineCapacityReservation

        observed_at = now or datetime.now(UTC)
        await self.lock_for_admission(session)
        # Reclaim provably unused expirations first so a crashed worker's
        # abandoned prelaunch reservation does not hold the machine forever.
        await self._expire_within(
            session, backend_ref=request.backend_ref, now=observed_at
        )
        reservation_id = request.reservation_id
        existing = await session.get(MachineCapacityReservation, reservation_id)
        if existing is not None and str(existing.state) in ACCOUNTED_STATES:
            # This attempt already holds its reservation. Reconcile the retry
            # with the existing allocation rather than taking a second one, and
            # extend the bounded prelaunch hand-off so a legitimate retry is
            # not reclaimed underneath itself.
            if str(existing.state) == STATE_PRELAUNCH:
                existing.expires_at = observed_at + timedelta(
                    seconds=budget.prelaunch_ttl_seconds
                )
                if request.container_ref and not str(existing.container_ref or ""):
                    existing.container_ref = str(request.container_ref)
            usage = await self.usage_within(
                session, backend_ref=request.backend_ref, now=observed_at
            )
            return ReservationOutcome(
                admitted=True,
                reservation_id=reservation_id,
                state=str(existing.state),
                reused=True,
                decision=ResourceAdmissionDecision(
                    admitted=True,
                    limiting_resource=None,
                    unsatisfiable=False,
                    demand=request.demand,
                    usage=usage,
                    budget=budget,
                    retry_after_seconds=0,
                ),
            )
        usage = await self.usage_within(
            session, backend_ref=request.backend_ref, now=observed_at
        )
        decision = evaluate_resource_admission(
            demand=request.demand, budget=budget, usage=usage
        )
        if decision.unsatisfiable:
            # Never persist a waiter for a request that can never fit.
            if existing is not None:
                existing.state = STATE_RELEASED
                existing.updated_at = observed_at
            return ReservationOutcome(
                admitted=False,
                reservation_id=reservation_id,
                state=STATE_RELEASED,
                decision=decision,
            )
        state = STATE_PRELAUNCH if decision.admitted else STATE_WAITING
        # An admitted reservation is a bounded prelaunch hand-off; a refused one
        # is a waiter marker that must not outlive the run that abandoned it.
        expires_at = observed_at + timedelta(seconds=budget.prelaunch_ttl_seconds)
        if existing is None:
            session.add(
                MachineCapacityReservation(
                    reservation_id=reservation_id,
                    backend_ref=request.backend_ref,
                    workload_class=request.workload_class,
                    owner_kind=request.owner_kind,
                    owner_ref=request.owner_ref,
                    generation=int(request.generation),
                    state=state,
                    plan_ref=request.plan_ref,
                    host_class_ref=request.host_class_ref,
                    launch_policy_ref=request.launch_policy_ref,
                    cpu_millis=request.demand.cpu_millis,
                    memory_mib=request.demand.memory_mib,
                    processes=request.demand.processes,
                    temporary_storage_mib=request.demand.temporary_storage_mib,
                    container_ref=request.container_ref,
                    created_at=observed_at,
                    expires_at=expires_at,
                    updated_at=observed_at,
                )
            )
        else:
            # A waiter that is now admitted keeps its original ``created_at``
            # so its recorded wait is not reset by the admission that ended it.
            existing.state = state
            existing.workload_class = request.workload_class
            existing.plan_ref = request.plan_ref
            existing.host_class_ref = request.host_class_ref
            existing.launch_policy_ref = request.launch_policy_ref
            existing.cpu_millis = request.demand.cpu_millis
            existing.memory_mib = request.demand.memory_mib
            existing.processes = request.demand.processes
            existing.temporary_storage_mib = request.demand.temporary_storage_mib
            existing.container_ref = request.container_ref
            existing.expires_at = expires_at
            existing.updated_at = observed_at
        await session.flush()
        return ReservationOutcome(
            admitted=decision.admitted,
            reservation_id=reservation_id,
            state=state,
            decision=decision,
        )

    async def reserve(
        self,
        *,
        request: ReservationRequest,
        budget: MachineResourceBudget,
        now: datetime | None = None,
    ) -> ReservationOutcome:
        async with self._factory()() as session:
            outcome = await self.reserve_within(
                session, request=request, budget=budget, now=now
            )
            await session.commit()
            return outcome

    async def verify_within(
        self,
        session: Any,
        *,
        reservation_id: str,
        generation: int,
        now: datetime | None = None,
    ) -> bool:
        """Re-verify the exact reservation fence before a Docker mutation.

        A read-only precheck is advisory. Between it and the container create,
        another worker may have won the machine, the reservation may have
        expired, or a newer generation may have replaced it. Returns ``True``
        only when this exact reservation and generation still hold compute.
        """

        from api_service.db.models import MachineCapacityReservation

        observed_at = now or datetime.now(UTC)
        row = await session.get(MachineCapacityReservation, reservation_id)
        if row is None:
            return False
        if int(row.generation) != int(generation):
            return False
        if str(row.state) not in COMPUTE_ACCOUNTED_STATES:
            return False
        expires_at = _as_aware(row.expires_at)
        if (
            str(row.state) == STATE_PRELAUNCH
            and expires_at is not None
            and expires_at <= observed_at
            and not str(row.container_ref or "")
        ):
            return False
        return True

    async def verify(
        self, *, reservation_id: str, generation: int, now: datetime | None = None
    ) -> bool:
        async with self._factory()() as session:
            return await self.verify_within(
                session,
                reservation_id=reservation_id,
                generation=generation,
                now=now,
            )

    async def confirm(
        self,
        *,
        reservation_id: str,
        generation: int,
        container_ref: str,
        now: datetime | None = None,
    ) -> None:
        """Bind the reservation to the consumer that now exists.

        Once a container carries the reservation, the reservation stops being
        clock-reclaimable: a live consumer keeps its accounting even if the
        workflow that asked for it died.

        A reservation whose accounting was already released while the launch
        was in flight is restored rather than refused. The container provably
        exists at this point, so refusing would fail a successful launch and
        leave a live consumer with no accounting record — strictly worse than
        re-accounting the reservation this exact owner and generation already
        held. The generation fence still refuses a stale attempt.
        """

        from sqlalchemy import update

        from api_service.db.models import MachineCapacityReservation

        observed_at = now or datetime.now(UTC)
        async with self._factory()() as session:
            result = await session.execute(
                update(MachineCapacityReservation)
                .where(
                    MachineCapacityReservation.reservation_id == reservation_id,
                    MachineCapacityReservation.generation == int(generation),
                    MachineCapacityReservation.state.in_(CONFIRMABLE_STATES),
                )
                .values(
                    state=STATE_ACTIVE,
                    container_ref=str(container_ref),
                    expires_at=None,
                    updated_at=observed_at,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise MachineCapacityConflict(
                    "machine reservation fence does not match at confirmation"
                )
            await session.commit()

    async def release(
        self,
        *,
        reservation_id: str,
        generation: int,
        evidence: ReleaseEvidence,
        now: datetime | None = None,
    ) -> str | None:
        """Release what the evidence proves is free, exactly once.

        Returns the state the reservation moved to, or ``None`` when the
        evidence proves nothing and the accounting must be retained.
        """

        from sqlalchemy import update

        from api_service.db.models import MachineCapacityReservation

        target = release_state_for(evidence)
        if target is None:
            return None
        observed_at = now or datetime.now(UTC)
        allowed = ACCOUNTED_STATES
        if target == STATE_STORAGE_RETAINED:
            allowed = COMPUTE_ACCOUNTED_STATES
        async with self._factory()() as session:
            result = await session.execute(
                update(MachineCapacityReservation)
                .where(
                    MachineCapacityReservation.reservation_id == reservation_id,
                    # Exact generation: a cleanup that completes late must never
                    # release the reservation a newer attempt now owns.
                    MachineCapacityReservation.generation == int(generation),
                    MachineCapacityReservation.state.in_(allowed),
                )
                .values(state=target, expires_at=None, updated_at=observed_at)
            )
            await session.commit()
        # rowcount 0 means the release already happened (or the fence moved on);
        # releasing once is the contract, so this is not an error.
        return target if result.rowcount == 1 else None

    # ---------------------------------------------------------- reconciliation

    async def _expire_within(
        self, session: Any, *, backend_ref: str, now: datetime
    ) -> int:
        """Reclaim expired reservations that provably never launched."""

        from sqlalchemy import and_, or_, update

        from api_service.db.models import MachineCapacityReservation

        never_named_a_consumer = or_(
            MachineCapacityReservation.container_ref.is_(None),
            MachineCapacityReservation.container_ref == "",
        )
        result = await session.execute(
            update(MachineCapacityReservation)
            .where(
                MachineCapacityReservation.backend_ref == backend_ref,
                MachineCapacityReservation.expires_at.is_not(None),
                MachineCapacityReservation.expires_at <= now,
                or_(
                    # A waiter reserves nothing, so an abandoned one is only
                    # noise in the oldest-waiter age and always expires.
                    MachineCapacityReservation.state == STATE_WAITING,
                    # Proven no launch: nothing was ever bound to this
                    # reservation. A reservation that named its container
                    # before mutating Docker is never reclaimed by the clock —
                    # the container may already be running, and only daemon
                    # evidence can prove otherwise (see ``reconcile``).
                    and_(
                        MachineCapacityReservation.state == STATE_PRELAUNCH,
                        never_named_a_consumer,
                    ),
                ),
            )
            .values(state=STATE_RELEASED, expires_at=None, updated_at=now)
        )
        return int(result.rowcount or 0)

    async def reconcile(
        self,
        *,
        backend_ref: str,
        inventory: OwnedContainerInventory | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Reconcile durable reservations against owned backend state.

        ``inventory`` reports the containers MoonMind owns on this backend, the
        resources they are actually running with, and *which owner labels were
        enumerated to produce it*. ``None`` means the backend could not be
        read: admission is blocked rather than freed, because an unreadable
        daemon is not an empty machine.

        Absence from the inventory only releases accounting when the
        enumeration covered every owned launch class. A partial enumeration may
        still adopt what it saw — adding accounting is always safe — but it may
        not release anything, because a launch class it never queried would
        look identical to a consumer that vanished.

        Foreign containers are never in this inventory and are never touched:
        this method only writes MoonMind's own accounting rows.
        """

        from sqlalchemy import select

        from api_service.db.models import MachineCapacityReservation

        observed_at = now or datetime.now(UTC)
        async with self._factory()() as session:
            await self.lock_for_admission(session)
            blocked_id = machine_reservation_id(
                backend_ref=backend_ref,
                owner_kind="reconciliation",
                owner_ref=backend_ref,
                generation=1,
            )
            blocked_row = await session.get(MachineCapacityReservation, blocked_id)
            if inventory is None:
                if blocked_row is None:
                    session.add(
                        MachineCapacityReservation(
                            reservation_id=blocked_id,
                            backend_ref=backend_ref,
                            workload_class=WORKLOAD_CLASS_RECONCILIATION,
                            owner_kind="reconciliation",
                            owner_ref=backend_ref,
                            generation=1,
                            state=STATE_BLOCKED,
                            created_at=observed_at,
                            updated_at=observed_at,
                        )
                    )
                else:
                    blocked_row.state = STATE_BLOCKED
                    blocked_row.updated_at = observed_at
                await session.commit()
                return {
                    "backendObserved": False,
                    "scopeComplete": False,
                    "admissionBlocked": True,
                    "expired": 0,
                    "adopted": 0,
                    "observed": 0,
                    "computeReleased": 0,
                    "reconciliationFaults": 0,
                }
            scope_complete = inventory.covers_every_owned_launch_class
            if (
                scope_complete
                and blocked_row is not None
                and str(blocked_row.state) == STATE_BLOCKED
            ):
                # Only a complete enumeration re-establishes the backend. A
                # partial read proves nothing about the classes it skipped.
                blocked_row.state = STATE_RELEASED
                blocked_row.updated_at = observed_at
            expired = await self._expire_within(
                session, backend_ref=backend_ref, now=observed_at
            )
            rows = (
                (
                    await session.execute(
                        select(MachineCapacityReservation).where(
                            MachineCapacityReservation.backend_ref == backend_ref,
                            MachineCapacityReservation.state.in_(
                                COMPUTE_ACCOUNTED_STATES
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            live_containers = inventory.containers
            may_release = scope_complete
            accounted_refs = set()
            compute_released = 0
            for row in rows:
                container_ref = str(row.container_ref or "")
                if not container_ref:
                    # Never named a consumer. The bounded clock path owns it.
                    continue
                accounted_refs.add(container_ref)
                if container_ref in live_containers:
                    continue
                if not may_release:
                    # This enumeration did not cover every owned launch class,
                    # so absence from it proves nothing about this consumer.
                    continue
                if str(row.state) == STATE_PRELAUNCH:
                    expires_at = _as_aware(row.expires_at)
                    if expires_at is None or expires_at > observed_at:
                        # Still inside its bounded launch window: the container
                        # it named may not be running yet, so absence is not
                        # evidence of a vanished consumer. Its initialization
                        # permit follows that actual state.
                        continue
                    # The whole window elapsed and the daemon proves nothing is
                    # running under the name it reserved, so nothing is spent.
                    row.state = STATE_RELEASED
                    row.expires_at = None
                    row.updated_at = observed_at
                    compute_released += 1
                    continue
                # The consumer is gone from the backend, so its compute is
                # provably free. Storage stays accounted until cleanup proves
                # the retained volumes are gone — except for an adopted row,
                # which never owned storage evidence to retain.
                row.state = (
                    STATE_RELEASED
                    if str(row.state) == STATE_ADOPTED
                    else STATE_STORAGE_RETAINED
                )
                row.updated_at = observed_at
                compute_released += 1
            adopted = 0
            observed_rows = 0
            for container_ref, owned in sorted(live_containers.items()):
                if container_ref in accounted_refs:
                    continue
                # An owned live container with no accounting record is never
                # free capacity. A reserving launch class that has no record is
                # a reconciliation fault; a class the documented policy accounts
                # by observation is simply accounted.
                is_fault = owned.launch_class.reserves
                workload_class = (
                    WORKLOAD_CLASS_UNATTRIBUTED if is_fault else WORKLOAD_CLASS_OBSERVED
                )
                demand = owned.demand
                adopted_id = machine_reservation_id(
                    backend_ref=backend_ref,
                    owner_kind="adopted_container",
                    owner_ref=container_ref,
                    generation=1,
                )
                existing = await session.get(MachineCapacityReservation, adopted_id)
                if existing is None:
                    session.add(
                        MachineCapacityReservation(
                            reservation_id=adopted_id,
                            backend_ref=backend_ref,
                            workload_class=workload_class,
                            owner_kind="adopted_container",
                            owner_ref=container_ref,
                            generation=1,
                            state=STATE_ADOPTED,
                            cpu_millis=demand.cpu_millis,
                            memory_mib=demand.memory_mib,
                            processes=demand.processes,
                            temporary_storage_mib=demand.temporary_storage_mib,
                            container_ref=container_ref,
                            created_at=observed_at,
                            updated_at=observed_at,
                        )
                    )
                elif str(existing.state) not in ACCOUNTED_STATES:
                    existing.state = STATE_ADOPTED
                    existing.workload_class = workload_class
                    existing.cpu_millis = demand.cpu_millis
                    existing.memory_mib = demand.memory_mib
                    existing.processes = demand.processes
                    existing.temporary_storage_mib = demand.temporary_storage_mib
                    existing.updated_at = observed_at
                else:
                    continue
                if is_fault:
                    adopted += 1
                else:
                    observed_rows += 1
            await session.commit()
            usage = await self.usage_within(
                session, backend_ref=backend_ref, now=observed_at
            )
        return {
            "backendObserved": True,
            "scopeComplete": may_release,
            "admissionBlocked": usage.reconciliation_blocked,
            "expired": expired,
            "adopted": adopted,
            "observed": observed_rows,
            "computeReleased": compute_released,
            "reconciliationFaults": usage.reconciliation_faults,
        }


def _dialect_name(session: Any) -> str:
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    name = getattr(dialect, "name", "") or ""
    if name:
        return name
    get_bind = getattr(session, "get_bind", None)
    if callable(get_bind):
        try:
            return getattr(get_bind().dialect, "name", "") or ""
        except Exception:
            return ""
    return ""


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


__all__ = [
    "ACCOUNTED_STATES",
    "COMPUTE_ACCOUNTED_STATES",
    "CONFIRMABLE_STATES",
    "COVERED_WORKLOAD_CLASSES",
    "INITIALIZING_STATES",
    "LIMITING_RESOURCE_CPU",
    "LIMITING_RESOURCE_INITIALIZING",
    "LIMITING_RESOURCE_MEMORY",
    "LIMITING_RESOURCE_PROCESSES",
    "LIMITING_RESOURCE_RECONCILIATION",
    "LIMITING_RESOURCE_STORAGE",
    "MACHINE_CAPACITY_ADMISSION_LOCK_KEY",
    "STATE_ACTIVE",
    "STATE_ADOPTED",
    "STATE_BLOCKED",
    "STATE_PRELAUNCH",
    "STATE_RELEASED",
    "STATE_STORAGE_RETAINED",
    "STATE_WAITING",
    "STORAGE_ACCOUNTED_STATES",
    "WORKLOAD_CLASS_CONTAINER_JOB",
    "WORKLOAD_CLASS_GENERIC_HOST",
    "WORKLOAD_CLASS_OBSERVED",
    "WORKLOAD_CLASS_RECONCILIATION",
    "WORKLOAD_CLASS_UNATTRIBUTED",
    "MachineCapacityConflict",
    "MachineCapacityLedger",
    "MachineCapacityUnavailable",
    "MachineResourceBudget",
    "MachineTotals",
    "MachineUsage",
    "ReleaseEvidence",
    "ReservationOutcome",
    "ReservationRequest",
    "ResourceAdmissionDecision",
    "ResourceDemand",
    "evaluate_resource_admission",
    "machine_budget_from_runner",
    "machine_reservation_id",
    "probe_machine_totals",
    "release_state_for",
]
