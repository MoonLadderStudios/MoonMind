"""Initial escaped-incident corpus and the incident-ingestion workflow.

Source issue: MoonLadderStudios/MoonMind#3709.

Each corpus entry generalizes a real escaped reliability incident into a stable
invariant plus a minimized declarative fault scenario, with the source
incident/PR reference, the expected outcome, and an operational signal that would
detect the failure class. New incidents are ingested through
:func:`ingest_incident`, which minimizes a failing plan and stores a safe,
bounded scenario under the reliability replay corpus.

The pure-domain corpus here covers the incident classes that are expressible
against the canonical lifecycle. UI, WebSocket-transport, and exact-image
incidents from the issue's seed list are exercised at their own higher test
layers (see ``docs/Omnigent/OmnigentFaultInjectionSuite.md``); a pure-domain
corpus entry never over-claims to reproduce those boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .conversions import plan_to_scenario, scenario_to_plan
from .harness import FaultPlan, ObservationFault, run_plan
from .invariants import violations
from .minimizer import minimize_plan
from .scenario import (
    CommandWindow,
    FaultScenario,
    LogicalOperation,
    ResponseBehavior,
    dumps_scenario,
    loads_scenario,
)


@dataclass(frozen=True)
class CorpusEntry:
    """One generalized incident: invariant + minimized scenario + metadata."""

    scenario_id: str
    source_ref: str
    invariant: str
    plan: FaultPlan
    operational_signal: str
    expected_converges: bool = True

    def scenario(self) -> FaultScenario:
        return plan_to_scenario(
            self.plan,
            scenario_id=self.scenario_id,
            source_ref=self.source_ref,
            invariant=self.invariant,
        )


#: Initial generalized escaped-incident scenarios. Each is a converging plan
#: whose named invariant must hold under the framework.
INITIAL_CORPUS: tuple[CorpusEntry, ...] = (
    CorpusEntry(
        scenario_id="missed-terminal-edge-after-heartbeat-timeout",
        source_ref="#3698",
        invariant="eventual_convergence",
        operational_signal="terminal recorded without a terminal SSE edge",
        plan=FaultPlan(
            seed=3698,
            observation_faults=(ObservationFault.MISSED_EDGE,),
            recovery_round=4,
            ground_truth_terminal="success",
        ),
    ),
    CorpusEntry(
        scenario_id="unknown-completion-vocabulary-mid-session",
        source_ref="#3683",
        invariant="compatibility_safety",
        operational_signal="session terminalized on unrecognized provider status",
        plan=FaultPlan(
            seed=3683,
            observation_faults=(ObservationFault.UNKNOWN_VOCAB,),
            recovery_round=4,
            ground_truth_terminal="success",
        ),
    ),
    CorpusEntry(
        scenario_id="stale-snapshot-after-newer-frontier",
        source_ref="#3665",
        invariant="monotonic_authority",
        operational_signal="durable revision moved backward after a stale snapshot",
        plan=FaultPlan(
            seed=3665,
            observation_faults=(ObservationFault.STALE_SESSION,),
            recovery_round=4,
            ground_truth_terminal="success",
        ),
    ),
    CorpusEntry(
        scenario_id="first-message-dispatch-response-lost",
        source_ref="MM-omnigent-first-message",
        invariant="at_most_once_submission",
        operational_signal="more than one accepted provider turn for one attempt",
        plan=FaultPlan(
            seed=1001,
            submit_response=ResponseBehavior.DROP,
            recovery_round=3,
            ground_truth_terminal="success",
        ),
    ),
    CorpusEntry(
        scenario_id="cleanup-races-new-continuation",
        source_ref="MM-omnigent-cleanup-race",
        invariant="cleanup_safety",
        operational_signal="cleanup deleted a replacement-generation resource",
        plan=FaultPlan(
            seed=1002,
            command_crashes={
                LogicalOperation.BEGIN_CLEANUP: (
                    CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT
                )
            },
            recovery_round=3,
            ground_truth_terminal="success",
        ),
    ),
    CorpusEntry(
        scenario_id="profile-lease-replacement-old-host-alive",
        source_ref="MM-omnigent-lease-replacement",
        invariant="lease_safety",
        operational_signal="lease released while a credential consumer remained",
        plan=FaultPlan(
            seed=1003,
            observation_faults=(ObservationFault.CONSUMER_ACTIVE,),
            recovery_round=4,
            ground_truth_terminal="failure",
        ),
    ),
)


def ingest_incident(
    plan: FaultPlan,
    *,
    scenario_id: str,
    source_ref: str,
    invariant: str,
) -> FaultScenario:
    """Turn a failing plan into a minimized, storable declarative scenario.

    This is the incident-ingestion workflow entrypoint: given a plan that
    reproduces an escaped failure (violates ``invariant``), minimize it while
    preserving the failure and return the safe, bounded scenario to store under
    the reliability replay corpus.
    """

    minimized = minimize_plan(plan)
    return plan_to_scenario(
        minimized,
        scenario_id=scenario_id,
        source_ref=source_ref,
        invariant=invariant,
    )


def write_scenario_file(scenario: FaultScenario, root: Path) -> Path:
    """Write a scenario as YAML under ``root/<scenario_id>/fault-scenario.yaml``."""

    scenario_id = scenario.scenario_id or f"seed-{scenario.seed}"
    target_dir = root / scenario_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "fault-scenario.yaml"
    target.write_text(dumps_scenario(scenario), encoding="utf-8")
    return target


def load_corpus_dir(root: Path) -> list[FaultScenario]:
    """Load every ``*/fault-scenario.yaml`` under ``root`` (quarantining unknowns)."""

    scenarios: list[FaultScenario] = []
    for path in sorted(root.glob("*/fault-scenario.yaml")):
        scenario = loads_scenario(path.read_text(encoding="utf-8"), on_unknown="quarantine")
        if scenario is not None:
            scenarios.append(scenario)
    return scenarios


def replay_scenario(scenario: FaultScenario):
    """Execute a declarative scenario and return its trace."""

    return run_plan(scenario_to_plan(scenario))


def scenario_violations(scenario: FaultScenario) -> list[str]:
    """Convenience: invariant violations from replaying a declarative scenario."""

    return violations(replay_scenario(scenario))


__all__ = [
    "CorpusEntry",
    "INITIAL_CORPUS",
    "ingest_incident",
    "write_scenario_file",
    "load_corpus_dir",
    "replay_scenario",
    "scenario_violations",
]
