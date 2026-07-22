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
from typing import Any, NamedTuple
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentBridgeSession, OmnigentBridgeSessionEvent
from moonmind.omnigent.bridge_security import BridgeSessionBinding, redact_raw_events
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.utils.logging import redact_sensitive_payload

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
RESOURCE_HARVEST_COMPLETED_KEY = "resource_harvest_completed_at"
PROVIDER_SESSION_DELETED_KEY = "provider_session_deleted_at"
EMBEDDED_LAUNCH_KEY = "embedded_runner_launch"
EMBEDDED_LIFECYCLE_KEY = "embedded_runner_lifecycle"
EMBEDDED_LIFECYCLE_VERSION = 1

EMBEDDED_RUNNER_STATES = frozenset(
    {
        "launch_reserved",
        "launch_sent",
        "launch_acknowledged",
        "runner_identity_bound",
        "runner_tunnel_waiting",
        "runner_tunnel_ready",
        "first_message_prepared",
        "first_message_posting",
        "first_message_posted",
        "running",
        "draining",
        "stopped",
        "failed",
        "stale",
    }
)

# The lifecycle journal is authority, not an observational log.  Keep the
# transition relation explicit so retries may repeat the current state, while
# stale activities, cross-generation callbacks, and impossible state jumps
# fail before mutating durable evidence.
EMBEDDED_RUNNER_TRANSITIONS: dict[str | None, frozenset[str]] = {
    # ``runner_identity_bound`` is also a valid reconstruction entrypoint for
    # rows created from authenticated upstream evidence during recovery.
    None: frozenset({"launch_reserved", "runner_identity_bound", "failed"}),
    "launch_reserved": frozenset({"launch_sent", "failed", "stale"}),
    "launch_sent": frozenset({"launch_acknowledged", "failed", "stale"}),
    "launch_acknowledged": frozenset({"runner_identity_bound", "failed", "stale"}),
    "runner_identity_bound": frozenset({"runner_tunnel_waiting", "runner_tunnel_ready", "failed", "stale"}),
    "runner_tunnel_waiting": frozenset({"runner_tunnel_ready", "first_message_prepared", "draining", "failed", "stale"}),
    "runner_tunnel_ready": frozenset({"runner_tunnel_waiting", "first_message_prepared", "draining", "failed", "stale"}),
    "first_message_prepared": frozenset({"first_message_posting", "runner_tunnel_waiting", "draining", "failed", "stale"}),
    "first_message_posting": frozenset({"first_message_posted", "runner_tunnel_waiting", "draining", "failed", "stale"}),
    "first_message_posted": frozenset({"running", "runner_tunnel_waiting", "draining", "stopped", "failed", "stale"}),
    "running": frozenset({"runner_tunnel_waiting", "runner_tunnel_ready", "draining", "stopped", "failed", "stale"}),
    "draining": frozenset({"stopped", "failed", "stale"}),
    "stale": frozenset({"draining", "stopped", "failed", "launch_reserved"}),
    "failed": frozenset({"launch_reserved"}),
    "stopped": frozenset(),
}

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


def _advance_embedded_lifecycle(
    row: OmnigentBridgeSession,
    state: str,
    *,
    code: str,
    runner_id: str | None = None,
) -> None:
    """Append secret-free, versioned evidence for one runner transition."""

    if state not in EMBEDDED_RUNNER_STATES:
        raise ValueError(f"unknown embedded runner lifecycle state: {state}")
    now = datetime.now(tz=UTC).isoformat()
    metadata = dict(row.metadata_ or {})
    lifecycle = dict(metadata.get(EMBEDDED_LIFECYCLE_KEY) or {})
    previous_state = lifecycle.get("state")
    if previous_state != state and state not in EMBEDDED_RUNNER_TRANSITIONS.get(
        previous_state, frozenset()
    ):
        raise OmnigentIdempotencyError(
            f"invalid embedded runner lifecycle transition: {previous_state or 'none'} -> {state}"
        )
    attempt = int(lifecycle.get("attempt") or 0)
    if state == "launch_reserved" and lifecycle.get("state") != "launch_reserved":
        attempt += 1
    identity = {
        "hostId": row.omnigent_host_id,
        "runnerId": runner_id or row.omnigent_runner_id,
        "sessionId": row.omnigent_session_id,
        "providerProfileId": row.provider_profile_id,
        "providerLeaseId": row.provider_lease_id,
        "hostBindingRef": row.host_binding_ref,
        "hostLeaseRef": row.host_lease_ref,
        "credentialGeneration": row.credential_generation,
    }
    transition = {
        "state": state,
        "at": now,
        "attempt": attempt,
        "code": code,
        **identity,
    }
    timeline = list(lifecycle.get("timeline") or [])
    if not timeline or any(timeline[-1].get(k) != transition.get(k) for k in ("state", "code")):
        timeline.append(transition)
    lifecycle.update(
        {
            "version": EMBEDDED_LIFECYCLE_VERSION,
            "state": state,
            "updatedAt": now,
            "attempt": attempt,
            "reconciliationCode": code,
            "timeline": timeline[-64:],
            **identity,
        }
    )
    metadata[EMBEDDED_LIFECYCLE_KEY] = lifecycle
    row.metadata_ = metadata


class BridgeProjectionAmbiguousError(RuntimeError):
    """Raised when an explicitly scoped projection resolves to multiple sessions."""


class BridgeEventPage(NamedTuple):
    """One bounded read of the durable event journal."""

    rows: list[OmnigentBridgeSessionEvent]
    has_more: bool
    latest_sequence: int
    earliest_sequence: int | None


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

    async def list_embedded_host_readiness(self) -> list[dict[str, Any]]:
        """Return bounded, non-secret readiness for active embedded host leases."""
        from api_service.db.models import OmnigentOAuthHostLeaseRecord

        now = datetime.now(tz=UTC)
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentOAuthHostLeaseRecord)
                .where(
                    OmnigentOAuthHostLeaseRecord.status.in_(
                        {"starting", "ready", "assigned", "draining"}
                    ),
                    OmnigentOAuthHostLeaseRecord.expires_at > now,
                    OmnigentOAuthHostLeaseRecord.omnigent_host_id.is_not(None),
                )
                .order_by(OmnigentOAuthHostLeaseRecord.acquired_at)
                .limit(250)
            )
            return [
                {
                    "id": row.omnigent_host_id,
                    "status": row.host_readiness or row.status,
                    "ready": (
                        row.disconnected_at is None
                        and (row.host_readiness or row.status) in {"ready", "assigned"}
                    ),
                    "capabilities": dict(row.host_capabilities_json or {}),
                    "disconnected": row.disconnected_at is not None,
                }
                for row in result.scalars().all()
            ]

    async def record_embedded_host_lifecycle(
        self,
        *,
        host_id: str,
        credential_generation: int,
        credential_profile_id: str = "bootstrap-local",
        capabilities: dict[str, Any] | None = None,
        readiness: str | None = None,
        disconnected: bool = False,
    ) -> None:
        """Persist embedded connection state on its exact profile host lease.

        A host must already have been selected by the profile/lease coordinator;
        the embedded protocol is not allowed to create or claim a lease.
        """
        from api_service.db.models import (
            ManagedAgentProviderProfile,
            OmnigentOAuthHostBindingRecord,
            OmnigentOAuthHostLeaseRecord,
        )

        now = datetime.now(tz=UTC)
        async with self._session_factory() as session:
            matched = (
                await session.execute(
                    select(
                        OmnigentOAuthHostLeaseRecord,
                        OmnigentOAuthHostBindingRecord,
                        ManagedAgentProviderProfile,
                    )
                    .join(
                        OmnigentOAuthHostBindingRecord,
                        OmnigentOAuthHostBindingRecord.binding_ref
                        == OmnigentOAuthHostLeaseRecord.binding_ref,
                    )
                    .join(
                        ManagedAgentProviderProfile,
                        ManagedAgentProviderProfile.profile_id
                        == OmnigentOAuthHostLeaseRecord.provider_profile_id,
                    )
                    .where(
                        OmnigentOAuthHostLeaseRecord.omnigent_host_id == host_id,
                        OmnigentOAuthHostLeaseRecord.status.in_(
                            {"starting", "ready", "assigned", "draining"}
                        ),
                        OmnigentOAuthHostLeaseRecord.expires_at > now,
                    )
                )
            ).one_or_none()
            if matched is None:
                raise OmnigentIdempotencyError(
                    "embedded host is not bound to an active profile lease"
                )
            lease, binding, profile = matched
            try:
                lease.validate_binding_generation(binding=binding, profile=profile)
            except ValueError as exc:
                raise OmnigentIdempotencyError(
                    "embedded host credential generation is stale for its profile binding"
                ) from exc
            if lease.host_auth_profile_id not in (None, credential_profile_id):
                raise OmnigentIdempotencyError(
                    "embedded host authentication profile does not match its lease"
                )
            if lease.host_auth_generation not in (None, credential_generation):
                raise OmnigentIdempotencyError(
                    "embedded host authentication generation does not match its lease"
                )
            # The upstream-verified host credential is a separate authority from
            # the Provider Profile OAuth generation above. Bind it only after the
            # preassigned host identity and OAuth lease have both matched.
            lease.host_auth_profile_id = credential_profile_id
            lease.host_auth_generation = credential_generation
            lease.last_heartbeat_at = now
            lease.disconnected_at = now if disconnected else None
            if capabilities is not None:
                safe_capabilities = redact_sensitive_payload(dict(capabilities))
                if not isinstance(safe_capabilities, dict):
                    raise OmnigentIdempotencyError(
                        "invalid embedded host capabilities payload"
                    )
                lease.host_capabilities_json = safe_capabilities
            if readiness is not None:
                lease.host_readiness = readiness[:32]
            await session.commit()

    async def active_host_protocol_modes(
        self, *, exclude_idempotency_key: str | None = None
    ) -> dict[str, int]:
        """Return durable protocol-mode ownership for non-terminal sessions.

        A missing mode is deliberately reported as ``unknown``.  Deployments
        must not switch the host protocol while a legacy/ambiguous active row
        exists because doing so could route its controls to the wrong owner.
        """

        async with self._session_factory() as session:
            query = select(OmnigentBridgeSession.metadata_).where(
                OmnigentBridgeSession.status.not_in(_TERMINAL_STATUSES)
            )
            if exclude_idempotency_key:
                query = query.where(
                    OmnigentBridgeSession.idempotency_key != exclude_idempotency_key
                )
            result = await session.execute(query)
            modes: dict[str, int] = {}
            for metadata in result.scalars().all():
                mode = str((metadata or {}).get("hostProtocolMode") or "").strip()
                key = mode or "unknown"
                modes[key] = modes.get(key, 0) + 1
            return modes

    async def cleanup_required_host_lease_refs(self) -> set[str]:
        """Return active durable host leases whose terminal evidence needs cleanup.

        This is intentionally derived from the canonical bridge rows on every
        janitor pass, so an API restart cannot lose the handoff recorded by an
        authoritative runner-exit frame.
        """

        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    OmnigentBridgeSession.host_lease_ref,
                    OmnigentBridgeSession.terminal_refs,
                ).where(
                    OmnigentBridgeSession.host_lease_ref.is_not(None),
                )
            )
            refs: set[str] = set()
            for host_lease_ref, terminal_refs in result.all():
                if host_lease_ref and (terminal_refs or {}).get(
                    "cleanupState"
                ) in {"runner_exited", "failed"}:
                    refs.add(str(host_lease_ref))
            return refs

    async def record_terminal_cleanup(
        self,
        *,
        host_lease_ref: str,
        completed: bool,
        code: str | None = None,
        summary: str | None = None,
    ) -> OmnigentBridgeSession | None:
        """Persist the authoritative host/Profile lease cleanup outcome.

        The janitor owns the cleanup side effect.  This method projects its
        result back onto the durable bridge contract without making the host
        lease or container name an access authority.
        """

        from moonmind.utils.logging import redact_sensitive_text

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.host_lease_ref == host_lease_ref)
                .order_by(OmnigentBridgeSession.updated_at.desc())
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            safe_summary = redact_sensitive_text(str(summary or ""))[:512]
            safe_code = str(code or "")[:96] or None
            refs = dict(row.terminal_refs or {})
            refs.update(
                {
                    "cleanupState": "completed" if completed else "failed",
                    "leaseReleaseState": "released" if completed else "failed",
                }
            )
            if safe_code:
                refs["cleanupFailureCode"] = safe_code
            else:
                refs.pop("cleanupFailureCode", None)
            row.terminal_refs = refs
            idempotency_key = row.idempotency_key
            await session.commit()
            await session.refresh(row)
            detached = _detached(session, row)

        control_key = f"terminal-cleanup:{host_lease_ref}"
        common_metadata = {
            "actor": "moonmind_janitor",
            "controlType": "terminal_cleanup",
            "controlIdempotencyKey": control_key,
            "sourceMode": "embedded",
        }
        await self.record_lifecycle_event(
            idempotency_key,
            event_type="control",
            event_identity=f"embedded-control:{control_key}:requested",
            summary="Embedded terminal cleanup requested",
            metadata={**common_metadata, "controlOutcome": "requested"},
        )
        await self.record_lifecycle_event(
            idempotency_key,
            event_type="control",
            status="completed" if completed else "failed",
            event_identity=(
                f"embedded-control:{control_key}:"
                f"{'completed' if completed else 'failed'}"
            ),
            code=safe_code,
            summary=safe_summary or (
                "Embedded terminal cleanup completed"
                if completed
                else "Embedded terminal cleanup failed"
            ),
            failure_class=None if completed else "integration_error",
            metadata={
                **common_metadata,
                "controlOutcome": "completed" if completed else "failed",
                "cleanupCompleted": completed,
                "leaseReleased": completed,
            },
        )
        return detached
    async def embedded_reconciliation_host_lease_refs(
        self, *, abandoned_before: datetime
    ) -> dict[str, str]:
        """Return durable embedded generations that require ordered host cleanup.

        The janitor deliberately consumes bridge authority instead of socket
        registries: process-local channels disappear on restart.  Stopping the
        credential-consuming host before releasing its lease is the safe common
        repair for every generation that can no longer be proven recoverable.
        """
        from api_service.db.models import ManagedAgentProviderProfile

        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    OmnigentBridgeSession.host_lease_ref,
                    OmnigentBridgeSession.metadata_,
                    OmnigentBridgeSession.credential_generation,
                    ManagedAgentProviderProfile.credential_generation,
                )
                .outerjoin(
                    ManagedAgentProviderProfile,
                    ManagedAgentProviderProfile.profile_id
                    == OmnigentBridgeSession.provider_profile_id,
                )
                .where(
                    OmnigentBridgeSession.omnigent_endpoint_ref == "embedded",
                    OmnigentBridgeSession.host_lease_ref.is_not(None),
                    OmnigentBridgeSession.status.not_in(_TERMINAL_STATUSES),
                )
            )
            required: dict[str, str] = {}
            for lease_ref, metadata, bound_generation, current_generation in result.all():
                lifecycle = dict((metadata or {}).get(EMBEDDED_LIFECYCLE_KEY) or {})
                state = str(lifecycle.get("state") or "")
                updated_raw = lifecycle.get("updatedAt")
                try:
                    updated_at = datetime.fromisoformat(str(updated_raw))
                except (TypeError, ValueError):
                    updated_at = None
                if (
                    current_generation is not None
                    and bound_generation != current_generation
                ):
                    required[str(lease_ref)] = "credential_generation_cleanup"
                elif state == "launch_acknowledged" and updated_at and updated_at <= abandoned_before:
                    required[str(lease_ref)] = "acknowledgement_without_binding_cleanup"
                elif state in {"runner_identity_bound", "runner_tunnel_waiting"} and updated_at and updated_at <= abandoned_before:
                    required[str(lease_ref)] = "binding_without_tunnel_cleanup"
                elif state == "launch_reserved" and updated_at and updated_at <= abandoned_before:
                    required[str(lease_ref)] = "abandoned_launch_cleanup"
                elif state in {"stopped", "failed", "stale"}:
                    required[str(lease_ref)] = "stale_binding_cleanup"
            return required

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
                merged_metadata = dict(row.metadata_ or {})
                missing_metadata = {
                    key: value
                    for key, value in metadata.items()
                    if key not in merged_metadata
                }
                if missing_metadata:
                    merged_metadata.update(missing_metadata)
                    row.metadata_ = merged_metadata
                    changed = True
                workspace = _string_or_none(metadata.get("workspace"))
                if row.workspace is None and workspace is not None:
                    row.workspace = workspace
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
        effective_launch_snapshot: dict[str, Any] | None = None,
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
                "effectiveLaunchRef": (
                    effective_launch_snapshot.get("snapshotRef")
                    if effective_launch_snapshot
                    else None
                ),
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
            if effective_launch_snapshot and (
                stored.effective_launch_snapshot_json is not None
                and stored.effective_launch_snapshot_json != effective_launch_snapshot
            ):
                raise OmnigentIdempotencyError(
                    "bridge authorization is already bound to another launch snapshot"
                )
            if effective_launch_snapshot:
                stored.effective_launch_snapshot_json = effective_launch_snapshot
            if stored.omnigent_endpoint_ref == "pending":
                stored.omnigent_endpoint_ref = endpoint_ref
            elif stored.omnigent_endpoint_ref != endpoint_ref:
                raise OmnigentIdempotencyError(
                    "bridge authorization is already bound to another endpoint"
                )
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

    async def bind_embedded_runner(
        self, idempotency_key: str, *, host_id: str, runner_id: str
    ) -> OmnigentBridgeSession:
        """Persist the exact launched runner without permitting rebinding.

        This write is the durable handoff between the process-local host tunnel
        and first-message delivery. Retried launch responses may repeat the same
        identity, but can never redirect an existing session.
        """

        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            if row.omnigent_host_id != host_id:
                raise OmnigentIdempotencyError(
                    "embedded runner host does not match durable host assignment"
                )
            if row.omnigent_runner_id and row.omnigent_runner_id != runner_id:
                raise OmnigentIdempotencyError(
                    "embedded session is already bound to another runner"
                )
            lifecycle_state = (
                (row.metadata_ or {}).get(EMBEDDED_LIFECYCLE_KEY) or {}
            ).get("state")
            if row.omnigent_runner_id == runner_id and lifecycle_state in {
                "runner_identity_bound",
                "runner_tunnel_waiting",
                "runner_tunnel_ready",
                "first_message_prepared",
                "first_message_posting",
                "first_message_posted",
                "running",
                "draining",
                "stopped",
                "failed",
                "stale",
            }:
                return _detached(session, row)
            launch = dict((row.metadata_ or {}).get(EMBEDDED_LAUNCH_KEY) or {})
            if launch.get("runnerId") not in {None, runner_id}:
                raise OmnigentIdempotencyError(
                    "embedded runner does not match the reserved launch generation"
                )
            if launch.get("credentialGeneration") not in {
                None, row.credential_generation
            }:
                raise OmnigentIdempotencyError(
                    "embedded runner callback uses a stale credential generation"
                )
            row.omnigent_runner_id = runner_id
            metadata = dict(row.metadata_ or {})
            launch = dict(metadata.get(EMBEDDED_LAUNCH_KEY) or {})
            launch.update(
                {
                    "state": "launched",
                    "hostId": host_id,
                    "runnerId": runner_id,
                    "completedAt": datetime.now(tz=UTC).isoformat(),
                }
            )
            metadata[EMBEDDED_LAUNCH_KEY] = launch
            row.metadata_ = metadata
            _advance_embedded_lifecycle(
                row, "runner_identity_bound", code="runner_identity_bound", runner_id=runner_id
            )
            _advance_embedded_lifecycle(
                row, "runner_tunnel_waiting", code="runner_tunnel_pending", runner_id=runner_id
            )
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def begin_embedded_runner_launch(
        self, idempotency_key: str, *, host_id: str,
        runner_id: str | None = None, generation: int | None = None,
        credential_generation: int | None = None,
        launch_generation: int | None = None,
    ) -> OmnigentBridgeSession:
        """Reserve the one permitted launch before crossing the socket boundary."""

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.idempotency_key == idempotency_key)
                .with_for_update()
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                raise OmnigentIdempotencyError("missing Omnigent bridge session row")
            if row.omnigent_host_id != host_id:
                raise OmnigentIdempotencyError(
                    "embedded launch host does not match durable host assignment"
                )
            launch = dict((row.metadata_ or {}).get(EMBEDDED_LAUNCH_KEY) or {})
            if row.omnigent_runner_id:
                return _detached(session, row)
            if launch.get("state") in {"pending", "launched"}:
                if (
                    launch.get("hostId") == host_id
                    and launch.get("runnerId") == runner_id
                    and launch.get("generation") == generation
                    and launch.get("credentialGeneration") == credential_generation
                    and launch.get("launchGeneration") == launch_generation
                ):
                    return _detached(session, row)
                raise OmnigentIdempotencyError(
                    "embedded runner launch generation requires durable reconciliation"
                )
            metadata = dict(row.metadata_ or {})
            metadata[EMBEDDED_LAUNCH_KEY] = {
                "state": "pending",
                "hostId": host_id,
                "runnerId": runner_id,
                "generation": generation,
                "credentialGeneration": credential_generation,
                "launchGeneration": launch_generation,
                "reservedAt": datetime.now(tz=UTC).isoformat(),
            }
            row.metadata_ = metadata
            _advance_embedded_lifecycle(row, "launch_reserved", code="launch_reserved")
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def fail_embedded_runner_launch(
        self, idempotency_key: str, *, host_id: str
    ) -> OmnigentBridgeSession:
        """Release a launch reservation after the host rejects the side effect."""

        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            if row.omnigent_host_id != host_id:
                raise OmnigentIdempotencyError(
                    "embedded launch host does not match durable host assignment"
                )
            metadata = dict(row.metadata_ or {})
            launch = dict(metadata.get(EMBEDDED_LAUNCH_KEY) or {})
            if launch.get("state") != "pending" or row.omnigent_runner_id:
                raise OmnigentIdempotencyError(
                    "embedded runner launch failure requires durable reconciliation"
                )
            launch.update(
                {
                    "state": "failed",
                    "failedAt": datetime.now(tz=UTC).isoformat(),
                }
            )
            metadata[EMBEDDED_LAUNCH_KEY] = launch
            row.metadata_ = metadata
            _advance_embedded_lifecycle(row, "failed", code="host_launch_rejected")
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def record_embedded_runner_exit(
        self, *, runner_id: str, error: str
    ) -> OmnigentBridgeSession | None:
        """Terminalize a session from the host's authoritative runner exit.

        Runner exit is durable terminal evidence even when the runner never
        managed to emit a terminal session event.  The stable lifecycle event
        makes retries/replayed exit frames idempotent.
        """

        from moonmind.utils.logging import redact_sensitive_text

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.omnigent_runner_id == runner_id)
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            recorded_at = datetime.now(tz=UTC)
            safe_error = redact_sensitive_text(str(error))[:512]
            row.status = "failed"
            row.first_message_state = FIRST_MESSAGE_TERMINAL
            metadata = dict(row.metadata_ or {})
            metadata["embedded_runner_exit"] = {
                "runnerId": runner_id,
                "error": safe_error,
                "recordedAt": recorded_at.isoformat(),
            }
            row.metadata_ = metadata
            _advance_embedded_lifecycle(
                row, "failed", code="embedded_runner_exited", runner_id=runner_id
            )
            terminal_refs = dict(row.terminal_refs or {})
            terminal_refs.update(
                {
                    "cleanupState": "runner_exited",
                    "failureClass": "execution_error",
                    "runnerId": runner_id,
                    "runnerExitSummary": safe_error,
                }
            )
            row.terminal_refs = terminal_refs
            idempotency_key = row.idempotency_key
            await session.commit()
            await session.refresh(row)
            detached = _detached(session, row)
        await self.record_lifecycle_event(
            idempotency_key,
            event_type="terminal",
            status="failed",
            event_identity=f"embedded-runner-exit:{runner_id}",
            code="embedded_runner_exited",
            summary=safe_error or "embedded runner exited without a terminal event",
            failure_class="execution_error",
            metadata={"cleanupCompleted": False, "janitorRequired": True},
        )
        return detached

    async def mark_embedded_runner_state(
        self, idempotency_key: str, *, state: str, code: str
    ) -> OmnigentBridgeSession:
        """Persist an authenticated tunnel/reconciliation transition."""

        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            _advance_embedded_lifecycle(row, state, code=code)
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def prepare_embedded_runner_replacement(
        self, idempotency_key: str, *, runner_id: str
    ) -> OmnigentBridgeSession:
        """Clear a stale assignment only after the host confirmed it stopped.

        Provider and host lease fields intentionally remain untouched; callers
        may launch the replacement before credential capacity is released.
        """

        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            if row.omnigent_runner_id != runner_id:
                raise OmnigentIdempotencyError(
                    "embedded replacement does not match durable runner assignment"
                )
            _advance_embedded_lifecycle(
                row, "draining", code="stale_runner_stop_confirmed", runner_id=runner_id
            )
            _advance_embedded_lifecycle(
                row, "stopped", code="stale_runner_stopped", runner_id=runner_id
            )
            row.omnigent_runner_id = None
            metadata = dict(row.metadata_ or {})
            prior_launch = dict(metadata.get(EMBEDDED_LAUNCH_KEY) or {})
            metadata[EMBEDDED_LAUNCH_KEY] = {
                "state": "failed",
                "hostId": row.omnigent_host_id,
                "credentialGeneration": row.credential_generation,
                "launchGeneration": int(prior_launch.get("launchGeneration") or 1),
                "reconciliationCode": "safe_replacement_required",
                "reconciledAt": datetime.now(tz=UTC).isoformat(),
            }
            # A stopped generation is terminal. Start a fresh lifecycle journal
            # for the replacement while retaining the prior timeline as evidence.
            previous = dict(metadata.get(EMBEDDED_LIFECYCLE_KEY) or {})
            metadata["embedded_runner_lifecycle_previous"] = previous
            metadata.pop(EMBEDDED_LIFECYCLE_KEY, None)
            row.metadata_ = metadata
            await session.commit()
            await session.refresh(row)
            return _detached(session, row)

    async def get_session_by_runner_id(
        self, runner_id: str
    ) -> OmnigentBridgeSession | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.omnigent_runner_id == runner_id)
                .limit(1)
            )
            row = result.scalars().first()
            return _detached(session, row) if row is not None else None

    async def get_active_session_by_runner_identity(
        self, runner_id: str
    ) -> OmnigentBridgeSession | None:
        """Resolve a bound or launch-reserved runner, excluding terminal rows."""

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession).where(
                    OmnigentBridgeSession.omnigent_endpoint_ref == "embedded",
                    OmnigentBridgeSession.status.not_in(_TERMINAL_STATUSES),
                )
            )
            for row in result.scalars():
                launch = dict((row.metadata_ or {}).get(EMBEDDED_LAUNCH_KEY) or {})
                if row.omnigent_runner_id == runner_id or (
                    launch.get("state") in {"pending", "launched"}
                    and launch.get("runnerId") == runner_id
                ):
                    return _detached(session, row)
            return None

    async def record_lifecycle_event(
        self,
        idempotency_key: str,
        *,
        event_type: str,
        status: str = "running",
        event_identity: str | None = None,
        code: str | None = None,
        summary: str | None = None,
        failure_class: str | None = None,
        diagnostics_ref: str | None = None,
        remediation_action: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OmnigentBridgeSession:
        """Append a bounded, secret-safe pre-stream lifecycle event."""

        row, _created = await self._record_lifecycle_event(
            idempotency_key,
            event_type=event_type,
            status=status,
            event_identity=event_identity,
            code=code,
            summary=summary,
            failure_class=failure_class,
            diagnostics_ref=diagnostics_ref,
            remediation_action=remediation_action,
            metadata=metadata,
        )
        return row

    async def claim_lifecycle_event(
        self,
        idempotency_key: str,
        *,
        event_type: str,
        event_identity: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically claim a logical side effect with a durable event.

        The bridge-session row lock serializes competing claimants.  Exactly one
        caller creates the stable event identity and may perform the live side
        effect; later callers must reconcile the already durable control state.
        """

        _row, created = await self._record_lifecycle_event(
            idempotency_key,
            event_type=event_type,
            status="running",
            event_identity=event_identity,
            summary=summary,
            metadata=metadata,
        )
        return created

    async def _record_lifecycle_event(
        self,
        idempotency_key: str,
        *,
        event_type: str,
        status: str = "running",
        event_identity: str | None = None,
        code: str | None = None,
        summary: str | None = None,
        failure_class: str | None = None,
        diagnostics_ref: str | None = None,
        remediation_action: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[OmnigentBridgeSession, bool]:
        """Record one stable lifecycle identity and report whether it was new."""

        from moonmind.utils.logging import redact_sensitive_text

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.idempotency_key == idempotency_key)
                .with_for_update()
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                raise OmnigentIdempotencyError("missing Omnigent bridge session row")
            has_explicit_identity = event_identity is not None
            event_identity = event_identity or (
                f"{event_type}:{code or ''}:{summary or ''}"
            )
            stable_event_id = (
                "bse_"
                + uuid5(NAMESPACE_URL, f"{row.bridge_session_id}:{event_identity}").hex
            )
            existing = await session.get(OmnigentBridgeSessionEvent, stable_event_id)
            if existing is not None:
                return _detached(session, row), False
            max_sequence_result = await session.execute(
                select(func.max(OmnigentBridgeSessionEvent.sequence)).where(
                    OmnigentBridgeSessionEvent.bridge_session_id
                    == row.bridge_session_id
                )
            )
            sequence = int(max_sequence_result.scalar() or 0) + 1
            row_metadata = dict(row.metadata_ or {})
            journal = list(row_metadata.get(BRIDGE_EVENT_JOURNAL_KEY) or [])
            entry = {
                "type": str(event_type)[:96],
                "status": str(status)[:32],
                "sequence": sequence,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
            if code:
                entry["code"] = str(code)[:96]
            if summary:
                entry["summary"] = redact_sensitive_text(summary)[:512]
            if failure_class:
                entry["failureClass"] = str(failure_class)[:64]
            if diagnostics_ref:
                entry["diagnosticsRef"] = str(diagnostics_ref)[:1024]
            if remediation_action:
                entry["remediationAction"] = str(remediation_action)[:96]
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
                        "unexpectedEventClasses",
                        "droppedEventCount",
                        "duplicateEventCount",
                        "reordered",
                        "semanticMismatchCount",
                        "comparisonAvailable",
                        "workflowId",
                        "stepExecutionId",
                        "cleanupCompleted",
                        "leaseReleased",
                        "janitorRequired",
                        "credentialMountPath",
                        "sessionInterrupted",
                        "hostCleanupMode",
                        "stateResourcesCleaned",
                        "hostLeaseReleased",
                        "actor",
                        "controlType",
                        "controlOutcome",
                        "controlId",
                        "controlIdempotencyKey",
                        "expectedSessionId",
                        "expectedHostId",
                        "expectedRunnerId",
                        "expectedTurnState",
                        "sourceMode",
                        "controlKey",
                        "captureManifestRef",
                        "resourceProjectionRef",
                        "evidenceCompleteness",
                    }
                }
            journal.append(entry)
            row_metadata[BRIDGE_EVENT_JOURNAL_KEY] = journal[-100:]
            row.metadata_ = row_metadata
            safe_summary = redact_sensitive_text(summary or "")[:512] or None
            event_metadata = dict(entry)
            event_metadata["eventIdentity"] = str(event_identity)[:255]
            event_metadata["moonmind"] = {
                "workflowChatVisible": False,
                "source": "bridge_lifecycle",
            }
            session.add(
                OmnigentBridgeSessionEvent(
                    event_id=stable_event_id,
                    bridge_session_id=row.bridge_session_id,
                    sequence=sequence,
                    deduplication_key=f"lifecycle:{event_identity}"[:128],
                    timestamp=datetime.now(tz=UTC),
                    direction="moonmind_system",
                    event_type=(
                        f"lifecycle.{str(event_type)[:86]}"
                        if has_explicit_identity
                        else str(event_type)[:96]
                    ),
                    normalized_status=("waiting" if status == "waiting" else None),
                    text_preview=safe_summary,
                    artifact_ref=(
                        str(diagnostics_ref)[:1024] if diagnostics_ref else None
                    ),
                    metadata_=event_metadata,
                )
            )
            if event_type == "terminal" and status in _TERMINAL_STATUSES:
                row.status = coalesce_bridge_status(status)
                row.first_message_state = FIRST_MESSAGE_TERMINAL
            await session.commit()
            await session.refresh(row)
            return _detached(session, row), True

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
        run_id: str | None = None,
        step_execution_id: str | None = None,
        agent_run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> OmnigentBridgeSession | None:
        """Resolve the Workflow Chat bridge projection target (§15).

        Explicit AgentRun/step identity has precedence over workflow run/step
        scope, which has precedence over an explicit idempotency key. Only an
        unscoped workflow lookup selects the latest eligible session.
        """
        workflow = (workflow_id or "").strip()
        run = (run_id or "").strip()
        step = (step_execution_id or "").strip()
        agent_run = (agent_run_id or "").strip()
        key = (idempotency_key or "").strip()
        scoped_filters: list[Any] = []
        if agent_run or step:
            if agent_run:
                scoped_filters.append(
                    OmnigentBridgeSession.moonmind_agent_run_id == agent_run
                )
            if step:
                scoped_filters.append(OmnigentBridgeSession.step_execution_id == step)
            if workflow:
                scoped_filters.append(
                    OmnigentBridgeSession.moonmind_workflow_id == workflow
                )
            if run:
                scoped_filters.append(OmnigentBridgeSession.moonmind_run_id == run)
        elif workflow and run:
            scoped_filters.extend(
                (
                    OmnigentBridgeSession.moonmind_workflow_id == workflow,
                    OmnigentBridgeSession.moonmind_run_id == run,
                )
            )
        if scoped_filters:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(OmnigentBridgeSession).where(*scoped_filters).limit(2)
                )
                rows = list(result.scalars().all())
                if len(rows) > 1:
                    raise BridgeProjectionAmbiguousError(
                        "Multiple bridge sessions match the explicit projection scope"
                    )
                return _detached(session, rows[0]) if rows else None

        if key:
            keyed = await self.get_existing(key)
            if keyed is not None:
                return keyed
        if not workflow:
            return None
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
                OmnigentBridgeSession.bridge_session_id.desc(),
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
        capabilities: dict[str, bool] | None = None,
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
            changed = False
            if capabilities is not None:
                metadata["interventionCapabilities"] = capabilities
                changed = True
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
                changed = True
            if changed:
                row.metadata_ = metadata
                await session.commit()
                await session.refresh(row)
            return _detached(session, row)

    async def record_resource_harvest_completed(self, session_id: str) -> None:
        """Persist authority that provider resources were harvested successfully."""

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession).where(
                    OmnigentBridgeSession.omnigent_session_id == session_id
                )
            )
            row = result.scalars().one_or_none()
            if row is None:
                raise OmnigentIdempotencyError("provider session is not bridge-bound")
            metadata = dict(row.metadata_ or {})
            metadata[RESOURCE_HARVEST_COMPLETED_KEY] = datetime.now(tz=UTC).isoformat()
            row.metadata_ = metadata
            await session.commit()

    async def record_provider_session_deleted(self, session_id: str) -> None:
        """Clear a deleted provider binding so create retries cannot reuse it."""

        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession).where(
                    OmnigentBridgeSession.omnigent_session_id == session_id
                )
            )
            row = result.scalars().one_or_none()
            if row is None:
                raise OmnigentIdempotencyError("provider session is not bridge-bound")
            metadata = dict(row.metadata_ or {})
            metadata[PROVIDER_SESSION_DELETED_KEY] = datetime.now(tz=UTC).isoformat()
            row.metadata_ = metadata
            row.omnigent_session_id = None
            await session.commit()

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
            lifecycle_state = (
                (row.metadata_ or {}).get(EMBEDDED_LIFECYCLE_KEY) or {}
            ).get("state")
            if row.omnigent_endpoint_ref == "embedded" and lifecycle_state in {
                "runner_tunnel_waiting",
                "runner_tunnel_ready",
                "first_message_prepared",
            }:
                _advance_embedded_lifecycle(
                    row, "first_message_prepared", code="first_message_digest_persisted"
                )
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
            if row.omnigent_endpoint_ref == "embedded":
                _advance_embedded_lifecycle(
                    row, "first_message_posting", code="first_message_side_effect_started"
                )
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
            if row.omnigent_endpoint_ref == "embedded":
                _advance_embedded_lifecycle(
                    row, "first_message_posted", code="first_message_response_persisted"
                )
                _advance_embedded_lifecycle(
                    row, "running", code="first_message_delivery_complete"
                )
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
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.idempotency_key == idempotency_key)
                .with_for_update()
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                raise OmnigentIdempotencyError("missing Omnigent bridge session row")
            row.status = coalesce_bridge_status(status)
            row.first_message_state = FIRST_MESSAGE_TERMINAL
            if row.omnigent_endpoint_ref == "embedded":
                lifecycle_state = (
                    "stopped"
                    if row.status in {"completed", "canceled"}
                    else "failed"
                )
                _advance_embedded_lifecycle(
                    row, lifecycle_state, code=f"terminal_{row.status}"
                )
            if terminal_refs:
                safe_terminal_refs = redact_sensitive_payload(terminal_refs)
                if not isinstance(safe_terminal_refs, dict):
                    raise OmnigentIdempotencyError("invalid terminal evidence payload")
                row.terminal_refs = safe_terminal_refs
                # Keep the first-class evidence ref columns in sync with the
                # capture bundle instead of leaving them NULL for post-migration
                # rows (§7.1); the JSON ``terminal_refs`` blob is preserved as-is.
                for column, value in _canonical_ref_columns(safe_terminal_refs).items():
                    setattr(row, column, value)
            if events:
                # Replace only provider events. Lifecycle rows are independent
                # terminal evidence and must survive provider stream indexing.
                await session.execute(
                    delete(OmnigentBridgeSessionEvent).where(
                        OmnigentBridgeSessionEvent.bridge_session_id
                        == row.bridge_session_id,
                        OmnigentBridgeSessionEvent.direction != "moonmind_system",
                    )
                )
                max_sequence_result = await session.execute(
                    select(func.max(OmnigentBridgeSessionEvent.sequence)).where(
                        OmnigentBridgeSessionEvent.bridge_session_id
                        == row.bridge_session_id
                    )
                )
                offset = int(max_sequence_result.scalar() or 0)
                provider_events = []
                for index, event in enumerate(events, start=1):
                    prepared = dict(event)
                    prepared["sequence"] = offset + index
                    provider_events.append(prepared)
                for event_row in _build_event_rows(
                    row.bridge_session_id, provider_events
                ):
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

    async def list_event_page(
        self, bridge_session_id: str, *, after: int = 0, limit: int = 100
    ) -> BridgeEventPage:
        """Read at most ``limit`` events after a durable sequence cursor.

        The extra row establishes ``has_more`` without loading the remaining
        history. Min/max are scalar index queries used for retention-gap and
        terminal-drain decisions.
        """

        async with self._session_factory() as session:
            bounds = await session.execute(
                select(
                    func.min(OmnigentBridgeSessionEvent.sequence),
                    func.max(OmnigentBridgeSessionEvent.sequence),
                ).where(
                    OmnigentBridgeSessionEvent.bridge_session_id == bridge_session_id
                )
            )
            earliest, latest = bounds.one()
            result = await session.execute(
                select(OmnigentBridgeSessionEvent)
                .where(
                    OmnigentBridgeSessionEvent.bridge_session_id == bridge_session_id,
                    OmnigentBridgeSessionEvent.sequence > after,
                )
                .order_by(OmnigentBridgeSessionEvent.sequence)
                .limit(limit + 1)
            )
            rows = list(result.scalars().all())
            has_more = len(rows) > limit
            rows = rows[:limit]
            for row in rows:
                session.expunge(row)
            return BridgeEventPage(
                rows,
                has_more,
                int(latest or 0),
                int(earliest) if earliest is not None else None,
            )

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
        if not events:
            return []
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
            dedup_keys = [_event_deduplication_key(event) for event in events]
            existing_result = await session.execute(
                select(OmnigentBridgeSessionEvent.deduplication_key).where(
                    OmnigentBridgeSessionEvent.bridge_session_id == key,
                    OmnigentBridgeSessionEvent.deduplication_key.in_(dedup_keys),
                )
            )
            existing = set(existing_result.scalars().all())
            # A reconnect can replay the same provider frame more than once in
            # a single received batch.  Deduplicate both against committed rows
            # and earlier entries in this batch before assigning sequences.
            seen = set(existing)
            pending: list[tuple[dict[str, Any], str]] = []
            for event, dedup_key in zip(events, dedup_keys, strict=True):
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                pending.append((event, dedup_key))
            if not pending:
                return []
            max_sequence_result = await session.execute(
                select(func.max(OmnigentBridgeSessionEvent.sequence)).where(
                    OmnigentBridgeSessionEvent.bridge_session_id == key
                )
            )
            next_sequence = int(max_sequence_result.scalar() or 0) + 1
            prepared_events: list[dict[str, Any]] = []
            for offset, (event, dedup_key) in enumerate(pending):
                prepared = redact_raw_events([dict(event)])[0]
                prepared["sequence"] = next_sequence + offset
                prepared["deduplicationKey"] = dedup_key
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

    async def attach_active_journal_refs(
        self, bridge_session_id: str, *, raw_ref: str, normalized_ref: str
    ) -> None:
        """Atomically switch both active journal refs after artifacts exist."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.bridge_session_id == bridge_session_id)
                .with_for_update()
            )
            row = result.scalar_one()
            row.raw_events_ref = raw_ref
            row.normalized_events_ref = normalized_ref
            await session.commit()

    async def attach_capture_evidence(
        self,
        bridge_session_id: str,
        *,
        capture_manifest_ref: str,
        resource_projection_ref: str,
        evidence_completeness: str,
    ) -> None:
        """Attach MoonMind-owned embedded harvest evidence without terminalizing."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(OmnigentBridgeSession)
                .where(OmnigentBridgeSession.bridge_session_id == bridge_session_id)
                .with_for_update()
            )
            row = result.scalar_one()
            row.capture_manifest_ref = str(capture_manifest_ref)[:1024]
            refs = dict(row.terminal_refs or {})
            refs.update(
                {
                    "captureManifestRef": str(capture_manifest_ref)[:1024],
                    "resourceProjectionRef": str(resource_projection_ref)[:1024],
                    "evidenceCompleteness": str(evidence_completeness)[:64],
                }
            )
            row.terminal_refs = refs
            await session.commit()

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
                deduplication_key=_event_deduplication_key(event),
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


def _event_deduplication_key(event: dict[str, Any]) -> str:
    """Prefer explicit/provider identity, otherwise bind content to its cursor."""
    import hashlib
    import json

    explicit = _string_or_none(event.get("deduplicationKey"))
    if explicit:
        return explicit[:128]
    metadata = event.get("metadata") or {}
    reconciliation = metadata.get("reconciliation") or {}
    provider_id = _string_or_none(reconciliation.get("providerEventId"))
    if provider_id:
        return f"provider:{provider_id}"[:128]
    cursor = reconciliation.get("streamCursor") or event.get("sequence") or 0
    canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"cursor:{cursor}:{digest}"[:128]


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
