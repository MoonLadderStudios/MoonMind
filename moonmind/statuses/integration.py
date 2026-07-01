"""Provider/integration-normalized status domain values."""

from __future__ import annotations

import enum


class IntegrationNormalizedStatus(str, enum.Enum):
    """Provider-neutral statuses after adapter normalization."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


INTEGRATION_STATUS_VALUES: frozenset[str] = frozenset(
    item.value for item in IntegrationNormalizedStatus
)
TERMINAL_INTEGRATION_STATUS_VALUES: frozenset[str] = frozenset(
    {
        IntegrationNormalizedStatus.COMPLETED.value,
        IntegrationNormalizedStatus.FAILED.value,
        IntegrationNormalizedStatus.CANCELED.value,
    }
)


__all__ = [
    "INTEGRATION_STATUS_VALUES",
    "TERMINAL_INTEGRATION_STATUS_VALUES",
    "IntegrationNormalizedStatus",
]
