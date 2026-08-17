"""Universal optimistic-concurrency and lease-fencing control plane.

MoonLadderStudios/MoonMind#3704.

This module makes optimistic concurrency and lease fencing universal across the
Omnigent control plane. Every mutable lifecycle write declares the exact
revision and ownership generation it observed; a rejected stale write returns a
typed conflict so callers reconcile from current state rather than blindly
retrying the same mutation.

The canonical design lives in ``docs/Omnigent/ConcurrencyAndFencing.md``.

The public surface is:

* :class:`ConflictOutcome` — the stable outcome vocabulary.
* :class:`OmnigentControlPlaneRepository` — typed repository operations
  (``load_for_update``, ``compare_and_swap_session``, ``compare_and_swap_turn``,
  ``claim_command``, ``record_command_delivery``, ``advance_observation_frontier``,
  ``acquire_fencing_generation``, ``claim_cleanup``, ``complete_cleanup``) whose
  lifecycle-changing methods require expected revisions and fencing generations.

Application code never receives unconstrained mutable SQLAlchemy entities from
this repository; reads return immutable snapshots and writes return typed
:class:`CasResult` values.
"""

from __future__ import annotations

import enum
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from api_service.db.models import (
    OmnigentBridgeSession,
    OmnigentCleanupAuthority,
    OmnigentCommand,
    OmnigentFencingGeneration,
    OmnigentTurnAttempt,
)
from moonmind.utils.metrics import get_metrics_emitter

logger = logging.getLogger(__name__)


class ConflictOutcome(str, enum.Enum):
    """Stable, actionable outcomes for a lifecycle-changing write.

    A conflict is observable and actionable but is not an execution failure when
    normal reconciliation can safely converge. Only the caller decides whether an
    outcome is terminal for its journey.
    """

    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    REVISION_CONFLICT = "revision_conflict"
    FENCING_CONFLICT = "fencing_conflict"
    DELIVERY_UNKNOWN = "delivery_unknown"
    IMMUTABLE_AUTHORITY_CONFLICT = "immutable_authority_conflict"
    NOT_OWNER = "not_owner"


#: Outcomes that reconciliation can converge without treating the write as an
#: execution failure.
RECONCILABLE_OUTCOMES = frozenset(
    {
        ConflictOutcome.APPLIED,
        ConflictOutcome.ALREADY_APPLIED,
        ConflictOutcome.REVISION_CONFLICT,
        ConflictOutcome.FENCING_CONFLICT,
        ConflictOutcome.DELIVERY_UNKNOWN,
        ConflictOutcome.NOT_OWNER,
    }
)

#: Outcomes that indicate a successful (or already-satisfied) write.
APPLIED_OUTCOMES = frozenset(
    {ConflictOutcome.APPLIED, ConflictOutcome.ALREADY_APPLIED}
)


class ConcurrencyTelemetryEvent(str, enum.Enum):
    """Bounded, low-cardinality control-plane telemetry events."""

    REVISION_CONFLICT = "revision_conflict"
    FENCING_CONFLICT = "fencing_conflict"
    IMMUTABLE_AUTHORITY_CONFLICT = "immutable_authority_conflict"
    NOT_OWNER = "not_owner"
    DUPLICATE_COMMAND_SUPPRESSED = "duplicate_command_suppressed"
    DELIVERY_UNKNOWN_RECONCILED = "delivery_unknown_reconciled"
    STALE_OBSERVATION_RETAINED = "stale_observation_retained"
    CLEANUP_CLAIM_CONFLICT = "cleanup_claim_conflict"


#: Bounded surfaces used as the only high-level dimension on conflict telemetry.
#: Deliberately excludes workflow, session, turn, host, lease, profile, user, and
#: credential identity so no high-cardinality value ever reaches a metric label.
TELEMETRY_SURFACES = frozenset(
    {"session", "turn", "command", "cleanup", "fencing", "observation"}
)

_CONFLICT_METRIC = "omnigent.control_plane.events"

# Process-wide bounded counters. Keyed by ``(event, surface)`` so the cardinality
# is fixed by the two bounded enums above.
_COUNTERS: Counter[tuple[str, str]] = Counter()


def record_concurrency_event(
    event: ConcurrencyTelemetryEvent, *, surface: str
) -> None:
    """Emit a bounded counter and structured log for a control-plane event.

    Labels are restricted to the two bounded enums. No workflow, session, turn,
    host, lease, profile, user, or credential identity is ever emitted, and no
    secret-shaped value can enter a label.
    """

    if surface not in TELEMETRY_SURFACES:
        raise ValueError(f"unknown control-plane telemetry surface: {surface!r}")
    key = (event.value, surface)
    _COUNTERS[key] += 1
    get_metrics_emitter().increment(
        _CONFLICT_METRIC, tags={"event": event.value, "surface": surface}
    )
    logger.info(
        "omnigent control-plane concurrency event",
        extra={"concurrency_event": event.value, "concurrency_surface": surface},
    )


def counter_snapshot() -> dict[tuple[str, str], int]:
    """Return a copy of the process-wide bounded counters (for tests/introspection)."""

    return dict(_COUNTERS)


def reset_counters() -> None:
    """Reset the process-wide bounded counters (test helper)."""

    _COUNTERS.clear()


_OUTCOME_EVENT = {
    ConflictOutcome.REVISION_CONFLICT: ConcurrencyTelemetryEvent.REVISION_CONFLICT,
    ConflictOutcome.FENCING_CONFLICT: ConcurrencyTelemetryEvent.FENCING_CONFLICT,
    ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT: (
        ConcurrencyTelemetryEvent.IMMUTABLE_AUTHORITY_CONFLICT
    ),
    ConflictOutcome.NOT_OWNER: ConcurrencyTelemetryEvent.NOT_OWNER,
}


def _emit_outcome(outcome: ConflictOutcome, *, surface: str) -> None:
    event = _OUTCOME_EVENT.get(outcome)
    if event is not None:
        record_concurrency_event(event, surface=surface)


@dataclass(frozen=True)
class CasResult:
    """Typed result of a lifecycle-changing write.

    ``observed`` is an immutable snapshot of the current durable state when the
    write did not apply, so the caller can reconcile without re-reading.
    """

    outcome: ConflictOutcome
    revision: int | None = None
    observed: Mapping[str, Any] | None = None

    @property
    def applied(self) -> bool:
        return self.outcome in APPLIED_OUTCOMES

    @property
    def conflict(self) -> bool:
        return not self.applied

    def raise_for_conflict(self) -> "CasResult":
        """Raise :class:`ConcurrencyConflict` unless the write applied."""

        if not self.applied:
            raise ConcurrencyConflict(self.outcome, observed=self.observed)
        return self


class ConcurrencyConflict(RuntimeError):
    """Raised when a caller opts into exception-style conflict handling."""

    def __init__(
        self, outcome: ConflictOutcome, *, observed: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(f"omnigent control-plane conflict: {outcome.value}")
        self.outcome = outcome
        self.observed = observed


class Aggregate(str, enum.Enum):
    """Aggregates exposed through :meth:`OmnigentControlPlaneRepository.load_for_update`."""

    SESSION = "session"
    TURN = "turn"
    COMMAND = "command"
    CLEANUP = "cleanup"


_AGGREGATE_MODEL = {
    Aggregate.SESSION: OmnigentBridgeSession,
    Aggregate.TURN: OmnigentTurnAttempt,
    Aggregate.COMMAND: OmnigentCommand,
    Aggregate.CLEANUP: OmnigentCleanupAuthority,
}
_AGGREGATE_PK = {
    Aggregate.SESSION: "bridge_session_id",
    Aggregate.TURN: "turn_attempt_id",
    Aggregate.COMMAND: "command_id",
    Aggregate.CLEANUP: "cleanup_id",
}


def _snapshot(row: Any) -> dict[str, Any]:
    """Return an immutable-by-convention snapshot of a control-plane row."""

    columns = row.__table__.columns.keys()
    return {name: getattr(row, name) for name in columns}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OmnigentControlPlaneRepository:
    """Typed compare-and-swap + fencing repository for the Omnigent control plane.

    Every lifecycle-changing method requires the caller's observed revision and
    the relevant fencing generation; zero updated rows means stale authority and
    yields a typed conflict, never a silent overwrite.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    # -- observation ---------------------------------------------------------

    async def load_for_update(
        self, aggregate: Aggregate, key: str
    ) -> dict[str, Any] | None:
        """Return a locked, immutable snapshot of one aggregate, or ``None``.

        The row is read under ``FOR UPDATE`` so the observed revision and fencing
        generation are a consistent basis for a subsequent compare-and-swap.
        """

        model = _AGGREGATE_MODEL[aggregate]
        pk = getattr(model, _AGGREGATE_PK[aggregate])
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(model).where(pk == key).with_for_update()
            )
            if row is None:
                return None
            return _snapshot(row)

    # -- session -------------------------------------------------------------

    async def compare_and_swap_session(
        self,
        bridge_session_id: str,
        *,
        expected_revision: int,
        expected_supervisor_generation: int,
        values: Mapping[str, Any],
        immutable_states: frozenset[str] = frozenset(),
    ) -> CasResult:
        """Advance the canonical session revision under fencing.

        The write applies only when both the observed ``revision`` and the
        ``supervisor_generation`` still match. A stale write against a session
        already resting in one of ``immutable_states`` is reported as an
        immutable-authority conflict so a former supervisor cannot regress a
        newer terminal state.
        """

        self._reject_reserved(values, {"revision", "supervisor_generation"})
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(OmnigentBridgeSession)
                .where(
                    OmnigentBridgeSession.bridge_session_id == bridge_session_id,
                    OmnigentBridgeSession.revision == expected_revision,
                    OmnigentBridgeSession.supervisor_generation
                    == expected_supervisor_generation,
                )
                .values(
                    **values,
                    revision=OmnigentBridgeSession.revision + 1,
                    updated_at=_now(),
                )
            )
            if result.rowcount == 1:
                return CasResult(
                    ConflictOutcome.APPLIED, revision=expected_revision + 1
                )
            current = await session.scalar(
                select(OmnigentBridgeSession).where(
                    OmnigentBridgeSession.bridge_session_id == bridge_session_id
                )
            )
        return self._classify_session_conflict(
            current,
            expected_revision=expected_revision,
            expected_supervisor_generation=expected_supervisor_generation,
            immutable_states=immutable_states,
        )

    def _classify_session_conflict(
        self,
        current: OmnigentBridgeSession | None,
        *,
        expected_revision: int,
        expected_supervisor_generation: int,
        immutable_states: frozenset[str],
    ) -> CasResult:
        if current is None:
            raise LookupError("unknown omnigent bridge session")
        observed = _snapshot(current)
        if current.supervisor_generation != expected_supervisor_generation:
            outcome = ConflictOutcome.FENCING_CONFLICT
        elif (
            current.status in immutable_states
            and expected_revision < current.revision
        ):
            outcome = ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT
        else:
            outcome = ConflictOutcome.REVISION_CONFLICT
        _emit_outcome(outcome, surface="session")
        return CasResult(outcome, revision=current.revision, observed=observed)

    # -- turn ----------------------------------------------------------------

    async def compare_and_swap_turn(
        self,
        turn_attempt_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        values: Mapping[str, Any],
        allow_terminal_regression: bool = False,
    ) -> CasResult:
        """Advance one turn-attempt revision under fencing.

        Once an attempt is terminal it is immutable authority: a later write that
        would move it away from terminal (without ``allow_terminal_regression``)
        is rejected as an immutable-authority conflict.
        """

        self._reject_reserved(values, {"revision", "fencing_generation"})
        setting_non_terminal = values.get("terminal") is False
        async with self._session_factory() as session, session.begin():
            current = await session.scalar(
                select(OmnigentTurnAttempt)
                .where(OmnigentTurnAttempt.turn_attempt_id == turn_attempt_id)
                .with_for_update()
            )
            if current is None:
                raise LookupError(f"unknown omnigent turn attempt: {turn_attempt_id}")
            if (
                current.terminal
                and setting_non_terminal
                and not allow_terminal_regression
            ):
                observed = _snapshot(current)
                _emit_outcome(
                    ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT, surface="turn"
                )
                return CasResult(
                    ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT,
                    revision=current.revision,
                    observed=observed,
                )
            if (
                current.revision != expected_revision
                or current.fencing_generation != expected_fencing_generation
            ):
                observed = _snapshot(current)
                if current.fencing_generation != expected_fencing_generation:
                    outcome = ConflictOutcome.FENCING_CONFLICT
                else:
                    outcome = ConflictOutcome.REVISION_CONFLICT
                _emit_outcome(outcome, surface="turn")
                return CasResult(
                    outcome, revision=current.revision, observed=observed
                )
            for key, value in values.items():
                setattr(current, key, value)
            current.revision = expected_revision + 1
            current.updated_at = _now()
            return CasResult(ConflictOutcome.APPLIED, revision=current.revision)

    async def advance_observation_frontier(
        self,
        turn_attempt_id: str,
        *,
        observed_sequence: int,
        expected_revision: int,
    ) -> CasResult:
        """Advance the retained observation frontier without regressing state.

        A delayed event at or below the current frontier is retained as an
        observation (``already_applied``) but never regresses the frontier.
        """

        async with self._session_factory() as session, session.begin():
            current = await session.scalar(
                select(OmnigentTurnAttempt)
                .where(OmnigentTurnAttempt.turn_attempt_id == turn_attempt_id)
                .with_for_update()
            )
            if current is None:
                raise LookupError(f"unknown omnigent turn attempt: {turn_attempt_id}")
            if observed_sequence <= current.observation_frontier:
                record_concurrency_event(
                    ConcurrencyTelemetryEvent.STALE_OBSERVATION_RETAINED,
                    surface="observation",
                )
                return CasResult(
                    ConflictOutcome.ALREADY_APPLIED,
                    revision=current.revision,
                    observed=_snapshot(current),
                )
            if current.revision != expected_revision:
                _emit_outcome(ConflictOutcome.REVISION_CONFLICT, surface="observation")
                return CasResult(
                    ConflictOutcome.REVISION_CONFLICT,
                    revision=current.revision,
                    observed=_snapshot(current),
                )
            current.observation_frontier = observed_sequence
            current.revision = expected_revision + 1
            current.updated_at = _now()
            return CasResult(ConflictOutcome.APPLIED, revision=current.revision)

    # -- fencing -------------------------------------------------------------

    async def acquire_fencing_generation(
        self, scope_key: str, *, scope_kind: str
    ) -> int:
        """Return a strictly newer fencing generation for ``scope_key``.

        The prior owner keeps its older token and can no longer mutate provider,
        host, workspace, cleanup, or durable state fenced by this scope.
        """

        for _ in range(5):
            try:
                async with self._session_factory() as session, session.begin():
                    current = await session.scalar(
                        select(OmnigentFencingGeneration)
                        .where(OmnigentFencingGeneration.scope_key == scope_key)
                        .with_for_update()
                    )
                    if current is None:
                        session.add(
                            OmnigentFencingGeneration(
                                scope_key=scope_key,
                                scope_kind=scope_kind,
                                generation=1,
                            )
                        )
                        return 1
                    current.generation += 1
                    current.updated_at = _now()
                    return current.generation
            except IntegrityError:
                # A concurrent acquirer inserted the scope first; retry and
                # increment its generation instead of racing the insert.
                continue
        raise RuntimeError(
            f"could not acquire fencing generation for scope {scope_kind}"
        )

    async def current_fencing_generation(self, scope_key: str) -> int:
        """Return the current generation for ``scope_key`` (0 when never acquired)."""

        async with self._session_factory() as session:
            current = await session.scalar(
                select(OmnigentFencingGeneration.generation).where(
                    OmnigentFencingGeneration.scope_key == scope_key
                )
            )
            return int(current or 0)

    # -- commands ------------------------------------------------------------

    async def claim_command(
        self,
        *,
        command_id: str,
        bridge_session_id: str,
        command_type: str,
        payload_digest: str,
        idempotency_key: str,
        expected_session_revision: int,
        owner_class: str,
        owner: str,
        fencing_generations: Mapping[str, int] | None = None,
        expected_supervisor_generation: int | None = None,
        turn_attempt_id: str | None = None,
        expected_turn_revision: int | None = None,
    ) -> CasResult:
        """Claim exactly one logical command for execution.

        Concurrent retries of the same logical command (same idempotency key)
        converge on one durable claim. A duplicate with a matching payload digest
        is suppressed and reported as ``already_applied``; a reused idempotency
        identity carrying a different payload is an immutable-authority conflict.
        The claim also fails closed when the observed session/turn revision or
        supervisor generation is stale.
        """

        fencing = dict(fencing_generations or {})
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(OmnigentCommand).where(
                    OmnigentCommand.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return self._existing_command_result(existing, payload_digest)

            session_row = await session.scalar(
                select(OmnigentBridgeSession)
                .where(
                    OmnigentBridgeSession.bridge_session_id == bridge_session_id
                )
                .with_for_update()
            )
            if session_row is None:
                raise LookupError(
                    f"unknown omnigent bridge session: {bridge_session_id}"
                )
            conflict = self._validate_command_authority(
                session_row,
                expected_session_revision=expected_session_revision,
                expected_supervisor_generation=expected_supervisor_generation,
            )
            if conflict is not None:
                return conflict

            if turn_attempt_id is not None:
                turn_row = await session.scalar(
                    select(OmnigentTurnAttempt)
                    .where(OmnigentTurnAttempt.turn_attempt_id == turn_attempt_id)
                    .with_for_update()
                )
                if turn_row is None:
                    raise LookupError(
                        f"unknown omnigent turn attempt: {turn_attempt_id}"
                    )
                if (
                    expected_turn_revision is not None
                    and turn_row.revision != expected_turn_revision
                ):
                    _emit_outcome(ConflictOutcome.REVISION_CONFLICT, surface="command")
                    return CasResult(
                        ConflictOutcome.REVISION_CONFLICT,
                        revision=turn_row.revision,
                        observed=_snapshot(turn_row),
                    )

            command = OmnigentCommand(
                command_id=command_id,
                bridge_session_id=bridge_session_id,
                turn_attempt_id=turn_attempt_id,
                command_type=command_type,
                payload_digest=payload_digest,
                idempotency_key=idempotency_key,
                expected_session_revision=expected_session_revision,
                expected_turn_revision=expected_turn_revision,
                fencing_generations=fencing,
                owner_class=owner_class,
                claim_owner=owner,
                claim_generation=int(
                    fencing.get("command", expected_supervisor_generation or 1)
                ),
                delivery_state="claimed",
                revision=1,
            )
            session.add(command)
            try:
                await session.flush()
            except IntegrityError:
                # A concurrent retry won the unique idempotency claim; converge on
                # the durable winner without issuing a second side effect.
                await session.rollback()
                return await self._resolve_duplicate_claim(
                    idempotency_key, payload_digest
                )
            return CasResult(ConflictOutcome.APPLIED, revision=1)

    def _existing_command_result(
        self, existing: OmnigentCommand, payload_digest: str
    ) -> CasResult:
        observed = _snapshot(existing)
        if existing.payload_digest == payload_digest:
            record_concurrency_event(
                ConcurrencyTelemetryEvent.DUPLICATE_COMMAND_SUPPRESSED,
                surface="command",
            )
            return CasResult(
                ConflictOutcome.ALREADY_APPLIED,
                revision=existing.revision,
                observed=observed,
            )
        _emit_outcome(ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT, surface="command")
        return CasResult(
            ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT,
            revision=existing.revision,
            observed=observed,
        )

    async def _resolve_duplicate_claim(
        self, idempotency_key: str, payload_digest: str
    ) -> CasResult:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(OmnigentCommand).where(
                    OmnigentCommand.idempotency_key == idempotency_key
                )
            )
        if existing is None:  # pragma: no cover - defensive
            raise RuntimeError("command claim vanished after integrity conflict")
        return self._existing_command_result(existing, payload_digest)

    def _validate_command_authority(
        self,
        session_row: OmnigentBridgeSession,
        *,
        expected_session_revision: int,
        expected_supervisor_generation: int | None,
    ) -> CasResult | None:
        if (
            expected_supervisor_generation is not None
            and session_row.supervisor_generation != expected_supervisor_generation
        ):
            _emit_outcome(ConflictOutcome.FENCING_CONFLICT, surface="command")
            return CasResult(
                ConflictOutcome.FENCING_CONFLICT,
                revision=session_row.revision,
                observed=_snapshot(session_row),
            )
        if session_row.revision != expected_session_revision:
            _emit_outcome(ConflictOutcome.REVISION_CONFLICT, surface="command")
            return CasResult(
                ConflictOutcome.REVISION_CONFLICT,
                revision=session_row.revision,
                observed=_snapshot(session_row),
            )
        return None

    async def record_command_delivery(
        self,
        command_id: str,
        *,
        owner: str,
        expected_revision: int,
        delivery_state: str,
        provider_receipt: str | None = None,
        outcome: str | None = None,
    ) -> CasResult:
        """Persist a command's delivery state and provider receipt under CAS.

        Only the current claim owner may record delivery. When the provider side
        effect may already have occurred, the caller records
        ``delivery_state="delivery_unknown"`` so the command is reconciled rather
        than blindly re-issued.
        """

        if delivery_state not in {
            "claimed",
            "dispatched",
            "delivered",
            "delivery_unknown",
            "reconciled",
        }:
            raise ValueError(f"invalid command delivery_state: {delivery_state!r}")
        async with self._session_factory() as session, session.begin():
            current = await session.scalar(
                select(OmnigentCommand)
                .where(OmnigentCommand.command_id == command_id)
                .with_for_update()
            )
            if current is None:
                raise LookupError(f"unknown omnigent command: {command_id}")
            if current.claim_owner != owner:
                _emit_outcome(ConflictOutcome.NOT_OWNER, surface="command")
                return CasResult(
                    ConflictOutcome.NOT_OWNER,
                    revision=current.revision,
                    observed=_snapshot(current),
                )
            if current.revision != expected_revision:
                _emit_outcome(ConflictOutcome.REVISION_CONFLICT, surface="command")
                return CasResult(
                    ConflictOutcome.REVISION_CONFLICT,
                    revision=current.revision,
                    observed=_snapshot(current),
                )
            current.delivery_state = delivery_state
            if provider_receipt is not None:
                current.provider_receipt = provider_receipt
            if outcome is not None:
                current.outcome = outcome
            current.revision = expected_revision + 1
            current.updated_at = _now()
            if delivery_state == "delivery_unknown":
                record_concurrency_event(
                    ConcurrencyTelemetryEvent.DELIVERY_UNKNOWN_RECONCILED,
                    surface="command",
                )
                return CasResult(
                    ConflictOutcome.DELIVERY_UNKNOWN, revision=current.revision
                )
            return CasResult(ConflictOutcome.APPLIED, revision=current.revision)

    # -- cleanup -------------------------------------------------------------

    async def claim_cleanup(
        self,
        *,
        cleanup_id: str,
        bridge_session_id: str,
        owner: str,
        owner_generation: int,
        host_generation: int | None = None,
        provider_session_epoch: int | None = None,
        workspace_generation: int | None = None,
        lease_generation: int | None = None,
    ) -> CasResult:
        """Claim durable cleanup authority for one session.

        Exactly one janitor holds cleanup authority. A replacement janitor with a
        strictly newer generation fences the prior owner; a janitor with an equal
        or older generation loses with a cleanup-claim conflict. Completed cleanup
        is idempotent.
        """

        async with self._session_factory() as session, session.begin():
            current = await session.scalar(
                select(OmnigentCleanupAuthority)
                .where(
                    OmnigentCleanupAuthority.bridge_session_id == bridge_session_id
                )
                .with_for_update()
            )
            generations = {
                "host_generation": host_generation,
                "provider_session_epoch": provider_session_epoch,
                "workspace_generation": workspace_generation,
                "lease_generation": lease_generation,
            }
            if current is None:
                cleanup = OmnigentCleanupAuthority(
                    cleanup_id=cleanup_id,
                    bridge_session_id=bridge_session_id,
                    status="claimed",
                    claim_owner=owner,
                    claim_generation=owner_generation,
                    claimed_at=_now(),
                    revision=1,
                    **generations,
                )
                session.add(cleanup)
                try:
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    return await self._claim_existing_cleanup(
                        bridge_session_id,
                        owner=owner,
                        owner_generation=owner_generation,
                        generations=generations,
                    )
                return CasResult(ConflictOutcome.APPLIED, revision=1)
            return self._apply_cleanup_claim(
                current,
                owner=owner,
                owner_generation=owner_generation,
                generations=generations,
            )

    async def _claim_existing_cleanup(
        self,
        bridge_session_id: str,
        *,
        owner: str,
        owner_generation: int,
        generations: Mapping[str, int | None],
    ) -> CasResult:
        async with self._session_factory() as session, session.begin():
            current = await session.scalar(
                select(OmnigentCleanupAuthority)
                .where(
                    OmnigentCleanupAuthority.bridge_session_id == bridge_session_id
                )
                .with_for_update()
            )
            if current is None:  # pragma: no cover - defensive
                raise RuntimeError("cleanup authority vanished after integrity conflict")
            return self._apply_cleanup_claim(
                current,
                owner=owner,
                owner_generation=owner_generation,
                generations=generations,
            )

    def _apply_cleanup_claim(
        self,
        current: OmnigentCleanupAuthority,
        *,
        owner: str,
        owner_generation: int,
        generations: Mapping[str, int | None],
    ) -> CasResult:
        if current.status == "completed":
            return CasResult(
                ConflictOutcome.ALREADY_APPLIED,
                revision=current.revision,
                observed=_snapshot(current),
            )
        if current.status == "claimed" and current.claim_owner == owner:
            return CasResult(
                ConflictOutcome.ALREADY_APPLIED,
                revision=current.revision,
                observed=_snapshot(current),
            )
        if owner_generation <= current.claim_generation:
            record_concurrency_event(
                ConcurrencyTelemetryEvent.CLEANUP_CLAIM_CONFLICT, surface="cleanup"
            )
            return CasResult(
                ConflictOutcome.FENCING_CONFLICT,
                revision=current.revision,
                observed=_snapshot(current),
            )
        current.status = "claimed"
        current.claim_owner = owner
        current.claim_generation = owner_generation
        current.claimed_at = _now()
        for key, value in generations.items():
            if value is not None:
                setattr(current, key, value)
        current.revision += 1
        current.updated_at = _now()
        return CasResult(ConflictOutcome.APPLIED, revision=current.revision)

    async def complete_cleanup(
        self,
        cleanup_id: str,
        *,
        owner: str,
        owner_generation: int,
        expected_revision: int,
    ) -> CasResult:
        """Mark cleanup complete, only from the current cleanup authority.

        A former janitor whose generation was superseded cannot complete cleanup
        or release resources that now belong to a replacement generation.
        """

        async with self._session_factory() as session, session.begin():
            current = await session.scalar(
                select(OmnigentCleanupAuthority)
                .where(OmnigentCleanupAuthority.cleanup_id == cleanup_id)
                .with_for_update()
            )
            if current is None:
                raise LookupError(f"unknown omnigent cleanup authority: {cleanup_id}")
            if current.status == "completed":
                return CasResult(
                    ConflictOutcome.ALREADY_APPLIED,
                    revision=current.revision,
                    observed=_snapshot(current),
                )
            if (
                current.claim_owner != owner
                or current.claim_generation != owner_generation
            ):
                record_concurrency_event(
                    ConcurrencyTelemetryEvent.CLEANUP_CLAIM_CONFLICT, surface="cleanup"
                )
                return CasResult(
                    ConflictOutcome.NOT_OWNER,
                    revision=current.revision,
                    observed=_snapshot(current),
                )
            if current.revision != expected_revision:
                _emit_outcome(ConflictOutcome.REVISION_CONFLICT, surface="cleanup")
                return CasResult(
                    ConflictOutcome.REVISION_CONFLICT,
                    revision=current.revision,
                    observed=_snapshot(current),
                )
            current.status = "completed"
            current.completed_at = _now()
            current.revision = expected_revision + 1
            current.updated_at = _now()
            return CasResult(ConflictOutcome.APPLIED, revision=current.revision)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _reject_reserved(
        values: Mapping[str, Any], reserved: set[str]
    ) -> None:
        collisions = reserved.intersection(values)
        if collisions:
            raise ValueError(
                "compare-and-swap manages "
                f"{sorted(reserved)}; callers must not set {sorted(collisions)}"
            )


__all__ = [
    "Aggregate",
    "APPLIED_OUTCOMES",
    "CasResult",
    "ConcurrencyConflict",
    "ConcurrencyTelemetryEvent",
    "ConflictOutcome",
    "OmnigentControlPlaneRepository",
    "RECONCILABLE_OUTCOMES",
    "TELEMETRY_SURFACES",
    "counter_snapshot",
    "record_concurrency_event",
    "reset_counters",
]
