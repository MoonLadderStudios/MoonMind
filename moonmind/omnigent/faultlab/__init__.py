"""Programmable fault-injection provider and model-based reliability suite.

Source issue: MoonLadderStudios/MoonMind#3709
([Omnigent control plane 8/11]).

This package provides one stateful fault model for the Omnigent lifecycle:

* a versioned declarative fault-scenario format (:mod:`.scenario`);
* a programmable fake provider with an independent side-effect / idempotency
  ledger (:mod:`.provider`);
* a reference state machine independent of the production reducer
  (:mod:`.reference_model`);
* an execution harness that drives the *production* reconciler under injected
  transport, observation, and crash faults (:mod:`.harness`);
* the twelve required reliability invariants as predicates (:mod:`.invariants`);
* a seed-based deterministic generator (:mod:`.generator`);
* a failing-plan minimizer (:mod:`.minimizer`);
* secret-safe diagnostic bundles (:mod:`.diagnostics`);
* an initial corpus of generalized escaped-incident scenarios (:mod:`.corpus`).

See ``docs/Omnigent/OmnigentFaultInjectionSuite.md`` for the design and CI
policy.
"""

from __future__ import annotations

from .diagnostics import DiagnosticBundle, build_diagnostic_bundle
from .generator import generate_plan, is_deterministic
from .harness import (
    ExecutionTrace,
    FaultPlan,
    JournalEntry,
    ObservationFault,
    apply_decision,
    run_plan,
)
from .invariants import check_all, violations
from .minimizer import minimize_plan
from .provider import ProgrammableFakeProvider, SideEffectLedger, payload_digest
from .reference_model import (
    IllegalTransitionError,
    ReferenceCommand,
    ReferenceModel,
    ReferencePhase,
)
from .scenario import (
    FAULT_SCENARIO_SCHEMA_VERSION,
    CommandWindow,
    FaultScenario,
    LogicalOperation,
    ResponseBehavior,
    ScenarioStep,
    SideEffect,
    UnknownScenarioSchemaVersionError,
    dumps_scenario,
    load_scenario,
    loads_scenario,
)

__all__ = [
    # scenario format
    "FAULT_SCENARIO_SCHEMA_VERSION",
    "CommandWindow",
    "FaultScenario",
    "LogicalOperation",
    "ResponseBehavior",
    "ScenarioStep",
    "SideEffect",
    "UnknownScenarioSchemaVersionError",
    "load_scenario",
    "loads_scenario",
    "dumps_scenario",
    # provider
    "ProgrammableFakeProvider",
    "SideEffectLedger",
    "payload_digest",
    # reference model
    "IllegalTransitionError",
    "ReferenceCommand",
    "ReferenceModel",
    "ReferencePhase",
    # harness
    "ExecutionTrace",
    "FaultPlan",
    "JournalEntry",
    "ObservationFault",
    "apply_decision",
    "run_plan",
    # invariants
    "check_all",
    "violations",
    # generator / minimizer / diagnostics / corpus
    "generate_plan",
    "is_deterministic",
    "minimize_plan",
    "DiagnosticBundle",
    "build_diagnostic_bundle",
]
