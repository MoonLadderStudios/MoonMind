"""Verification-only traceability test for MM-680 MoonSpec artifacts."""

from __future__ import annotations

from pathlib import Path


def test_mm680_traceability_preserved_in_plan_and_tasks() -> None:
    feature_dir = Path("specs/335-agent-tool-surface-isolation")
    plan = (feature_dir / "plan.md").read_text(encoding="utf-8")
    tasks = (feature_dir / "tasks.md").read_text(encoding="utf-8")

    assert "MM-680" in plan
    assert "MM-680" in tasks
    assert "Generalizable Agent Tool-Surface Isolation" in plan
    assert "original Jira preset brief" in tasks

