from pathlib import Path

import pytest


FEATURE_DIR = Path("specs/341-server-side-validation")


def test_mm656_moonspec_artifacts_preserve_issue_and_source_traceability():
    if not FEATURE_DIR.exists():
        pytest.skip("MoonSpec artifacts are not present in this branch")
    spec = (FEATURE_DIR / "spec.md").read_text(encoding="utf-8")
    plan = (FEATURE_DIR / "plan.md").read_text(encoding="utf-8")
    tasks = (FEATURE_DIR / "tasks.md").read_text(encoding="utf-8")
    combined = "\n".join([spec, plan, tasks])

    assert "MM-656" in spec
    assert "Jira preset brief" in spec
    assert "FR-012" in combined
    assert "SC-005" in combined
    for index in range(1, 12):
        assert f"DESIGN-REQ-{index:03d}" in combined
