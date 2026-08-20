"""Structure tests for the Omnigent live-verification health workflow.

Source issue: MoonLadderStudios/MoonMind#3710.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/omnigent-live-verification-health.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def test_health_workflow_is_scheduled_and_dispatchable() -> None:
    triggers = _triggers(_workflow())
    assert triggers["schedule"] == [{"cron": "17 * * * *"}]
    assert "workflow_dispatch" in triggers


def test_health_workflow_runs_on_reliable_infra_without_provider_secrets() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["health"]
    # Must not depend on the unreliable self-hosted provider runner.
    assert job["runs-on"] == "ubuntu-latest"
    assert "environment" not in job
    # It must not consume provider credentials.
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "OMNIGENT_API_TOKEN" not in raw
    assert not re.search(r"secrets\.", raw)


def test_health_workflow_fails_readiness_closed_via_cli() -> None:
    steps = _workflow()["jobs"]["health"]["steps"]
    assemble = next(
        step for step in steps if step.get("name") == "Assemble non-secret status document"
    )
    gate = next(
        step
        for step in steps
        if step.get("name") == "Fail readiness closed on unhealthy protected tier"
    )
    assert "tools/assemble_omnigent_live_status.py" in assemble["run"]
    assert "tools/omnigent_live_verification_health.py" in gate["run"]
    assert "--status status.json" in gate["run"]
