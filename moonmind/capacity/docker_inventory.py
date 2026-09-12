"""Owned-container inventory for machine capacity reconciliation.

Source issue: MoonLadderStudios/MoonMind#3881 (remaining implementation 1, 6).

Reconciliation needs one answer: which containers MoonMind owns are actually
running on this backend, with what resources, and under which launch class.
Only MoonMind's own owner labels are queried, so a foreign container is never
in the result and is therefore never a candidate for anything this module's
callers do. An unreadable daemon returns ``None`` rather than an empty
inventory: "nothing is running" and "I could not look" must not be the same
value.

The registry below is the deployment's documented resource-class policy. Every
MoonMind-owned container launch class appears in it exactly once, split by how
it is accounted:

``reserving`` launch classes
    Reserve their demand in ``machine_capacity_reservations`` *before* they
    launch and can therefore be refused when the machine is full. A running
    container of a reserving class with no accounting record is a
    reconciliation fault.
``observed`` launch classes
    Credential-authority, session and workload containers that resource
    admission must never refuse — refusing them would break authentication or
    an already-admitted run rather than protect the machine. They are accounted
    from daemon evidence instead, so the limits they declared are subtracted
    from what reserving launches may take. They are not faults.

A launch class missing from this registry would be invisible to reconciliation
and its capacity would read as free, which is exactly the defect this registry
exists to make impossible.

An owned container is accounted from the limits it actually declared. Managed
sessions, session Docker sidecars, unprofiled workloads and OAuth auth runners
declare none, and Docker reports an undeclared ``HostConfig`` limit as a nil
pointer. Two things follow, and both are deliberate:

* An undeclared limit is *unset*, never an unreadable daemon. Discarding a
  whole enumeration because one ordinary container has no ``--pids-limit``
  would report "I could not look" for the common case and block every
  admission on this backend.
* An undeclared limit contributes nothing to that container's accounted
  demand, because there is no bound to subtract. The container is still
  enumerated, still adopted and still never a fault; the deployment's
  documented utilization headroom is what covers what it actually spends.
  ``OwnedContainer.undeclared_limits`` names those resources so the gap is
  reported rather than silently read as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Sequence

from moonmind.capacity.machine_reservations import (
    LIMITING_RESOURCE_CPU,
    LIMITING_RESOURCE_MEMORY,
    LIMITING_RESOURCE_PROCESSES,
    WORKLOAD_CLASS_CONTAINER_JOB,
    WORKLOAD_CLASS_GENERIC_HOST,
    ResourceDemand,
)

_MIB = 1024 * 1024


@dataclass(frozen=True)
class OwnedLaunchClass:
    """One MoonMind-owned container launch class and how it is accounted.

    ``workload_class`` names the reservation class this launch takes before it
    starts. ``None`` means the class is accounted by observation instead; see
    the module docstring for why that distinction exists.
    """

    name: str
    label_selector: str
    workload_class: str | None = None

    @property
    def reserves(self) -> bool:
        return self.workload_class is not None


#: Every MoonMind-owned container launch class the deployment can create.
OWNED_LAUNCH_CLASSES: tuple[OwnedLaunchClass, ...] = (
    OwnedLaunchClass(
        "generic_omnigent_host",
        "moonmind.owner=generic-omnigent-host",
        WORKLOAD_CLASS_GENERIC_HOST,
    ),
    OwnedLaunchClass(
        "container_job",
        "moonmind.container_job",
        WORKLOAD_CLASS_CONTAINER_JOB,
    ),
    OwnedLaunchClass("omnigent_oauth_host", "moonmind.kind=omnigent-oauth-host"),
    OwnedLaunchClass(
        "omnigent_oauth_credential_validator",
        "moonmind.kind=omnigent-oauth-credential-validator",
    ),
    OwnedLaunchClass("oauth_auth_runner", "moonmind.oauth_session=true"),
    OwnedLaunchClass("managed_session", "moonmind.kind=managed-session"),
    OwnedLaunchClass("session_docker_sidecar", "moonmind.kind=session-docker-sidecar"),
    OwnedLaunchClass("workload", "moonmind.kind=workload"),
    OwnedLaunchClass("bounded_service", "moonmind.kind=bounded_service"),
    # A deployment handover is control-plane recovery, not a new workload
    # admission. Observe its inherited Compose resource limits while both
    # releases coexist so these containers cannot read as free capacity.
    OwnedLaunchClass("release_control_plane", "moonmind.release.owner"),
)

#: The owner labels every MoonMind-managed launch carries.
OWNED_CONTAINER_LABEL_FILTERS = tuple(
    launch_class.label_selector for launch_class in OWNED_LAUNCH_CLASSES
)

#: The reservation classes a full enumeration can speak for. Reconciliation may
#: only release accounting for these, and only when the inventory it was given
#: actually enumerated every owned launch class.
RESERVING_WORKLOAD_CLASSES = tuple(
    launch_class.workload_class
    for launch_class in OWNED_LAUNCH_CLASSES
    if launch_class.workload_class is not None
)


@dataclass(frozen=True)
class OwnedContainer:
    """One running MoonMind-owned container, as its own daemon reports it.

    ``demand`` is what the container *declared*, not what it is spending: a
    resource named in ``undeclared_limits`` carries no bound the daemon can
    report, so it contributes nothing here. Keeping the two apart is what lets
    a caller tell an idle container from an unbounded one.
    """

    demand: ResourceDemand
    launch_class: OwnedLaunchClass
    #: Resources this container declared no limit for, from the shared
    #: low-cardinality resource vocabulary.
    undeclared_limits: frozenset[str] = frozenset()

    @property
    def declares_every_limit(self) -> bool:
        return not self.undeclared_limits


@dataclass(frozen=True)
class OwnedContainerInventory:
    """Running owned containers plus the exact scope that was queried.

    The scope travels with the result on purpose. Absence from an inventory is
    only evidence that a consumer is gone when the inventory enumerated that
    consumer's launch class; a partial enumeration that released accounting on
    that basis would free capacity a live container is still spending.
    """

    containers: Mapping[str, OwnedContainer]
    label_selectors: tuple[str, ...]

    @property
    def covers_every_owned_launch_class(self) -> bool:
        return set(OWNED_CONTAINER_LABEL_FILTERS).issubset(set(self.label_selectors))

    @property
    def undeclared_limit_containers(self) -> tuple[str, ...]:
        """Refs whose accounted demand is narrower than what they may spend.

        These containers are enumerated and accounted like any other; they
        simply declared no bound for at least one resource, so the ledger has
        nothing to subtract for it. Reporting them is what keeps that a stated
        limit of the accounting rather than a silent zero.
        """

        return tuple(
            sorted(
                ref for ref, owned in self.containers.items() if owned.undeclared_limits
            )
        )

    def excluding(self, container_ref: str) -> "OwnedContainerInventory":
        """Return this inventory without ``container_ref``, as a partial view.

        A caller that already accounts one container separately may want the
        rest, but the result is no longer evidence of anything: the container it
        omits is still running. Dropping the enumerated scope along with the
        container is what makes that structural — a filtered view can never
        report ``covers_every_owned_launch_class``, so reconciliation can never
        read it as proof that the omitted consumer vanished.

        Removing nothing changes nothing: an inventory that never contained
        ``container_ref`` is still the complete enumeration it already was.
        """

        if container_ref not in self.containers:
            return self
        return OwnedContainerInventory(
            containers={
                ref: owned
                for ref, owned in self.containers.items()
                if ref != container_ref
            },
            label_selectors=(),
        )


_INSPECT_FORMAT = (
    "{{.Name}}\t{{.HostConfig.Memory}}\t{{.HostConfig.NanoCpus}}"
    "\t{{.HostConfig.PidsLimit}}"
)

#: How a daemon renders a ``HostConfig`` limit the container never declared.
#: ``PidsLimit`` is a pointer, so a container created without ``--pids-limit``
#: renders as Go's nil placeholder rather than as a number.
_UNDECLARED_RENDERINGS = frozenset({"", "<no value>", "<nil>"})

Runner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]


class _UnreadableField(Exception):
    """The daemon answered something this module cannot read as a limit."""


def _declared_limit(raw: str) -> int | None:
    """Return the positive limit this field declares, or ``None`` for none.

    Docker expresses "no limit" three interchangeable ways for these fields —
    the nil pointer above, ``0`` and ``-1`` — so all three answer the same
    question the same way: nothing was declared, and there is nothing to
    subtract. Anything else that is not a number is a daemon answer this
    module cannot read, and that must stay distinguishable from an unbounded
    container: one blocks admission, the other is an ordinary container.
    """

    text = raw.strip()
    if text in _UNDECLARED_RENDERINGS:
        return None
    try:
        value = int(text)
    except ValueError as exc:
        raise _UnreadableField(text) from exc
    return value if value > 0 else None


async def probe_owned_containers(
    runner: Runner,
) -> OwnedContainerInventory | None:
    """Return running MoonMind-owned containers, or ``None`` if unreadable.

    Only running containers are reported: a created-but-unstarted container
    consumes no CPU, memory or processes, so counting it would refuse capacity
    the machine actually has.

    A limit the container never declared is read as unset and named in
    ``OwnedContainer.undeclared_limits``; only an answer that is neither a
    number nor Docker's nil rendering makes the enumeration unreadable.
    """

    classified: dict[str, OwnedLaunchClass] = {}
    for launch_class in OWNED_LAUNCH_CLASSES:
        code, stdout, _ = await runner(
            (
                "ps",
                "--filter",
                f"label={launch_class.label_selector}",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            )
        )
        if code:
            return None
        for line in stdout.decode(errors="replace").splitlines():
            name = line.strip()
            # Registry order resolves a container that carries more than one
            # owner label; reserving classes are declared first, so a launch
            # that reserves is never demoted to an observed one.
            if name and name not in classified:
                classified[name] = launch_class
    if not classified:
        return OwnedContainerInventory(
            containers={}, label_selectors=OWNED_CONTAINER_LABEL_FILTERS
        )
    code, stdout, _ = await runner(
        ("inspect", "--format", _INSPECT_FORMAT, *sorted(classified))
    )
    if code:
        return None
    observed: dict[str, OwnedContainer] = {}
    for line in stdout.decode(errors="replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            return None
        name, raw_memory, raw_cpu, raw_pids = parts
        try:
            memory_bytes = _declared_limit(raw_memory)
            nano_cpus = _declared_limit(raw_cpu)
            pids = _declared_limit(raw_pids)
        except _UnreadableField:
            return None
        container_ref = name.strip().lstrip("/")
        launch_class = classified.get(container_ref)
        if launch_class is None:
            # Inspected a container the enumeration did not name. The daemon
            # answered something this inventory cannot classify, so it is not a
            # provable enumeration.
            return None
        observed[container_ref] = OwnedContainer(
            demand=ResourceDemand(
                cpu_millis=(nano_cpus or 0) // 1_000_000,
                memory_mib=((memory_bytes or 0) + _MIB - 1) // _MIB,
                processes=pids or 0,
            ),
            launch_class=launch_class,
            undeclared_limits=frozenset(
                resource
                for resource, declared in (
                    (LIMITING_RESOURCE_CPU, nano_cpus),
                    (LIMITING_RESOURCE_MEMORY, memory_bytes),
                    (LIMITING_RESOURCE_PROCESSES, pids),
                )
                if declared is None
            ),
        )
    return OwnedContainerInventory(
        containers=observed, label_selectors=OWNED_CONTAINER_LABEL_FILTERS
    )


__all__ = [
    "OWNED_CONTAINER_LABEL_FILTERS",
    "OWNED_LAUNCH_CLASSES",
    "RESERVING_WORKLOAD_CLASSES",
    "OwnedContainer",
    "OwnedContainerInventory",
    "OwnedLaunchClass",
    "probe_owned_containers",
]
