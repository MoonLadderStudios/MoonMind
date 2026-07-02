"""Simplified API temporalStatus domain values."""

from __future__ import annotations

import enum
from typing import Literal


class TemporalStatus(str, enum.Enum):
    """Simplified Temporal lifecycle values exposed by execution APIs."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


TemporalStatusValue = Literal["running", "completed", "failed", "canceled"]
TEMPORAL_STATUS_VALUES: frozenset[str] = frozenset(
    item.value for item in TemporalStatus
)


__all__ = ["TEMPORAL_STATUS_VALUES", "TemporalStatus", "TemporalStatusValue"]
