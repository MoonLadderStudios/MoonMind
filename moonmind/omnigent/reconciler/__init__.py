"""Pure Omnigent lifecycle reconciler and canonical transition contracts.

Source issue: MoonLadderStudios/MoonMind#3702
([Omnigent control plane 1/11]).

This package defines one side-effect-free reducer,
:func:`reconcile`, that converts immutable intent, current durable state, and
authoritative observations into an explicit :class:`ReconciliationDecision` with
stable reason codes. It performs no database, network, filesystem, Docker,
artifact, logging, telemetry, or Temporal call.

See ``docs/Omnigent/OmnigentLifecycleReconciler.md`` for the transition
vocabulary, evidence rules, and authority boundary.
"""

from __future__ import annotations

from .contracts import (
    COMMAND_DECISION_KINDS,
    CommandSpec,
    CompiledSessionIntent,
    CompatibilityObservation,
    DecisionDiagnostics,
    DecisionKind,
    DesiredLifecycle,
    DurableSessionState,
    EvidenceObservation,
    EvidenceRequirement,
    EventFrontierObservation,
    HostObservation,
    KNOWN_COMPATIBILITY_VERSIONS,
    LINEAR_PHASE_ORDER,
    LeaseObservation,
    LeaseState,
    ObservationSet,
    PriorDecisionSummary,
    ProviderSessionObservation,
    ProviderStatusClass,
    ProviderTurnObservation,
    RECONCILER_CONTRACT_VERSION,
    ReasonCode,
    ReconciliationDecision,
    SETTLED_DECISION_KINDS,
    SessionLifecyclePhase,
    ShadowComparison,
    SubmissionState,
    TerminalOutcome,
    WorkspaceObservation,
)
from .reducer import (
    LEGACY_ACTION_TO_DECISION_KIND,
    classify_provider_status,
    current_phase,
    reconcile,
    shadow_compare,
)

__all__ = [
    "COMMAND_DECISION_KINDS",
    "CommandSpec",
    "CompiledSessionIntent",
    "CompatibilityObservation",
    "DecisionDiagnostics",
    "DecisionKind",
    "DesiredLifecycle",
    "DurableSessionState",
    "EvidenceObservation",
    "EvidenceRequirement",
    "EventFrontierObservation",
    "HostObservation",
    "KNOWN_COMPATIBILITY_VERSIONS",
    "LINEAR_PHASE_ORDER",
    "LeaseObservation",
    "LeaseState",
    "ObservationSet",
    "PriorDecisionSummary",
    "ProviderSessionObservation",
    "ProviderStatusClass",
    "ProviderTurnObservation",
    "RECONCILER_CONTRACT_VERSION",
    "ReasonCode",
    "ReconciliationDecision",
    "SETTLED_DECISION_KINDS",
    "SessionLifecyclePhase",
    "ShadowComparison",
    "SubmissionState",
    "TerminalOutcome",
    "WorkspaceObservation",
    "LEGACY_ACTION_TO_DECISION_KIND",
    "classify_provider_status",
    "current_phase",
    "reconcile",
    "shadow_compare",
]
