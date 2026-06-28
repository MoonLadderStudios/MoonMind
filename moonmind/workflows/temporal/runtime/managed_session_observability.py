"""Managed-session observability event bridge.

The bridge is the adapter-boundary translator from runtime-visible Codex
observations to MoonMind's stable managed-session event vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from moonmind.schemas.managed_session_models import CodexManagedSessionRecord


class ManagedSessionEventPublisher(Protocol):
    def emit_session_event(
        self,
        *,
        record: CodexManagedSessionRecord,
        text: str,
        kind: str,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        pass


_NATIVE_EVENT_KIND_MAP = {
    "assistant_message_delta": "assistant_message_delta",
    "assistant_message_completed": "assistant_message_completed",
    "assistant_message": "assistant_message",
    "tool_call_started": "tool_started",
    "tool_call_completed": "tool_completed",
    "approval_requested": "approval_requested",
    "approval_resolved": "approval_resolved",
    "runtime_status": "runtime_status",
    "model_status": "model_status",
}


class ManagedSessionObservabilityBridge:
    """Translate reliable managed-session observations into stable events."""

    def __init__(self, publisher: ManagedSessionEventPublisher) -> None:
        self._publisher = publisher

    def emit_session_available(
        self,
        *,
        record: CodexManagedSessionRecord,
        resumed: bool,
        metadata: Mapping[str, Any] | None = None,
        active_turn_id: str | None = None,
    ) -> None:
        action = "resume_session" if resumed else "start_session"
        kind = "session_resumed" if resumed else "session_started"
        self._publisher.emit_session_event(
            record=record,
            kind=kind,
            text=(
                f"Session {'resumed' if resumed else 'started'}. "
                f"Epoch {record.session_epoch} thread {record.thread_id}."
            ),
            active_turn_id=active_turn_id,
            metadata={"action": action},
        )
        self.emit_runtime_status(
            record=record,
            status="resumed" if resumed else "started",
            metadata={"action": action},
            active_turn_id=active_turn_id,
        )
        model = self._non_blank_string((metadata or {}).get("model"))
        if model is not None:
            self.emit_model_status(
                record=record,
                model=model,
                metadata={"action": action},
                active_turn_id=active_turn_id,
            )

    def emit_user_message_submitted(
        self,
        *,
        record: CodexManagedSessionRecord,
        turn_id: str,
        instructions: str,
        reason: str | None,
    ) -> None:
        metadata: dict[str, object] = {
            "action": "send_turn",
            "messageLength": len(instructions),
        }
        if reason:
            metadata["reason"] = reason
        self._publisher.emit_session_event(
            record=record,
            kind="user_message_submitted",
            text=f"User message submitted for turn {turn_id}.",
            turn_id=turn_id,
            active_turn_id=turn_id,
            metadata=metadata,
        )

    def emit_turn_started(
        self,
        *,
        record: CodexManagedSessionRecord,
        turn_id: str,
        reason: str | None,
        source: str | None = None,
    ) -> None:
        metadata: dict[str, object] = {"action": "send_turn"}
        if reason:
            metadata["reason"] = reason
        if source:
            metadata["source"] = source
        self._publisher.emit_session_event(
            record=record,
            kind="turn_started",
            text=f"Turn started: {turn_id}.",
            turn_id=turn_id,
            active_turn_id=turn_id,
            metadata=metadata,
        )

    def emit_assistant_output(
        self,
        *,
        record: CodexManagedSessionRecord,
        turn_id: str,
        assistant_text: Any,
        reason: str | None,
    ) -> None:
        text = self._non_blank_string(assistant_text)
        if text is None:
            return
        metadata: dict[str, object] = {
            "action": "send_turn",
            "contentLength": len(text),
        }
        if reason:
            metadata["reason"] = reason
        self._publisher.emit_session_event(
            record=record,
            kind="assistant_message",
            text="Assistant message completed.",
            turn_id=turn_id,
            active_turn_id=record.active_turn_id,
            metadata=metadata,
        )
        self._publisher.emit_session_event(
            record=record,
            kind="assistant_message_completed",
            text="Assistant message completed.",
            turn_id=turn_id,
            active_turn_id=record.active_turn_id,
            metadata=metadata,
        )

    def emit_turn_completed(
        self,
        *,
        record: CodexManagedSessionRecord,
        turn_id: str,
        assistant_text: Any,
        reason: str | None,
    ) -> None:
        metadata: dict[str, object] = {"action": "send_turn"}
        if reason:
            metadata["reason"] = reason
        text = self._non_blank_string(assistant_text)
        if text is not None:
            metadata["assistantMessageLength"] = len(text)
        self._publisher.emit_session_event(
            record=record,
            kind="turn_completed",
            text=f"Turn completed: {turn_id}.",
            turn_id=turn_id,
            active_turn_id=record.active_turn_id,
            metadata=metadata,
        )

    def emit_turn_failed(
        self,
        *,
        record: CodexManagedSessionRecord,
        turn_id: str,
        response_metadata: Mapping[str, Any],
        reason: str | None,
    ) -> None:
        metadata: dict[str, object] = {"action": "send_turn"}
        if reason:
            metadata["reason"] = reason
        for key in (
            "failureClass",
            "failureCause",
            "retryRecommendedAction",
            "disposition",
        ):
            value = response_metadata.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                metadata[key] = value
        error = self._non_blank_string(response_metadata.get("reason"))
        if error is not None:
            metadata["error"] = error[:500]
        self._publisher.emit_session_event(
            record=record,
            kind="turn_failed",
            text=f"Turn failed: {turn_id}.",
            turn_id=turn_id,
            active_turn_id=record.active_turn_id,
            metadata=metadata,
        )

    def emit_runtime_status(
        self,
        *,
        record: CodexManagedSessionRecord,
        status: str,
        metadata: Mapping[str, Any] | None = None,
        active_turn_id: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"status": status}
        payload.update(self._compact_metadata(metadata or {}))
        self._publisher.emit_session_event(
            record=record,
            kind="runtime_status",
            text=f"Runtime status: {status}.",
            active_turn_id=active_turn_id,
            metadata=payload,
        )

    def emit_model_status(
        self,
        *,
        record: CodexManagedSessionRecord,
        model: str,
        metadata: Mapping[str, Any] | None = None,
        active_turn_id: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"model": model}
        payload.update(self._compact_metadata(metadata or {}))
        self._publisher.emit_session_event(
            record=record,
            kind="model_status",
            text="Model status available.",
            active_turn_id=active_turn_id,
            metadata=payload,
        )

    def emit_native_observations(
        self,
        *,
        record: CodexManagedSessionRecord,
        observations: Any,
        default_turn_id: str | None = None,
    ) -> None:
        if not isinstance(observations, list):
            return
        for observation in observations:
            if not isinstance(observation, Mapping):
                continue
            native_kind = self._non_blank_string(
                observation.get("kind") or observation.get("type")
            )
            if native_kind is None:
                continue
            stable_kind = _NATIVE_EVENT_KIND_MAP.get(native_kind)
            if stable_kind is None:
                continue
            turn_id = (
                self._non_blank_string(observation.get("turnId")) or default_turn_id
            )
            self._publisher.emit_session_event(
                record=record,
                kind=stable_kind,
                text=(
                    self._non_blank_string(observation.get("text"))
                    or f"{stable_kind.replace('_', ' ').title()}."
                ),
                turn_id=turn_id,
                active_turn_id=(
                    self._non_blank_string(observation.get("activeTurnId"))
                    or record.active_turn_id
                ),
                metadata=self._compact_metadata(
                    observation.get("metadata")
                    if isinstance(observation.get("metadata"), Mapping)
                    else {}
                ),
            )

    @staticmethod
    def _non_blank_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    @staticmethod
    def _compact_metadata(metadata: Mapping[str, Any]) -> dict[str, object]:
        compact: dict[str, object] = {}
        for key, value in metadata.items():
            if not isinstance(key, str) or not key.strip():
                continue
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                compact[key] = value
        return compact
