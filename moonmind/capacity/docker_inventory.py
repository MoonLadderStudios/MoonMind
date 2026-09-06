"""Owned-container inventory for machine capacity reconciliation.

Source issue: MoonLadderStudios/MoonMind#3881 (remaining implementation 6).

Reconciliation needs one answer: which containers MoonMind owns are actually
running on this backend, and with what resources. Only MoonMind's own owner
labels are queried, so a foreign container is never in the result and is
therefore never a candidate for anything this module's callers do. An
unreadable daemon returns ``None`` rather than an empty inventory: "nothing is
running" and "I could not look" must not be the same value.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence

from moonmind.capacity.machine_reservations import ResourceDemand

_MIB = 1024 * 1024

#: The owner labels every MoonMind-managed launch carries. A launch class whose
#: label is missing here would be invisible to reconciliation and would read as
#: free capacity.
OWNED_CONTAINER_LABEL_FILTERS = (
    "moonmind.owner=generic-omnigent-host",
    "moonmind.container_job",
)

_INSPECT_FORMAT = (
    "{{.Name}}\t{{.HostConfig.Memory}}\t{{.HostConfig.NanoCpus}}"
    "\t{{.HostConfig.PidsLimit}}"
)

Runner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]


async def probe_owned_containers(
    runner: Runner,
) -> dict[str, ResourceDemand] | None:
    """Return running MoonMind-owned containers, or ``None`` if unreadable.

    Only running containers are reported: a created-but-unstarted container
    consumes no CPU, memory or processes, so counting it would refuse capacity
    the machine actually has.
    """

    names: set[str] = set()
    for label in OWNED_CONTAINER_LABEL_FILTERS:
        code, stdout, _ = await runner(
            (
                "ps",
                "--filter",
                f"label={label}",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            )
        )
        if code:
            return None
        names.update(
            line.strip()
            for line in stdout.decode(errors="replace").splitlines()
            if line.strip()
        )
    if not names:
        return {}
    code, stdout, _ = await runner(
        ("inspect", "--format", _INSPECT_FORMAT, *sorted(names))
    )
    if code:
        return None
    observed: dict[str, ResourceDemand] = {}
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
        observed[name.strip().lstrip("/")] = ResourceDemand(
            cpu_millis=nano_cpus // 1_000_000,
            memory_mib=(memory_bytes + _MIB - 1) // _MIB,
            processes=pids,
        )
    return observed


__all__ = ["OWNED_CONTAINER_LABEL_FILTERS", "probe_owned_containers"]
