"""Compact reference state machine for the Omnigent lifecycle.

Source issue: MoonLadderStudios/MoonMind#3709.

This is a deliberately independent oracle. It does **not** import or call the
production reconciler (``moonmind.omnigent.reconciler.reconcile``) or any
production repository/adapter. It re-derives, by hand, the legal lifecycle
ordering so a generated run can be compared against a second, differently written
model: final state, the set of emitted logical commands, and any invariant
violation. If the reference simply called the reducer it would prove nothing, so
its transition rules are written from the canonical lifecycle description, not
copied from the reducer.

Modelled facets (from the issue):

* canonical session lifecycle;
* turn-attempt lifecycle;
* command delivery state;
* observation frontier (via the harness, not here);
* host and profile lease generations;
* cleanup state;
* allowed terminal and recovery transitions.
"""

from __future__ import annotations

from enum import Enum


class IllegalTransitionError(AssertionError):
    """Raised when a command is applied in a state the lifecycle forbids.

    Because the reference model is independent, an illegal transition means the
    production reconciler emitted a command the canonical lifecycle does not
    allow (out of order, duplicated logical command, or after a terminal), which
    is itself an invariant violation.
    """


class ReferenceCommand(str, Enum):
    """The durable side-effect commands the lifecycle can take.

    ``RECORD_TERMINAL`` covers both the reducer's ``record_provider_terminal``
    and ``synthesize_terminal_from_snapshot`` — from the lifecycle's point of
    view both durably record the one canonical terminal.
    """

    ENSURE_PROFILE_LEASE = "ensure_profile_lease"
    ENSURE_HOST = "ensure_host"
    ENSURE_SESSION = "ensure_session"
    SUBMIT_TURN = "submit_turn"
    RECORD_TERMINAL = "record_terminal"
    HARVEST_EVIDENCE = "harvest_evidence"
    BEGIN_CLEANUP = "begin_cleanup"
    RELEASE_LEASES = "release_leases"


class ReferencePhase(str, Enum):
    """Coarse lifecycle phase tracked independently of the reducer's phase enum."""

    INITIALIZING = "initializing"
    PROFILE_LEASE_HELD = "profile_lease_held"
    HOST_READY = "host_ready"
    SESSION_READY = "session_ready"
    TURN_IN_FLIGHT = "turn_in_flight"
    TERMINAL_RECORDED = "terminal_recorded"
    EVIDENCE_HARVESTED = "evidence_harvested"
    CLEANUP_DONE = "cleanup_done"
    CLOSED = "closed"


class ReferenceModel:
    """A hand-written lifecycle oracle for one session.

    ``desired_cancel`` records a cancelled terminal directly from initialization,
    matching the canonical rule that a cancellation is recorded before any turn is
    submitted. ``ground_truth_terminal`` is the terminal the provider actually
    reached for a normal run.
    """

    def __init__(
        self,
        *,
        requires_profile_lease: bool = True,
        requires_host: bool = True,
        requires_cleanup: bool = True,
        desired_cancel: bool = False,
        ground_truth_terminal: str = "success",
    ) -> None:
        self.requires_profile_lease = requires_profile_lease
        self.requires_host = requires_host
        self.requires_cleanup = requires_cleanup
        self.desired_cancel = desired_cancel
        self.ground_truth_terminal = ground_truth_terminal

        self.profile_lease_held = False
        self.host_lease_held = False
        self.session_attached = False
        self.submitted = False
        self.submit_count = 0
        self.terminal_outcome: str | None = None
        self.evidence_harvested = False
        self.cleanup_complete = False
        self.leases_released = False

    # -- expected happy-path oracle --------------------------------------

    def expected_command_sequence(self) -> tuple[ReferenceCommand, ...]:
        """The canonical ordered logical commands for a healthy run.

        Used to assert the reconciler emits exactly this set, in this order, with
        no duplicates and nothing extra, on a fault-free scenario.
        """

        seq: list[ReferenceCommand] = []
        if self.desired_cancel:
            seq.append(ReferenceCommand.RECORD_TERMINAL)
        else:
            if self.requires_profile_lease:
                seq.append(ReferenceCommand.ENSURE_PROFILE_LEASE)
            if self.requires_host:
                seq.append(ReferenceCommand.ENSURE_HOST)
            seq.append(ReferenceCommand.ENSURE_SESSION)
            seq.append(ReferenceCommand.SUBMIT_TURN)
            seq.append(ReferenceCommand.RECORD_TERMINAL)
        seq.append(ReferenceCommand.HARVEST_EVIDENCE)
        if self.requires_cleanup:
            seq.append(ReferenceCommand.BEGIN_CLEANUP)
        if self._leases_acquirable():
            seq.append(ReferenceCommand.RELEASE_LEASES)
        return tuple(seq)

    def _leases_acquirable(self) -> bool:
        # On desired cancellation the terminal is recorded before any lease is
        # acquired, so no release command is expected.
        if self.desired_cancel:
            return False
        return self.requires_profile_lease or self.requires_host

    # -- transition machine ----------------------------------------------

    def apply(self, command: ReferenceCommand) -> None:
        """Advance the model by one durable command, or reject it.

        Each rule is written from the canonical ordering, independent of the
        reducer implementation.
        """

        handler = {
            ReferenceCommand.ENSURE_PROFILE_LEASE: self._ensure_profile_lease,
            ReferenceCommand.ENSURE_HOST: self._ensure_host,
            ReferenceCommand.ENSURE_SESSION: self._ensure_session,
            ReferenceCommand.SUBMIT_TURN: self._submit_turn,
            ReferenceCommand.RECORD_TERMINAL: self._record_terminal,
            ReferenceCommand.HARVEST_EVIDENCE: self._harvest_evidence,
            ReferenceCommand.BEGIN_CLEANUP: self._begin_cleanup,
            ReferenceCommand.RELEASE_LEASES: self._release_leases,
        }[command]
        handler()

    def _reject(self, message: str) -> None:
        raise IllegalTransitionError(message)

    def _ensure_profile_lease(self) -> None:
        if not self.requires_profile_lease:
            self._reject("profile lease not required")
        if self.terminal_outcome is not None:
            self._reject("lease acquisition after terminal")
        if self.profile_lease_held:
            self._reject("profile lease already held")
        self.profile_lease_held = True

    def _ensure_host(self) -> None:
        if not self.requires_host:
            self._reject("host not required")
        if self.terminal_outcome is not None:
            self._reject("host acquisition after terminal")
        if self.requires_profile_lease and not self.profile_lease_held:
            self._reject("host before profile lease")
        if self.host_lease_held:
            self._reject("host already held")
        self.host_lease_held = True

    def _ensure_session(self) -> None:
        if self.terminal_outcome is not None:
            self._reject("session attach after terminal")
        if self.requires_profile_lease and not self.profile_lease_held:
            self._reject("session before profile lease")
        if self.requires_host and not self.host_lease_held:
            self._reject("session before host")
        if self.session_attached:
            self._reject("session already attached")
        self.session_attached = True

    def _submit_turn(self) -> None:
        if not self.session_attached:
            self._reject("submit before session attach")
        if self.terminal_outcome is not None:
            self._reject("submit after terminal")
        if self.submitted:
            # At-most-once logical submission is enforced here independently of
            # the ledger: a second distinct submit command is illegal.
            self._reject("duplicate turn submission")
        self.submitted = True
        self.submit_count += 1

    def _record_terminal(self) -> None:
        if self.terminal_outcome is not None:
            self._reject("terminal already recorded")
        if self.desired_cancel:
            self.terminal_outcome = "cancelled"
        else:
            if not self.submitted:
                self._reject("terminal before submission")
            self.terminal_outcome = self.ground_truth_terminal

    def _harvest_evidence(self) -> None:
        if self.terminal_outcome is None:
            self._reject("harvest before terminal")
        if self.evidence_harvested:
            self._reject("evidence already harvested")
        self.evidence_harvested = True

    def _begin_cleanup(self) -> None:
        if not self.requires_cleanup:
            self._reject("cleanup not required")
        if self.terminal_outcome is None:
            self._reject("cleanup before terminal")
        if not self.evidence_harvested:
            # Historical-read safety: evidence must be harvested before cleanup
            # can remove the authoritative workspace.
            self._reject("cleanup before evidence harvest")
        if self.cleanup_complete:
            self._reject("cleanup already complete")
        self.cleanup_complete = True

    def _release_leases(self) -> None:
        if self.terminal_outcome is None:
            self._reject("release before terminal")
        if self.requires_cleanup and not self.cleanup_complete:
            self._reject("release before cleanup complete")
        if self.leases_released:
            self._reject("leases already released")
        if not (self.profile_lease_held or self.host_lease_held):
            self._reject("release with no held lease")
        self.leases_released = True

    # -- terminal predicates ---------------------------------------------

    def is_closed(self) -> bool:
        """Whether the reference reached a fully settled, closed lifecycle."""

        if self.terminal_outcome is None:
            return False
        if not self.evidence_harvested:
            return False
        if self.requires_cleanup and not self.cleanup_complete:
            return False
        if self._leases_acquirable() and not self.leases_released:
            return False
        return True

    def final_phase(self) -> ReferencePhase:
        if self.is_closed():
            return ReferencePhase.CLOSED
        if self.leases_released or self.cleanup_complete:
            return ReferencePhase.CLEANUP_DONE
        if self.evidence_harvested:
            return ReferencePhase.EVIDENCE_HARVESTED
        if self.terminal_outcome is not None:
            return ReferencePhase.TERMINAL_RECORDED
        if self.submitted:
            return ReferencePhase.TURN_IN_FLIGHT
        if self.session_attached:
            return ReferencePhase.SESSION_READY
        if self.host_lease_held:
            return ReferencePhase.HOST_READY
        if self.profile_lease_held:
            return ReferencePhase.PROFILE_LEASE_HELD
        return ReferencePhase.INITIALIZING


__all__ = [
    "IllegalTransitionError",
    "ReferenceCommand",
    "ReferencePhase",
    "ReferenceModel",
]
