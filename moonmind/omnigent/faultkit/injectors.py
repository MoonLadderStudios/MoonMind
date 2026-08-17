"""Host, lease, and infrastructure fault controls with command-window crashes.

Owned by MoonLadderStudios/MoonMind#3709.

The injector is a deterministic decision layer keyed off the declarative
scenario. It never introduces randomness of its own: given the same scenario it
crashes the same windows and surfaces the same named faults, which is what the
"deterministic replay" property requires.

Named infrastructure faults (:class:`InfraFault`) are surfaced to the reconciler
as flags on a command result so the reconciler -- not the injector -- owns every
recovery decision.
"""

from __future__ import annotations

from enum import Enum

from moonmind.omnigent.faultkit.commands import (
    CommandWindow,
    CommandWindowCrash,
    LogicalCommand,
)
from moonmind.omnigent.faultkit.scenario import ScenarioStep


class InfraFault(str, Enum):
    """A named host / lease / infrastructure fault a scenario can inject."""

    # Provider Profile lease
    LEASE_EXPIRED = "lease_expired"
    LEASE_REPLACED = "lease_replaced"
    # Host lease / fencing
    HOST_EXPIRED = "host_expired"
    HOST_REPLACED = "host_replaced"
    # Docker / Compose
    DOCKER_STOP_FAILED = "docker_stop_failed"
    DOCKER_REMOVE_FAILED = "docker_remove_failed"
    # Workspace
    WORKSPACE_RESTORE_FAILED = "workspace_restore_failed"
    WORKSPACE_PUBLISH_FAILED = "workspace_publish_failed"
    # Artifact
    ARTIFACT_WRITE_FAILED = "artifact_write_failed"
    ARTIFACT_READ_FAILED = "artifact_read_failed"
    # Database
    DB_CONFLICT = "db_conflict"
    # Temporal activity
    ACTIVITY_TIMEOUT = "activity_timeout"
    ACTIVITY_CANCELLED = "activity_cancelled"
    ACTIVITY_DELAYED = "activity_delayed"
    # Process restart at a command boundary
    PROCESS_RESTART = "process_restart"


_VALID_FAULT_VALUES = frozenset(fault.value for fault in InfraFault)


def parse_infra_fault(raw: str | None) -> InfraFault | None:
    """Parse a scenario ``fault`` string; unknown faults fail fast."""
    if raw is None:
        return None
    if raw not in _VALID_FAULT_VALUES:
        raise ValueError(f"unknown infrastructure fault {raw!r}")
    return InfraFault(raw)


class FaultInjector:
    """Applies command-window crashes and surfaces named infrastructure faults."""

    def crash_window(self, step: ScenarioStep) -> CommandWindow | None:
        return step.crash_at

    def infra_fault(self, step: ScenarioStep) -> InfraFault | None:
        return parse_infra_fault(step.fault)

    def maybe_crash(
        self, command: LogicalCommand, window: CommandWindow, step: ScenarioStep
    ) -> None:
        """Raise :class:`CommandWindowCrash` if the scenario crashes ``window``."""
        if step.crash_at is window:
            raise CommandWindowCrash(command, window)


__all__ = [
    "InfraFault",
    "FaultInjector",
    "parse_infra_fault",
]
