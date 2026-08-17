"""The reconciler under test: MoonMind's Omnigent reconciliation policy.

Owned by MoonLadderStudios/MoonMind#3709.

This is a compact, in-process implementation of the reconciliation contract the
Omnigent control plane must honor. It is the system under test: generated action
sequences drive it alongside the independent
:class:`~moonmind.omnigent.faultkit.reference_model.ReferenceModel` oracle, and
the two are compared.

The reconciler owns every recovery decision. Given the outcome of a logical
command (which may be a dropped response, a fenced write, a stale snapshot, or a
duplicated event) it decides whether to advance state, reconcile, quarantine, or
refuse -- it never blindly retries an ambiguous submission and never lets a
former generation mutate current authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from moonmind.omnigent.faultkit.commands import LogicalCommand
from moonmind.omnigent.faultkit.fake_provider import CommandOutcome
from moonmind.omnigent.faultkit.reference_model import (
    CleanupState,
    SessionState,
    SessionView,
    TerminalKind,
    TurnState,
    turn_transition_allowed,
)
from moonmind.omnigent.faultkit.scenario import (
    ResponseMode,
    ScenarioStep,
    SideEffectKind,
)


class Decision(str, Enum):
    """The reconciler's authorization for a step before it touches the provider."""

    PROCEED = "proceed"
    SKIP_IDEMPOTENT = "skip_reconcile_existing_submission"
    SKIP_FENCED = "skip_fenced_former_generation"
    SKIP_LEASE_HELD = "skip_lease_release_consumer_present"
    SKIP_CLEANUP_GUARD = "skip_cleanup_replacement_generation"


class TurnStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    AMBIGUOUS = "ambiguous"
    ACCEPTED = "accepted"
    TERMINAL = "terminal"


@dataclass
class JournalEntry:
    step_index: int
    command: LogicalCommand
    decision: Decision
    note: str = ""


@dataclass
class FaultKitReconciler:
    """In-process reconciler implementing MoonMind's reliability contract."""

    view: SessionView = field(default_factory=SessionView)
    journal: list[JournalEntry] = field(default_factory=list)
    # Generations are 1-based; a step generation of 0 means "current".
    generation: int = 1
    frontier: int = 0
    lease_consumers: int = 0
    cleanup_state: CleanupState = CleanupState.NONE
    #: idempotency identity -> number of *authorized* fresh submissions.
    authorized_submissions: dict[str, int] = field(default_factory=dict)
    #: idempotency identity -> times the reconciler authorized a submit while the
    #: identity was already active (a blind ambiguity retry). Counted at the
    #: authorization boundary so it is independent of provider-side dedup.
    blind_resubmissions: dict[str, int] = field(default_factory=dict)
    turn_status: TurnStatus = TurnStatus.NONE
    current_turn_key: str | None = None
    quarantined_observations: int = 0
    #: Durable terminal evidence, retained independently of live resources.
    terminal_evidence: dict[str, Any] | None = None
    #: (session_revision, turn_revision) captured after each applied command, so
    #: monotonicity can be verified across the whole run.
    revision_history: list[tuple[int, int]] = field(default_factory=list)

    # -- authorization ---------------------------------------------------------

    def decide(self, step_index: int, step: ScenarioStep, idempotency_key: str | None) -> Decision:
        """Authorize (or refuse) a step before it hits the provider."""
        command = step.on
        decision = self._authorize(step, idempotency_key)
        if command is LogicalCommand.SUBMIT_TURN and decision is Decision.PROCEED:
            key = idempotency_key or ""
            # A submit authorized while this identity is already active is a blind
            # ambiguity retry -- the property the correct reconciler must avoid.
            if (
                self.turn_status
                in {TurnStatus.AMBIGUOUS, TurnStatus.ACCEPTED, TurnStatus.TERMINAL}
                and self.current_turn_key == key
            ):
                self.blind_resubmissions[key] = self.blind_resubmissions.get(key, 0) + 1
            self.authorized_submissions[key] = (
                self.authorized_submissions.get(key, 0) + 1
            )
        self.journal.append(
            JournalEntry(step_index=step_index, command=command, decision=decision)
        )
        return decision

    def _authorize(self, step: ScenarioStep, idempotency_key: str | None) -> Decision:
        command = step.on
        if command is LogicalCommand.SUBMIT_TURN:
            key = idempotency_key or ""
            # No blind ambiguity retry: an accepted/terminal/ambiguous identity
            # is reconciled, never re-submitted.
            if self.turn_status in {
                TurnStatus.ACCEPTED,
                TurnStatus.TERMINAL,
                TurnStatus.AMBIGUOUS,
            } and self.current_turn_key == key:
                return Decision.SKIP_IDEMPOTENT
            return Decision.PROCEED
        if command is LogicalCommand.LEASE_RELEASE:
            # Lease safety: never release capacity while a consumer remains.
            if self.lease_consumers > 0:
                return Decision.SKIP_LEASE_HELD
            return Decision.PROCEED
        if command is LogicalCommand.CLEANUP:
            # Cleanup safety: never delete replacement-generation resources.
            if step.generation and step.generation > self.generation:
                return Decision.SKIP_CLEANUP_GUARD
            return Decision.PROCEED
        # Fencing safety: a former generation never mutates current authority.
        if step.generation and step.generation < self.generation and command in {
            LogicalCommand.SUBMIT_TURN,
            LogicalCommand.WORKSPACE_PUBLISH,
            LogicalCommand.DELETE_SESSION,
        }:
            return Decision.SKIP_FENCED
        return Decision.PROCEED

    # -- application -----------------------------------------------------------

    def apply(
        self,
        step: ScenarioStep,
        outcome: CommandOutcome,
        idempotency_key: str | None,
    ) -> None:
        """Fold the observed outcome into durable state."""
        # Compatibility safety: quarantine unknown/malformed observations.
        if outcome.response_mode in {ResponseMode.UNKNOWN_SCHEMA, ResponseMode.MALFORMED}:
            self.quarantined_observations += 1
            self._note("quarantined_unknown_or_malformed_observation")
            return
        if outcome.fenced:
            self._note("rejected_fenced_write")
            return

        command = step.on
        handler = getattr(self, f"_apply_{command.value}", None)
        if handler is not None:
            handler(step, outcome, idempotency_key)
        self.revision_history.append(
            (self.view.session_revision, self.view.turn_revision)
        )

    # -- per-command handlers --------------------------------------------------

    def _apply_ensure_session(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        if outcome.side_effect is SideEffectKind.CREATED:
            self._advance_session(SessionState.IDLE)
        elif not outcome.delivered:
            # Side effect may exist though response was lost; reconcile later.
            self._note("session_create_ambiguous")

    _apply_attach_session = _apply_ensure_session

    def _apply_submit_turn(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        key = key or ""
        if outcome.side_effect is SideEffectKind.ACCEPTED:
            self.current_turn_key = key
            if outcome.delivered:
                self.turn_status = TurnStatus.ACCEPTED
                self._advance_turn(TurnState.ACCEPTED)
                self._advance_session(SessionState.RUNNING)
            else:
                # Provider accepted but the receipt was lost: ambiguous, must be
                # reconciled from evidence -- not blindly re-submitted.
                self.turn_status = TurnStatus.AMBIGUOUS
                self._note("submit_response_lost_pending_reconcile")
        else:
            # No side effect committed; a later submission is legitimate.
            self.turn_status = TurnStatus.NONE
            self._note("submit_no_side_effect")

    def _apply_read_events(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        # Frontier is monotonic; duplicates and reorders never move it backward.
        self.frontier += self._new_event_count(outcome)
        for event in outcome.events:
            etype = str(event.get("type", ""))
            if etype in {"completed", "response.completed", "turn.completed"}:
                self._converge_terminal(TurnState.COMPLETED)
            elif etype in {"failed", "response.failed", "turn.failed"}:
                self._converge_terminal(TurnState.FAILED)

    def _apply_observe_snapshot(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        snapshot = outcome.snapshot or {}
        turn = str(snapshot.get("turnState", ""))
        # Monotonic authority: a stale snapshot never rolls a terminal back.
        if self.turn_status is TurnStatus.TERMINAL:
            if turn not in {"completed", "failed"}:
                self._note("ignored_stale_snapshot_after_terminal")
            return
        if turn == "completed":
            self._converge_terminal(TurnState.COMPLETED)
        elif turn == "failed":
            self._converge_terminal(TurnState.FAILED)
        elif turn == "running":
            if self.turn_status in {TurnStatus.AMBIGUOUS, TurnStatus.ACCEPTED}:
                self.turn_status = TurnStatus.ACCEPTED
                self._advance_turn(TurnState.RUNNING)

    def _apply_reconcile(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        snapshot = outcome.snapshot or {}
        turn = str(snapshot.get("turnState", ""))
        if self.turn_status is TurnStatus.AMBIGUOUS:
            if turn == "completed":
                self._converge_terminal(TurnState.COMPLETED)
                self._note("reconciled_ambiguous_from_snapshot")
            elif turn == "failed":
                self._converge_terminal(TurnState.FAILED)
                self._note("reconciled_ambiguous_from_snapshot")
            elif turn in {"running", "accepted"}:
                self.turn_status = TurnStatus.ACCEPTED
                self._advance_turn(TurnState.ACCEPTED)
                self._note("reconciled_ambiguous_accepted")
            else:
                self._note("reconcile_awaiting_evidence")

    def _apply_host_replace(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        self.generation += 1
        self._note(f"host_replaced_generation_{self.generation}")

    def _apply_lease_replace(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        self.generation += 1
        self._note(f"lease_replaced_generation_{self.generation}")

    def _apply_lease_acquire(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        self.lease_consumers += 1
        self.view.lease_held = True
        self.view.lease_consumers = self.lease_consumers

    def _apply_lease_release(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        if self.lease_consumers > 0:
            self.lease_consumers -= 1
        if self.lease_consumers == 0:
            self.view.lease_held = False
        self.view.lease_consumers = self.lease_consumers

    def _apply_lease_expire(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        # Lease safety: expiry must not release capacity under an active consumer.
        if self.lease_consumers > 0:
            self._note("lease_expiry_deferred_consumer_present")
        else:
            self.view.lease_held = False

    def _apply_cleanup(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        # Retain terminal evidence; cleanup removes live resources only.
        self._retain_terminal_evidence()
        self.cleanup_state = CleanupState.DONE
        self.view.cleanup_state = CleanupState.DONE

    def _apply_delete_session(self, step, outcome, key) -> None:  # type: ignore[no-untyped-def]
        self._retain_terminal_evidence()

    # -- state helpers ---------------------------------------------------------

    def _new_event_count(self, outcome: CommandOutcome) -> int:
        seen: set[str] = set()
        count = 0
        for event in outcome.events:
            ident = str(event.get("id") or event.get("type") or "")
            if ident not in seen:
                seen.add(ident)
                count += 1
        return count

    def _advance_session(self, nxt: SessionState) -> None:
        order = [
            SessionState.ABSENT,
            SessionState.CREATED,
            SessionState.IDLE,
            SessionState.RUNNING,
            SessionState.TERMINAL,
        ]
        if self.view.session_state is SessionState.TERMINAL:
            return
        # Monotonic revision on any real advance.
        if nxt != self.view.session_state:
            self.view.session_state = nxt
            self.view.session_revision += 1

    def _advance_turn(self, nxt: TurnState) -> None:
        if turn_transition_allowed(self.view.turn_state, nxt) and nxt != self.view.turn_state:
            self.view.turn_state = nxt
            self.view.turn_revision += 1

    def _converge_terminal(self, terminal: TurnState) -> None:
        if self.turn_status is TurnStatus.TERMINAL:
            return
        # An ambiguous/never-advanced turn must pass through ACCEPTED first so the
        # forward-only transition table stays honored.
        if self.view.turn_state is TurnState.NONE:
            self._advance_turn(TurnState.ACCEPTED)
        self._advance_turn(terminal)
        self.turn_status = TurnStatus.TERMINAL
        self.view.session_state = SessionState.TERMINAL
        self.view.terminal_kind = TerminalKind.SESSION
        self.view.terminal_evidence_retained = True
        self.view.session_revision += 1
        self._retain_terminal_evidence()

    def _retain_terminal_evidence(self) -> None:
        if self.terminal_evidence is None and self.view.turn_state in {
            TurnState.COMPLETED,
            TurnState.FAILED,
        }:
            self.terminal_evidence = {
                "turnState": self.view.turn_state.value,
                "turnRevision": self.view.turn_revision,
                "terminalKind": self.view.terminal_kind.value,
            }
        self.view.terminal_evidence_retained = (
            self.view.terminal_evidence_retained or self.terminal_evidence is not None
        )

    def _note(self, note: str) -> None:
        if self.journal:
            self.journal[-1].note = note


__all__ = ["FaultKitReconciler", "Decision", "TurnStatus", "JournalEntry"]
