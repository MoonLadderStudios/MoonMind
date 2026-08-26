"""New-admission readiness tests, including incident visibility cases.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

Covers fail-closed admission, the requirement that historical reads and cleanup
stay available, and the incident cases where a missing WebSocket runtime
capability and image-compatibility drift must be visible before admission.
"""

from __future__ import annotations

from datetime import timedelta

from moonmind.omnigent.control_plane.readiness import (
    ReadinessCapability,
    ReadinessInputs,
    ReadinessState,
    evaluate_admission_readiness,
)


def _all_ready_inputs(**overrides) -> ReadinessInputs:
    base = dict(
        reconciler_generation_ready=True,
        schema_compatible=True,
        provider_snapshot_ready=True,
        event_transport_ready=True,
        server_build_ready=True,
        ui_build_ready=True,
        host_build_ready=True,
        websocket_available=True,
        worker_backend_ready=True,
        container_backend_ready=True,
        observation_age=timedelta(seconds=30),
        janitor_healthy=True,
        exact_image_conformant=True,
        protected_live_evidence_age=timedelta(hours=1),
    )
    base.update(overrides)
    return ReadinessInputs(**base)


def test_all_capabilities_ready_admits_new():
    readiness = evaluate_admission_readiness(_all_ready_inputs())
    assert readiness.admit_new is True
    assert readiness.blocking == ()


def test_unknown_capability_fails_closed():
    # A completely unspecified input set is unknown across the board -> not admitted.
    readiness = evaluate_admission_readiness(ReadinessInputs())
    assert readiness.admit_new is False
    assert readiness.blocking  # non-empty


def test_historical_reads_and_cleanup_stay_available_even_when_blocked():
    readiness = evaluate_admission_readiness(ReadinessInputs())
    assert readiness.admit_new is False
    assert readiness.allow_historical_reads is True
    assert readiness.allow_cleanup is True


def test_missing_websocket_capability_visible_and_blocks_admission():
    readiness = evaluate_admission_readiness(_all_ready_inputs(websocket_available=False))
    assert readiness.admit_new is False
    assert ReadinessCapability.WEBSOCKET in readiness.blocking
    ws = readiness.capability(ReadinessCapability.WEBSOCKET)
    assert ws.state is ReadinessState.NOT_READY
    assert ws.detail  # actionable detail present


def test_image_compatibility_drift_visible_before_admission():
    readiness = evaluate_admission_readiness(_all_ready_inputs(exact_image_conformant=False))
    assert readiness.admit_new is False
    assert ReadinessCapability.EXACT_IMAGE in readiness.blocking


def test_stale_protected_live_evidence_blocks_admission():
    readiness = evaluate_admission_readiness(
        _all_ready_inputs(protected_live_evidence_age=timedelta(hours=48))
    )
    assert readiness.admit_new is False
    assert ReadinessCapability.PROTECTED_LIVE_EVIDENCE in readiness.blocking


def test_stale_observation_freshness_blocks_admission():
    readiness = evaluate_admission_readiness(
        _all_ready_inputs(observation_age=timedelta(hours=1))
    )
    assert readiness.admit_new is False
    assert ReadinessCapability.OBSERVATION_FRESHNESS in readiness.blocking


def test_readiness_to_dict_is_bounded_and_serializable():
    doc = evaluate_admission_readiness(_all_ready_inputs()).to_dict()
    assert doc["admitNew"] is True
    assert isinstance(doc["capabilities"], list)
    # Every capability value is from the closed vocabulary.
    allowed = {c.value for c in ReadinessCapability}
    for entry in doc["capabilities"]:
        assert entry["capability"] in allowed
        assert entry["state"] in {"ready", "not_ready", "unknown"}
