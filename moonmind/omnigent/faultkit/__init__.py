"""Programmable Omnigent fault-injection provider and model-based reliability kit.

Deliverable for MoonLadderStudios/MoonMind#3709 (Omnigent control plane 8/11).

This package provides one unified, deterministic fault model for the Omnigent
control plane so lifecycle interleavings can be explored systematically instead
of reactively per incident. It is runtime-neutral, hermetic (no network, no
credentials, no Docker socket), and secret-free by construction.

Components
----------
* ``scenario`` -- the versioned declarative ``moonmind.omnigent-fault-scenario/v1``
  format plus the logical command vocabulary and command-window injection points.
* ``recording`` -- an independent recorder of requests, side effects, logical
  idempotency identities, payload digests, responses, and observations.
* ``fake_provider`` -- the programmable fake Omnigent provider/host driven by a
  scenario.
* ``injectors`` -- host, Provider-Profile lease, and infrastructure fault
  controls with shared fail-before/fail-after command windows.
* ``reference_model`` -- a compact reference state machine independent of the
  production reconciler, used as the invariant oracle.
* ``reconciler`` -- the reconciler-under-test that embodies MoonMind's
  reconciliation policy and is compared against the reference model.
* ``invariants`` -- the twelve named reliability properties.
* ``generator`` -- a seeded action-sequence generator.
* ``minimizer`` -- a delta-debug style minimizer that shrinks a failing sequence
  while preserving the violated invariant.
* ``harness`` -- drives the reconciler + fake provider + reference model and
  returns a result with the recorder log, decision journal, and violations.
* ``diagnostics`` -- builds a safe, credential-free diagnostic bundle.
* ``corpus`` -- the initial declarative scenarios lifted from escaped incidents
  and the incident-ingestion metadata contract.
"""

from __future__ import annotations

from moonmind.omnigent.faultkit.commands import (
    COMMAND_WINDOWS,
    CommandWindow,
    LogicalCommand,
)
from moonmind.omnigent.faultkit.invariants import (
    INVARIANTS,
    Invariant,
    InvariantViolation,
    check_invariants,
)
from moonmind.omnigent.faultkit.recording import (
    ProviderRecorder,
    RecordedObservation,
    RecordedRequest,
    RecordedSideEffect,
    scan_for_secrets,
)
from moonmind.omnigent.faultkit.scenario import (
    SUPPORTED_SCENARIO_SCHEMA_VERSIONS,
    ResponseMode,
    Scenario,
    ScenarioSchemaError,
    ScenarioStep,
    SideEffectKind,
    load_scenario,
)

__all__ = [
    "COMMAND_WINDOWS",
    "CommandWindow",
    "LogicalCommand",
    "INVARIANTS",
    "Invariant",
    "InvariantViolation",
    "check_invariants",
    "ProviderRecorder",
    "RecordedObservation",
    "RecordedRequest",
    "RecordedSideEffect",
    "scan_for_secrets",
    "SUPPORTED_SCENARIO_SCHEMA_VERSIONS",
    "ResponseMode",
    "Scenario",
    "ScenarioSchemaError",
    "ScenarioStep",
    "SideEffectKind",
    "load_scenario",
]
