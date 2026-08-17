"""Drives the reconciler, fake provider, and reference model over a scenario.

Owned by MoonLadderStudios/MoonMind#3709.

The harness plays a declarative scenario's steps in order. For each step it lets
the reconciler authorize the command, runs the (authorized) command against the
programmable fake provider, folds the observed outcome back into the reconciler,
and advances the independent reference-model oracle from the provider's
authoritative ground truth. The run is fully deterministic: the same scenario
(and seed) produces the same decisions and observations every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from moonmind.omnigent.faultkit.commands import LogicalCommand
from moonmind.omnigent.faultkit.fake_provider import (
    CommandOutcome,
    ProgrammableOmnigentProvider,
)
from moonmind.omnigent.faultkit.reconciler import Decision, FaultKitReconciler
from moonmind.omnigent.faultkit.recording import ProviderRecorder
from moonmind.omnigent.faultkit.reference_model import ReferenceModel, SessionView
from moonmind.omnigent.faultkit.scenario import (
    Scenario,
    ScenarioStep,
    SideEffectKind,
)

_SKIP_DECISIONS = {
    Decision.SKIP_IDEMPOTENT,
    Decision.SKIP_FENCED,
    Decision.SKIP_LEASE_HELD,
    Decision.SKIP_CLEANUP_GUARD,
}


def _turn_key(step: ScenarioStep) -> str | None:
    if step.on in {LogicalCommand.SUBMIT_TURN}:
        return f"turn:{step.turn}"
    return None


@dataclass
class RunResult:
    """Everything a test or a diagnostic bundle needs from one scenario run."""

    scenario: Scenario
    recorder: ProviderRecorder
    reconciler: FaultKitReconciler
    reference_view: SessionView
    outcomes: list[CommandOutcome] = field(default_factory=list)

    @property
    def reconciler_view(self) -> SessionView:
        return self.reconciler.view

    def decision_journal(self) -> list[dict[str, Any]]:
        return [
            {
                "stepIndex": entry.step_index,
                "command": entry.command.value,
                "decision": entry.decision.value,
                "note": entry.note,
            }
            for entry in self.reconciler.journal
        ]


def run_scenario(
    scenario: Scenario,
    *,
    recorder: ProviderRecorder | None = None,
    reconciler_factory: Callable[[], FaultKitReconciler] = FaultKitReconciler,
) -> RunResult:
    """Execute a scenario end to end and return a :class:`RunResult`.

    ``reconciler_factory`` lets tests substitute a deliberately broken reconciler
    to prove the invariant suite detects regressions.
    """
    scenario.require_executable()
    recorder = recorder or ProviderRecorder()
    provider = ProgrammableOmnigentProvider(scenario, recorder=recorder)
    reconciler = reconciler_factory()
    reference = ReferenceModel()
    outcomes: list[CommandOutcome] = []

    for index, step in enumerate(scenario.steps):
        key = _turn_key(step)
        decision = reconciler.decide(index, step, key)
        if decision in _SKIP_DECISIONS:
            continue
        outcome = provider.execute(step, idempotency_key=key)
        outcomes.append(outcome)
        reconciler.apply(step, outcome, key)
        _advance_reference(reference, outcome)

    return RunResult(
        scenario=scenario,
        recorder=recorder,
        reconciler=reconciler,
        reference_view=reference.finalize(),
        outcomes=outcomes,
    )


def _advance_reference(reference: ReferenceModel, outcome: CommandOutcome) -> None:
    """Advance the oracle only from authoritative provider ground truth."""
    if outcome.fenced:
        return
    if outcome.side_effect is SideEffectKind.CREATED:
        reference.observe_created_truth()
    elif outcome.side_effect is SideEffectKind.ACCEPTED:
        reference.observe_accept_truth()
    elif outcome.side_effect is SideEffectKind.DELETED:
        reference.observe_deleted_truth()
    if outcome.snapshot is not None:
        reference.observe_snapshot_truth(outcome.snapshot)
    for event in outcome.events:
        etype = str(event.get("type", ""))
        if etype in {"completed", "response.completed", "turn.completed"}:
            reference.observe_snapshot_truth({"turnState": "completed"})
        elif etype in {"failed", "response.failed", "turn.failed"}:
            reference.observe_snapshot_truth({"turnState": "failed"})


__all__ = ["RunResult", "run_scenario"]
