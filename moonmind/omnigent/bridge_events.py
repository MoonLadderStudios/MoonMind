"""Event normalization boundary for the Omnigent bridge.

Owned by MM-1157: this module converts provider stream observations into the
MoonMind-safe ``moonmind.omnigent_bridge.event.v1`` shape and applies the
contract-drift split from ``docs/Omnigent/OmnigentBridge.md`` section 10.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from moonmind.omnigent.bridge_artifacts import OmnigentContractError
from moonmind.omnigent.bridge_security import redact_raw_events
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

BRIDGE_EVENT_SCHEMA_VERSION = "moonmind.omnigent_bridge.event.v1"

_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "canceled",
    "cancelled",
    "timed_out",
    "timeout",
}
_NON_TERMINAL_STATUSES = {
    "created",
    "launching",
    "provisioning",
    "running",
    "waiting",
    "idle",
}
_RECOGNIZED_EXACT_EVENT_TYPES = {
    "",
    "completed",
    "failed",
    "host.capabilities",
    "host.heartbeat",
    "resource.changed_file",
    "resource.session_file",
    "response.completed",
    "response.canceled",
    "response.delta",
    "response.elicitation_request",
    "response.failed",
    "response.timed_out",
    "response.output",
    "session.created",
    "session.final_snapshot",
    "session.started",
    "session.ready",
    "session.reset",
    "session.status",
    "session.cleanup",
    "stream.done",
    "turn.started",
    "turn.interrupted",
    "control.requested",
    "control.completed",
    "control.failed",
    "approval.requested",
    "approval.resolved",
    "resource.published",
}
_RECOGNIZED_EVENT_PREFIXES = (
    "response.output",
    "session.child",
    "session.input",
    "session.item",
)


@dataclass(frozen=True, slots=True)
class BridgeEventNormalization:
    """Normalized bridge event plus optional degraded-drift diagnostic."""

    event: dict[str, Any]
    diagnostic: dict[str, Any] | None = None


def normalize_omnigent_observation(payload: dict[str, Any]) -> str | None:
    """Normalize Omnigent observations.

    Execution-critical contract drift raises ``OmnigentContractError``. Optional
    resource drift is handled by ``build_omnigent_bridge_event`` because that
    path can attach a diagnostic and degrade without changing run status.
    """

    return _normalize_status(payload, allow_optional_resource_drift=False)


def build_omnigent_bridge_event(
    *,
    payload: dict[str, Any],
    sequence: int,
    request: AgentExecutionRequest,
    omnigent_session_id: str | None,
    bridge_session_id: str | None = None,
    source: str = "omnigent_stream",
    source_metadata: dict[str, Any] | None = None,
) -> BridgeEventNormalization:
    """Build the canonical v1 bridge event and index projection fields."""

    event_type = _event_type(payload)
    diagnostic: dict[str, Any] | None = None
    try:
        normalized_status = _normalize_status(
            payload,
            allow_optional_resource_drift=True,
        )
    except _OptionalResourceDrift as exc:
        normalized_status = "running"
        diagnostic = {
            "code": "omnigent_optional_resource_contract_drift",
            "eventType": event_type,
            "message": str(exc),
            "sequence": sequence,
            "severity": "degraded",
        }

    event = {
        "schemaVersion": BRIDGE_EVENT_SCHEMA_VERSION,
        "sequence": sequence,
        "timestamp": _timestamp(payload),
        "bridgeSessionId": str(bridge_session_id or ""),
        "moonmindWorkflowId": _workflow_id(request),
        "moonmindAgentRunId": _agent_run_id(request),
        "direction": _direction(payload),
        "type": event_type,
        "eventType": event_type,
        "normalizedStatus": normalized_status or "running",
        "data": _safe_event_data(payload),
        "artifactRefs": _artifact_refs(payload),
        "metadata": {
            "moonmind": {
                "workflowChatVisible": _workflow_chat_visible(event_type),
                "source": source,
            }
        },
    }
    if omnigent_session_id:
        event["omnigentSessionId"] = omnigent_session_id
    if source_metadata:
        event["metadata"]["moonmind"]["sourceMetadata"] = dict(source_metadata)
    if diagnostic is not None:
        event["metadata"]["moonmind"]["contractDrift"] = diagnostic
    text_preview = _text_preview(payload)
    if text_preview:
        event["textPreview"] = text_preview
    artifact_ref = _first_artifact_ref(event["artifactRefs"])
    if artifact_ref:
        event["artifactRef"] = artifact_ref
    return BridgeEventNormalization(event=event, diagnostic=diagnostic)


class _OptionalResourceDrift(RuntimeError):
    """Raised internally when optional resource drift should degrade."""


def _normalize_status(
    payload: dict[str, Any],
    *,
    allow_optional_resource_drift: bool,
) -> str | None:
    event_type = _event_type(payload)
    if event_type == "stream.done":
        return None
    if event_type in {"response.completed", "completed"}:
        return "completed"
    if event_type in {"response.failed", "failed"}:
        return "failed"
    if event_type == "response.canceled":
        return "canceled"
    if event_type == "response.timed_out":
        return "timed_out"
    if event_type in {"response.elicitation_request", "elicitation_request"}:
        return "awaiting_approval"
    if event_type == "session.created":
        return "created"
    if event_type == "session.started":
        return "running"
    if not _is_recognized_event_type(event_type):
        message = f"Unsupported Omnigent event type: {event_type}"
        if allow_optional_resource_drift and _is_optional_resource_event(event_type):
            raise _OptionalResourceDrift(message)
        raise OmnigentContractError(message)

    status = payload.get("status")
    session = payload.get("session")
    if isinstance(session, dict) and status is None:
        status = session.get("status")
    response = payload.get("response")
    if isinstance(response, dict) and status is None:
        status = response.get("status")
    data = payload.get("data")
    if isinstance(data, dict) and status is None:
        data_response = data.get("response")
        if isinstance(data_response, dict):
            status = data_response.get("status")
    if status is None:
        return None

    raw = str(status).strip().lower()
    if raw in {"cancelled", "timeout"}:
        return {"cancelled": "canceled", "timeout": "timed_out"}[raw]
    if raw in _TERMINAL_STATUSES:
        return raw
    if raw == "waiting":
        return (
            "awaiting_approval"
            if _has_active_elicitation(payload)
            else "intervention_requested"
        )
    if raw in _NON_TERMINAL_STATUSES:
        return raw
    message = f"Unsupported Omnigent status: {raw}"
    if allow_optional_resource_drift and _is_optional_resource_event(event_type):
        raise _OptionalResourceDrift(message)
    raise OmnigentContractError(message)


def _event_type(payload: dict[str, Any]) -> str:
    return str(payload.get("type") or payload.get("eventType") or "").strip()


def _is_recognized_event_type(event_type: str) -> bool:
    return event_type in _RECOGNIZED_EXACT_EVENT_TYPES or event_type.startswith(
        _RECOGNIZED_EVENT_PREFIXES
    )


def _is_optional_resource_event(event_type: str) -> bool:
    return event_type.startswith("resource.")


def _has_active_elicitation(payload: dict[str, Any]) -> bool:
    pending = payload.get("pending_inputs") or payload.get("pendingInputs") or []
    if isinstance(pending, list) and pending:
        return True
    return bool(payload.get("elicitation") or payload.get("elicitation_request"))


def _timestamp(payload: dict[str, Any]) -> str:
    raw = payload.get("timestamp") or payload.get("created_at") or payload.get("createdAt")
    if raw:
        return str(raw)
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _direction(payload: dict[str, Any]) -> str:
    raw = str(payload.get("direction") or "").strip()
    if raw in {"moonmind_to_host", "host_to_moonmind", "system"}:
        return raw
    return "host_to_moonmind"


def _safe_event_data(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_raw_events([payload])[0]
    data = redacted.get("data")
    if isinstance(data, dict):
        return data
    extracted: dict[str, Any] = {}
    for key in ("id", "item_id", "itemId", "pending_id", "pendingId", "status"):
        value = redacted.get(key)
        if value is not None:
            extracted[key] = value
    text = _text_preview(redacted)
    if text:
        extracted["text"] = text
    return extracted


def _artifact_refs(payload: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key, value in payload.items():
        if key.endswith("Ref") or key.endswith("Refs"):
            refs[key] = value
    data = payload.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("Ref") or key.endswith("Refs"):
                refs[key] = value
    return refs


def _first_artifact_ref(refs: dict[str, Any]) -> str | None:
    for value in refs.values():
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item:
                    return item
    return None


def _text_preview(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("text"),
        payload.get("delta"),
        payload.get("message"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("text"), data.get("delta"), data.get("message")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:500]
    return None


def _workflow_chat_visible(event_type: str) -> bool:
    return not event_type.startswith("host.")


def _workflow_id(request: AgentExecutionRequest) -> str:
    if request.step_execution is not None:
        return request.step_execution.workflow_id
    return request.correlation_id


def _agent_run_id(request: AgentExecutionRequest) -> str:
    if request.step_execution is not None:
        return request.step_execution.run_id
    return request.correlation_id


__all__ = [
    "BRIDGE_EVENT_SCHEMA_VERSION",
    "BridgeEventNormalization",
    "build_omnigent_bridge_event",
    "normalize_omnigent_observation",
]
