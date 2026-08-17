"""Deterministic, idempotent backfill from legacy bridge rows.

Issue MoonLadderStudios/MoonMind#3703 (Migration and compatibility). The
control-plane tables are created additively by migration ``356``; this module
backfills canonical :class:`OmnigentSession` authority from the existing
``omnigent_bridge_sessions`` rows without deleting or rewriting them.

Design:

* Group legacy rows by provider session and Workflow authority.
* Select a canonical row only when immutable authority is complete and
  nonconflicting. Conflicting immutable authority is quarantined fail-closed --
  never chosen by ``updated_at``.
* Preserve every event, artifact, publication, diagnostic, and terminal ref on
  the canonical session metadata so no evidence is lost.
* Convert each request-specific bridge row into a turn-attempt lineage entry.
* Keep previously issued duplicate chat-binding ids as safe aliases to the
  canonical authority, or as stable fail-closed diagnostics for quarantined
  groups.
* Support dry-run (plan only) and idempotent apply. Deterministic ids make a
  repeat apply a no-op.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    OmnigentBridgeSession,
    OmnigentChatBindingAlias,
    OmnigentSession,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane.repositories import (
    CHAT_BINDING_RESOLUTION_ALIAS,
    CHAT_BINDING_RESOLUTION_FAIL_CLOSED,
    TERMINAL_SESSION_STATES,
    compute_authority_scope,
)

_STATUS_ALIASES = {"cancelled": "canceled", "timeout": "timed_out"}

QUARANTINE_DIAGNOSTIC = "ambiguous_authority"


def _normalize_status(status: str | None) -> str:
    raw = str(status or "").strip().lower()
    return _STATUS_ALIASES.get(raw, raw)


def _is_terminal(status: str | None) -> bool:
    return _normalize_status(status) in TERMINAL_SESSION_STATES


def _deterministic_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def _sort_key(row: "BridgeRowView") -> tuple[str, str]:
    created = row.created_at.isoformat() if row.created_at is not None else ""
    return (created, row.bridge_session_id)


@dataclass(frozen=True)
class BridgeRowView:
    """Immutable projection of one legacy ``omnigent_bridge_sessions`` row."""

    bridge_session_id: str
    provider: str
    compatibility_profile: str
    moonmind_workflow_id: str
    moonmind_run_id: str | None
    moonmind_agent_run_id: str | None
    step_execution_id: str | None
    idempotency_key: str
    provider_session_id: str | None
    chat_binding_id: str | None
    status: str | None
    first_message_state: str | None
    first_message_digest: str | None
    provider_profile_id: str | None
    credential_generation: int | None
    host_binding_ref: str | None
    host_lease_ref: str | None
    terminal_refs: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    refs: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: OmnigentBridgeSession) -> "BridgeRowView":
        return cls(
            bridge_session_id=row.bridge_session_id,
            provider=row.provider,
            compatibility_profile=row.compatibility_profile,
            moonmind_workflow_id=row.moonmind_workflow_id,
            moonmind_run_id=row.moonmind_run_id,
            moonmind_agent_run_id=row.moonmind_agent_run_id,
            step_execution_id=row.step_execution_id,
            idempotency_key=row.idempotency_key,
            provider_session_id=row.omnigent_session_id,
            chat_binding_id=row.chat_binding_id,
            status=row.status,
            first_message_state=row.first_message_state,
            first_message_digest=row.first_message_digest,
            provider_profile_id=row.provider_profile_id,
            credential_generation=row.credential_generation,
            host_binding_ref=row.host_binding_ref,
            host_lease_ref=row.host_lease_ref,
            terminal_refs=dict(row.terminal_refs or {}),
            metadata=dict(row.metadata_ or {}),
            refs={
                "rawEventsRef": row.raw_events_ref,
                "normalizedEventsRef": row.normalized_events_ref,
                "initialSnapshotRef": row.initial_snapshot_ref,
                "finalSnapshotRef": row.final_snapshot_ref,
                "captureManifestRef": row.capture_manifest_ref,
                "diagnosticsRef": row.diagnostics_ref,
                "externalStateRef": row.external_state_ref,
            },
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True)
class PlannedTurnAttempt:
    turn_attempt_id: str
    idempotency_key: str
    turn_kind: str
    state: str
    outcome: str | None
    step_execution_id: str | None
    instruction_digest: str | None
    continuation_of_attempt_id: str | None
    source_bridge_session_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlannedAlias:
    chat_binding_id: str
    canonical_session_id: str | None
    resolution: str
    diagnostic_code: str | None
    source_bridge_session_id: str | None


@dataclass(frozen=True)
class PlannedSession:
    session_id: str
    authority_scope: str
    moonmind_workflow_id: str
    moonmind_run_id: str | None
    moonmind_agent_run_id: str | None
    step_execution_id: str | None
    provider: str
    compatibility_profile: str
    provider_session_id: str | None
    chat_binding_id: str | None
    terminal_state: str | None
    provider_profile_id: str | None
    credential_generation: int | None
    host_binding_ref: str | None
    host_lease_ref: str | None
    metadata: Mapping[str, Any]
    turn_attempts: tuple[PlannedTurnAttempt, ...]
    aliases: tuple[PlannedAlias, ...]


@dataclass(frozen=True)
class QuarantinedGroup:
    group_key: str
    reason: str
    bridge_session_ids: tuple[str, ...]
    aliases: tuple[PlannedAlias, ...]


@dataclass(frozen=True)
class BackfillPlan:
    sessions: tuple[PlannedSession, ...]
    quarantined: tuple[QuarantinedGroup, ...]

    @property
    def alias_count(self) -> int:
        session_aliases = sum(len(s.aliases) for s in self.sessions)
        quarantine_aliases = sum(len(q.aliases) for q in self.quarantined)
        return session_aliases + quarantine_aliases


def _group_key(row: BridgeRowView) -> str:
    """Group by provider session and Workflow authority.

    Rows that never attached a provider session cannot be duplicates of one, so
    each is its own group keyed by its bridge session id.
    """

    provider_session = str(row.provider_session_id or "").strip()
    if provider_session:
        return f"wf:{row.moonmind_workflow_id}|session:{provider_session}"
    return f"unattached:{row.bridge_session_id}"


def _immutable_authority(row: BridgeRowView) -> tuple[str, str, str, str]:
    return (
        str(row.moonmind_workflow_id or ""),
        str(row.provider or ""),
        str(row.compatibility_profile or ""),
        str(row.provider_session_id or ""),
    )


def _plan_group(group_key: str, rows: list[BridgeRowView]) -> PlannedSession | QuarantinedGroup:
    ordered = sorted(rows, key=_sort_key)

    # Fail closed on conflicting immutable authority; never choose by updated_at.
    authorities = {_immutable_authority(row) for row in ordered}
    if len(authorities) > 1:
        aliases = tuple(
            PlannedAlias(
                chat_binding_id=row.chat_binding_id,
                canonical_session_id=None,
                resolution=CHAT_BINDING_RESOLUTION_FAIL_CLOSED,
                diagnostic_code=QUARANTINE_DIAGNOSTIC,
                source_bridge_session_id=row.bridge_session_id,
            )
            for row in ordered
            if row.chat_binding_id
        )
        return QuarantinedGroup(
            group_key=group_key,
            reason="conflicting_immutable_authority",
            bridge_session_ids=tuple(row.bridge_session_id for row in ordered),
            aliases=aliases,
        )

    anchor = ordered[0]
    provider_session_id = str(anchor.provider_session_id or "").strip() or None
    scope = compute_authority_scope(
        moonmind_workflow_id=anchor.moonmind_workflow_id,
        provider=anchor.provider,
        provider_session_id=provider_session_id,
    )
    session_id = _deterministic_id("oms_bf_", scope)
    if provider_session_id is None:
        # An unattached row is scoped by canonical identity so two pre-attach
        # rows never collide before a provider session exists.
        scope = f"{scope}{session_id}"

    # Canonical chat binding: the deterministic earliest binding; the rest
    # become safe aliases to the canonical authority.
    canonical_binding: str | None = None
    aliases: list[PlannedAlias] = []
    for row in ordered:
        binding = row.chat_binding_id
        if not binding:
            continue
        if canonical_binding is None:
            canonical_binding = binding
            continue
        if binding == canonical_binding:
            continue
        aliases.append(
            PlannedAlias(
                chat_binding_id=binding,
                canonical_session_id=session_id,
                resolution=CHAT_BINDING_RESOLUTION_ALIAS,
                diagnostic_code=None,
                source_bridge_session_id=row.bridge_session_id,
            )
        )

    # Turn-attempt lineage: first request is the instruction; later requests are
    # continuation turns of the first attempt.
    turn_attempts: list[PlannedTurnAttempt] = []
    first_attempt_id: str | None = None
    for index, row in enumerate(ordered):
        attempt_id = _deterministic_id("omt_bf_", row.idempotency_key)
        terminal = _is_terminal(row.status)
        attempt = PlannedTurnAttempt(
            turn_attempt_id=attempt_id,
            idempotency_key=row.idempotency_key,
            turn_kind="instruction" if index == 0 else "continuation",
            state="terminal" if terminal else "running",
            outcome=_normalize_status(row.status) if terminal else None,
            step_execution_id=row.step_execution_id,
            instruction_digest=row.first_message_digest,
            continuation_of_attempt_id=None if index == 0 else first_attempt_id,
            source_bridge_session_id=row.bridge_session_id,
        )
        if index == 0:
            first_attempt_id = attempt_id
        turn_attempts.append(attempt)

    # Session terminality is conservative: only when every request row is
    # terminal does the canonical authority terminalize. A single terminal
    # continuation row must never terminalize a session whose provider session
    # is still active (the #3685 regression class).
    all_terminal = bool(ordered) and all(_is_terminal(row.status) for row in ordered)
    terminal_state = _normalize_status(ordered[-1].status) if all_terminal else None

    preserved_refs = {
        row.bridge_session_id: {
            "refs": {k: v for k, v in row.refs.items() if v},
            "terminalRefs": dict(row.terminal_refs or {}),
            "status": row.status,
            "firstMessageState": row.first_message_state,
        }
        for row in ordered
    }
    metadata = {
        "backfill": {
            "issue": "MoonLadderStudios/MoonMind#3703",
            "sourceBridgeSessionIds": [row.bridge_session_id for row in ordered],
            "preservedRefs": preserved_refs,
        }
    }

    return PlannedSession(
        session_id=session_id,
        authority_scope=scope,
        moonmind_workflow_id=anchor.moonmind_workflow_id,
        moonmind_run_id=anchor.moonmind_run_id,
        moonmind_agent_run_id=anchor.moonmind_agent_run_id,
        step_execution_id=anchor.step_execution_id,
        provider=anchor.provider,
        compatibility_profile=anchor.compatibility_profile,
        provider_session_id=provider_session_id,
        chat_binding_id=canonical_binding,
        terminal_state=terminal_state,
        provider_profile_id=anchor.provider_profile_id,
        credential_generation=anchor.credential_generation,
        host_binding_ref=anchor.host_binding_ref,
        host_lease_ref=anchor.host_lease_ref,
        metadata=metadata,
        turn_attempts=tuple(turn_attempts),
        aliases=tuple(aliases),
    )


def plan_backfill(rows: Sequence[BridgeRowView]) -> BackfillPlan:
    """Deterministically plan the canonical backfill for legacy bridge rows."""

    grouped: dict[str, list[BridgeRowView]] = defaultdict(list)
    for row in rows:
        grouped[_group_key(row)].append(row)

    sessions: list[PlannedSession] = []
    quarantined: list[QuarantinedGroup] = []
    for group_key in sorted(grouped):
        planned = _plan_group(group_key, grouped[group_key])
        if isinstance(planned, QuarantinedGroup):
            quarantined.append(planned)
        else:
            sessions.append(planned)
    return BackfillPlan(sessions=tuple(sessions), quarantined=tuple(quarantined))


@dataclass
class BackfillReport:
    applied: bool
    sessions_planned: int
    sessions_created: int
    turn_attempts_created: int
    aliases_created: int
    quarantined_groups: int
    plan: BackfillPlan


async def run_backfill(session: AsyncSession, *, apply: bool = False) -> BackfillReport:
    """Plan (and optionally apply) the backfill against a live session.

    Dry-run (``apply=False``) performs no writes. Apply is idempotent: canonical
    sessions, turn attempts, and aliases use deterministic ids, so a repeat apply
    creates nothing new. Legacy bridge rows are never modified or deleted.
    """

    result = await session.execute(select(OmnigentBridgeSession))
    rows = [BridgeRowView.from_row(row) for row in result.scalars().all()]
    plan = plan_backfill(rows)

    sessions_created = 0
    turn_attempts_created = 0
    aliases_created = 0

    if apply:
        for planned in plan.sessions:
            existing = await session.get(OmnigentSession, planned.session_id)
            if existing is None:
                session.add(
                    OmnigentSession(
                        session_id=planned.session_id,
                        moonmind_workflow_id=planned.moonmind_workflow_id,
                        moonmind_run_id=planned.moonmind_run_id,
                        moonmind_agent_run_id=planned.moonmind_agent_run_id,
                        step_execution_id=planned.step_execution_id,
                        provider=planned.provider,
                        compatibility_profile=planned.compatibility_profile,
                        provider_session_id=planned.provider_session_id,
                        authority_scope=planned.authority_scope,
                        chat_binding_id=planned.chat_binding_id,
                        desired_state=planned.terminal_state or "active",
                        observed_state=planned.terminal_state or "unknown",
                        reconciled_state=planned.terminal_state or "unknown",
                        terminal_state=planned.terminal_state,
                        historical_read_state=(
                            "historical" if planned.terminal_state else "live"
                        ),
                        provider_profile_id=planned.provider_profile_id,
                        credential_generation=planned.credential_generation,
                        host_binding_ref=planned.host_binding_ref,
                        host_lease_ref=planned.host_lease_ref,
                        metadata_=dict(planned.metadata),
                    )
                )
                await session.flush()
                sessions_created += 1

            for attempt in planned.turn_attempts:
                if await session.get(OmnigentTurnAttempt, attempt.turn_attempt_id):
                    continue
                session.add(
                    OmnigentTurnAttempt(
                        turn_attempt_id=attempt.turn_attempt_id,
                        session_id=planned.session_id,
                        step_execution_id=attempt.step_execution_id,
                        turn_kind=attempt.turn_kind,
                        continuation_of_attempt_id=attempt.continuation_of_attempt_id,
                        idempotency_key=attempt.idempotency_key,
                        instruction_digest=attempt.instruction_digest,
                        state=attempt.state,
                        outcome=attempt.outcome,
                        metadata_={
                            "backfill": {
                                "sourceBridgeSessionId": attempt.source_bridge_session_id
                            }
                        },
                    )
                )
                await session.flush()
                turn_attempts_created += 1

            for alias in planned.aliases:
                if await _ensure_alias(session, alias):
                    aliases_created += 1

        for group in plan.quarantined:
            for alias in group.aliases:
                if await _ensure_alias(session, alias):
                    aliases_created += 1

        await session.commit()

    return BackfillReport(
        applied=apply,
        sessions_planned=len(plan.sessions),
        sessions_created=sessions_created,
        turn_attempts_created=turn_attempts_created,
        aliases_created=aliases_created,
        quarantined_groups=len(plan.quarantined),
        plan=plan,
    )


async def _ensure_alias(session: AsyncSession, alias: PlannedAlias) -> bool:
    if await session.get(OmnigentChatBindingAlias, alias.chat_binding_id):
        return False
    session.add(
        OmnigentChatBindingAlias(
            chat_binding_id=alias.chat_binding_id,
            canonical_session_id=alias.canonical_session_id,
            resolution=alias.resolution,
            diagnostic_code=alias.diagnostic_code,
            source_bridge_session_id=alias.source_bridge_session_id,
        )
    )
    await session.flush()
    return True
