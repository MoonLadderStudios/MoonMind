"""Programmable, recording fake Omnigent provider and host.

Owned by MoonLadderStudios/MoonMind#3709.

The fake is a deterministic scenario engine usable by unit, component, Temporal,
integration, and browser tests. It models a *well-behaved-but-faulty* provider:
it enforces provider-side idempotency (a repeated turn idempotency identity
commits at most one accepted side effect) and durable-store fencing (a
stale-generation write is rejected) while still injecting dropped, delayed,
duplicated, reordered, ambiguous, and contradictory behavior as scripted.

Every request, side effect, logical idempotency key, payload digest, response,
and observation is captured through :class:`~moonmind.omnigent.faultkit.recording.ProviderRecorder`
so tests assert at-most-once behavior independently of MoonMind's own state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from moonmind.omnigent.faultkit.commands import (
    COMMAND_WINDOWS,
    CommandWindow,
    CommandWindowCrash,
    LogicalCommand,
)
from moonmind.omnigent.faultkit.injectors import FaultInjector, InfraFault
from moonmind.omnigent.faultkit.recording import ProviderRecorder
from moonmind.omnigent.faultkit.scenario import (
    ResponseMode,
    Scenario,
    ScenarioStep,
    SideEffectKind,
)


@dataclass
class CommandOutcome:
    """The observable result of running one logical command against the fake."""

    command: LogicalCommand
    response_mode: ResponseMode
    delivered: bool
    side_effect: SideEffectKind = SideEffectKind.NONE
    events: tuple[dict[str, Any], ...] = ()
    snapshot: dict[str, Any] | None = None
    disconnected: bool = False
    infra_fault: InfraFault | None = None
    crashed_window: CommandWindow | None = None
    fenced: bool = False
    error: str | None = None
    generation: int = 0

    @property
    def crashed(self) -> bool:
        return self.crashed_window is not None

    @property
    def response_lost(self) -> bool:
        """A side effect committed but no receipt reached the caller."""
        return (
            self.side_effect is not SideEffectKind.NONE and not self.delivered
        ) or (self.side_effect is not SideEffectKind.NONE and self.crashed)


class ProgrammableOmnigentProvider:
    """A deterministic fake Omnigent provider/host driven by a scenario."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        recorder: ProviderRecorder | None = None,
        injector: FaultInjector | None = None,
    ) -> None:
        scenario.require_executable()
        self.scenario = scenario
        self.recorder = recorder or ProviderRecorder()
        self.injector = injector or FaultInjector()
        # Durable provider state.
        self.session_exists = False
        self.session_deleted = False
        # Generations are 1-based; a step generation of 0 means "current".
        self.generation = 1
        self._accepted_turn_keys: set[str] = set()
        self._event_log: list[dict[str, Any]] = []
        self._cursor = 0
        self._snapshot: dict[str, Any] = {
            "sessionState": "absent",
            "turnState": "none",
            "unfinishedToolCalls": 0,
        }

    # -- scenario tape ---------------------------------------------------------

    def steps_for(self, scenario: Scenario) -> list[ScenarioStep]:
        return list(scenario.steps)

    # -- execution -------------------------------------------------------------

    def execute(
        self,
        step: ScenarioStep,
        *,
        idempotency_key: str | None = None,
        payload: Any = None,
    ) -> CommandOutcome:
        """Run one logical command as scripted by ``step`` and record evidence."""
        command = step.on
        payload = payload if payload is not None else {"command": command.value}
        req_generation = step.generation or self.generation
        self.recorder.record_request(
            command=command,
            payload=payload,
            idempotency_key=idempotency_key,
            generation=req_generation,
        )

        outcome = CommandOutcome(
            command=command,
            response_mode=step.response,
            delivered=False,
            generation=req_generation,
            infra_fault=self.injector.infra_fault(step),
        )

        try:
            self.injector.maybe_crash(command, CommandWindow.BEFORE_CLAIM, step)
            # claim
            self.injector.maybe_crash(
                command, CommandWindow.AFTER_CLAIM_BEFORE_SIDE_EFFECT, step
            )
            self._commit_side_effect(step, idempotency_key, req_generation, outcome)
            self.injector.maybe_crash(
                command, CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT, step
            )
            self._build_receipt(step, command, outcome)
            self.injector.maybe_crash(
                command, CommandWindow.AFTER_RECEIPT_BEFORE_STATE_TRANSITION, step
            )
            self.injector.maybe_crash(
                command,
                CommandWindow.AFTER_TRANSITION_BEFORE_ACTIVITY_RESPONSE,
                step,
            )
            self._deliver(step, command, outcome)
        except CommandWindowCrash as crash:
            outcome.crashed_window = crash.window
            outcome.delivered = False
            self.recorder.record_response(
                command=command, mode=step.response, delivered=False
            )
            return outcome

        return outcome

    # -- phases ----------------------------------------------------------------

    def _commit_side_effect(
        self,
        step: ScenarioStep,
        idempotency_key: str | None,
        generation: int,
        outcome: CommandOutcome,
    ) -> None:
        kind = step.side_effect
        if kind is SideEffectKind.NONE:
            return

        # Durable-store fencing: reject stale-generation writes.
        if generation < self.generation and step.on in {
            LogicalCommand.SUBMIT_TURN,
            LogicalCommand.DELETE_SESSION,
            LogicalCommand.WORKSPACE_PUBLISH,
            LogicalCommand.CLEANUP,
        }:
            outcome.fenced = True
            return

        if kind is SideEffectKind.ACCEPTED:
            # Provider-side idempotency: at most one accepted side effect per key.
            if idempotency_key is not None and idempotency_key in self._accepted_turn_keys:
                return
            if idempotency_key is not None:
                self._accepted_turn_keys.add(idempotency_key)
            self._snapshot["turnState"] = "running"
            self._snapshot["sessionState"] = "running"
        elif kind is SideEffectKind.CREATED:
            self.session_exists = True
            self._snapshot["sessionState"] = "idle"
        elif kind is SideEffectKind.ATTACHED:
            self.session_exists = True
        elif kind is SideEffectKind.DELETED:
            self.session_deleted = True
            self.session_exists = False
        elif kind is SideEffectKind.REPLACED:
            self.generation += 1

        outcome.side_effect = kind
        self.recorder.record_side_effect(
            command=step.on,
            kind=kind,
            idempotency_key=idempotency_key,
            generation=generation,
        )

    def _build_receipt(
        self, step: ScenarioStep, command: LogicalCommand, outcome: CommandOutcome
    ) -> None:
        if command is LogicalCommand.READ_EVENTS:
            outcome.events = self._read_events(step)
            outcome.disconnected = step.disconnect
            self.recorder.record_observation(
                command=command,
                kind="events",
                frontier=self._cursor,
                payload=list(outcome.events),
            )
        elif command in {LogicalCommand.OBSERVE_SNAPSHOT, LogicalCommand.RECONCILE}:
            snapshot = self._observe_snapshot(step)
            outcome.snapshot = snapshot
            self.recorder.record_observation(
                command=command,
                kind="snapshot",
                frontier=self._cursor,
                payload=snapshot,
            )

    def _deliver(
        self, step: ScenarioStep, command: LogicalCommand, outcome: CommandOutcome
    ) -> None:
        mode = step.response
        if mode is ResponseMode.SUCCESS:
            outcome.delivered = True
        elif mode is ResponseMode.DROP:
            outcome.delivered = False  # side effect committed; receipt lost
        elif mode is ResponseMode.TIMEOUT:
            outcome.delivered = False
            outcome.error = "timeout"
        elif mode is ResponseMode.ERROR:
            outcome.delivered = False
            outcome.error = "provider_error"
        elif mode is ResponseMode.AUTH_FAILURE:
            outcome.delivered = False
            outcome.error = "auth_failure"
        elif mode is ResponseMode.MALFORMED:
            outcome.delivered = True
            outcome.error = "malformed"
            outcome.snapshot = None
        elif mode is ResponseMode.UNKNOWN_SCHEMA:
            outcome.delivered = True
            outcome.error = "unknown_schema"
        # A read whose response never reached the caller yields no observation to
        # the caller, even though the provider recorded what it emitted.
        if not outcome.delivered and command in {
            LogicalCommand.READ_EVENTS,
            LogicalCommand.OBSERVE_SNAPSHOT,
        }:
            outcome.events = ()
            outcome.snapshot = None
        self.recorder.record_response(
            command=command, mode=mode, delivered=outcome.delivered
        )

    # -- event/snapshot helpers ------------------------------------------------

    def _read_events(self, step: ScenarioStep) -> tuple[dict[str, Any], ...]:
        for event in step.emit:
            self._event_log.append(dict(event))
        batch = self._event_log[self._cursor :]
        # Only advance the cursor when the stream did not disconnect mid-batch.
        if not step.disconnect:
            self._cursor = len(self._event_log)
        else:
            # A disconnect leaves a replay gap: advance past only the first event.
            if batch:
                self._cursor += 1
        result = list(batch)
        if step.reorder and len(result) >= 2:
            result[0], result[-1] = result[-1], result[0]
        if step.duplicate and result:
            result = result + [dict(result[-1])]
        return tuple(result)

    def _observe_snapshot(self, step: ScenarioStep) -> dict[str, Any]:
        if step.snapshot is not None:
            self._snapshot.update(step.snapshot)
        return dict(self._snapshot)

    # -- introspection ---------------------------------------------------------

    @property
    def accepted_turn_count(self) -> int:
        return len(self._accepted_turn_keys)


__all__ = [
    "ProgrammableOmnigentProvider",
    "CommandOutcome",
]
