from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/omnigent-live-conformance.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


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
    assert job["strategy"]["max-parallel"] == 1
    assert job["strategy"]["matrix"]["mode"] == [
        "product",
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
    assert run_step["env"]["OMNIGENT_ENABLED"] == "${{ vars.OMNIGENT_ENABLED }}"
    assert run_step["env"]["OMNIGENT_SERVER_URL"] == (
        "${{ vars.OMNIGENT_SERVER_URL }}"
    )
    assert run_step["env"]["OMNIGENT_API_TOKEN"] == (
        "${{ secrets.OMNIGENT_API_TOKEN }}"
    )
    assert run_step["env"]["OMNIGENT_DEFAULT_AGENT_NAME"] == (
        "${{ vars.OMNIGENT_DEFAULT_AGENT_NAME }}"
    )


def test_live_conformance_always_uploads_case_evidence() -> None:
    steps = _workflow()["jobs"]["live-matrix"]["steps"]
    upload = next(step for step in steps if step.get("name") == "Upload case evidence")
    stage = next(
        step for step in steps if step.get("name") == "Stage secret-safe case evidence"
    )
    assert 'if [[ "$LIVE_CASE_OUTCOME" == "success" ]]' in stage["run"]
    assert "withheld-after-unsuccessful-safety-gate" in stage["run"]
    assert upload["if"] == "always()"
    assert "github.run_attempt" in upload["with"]["name"]
    assert upload["with"]["path"].endswith("upload/${{ matrix.mode }}")
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
    assert "expected five passing reports" in manifest["run"]
    assert "MoonLadderStudios/MoonMind#3456" in manifest["run"]
    assert "MoonLadderStudios/MoonMind#3448" in manifest["run"]
    assert "moonmind.omnigent.product-acceptance/v1" in manifest["run"]
    assert '"commit": os.environ["GITHUB_SHA"]' in manifest["run"]
    upload = next(
        step
        for step in job["steps"]
        if step.get("name") == "Upload published matrix"
    )
    download = next(
        step
        for step in job["steps"]
        if step.get("name") == "Download passing case evidence"
    )
    assert "github.run_attempt" in download["with"]["pattern"]
    assert "github.run_attempt" in upload["with"]["name"]
    assert upload["with"]["retention-days"] == 90
    assert job["permissions"]["issues"] == "write"
    link = next(
        step
        for step in job["steps"]
        if step.get("name") == "Link passing acceptance report from issues 3456 and 3448"
    )
    assert "gh issue comment 3456" in link["run"]
    assert "gh issue comment 3448" in link["run"]
    assert "github.run_id" in link["run"]
    assert "github.run_attempt" in link["run"]
    assert "github.sha" in link["run"]
