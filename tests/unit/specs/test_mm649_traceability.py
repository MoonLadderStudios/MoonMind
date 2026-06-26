from __future__ import annotations

from pathlib import Path

import pytest


FEATURE_DIR = Path("specs/348-target-aware-step-scope")


def test_mm649_moonspec_artifacts_preserve_required_traceability() -> None:
    if not FEATURE_DIR.exists():
        pytest.skip("MoonSpec artifacts are not present in this branch")
    required = {
        "MM-649",
        "canonical Jira preset brief",
        "DESIGN-REQ-001",
        "DESIGN-REQ-002",
        "DESIGN-REQ-021",
        "DESIGN-REQ-022",
        "FR-009",
        "SC-005",
    }
    artifact_names = [
        "spec.md",
        "plan.md",
        "research.md",
        "quickstart.md",
        "tasks.md",
        "moonspec_align_report.md",
    ]

    for artifact_name in artifact_names:
        text = (FEATURE_DIR / artifact_name).read_text()
        missing = sorted(item for item in required if item not in text)
        assert not missing, f"{artifact_name} missing {missing}"
