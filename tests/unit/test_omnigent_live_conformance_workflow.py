from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/omnigent-live-conformance.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True)


def test_live_conformance_is_scheduled_and_manually_gated() -> None:
    workflow = _workflow()
    triggers = _triggers(workflow)
    assert triggers["schedule"] == [{"cron": "41 6 * * 1"}]
    assert set(triggers["workflow_dispatch"]["inputs"]) == {
        "server_image",
        "host_image",
    }
    job = workflow["jobs"]["live-matrix"]
    assert job["environment"] == "omnigent-provider-verification"
    assert job["runs-on"] == ["self-hosted", "linux", "omnigent-provider-verification"]


def test_live_conformance_runs_the_complete_independent_matrix() -> None:
    job = _workflow()["jobs"]["live-matrix"]
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"]["mode"] == [
        "stock",
        "static",
        "ondemand",
        "failures",
    ]
    run_step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Run credentialed live matrix case"
    )
    assert "tools/run_omnigent_live_conformance.py" in run_step["run"]
    assert '--mode "${{ matrix.mode }}"' in run_step["run"]
    assert run_step["env"]["MOONMIND_OMNIGENT_ACTION_COMMAND"] == (
        "${{ secrets.MOONMIND_OMNIGENT_ACTION_COMMAND }}"
    )


def test_live_conformance_always_uploads_case_evidence() -> None:
    steps = _workflow()["jobs"]["live-matrix"]["steps"]
    upload = next(step for step in steps if step.get("name") == "Upload case evidence")
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == 30


def test_publication_requires_every_matrix_case_to_pass() -> None:
    job = _workflow()["jobs"]["publish-matrix"]
    assert job["needs"] == "live-matrix"
    assert job["if"] == "${{ needs.live-matrix.result == 'success' }}"
    manifest = next(
        step
        for step in job["steps"]
        if step.get("name") == "Write publication manifest"
    )
    assert "expected four passing reports" in manifest["run"]
    assert "MoonLadderStudios/MoonMind#3368" in manifest["run"]
    upload = next(
        step
        for step in job["steps"]
        if step.get("name") == "Upload published matrix"
    )
    assert upload["with"]["retention-days"] == 90
