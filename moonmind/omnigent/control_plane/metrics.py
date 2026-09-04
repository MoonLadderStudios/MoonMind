"""Low-cardinality Omnigent lifecycle metric families.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

This module adds the reconciliation (non-conflict), provider/transport,
resource/cleanup, and compatibility/verification metric families required by the
issue. The concurrency *conflict* counters (revision/fencing conflicts,
duplicate-command suppression, delivery-unknown created/reconciled, stale
observation retained, cleanup-claim conflicts) already live in
:mod:`moonmind.omnigent.control_plane.telemetry` (from #3704) and are **not**
duplicated here (Simplicity Gate).

Cardinality discipline (issue acceptance criterion):

* Every metric name is drawn from the closed :data:`METRICS` registry.
* Every label key is declared per-metric and every label value is drawn from a
  closed bounded vocabulary; an unknown value collapses to ``"other"`` rather
  than entering the label space.
* No Workflow, run, user, session, binding, provider-session, host, runner,
  profile, credential, repository, or workspace identity may ever be a label:
  :data:`FORBIDDEN_LABEL_KEYS` is rejected at registration and at record time,
  so a high-cardinality identity can never enter the metric label space.

The registry keeps a process-local aggregate for diagnostics and records the
same bounded values through the process OpenTelemetry meter. Export remains
asynchronous in the SDK, and recording failures are isolated from application
correctness.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Mapping

logger = logging.getLogger("moonmind.omnigent.control_plane.metrics")

#: Identity labels that must never appear on any Omnigent metric. Registering or
#: recording one fails closed (programming error), so identity can never leak
#: into the metric label space through a new metric or a stray keyword.
FORBIDDEN_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "workflow",
        "workflow_id",
        "run",
        "run_id",
        "user",
        "user_id",
        "session",
        "session_id",
        "binding",
        "chat_binding_id",
        "provider_session",
        "provider_session_id",
        "host",
        "host_id",
        "runner",
        "runner_id",
        "profile",
        "profile_id",
        "credential",
        "credential_id",
        "repository",
        "repo",
        "workspace",
        "workspace_id",
    }
)


# --- Bounded label vocabularies ---------------------------------------------

# Every label key used by any metric maps to a closed value set. An out-of-set
# value collapses to "other"; a missing value collapses to "unknown".
BOUNDED_LABEL_VALUES: dict[str, frozenset[str]] = {
    "decision_class": frozenset(
        {
            "no_op",
            "await_observation",
            "ensure_profile_lease",
            "ensure_host",
            "ensure_provider_session",
            "submit_turn",
            "record_provider_terminal",
            "synthesize_terminal_from_snapshot",
            "harvest_evidence",
            "begin_cleanup",
            "release_leases",
            "retry_transient_observation",
            "quarantine_ambiguous_state",
            "fail_nonretryable",
        }
    ),
    # Coarse reason *class* (not the full reason-code vocabulary) to stay low
    # cardinality: the timeline/decision journal carries the exact reason code.
    "reason_class": frozenset(
        {
            "provisioning",
            "awaiting",
            "terminal",
            "cleanup",
            "ambiguous",
            "failed",
            "compatibility",
        }
    ),
    "lease_scope": frozenset({"provider_profile", "host"}),
    "transport": frozenset({"http", "sse", "websocket"}),
    "transport_outcome": frozenset({"ready", "disconnect", "reconnect", "failure"}),
    "janitor_outcome": frozenset({"claim", "success", "conflict", "failure"}),
    "status": frozenset({"ok", "degraded", "unknown", "drift"}),
    "capability": frozenset(
        {
            "reconciler_generation",
            "schema",
            "provider_snapshot",
            "event_transport",
            "server_build",
            "ui_build",
            "host_build",
            "websocket",
            "worker_backend",
            "container_backend",
            "observation_freshness",
            "janitor",
            "exact_image",
            "protected_live_evidence",
        }
    ),
    "readiness": frozenset({"ready", "not_ready", "unknown"}),
    # --- Runtime-provider migration (MoonLadderStudios/MoonMind#3833) ---
    # Harness *class*, never a harness display name or a profile identity.
    "harness_class": frozenset(
        {"codex", "claude", "opencode", "pi", "unregistered"}
    ),
    "realizer_class": frozenset(
        {"generic_omnigent", "legacy_profile_bound_omnigent", "direct_compatibility"}
    ),
    "selection_source": frozenset(
        {"authored", "rollout_default", "recorded", "configured_default"}
    ),
    "rollout_state": frozenset(
        {
            "disabled",
            "explicit_only",
            "canary",
            "preferred",
            "new_work_default",
            "direct_compatibility_only",
            "retired_for_new_work",
        }
    ),
    "denial_reason": frozenset(
        {
            "combination_not_registered",
            "rollout_disabled",
            "rollout_canary_cohort_excluded",
            "support_evidence_missing",
            "support_evidence_stale",
            "target_not_launch_ready",
            "model_not_qualified",
            "architecture_unsupported",
            "host_mode_unavailable",
            "provider_profile_unavailable",
            "rollback_new_admission_stopped",
            "rollback_legacy_default_restored",
            "rollback_all_omnigent_stopped",
        }
    ),
    "followup_kind": frozenset(
        {
            "workflow_chat",
            "repository_continuation",
            "steering",
            "approval_response",
            "remediation",
            "checkpoint_resume",
            "linked_branch",
        }
    ),
    "availability": frozenset({"available", "unavailable"}),
    "cleanup_outcome": frozenset(
        {"cancelled_clean", "cancelled_incomplete", "completed_clean", "leaked"}
    ),
    "rollback_control": frozenset(
        {
            "stop_new_generic_codex_admission",
            "stop_new_generic_claude_admission",
            "stop_new_opencode_shared_image_admission",
            "restore_legacy_or_direct_default",
            "disable_native_interactive_chat",
            "stop_all_new_omnigent_work",
        }
    ),
}

#: Fallback label values emitted by :func:`_normalize_labels`: an
#: out-of-vocabulary value collapses to ``"other"`` and an omitted label to
#: ``"unknown"``. Both are declared members of *every* bounded vocabulary so an
#: exporter or dashboard reading the registry can enumerate every value this
#: recorder can actually produce (no undeclared value ever reaches the backend).
LABEL_FALLBACK_OTHER = "other"
LABEL_FALLBACK_UNKNOWN = "unknown"
BOUNDED_LABEL_VALUES = {
    key: values | {LABEL_FALLBACK_OTHER, LABEL_FALLBACK_UNKNOWN}
    for key, values in BOUNDED_LABEL_VALUES.items()
}


COUNTER = "counter"
OBSERVATION = "observation"


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    kind: str
    labels: tuple[str, ...]
    unit: str = "1"


def _def(name: str, kind: str, labels: tuple[str, ...] = (), unit: str = "1") -> MetricDefinition:
    forbidden = set(labels) & FORBIDDEN_LABEL_KEYS
    if forbidden:  # pragma: no cover - registry programming error
        raise ValueError(f"metric {name!r} declares forbidden identity labels: {sorted(forbidden)}")
    for key in labels:
        if key not in BOUNDED_LABEL_VALUES:  # pragma: no cover - registry programming error
            raise ValueError(f"metric {name!r} label {key!r} has no bounded value vocabulary")
    return MetricDefinition(name, kind, labels, unit)


# --- Reconciliation (non-conflict) ------------------------------------------

RECONCILIATION_DECISIONS = "omnigent_reconciliation_decisions"
RECONCILIATION_CONVERGENCE_LATENCY = "omnigent_reconciliation_convergence_latency_seconds"
REPEATED_NO_PROGRESS_DECISIONS = "omnigent_reconciliation_repeated_no_progress"
QUARANTINED_AMBIGUITY = "omnigent_reconciliation_quarantined_ambiguity"
SNAPSHOT_RECOVERED_TERMINAL = "omnigent_reconciliation_snapshot_recovered_terminal"

# --- Provider & transport ---------------------------------------------------

TRANSPORT_EVENTS = "omnigent_provider_transport_events"
LIVENESS_ONLY_DURATION = "omnigent_provider_liveness_only_seconds"
SNAPSHOT_LATENCY = "omnigent_provider_snapshot_latency_seconds"
SNAPSHOT_ERRORS = "omnigent_provider_snapshot_errors"
PROVIDER_TERMINAL_TO_MOONMIND_TERMINAL_LATENCY = (
    "omnigent_provider_terminal_to_moonmind_terminal_seconds"
)
UNKNOWN_PROVIDER_STATUS = "omnigent_provider_unknown_status"
UNKNOWN_SCHEMA_VALUE = "omnigent_provider_unknown_schema_value"
TRANSPORT_READINESS = "omnigent_provider_transport_readiness"

# --- Resources & cleanup ----------------------------------------------------

LEASE_ACQUIRE_LATENCY = "omnigent_lease_acquire_latency_seconds"
LEASE_RENEWAL_CONFLICTS = "omnigent_lease_renewal_conflicts"
CLEANUP_LAG = "omnigent_cleanup_lag_seconds"
ORPHANED_LEASES = "omnigent_orphaned_leases"
JANITOR_OPERATIONS = "omnigent_janitor_operations"
EVIDENCE_HARVEST_LATENCY = "omnigent_evidence_harvest_latency_seconds"
EVIDENCE_PUBLICATION_LATENCY = "omnigent_evidence_publication_latency_seconds"

# --- Compatibility & verification -------------------------------------------

DEPLOYED_BUILD_COMPATIBILITY = "omnigent_deployed_build_compatibility"
RUNTIME_CAPABILITY_READINESS = "omnigent_runtime_capability_readiness"
EXACT_IMAGE_CONFORMANCE = "omnigent_exact_image_conformance"
PROTECTED_LIVE_EVIDENCE_AGE = "omnigent_protected_live_evidence_age_seconds"
PROVIDER_VERIFICATION_RUNNER_HEALTH = "omnigent_provider_verification_runner_health"

# --- Runtime-provider migration (#3833) --------------------------------------
#
# One bounded family per required migration signal. Every label value is drawn
# from a closed vocabulary above, and no user, workflow, session, profile,
# repository, or credential identity may appear (enforced by
# :data:`FORBIDDEN_LABEL_KEYS`).

MIGRATION_SELECTED_PATH = "omnigent_migration_selected_path"
MIGRATION_ROLLOUT_STATE = "omnigent_migration_rollout_state"
MIGRATION_LAUNCH_READINESS = "omnigent_migration_launch_readiness"
MIGRATION_SUPPORT_EVIDENCE_DENIAL = "omnigent_migration_support_evidence_denial"
MIGRATION_PROVIDER_PROFILE_WAIT = (
    "omnigent_migration_provider_profile_wait_seconds"
)
MIGRATION_HOST_LATENCY = "omnigent_migration_host_latency_seconds"
MIGRATION_FIRST_TURN_LATENCY = "omnigent_migration_first_turn_latency_seconds"
MIGRATION_FOLLOWUP_AVAILABILITY = "omnigent_migration_followup_availability"
MIGRATION_CLEANUP_OUTCOME = "omnigent_migration_cleanup_outcome"
MIGRATION_FALLBACK_DENIED = "omnigent_migration_fallback_denied"
MIGRATION_ROLLBACK_ACTIVATION = "omnigent_migration_rollback_activation"


METRICS: dict[str, MetricDefinition] = {
    m.name: m
    for m in (
        # Reconciliation
        _def(RECONCILIATION_DECISIONS, COUNTER, ("decision_class", "reason_class")),
        _def(RECONCILIATION_CONVERGENCE_LATENCY, OBSERVATION, (), "seconds"),
        _def(REPEATED_NO_PROGRESS_DECISIONS, COUNTER),
        _def(QUARANTINED_AMBIGUITY, COUNTER),
        _def(SNAPSHOT_RECOVERED_TERMINAL, COUNTER),
        # Provider & transport
        _def(TRANSPORT_EVENTS, COUNTER, ("transport", "transport_outcome")),
        _def(LIVENESS_ONLY_DURATION, OBSERVATION, (), "seconds"),
        _def(SNAPSHOT_LATENCY, OBSERVATION, (), "seconds"),
        _def(SNAPSHOT_ERRORS, COUNTER),
        _def(PROVIDER_TERMINAL_TO_MOONMIND_TERMINAL_LATENCY, OBSERVATION, (), "seconds"),
        _def(UNKNOWN_PROVIDER_STATUS, COUNTER),
        _def(UNKNOWN_SCHEMA_VALUE, COUNTER),
        _def(TRANSPORT_READINESS, COUNTER, ("transport", "readiness")),
        # Resources & cleanup
        _def(LEASE_ACQUIRE_LATENCY, OBSERVATION, ("lease_scope",), "seconds"),
        _def(LEASE_RENEWAL_CONFLICTS, COUNTER, ("lease_scope",)),
        _def(CLEANUP_LAG, OBSERVATION, (), "seconds"),
        _def(ORPHANED_LEASES, COUNTER, ("lease_scope",)),
        _def(JANITOR_OPERATIONS, COUNTER, ("janitor_outcome",)),
        _def(EVIDENCE_HARVEST_LATENCY, OBSERVATION, (), "seconds"),
        _def(EVIDENCE_PUBLICATION_LATENCY, OBSERVATION, (), "seconds"),
        # Compatibility & verification
        _def(DEPLOYED_BUILD_COMPATIBILITY, COUNTER, ("status",)),
        _def(RUNTIME_CAPABILITY_READINESS, COUNTER, ("capability", "readiness")),
        _def(EXACT_IMAGE_CONFORMANCE, COUNTER, ("status",)),
        _def(PROTECTED_LIVE_EVIDENCE_AGE, OBSERVATION, (), "seconds"),
        _def(PROVIDER_VERIFICATION_RUNNER_HEALTH, COUNTER, ("status",)),
        # Runtime-provider migration (#3833)
        _def(
            MIGRATION_SELECTED_PATH,
            COUNTER,
            ("harness_class", "realizer_class", "selection_source"),
        ),
        _def(
            MIGRATION_ROLLOUT_STATE,
            COUNTER,
            ("harness_class", "realizer_class", "rollout_state"),
        ),
        _def(MIGRATION_LAUNCH_READINESS, COUNTER, ("harness_class", "readiness")),
        _def(
            MIGRATION_SUPPORT_EVIDENCE_DENIAL,
            COUNTER,
            ("harness_class", "denial_reason"),
        ),
        _def(
            MIGRATION_PROVIDER_PROFILE_WAIT,
            OBSERVATION,
            ("harness_class",),
            "seconds",
        ),
        _def(MIGRATION_HOST_LATENCY, OBSERVATION, ("harness_class",), "seconds"),
        _def(
            MIGRATION_FIRST_TURN_LATENCY,
            OBSERVATION,
            ("harness_class",),
            "seconds",
        ),
        _def(
            MIGRATION_FOLLOWUP_AVAILABILITY,
            COUNTER,
            ("harness_class", "followup_kind", "availability"),
        ),
        _def(
            MIGRATION_CLEANUP_OUTCOME,
            COUNTER,
            ("harness_class", "cleanup_outcome"),
        ),
        _def(
            MIGRATION_FALLBACK_DENIED,
            COUNTER,
            ("harness_class", "denial_reason"),
        ),
        _def(MIGRATION_ROLLBACK_ACTIVATION, COUNTER, ("rollback_control",)),
    )
}


@dataclass
class _ObservationAggregate:
    count: int = 0
    total: float = 0.0
    last: float = 0.0


_lock = threading.Lock()
_otel_lock = threading.Lock()
_counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
_observations: dict[tuple[str, tuple[tuple[str, str], ...]], _ObservationAggregate] = {}
_otel_instruments: dict[str, object] = {}


def _otel_instrument(metric: MetricDefinition) -> object:
    """Resolve one process-wide OTel instrument lazily at the activity/API edge."""

    with _otel_lock:
        existing = _otel_instruments.get(metric.name)
        if existing is not None:
            return existing
        from opentelemetry import metrics as otel_metrics

        meter = otel_metrics.get_meter("moonmind.omnigent.control_plane")
        instrument = (
            meter.create_counter(metric.name, unit=metric.unit)
            if metric.kind == COUNTER
            else meter.create_histogram(metric.name, unit=metric.unit)
        )
        _otel_instruments[metric.name] = instrument
        return instrument


def _emit_otel(metric: MetricDefinition, value: float, labels: tuple[tuple[str, str], ...]) -> None:
    """Best-effort OTel recording; exporter/instrument failure is never authority."""

    try:
        instrument = _otel_instrument(metric)
        attributes = dict(labels)
        if metric.kind == COUNTER:
            instrument.add(int(value), attributes=attributes)  # type: ignore[attr-defined]
        else:
            instrument.record(float(value), attributes=attributes)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("Omnigent OpenTelemetry metric recording failed", exc_info=True)


def _normalize_labels(metric: MetricDefinition, labels: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    forbidden = set(labels) & FORBIDDEN_LABEL_KEYS
    if forbidden:
        raise ValueError(
            f"forbidden identity labels for {metric.name!r}: {sorted(forbidden)}"
        )
    unknown = set(labels) - set(metric.labels)
    if unknown:
        raise ValueError(f"unknown labels for {metric.name!r}: {sorted(unknown)}")
    normalized: list[tuple[str, str]] = []
    for key in metric.labels:
        allowed = BOUNDED_LABEL_VALUES[key]
        raw = labels.get(key, "unknown")
        value = raw.value if hasattr(raw, "value") else str(raw)
        if value not in allowed:
            value = "unknown" if key not in labels else "other"
        normalized.append((key, value))
    return tuple(normalized)


def _definition(name: str) -> MetricDefinition:
    try:
        return METRICS[name]
    except KeyError:  # pragma: no cover - programming error
        raise KeyError(f"unknown Omnigent control-plane metric: {name!r}")


def increment(name: str, *, amount: int = 1, **labels: object) -> None:
    """Increment a bounded counter metric.

    Fails closed on an unknown metric name, an unknown/forbidden label key, or a
    non-counter metric — those are programming errors, not runtime inputs. A
    bounded but out-of-vocabulary label value collapses to ``"other"`` rather
    than raising, so untrusted classification input can never crash a caller.
    """

    metric = _definition(name)
    if metric.kind != COUNTER:  # pragma: no cover - programming error
        raise TypeError(f"metric {name!r} is not a counter")
    key = _normalize_labels(metric, labels)
    with _lock:
        _counters[(name, key)] += amount
    _emit_otel(metric, amount, key)
    logger.info("omnigent.control_plane.metric", extra={"metric": name, "kind": COUNTER})


def observe(name: str, value: float, **labels: object) -> None:
    """Record one observation (latency/age/duration) into a bounded aggregate."""

    metric = _definition(name)
    if metric.kind != OBSERVATION:  # pragma: no cover - programming error
        raise TypeError(f"metric {name!r} is not an observation metric")
    key = _normalize_labels(metric, labels)
    numeric = float(value)
    with _lock:
        agg = _observations.get((name, key))
        if agg is None:
            agg = _ObservationAggregate()
            _observations[(name, key)] = agg
        agg.count += 1
        agg.total += numeric
        agg.last = numeric
    _emit_otel(metric, numeric, key)
    logger.info("omnigent.control_plane.metric", extra={"metric": name, "kind": OBSERVATION})


def snapshot() -> dict[str, object]:
    """Return a copy of current counters and observation aggregates."""

    with _lock:
        counters = {f"{name}{list(labels)}": count for (name, labels), count in _counters.items()}
        observations = {
            f"{name}{list(labels)}": {"count": agg.count, "total": agg.total, "last": agg.last}
            for (name, labels), agg in _observations.items()
        }
    return {"counters": counters, "observations": observations}


def counter_series() -> tuple[tuple[str, dict[str, str], int], ...]:
    """Return structured counter samples for operator status projections.

    ``snapshot`` formats keys for human diagnostics; this returns the same
    aggregates as ``(metric name, labels, value)`` so a projection can filter by
    a bounded label without parsing a formatted string.
    """

    with _lock:
        return tuple(
            (name, dict(labels), count)
            for (name, labels), count in _counters.items()
        )


def label_inventory() -> dict[str, tuple[str, ...]]:
    """Return the declared label keys for every metric (for contract tests)."""

    return {name: metric.labels for name, metric in METRICS.items()}


# --- Runtime-provider migration recording helpers ----------------------------

#: Exact harness id -> bounded harness *class* label. Matching is by exact id,
#: never by substring, so a new harness collapses to ``unregistered`` instead of
#: silently joining another class.
_HARNESS_CLASSES: dict[str, str] = {
    "codex-native": "codex",
    "claude-native": "claude",
    "opencode-native": "opencode",
    "pi-native": "pi",
}


def harness_class_for(harness_id: object) -> str:
    """Return the bounded harness class label for an exact harness id."""

    return _HARNESS_CLASSES.get(str(harness_id or "").strip(), "unregistered")


def record_runtime_target_selection(
    *,
    harness_id: object,
    realizer_class: object,
    selection_source: object,
    rollout_state: object,
    available: bool,
    denial_reason: object = None,
) -> None:
    """Record one runtime-target selection through the shared boundary.

    Emits the selected-path, rollout-state, and (when the target is
    unavailable) the fallback-denial counters in one place so every authoring
    surface reports the migration the same way.
    """

    harness_class = harness_class_for(harness_id)
    increment(
        MIGRATION_SELECTED_PATH,
        harness_class=harness_class,
        realizer_class=realizer_class,
        selection_source=selection_source,
    )
    increment(
        MIGRATION_ROLLOUT_STATE,
        harness_class=harness_class,
        realizer_class=realizer_class,
        rollout_state=rollout_state,
    )
    if not available:
        increment(
            MIGRATION_FALLBACK_DENIED,
            harness_class=harness_class,
            denial_reason=denial_reason,
        )


def record_safely(recorder: Callable[..., None], /, **labels: object) -> None:
    """Invoke one recorder without letting telemetry become execution authority.

    Every migration call site records through this helper so a metric-registry
    programming error, an exporter failure, or an unexpected label can never
    change a launch, turn, cleanup, or admission outcome.
    """

    try:
        recorder(**labels)
    except Exception:  # pragma: no cover - telemetry is never authority
        logger.warning("Omnigent metric recording failed", exc_info=True)


def record_migration_launch_readiness(
    *, harness_id: object, ready: bool | None
) -> None:
    """Record one host/harness launch readiness outcome for the migration view.

    ``ready is None`` records ``unknown`` (the launch neither completed nor
    failed observably), so an indeterminate outcome never masquerades as a
    successful one.
    """

    increment(
        MIGRATION_LAUNCH_READINESS,
        harness_class=harness_class_for(harness_id),
        readiness=("unknown" if ready is None else ("ready" if ready else "not_ready")),
    )


def record_support_evidence_denial(
    *, harness_id: object, denial_reason: object
) -> None:
    """Record one missing, stale, or expired support-evidence denial."""

    increment(
        MIGRATION_SUPPORT_EVIDENCE_DENIAL,
        harness_class=harness_class_for(harness_id),
        denial_reason=denial_reason,
    )


def record_provider_profile_wait(
    *, harness_id: object, wait_seconds: float
) -> None:
    """Record how long one execution waited for Provider Profile capacity."""

    observe(
        MIGRATION_PROVIDER_PROFILE_WAIT,
        max(0.0, float(wait_seconds)),
        harness_class=harness_class_for(harness_id),
    )


def record_host_latency(*, harness_id: object, latency_seconds: float) -> None:
    """Record the time from host allocation to an attested ready host."""

    observe(
        MIGRATION_HOST_LATENCY,
        max(0.0, float(latency_seconds)),
        harness_class=harness_class_for(harness_id),
    )


def record_first_turn_latency(
    *, harness_id: object, latency_seconds: float
) -> None:
    """Record the time the first canonical turn of a session took."""

    observe(
        MIGRATION_FIRST_TURN_LATENCY,
        max(0.0, float(latency_seconds)),
        harness_class=harness_class_for(harness_id),
    )


def record_followup_availability(
    *, harness_id: object, followup_kind: object, available: bool
) -> None:
    """Record whether one follow-up turn source was accepted or refused."""

    increment(
        MIGRATION_FOLLOWUP_AVAILABILITY,
        harness_class=harness_class_for(harness_id),
        followup_kind=followup_kind,
        availability=("available" if available else "unavailable"),
    )


def record_cleanup_outcome(*, harness_id: object, cleanup_outcome: object) -> None:
    """Record one terminal cleanup outcome for the migration view."""

    increment(
        MIGRATION_CLEANUP_OUTCOME,
        harness_class=harness_class_for(harness_id),
        cleanup_outcome=cleanup_outcome,
    )


def record_rollback_activation(control: object) -> None:
    """Record activation of one runtime-provider rollback control."""

    increment(MIGRATION_ROLLBACK_ACTIVATION, rollback_control=control)


def reset() -> None:
    """Reset all aggregates. Test-only; production metrics are monotonic."""

    with _lock:
        _counters.clear()
        _observations.clear()


__all__ = [
    "FORBIDDEN_LABEL_KEYS",
    "BOUNDED_LABEL_VALUES",
    "LABEL_FALLBACK_OTHER",
    "LABEL_FALLBACK_UNKNOWN",
    "METRICS",
    "MetricDefinition",
    "COUNTER",
    "OBSERVATION",
    "increment",
    "observe",
    "snapshot",
    "counter_series",
    "label_inventory",
    "reset",
    # names
    "RECONCILIATION_DECISIONS",
    "RECONCILIATION_CONVERGENCE_LATENCY",
    "REPEATED_NO_PROGRESS_DECISIONS",
    "QUARANTINED_AMBIGUITY",
    "SNAPSHOT_RECOVERED_TERMINAL",
    "TRANSPORT_EVENTS",
    "LIVENESS_ONLY_DURATION",
    "SNAPSHOT_LATENCY",
    "SNAPSHOT_ERRORS",
    "PROVIDER_TERMINAL_TO_MOONMIND_TERMINAL_LATENCY",
    "UNKNOWN_PROVIDER_STATUS",
    "UNKNOWN_SCHEMA_VALUE",
    "TRANSPORT_READINESS",
    "LEASE_ACQUIRE_LATENCY",
    "LEASE_RENEWAL_CONFLICTS",
    "CLEANUP_LAG",
    "ORPHANED_LEASES",
    "JANITOR_OPERATIONS",
    "EVIDENCE_HARVEST_LATENCY",
    "EVIDENCE_PUBLICATION_LATENCY",
    "DEPLOYED_BUILD_COMPATIBILITY",
    "RUNTIME_CAPABILITY_READINESS",
    "EXACT_IMAGE_CONFORMANCE",
    "PROTECTED_LIVE_EVIDENCE_AGE",
    "PROVIDER_VERIFICATION_RUNNER_HEALTH",
    "MIGRATION_SELECTED_PATH",
    "MIGRATION_ROLLOUT_STATE",
    "MIGRATION_LAUNCH_READINESS",
    "MIGRATION_SUPPORT_EVIDENCE_DENIAL",
    "MIGRATION_PROVIDER_PROFILE_WAIT",
    "MIGRATION_HOST_LATENCY",
    "MIGRATION_FIRST_TURN_LATENCY",
    "MIGRATION_FOLLOWUP_AVAILABILITY",
    "MIGRATION_CLEANUP_OUTCOME",
    "MIGRATION_FALLBACK_DENIED",
    "MIGRATION_ROLLBACK_ACTIVATION",
    "harness_class_for",
    "record_safely",
    "record_runtime_target_selection",
    "record_migration_launch_readiness",
    "record_support_evidence_denial",
    "record_provider_profile_wait",
    "record_host_latency",
    "record_first_turn_latency",
    "record_followup_availability",
    "record_cleanup_outcome",
    "record_rollback_activation",
]
