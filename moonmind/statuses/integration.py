"""Provider/integration-normalized status domain values."""

from __future__ import annotations

import enum
from typing import Literal

ProviderNormalizedStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
    "unknown",
]


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
PROVIDER_NORMALIZED_STATUSES = INTEGRATION_STATUS_VALUES
TERMINAL_INTEGRATION_STATUS_VALUES: frozenset[str] = frozenset(
    {
        IntegrationNormalizedStatus.COMPLETED.value,
        IntegrationNormalizedStatus.FAILED.value,
        IntegrationNormalizedStatus.CANCELED.value,
    }
)


__all__ = [
    "INTEGRATION_STATUS_VALUES",
    "PROVIDER_NORMALIZED_STATUSES",
    "TERMINAL_INTEGRATION_STATUS_VALUES",
    "IntegrationNormalizedStatus",
    "ProviderNormalizedStatus",
]
