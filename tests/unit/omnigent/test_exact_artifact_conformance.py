"""Tests for the Tier-1 exact deployable-artifact conformance gate.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import copy

import pytest

from moonmind.omnigent.exact_artifact_conformance import (
    EXACT_ARTIFACT_CONFORMANCE_VERSION,
    REQUIRED_CAPABILITIES,
    ExactArtifactConformanceError,
    assert_exact_artifact_evidence,
    evaluate_exact_artifact_conformance,
)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
SERVER_DIGEST = "sha256:" + "a" * 64
WORKER_DIGEST = "sha256:" + "b" * 64
UI_DIGEST = "sha256:" + "c" * 64

REQUIRED_DIGESTS = {
    "server": SERVER_DIGEST,
    "worker": WORKER_DIGEST,
    "ui": UI_DIGEST,
}


def _capabilities(role: str) -> list[dict[str, object]]:
    return [
        {"name": name, "ok": True, "detail": f"{role} {name} present"}
        for name in REQUIRED_CAPABILITIES[role]
    ]


def _passing_report(**overrides) -> dict[str, object]:
    report = {
        "sourceCommit": COMMIT,
        "images": {
            "server": f"ghcr.io/moonladderstudios/moonmind@{SERVER_DIGEST}",
            "worker": f"ghcr.io/moonladderstudios/moonmind-worker@{WORKER_DIGEST}",
            "ui": f"ghcr.io/moonladderstudios/moonmind-ui@{UI_DIGEST}",
        },
        "capabilities": {role: _capabilities(role) for role in REQUIRED_CAPABILITIES},
        "fakeProviderExecution": {
            "terminalState": "converged",
            "restartAfterHostRemoval": True,
            "terminalReplayAfterHostRemoval": True,
        },
        "secretScan": {"status": "passed"},
    }
    report.update(overrides)
    return report


def test_passing_report_verifies_clean() -> None:
    projection = evaluate_exact_artifact_conformance(
        _passing_report(), required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "passed"
    assert projection["failures"] == []
    assert projection["schemaVersion"] == EXACT_ARTIFACT_CONFORMANCE_VERSION
    assert projection["sourceCommit"] == COMMIT


def test_missing_uvicorn_websocket_impl_fails_gate_3697() -> None:
    """#3697: a deployed image without a Uvicorn WebSocket impl must fail."""
    report = _passing_report()
    server_caps = report["capabilities"]["server"]
    for entry in server_caps:
        if entry["name"] == "uvicorn_websocket_impl":
            entry["ok"] = False
            entry["detail"] = "no installed WebSocket implementation"

    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "failed"
    codes = {failure["code"] for failure in projection["failures"]}
    assert "failed_capability:server:uvicorn_websocket_impl" in codes


def test_dropped_uvicorn_websocket_capability_fails_gate() -> None:
    report = _passing_report()
    report["capabilities"]["server"] = [
        entry
        for entry in report["capabilities"]["server"]
        if entry["name"] != "uvicorn_websocket_impl"
    ]
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "failed"
    codes = {failure["code"] for failure in projection["failures"]}
    assert "missing_capability:server:uvicorn_websocket_impl" in codes


def test_digest_mismatch_fails_closed() -> None:
    report = _passing_report()
    report["images"]["server"] = "ghcr.io/moonladderstudios/moonmind@sha256:" + "d" * 64
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "failed"
    assert any(
        failure["code"] == "digest_mismatch:server"
        for failure in projection["failures"]
    )


def test_unknown_required_digest_fails_closed() -> None:
    projection = evaluate_exact_artifact_conformance(
        _passing_report(),
        required_digests={"server": SERVER_DIGEST, "worker": WORKER_DIGEST},
    )
    assert projection["verdict"] == "failed"
    assert any(
        failure["code"] == "unknown_required_digest:ui"
        for failure in projection["failures"]
    )


def test_fake_provider_not_converged_fails_closed() -> None:
    report = _passing_report()
    report["fakeProviderExecution"]["terminalState"] = "running"
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "failed"
    assert any(
        failure["code"] == "fake_provider_not_converged"
        for failure in projection["failures"]
    )


def test_missing_restart_replay_fails_closed() -> None:
    report = _passing_report()
    report["fakeProviderExecution"]["terminalReplayAfterHostRemoval"] = False
    report["fakeProviderExecution"]["restartAfterHostRemoval"] = False
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    codes = {failure["code"] for failure in projection["failures"]}
    assert "restart_after_host_removal_failed" in codes
    assert "terminal_replay_after_host_removal_failed" in codes


def test_missing_fake_provider_execution_fails_closed() -> None:
    report = _passing_report()
    del report["fakeProviderExecution"]
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert any(
        failure["code"] == "fake_provider_execution_missing"
        for failure in projection["failures"]
    )


def test_secret_scan_not_passed_fails_closed() -> None:
    report = _passing_report(secretScan={"status": "failed"})
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert any(
        failure["code"] == "secret_scan_not_passed"
        for failure in projection["failures"]
    )


def test_unpinned_image_is_a_hard_error() -> None:
    report = _passing_report()
    report["images"]["server"] = "ghcr.io/moonladderstudios/moonmind:latest"
    with pytest.raises(ExactArtifactConformanceError):
        evaluate_exact_artifact_conformance(report, required_digests=REQUIRED_DIGESTS)


def test_worker_capability_gap_fails_closed() -> None:
    report = _passing_report()
    report["capabilities"]["worker"] = [
        entry
        for entry in report["capabilities"]["worker"]
        if entry["name"] != "worker_task_queues_advertised"
    ]
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert any(
        failure["code"] == "missing_capability:worker:worker_task_queues_advertised"
        for failure in projection["failures"]
    )


def test_ui_no_root_v1_capability_required() -> None:
    report = _passing_report()
    for entry in report["capabilities"]["ui"]:
        if entry["name"] == "no_root_v1_requests":
            entry["ok"] = False
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert any(
        failure["code"] == "failed_capability:ui:no_root_v1_requests"
        for failure in projection["failures"]
    )


def test_secret_like_material_in_evidence_is_rejected() -> None:
    report = _passing_report()
    report["capabilities"]["server"][0]["detail"] = "token=ghp_deadbeefdeadbeefdead"
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "failed"
    assert any(
        failure["code"] == "evidence_not_secret_free"
        for failure in projection["failures"]
    )
    # The offending material must not be re-echoed into the projection.
    assert "ghp_" not in str(projection)


def test_missing_source_commit_is_a_hard_error() -> None:
    report = _passing_report()
    del report["sourceCommit"]
    with pytest.raises(ExactArtifactConformanceError):
        evaluate_exact_artifact_conformance(report, required_digests=REQUIRED_DIGESTS)


# --- Downstream consumption (publication/readiness gate) --------------------


def _passing_projection() -> dict[str, object]:
    return copy.deepcopy(
        evaluate_exact_artifact_conformance(
            _passing_report(), required_digests=REQUIRED_DIGESTS
        )
    )


def test_assert_evidence_accepts_passing_projection() -> None:
    assert_exact_artifact_evidence(
        _passing_projection(),
        expected_commit=COMMIT,
        required_digests=REQUIRED_DIGESTS,
    )


def test_assert_evidence_rejects_failed_verdict() -> None:
    projection = _passing_projection()
    projection["verdict"] = "failed"
    projection["failures"] = [{"code": "digest_mismatch:server", "detail": "x"}]
    with pytest.raises(ExactArtifactConformanceError):
        assert_exact_artifact_evidence(projection, expected_commit=COMMIT)


def test_assert_evidence_rejects_commit_mismatch() -> None:
    with pytest.raises(ExactArtifactConformanceError):
        assert_exact_artifact_evidence(
            _passing_projection(), expected_commit="f" * 40
        )


def test_assert_evidence_rejects_digest_mismatch() -> None:
    with pytest.raises(ExactArtifactConformanceError):
        assert_exact_artifact_evidence(
            _passing_projection(),
            expected_commit=COMMIT,
            required_digests={**REQUIRED_DIGESTS, "server": "sha256:" + "e" * 64},
        )


def test_assert_evidence_rejects_unknown_schema() -> None:
    projection = _passing_projection()
    projection["schemaVersion"] = "some.other/v9"
    with pytest.raises(ExactArtifactConformanceError):
        assert_exact_artifact_evidence(projection, expected_commit=COMMIT)
