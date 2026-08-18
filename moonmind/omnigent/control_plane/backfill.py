"""Deterministic backfill from legacy bridge rows to control-plane aggregates.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

The legacy ``omnigent_bridge_sessions`` row conflates one logical provider
session with every request/turn/continuation attempt against it. #3685 shows a
production shape where seven bridge rows and chat bindings pointed at one
provider session. This module folds those rows into the new durable aggregates
**without deleting the legacy rows**:

* Group bridge rows by ``(workflow, provider session)`` authority.
* Select a canonical session only when the group's immutable authority is
  complete and nonconflicting; conflicting authority is quarantined fail-closed
  (never chosen by ``updated_at``).
* Convert each request-specific bridge row into turn-attempt lineage (the
  earliest row is ``initial``; the rest are ``continuation``).
* Preserve every event / artifact / snapshot / diagnostic / terminal ref as an
  append-only migration observation.
* Keep previously issued chat-binding URLs as safe aliases to the canonical
  authority, or as stable fail-closed diagnostics for quarantined groups.

Both ``dry_run`` and idempotent apply modes are supported: repeat dry-run and
repeat apply produce the same plan and leave the same rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentBridgeSession, OmnigentBridgeSessionEvent

from .records import compute_digest
from .repositories import ControlPlaneRepositories

# Sentinel used only for deterministic sorting when created_at is NULL.
_EPOCH_SENTINEL = datetime.min.replace(tzinfo=timezone.utc)

# Bridge evidence refs preserved verbatim into a migration observation so no
# artifact/diagnostic/terminal ref is lost during backfill.
_EVIDENCE_REF_FIELDS = (
    "raw_events_ref",
    "normalized_events_ref",
    "initial_snapshot_ref",
    "final_snapshot_ref",
    "capture_manifest_ref",
    "diagnostics_ref",
    "external_state_ref",
)

_MIGRATION_OBSERVATION_TYPE = "legacy_bridge_row"
# Per-event evidence preserved from the legacy bridge event index. An artifact
# referenced only by an event row (never by a session-level ref) would otherwise
# become unreachable once the legacy tables are retired.
_MIGRATION_EVENT_OBSERVATION_TYPE = "legacy_bridge_event"
_MIGRATION_SOURCE = "bridge_backfill"


def _canonical_session_id(workflow_id: str, group_key: str) -> str:
    return "ocs_" + compute_digest(["session", workflow_id, group_key])[:40]


def _turn_attempt_id(bridge_session_id: str) -> str:
    return "ota_" + compute_digest(["turn", bridge_session_id])[:40]


def _group_key(row: OmnigentBridgeSession) -> tuple[str, str, bool]:
    """Return ``(workflow_id, group_key, attached)``.

    Rows that share a provider session (``omnigent_session_id``) group together;
    unattached rows are each their own singleton so pre-attachment rows never
    collapse into an unrelated authority.
    """

    if row.omnigent_session_id:
        return (row.moonmind_workflow_id, row.omnigent_session_id, True)
    return (row.moonmind_workflow_id, f"unattached:{row.bridge_session_id}", False)


def _immutable_authority(row: OmnigentBridgeSession) -> tuple[str, Optional[str]]:
    return (row.provider, row.compatibility_profile)


@dataclass
class PlannedSession:
    session_id: str
    moonmind_workflow_id: str
    provider: str
    compatibility_profile: Optional[str]
    provider_session_ref: Optional[str]
    chat_binding_id: Optional[str]
    canonical_bridge_session_id: str
    member_bridge_session_ids: list[str]
    terminal_state: Optional[str]
    alias_chat_binding_ids: list[str] = field(default_factory=list)


@dataclass
class PlannedTurnAttempt:
    turn_attempt_id: str
    session_id: str
    bridge_session_id: str
    idempotency_key: str
    lineage_kind: str


@dataclass
class PlannedAlias:
    chat_binding_id: str
    session_id: Optional[str]
    alias_state: str
    diagnostic_reason: Optional[str]


@dataclass
class QuarantinedGroup:
    moonmind_workflow_id: str
    group_key: str
    bridge_session_ids: list[str]
    conflicting_authorities: list[tuple[str, Optional[str]]]
    chat_binding_ids: list[str]


@dataclass
class BackfillPlan:
    sessions: list[PlannedSession] = field(default_factory=list)
    turn_attempts: list[PlannedTurnAttempt] = field(default_factory=list)
    aliases: list[PlannedAlias] = field(default_factory=list)
    quarantined_groups: list[QuarantinedGroup] = field(default_factory=list)
    preserved_evidence_rows: int = 0

    def summary(self) -> dict[str, int]:
        return {
            "sessions": len(self.sessions),
            "turn_attempts": len(self.turn_attempts),
            "aliases": len(self.aliases),
            "quarantined_groups": len(self.quarantined_groups),
            "preserved_evidence_rows": self.preserved_evidence_rows,
        }


@dataclass
class BackfillReport:
    dry_run: bool
    plan: BackfillPlan
    sessions_written: int = 0
    turn_attempts_written: int = 0
    observations_written: int = 0
    aliases_written: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "plan": self.plan.summary(),
            "sessions_written": self.sessions_written,
            "turn_attempts_written": self.turn_attempts_written,
            "observations_written": self.observations_written,
            "aliases_written": self.aliases_written,
        }


def _terminal_state_for(rows: list[OmnigentBridgeSession]) -> Optional[str]:
    """Derive a session terminal state from member rows, if any is terminal.

    A member turn being terminal does not make the session terminal, so this
    only reports a session terminal state when a member row itself recorded a
    session-level terminal status via ``terminal_refs``.
    """

    for row in rows:
        terminal_refs = row.terminal_refs or {}
        session_terminal = terminal_refs.get("session_terminal_state")
        if session_terminal:
            return str(session_terminal)
    return None


async def plan_backfill(session: AsyncSession) -> BackfillPlan:
    """Compute a deterministic, side-effect-free backfill plan."""

    rows = list(
        (
            await session.execute(
                select(OmnigentBridgeSession).order_by(
                    OmnigentBridgeSession.created_at,
                    OmnigentBridgeSession.bridge_session_id,
                )
            )
        )
        .scalars()
        .all()
    )

    groups: dict[tuple[str, str], list[OmnigentBridgeSession]] = {}
    for row in rows:
        workflow_id, group_key, _attached = _group_key(row)
        groups.setdefault((workflow_id, group_key), []).append(row)

    plan = BackfillPlan()

    for (workflow_id, group_key), members in sorted(groups.items()):
        # Deterministic member order: created_at then id (never updated_at).
        members = sorted(
            members, key=lambda r: (r.created_at or _EPOCH_SENTINEL, r.bridge_session_id)
        )
        authorities = {_immutable_authority(r) for r in members}
        chat_binding_ids = [
            r.chat_binding_id for r in members if r.chat_binding_id
        ]

        if len(authorities) > 1:
            # Conflicting immutable authority: fail closed, quarantine.
            plan.quarantined_groups.append(
                QuarantinedGroup(
                    moonmind_workflow_id=workflow_id,
                    group_key=group_key,
                    bridge_session_ids=[r.bridge_session_id for r in members],
                    conflicting_authorities=sorted(
                        authorities, key=lambda a: (a[0], a[1] or "")
                    ),
                    chat_binding_ids=chat_binding_ids,
                )
            )
            for binding in chat_binding_ids:
                plan.aliases.append(
                    PlannedAlias(
                        chat_binding_id=binding,
                        session_id=None,
                        alias_state="quarantined",
                        diagnostic_reason=(
                            "conflicting immutable authority for "
                            f"{workflow_id}/{group_key}"
                        ),
                    )
                )
            plan.preserved_evidence_rows += len(members)
            continue

        canonical_row = members[0]
        provider, compatibility_profile = next(iter(authorities))
        session_id = _canonical_session_id(workflow_id, group_key)
        # Prefer the canonical row's binding; else the deterministically-smallest
        # binding present in the group.
        canonical_binding = canonical_row.chat_binding_id or (
            sorted(chat_binding_ids)[0] if chat_binding_ids else None
        )

        planned_session = PlannedSession(
            session_id=session_id,
            moonmind_workflow_id=workflow_id,
            provider=provider,
            compatibility_profile=compatibility_profile,
            provider_session_ref=(
                canonical_row.omnigent_session_id if canonical_row.omnigent_session_id else None
            ),
            chat_binding_id=canonical_binding,
            canonical_bridge_session_id=canonical_row.bridge_session_id,
            member_bridge_session_ids=[r.bridge_session_id for r in members],
            terminal_state=_terminal_state_for(members),
        )

        for index, member in enumerate(members):
            lineage = "initial" if index == 0 else "continuation"
            plan.turn_attempts.append(
                PlannedTurnAttempt(
                    turn_attempt_id=_turn_attempt_id(member.bridge_session_id),
                    session_id=session_id,
                    bridge_session_id=member.bridge_session_id,
                    idempotency_key=member.idempotency_key,
                    lineage_kind=lineage,
                )
            )
            plan.preserved_evidence_rows += 1
            # Every member binding that is not the canonical binding becomes a
            # safe alias to the canonical authority.
            if member.chat_binding_id and member.chat_binding_id != canonical_binding:
                planned_session.alias_chat_binding_ids.append(member.chat_binding_id)
                plan.aliases.append(
                    PlannedAlias(
                        chat_binding_id=member.chat_binding_id,
                        session_id=session_id,
                        alias_state="active",
                        diagnostic_reason=None,
                    )
                )

        # The canonical binding also resolves to the canonical session.
        if canonical_binding:
            plan.aliases.append(
                PlannedAlias(
                    chat_binding_id=canonical_binding,
                    session_id=session_id,
                    alias_state="active",
                    diagnostic_reason=None,
                )
            )

        plan.sessions.append(planned_session)

    return plan


async def _evidence_bounded_index(row: OmnigentBridgeSession) -> dict[str, Any]:
    index: dict[str, Any] = {
        "bridge_session_id": row.bridge_session_id,
        "moonmind_agent_run_id": row.moonmind_agent_run_id,
        "status": row.status,
    }
    for name in _EVIDENCE_REF_FIELDS:
        value = getattr(row, name, None)
        if value:
            index[name] = value
    if row.terminal_refs:
        index["terminal_refs"] = dict(row.terminal_refs)
    return index


async def run_backfill(
    session_factory: Callable[[], Any], *, dry_run: bool = True
) -> BackfillReport:
    """Plan (and, unless ``dry_run``, idempotently apply) the backfill.

    Idempotent: canonical sessions, turn attempts, observations, and aliases all
    use deterministic identities, so a repeat apply writes nothing new.
    """

    async with session_factory() as session:
        plan = await plan_backfill(session)
        report = BackfillReport(dry_run=dry_run, plan=plan)
        if dry_run:
            return report

        bridge_by_id = {
            row.bridge_session_id: row
            for row in (
                await session.execute(select(OmnigentBridgeSession))
            )
            .scalars()
            .all()
        }

        # Index the legacy per-event stream so artifacts referenced only by an
        # event row (not by a session-level ref) are preserved as canonical
        # observations before the legacy tables can be retired.
        events_by_bridge: dict[str, list[OmnigentBridgeSessionEvent]] = {}
        for event in (
            (
                await session.execute(
                    select(OmnigentBridgeSessionEvent).order_by(
                        OmnigentBridgeSessionEvent.bridge_session_id,
                        OmnigentBridgeSessionEvent.sequence,
                    )
                )
            )
            .scalars()
            .all()
        ):
            events_by_bridge.setdefault(event.bridge_session_id, []).append(event)

        repos = ControlPlaneRepositories.bind(session)

        for planned in plan.sessions:
            existing = await repos.sessions.get(planned.session_id)
            if existing is None:
                await repos.sessions.create(
                    session_id=planned.session_id,
                    moonmind_workflow_id=planned.moonmind_workflow_id,
                    provider=planned.provider,
                    compatibility_profile=planned.compatibility_profile,
                    provider_session_ref=planned.provider_session_ref,
                    chat_binding_id=planned.chat_binding_id,
                    metadata={
                        "backfilled_from_bridge": planned.canonical_bridge_session_id,
                        "member_bridge_session_ids": planned.member_bridge_session_ids,
                    },
                )
                report.sessions_written += 1
                if planned.terminal_state:
                    await repos.sessions.mark_terminal(
                        planned.session_id, planned.terminal_state
                    )

        for planned_turn in plan.turn_attempts:
            existing_turn = await repos.turn_attempts.get(planned_turn.turn_attempt_id)
            if existing_turn is None:
                await repos.turn_attempts.create(
                    turn_attempt_id=planned_turn.turn_attempt_id,
                    session_id=planned_turn.session_id,
                    idempotency_key=planned_turn.idempotency_key,
                    lineage_kind=planned_turn.lineage_kind,
                )
                report.turn_attempts_written += 1

            # Preserve the member row's evidence as an append-only observation.
            bridge_row = bridge_by_id.get(planned_turn.bridge_session_id)
            if bridge_row is not None:
                before = await repos.observations.list_for_session(
                    planned_turn.session_id,
                    observation_type=_MIGRATION_OBSERVATION_TYPE,
                )
                dedup = f"{_MIGRATION_OBSERVATION_TYPE}:{bridge_row.bridge_session_id}"
                observed_at = bridge_row.updated_at or bridge_row.created_at or _EPOCH_SENTINEL
                await repos.observations.append(
                    observation_id="oob_" + compute_digest(["evidence", dedup])[:40],
                    session_id=planned_turn.session_id,
                    observation_type=_MIGRATION_OBSERVATION_TYPE,
                    source=_MIGRATION_SOURCE,
                    observed_at=observed_at,
                    deduplication_key=dedup,
                    payload_ref=bridge_row.external_state_ref,
                    bounded_index=await _evidence_bounded_index(bridge_row),
                )
                after = await repos.observations.list_for_session(
                    planned_turn.session_id,
                    observation_type=_MIGRATION_OBSERVATION_TYPE,
                )
                if len(after) > len(before):
                    report.observations_written += 1

            # Preserve per-event artifact evidence for this member row so an
            # artifact referenced only by an event survives legacy retirement.
            for event in events_by_bridge.get(planned_turn.bridge_session_id, []):
                if not event.artifact_ref:
                    continue
                event_dedup = f"{_MIGRATION_EVENT_OBSERVATION_TYPE}:{event.event_id}"
                before_events = await repos.observations.list_for_session(
                    planned_turn.session_id,
                    observation_type=_MIGRATION_EVENT_OBSERVATION_TYPE,
                )
                await repos.observations.append(
                    observation_id="obe_" + compute_digest(["event", event_dedup])[:40],
                    session_id=planned_turn.session_id,
                    observation_type=_MIGRATION_EVENT_OBSERVATION_TYPE,
                    source=_MIGRATION_SOURCE,
                    observed_at=event.timestamp,
                    deduplication_key=event_dedup,
                    source_sequence=event.sequence,
                    payload_ref=event.artifact_ref,
                    bounded_index={
                        "bridge_session_id": event.bridge_session_id,
                        "event_id": event.event_id,
                        "sequence": event.sequence,
                        "direction": event.direction,
                        "event_type": event.event_type,
                        "normalized_status": event.normalized_status,
                        "artifact_ref": event.artifact_ref,
                    },
                )
                after_events = await repos.observations.list_for_session(
                    planned_turn.session_id,
                    observation_type=_MIGRATION_EVENT_OBSERVATION_TYPE,
                )
                if len(after_events) > len(before_events):
                    report.observations_written += 1

        for alias in plan.aliases:
            existing_alias = await repos.chat_binding_aliases.resolve(
                alias.chat_binding_id
            )
            await repos.chat_binding_aliases.register(
                chat_binding_id=alias.chat_binding_id,
                session_id=alias.session_id,
                alias_state=alias.alias_state,
                diagnostic_reason=alias.diagnostic_reason,
            )
            if existing_alias is None:
                report.aliases_written += 1

        await session.commit()

    return report
