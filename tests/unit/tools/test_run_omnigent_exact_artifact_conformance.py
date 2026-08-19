"""Tests for the exact-artifact conformance driver's pure core.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import json

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


def test_require_docker_fails_loud_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(driver.shutil, "which", lambda _name: None)
    with pytest.raises(driver.DriverError):
        driver._require_docker()


def test_assemble_report_merges_runtime_over_in_image() -> None:
    # In-image probe reports the import-level subset only; runtime evidence
    # supplies the entrypoint/route/migration/fake-provider capabilities.
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
        "fakeProviderExecution": {
            "terminalState": "converged",
            "restartAfterHostRemoval": True,
            "terminalReplayAfterHostRemoval": True,
        },
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
