"""Shared Jules status normalization helpers.

Canonical classification lives in :mod:`moonmind.jules.vocabulary`; this
module preserves the historical snapshot return contract for existing callers
(activity runtime, worker polling) while delegating mapping to the single
reviewed wire map. Blank/None now classifies as ``unknown`` (never a
fabricated queued state).
"""

from __future__ import annotations

from typing import Any

from moonmind.jules.vocabulary import (
    JULES_CANCELED_PROVIDER_STATUSES as _CANCELED,
)
from moonmind.jules.vocabulary import (
    JULES_FAILED_PROVIDER_STATUSES as _FAILED,
)
from moonmind.jules.vocabulary import (
    JULES_SUCCESS_PROVIDER_STATUSES as _SUCCESS,
)
from moonmind.jules.vocabulary import (
    JULES_TERMINAL_FAILURE_PROVIDER_STATUSES as _TERMINAL_FAILURE,
)
from moonmind.jules.vocabulary import (
    JULES_TERMINAL_SUCCESS_PROVIDER_STATUSES as _TERMINAL_SUCCESS,
)
from moonmind.jules.vocabulary import (
    JULES_WIRE_STATUS_MAP,
    JulesNormalizedStatus,
    JulesStatusClassification,
    JulesUnknownStatusError,
    classify_jules_status,
    is_jules_terminal_status,
    require_known_jules_status,
)

JULES_DEFAULT_PROVIDER_STATUS = "pending"
JULES_SUCCESS_PROVIDER_STATUSES = _SUCCESS
JULES_CANCELED_PROVIDER_STATUSES = _CANCELED
JULES_FAILED_PROVIDER_STATUSES = _FAILED
JULES_TERMINAL_SUCCESS_PROVIDER_STATUSES = _TERMINAL_SUCCESS
JULES_TERMINAL_FAILURE_PROVIDER_STATUSES = _TERMINAL_FAILURE

# Back-compat alias: historical snapshot name for the canonical classification.
JulesStatusSnapshot = JulesStatusClassification


def normalize_jules_status(raw_status: Any) -> JulesStatusClassification:
    """Classify one Jules status (display-safe; unknown-tolerant, never raises)."""
    return classify_jules_status(raw_status)


__all__ = [
    "JULES_CANCELED_PROVIDER_STATUSES",
    "JULES_DEFAULT_PROVIDER_STATUS",
    "JULES_FAILED_PROVIDER_STATUSES",
    "JULES_SUCCESS_PROVIDER_STATUSES",
    "JULES_TERMINAL_FAILURE_PROVIDER_STATUSES",
    "JULES_TERMINAL_SUCCESS_PROVIDER_STATUSES",
    "JULES_WIRE_STATUS_MAP",
    "JulesNormalizedStatus",
    "JulesStatusClassification",
    "JulesStatusSnapshot",
    "JulesUnknownStatusError",
    "classify_jules_status",
    "is_jules_terminal_status",
    "normalize_jules_status",
    "require_known_jules_status",
]
