"""Tests for the exact-artifact runtime-evidence assembly core.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.exact_artifact_conformance import (
    REQUIRED_CAPABILITIES,
    evaluate_exact_artifact_conformance,
)
from tools import gather_exact_artifact_runtime_evidence as gather

SERVER_DIGEST = "sha256:" + "a" * 64
WORKER_DIGEST = "sha256:" + "b" * 64
UI_DIGEST = "sha256:" + "c" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROUTE_INVENTORY = (
    _REPO_ROOT / "tests/fixtures/omnigent/native_ui_network_contract_v2.json"
)


def _signals(role: str) -> list[dict[str, object]]:
    return [
        gather.signal(name, True, f"{role} {name}")
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
    inventory = json.loads(_ROUTE_INVENTORY.read_text(encoding="utf-8"))
    artifact_digests = inventory["artifactDigests"]
    inventory["artifactProvenance"] = {
        "sourceMode": "running_images",
        "generationBoundary": "inside_pinned_omnigent_server_image",
        "compiledUiDigest": "sha256:" + "d" * 64,
        "deployableImages": {
            "omnigentServer": "omnigent-server@sha256:" + "e" * 64,
            "omnigentHost": "omnigent-host@sha256:" + "f" * 64,
            "moonmindFacade": f"img@{SERVER_DIGEST}",
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


def test_build_runtime_evidence_marks_secret_scan_passed() -> None:
    evidence = gather.build_runtime_evidence(
        server=_signals("server"),
        worker=_signals("worker"),
        ui=_signals("ui"),
        route_inventory={"inventoryDigest": "sha256:" + "d" * 64},
    )
    assert evidence["secretScan"]["status"] == "passed"
    assert set(evidence["capabilities"]) == {"server", "worker", "ui"}
    assert evidence["routeInventory"]["inventoryDigest"].startswith("sha256:")


def test_runtime_evidence_feeds_a_passing_gate() -> None:
    route_inventory = _exact_route_inventory()
    evidence = gather.build_runtime_evidence(
        server=_signals("server"),
        worker=_signals("worker"),
        ui=_signals("ui"),
        route_inventory=route_inventory,
    )
    report = {
        "sourceCommit": COMMIT,
        "images": {
            "server": f"img@{SERVER_DIGEST}",
            "worker": f"img@{WORKER_DIGEST}",
            "ui": f"img@{UI_DIGEST}",
        },
        "capabilities": evidence["capabilities"],
        "routeInventory": evidence["routeInventory"],
        "secretScan": evidence["secretScan"],
    }
    projection = evaluate_exact_artifact_conformance(
        report,
        required_digests={
            "server": SERVER_DIGEST,
            "worker": WORKER_DIGEST,
            "ui": UI_DIGEST,
        },
    )
    assert projection["verdict"] == "passed", projection["failures"]


def test_build_runtime_evidence_rejects_secret_material() -> None:
    tainted = _signals("server")
    tainted[0]["detail"] = "authorization: Bearer sk-abcdef0123456789"
    with pytest.raises(ConformanceContractError):
        gather.build_runtime_evidence(
            server=tainted,
            worker=_signals("worker"),
            ui=_signals("ui"),
            route_inventory={},
        )
