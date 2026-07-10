from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "codex-conformance-canary.yml"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "provider" / "codex" / "long_command_canary.md"


def _load_workflow() -> dict:
    assert WORKFLOW_PATH.exists(), f"Missing workflow: {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _workflow_on(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True)


def test_codex_canary_workflow_has_required_triggers() -> None:
    triggers = _workflow_on(_load_workflow())

    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert triggers["workflow_dispatch"]["inputs"]["candidate_digest"]["required"] is True
    assert triggers["workflow_dispatch"]["inputs"]["candidate_image_ref"]["required"] is True

    push_paths = set(triggers["push"]["paths"])
    assert "api_service/Dockerfile" in push_paths
    assert "api_service/docker/**" in push_paths
    assert "moonmind/workflows/adapters/codex_session_adapter.py" in push_paths
    assert "moonmind/workflows/temporal/runtime/codex_session_runtime.py" in push_paths
    assert "moonmind/codex_conformance/**" in push_paths


def test_codex_canary_job_publishes_versioned_compact_evidence() -> None:
    workflow = _load_workflow()
    job = workflow["jobs"]["codex-canary"]
    steps = job["steps"]

    run_step = next(step for step in steps if step.get("name") == "Run live Codex canary")
    assert "tools/run_codex_conformance_canary.py" in run_step["run"]
    assert "--candidate-digest" in run_step["run"]
    assert "--output artifacts/codex-conformance/canary-result.json" in run_step["run"]
    assert run_step["env"]["MOONMIND_API_TOKEN"] == "${{ secrets.MOONMIND_CODEX_CANARY_API_TOKEN }}"

    validate_step = next(step for step in steps if step.get("name") == "Validate compact evidence")
    assert "pytest tests/provider/codex" in validate_step["run"]
    assert "provider_verification and codex" in validate_step["run"]

    upload_step = next(step for step in steps if step.get("name") == "Upload conformance result")
    assert upload_step["uses"].startswith("actions/upload-artifact@")
    assert upload_step["with"]["path"] == "artifacts/codex-conformance/canary-result.json"
    assert upload_step["with"]["retention-days"] == 30


def test_codex_canary_has_dedicated_harmless_fixture() -> None:
    text = FIXTURE_PATH.read_text(encoding="utf-8")

    assert "MoonLadderStudios/MoonMind#3150" in text
    assert "var/conformance/long_command_result.json" in text
    assert "Do not mutate GitHub" in text
