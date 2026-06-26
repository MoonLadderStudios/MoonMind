"""Mapping helpers for MoonMind schedule policies → Temporal SDK types.

Pure functions — no I/O, no SDK client calls.  These translate the
MoonMind policy vocabulary (overlap mode, catchup mode, jitter, etc.)
into ``temporalio.client`` schedule dataclasses.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from temporalio.client import (
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)

# ---------------------------------------------------------------------------
# Overlap policy
# ---------------------------------------------------------------------------

_OVERLAP_MAP: dict[str, ScheduleOverlapPolicy] = {
    "skip": ScheduleOverlapPolicy.SKIP,
    "allow": ScheduleOverlapPolicy.ALLOW_ALL,
    "buffer_one": ScheduleOverlapPolicy.BUFFER_ONE,
    "cancel_previous": ScheduleOverlapPolicy.CANCEL_OTHER,
}

def map_overlap_policy(mode: str) -> ScheduleOverlapPolicy:
    """Map a MoonMind overlap mode string to a Temporal ``ScheduleOverlapPolicy``.

    Raises ``ValueError`` for unrecognised modes.
    """
    normalised = mode.strip().lower()
    try:
        return _OVERLAP_MAP[normalised]
    except KeyError:
        raise ValueError(
            f"Unknown overlap mode {mode!r}; expected one of {sorted(_OVERLAP_MAP)}"
        ) from None

# ---------------------------------------------------------------------------
# Catchup / backfill window
# ---------------------------------------------------------------------------

_CATCHUP_MAP: dict[str, timedelta] = {
    "none": timedelta(0),
    "last": timedelta(minutes=15),
    "all": timedelta(days=365),
}

def map_catchup_window(mode: str) -> timedelta:
    """Map a MoonMind catchup mode to a ``catchup_window`` timedelta.

    Raises ``ValueError`` for unrecognised modes.
    """
    normalised = mode.strip().lower()
    try:
        return _CATCHUP_MAP[normalised]
    except KeyError:
        raise ValueError(
            f"Unknown catchup mode {mode!r}; expected one of {sorted(_CATCHUP_MAP)}"
        ) from None

# ---------------------------------------------------------------------------
# Composite builders
# ---------------------------------------------------------------------------

def build_schedule_spec(
    cron: str,
    timezone: str = "UTC",
    jitter_seconds: int = 0,
) -> ScheduleSpec:
    """Build a ``ScheduleSpec`` from MoonMind-level inputs."""
    return ScheduleSpec(
        cron_expressions=[cron],
        jitter=timedelta(seconds=max(0, jitter_seconds)),
        time_zone_name=timezone,
    )

def build_schedule_policy(
    overlap_mode: str = "skip",
    catchup_mode: str = "last",
) -> SchedulePolicy:
    """Build a ``SchedulePolicy`` from MoonMind-level inputs."""
    return SchedulePolicy(
        overlap=map_overlap_policy(overlap_mode),
        catchup_window=map_catchup_window(catchup_mode),
    )

def build_schedule_state(
    enabled: bool = True,
    note: str = "",
) -> ScheduleState:
    """Build a ``ScheduleState`` from MoonMind-level inputs."""
    return ScheduleState(
        paused=not enabled,
        note=note,
    )

# ---------------------------------------------------------------------------
# ID conventions
# ---------------------------------------------------------------------------

def make_schedule_id(definition_id: UUID) -> str:
    """Return the Temporal Schedule ID for a recurring task definition."""
    return f"mm-schedule:{definition_id}"

def make_workflow_id_template(definition_id: UUID) -> str:
    """Return a deterministic workflow-ID template for schedule-spawned runs.

    Temporal evaluates ``{{.ScheduleTime}}`` server-side when the
    schedule fires, producing a unique workflow ID per time slot.
    """
    return f"mm:{definition_id}:{{{{.ScheduleTime}}}}"

__all__ = [
    "build_schedule_policy",
    "build_schedule_spec",
    "build_schedule_state",
    "make_schedule_id",
    "make_workflow_id_template",
    "map_catchup_window",
    "map_overlap_policy",
]
