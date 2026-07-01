"""Temporal close-status domain values."""

from __future__ import annotations

import enum


class TemporalExecutionCloseStatus(str, enum.Enum):
    """Terminal Temporal close statuses tracked for invariant checks."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    TERMINATED = "terminated"
    TIMED_OUT = "timed_out"
    CONTINUED_AS_NEW = "continued_as_new"


CLOSE_STATUS_VALUES: frozenset[str] = frozenset(
    item.value for item in TemporalExecutionCloseStatus
)


__all__ = ["CLOSE_STATUS_VALUES", "TemporalExecutionCloseStatus"]
