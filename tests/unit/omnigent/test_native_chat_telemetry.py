"""Bounded, identity-free native Chat telemetry signal tests (#3642 §10)."""

from __future__ import annotations

import pytest

from moonmind.observability.metrics import FORBIDDEN_LABELS
from moonmind.omnigent import native_chat_telemetry as tel


def test_every_signal_is_bounded_and_identity_free() -> None:
    names = [m.name for m in tel.REGISTRY]
    assert len(names) == len(set(names))
    for metric in tel.REGISTRY:
        assert metric.name.startswith("moonmind_omnigent_native_chat_")
        assert metric.owner and metric.consumers
        # No metric may carry a Workflow/user/binding/session/credential identity.
        assert not FORBIDDEN_LABELS.intersection(metric.labels)
        for label in metric.labels:
            assert label in tel.BOUNDED_VALUES


def test_definition_rejects_unknown_signal() -> None:
    with pytest.raises(KeyError):
        tel.definition("moonmind_omnigent_native_chat_unknown")


def test_normalize_bounds_values_and_rejects_identity_labels() -> None:
    labels = tel.normalize_labels(
        "moonmind_omnigent_native_chat_requests",
        {"native_chat_stage": tel.STAGE_MUTATION, "outcome": tel.OUTCOME_DELIVERY_UNKNOWN},
    )
    assert labels == {
        "native_chat_stage": "mutation",
        "outcome": "delivery_unknown",
    }
    # Out-of-band value collapses to "other" rather than widening cardinality.
    collapsed = tel.normalize_labels(
        "moonmind_omnigent_native_chat_requests",
        {"native_chat_stage": "not_a_stage", "outcome": tel.OUTCOME_SUCCESS},
    )
    assert collapsed["native_chat_stage"] == "other"


def test_unknown_label_key_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown labels"):
        tel.normalize_labels(
            "moonmind_omnigent_native_chat_requests",
            {"native_chat_stage": tel.STAGE_MUTATION, "outcome": tel.OUTCOME_SUCCESS, "region": "us"},
        )


def test_identity_label_is_rejected_even_if_registered_key_collision() -> None:
    # session_id is a FORBIDDEN identity label; even attempting it fails closed.
    with pytest.raises(ValueError, match="identity labels are forbidden"):
        tel.normalize_labels(
            "moonmind_omnigent_native_chat_ui_readiness",
            {"session_id": "abc"},
        )


def test_stages_cover_every_journey_boundary() -> None:
    # The brief §10 boundaries must each have a stage.
    for stage in (
        tel.STAGE_BINDING_RESOLUTION,
        tel.STAGE_NATIVE_UI_COMPATIBILITY,
        tel.STAGE_NATIVE_UI_LOAD,
        tel.STAGE_NATIVE_UI_RECONNECT,
        tel.STAGE_HTTP_REQUEST,
        tel.STAGE_SSE_STREAM,
        tel.STAGE_WEBSOCKET,
        tel.STAGE_AUTHORIZATION,
        tel.STAGE_CAPABILITY,
        tel.STAGE_SECURITY_SCAN,
        tel.STAGE_MUTATION,
        tel.STAGE_DIAGNOSTIC_FALLBACK,
        tel.STAGE_TERMINAL_REPLAY,
        tel.STAGE_CONTINUATION,
        tel.STAGE_UPSTREAM,
    ):
        assert stage in tel.NATIVE_CHAT_STAGES
