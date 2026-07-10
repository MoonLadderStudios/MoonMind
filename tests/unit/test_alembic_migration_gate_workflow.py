from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "alembic-migration-gate.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True)) or {}


def test_gate_runs_for_merge_candidates_and_merge_queue() -> None:
    workflow = _load_workflow()
    triggers = _triggers(workflow)

    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["merge_group"]["types"] == ["checks_requested"]
    assert triggers["push"]["branches"] == ["main"]

    checkout = workflow["jobs"]["migration-gate"]["steps"][0]
    assert checkout["name"] == "Check out merge candidate"
    assert "ref" not in checkout.get("with", {})


def test_gate_checks_graph_and_upgrades_clean_postgres() -> None:
    job = _load_workflow()["jobs"]["migration-gate"]
    assert job["services"]["postgres"]["image"] == "postgres:17"

    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "python tools/check_alembic_graph.py" in commands
    assert "upgrade head" in commands
    assert "current --check-heads" in commands
