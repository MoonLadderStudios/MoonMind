"""Traceability checks for the MM-569 MoonSpec artifact set."""

from __future__ import annotations

from pathlib import Path

import pytest


FEATURE_DIR = Path("specs/331-model-step-type-payloads")
TRACEABILITY_TOKENS = [
    "MM-569",
    "manual-mm-569-mm-574",
    *[f"FR-{index:03d}" for index in range(1, 12)],
    *[f"SC-{index:03d}" for index in range(1, 7)],
    "DESIGN-REQ-012",
    "DESIGN-REQ-013",
    "DESIGN-REQ-014",
    "DESIGN-REQ-015",
    "DESIGN-REQ-018",
    "DESIGN-REQ-021",
]


def test_mm569_moonspec_artifacts_preserve_required_traceability() -> None:
    if not FEATURE_DIR.exists():
        pytest.skip("MoonSpec artifacts are not present in this branch")
    artifact_text = "\n".join(
        (FEATURE_DIR / name).read_text(encoding="utf-8")
        for name in (
            "spec.md",
            "plan.md",
            "research.md",
            "data-model.md",
            "contracts/step-type-validation-contract.md",
            "quickstart.md",
            "tasks.md",
        )
    )

    missing = [token for token in TRACEABILITY_TOKENS if token not in artifact_text]

    assert missing == []
