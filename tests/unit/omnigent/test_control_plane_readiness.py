"""New-admission readiness tests, including incident visibility cases.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

Covers fail-closed admission, the requirement that historical reads and cleanup
stay available, and the incident cases where a missing WebSocket runtime
capability and image-compatibility drift must be visible before admission.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from moonmind.omnigent.control_plane.readiness import (
    STRUCTURAL_CAPABILITIES,
    TRANSIENT_CAPABILITIES,
    ReadinessCapability,
    ReadinessClass,
    ReadinessInputs,
    ReadinessState,
    evaluate_admission_readiness,
    readiness_class,
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


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3885 — stable qualification vs transient
# availability. A busy or throttled installation waits; it does not become
# structurally unsupported, because "unsupported" is what invites a
# substitution to a different credential, profile, or runtime.
# ---------------------------------------------------------------------------


def test_capacity_capabilities_are_classified_transient():
    assert TRANSIENT_CAPABILITIES == {
        ReadinessCapability.PROVIDER_CAPACITY,
        ReadinessCapability.HOST_CAPACITY,
        ReadinessCapability.WORKER_CAPACITY,
    }
    assert not (TRANSIENT_CAPABILITIES & STRUCTURAL_CAPABILITIES)
    assert readiness_class(ReadinessCapability.SCHEMA) is ReadinessClass.STRUCTURAL
    assert (
        readiness_class(ReadinessCapability.HOST_CAPACITY) is ReadinessClass.TRANSIENT
    )


def test_unobserved_capacity_pressure_never_blocks_admission():
    """Absence of a capacity observation is not evidence of saturation."""

    readiness = evaluate_admission_readiness(_all_ready_inputs())
    for capability in TRANSIENT_CAPABILITIES:
        assert readiness.capability(capability).state is ReadinessState.READY
    assert readiness.admit_new is True
    assert readiness.wait_for_capacity is False


def test_a_transient_capability_still_fails_closed_when_structural():
    """The fail-closed rule is unchanged for every qualification capability."""

    readiness = evaluate_admission_readiness(ReadinessInputs())
    assert readiness.structural_blocking
    assert readiness.structurally_supported is False
    # A wholly unobserved deployment is not "waiting"; it is unqualified.
    assert readiness.wait_for_capacity is False


@pytest.mark.parametrize(
    "field,capability",
    [
        ("provider_capacity_available", ReadinessCapability.PROVIDER_CAPACITY),
        ("host_capacity_available", ReadinessCapability.HOST_CAPACITY),
        ("worker_capacity_available", ReadinessCapability.WORKER_CAPACITY),
    ],
)
def test_a_saturated_layer_waits_without_losing_structural_support(field, capability):
    readiness = evaluate_admission_readiness(_all_ready_inputs(**{field: False}))

    assert readiness.admit_new is False
    # The deployment is still qualified to run this combination.
    assert readiness.structurally_supported is True
    assert readiness.structural_blocking == ()
    assert readiness.transient_blocking == (capability,)
    assert readiness.wait_for_capacity is True
    assert "waits" in (readiness.capability(capability).detail or "")


def test_readiness_does_not_flap_between_busy_and_idle():
    """Structural support is identical whether or not another job is running."""

    idle = evaluate_admission_readiness(
        _all_ready_inputs(
            provider_capacity_available=True,
            host_capacity_available=True,
            worker_capacity_available=True,
        )
    )
    busy = evaluate_admission_readiness(
        _all_ready_inputs(
            provider_capacity_available=False,
            host_capacity_available=False,
            worker_capacity_available=False,
        )
    )

    assert idle.structurally_supported == busy.structurally_supported is True
    assert idle.structural_blocking == busy.structural_blocking == ()
    # Only admission and the waiting signal differ.
    assert idle.admit_new is True and busy.admit_new is False
    assert busy.wait_for_capacity is True
    assert len(busy.transient_blocking) == 3


def test_a_structural_failure_is_not_reported_as_a_capacity_wait():
    """A broken runtime capability must not be dressed up as a queue."""

    readiness = evaluate_admission_readiness(
        _all_ready_inputs(schema_compatible=False, host_capacity_available=False)
    )

    assert readiness.structurally_supported is False
    assert readiness.wait_for_capacity is False
    assert ReadinessCapability.SCHEMA in readiness.structural_blocking


def test_readiness_projection_publishes_the_class_of_every_capability():
    doc = evaluate_admission_readiness(
        _all_ready_inputs(provider_capacity_available=False)
    ).to_dict()

    assert doc["structurallySupported"] is True
    assert doc["waitForCapacity"] is True
    assert doc["transientBlocking"] == ["provider_capacity"]
    assert doc["structuralBlocking"] == []
    for entry in doc["capabilities"]:
        assert entry["readinessClass"] in {"structural", "transient"}
