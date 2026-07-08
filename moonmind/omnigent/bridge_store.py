"""Canonical Omnigent bridge session store and event index.

Source design: ``docs/Omnigent/OmnigentBridge.md`` §7.1/§7.2 and §17
(MM-1152, source issue MM-1140).

This module is the single durable store for Omnigent bridge sessions,
first-message idempotency, and the session event index. It supersedes the
``omnigent_external_runs`` mapping formerly owned by ``OmnigentRunStore``; there
is no parallel table, alias, or compatibility wrapper.

Session ``status`` is a terminal-safe coalescence of the normalized statuses
produced by :mod:`moonmind.omnigent.execute`: non-terminal normalized statuses
collapse into ``active`` while terminal statuses pass through, keeping
``timed_out`` distinct (``system_error``). The full, non-lossy normalized status
stream is preserved per event on ``omnigent_bridge_session_events``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentBridgeSession, OmnigentBridgeSessionEvent
from moonmind.omnigent.bridge_security import BridgeSessionBinding
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

# Traceability: MM-1152 canonical store, migrated off the MM-1140 source design.
BRIDGE_STORE_TRACEABILITY_ISSUES = ("MM-1152", "MM-1140")

FIRST_MESSAGE_NOT_PREPARED = "not_prepared"
FIRST_MESSAGE_PREPARED = "prepared"
FIRST_MESSAGE_POSTING = "posting"
FIRST_MESSAGE_POSTED = "posted"
FIRST_MESSAGE_TERMINAL = "terminal"

BRIDGE_PROVIDER = "omnigent"
BRIDGE_COMPATIBILITY_PROFILE = "omnigent.server.v1"

# Bridge lifecycle states owned by the bridge before the provider reports a
# normalized status (§7.1). ``active`` is the coalesced non-terminal value.
STATUS_DECLARED = "declared"
STATUS_CREATING = "creating"
STATUS_ACTIVE = "active"

_LIFECYCLE_STATUSES = frozenset({STATUS_DECLARED, STATUS_CREATING, STATUS_ACTIVE})

# Terminal normalized statuses pass straight through to the session status.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "canceled", "timed_out"})

# Non-terminal normalized statuses produced by execute.py; all coalesce to
# ``active`` (§7.1).
_NON_TERMINAL_NORMALIZED_STATUSES = frozenset(
    {
        "created",
        "launching",
        "provisioning",
        "running",
        "waiting",
        "idle",
        "awaiting_approval",
        "intervention_requested",
    }
)

# Provider-native aliases normalized before coalescence, matching execute.py.
_STATUS_ALIASES = {"cancelled": "canceled", "timeout": "timed_out"}


class OmnigentIdempotencyError(RuntimeError):
    """Base error for invalid durable retry state."""


class OmnigentDigestMismatchError(OmnigentIdempotencyError):
    """Raised when an idempotency key is reused for different first-message text."""


def coalesce_bridge_status(value: str) -> str:
    """Coalesce a normalized/lifecycle status into a bridge session status.

    Non-terminal normalized statuses collapse to ``active``; terminal statuses
    pass through unchanged; bridge lifecycle states pass through. ``timed_out``
    is kept distinct from ``failed`` (§7.1/§17). Unknown values fail fast rather
    than silently degrading (repository Compatibility Policy).
    """

    raw = str(value).strip().lower()
    raw = _STATUS_ALIASES.get(raw, raw)
    if raw in _TERMINAL_STATUSES:
        return raw
    if raw in _LIFECYCLE_STATUSES:
        return raw
    if raw in _NON_TERMINAL_NORMALIZED_STATUSES:
        return STATUS_ACTIVE
    raise ValueError(f"Unsupported normalized status for bridge coalescence: {value!r}")


def bridge_failure_class(status: str) -> str | None:
    """Map a terminal bridge status to a MoonMind failure class (§17).

    Mirrors ``moonmind.omnigent.execute._failure_class_for`` so ``timed_out`` and
    ``canceled`` remain ``system_error`` and are never collapsed into ``failed``.
    """

    raw = _STATUS_ALIASES.get(str(status).strip().lower(), str(status).strip().lower())
    if raw == "completed":
        return None
    if raw == "failed":
        return "execution_error"
    if raw in {"canceled", "timed_out"}:
        return "system_error"
    return "integration_error"


class OmnigentBridgeSessionStore:
    """Persistence boundary for canonical Omnigent bridge session rows."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def get_or_create(
        self,
        *,
        request: AgentExecutionRequest,
        endpoint_ref: str,
        agent_id: str | None,
        agent_name: str | None,
        target_metadata: dict[str, Any],
    ) -> OmnigentBridgeSession:
        metadata = dict(target_metadata or {})
        async with self._session_factory() as session:
            row = await self._get(session, request.idempotency_key)
            if row is None:
                row = OmnigentBridgeSession(
                    bridge_session_id=f"brs_{uuid4().hex}",
                    provider=BRIDGE_PROVIDER,
                    compatibility_profile=BRIDGE_COMPATIBILITY_PROFILE,
                    moonmind_workflow_id=_workflow_id(request),
                    moonmind_run_id=_run_id(request),
                    moonmind_agent_run_id=_agent_run_id(request),
                    step_execution_id=_step_execution_id(request),
                    idempotency_key=request.idempotency_key,
                    omnigent_endpoint_ref=endpoint_ref,
                    omnigent_agent_id=agent_id,
                    omnigent_agent_name=agent_name,
                    host_type=str(metadata.get("hostType") or "managed"),
                    workspace=_string_or_none(metadata.get("workspace")),
                    status=STATUS_DECLARED,
                    first_message_state=FIRST_MESSAGE_NOT_PREPARED,
                    terminal_refs={},
                    metadata_=metadata,
                )
                session.add(row)
                await session.commit()
            else:
                changed = False
                if row.omnigent_agent_id is None and agent_id is not None:
                    row.omnigent_agent_id = agent_id
                    changed = True
                if row.omnigent_agent_name is None and agent_name is not None:
                    row.omnigent_agent_name = agent_name
                    changed = True
                if not row.metadata_:
                    row.metadata_ = metadata
                    changed = True
                if changed:
                    await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def get_binding(self, idempotency_key: str) -> BridgeSessionBinding | None:
        """Return the MoonMind identity already bound to a bridge session.

        Read-only lookup used to authorize the bridge session before any
        provider call (OmnigentBridge.md §16 rule 1); returns ``None`` when no
        durable row exists yet for the idempotency key.
        """

        async with self._session_factory() as session:
            row = await self._get(session, idempotency_key)
            if row is None:
                return None
            return BridgeSessionBinding(
                workflow_id=row.moonmind_workflow_id,
                agent_run_id=row.moonmind_agent_run_id,
            )

    async def attach_session(
        self, idempotency_key: str, session_id: str
    ) -> OmnigentBridgeSession:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            if row.omnigent_session_id and row.omnigent_session_id != session_id:
                raise OmnigentIdempotencyError(
                    "idempotency key already maps to a different Omnigent session"
                )
            row.omnigent_session_id = session_id
            if row.status == STATUS_DECLARED:
                row.status = STATUS_CREATING
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def mark_prepared(
        self, idempotency_key: str, *, digest: str, marker: str
    ) -> OmnigentBridgeSession:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            if row.first_message_digest and row.first_message_digest != digest:
                raise OmnigentDigestMismatchError(
                    "idempotencyKey reused with a different first-message digest"
                )
            if row.first_message_state == FIRST_MESSAGE_NOT_PREPARED:
                row.first_message_state = FIRST_MESSAGE_PREPARED
            row.first_message_digest = digest
            row.first_message_marker = marker
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def mark_posting(self, idempotency_key: str) -> OmnigentBridgeSession:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            row.first_message_state = FIRST_MESSAGE_POSTING
            row.first_message_post_attempted_at = datetime.now(tz=UTC)
            if row.status in _LIFECYCLE_STATUSES:
                row.status = STATUS_ACTIVE
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def mark_posted(
        self,
        idempotency_key: str,
        *,
        response: dict[str, Any] | None = None,
        item_id: str | None = None,
    ) -> OmnigentBridgeSession:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            row.first_message_state = FIRST_MESSAGE_POSTED
            row.first_message_posted_at = datetime.now(tz=UTC)
            response = response or {}
            row.first_message_pending_id = _string_or_none(response.get("pending_id"))
            row.first_message_item_id = item_id or _string_or_none(response.get("item_id"))
            if row.status in _LIFECYCLE_STATUSES:
                row.status = STATUS_ACTIVE
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def mark_terminal(
        self,
        idempotency_key: str,
        *,
        status: str,
        terminal_refs: dict[str, Any] | None = None,
        events: Sequence[dict[str, Any]] | None = None,
    ) -> OmnigentBridgeSession:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            row.status = coalesce_bridge_status(status)
            row.first_message_state = FIRST_MESSAGE_TERMINAL
            if terminal_refs:
                row.terminal_refs = terminal_refs
                # Keep the first-class evidence ref columns in sync with the
                # capture bundle instead of leaving them NULL for post-migration
                # rows (§7.1); the JSON ``terminal_refs`` blob is preserved as-is.
                for column, value in _canonical_ref_columns(terminal_refs).items():
                    setattr(row, column, value)
            if events:
                # Terminal event indexing must be idempotent: a Temporal activity
                # retry that reattaches to the durable session can call
                # ``mark_terminal`` again for the same idempotency key. Replace the
                # session's event rows rather than appending, so ``list_events``
                # never returns duplicate sequences (§7.2).
                await session.execute(
                    delete(OmnigentBridgeSessionEvent).where(
                        OmnigentBridgeSessionEvent.bridge_session_id
                        == row.bridge_session_id
                    )
                )
                for event_row in _build_event_rows(row.bridge_session_id, events):
                    session.add(event_row)
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def list_events(
        self, bridge_session_id: str
    ) -> list[OmnigentBridgeSessionEvent]:
        """Return the ordered event index for one bridge session."""

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSessionEvent)
                .where(OmnigentBridgeSessionEvent.bridge_session_id == bridge_session_id)
                .order_by(OmnigentBridgeSessionEvent.sequence)
            )
            rows = list(result.scalars().all())
            for row in rows:
                session.expunge(row)
            return rows

    async def _get(
        self, session: AsyncSession, idempotency_key: str
    ) -> OmnigentBridgeSession | None:
        result = await session.execute(
            select(OmnigentBridgeSession).where(
                OmnigentBridgeSession.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def _require(
        self, session: AsyncSession, idempotency_key: str
    ) -> OmnigentBridgeSession:
        row = await self._get(session, idempotency_key)
        if row is None:
            raise OmnigentIdempotencyError("missing Omnigent bridge session row")
        return row


# Maps each first-class evidence ref column to the capture-bundle ref key that
# supplies it (see ``moonmind.omnigent.execute`` capture bundle metadata refs).
# ``diagnostics_ref`` is sourced from the top-level ``diagnosticsRef`` and is
# handled separately below.
_CANONICAL_REF_COLUMN_SOURCES = {
    "raw_events_ref": "rawSseStreamRef",
    "normalized_events_ref": "normalizedEventStreamRef",
    "initial_snapshot_ref": "initialSnapshotRef",
    "final_snapshot_ref": "finalSnapshotRef",
    "capture_manifest_ref": "captureManifestRef",
    "external_state_ref": "externalStateRef",
}


def _canonical_ref_columns(terminal_refs: dict[str, Any] | None) -> dict[str, str]:
    """Project capture-bundle refs onto the first-class evidence columns (§7.1).

    ``terminal_refs`` carries the capture bundle's ``metadataRefs`` map plus the
    top-level ``diagnosticsRef``. Only non-empty values are returned so a partial
    bundle never clobbers an already-populated column with ``None``.
    """

    if not terminal_refs:
        return {}
    metadata_refs = terminal_refs.get("metadataRefs") or {}
    columns: dict[str, str] = {}
    for column, source_key in _CANONICAL_REF_COLUMN_SOURCES.items():
        value = _string_or_none(metadata_refs.get(source_key))
        if value is not None:
            columns[column] = value
    diagnostics_ref = _string_or_none(terminal_refs.get("diagnosticsRef"))
    if diagnostics_ref is not None:
        columns["diagnostics_ref"] = diagnostics_ref
    return columns


def _build_event_rows(
    bridge_session_id: str, events: Iterable[dict[str, Any]]
) -> list[OmnigentBridgeSessionEvent]:
    now = datetime.now(tz=UTC)
    rows: list[OmnigentBridgeSessionEvent] = []
    for index, event in enumerate(events, start=1):
        sequence = event.get("sequence")
        rows.append(
            OmnigentBridgeSessionEvent(
                event_id=f"bse_{uuid4().hex}",
                bridge_session_id=bridge_session_id,
                sequence=int(sequence) if sequence is not None else index,
                timestamp=now,
                direction=str(event.get("direction") or "host_to_moonmind"),
                event_type=str(event.get("eventType") or event.get("event_type") or ""),
                # Preserve the full, non-lossy normalized status stream (§7.2):
                # do not coalesce here.
                normalized_status=_string_or_none(event.get("normalizedStatus")),
                text_preview=_string_or_none(event.get("textPreview")),
                artifact_ref=_string_or_none(event.get("artifactRef")),
                metadata_=dict(event.get("metadata") or {}),
            )
        )
    return rows


def _workflow_id(request: AgentExecutionRequest) -> str:
    if request.step_execution is not None:
        return request.step_execution.workflow_id
    return request.correlation_id


def _run_id(request: AgentExecutionRequest) -> str | None:
    if request.step_execution is not None:
        return request.step_execution.run_id
    return None


def _agent_run_id(request: AgentExecutionRequest) -> str:
    if request.step_execution is not None:
        return request.step_execution.run_id
    return request.correlation_id


def _step_execution_id(request: AgentExecutionRequest) -> str | None:
    if request.step_execution is not None:
        return _string_or_none(request.step_execution.step_execution_id)
    return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _detached(
    session: AsyncSession, row: OmnigentBridgeSession
) -> OmnigentBridgeSession:
    session.expunge(row)
    return row


__all__ = [
    "BRIDGE_COMPATIBILITY_PROFILE",
    "BRIDGE_PROVIDER",
    "BRIDGE_STORE_TRACEABILITY_ISSUES",
    "FIRST_MESSAGE_NOT_PREPARED",
    "FIRST_MESSAGE_POSTED",
    "FIRST_MESSAGE_POSTING",
    "FIRST_MESSAGE_PREPARED",
    "FIRST_MESSAGE_TERMINAL",
    "STATUS_ACTIVE",
    "STATUS_CREATING",
    "STATUS_DECLARED",
    "OmnigentBridgeSessionStore",
    "OmnigentDigestMismatchError",
    "OmnigentIdempotencyError",
    "bridge_failure_class",
    "coalesce_bridge_status",
]
