"""Boundary-neutral projection of a fault run into logical commands.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7).

The pure-domain harness (:mod:`.harness`) plays a :class:`FaultPlan` against the
production reducer and records, in the independent provider ledger, every logical
side effect the run authorized. The higher test layers (PostgreSQL
repository/concurrency, Temporal, API/browser, exact-image) must replay *the same*
logical command stream against their real boundary and re-prove the same
invariants there — without re-deriving reducer semantics and without importing a
database, Temporal, HTTP, or Docker dependency into the framework.

This module is that thin seam. It turns an :class:`ExecutionTrace` into an ordered
list of :class:`ProjectedCommand` records plus the run's terminal projection. Each
projected command carries exactly the identity a durable boundary needs: the
reducer's deterministic ``command_id`` (the idempotency key), the closed command
type, a secret-safe payload digest, and — for the terminal-recording commands —
the observed terminal outcome. The projection is derived from the trace's
already-computed distinct durable commands and its independent ledger, so it
stays a faithful witness rather than a second reducer.

The module imports only the reconciler contracts, the scenario vocabulary, and the
provider digest helper; it has no infrastructure dependency, which is what lets one
projection drive every layer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from moonmind.omnigent.reconciler import DecisionKind, TerminalOutcome

from .harness import ExecutionTrace
from .provider import payload_digest
from .scenario import ResponseBehavior

#: Reducer decision kinds that record the canonical session terminal.
_TERMINAL_KINDS: frozenset[DecisionKind] = frozenset(
    {
        DecisionKind.RECORD_PROVIDER_TERMINAL,
        DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    }
)

#: The one command kind that submits a provider turn (the at-most-once identity).
_SUBMIT_KIND = DecisionKind.SUBMIT_TURN


@dataclass(frozen=True)
class ProjectedCommand:
    """One durable logical command a fault run authorized, boundary-neutral.

    ``command_id`` is the reducer's deterministic idempotency identity, reused
    verbatim as the durable idempotency key at every boundary so at-most-once can
    be proven from the boundary's own journal. ``payload_digest`` matches the
    digest the independent provider ledger recorded for the same command, so a
    replay that reuses a key with a *different* logical payload is detectable as a
    conflict rather than silently coalesced.

    The attempt disposition (``attempts``, ``crash_windows``, ``responses``,
    ``delivered``) carries the *ambiguity a boundary must replay*: how many times
    the command was attempted against the provider, at which command windows a
    process crash interrupted it, the transport response of each attempt, and
    whether any receipt was actually delivered. Projecting only the final
    successful transition would make an ``after_side_effect_before_receipt`` crash
    scenario indistinguishable from a fault-free one, so a boundary replay could
    not tell whether its retry/at-most-once handling actually survives the
    injected fault.
    """

    command_id: str
    command_type: str
    payload_digest: str
    is_submit: bool
    is_terminal: bool
    terminal_outcome: TerminalOutcome | None = None
    #: Number of provider attempts recorded under this command's idempotency key
    #: (retries after a lost response or a pre-receipt crash included).
    attempts: int = 1
    #: Command windows at which a process crash interrupted this command, in order.
    crash_windows: tuple[str, ...] = ()
    #: Transport response behavior of each attempt, in order.
    responses: tuple[str, ...] = ()
    #: Whether at least one attempt's response was actually delivered; ``False``
    #: means every attempt's receipt was lost (a drop/timeout ambiguity window).
    delivered: bool = True

    @property
    def faulted(self) -> bool:
        """Whether this command was attempted under an injected fault."""

        return bool(self.crash_windows) or self.attempts > 1 or not self.delivered


@dataclass(frozen=True)
class ProjectedRun:
    """The boundary-neutral projection of a whole fault run.

    ``commands`` is the ordered stream of durable logical commands. ``converged``,
    ``terminal_outcome``, and ``cleanup_complete`` are the terminal facts a
    boundary replay must end up agreeing with. ``keys_with_multiple_side_effects``
    is the independent ledger's at-most-once witness: it must always be empty, and
    a boundary that performs a second side effect for one key is a real bug even
    if MoonMind's own state looks consistent.
    """

    scenario_id: str | None
    commands: tuple[ProjectedCommand, ...]
    converged: bool
    terminal_outcome: TerminalOutcome | None
    cleanup_complete: bool
    ledger_multiple_side_effect_keys: tuple[str, ...] = field(default_factory=tuple)
    #: Every ``(command_id, crash_window)`` crash the run fired, in order — the
    #: authority handoffs a boundary replay must reproduce to prove its retry and
    #: at-most-once handling survives a mid-command process restart.
    crashes: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def submit_commands(self) -> tuple[ProjectedCommand, ...]:
        return tuple(c for c in self.commands if c.is_submit)

    @property
    def terminal_commands(self) -> tuple[ProjectedCommand, ...]:
        return tuple(c for c in self.commands if c.is_terminal)

    @property
    def faulted_commands(self) -> tuple[ProjectedCommand, ...]:
        """Commands the run attempted under an injected crash/lost-response fault."""

        return tuple(c for c in self.commands if c.faulted)


def project_run(trace: ExecutionTrace, *, scenario_id: str | None = None) -> ProjectedRun:
    """Project a completed :class:`ExecutionTrace` into a boundary-neutral run.

    The projection is taken from ``trace.distinct_commands`` (the durable commands
    that actually transitioned state, in order) so it never re-runs or second-
    guesses the reducer. The submit and terminal commands are tagged so a boundary
    replay knows which command carries the at-most-once turn identity and which
    records the canonical session terminal.
    """

    # Attempt journal per command id, taken from the independent provider ledger
    # and the crash log so a faulted command projects the ambiguity it authorized
    # (retries, crash windows, lost receipts), not only its final transition.
    requests_by_key: dict[str, list] = defaultdict(list)
    for request in trace.ledger.requests:
        requests_by_key[request.idempotency_key].append(request)
    crash_windows_by_key: dict[str, list[str]] = defaultdict(list)
    for crashed_command_id, window in trace.crashes_fired:
        crash_windows_by_key[crashed_command_id].append(window.value)

    commands: list[ProjectedCommand] = []
    for command_id, kind in trace.distinct_commands:
        is_terminal = kind in _TERMINAL_KINDS
        requests = requests_by_key.get(command_id, [])
        responses = tuple(request.response.value for request in requests)
        delivered = (
            any(request.response == ResponseBehavior.SUCCESS for request in requests)
            if requests
            else True
        )
        commands.append(
            ProjectedCommand(
                command_id=command_id,
                command_type=kind.value,
                # Mirrors the payload the harness handed the provider ledger for
                # this command (``{"kind": <decision>}``) so the durable
                # idempotency digest lines up with the independent witness.
                payload_digest=payload_digest({"kind": kind.value}),
                is_submit=kind == _SUBMIT_KIND,
                is_terminal=is_terminal,
                terminal_outcome=trace.final.terminal_outcome if is_terminal else None,
                attempts=len(requests) or 1,
                crash_windows=tuple(crash_windows_by_key.get(command_id, ())),
                responses=responses,
                delivered=delivered,
            )
        )

    return ProjectedRun(
        scenario_id=scenario_id,
        commands=tuple(commands),
        converged=trace.converged,
        terminal_outcome=trace.final.terminal_outcome,
        cleanup_complete=trace.final.cleanup_complete,
        ledger_multiple_side_effect_keys=tuple(
            trace.ledger.keys_with_multiple_side_effects()
        ),
        crashes=tuple(
            (command_id, window.value) for command_id, window in trace.crashes_fired
        ),
    )


__all__ = [
    "ProjectedCommand",
    "ProjectedRun",
    "project_run",
]
