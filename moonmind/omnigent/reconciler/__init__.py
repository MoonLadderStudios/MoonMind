"""Pure Omnigent lifecycle reconciler and canonical transition contracts.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

This package defines one side-effect-free decision boundary for the Omnigent
session lifecycle. :func:`reconcile` maps immutable intent, current durable
state, and authoritative observations to a single explicit
:class:`ReconciliationDecision` with stable reason codes. It performs no
database, network, filesystem, Docker, artifact, logging, telemetry, or Temporal
calls and is deterministic for equal inputs.

See ``docs/Omnigent/OmnigentLifecycleReconciler.md`` for the transition
vocabulary, evidence rules, and authority boundary.
"""

from moonmind.omnigent.reconciler.decision import (
    CommandSpec,
    EvidenceRequirement,
    ReconciliationDecision,
)
from moonmind.omnigent.reconciler.models import (
    CompiledSessionIntent,
    DurableSessionState,
    EventFrontier,
    EvidenceAvailability,
    HostRuntimeState,
    LeaseObservation,
    Observation,
    ObservationPresence,
    ObservationSet,
    ProviderSessionSnapshot,
    ProviderTurnSnapshot,
    RuntimeReadiness,
    TerminalEvidence,
    TurnAttempt,
    WorkspaceState,
)
from moonmind.omnigent.reconciler.reconcile import reconcile
from moonmind.omnigent.reconciler.shadow import (
    LEGACY_ACTION_ALIASES,
    ShadowComparison,
    compare_shadow,
)
from moonmind.omnigent.reconciler.versions import (
    DECISION_SCHEMA_VERSION,
    DURABLE_STATE_SCHEMA_VERSION,
    INTENT_SCHEMA_VERSION,
    OBSERVATION_SET_SCHEMA_VERSION,
    REASON_CODE_VERSION,
    ReconcilerContractError,
    UnknownSchemaVersionError,
)
from moonmind.omnigent.reconciler.vocabulary import (
    DecisionAction,
    DesiredLifecycle,
    DurablePhase,
    ReasonCode,
    TurnSubmissionState,
    parse_reason_code,
)

__all__ = [
    "CommandSpec",
    "CompiledSessionIntent",
    "DECISION_SCHEMA_VERSION",
    "DURABLE_STATE_SCHEMA_VERSION",
    "DecisionAction",
    "DesiredLifecycle",
    "DurablePhase",
    "DurableSessionState",
    "EventFrontier",
    "EvidenceAvailability",
    "EvidenceRequirement",
    "HostRuntimeState",
    "INTENT_SCHEMA_VERSION",
    "LEGACY_ACTION_ALIASES",
    "LeaseObservation",
    "OBSERVATION_SET_SCHEMA_VERSION",
    "Observation",
    "ObservationPresence",
    "ObservationSet",
    "ProviderSessionSnapshot",
    "ProviderTurnSnapshot",
    "REASON_CODE_VERSION",
    "ReasonCode",
    "ReconciliationDecision",
    "ReconcilerContractError",
    "RuntimeReadiness",
    "ShadowComparison",
    "TerminalEvidence",
    "TurnAttempt",
    "TurnSubmissionState",
    "UnknownSchemaVersionError",
    "WorkspaceState",
    "compare_shadow",
    "parse_reason_code",
    "reconcile",
]
