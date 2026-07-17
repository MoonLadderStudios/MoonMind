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

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentBridgeSession, OmnigentBridgeSessionEvent
from moonmind.omnigent.bridge_security import BridgeSessionBinding
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

# Traceability: MM-1152 created the canonical store; MM-1156 moved the
# first-message idempotency state machine onto it from the superseded mapping.
BRIDGE_STORE_TRACEABILITY_ISSUES = ("MM-1152", "MM-1156", "MM-1140")

FIRST_MESSAGE_NOT_PREPARED = "not_prepared"
FIRST_MESSAGE_PREPARED = "prepared"
FIRST_MESSAGE_POSTING = "posting"
FIRST_MESSAGE_POSTED = "posted"
FIRST_MESSAGE_TERMINAL = "terminal"

# MM-1155 (source: MM-1140): durable bridge event journal carried on the
# canonical bridge session row metadata. ``session.created`` (OmnigentBridge.md
# §8.2 step 6, §10.3) is recorded here before the bridge attempts any
# first-message prepare/post.
BRIDGE_EVENT_JOURNAL_KEY = "bridge_event_journal"
SESSION_CREATED_EVENT_TYPE = "session.created"

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
        workflow_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> OmnigentBridgeSession:
        """Create or reuse a durable bridge row keyed by idempotency key.

        ``workflow_id`` / ``agent_run_id`` override the request-derived MoonMind
        identity when the caller already holds a verified binding (the Session
        API Facade validates ownership out-of-band and synthesizes an
        ``AgentExecutionRequest`` with no ``step_execution``). Without them the
        identity is derived from the request, preserving the managed-execution
        path behavior.
        """

        metadata = dict(target_metadata or {})
        resolved_workflow_id = (workflow_id or "").strip() or _workflow_id(request)
        resolved_agent_run_id = (agent_run_id or "").strip() or _agent_run_id(request)
        async with self._session_factory() as session:
            row = await self._get(session, request.idempotency_key)
            if row is None:
                row = OmnigentBridgeSession(
                    bridge_session_id=f"brs_{uuid4().hex}",
                    provider=BRIDGE_PROVIDER,
                    compatibility_profile=BRIDGE_COMPATIBILITY_PROFILE,
                    moonmind_workflow_id=resolved_workflow_id,
                    moonmind_run_id=_run_id(request),
                    moonmind_agent_run_id=resolved_agent_run_id,
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

    async def bind_profile_authorization(
        self,
        *,
        request: AgentExecutionRequest,
        endpoint_ref: str,
        provider_profile_id: str,
        provider_lease_id: str,
        credential_generation: int,
        host_binding_ref: str,
        host_lease_ref: str,
        omnigent_host_id: str | None,
    ) -> OmnigentBridgeSession:
        """Persist lease-authorized routing before provider session creation."""

        await self.get_or_create(
            request=request,
            endpoint_ref=endpoint_ref,
            agent_id=None,
            agent_name=None,
            target_metadata={
                "providerProfileId": provider_profile_id,
                "providerLeaseId": provider_lease_id,
                "credentialGeneration": credential_generation,
                "hostBindingRef": host_binding_ref,
                "hostLeaseRef": host_lease_ref,
                "omnigentHostId": omnigent_host_id,
            },
        )
        async with self._session_factory() as session:
            stored = await self._require(session, request.idempotency_key)
            expected = {
                "provider_profile_id": provider_profile_id,
                "provider_lease_id": provider_lease_id,
                "credential_generation": credential_generation,
                "host_binding_ref": host_binding_ref,
                "host_lease_ref": host_lease_ref,
            }
            for field, value in expected.items():
                current = getattr(stored, field)
                if current is not None and current != value:
                    raise OmnigentIdempotencyError(
                        f"bridge authorization field {field} is already bound"
                    )
                setattr(stored, field, value)
            if stored.omnigent_host_id and omnigent_host_id:
                if stored.omnigent_host_id != omnigent_host_id:
                    raise OmnigentIdempotencyError(
                        "bridge authorization is already bound to another host"
                    )
            elif omnigent_host_id:
                stored.omnigent_host_id = omnigent_host_id
            await session.commit()
            await session.refresh(stored)
            return _detached(session, stored)

    async def record_lifecycle_event(
        self,
        idempotency_key: str,
        *,
        event_type: str,
        code: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OmnigentBridgeSession:
        """Append a bounded, secret-safe pre-stream lifecycle event."""

        from moonmind.utils.logging import redact_sensitive_text

        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            row_metadata = dict(row.metadata_ or {})
            journal = list(row_metadata.get(BRIDGE_EVENT_JOURNAL_KEY) or [])
            entry = {
                "type": str(event_type)[:96],
                "sequence": len(journal) + 1,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
            if code:
                entry["code"] = str(code)[:96]
            if summary:
                entry["summary"] = redact_sensitive_text(summary)[:512]
            if metadata:
                entry["metadata"] = {
                    str(key)[:64]: value
                    for key, value in metadata.items()
                    if key
                    in {
                        "providerProfileId",
                        "providerLeaseId",
                        "credentialGeneration",
                        "hostBindingRef",
                        "hostLeaseRef",
                        "omnigentHostId",
                        "expectedEventClasses",
                        "actualEventClasses",
                        "missingEventClasses",
                        "droppedEventCount",
                    }
                }
            journal.append(entry)
            row_metadata[BRIDGE_EVENT_JOURNAL_KEY] = journal[-100:]
            row.metadata_ = row_metadata
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

    async def get_existing(self, idempotency_key: str) -> OmnigentBridgeSession | None:
        """Return the durable bridge row for an idempotency key, if any.

        Read-only lookup so the Session API Facade can inspect an already
        attached ``omnigent_session_id`` (and its persisted MoonMind owner)
        before resolving the current target or forwarding a provider create.
        Returns ``None`` when no row exists yet for the key.
        """

        key = (idempotency_key or "").strip()
        if not key:
            return None
        async with self._session_factory() as session:
            row = await self._get(session, key)
            if row is None:
                return None
            return _detached(session, row)

    async def get_session_owner(self, session_id: str) -> BridgeSessionBinding | None:
        """Return the MoonMind identity bound to a provider ``session_id``.

        Read-only lookup used to authorize direct session reads at the facade
        boundary (OmnigentBridge.md §16 rule 1) so a caller may only read a
        bridge session owned by a workflow they control. Returns ``None`` when
        no bridge row is bound to the provider session id.
        """

        key = (session_id or "").strip()
        if not key:
            return None
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.omnigent_session_id == key)
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return BridgeSessionBinding(
                workflow_id=row.moonmind_workflow_id,
                agent_run_id=row.moonmind_agent_run_id,
            )

    async def get_session_by_provider_session_id(
        self, session_id: str
    ) -> OmnigentBridgeSession | None:
        """Return the bridge row bound to one provider/host session id."""

        key = (session_id or "").strip()
        if not key:
            return None
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.omnigent_session_id == key)
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return _detached(session, row)

    async def get_bridge_session_owner(
        self, bridge_session_id: str
    ) -> BridgeSessionBinding | None:
        """Return the MoonMind owner for a canonical bridge session id."""

        row = await self.get_bridge_session(bridge_session_id)
        if row is None:
            return None
        return BridgeSessionBinding(
            workflow_id=row.moonmind_workflow_id,
            agent_run_id=row.moonmind_agent_run_id,
        )

    async def get_bridge_session(
        self, bridge_session_id: str
    ) -> OmnigentBridgeSession | None:
        """Return one bridge session by canonical ``bridge_session_id``."""

        key = (bridge_session_id or "").strip()
        if not key:
            return None
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.bridge_session_id == key)
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return _detached(session, row)

    async def resolve_projection_session(
        self,
        *,
        workflow_id: str | None = None,
        agent_run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> OmnigentBridgeSession | None:
        """Resolve the Workflow Chat bridge projection target (§15).

        Resolution prefers an explicit idempotency key when supplied; otherwise
        it returns the latest bridge session for the workflow, optionally scoped
        to the step/agent-run binding.
        """

        key = (idempotency_key or "").strip()
        if key:
            row = await self.get_existing(key)
            if row is not None:
                return row

        workflow = (workflow_id or "").strip()
        if not workflow:
            return None
        agent_run = (agent_run_id or "").strip()
        async with self._session_factory() as session:
            statement = select(OmnigentBridgeSession).where(
                OmnigentBridgeSession.moonmind_workflow_id == workflow
            )
            if agent_run:
                statement = statement.where(
                    OmnigentBridgeSession.moonmind_agent_run_id == agent_run
                )
            statement = statement.order_by(
                OmnigentBridgeSession.updated_at.desc(),
                OmnigentBridgeSession.first_message_post_attempted_at.desc().nulls_last(),
                OmnigentBridgeSession.created_at.desc(),
            ).limit(1)
            result = await session.execute(statement)
            row = result.scalars().first()
            if row is None:
                return None
            return _detached(session, row)

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
            if row.host_lease_ref:
                from api_service.db.models import OmnigentOAuthHostLeaseRecord

                host_lease = await session.get(
                    OmnigentOAuthHostLeaseRecord, row.host_lease_ref
                )
                if host_lease is not None:
                    host_lease.omnigent_session_id = session_id
                    host_lease.bridge_session_id = row.bridge_session_id
                    if row.omnigent_host_id:
                        host_lease.omnigent_host_id = row.omnigent_host_id
            if row.status == STATUS_DECLARED:
                row.status = STATUS_CREATING
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def record_session_created(
        self,
        idempotency_key: str,
        *,
        session_id: str,
        agent_id: str | None = None,
        endpoint_ref: str | None = None,
    ) -> OmnigentBridgeSession:
        """Emit ``session.created`` into the durable bridge event journal.

        MM-1155 (source: MM-1140): proxy-mode Session API Facade persistence
        (OmnigentBridge.md §8.2 step 6). The append is idempotent so a
        create/attach retry under the same idempotency key does not duplicate
        the event; the journal is carried on the canonical bridge session row
        metadata.
        """

        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            metadata = dict(row.metadata_ or {})
            journal = [
                entry
                for entry in (metadata.get(BRIDGE_EVENT_JOURNAL_KEY) or [])
                if isinstance(entry, dict)
            ]
            already_recorded = any(
                entry.get("type") == SESSION_CREATED_EVENT_TYPE for entry in journal
            )
            if not already_recorded:
                journal.append(
                    {
                        "type": SESSION_CREATED_EVENT_TYPE,
                        "sequence": len(journal) + 1,
                        "timestamp": datetime.now(tz=UTC).isoformat(),
                        "omnigentSessionId": session_id,
                        "omnigentAgentId": agent_id,
                        "endpointRef": endpoint_ref,
                    }
                )
                metadata[BRIDGE_EVENT_JOURNAL_KEY] = journal
                row.metadata_ = metadata
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
            row.first_message_item_id = item_id or _string_or_none(
                response.get("item_id")
            )
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
                .where(
                    OmnigentBridgeSessionEvent.bridge_session_id == bridge_session_id
                )
                .order_by(OmnigentBridgeSessionEvent.sequence)
            )
            rows = list(result.scalars().all())
            for row in rows:
                session.expunge(row)
            return rows

    async def append_events(
        self,
        bridge_session_id: str,
        events: Sequence[dict[str, Any]],
    ) -> list[OmnigentBridgeSessionEvent]:
        """Append normalized events to one bridge session's event index.

        Used by the embedded host-facing protocol facade so proxy and embedded
        modes feed the same MoonMind-facing session/event projection (§19.10).
        """

        key = (bridge_session_id or "").strip()
        if not key:
            raise OmnigentIdempotencyError("missing Omnigent bridge session id")
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.bridge_session_id == key)
                .with_for_update()
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                raise OmnigentIdempotencyError("missing Omnigent bridge session row")
            max_sequence_result = await session.execute(
                select(func.max(OmnigentBridgeSessionEvent.sequence)).where(
                    OmnigentBridgeSessionEvent.bridge_session_id == key
                )
            )
            next_sequence = int(max_sequence_result.scalar() or 0) + 1
            prepared_events: list[dict[str, Any]] = []
            for offset, event in enumerate(events):
                prepared = dict(event)
                prepared["sequence"] = next_sequence + offset
                prepared_events.append(prepared)
            rows = _build_event_rows(key, prepared_events)
            for event_row in rows:
                session.add(event_row)
            next_status = None
            for event in prepared_events:
                normalized = _string_or_none(event.get("normalizedStatus"))
                if normalized is None:
                    continue
                coalesced = coalesce_bridge_status(normalized)
                if (
                    next_status in _TERMINAL_STATUSES
                    and coalesced not in _TERMINAL_STATUSES
                ):
                    continue
                next_status = coalesced
            if next_status is not None and not (
                row.status in _TERMINAL_STATUSES
                and next_status not in _TERMINAL_STATUSES
            ):
                row.status = next_status
            await session.commit()
            for event_row in rows:
                await session.refresh(event_row)
                session.expunge(event_row)
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
    "BRIDGE_EVENT_JOURNAL_KEY",
    "BRIDGE_PROVIDER",
    "BRIDGE_STORE_TRACEABILITY_ISSUES",
    "SESSION_CREATED_EVENT_TYPE",
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
