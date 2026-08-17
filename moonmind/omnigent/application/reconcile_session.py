"""Reconcile a provider observation against canonical session state.

This is the flagship application use case: given a raw provider event payload and
the current persisted session, it uses pure domain policy to compute the next
status, the terminal/failure evidence, and the commands an adapter should run,
then persists the new status under optimistic concurrency.

It depends only on domain policy and the :class:`SessionRepository` port — no
SQLAlchemy, FastAPI, Docker, or provider client is imported here. The composition
root injects a concrete repository adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from moonmind.omnigent.domain.commands import Command, RecordTerminalStatus, ReleaseHost
from moonmind.omnigent.domain.decisions import ReconcileDecision
from moonmind.omnigent.domain.failures import failure_class_for_terminal_status
from moonmind.omnigent.domain.observations import (
    is_optional_resource_event,
    is_recognized_event_type,
    normalized_status_for_event_type,
)
from moonmind.omnigent.domain.session_state import (
    coalesce_session_status,
    is_terminal_status,
)
from moonmind.omnigent.domain.transitions import next_status
from moonmind.omnigent.ports.sessions import (
    SessionRecord,
    SessionRepository,
    SessionRevisionConflict,
)


class UnsupportedObservationError(RuntimeError):
    """Raised when a provider event cannot be reconciled (unrecognized type)."""


@dataclass(frozen=True, slots=True)
class ReconcileSessionResult:
    """Outcome of reconciling one observation."""

    record: SessionRecord
    decision: ReconcileDecision
    changed: bool


class ReconcileSession:
    """Apply one provider observation to a session, terminal-safe."""

    def __init__(self, sessions: SessionRepository) -> None:
        self._sessions = sessions

    def decide(
        self, current_status: str, payload: Mapping[str, Any]
    ) -> ReconcileDecision:
        """Compute the pure reconciliation decision (no persistence)."""

        event_type = str(
            payload.get("type") or payload.get("eventType") or ""
        ).strip()
        mapped, mapped_status = normalized_status_for_event_type(event_type)
        if mapped:
            if mapped_status is None:
                # ``stream.done`` carries no status change.
                return _keep(current_status)
            normalized = mapped_status
        else:
            if not is_recognized_event_type(event_type):
                if is_optional_resource_event(event_type):
                    return _degraded(current_status, event_type)
                raise UnsupportedObservationError(
                    f"Unsupported Omnigent event type: {event_type}"
                )
            status_value = _payload_status(payload)
            if status_value is None:
                return _keep(current_status)
            normalized = coalesce_session_status(status_value)

        target = next_status(current_status, normalized)
        terminal = is_terminal_status(target)
        failure_class = (
            failure_class_for_terminal_status(target) if terminal else None
        )
        commands: tuple[Command, ...] = ()
        if terminal and not is_terminal_status(current_status):
            commands = (
                RecordTerminalStatus(
                    bridge_session_id="", status=target
                ),
                ReleaseHost(bridge_session_id=""),
            )
        return ReconcileDecision(
            next_status=target,
            is_terminal=terminal,
            failure_class=failure_class,
            commands=commands,
        )

    async def reconcile(
        self, bridge_session_id: str, payload: Mapping[str, Any]
    ) -> ReconcileSessionResult:
        """Reconcile ``payload`` into the persisted session, terminal-safe."""

        record = await self._sessions.get(bridge_session_id)
        if record is None:
            raise LookupError(f"Unknown bridge session {bridge_session_id!r}")

        decision = self.decide(record.status, payload)
        # Re-target the placeholder command session ids to this session.
        decision = replace(
            decision,
            commands=tuple(
                _bind(command, bridge_session_id) for command in decision.commands
            ),
        )
        if decision.next_status == record.status:
            return ReconcileSessionResult(record=record, decision=decision, changed=False)

        updated = replace(record, status=decision.next_status)
        try:
            saved = await self._sessions.save(
                updated, expected_revision=record.revision
            )
        except SessionRevisionConflict:
            # A concurrent writer advanced the session; re-read and let the
            # caller retry with fresh state instead of clobbering it.
            raise
        return ReconcileSessionResult(record=saved, decision=decision, changed=True)


def _payload_status(payload: Mapping[str, Any]) -> str | None:
    status = payload.get("status")
    for key in ("session", "response"):
        nested = payload.get(key)
        if isinstance(nested, Mapping) and status is None:
            status = nested.get("status")
    data = payload.get("data")
    if isinstance(data, Mapping) and status is None:
        data_response = data.get("response")
        if isinstance(data_response, Mapping):
            status = data_response.get("status")
    return None if status is None else str(status)


def _keep(current_status: str) -> ReconcileDecision:
    return ReconcileDecision(
        next_status=coalesce_session_status(current_status)
        if not is_terminal_status(current_status)
        else current_status,
        is_terminal=is_terminal_status(current_status),
    )


def _degraded(current_status: str, event_type: str) -> ReconcileDecision:
    kept = _keep(current_status)
    return replace(
        kept,
        diagnostic=f"optional_resource_contract_drift:{event_type}",
    )


def _bind(command: Command, bridge_session_id: str) -> Command:
    if isinstance(command, RecordTerminalStatus):
        return RecordTerminalStatus(
            bridge_session_id=bridge_session_id, status=command.status
        )
    if isinstance(command, ReleaseHost):
        return ReleaseHost(bridge_session_id=bridge_session_id)
    return command


__all__ = [
    "ReconcileSession",
    "ReconcileSessionResult",
    "UnsupportedObservationError",
]
