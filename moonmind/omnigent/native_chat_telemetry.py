"""Bounded readiness/telemetry signals for native Omnigent Workflow Chat.

MoonLadderStudios/MoonMind#3642 (the native Workflow Chat rollout gate). The
native Chat journey crosses several MoonMind authority boundaries — binding
resolution, native-UI compatibility/load, the scoped HTTP/SSE/WebSocket facade,
authorization and identity-substitution guards, the immutable capability policy,
the high-security outbound scan, mutation delivery, diagnostic fallback, and
terminal replay/continuation. Operators need bounded operational visibility into
those outcomes to canary and, if needed, roll back the rollout.

This module is the production adapter for those signals. It deliberately reuses
the canonical observability primitives
(:class:`moonmind.observability.metrics.MetricDefinition` and the canonical
:data:`moonmind.observability.metrics.FORBIDDEN_LABELS` identity ban); the
definitions live in the canonical always-on registry and this adapter emits
through MoonMind's shared StatsD boundary.

Hard rule (brief §10): Workflow, user, binding, session, and credential identity
are never metric labels. Every label here is a low-cardinality bounded dimension
(a journey stage, a bounded outcome, or a bounded rollout mode); unknown values
normalize to ``"other"`` so a caller can never inject unbounded/identifying
cardinality through a label value.
"""

from __future__ import annotations

from typing import Mapping

from moonmind.observability.metrics import (
    FORBIDDEN_LABELS,
    REGISTRY as CANONICAL_REGISTRY,
    MetricDefinition,
    definition as canonical_definition,
    normalize_labels as canonical_normalize_labels,
)
from moonmind.utils.metrics import _MetricsEmitter, get_metrics_emitter

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
REGISTRY: tuple[MetricDefinition, ...] = tuple(
    metric
    for metric in CANONICAL_REGISTRY
    if metric.name.startswith("moonmind_omnigent_native_chat_")
)


def definition(name: str) -> MetricDefinition:
    metric = canonical_definition(name)
    if metric not in REGISTRY:
        raise KeyError(f"unknown native chat telemetry signal: {name}")
    return metric


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
    return canonical_normalize_labels(metric_name, labels)


class NativeChatTelemetry:
    """Production adapter from the canonical registry to the shared exporter."""

    def __init__(self, emitter: _MetricsEmitter | None = None) -> None:
        self._emitter = emitter or get_metrics_emitter()

    def request(self, *, stage: str, outcome: str) -> None:
        tags = normalize_labels(
            "moonmind_omnigent_native_chat_requests",
            {"native_chat_stage": stage, "outcome": outcome},
        )
        self._emitter.increment_canonical(
            "moonmind_omnigent_native_chat_requests", tags=tags
        )

    def upstream_latency(self, *, stage: str, seconds: float) -> None:
        tags = normalize_labels(
            "moonmind_omnigent_native_chat_upstream_latency_seconds",
            {"native_chat_stage": stage},
        )
        self._emitter.observe_canonical(
            "moonmind_omnigent_native_chat_upstream_latency_seconds",
            value=max(0.0, float(seconds)),
            tags=tags,
        )

    def readiness(self, value: str) -> None:
        selected = value if value in READINESS_VALUES else "other"
        for readiness in (*sorted(READINESS_VALUES), "other"):
            tags = normalize_labels(
                "moonmind_omnigent_native_chat_ui_readiness",
                {"readiness": readiness},
            )
            self._emitter.gauge_canonical(
                "moonmind_omnigent_native_chat_ui_readiness",
                value=1 if readiness == selected else 0,
                tags=tags,
            )

    def rollout(self, mode: str) -> None:
        selected = mode if mode in ROLLOUT_MODES else "other"
        for rollout_mode in (*sorted(ROLLOUT_MODES), "other"):
            tags = normalize_labels(
                "moonmind_omnigent_native_chat_rollout_state",
                {"rollout_mode": rollout_mode},
            )
            self._emitter.gauge_canonical(
                "moonmind_omnigent_native_chat_rollout_state",
                value=1 if rollout_mode == selected else 0,
                tags=tags,
            )


_telemetry: NativeChatTelemetry | None = None


def get_native_chat_telemetry() -> NativeChatTelemetry:
    global _telemetry
    if _telemetry is None:
        _telemetry = NativeChatTelemetry()
    return _telemetry


__all__ = [
    "BOUNDED_VALUES",
    "NATIVE_CHAT_OUTCOMES",
    "NATIVE_CHAT_STAGES",
    "NATIVE_CHAT_TELEMETRY_VERSION",
    "NativeChatTelemetry",
    "READINESS_VALUES",
    "REGISTRY",
    "ROLLOUT_MODES",
    "definition",
    "get_native_chat_telemetry",
    "normalize_labels",
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
    "STAGE_SECURITY_SCAN",
    "STAGE_SSE_STREAM",
    "STAGE_TERMINAL_REPLAY",
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
