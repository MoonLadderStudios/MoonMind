"""Remediation fleet metric + alert coverage for issue #3512, Area 7."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.observability.metrics import FORBIDDEN_LABELS
from moonmind.observability.remediation_metrics import (
    REMEDIATION_FLEET_REGISTRY,
    REMEDIATION_SIGNALS,
    RemediationMetricSink,
    normalize_remediation_labels,
    normalize_signal,
    remediation_alert_rules,
)

ROOT = Path(__file__).parents[3]

_REQUIRED_SIGNALS = {
    "action",
    "repeated_failure",
    "lock_conflict",
    "denial",
    "escalation",
    "unverified_mutation",
}


def test_registry_covers_the_six_required_signals_and_is_bounded():
    assert REMEDIATION_SIGNALS == _REQUIRED_SIGNALS
    assert len({m.name for m in REMEDIATION_FLEET_REGISTRY}) == len(
        REMEDIATION_FLEET_REGISTRY
    )
    for metric in REMEDIATION_FLEET_REGISTRY:
        assert metric.name.startswith("moonmind_remediation_")
        assert not FORBIDDEN_LABELS.intersection(metric.labels)
        assert metric.owner and metric.consumers


def test_registry_and_alerts_are_documented():
    doc = (ROOT / "docs/Observability/RemediationFleetMetrics.md").read_text()
    for metric in REMEDIATION_FLEET_REGISTRY:
        assert metric.name in doc
    for signal in _REQUIRED_SIGNALS:
        assert signal in doc
    runbook = ROOT / "docs/Runbooks/Observability/RemediationFleet.md"
    assert runbook.is_file()


def test_alerts_exist_for_each_signal_and_reference_runbook():
    rules = remediation_alert_rules()
    covered = {rule["signal"] for rule in rules}
    assert covered == _REQUIRED_SIGNALS
    for rule in rules:
        assert rule["runbook"] == "docs/Runbooks/Observability/RemediationFleet.md"
        assert rule["signal"] in rule["expr"]
    unverified = next(r for r in rules if r["signal"] == "unverified_mutation")
    assert unverified["severity"] == "critical"


def test_labels_are_bounded_and_reject_unknown_keys():
    labels = normalize_remediation_labels(
        "moonmind_remediation_events",
        {"signal": "action", "authority_mode": "admin_auto"},
    )
    assert labels == {"signal": "action", "authority_mode": "admin_auto"}
    # Unknown authority mode degrades to unknown.
    assert (
        normalize_remediation_labels(
            "moonmind_remediation_events",
            {"signal": "action", "authority_mode": "wat"},
        )["authority_mode"]
        == "unknown"
    )
    with pytest.raises(ValueError):
        normalize_remediation_labels(
            "moonmind_remediation_events", {"run_id": "x"}
        )


def test_unknown_signal_degrades_to_escalation():
    assert normalize_signal("action") == "action"
    assert normalize_signal("made-up") == "escalation"


def test_sink_counts_bounded_signals():
    sink = RemediationMetricSink()
    sink.record("action", authority_mode="admin_auto")
    sink.record("action", authority_mode="admin_auto")
    sink.record("unverified_mutation", authority_mode="admin_auto")
    assert sink.count("action") == 2
    assert sink.count("unverified_mutation", authority_mode="admin_auto") == 1
    assert sink.count("lock_conflict") == 0
