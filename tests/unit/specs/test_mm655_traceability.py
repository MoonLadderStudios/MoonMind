from pathlib import Path


FEATURE_DIR = Path("specs/340-effective-value-resolver")


def test_mm655_traceability_artifacts_preserve_original_request():
    spec = (FEATURE_DIR / "spec.md").read_text()
    plan = (FEATURE_DIR / "plan.md").read_text()
    tasks = (FEATURE_DIR / "tasks.md").read_text()

    assert "MM-655" in spec
    assert "# MM-655 MoonSpec Orchestration Input" in spec
    assert "Effective-value resolver with source explanation and operator locks" in spec
    assert "MM-655" in plan
    assert "MM-655" in tasks
    assert "FR-014" in tasks
    assert "SCN-007" in tasks
    assert "SC-006" in tasks
