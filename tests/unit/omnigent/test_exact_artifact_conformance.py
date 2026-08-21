"""Tests for the Tier-1 exact deployable-artifact conformance gate.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import copy
import json
from hashlib import sha256

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
    route_inventory = {
        "schemaVersion": "moonmind.omnigent.native-ui-route-inventory/v2",
        "artifactDigests": {
            "omnigent": "git:" + "1" * 40,
            "ui": "sha256:" + "2" * 64,
            "server": "sha256:" + "3" * 64,
            "host": "sha256:" + "4" * 64,
            "harnessImplementation": "sha256:" + "5" * 64,
            "moonmindFacade": "sha256:" + "6" * 64,
        },
        "routes": [
            {
                "routeKey": "GET /health",
                "transport": "http",
                "classification": "binding_scoped",
                "publicRoute": "/api/workflow-chat-bindings/{chatBindingId}/omnigent/health",
                "callerPermission": "binding_owner_or_explicit_read_grant",
                "requestBounds": {"maxBodyBytes": 0},
                "responseBounds": {"maxBodyBytes": 1024},
                "identityVirtualization": "provider_session_id_to_chat_binding_id",
                "reconnect": "reauthorize_each_request",
                "idempotency": "read_only",
                "historicalRead": "live_only",
                "unsupportedBehavior": "not_applicable",
                "mutationReceipt": None,
            }
        ],
        "uiRouteReferences": [
            {"path": "/v1/info", "sourceFile": "omnigent/web/src/lib/api.ts"}
        ],
        "websocketProtocols": [],
        "sseProtocols": [],
        "routeCount": 1,
        "classifiedRouteCount": 1,
        "unclassifiedRouteCount": 0,
    }
    route_inventory["inventoryDigest"] = "sha256:" + sha256(
        json.dumps(route_inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report = {
        "sourceCommit": COMMIT,
        "images": {
            "server": f"ghcr.io/moonladderstudios/moonmind@{SERVER_DIGEST}",
            "worker": f"ghcr.io/moonladderstudios/moonmind-worker@{WORKER_DIGEST}",
            "ui": f"ghcr.io/moonladderstudios/moonmind-ui@{UI_DIGEST}",
        },
        "capabilities": {role: _capabilities(role) for role in REQUIRED_CAPABILITIES},
        "routeInventory": route_inventory,
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


def test_failed_entrypoint_restart_fails_closed() -> None:
    """A deployable process that cannot restart against its own schema fails."""
    report = _passing_report()
    for entry in report["capabilities"]["server"]:
        if entry["name"] == "api_restart_against_existing_schema":
            entry["ok"] = False
            entry["detail"] = "entrypoint did not become healthy again"
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "failed"
    assert any(
        failure["code"] == "failed_capability:server:api_restart_against_existing_schema"
        for failure in projection["failures"]
    )


def test_gate_does_not_assert_unexercised_provider_execution() -> None:
    """The Tier-1 gate must not claim a boundary its driver never crosses.

    This driver runs no provider execution, so restart/terminal replay after a
    fake host is removed is owned by the reliability-journey and
    embedded-recovery gates. Requiring it here could only be satisfied by
    reusing an unrelated exit status, which is fabricated evidence.
    """
    required = {name for names in REQUIRED_CAPABILITIES.values() for name in names}
    assert "restart_after_host_removal" not in required
    assert "terminal_replay_after_host_removal" not in required
    # A report that carries no provider-execution section still passes, because
    # the gate never asserted one.
    report = _passing_report()
    assert "fakeProviderExecution" not in report
    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )
    assert projection["verdict"] == "passed"


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


def test_missing_route_inventory_fails_exact_artifact_gate() -> None:
    report = _passing_report()
    del report["routeInventory"]

    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )

    assert any(
        failure["code"] == "route_inventory_missing"
        for failure in projection["failures"]
    )


def test_unclassified_exact_stock_route_fails_exact_artifact_gate() -> None:
    report = _passing_report()
    report["routeInventory"]["routes"][0]["classification"] = "unclassified"
    report["routeInventory"]["unclassifiedRouteCount"] = 1
    report["routeInventory"]["classifiedRouteCount"] = 0

    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )

    assert any(
        failure["code"] == "route_inventory_unclassified"
        for failure in projection["failures"]
    )


def test_missing_transport_protocol_classification_fails_exact_artifact_gate() -> None:
    report = _passing_report()
    report["routeInventory"]["routes"][0]["transport"] = "sse"

    projection = evaluate_exact_artifact_conformance(
        report, required_digests=REQUIRED_DIGESTS
    )

    assert any(
        failure["code"] == "route_inventory_unclassified"
        for failure in projection["failures"]
    )


def test_route_inventory_digest_drift_fails_exact_artifact_gate() -> None:
    report = _passing_report()

    projection = evaluate_exact_artifact_conformance(
        report,
        required_digests=REQUIRED_DIGESTS,
        required_route_inventory_digest="sha256:" + "e" * 64,
    )

    assert any(
        failure["code"] == "route_inventory_digest_mismatch"
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


def test_assert_evidence_rejects_missing_route_inventory() -> None:
    projection = _passing_projection()
    del projection["routeInventory"]
    with pytest.raises(ExactArtifactConformanceError):
        assert_exact_artifact_evidence(projection, expected_commit=COMMIT)
