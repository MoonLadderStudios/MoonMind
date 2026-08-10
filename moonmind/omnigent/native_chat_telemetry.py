"""Bounded readiness/telemetry signals for native Omnigent Workflow Chat.

MoonLadderStudios/MoonMind#3642 (the native Workflow Chat rollout gate). The
native Chat journey crosses several MoonMind authority boundaries — binding
resolution, native-UI compatibility/load, the scoped HTTP/SSE/WebSocket facade,
authorization and identity-substitution guards, the immutable capability policy,
the high-security outbound scan, mutation delivery, diagnostic fallback, and
terminal replay/continuation. Operators need bounded operational visibility into
those outcomes to canary and, if needed, roll back the rollout.

This module is a small, self-contained signal registry for exactly that. It
deliberately reuses the canonical observability primitives
(:class:`moonmind.observability.metrics.MetricDefinition` and the canonical
:data:`moonmind.observability.metrics.FORBIDDEN_LABELS` identity ban) rather than
inventing parallel ones, and it is kept out of the always-on overview SLO
``REGISTRY`` because these are bounded rollout/readiness signals with their own
consumers, not steady-state service SLOs.

Hard rule (brief §10): Workflow, user, binding, session, and credential identity
are never metric labels. Every label here is a low-cardinality bounded dimension
(a journey stage, a bounded outcome, or a bounded rollout mode); unknown values
normalize to ``"other"`` so a caller can never inject unbounded/identifying
cardinality through a label value.
"""

from __future__ import annotations

from typing import Mapping

from moonmind.observability.metrics import FORBIDDEN_LABELS, MetricDefinition
from moonmind.utils.metrics import get_metrics_emitter

NATIVE_CHAT_TELEMETRY_VERSION = "moonmind.omnigent.native_chat_telemetry/v1"


# --- Bounded label dimensions -------------------------------------------------
# The journey stage a signal was recorded at. One bounded enumeration covers the
# whole native Chat path so a single omnibus counter (with an ``outcome``) can
# report binding resolution, transport requests, denials, scan decisions,
# mutation delivery, fallback, replay, and continuation without any per-identity
# label.
STAGE_BINDING_RESOLUTION = "binding_resolution"
STAGE_NATIVE_UI_COMPATIBILITY = "native_ui_compatibility"
STAGE_NATIVE_UI_LOAD = "native_ui_load"
STAGE_NATIVE_UI_RECONNECT = "native_ui_reconnect"
STAGE_HTTP_REQUEST = "http_request"
STAGE_SSE_STREAM = "sse_stream"
STAGE_WEBSOCKET = "websocket"
STAGE_AUTHORIZATION = "authorization"
STAGE_CAPABILITY = "capability"
STAGE_SECURITY_SCAN = "security_scan"
STAGE_RESOURCE = "resource"
STAGE_TERMINAL = "terminal"
STAGE_MUTATION = "mutation"
STAGE_DIAGNOSTIC_FALLBACK = "diagnostic_fallback"
STAGE_TERMINAL_REPLAY = "terminal_replay"
STAGE_CONTINUATION = "continuation"
STAGE_UPSTREAM = "upstream"

NATIVE_CHAT_STAGES: frozenset[str] = frozenset(
    {
        STAGE_BINDING_RESOLUTION,
        STAGE_NATIVE_UI_COMPATIBILITY,
        STAGE_NATIVE_UI_LOAD,
        STAGE_NATIVE_UI_RECONNECT,
        STAGE_HTTP_REQUEST,
        STAGE_SSE_STREAM,
        STAGE_WEBSOCKET,
        STAGE_AUTHORIZATION,
        STAGE_CAPABILITY,
        STAGE_SECURITY_SCAN,
        STAGE_RESOURCE,
        STAGE_TERMINAL,
        STAGE_MUTATION,
        STAGE_DIAGNOSTIC_FALLBACK,
        STAGE_TERMINAL_REPLAY,
        STAGE_CONTINUATION,
        STAGE_UPSTREAM,
    }
)

# Bounded outcome. Covers the distinctions the brief calls out: binding
# success/failure/ambiguity, authorization/substitution/capability denials,
# stale-state rejection, scan allow/block/enforcement-unavailable, and mutation
# accepted/completed/rejected/delivery-unknown.
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_DENIED = "denied"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_BLOCKED = "blocked"
OUTCOME_ENFORCEMENT_UNAVAILABLE = "enforcement_unavailable"
OUTCOME_STALE_REJECTED = "stale_rejected"
OUTCOME_DELIVERY_UNKNOWN = "delivery_unknown"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_UNAVAILABLE = "unavailable"

NATIVE_CHAT_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_SUCCESS,
        OUTCOME_FAILURE,
        OUTCOME_DENIED,
        OUTCOME_AMBIGUOUS,
        OUTCOME_BLOCKED,
        OUTCOME_ENFORCEMENT_UNAVAILABLE,
        OUTCOME_STALE_REJECTED,
        OUTCOME_DELIVERY_UNKNOWN,
        OUTCOME_TIMEOUT,
        OUTCOME_UNAVAILABLE,
    }
)

# Bounded rollout mode (mirrors ``native_chat_rollout`` modes). A rollout mode is
# a deployment posture, never an identity, so it is a safe bounded label.
ROLLOUT_MODES: frozenset[str] = frozenset(
    {"disabled", "read_only", "canary", "enabled"}
)

# Bounded readiness of the native UI compatibility/version gate.
READINESS_VALUES: frozenset[str] = frozenset({"ready", "degraded", "unavailable"})

BOUNDED_VALUES: dict[str, frozenset[str]] = {
    "native_chat_stage": NATIVE_CHAT_STAGES,
    "outcome": NATIVE_CHAT_OUTCOMES,
    "rollout_mode": ROLLOUT_MODES,
    "readiness": READINESS_VALUES,
}


# --- Signal registry ----------------------------------------------------------
REGISTRY: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        "moonmind_omnigent_native_chat_requests",
        "counter",
        "requests",
        ("native_chat_stage", "outcome"),
        "runtime",
        ("omnigent-native-chat", "native-chat-rollout"),
    ),
    MetricDefinition(
        "moonmind_omnigent_native_chat_upstream_latency_seconds",
        "histogram",
        "seconds",
        ("native_chat_stage",),
        "runtime",
        ("omnigent-native-chat", "native-chat-rollout"),
    ),
    MetricDefinition(
        "moonmind_omnigent_native_chat_ui_readiness",
        "gauge",
        "state",
        ("readiness",),
        "runtime",
        ("omnigent-native-chat", "native-chat-rollout"),
    ),
    MetricDefinition(
        "moonmind_omnigent_native_chat_rollout_state",
        "gauge",
        "state",
        ("rollout_mode",),
        "runtime",
        ("omnigent-native-chat", "native-chat-rollout"),
    ),
)


def definition(name: str) -> MetricDefinition:
    for metric in REGISTRY:
        if metric.name == name:
            return metric
    raise KeyError(f"unknown native chat telemetry signal: {name}")


def normalize_labels(metric_name: str, labels: Mapping[str, str]) -> dict[str, str]:
    """Return bounded, identity-free labels for a native chat signal.

    Fails closed on an unknown label key (a typo or an attempt to add an
    unregistered dimension) and collapses any out-of-band value to ``"other"``
    so a caller can never widen cardinality or leak an identity through a label
    value. Reuses the canonical :data:`FORBIDDEN_LABELS` as a defense-in-depth
    guard even though no signal here declares an identity label.
    """

    metric = definition(metric_name)
    banned = FORBIDDEN_LABELS.intersection(labels)
    if banned:
        raise ValueError(
            f"identity labels are forbidden on {metric_name}: {sorted(banned)}"
        )
    unknown = set(labels) - set(metric.labels)
    if unknown:
        raise ValueError(f"unknown labels for {metric_name}: {sorted(unknown)}")
    result: dict[str, str] = {}
    for key in metric.labels:
        value = str(labels.get(key, "unknown"))
        allowed = BOUNDED_VALUES[key]
        result[key] = value if value in allowed else "other"
    return result


def record_request(stage: str, outcome: str) -> None:
    """Best-effort emission for one native-chat authority-boundary outcome."""

    labels = normalize_labels(
        "moonmind_omnigent_native_chat_requests",
        {"native_chat_stage": stage, "outcome": outcome},
    )
    try:
        get_metrics_emitter().increment(
            "omnigent_native_chat_requests", tags=labels
        )
    except Exception:
        # Telemetry is auxiliary evidence and must never replace the primary
        # serving, denial, scan, or mutation outcome.
        return


def record_rollout(*, rollout_mode: str, readiness: str) -> None:
    """Emit the resolved deployment posture without request identities."""

    rollout_labels = normalize_labels(
        "moonmind_omnigent_native_chat_rollout_state",
        {"rollout_mode": rollout_mode},
    )
    readiness_labels = normalize_labels(
        "moonmind_omnigent_native_chat_ui_readiness",
        {"readiness": readiness},
    )
    try:
        emitter = get_metrics_emitter()
        emitter.increment("omnigent_native_chat_rollout_state", tags=rollout_labels)
        emitter.increment("omnigent_native_chat_ui_readiness", tags=readiness_labels)
    except Exception:
        return


def record_upstream_latency(seconds: float) -> None:
    """Record bounded upstream latency without attaching request identity."""

    labels = normalize_labels(
        "moonmind_omnigent_native_chat_upstream_latency_seconds",
        {"native_chat_stage": STAGE_UPSTREAM},
    )
    try:
        get_metrics_emitter().observe(
            "omnigent_native_chat_upstream_latency_seconds",
            value=max(0.0, float(seconds)),
            tags=labels,
        )
    except Exception:
        return


__all__ = [
    "BOUNDED_VALUES",
    "NATIVE_CHAT_OUTCOMES",
    "NATIVE_CHAT_STAGES",
    "NATIVE_CHAT_TELEMETRY_VERSION",
    "READINESS_VALUES",
    "REGISTRY",
    "ROLLOUT_MODES",
    "definition",
    "normalize_labels",
    "record_request",
    "record_rollout",
    "record_upstream_latency",
    # Stage constants
    "STAGE_AUTHORIZATION",
    "STAGE_BINDING_RESOLUTION",
    "STAGE_CAPABILITY",
    "STAGE_CONTINUATION",
    "STAGE_DIAGNOSTIC_FALLBACK",
    "STAGE_HTTP_REQUEST",
    "STAGE_MUTATION",
    "STAGE_NATIVE_UI_COMPATIBILITY",
    "STAGE_NATIVE_UI_LOAD",
    "STAGE_NATIVE_UI_RECONNECT",
    "STAGE_RESOURCE",
    "STAGE_SECURITY_SCAN",
    "STAGE_SSE_STREAM",
    "STAGE_TERMINAL_REPLAY",
    "STAGE_TERMINAL",
    "STAGE_UPSTREAM",
    "STAGE_WEBSOCKET",
    # Outcome constants
    "OUTCOME_AMBIGUOUS",
    "OUTCOME_BLOCKED",
    "OUTCOME_DELIVERY_UNKNOWN",
    "OUTCOME_DENIED",
    "OUTCOME_ENFORCEMENT_UNAVAILABLE",
    "OUTCOME_FAILURE",
    "OUTCOME_STALE_REJECTED",
    "OUTCOME_SUCCESS",
    "OUTCOME_TIMEOUT",
    "OUTCOME_UNAVAILABLE",
]
