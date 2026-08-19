"""Tests for the exact-artifact runtime-evidence assembly core.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

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


def _signals(role: str) -> list[dict[str, object]]:
    return [gather.signal(name, True, f"{role} {name}") for name in REQUIRED_CAPABILITIES[role]]


def _fake_provider() -> dict[str, object]:
    return {
        "terminalState": "converged",
        "restartAfterHostRemoval": True,
        "terminalReplayAfterHostRemoval": True,
    }


def test_build_runtime_evidence_marks_secret_scan_passed() -> None:
    evidence = gather.build_runtime_evidence(
        server=_signals("server"),
        worker=_signals("worker"),
        ui=_signals("ui"),
        fake_provider_execution=_fake_provider(),
    )
    assert evidence["secretScan"]["status"] == "passed"
    assert set(evidence["capabilities"]) == {"server", "worker", "ui"}


def test_runtime_evidence_feeds_a_passing_gate() -> None:
    evidence = gather.build_runtime_evidence(
        server=_signals("server"),
        worker=_signals("worker"),
        ui=_signals("ui"),
        fake_provider_execution=_fake_provider(),
    )
    report = {
        "sourceCommit": COMMIT,
        "images": {
            "server": f"img@{SERVER_DIGEST}",
            "worker": f"img@{WORKER_DIGEST}",
            "ui": f"img@{UI_DIGEST}",
        },
        "capabilities": evidence["capabilities"],
        "fakeProviderExecution": evidence["fakeProviderExecution"],
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
            fake_provider_execution=_fake_provider(),
        )
