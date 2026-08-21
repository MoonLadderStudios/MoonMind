"""Telemetry contract tests for Omnigent semantic spans and metric families.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

Covers the issue's telemetry contract requirements:
- every domain decision/command maps to a bounded semantic field;
- no secret-like or forbidden large payload appears in span/metric data;
- metric label inventories remain low cardinality and identity-free;
- exporter failure cannot alter application correctness.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.control_plane import metrics, spans, telemetry
from moonmind.omnigent.control_plane.records import FencingScope
from moonmind.omnigent.reconciler import DecisionKind


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


# --- Spans ------------------------------------------------------------------


def test_span_names_are_closed_and_prefixed():
    assert spans.OMNIGENT_SPANS
    for name in spans.OMNIGENT_SPANS:
        assert name.startswith("omnigent.")
    # A recommended-but-unknown name is rejected.
    assert not spans.is_omnigent_span("omnigent.not.a.real.span")


def test_sanitize_drops_unknown_keys_and_keeps_bounded_values():
    safe = spans.sanitize_span_attributes(
        {
            "decision_class": DecisionKind.SUBMIT_TURN.value,
            "expected_revision": 7,
            "attempt_ordinal": 2,
            "workflow_id": "wf-secret-identity",  # unknown key -> dropped
            "session_id": "sess-123",  # unknown key -> dropped
        }
    )
    assert safe == {
        "decision_class": "submit_turn",
        "expected_revision": 7,
        "attempt_ordinal": 2,
    }


@pytest.mark.parametrize(
    "forbidden_value",
    [
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "https://bucket.s3.amazonaws.com/x?X-Amz-Signature=deadbeef",
        "/home/app/secrets/token",
        "-----BEGIN RSA PRIVATE KEY-----",
        "line one\nline two transcript",
        "x" * (spans.MAX_ATTRIBUTE_VALUE_LEN + 1),
    ],
)
def test_sanitize_drops_secret_like_and_oversized_values(forbidden_value):
    safe = spans.sanitize_span_attributes({"reason_code": forbidden_value})
    assert "reason_code" not in safe


def test_omnigent_span_never_raises_and_runs_body_without_tracer():
    ran = []
    with spans.omnigent_span(spans.TURN_SUBMIT, decision_class="submit_turn"):
        ran.append(True)
    assert ran == [True]


def test_omnigent_span_unknown_name_is_noop_but_runs_body():
    ran = []
    with spans.omnigent_span("omnigent.bogus", decision_class="submit_turn"):
        ran.append(True)
    assert ran == [True]


def test_span_exporter_failure_cannot_change_correctness(monkeypatch):
    class _Boom:
        def start_as_current_span(self, name):
            raise RuntimeError("exporter down")

    monkeypatch.setattr(spans, "_tracer", lambda: _Boom())
    result = []
    with spans.omnigent_span(spans.SESSION_RECONCILE, reason_code="provider_running"):
        result.append("work-did-run")
    assert result == ["work-did-run"]


# --- Metrics ----------------------------------------------------------------


def test_every_decision_kind_maps_to_a_bounded_decision_class():
    allowed = metrics.BOUNDED_LABEL_VALUES["decision_class"]
    for kind in DecisionKind:
        assert kind.value in allowed


def test_metric_labels_are_low_cardinality_and_identity_free():
    inventory = metrics.label_inventory()
    assert inventory  # non-empty
    for name, labels in inventory.items():
        assert not (set(labels) & metrics.FORBIDDEN_LABEL_KEYS), name
        for label in labels:
            # Every declared label has a closed bounded value vocabulary.
            assert label in metrics.BOUNDED_LABEL_VALUES, (name, label)


def test_forbidden_identity_label_is_rejected_at_record_time():
    with pytest.raises(ValueError):
        metrics.increment(
            metrics.RECONCILIATION_DECISIONS,
            decision_class="submit_turn",
            reason_class="awaiting",
            session_id="sess-1",  # identity label -> rejected
        )


def test_out_of_vocabulary_label_value_collapses_to_other():
    metrics.increment(
        metrics.RECONCILIATION_DECISIONS,
        decision_class="not_a_real_decision",
        reason_class="awaiting",
    )
    counters = metrics.snapshot()["counters"]
    assert any("'other'" in key and "'awaiting'" in key for key in counters), counters


def test_observation_metric_aggregates_count_total_last():
    metrics.observe(metrics.SNAPSHOT_LATENCY, 0.5)
    metrics.observe(metrics.SNAPSHOT_LATENCY, 1.5)
    obs = metrics.snapshot()["observations"]
    key = next(iter(obs))
    assert obs[key]["count"] == 2
    assert obs[key]["total"] == 2.0
    assert obs[key]["last"] == 1.5


def test_metrics_record_into_the_opentelemetry_plane(monkeypatch):
    class _Instrument:
        def __init__(self):
            self.calls = []

        def add(self, value, *, attributes):
            self.calls.append(("add", value, attributes))

        def record(self, value, *, attributes):
            self.calls.append(("record", value, attributes))

    counter = _Instrument()
    histogram = _Instrument()
    monkeypatch.setattr(
        metrics,
        "_otel_instrument",
        lambda definition: counter
        if definition.kind == metrics.COUNTER
        else histogram,
    )

    metrics.increment(
        metrics.RECONCILIATION_DECISIONS,
        decision_class="submit_turn",
        reason_class="awaiting",
    )
    metrics.observe(metrics.RECONCILIATION_CONVERGENCE_LATENCY, 1.25)

    assert counter.calls == [
        (
            "add",
            1,
            {"decision_class": "submit_turn", "reason_class": "awaiting"},
        )
    ]
    assert histogram.calls == [("record", 1.25, {})]


def test_otel_metric_failure_cannot_change_local_evidence(monkeypatch):
    monkeypatch.setattr(
        metrics,
        "_otel_instrument",
        lambda _definition: (_ for _ in ()).throw(RuntimeError("exporter down")),
    )
    metrics.increment(metrics.QUARANTINED_AMBIGUITY)
    assert next(iter(metrics.snapshot()["counters"].values())) == 1


def test_concurrency_metrics_record_into_opentelemetry_and_preserve_local_evidence(
    monkeypatch,
):
    class _Counter:
        def __init__(self):
            self.calls = []

        def add(self, value, *, attributes):
            self.calls.append((value, attributes))

    counter = _Counter()
    telemetry.reset()
    monkeypatch.setattr(telemetry, "_otel_counter", lambda _name: counter)

    telemetry.record_revision_conflict(scope=FencingScope.SESSION_SUPERVISOR)

    assert counter.calls == [(1, {"fencing_scope": "session_supervisor"})]
    assert telemetry.snapshot()[(telemetry.REVISION_CONFLICTS, "session_supervisor")] == 1
    telemetry.reset()


def test_concurrency_otel_failure_cannot_change_local_evidence(monkeypatch):
    telemetry.reset()
    monkeypatch.setattr(
        telemetry,
        "_otel_counter",
        lambda _name: (_ for _ in ()).throw(RuntimeError("meter unavailable")),
    )

    telemetry.record_duplicate_command_suppressed()

    assert telemetry.snapshot()[(telemetry.DUPLICATE_COMMAND_SUPPRESSED, "session")] == 1
    telemetry.reset()


def test_counter_and_observation_kinds_are_enforced():
    with pytest.raises(TypeError):
        metrics.observe(metrics.RECONCILIATION_DECISIONS, 1.0, decision_class="no_op", reason_class="awaiting")
    with pytest.raises(TypeError):
        metrics.increment(metrics.SNAPSHOT_LATENCY)


def test_unknown_metric_name_fails_closed():
    with pytest.raises(KeyError):
        metrics.increment("omnigent_not_a_metric")


def test_all_required_metric_families_present():
    names = set(metrics.METRICS)
    # A representative from each of the four required families.
    assert metrics.RECONCILIATION_DECISIONS in names
    assert metrics.TRANSPORT_READINESS in names
    assert metrics.JANITOR_OPERATIONS in names
    assert metrics.EXACT_IMAGE_CONFORMANCE in names
