"""Provider/integration normalized status domain."""

from __future__ import annotations

from typing import Literal

ProviderNormalizedStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
    "unknown",
]

PROVIDER_NORMALIZED_STATUSES = frozenset(
    {"queued", "running", "completed", "failed", "canceled", "unknown"}
)
