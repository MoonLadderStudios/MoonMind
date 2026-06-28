"""MoonMind-owned managed-session observability helper API."""

from __future__ import annotations

import re
from typing import Any, Mapping

from moonmind.schemas.agent_runtime_models import ObservabilityEventKind
from moonmind.schemas.temporal_payload_policy import validate_compact_temporal_mapping
from moonmind.utils.logging import redact_sensitive_payload

from .log_streamer import RuntimeLogStreamer

_SENSITIVE_METADATA_KEY = re.compile(
    r"(token|secret|password|credential|api[_-]?key|auth(?!or(?!iz|is)|entic))",
    re.IGNORECASE,
)
_REFERENCE_PREFIXES = ("env://", "secret://", "vault://", "ref://")


def _redact_observability_metadata(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            str(nested_key): _redact_observability_metadata(
                nested_value,
                key=str(nested_key),
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_observability_metadata(item, key=key) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_observability_metadata(item, key=key) for item in value)
    redacted = redact_sensitive_payload(value, key=key)
    if key is None or not isinstance(redacted, str):
        return redacted
    normalized_key = key.replace("_", "").replace("-", "")
    if not _SENSITIVE_METADATA_KEY.search(normalized_key):
        return redacted
    if redacted.startswith(_REFERENCE_PREFIXES):
        return redacted
    return "[REDACTED]"


class ManagedSessionObservabilityBridge:
    """Emit normalized managed-session observability events through a streamer."""

    def __init__(
        self,
        *,
        log_streamer: RuntimeLogStreamer,
        run_id: str,
        workspace_path: str | None,
        session_id: str | None = None,
        session_epoch: int | None = None,
        container_id: str | None = None,
        thread_id: str | None = None,
        active_turn_id: str | None = None,
    ) -> None:
        self._log_streamer = log_streamer
        self._run_id = run_id
        self._workspace_path = workspace_path
        self._session_id = session_id
        self._session_epoch = session_epoch
        self._container_id = container_id
        self._thread_id = thread_id
        self._active_turn_id = active_turn_id

    @staticmethod
    def safe_metadata(
        metadata: dict[str, Any] | None = None,
        *,
        provider_native_event_name: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(metadata or {})
        provider_name = str(provider_native_event_name or "").strip()
        if provider_name:
            payload["providerNativeEventName"] = provider_name
        redacted = _redact_observability_metadata(payload)
        return validate_compact_temporal_mapping(
            redacted,
            field_name="observability.metadata",
        )

    def emit(
        self,
        *,
        kind: ObservabilityEventKind,
        text: str,
        stream: str = "session",
        offset: int | None = None,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
        preserve_text: bool = False,
    ) -> None:
        self._log_streamer.emit_observability_event(
            run_id=self._run_id,
            workspace_path=self._workspace_path,
            stream=stream,
            text=text,
            kind=kind,
            offset=offset,
            session_id=self._session_id,
            session_epoch=self._session_epoch,
            container_id=self._container_id,
            thread_id=self._thread_id,
            turn_id=turn_id,
            active_turn_id=(
                active_turn_id if active_turn_id is not None else self._active_turn_id
            ),
            metadata=self.safe_metadata(
                metadata,
                provider_native_event_name=provider_native_event_name,
            ),
            preserve_text=preserve_text,
        )

    def session_event(
        self,
        *,
        kind: ObservabilityEventKind,
        text: str,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
    ) -> None:
        self.emit(
            kind=kind,
            text=text,
            turn_id=turn_id,
            active_turn_id=active_turn_id,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
        )

    def turn_event(
        self,
        *,
        kind: ObservabilityEventKind,
        text: str,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
    ) -> None:
        self.emit(
            kind=kind,
            text=text,
            turn_id=turn_id,
            active_turn_id=active_turn_id,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
        )

    def user_message(
        self,
        *,
        text: str,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
    ) -> None:
        self.turn_event(
            kind="user_message_submitted",
            text=text,
            turn_id=turn_id,
            active_turn_id=turn_id,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
        )

    def assistant_message(
        self,
        *,
        text: str,
        kind: ObservabilityEventKind = "assistant_message",
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
        preserve_text: bool = False,
    ) -> None:
        self.emit(
            kind=kind,
            text=text,
            turn_id=turn_id,
            active_turn_id=active_turn_id,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
            preserve_text=preserve_text,
        )

    def tool_event(
        self,
        *,
        kind: ObservabilityEventKind,
        text: str,
        turn_id: str | None = None,
        active_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
    ) -> None:
        self.turn_event(
            kind=kind,
            text=text,
            turn_id=turn_id,
            active_turn_id=active_turn_id,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
        )

    def approval_event(
        self,
        *,
        kind: ObservabilityEventKind,
        text: str,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
    ) -> None:
        self.session_event(
            kind=kind,
            text=text,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
        )

    def reset_event(
        self,
        *,
        text: str,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
    ) -> None:
        self.session_event(
            kind="session_reset_boundary",
            text=text,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
        )

    def termination_event(
        self,
        *,
        text: str,
        metadata: dict[str, Any] | None = None,
        provider_native_event_name: str | None = None,
    ) -> None:
        self.session_event(
            kind="session_terminated",
            text=text,
            metadata=metadata,
            provider_native_event_name=provider_native_event_name,
        )
