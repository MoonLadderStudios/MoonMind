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
    from daemon evidence instead, so the capacity they consume is subtracted
    from what reserving launches may take. They are not faults.

A launch class missing from this registry would be invisible to reconciliation
and its capacity would read as free, which is exactly the defect this registry
exists to make impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Sequence

from moonmind.capacity.machine_reservations import (
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
    OwnedLaunchClass("managed_session", "moonmind.kind=managed-session"),
    OwnedLaunchClass("session_docker_sidecar", "moonmind.kind=session-docker-sidecar"),
    OwnedLaunchClass("workload", "moonmind.kind=workload"),
    OwnedLaunchClass("bounded_service", "moonmind.kind=bounded_service"),
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
    """One running MoonMind-owned container, as its own daemon reports it."""

    demand: ResourceDemand
    launch_class: OwnedLaunchClass


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


_INSPECT_FORMAT = (
    "{{.Name}}\t{{.HostConfig.Memory}}\t{{.HostConfig.NanoCpus}}"
    "\t{{.HostConfig.PidsLimit}}"
)

Runner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]


async def probe_owned_containers(
    runner: Runner,
) -> OwnedContainerInventory | None:
    """Return running MoonMind-owned containers, or ``None`` if unreadable.

    Only running containers are reported: a created-but-unstarted container
    consumes no CPU, memory or processes, so counting it would refuse capacity
    the machine actually has.
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
            memory_bytes = max(0, int(raw_memory.strip() or 0))
            nano_cpus = max(0, int(raw_cpu.strip() or 0))
            pids = max(0, int(raw_pids.strip() or 0))
        except ValueError:
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
                cpu_millis=nano_cpus // 1_000_000,
                memory_mib=(memory_bytes + _MIB - 1) // _MIB,
                processes=pids,
            ),
            launch_class=launch_class,
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
