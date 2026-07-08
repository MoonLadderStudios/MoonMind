"""Unified Omnigent bridge failure classification (OB-§17 / DESIGN-REQ-010).

Single reusable source of truth for the ``docs/Omnigent/OmnigentBridge.md`` §17
error-classification table. Every bridge component (session execution, transport
client, request adapter) maps a §17 failure to a canonical MoonMind failure
class through this one classifier instead of maintaining status-driven or
component-local mappings.

Source issue traceability: MM-1140 -> MM-1153.
"""

from __future__ import annotations

from enum import Enum

from moonmind.schemas.agent_runtime_models import FailureClass


class OmnigentFailureReason(str, Enum):
    """Canonical §17 failure rows for the Omnigent bridge."""

    UPSTREAM_UNREACHABLE = "upstream_unreachable"
    HOST_REGISTER_CONNECT = "host_register_connect"
    AUTH_FAILURE = "auth_failure"
    INVALID_SESSION_PAYLOAD = "invalid_session_payload"
    FIRST_MESSAGE_DIGEST_MISMATCH = "first_message_digest_mismatch"
    AMBIGUOUS_POSTING_RECONCILIATION = "ambiguous_posting_reconciliation"
    STREAM_DISCONNECT_ACTIVE = "stream_disconnect_active"
    RUNTIME_HARNESS_FAILURE = "runtime_harness_failure"
    SESSION_HOST_TIMEOUT = "session_host_timeout"
    OPTIONAL_RESOURCE_HARVEST_FAILED = "optional_resource_harvest_failed"
    REQUIRED_ARTIFACT_PERSISTENCE_FAILED = "required_artifact_persistence_failed"


# §17 table -> MoonMind failure class. ``None`` marks the "completed with
# diagnostics" outcome (no failure class) that only escalates when policy
# requires full evidence (see ``classify_omnigent_failure``).
OMNIGENT_FAILURE_CLASS_TABLE: dict[OmnigentFailureReason, FailureClass | None] = {
    OmnigentFailureReason.UPSTREAM_UNREACHABLE: "integration_error",
    OmnigentFailureReason.HOST_REGISTER_CONNECT: "integration_error",
    OmnigentFailureReason.AUTH_FAILURE: "integration_error",
    OmnigentFailureReason.INVALID_SESSION_PAYLOAD: "user_error",
    OmnigentFailureReason.FIRST_MESSAGE_DIGEST_MISMATCH: "user_error",
    OmnigentFailureReason.AMBIGUOUS_POSTING_RECONCILIATION: "integration_error",
    OmnigentFailureReason.STREAM_DISCONNECT_ACTIVE: "integration_error",
    OmnigentFailureReason.RUNTIME_HARNESS_FAILURE: "execution_error",
    OmnigentFailureReason.SESSION_HOST_TIMEOUT: "system_error",
    OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED: None,
    OmnigentFailureReason.REQUIRED_ARTIFACT_PERSISTENCE_FAILED: "system_error",
}


def classify_omnigent_failure(
    reason: OmnigentFailureReason,
    *,
    require_full_evidence: bool = False,
) -> FailureClass | None:
    """Classify one §17 failure row into a MoonMind failure class.

    Returns ``None`` for the "completed with diagnostics" row (optional resource
    harvest failure) unless ``require_full_evidence`` is set, in which case the
    missing evidence escalates to ``system_error`` (MoonMind evidence authority
    could not obtain required evidence). All other rows are a direct §17 lookup.
    """

    if reason is OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED:
        return "system_error" if require_full_evidence else None
    return OMNIGENT_FAILURE_CLASS_TABLE[reason]


def failure_class_for_terminal_status(status: str) -> FailureClass | None:
    """Classify a normalized terminal status via the §17 classifier.

    ``timed_out`` is kept distinct as ``system_error`` and is never collapsed
    into ``failed``. ``canceled`` remains a MoonMind terminal cancel mapped to
    ``system_error``; unexpected statuses default to ``integration_error``.
    """

    if status == "completed":
        return None
    if status == "canceled":
        return "system_error"
    if status == "failed":
        return classify_omnigent_failure(
            OmnigentFailureReason.RUNTIME_HARNESS_FAILURE
        )
    if status == "timed_out":
        return classify_omnigent_failure(
            OmnigentFailureReason.SESSION_HOST_TIMEOUT
        )
    return classify_omnigent_failure(OmnigentFailureReason.UPSTREAM_UNREACHABLE)


def classify_omnigent_http_status(status_code: int) -> FailureClass:
    """Classify an Omnigent HTTP status via the §17 classifier.

    4xx client-input statuses map to the §17 invalid-session-payload row
    (``user_error``); every other status (auth failure, upstream unreachable,
    host register/connect) maps to the §17 integration rows.
    """

    if status_code in {400, 404, 409, 422}:
        return _require_class(
            classify_omnigent_failure(
                OmnigentFailureReason.INVALID_SESSION_PAYLOAD
            )
        )
    return _require_class(
        classify_omnigent_failure(OmnigentFailureReason.UPSTREAM_UNREACHABLE)
    )


def _require_class(value: FailureClass | None) -> FailureClass:
    if value is None:  # pragma: no cover - guarded rows always classify
        raise ValueError("Omnigent §17 row unexpectedly classified to no class")
    return value


__all__ = [
    "OMNIGENT_FAILURE_CLASS_TABLE",
    "OmnigentFailureReason",
    "classify_omnigent_failure",
    "classify_omnigent_http_status",
    "failure_class_for_terminal_status",
]
