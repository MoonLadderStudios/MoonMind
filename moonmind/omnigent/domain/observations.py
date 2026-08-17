"""Canonical Omnigent observation (provider event) vocabulary.

Single source of truth for the recognized provider event-type vocabulary and the
pure event-type -> normalized-status mapping from
``docs/Omnigent/OmnigentBridge.md`` §10. ``bridge_events`` delegates event-type
recognition and the event-type portion of status normalization here so the
recognized vocabulary and transition table are defined exactly once.

This module is pure: it maps provider-native event-type strings (the only place
provider vocabulary is enumerated in the domain) into canonical normalized
statuses owned by :mod:`moonmind.omnigent.domain.session_state`.
"""

from __future__ import annotations

# Exact provider event types the bridge recognizes (§10). Membership here does
# not by itself imply a status mapping; unmapped-but-recognized events fall
# through to payload status inspection at the adapter boundary.
RECOGNIZED_EXACT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "",
        "browser.action_request",
        "completed",
        "failed",
        "host.capabilities",
        "host.heartbeat",
        "injection.consumed",
        "resource.changed_file",
        "resource.session_file",
        "response.cancelled",
        "response.client_task.cancel",
        "response.compaction.completed",
        "response.compaction.failed",
        "response.compaction.in_progress",
        "response.completed",
        "response.created",
        "response.delta",
        "response.elicitation_request",
        "response.elicitation_resolved",
        "response.error",
        "response.failed",
        "response.function_call_output.delta",
        "response.heartbeat",
        "response.in_progress",
        "response.incomplete",
        "response.output",
        "response.policy_denied",
        "response.queued",
        "response.reasoning.started",
        "response.reasoning_summary_text.delta",
        "response.reasoning_text.delta",
        "response.retry",
        "session.agent_changed",
        "session.changed_files.invalidated",
        "session.collaboration_mode",
        "session.created",
        "session.final_snapshot",
        "session.heartbeat",
        "session.interrupted",
        "session.mcp_startup",
        "session.model",
        "session.model_options",
        "session.presence",
        "session.reasoning_effort",
        "session.resource.created",
        "session.resource.deleted",
        "session.sandbox_status",
        "session.skills",
        "session.started",
        "session.status",
        "session.superseded",
        "session.terminal.activity",
        "session.terminal_pending",
        "session.todos",
        "session.usage",
        "stream.done",
        "stream.resume_gap",
        "turn.cancelled",
        "turn.completed",
        "turn.failed",
        "turn.started",
    }
)

RECOGNIZED_EVENT_PREFIXES: tuple[str, ...] = (
    "response.output",
    "session.child",
    "session.input",
    "session.item",
)

# Pure event-type -> normalized-status transition table (§10). Each entry maps a
# provider event type to the canonical normalized status it implies before any
# payload inspection. ``stream.done`` maps to ``None`` (no status change).
_EVENT_TYPE_STATUS: dict[str, str | None] = {
    "stream.done": None,
    "response.completed": "completed",
    "turn.completed": "completed",
    "completed": "completed",
    "response.error": "failed",
    "response.failed": "failed",
    "response.incomplete": "failed",
    "response.policy_denied": "failed",
    "turn.failed": "failed",
    "failed": "failed",
    "response.cancelled": "canceled",
    "session.interrupted": "canceled",
    "session.superseded": "canceled",
    "turn.cancelled": "canceled",
    "response.elicitation_request": "awaiting_approval",
    "elicitation_request": "awaiting_approval",
    "browser.action_request": "intervention_requested",
    "session.created": "created",
    "response.created": "running",
    "response.heartbeat": "running",
    "response.in_progress": "running",
    "response.queued": "running",
    "response.retry": "running",
    "session.heartbeat": "running",
    "session.started": "running",
    "turn.started": "running",
}


def normalized_status_for_event_type(event_type: str) -> tuple[bool, str | None]:
    """Map a provider event type to a normalized status.

    Returns ``(mapped, status)``. ``mapped`` is ``True`` when the event type
    alone determines the normalized status (``status`` may be ``None`` for
    ``stream.done``). ``mapped`` is ``False`` when the event type carries no
    status decision and the caller must inspect the payload status field.
    """

    if event_type in _EVENT_TYPE_STATUS:
        return True, _EVENT_TYPE_STATUS[event_type]
    return False, None


def is_recognized_event_type(event_type: str) -> bool:
    """Return whether the bridge recognizes a provider event type (§10)."""

    return event_type in RECOGNIZED_EXACT_EVENT_TYPES or event_type.startswith(
        RECOGNIZED_EVENT_PREFIXES
    )


def is_optional_resource_event(event_type: str) -> bool:
    """Return whether an event type is an optional (degradable) resource event."""

    return event_type.startswith("resource.")


__all__ = [
    "RECOGNIZED_EVENT_PREFIXES",
    "RECOGNIZED_EXACT_EVENT_TYPES",
    "is_optional_resource_event",
    "is_recognized_event_type",
    "normalized_status_for_event_type",
]
