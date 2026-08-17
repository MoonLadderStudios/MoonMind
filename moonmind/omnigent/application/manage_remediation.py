"""Remediation coordinator: pick one capability-derived recovery action.

Per the resilience principle, remediation prefers a single capability-derived
policy over enumerated edge cases. This use case maps a canonical failure class
to one bounded remediation action; the large legacy remediation surface remains
the source of the detailed conformance matrix during the incremental migration.
"""

from __future__ import annotations

from enum import Enum

from moonmind.schemas.agent_runtime_models import FailureClass


class RemediationAction(str, Enum):
    RETRY = "retry"
    REROUTE = "reroute"
    ESCALATE = "escalate"
    NONE = "none"


# One capability-derived mapping from failure class to bounded remediation.
_FAILURE_ACTION: dict[FailureClass, RemediationAction] = {
    "integration_error": RemediationAction.RETRY,
    "system_error": RemediationAction.RETRY,
    "execution_error": RemediationAction.ESCALATE,
    "user_error": RemediationAction.NONE,
}


class ManageRemediation:
    def select(self, failure_class: FailureClass | None) -> RemediationAction:
        if failure_class is None:
            return RemediationAction.NONE
        return _FAILURE_ACTION.get(failure_class, RemediationAction.ESCALATE)


__all__ = ["ManageRemediation", "RemediationAction"]
