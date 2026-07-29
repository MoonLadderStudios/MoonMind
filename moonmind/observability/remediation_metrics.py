"""Remediation fleet metrics and alerts (issue #3512, Area 7).

Autonomous remediation must not be enabled until operators can *see* fleet
behavior: how often mutating actions run, whether the same failure keeps
recurring, lock contention, denials, escalations, and — most importantly —
side-effecting mutations that were never verified. This module is the canonical,
bounded definition of those signals plus their alert rules.

It is intentionally decoupled from the global overview dashboard registry in
``moonmind.observability.metrics`` so the remediation fleet can evolve its own
signals and alerts without repinning the general SLO dashboard. Labels stay
bounded and never carry per-run identity (see ``FORBIDDEN_LABELS``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from moonmind.observability.metrics import (
    FORBIDDEN_LABELS,
    MetricDefinition,
)

# The six operator-facing remediation fleet signals required before autonomous
# remediation may be enabled.
REMEDIATION_SIGNALS = frozenset(
    {
        "action",              # a side-effecting action was delivered (action rate)
        "repeated_failure",    # the same failure signature recurred
        "lock_conflict",       # a mutation lock could not be acquired
        "denial",              # an action was denied or required approval
        "escalation",          # remediation escalated to a human/operator
        "unverified_mutation",  # a mutation applied without a passing verification
    }
)

REMEDIATION_METRIC_BOUNDED_VALUES: dict[str, frozenset[str]] = {
    "signal": REMEDIATION_SIGNALS,
    "authority_mode": frozenset(
        {"observe_only", "approval_gated", "admin_auto", "unknown"}
    ),
}

REMEDIATION_FLEET_REGISTRY: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        "moonmind_remediation_events",
        "counter",
        "events",
        ("signal", "authority_mode"),
        "remediation",
        ("remediation-fleet", "remediation-rollout-gate"),
    ),
)


def _definition(name: str) -> MetricDefinition:
    for metric in REMEDIATION_FLEET_REGISTRY:
        if metric.name == name:
            return metric
    raise KeyError(f"unknown remediation fleet metric: {name}")


def normalize_signal(value: str) -> str:
    """Return a bounded remediation signal, degrading unknowns to ``escalation``.

    An unknown signal is treated as an escalation-worthy anomaly rather than
    dropped, so it stays visible to operators.
    """

    normalized = str(value or "").strip()
    return normalized if normalized in REMEDIATION_SIGNALS else "escalation"


def normalize_remediation_labels(
    metric_name: str, labels: Mapping[str, str]
) -> dict[str, str]:
    """Bound and validate labels for a remediation fleet metric."""

    metric = _definition(metric_name)
    unknown = set(labels) - set(metric.labels)
    if unknown:
        raise ValueError(f"unknown labels for {metric_name}: {sorted(unknown)}")
    if FORBIDDEN_LABELS.intersection(metric.labels):
        raise ValueError(f"{metric_name} declares a forbidden identity label")
    result: dict[str, str] = {}
    for key in metric.labels:
        value = str(labels.get(key, "unknown"))
        allowed = REMEDIATION_METRIC_BOUNDED_VALUES[key]
        result[key] = value if value in allowed else (
            "unknown" if "unknown" in allowed else "escalation"
        )
    return result


class RemediationMetricSink:
    """In-process bounded counter sink used for tests and no-op emission."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str]] = Counter()

    def record(self, signal: str, *, authority_mode: str = "unknown") -> dict[str, str]:
        labels = normalize_remediation_labels(
            "moonmind_remediation_events",
            {"signal": normalize_signal(signal), "authority_mode": authority_mode},
        )
        self._counts[(labels["signal"], labels["authority_mode"])] += 1
        return labels

    def count(self, signal: str, *, authority_mode: str | None = None) -> int:
        normalized = normalize_signal(signal)
        if authority_mode is None:
            return sum(
                value
                for (sig, _mode), value in self._counts.items()
                if sig == normalized
            )
        return self._counts[(normalized, authority_mode)]


def remediation_alert_rules() -> tuple[dict[str, object], ...]:
    """Return the remediation fleet alert rules.

    Every rule references the shared remediation runbook and selects a single
    bounded signal so operators get a distinct alert per failure mode.
    """

    runbook = "docs/Runbooks/Observability/RemediationFleet.md"
    metric = "moonmind_remediation_events"

    def _rule(signal: str, severity: str, summary: str) -> dict[str, object]:
        camel = "".join(part.capitalize() for part in signal.split("_"))
        return {
            "alert": f"Remediation{camel}",
            "signal": signal,
            "expr": f'increase({metric}{{signal="{signal}"}}[15m]) > 0',
            "severity": severity,
            "summary": summary,
            "runbook": runbook,
        }

    return (
        _rule("action", "info", "Remediation mutating action rate"),
        _rule("repeated_failure", "warning", "Same remediation failure recurring"),
        _rule("lock_conflict", "warning", "Remediation mutation lock contention"),
        _rule("denial", "info", "Remediation action denied or approval-gated"),
        _rule("escalation", "warning", "Remediation escalated to an operator"),
        _rule(
            "unverified_mutation",
            "critical",
            "Remediation mutation applied without passing verification",
        ),
    )


__all__ = [
    "REMEDIATION_FLEET_REGISTRY",
    "REMEDIATION_METRIC_BOUNDED_VALUES",
    "REMEDIATION_SIGNALS",
    "RemediationMetricSink",
    "normalize_remediation_labels",
    "normalize_signal",
    "remediation_alert_rules",
]
