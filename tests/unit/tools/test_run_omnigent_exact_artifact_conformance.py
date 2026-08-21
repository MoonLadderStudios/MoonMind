"""Tests for the exact-artifact conformance driver's pure core.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import json
from hashlib import sha256

import pytest

from moonmind.omnigent.exact_artifact_conformance import (
    REQUIRED_CAPABILITIES,
    evaluate_exact_artifact_conformance,
)
from tools import run_omnigent_exact_artifact_conformance as driver

SERVER_DIGEST = "sha256:" + "a" * 64
WORKER_DIGEST = "sha256:" + "b" * 64
UI_DIGEST = "sha256:" + "c" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"

IMAGES = {
    "server": f"ghcr.io/moonladderstudios/moonmind@{SERVER_DIGEST}",
    "worker": f"ghcr.io/moonladderstudios/moonmind-worker@{WORKER_DIGEST}",
    "ui": f"ghcr.io/moonladderstudios/moonmind-ui@{UI_DIGEST}",
}


def _signals(role: str) -> list[dict[str, object]]:
    return [
        {"name": name, "ok": True, "detail": f"{role} {name}"}
        for name in REQUIRED_CAPABILITIES[role]
    ]


def _compiled_ui_surface(inventory: dict[str, object]) -> dict[str, object]:
    reference = inventory["uiRouteReferences"][0]
    body: dict[str, object] = {
        "routeLiterals": [
            {
                "pathPattern": reference["path"],
                "sourceFile": "assets/index.js",
                "literalDigest": "sha256:" + "2" * 64,
                "classification": "scoped_transport_adapter",
                "resolution": "exact_method_path_allowlist",
                "resolvedRoutes": [
                    {
                        "method": reference["method"],
                        "path": reference["path"],
                        "routeKey": reference["routeKey"],
                    }
                ],
                "unknownBehavior": "omnigent_chat_transport_unsupported",
            }
        ],
        "routeLiteralCount": 1,
        "classifiedRouteLiteralCount": 1,
    }
    body["digest"] = "sha256:" + sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return body


def _exact_route_inventory() -> dict[str, object]:
    inventory = json.loads(driver.ROUTE_INVENTORY_FIXTURE.read_text(encoding="utf-8"))
    artifact_digests = inventory["artifactDigests"]
    inventory["artifactProvenance"] = {
        "sourceMode": "running_images",
        "generationBoundary": "inside_pinned_omnigent_server_image",
        "compiledUiDigest": "sha256:" + "d" * 64,
        "deployableImages": {
            "omnigentServer": "omnigent-server@sha256:" + "e" * 64,
            "omnigentHost": "omnigent-host@sha256:" + "f" * 64,
            "moonmindFacade": IMAGES["server"],
        },
        "inImageArtifactDigests": {
            "omnigentHost": artifact_digests["host"],
            "moonmindFacade": artifact_digests["moonmindFacade"],
            "moonmindHarness": "sha256:" + "1" * 64,
        },
        "compiledUiNetworkSurface": _compiled_ui_surface(inventory),
    }
    inventory.pop("inventoryDigest", None)
    inventory["inventoryDigest"] = "sha256:" + sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return inventory


def test_require_docker_fails_loud_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(driver.shutil, "which", lambda _name: None)
    with pytest.raises(driver.DriverError):
        driver._require_docker()


def test_assemble_report_merges_runtime_over_in_image() -> None:
    # In-image probe reports the import-level subset only; runtime evidence
    # supplies the entrypoint/route/migration/restart capabilities.
    in_image = {
        "server": [
            {"name": "uvicorn_websocket_impl", "ok": True, "detail": "impl present"},
            {"name": "omnigent_adapters_import", "ok": True, "detail": "ok"},
        ],
        "worker": [
            {"name": "omnigent_adapters_import", "ok": True, "detail": "ok"},
        ],
    }
    runtime_evidence = {
        "capabilities": {role: _signals(role) for role in REQUIRED_CAPABILITIES},
        "routeInventory": _exact_route_inventory(),
        "secretScan": {"status": "passed"},
    }
    report = driver.assemble_report(
        images=IMAGES,
        source_commit=COMMIT,
        in_image_probes=in_image,
        runtime_evidence=runtime_evidence,
    )
    projection = evaluate_exact_artifact_conformance(
        report,
        required_digests={
            "server": SERVER_DIGEST,
            "worker": WORKER_DIGEST,
            "ui": UI_DIGEST,
        },
    )
    assert projection["verdict"] == "passed", projection["failures"]


def test_load_required_digests_defaults_to_image_digests() -> None:
    digests = driver.load_required_digests(None, IMAGES)
    assert digests == {
        "server": SERVER_DIGEST,
        "worker": WORKER_DIGEST,
        "ui": UI_DIGEST,
    }


def test_load_required_digests_reads_manifest(tmp_path) -> None:
    manifest = tmp_path / "compat.json"
    manifest.write_text(
        json.dumps(
            {"digests": {"server": SERVER_DIGEST, "worker": WORKER_DIGEST, "ui": UI_DIGEST}}
        ),
        encoding="utf-8",
    )
    digests = driver.load_required_digests(manifest, IMAGES)
    assert digests["server"] == SERVER_DIGEST


def test_assembled_report_carries_no_unexercised_provider_execution() -> None:
    """The driver runs no provider execution, so it records none."""
    report = driver.assemble_report(
        images=IMAGES,
        source_commit=COMMIT,
        in_image_probes={},
        runtime_evidence={
            "capabilities": {role: _signals(role) for role in REQUIRED_CAPABILITIES},
            "routeInventory": _exact_route_inventory(),
            "secretScan": {"status": "passed"},
            # Even if an upstream document carried one, the driver must not
            # promote an unexercised claim into the gate's report.
            "fakeProviderExecution": {"terminalState": "converged"},
        },
    )

    assert "fakeProviderExecution" not in report


def test_assemble_report_rejects_checkout_inventory() -> None:
    inventory = _exact_route_inventory()
    inventory["artifactProvenance"]["sourceMode"] = "checkout"
    with pytest.raises(driver.DriverError, match="not exact-image evidence"):
        driver.assemble_report(
            images=IMAGES,
            source_commit=COMMIT,
            in_image_probes={},
            runtime_evidence={
                "capabilities": {},
                "routeInventory": inventory,
            },
        )


def test_in_image_probe_runs_the_locally_resolvable_content_id() -> None:
    """A locally loaded image has no repo digest, so `name@sha256:` is unpullable."""
    runnable = "sha256:" + "a" * 64
    command = driver.in_image_probe_command(runnable, "worker")

    assert command[:3] == ["docker", "run", "--rm"]
    assert command[command.index(runnable) - 1] == "python"
    assert command[-2:] == ["--role", "worker"]
