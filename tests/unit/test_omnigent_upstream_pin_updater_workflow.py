"""Structure tests for the omnigent upstream-pin updater workflow.

Source issue: MoonLadderStudios/MoonMind#3957.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/omnigent-upstream-pin-updater.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on") or workflow.get(True) or {}


def test_updater_is_scheduled_weekly_and_dispatchable() -> None:
    triggers = _triggers(_workflow())
    assert triggers["schedule"] == [{"cron": "0 7 * * 1"}]
    dispatch = triggers["workflow_dispatch"]["inputs"]
    assert "allow_prereleases" in dispatch
    assert dispatch["allow_prereleases"].get("default") is False
    assert dispatch["upstream_repo"]["default"] == "omnigent-ai/omnigent"
    assert "dry_run" in dispatch


def test_updater_uses_minimum_permissions() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    # Only the propose job (which opens the single candidate PR) escalates.
    assert workflow["jobs"]["plan"].get("permissions", workflow["permissions"]) == {
        "contents": "read",
        "actions": "read",
    }
    propose_permissions = workflow["jobs"]["propose"]["permissions"]
    assert propose_permissions["contents"] == "write"
    assert propose_permissions["pull-requests"] == "write"


def test_updater_never_runs_fetched_code_or_production_secrets() -> None:
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "OMNIGENT_API_TOKEN" not in raw
    assert "MOONMIND_OMNIGENT_ACTION_COMMAND" not in raw
    for match in re.finditer(r"secrets\.([A-Za-z_]+)", raw):
        assert match.group(0) == "secrets.GITHUB_TOKEN", match.group(0)
    assert "secrets.GITHUB_TOKEN" not in raw or "github.token" in raw
    # No piping fetched archives into a shell, no package installs from
    # upstream, no checkout of the upstream repository itself.
    assert "curl " not in raw or "| sh" not in raw
    assert "| sh" not in raw
    assert "| bash" not in raw
    assert "omnigent-ai/omnigent@" not in raw


def test_updater_is_idempotent_single_pr() -> None:
    workflow = _workflow()
    assert workflow["concurrency"]["group"] == "omnigent-upstream-pin-updater"
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    # One controlled branch per candidate commit, updated in place.
    assert "automation/omnigent-pin-" in raw
    assert "gh pr view" in raw
    assert "gh pr create" in raw
    assert "force-with-lease" in raw
    # Never changes main directly: the pointer moves on the candidate branch.
    assert "--base main" in raw or "--base=main" in raw
    assert "dry_run" in raw


def test_updater_plans_through_hermetic_cli() -> None:
    steps = _workflow()["jobs"]["plan"]["steps"]
    run_text = "\n".join(step.get("run", "") for step in steps)
    assert "tools/check_omnigent_upstream_pin_update.py" in run_text
    assert "git ls-tree HEAD omnigent" in run_text
    assert "repos/" in run_text and "/releases" in run_text


def test_updater_runs_owning_hermetic_shards() -> None:
    steps = _workflow()["jobs"]["qualify"]["steps"]
    run_text = "\n".join(step.get("run", "") for step in steps)
    for shard in (
        "tests/unit/omnigent/test_adapter_contracts.py",
        "tests/unit/omnigent/test_host_registration_inventory.py",
        "tests/unit/omnigent/test_oauth_home_materializers.py",
        "tests/unit/omnigent/test_omnigent_session_timeline_api.py",
        "tests/unit/omnigent/test_native_ui.py",
        "tests/unit/omnigent/test_omnigent_facade_unknown_route_fails_closed.py",
        "tests/unit/omnigent/test_execution_support_evidence.py",
        "tests/unit/omnigent/test_host_cleanup_service.py",
    ):
        assert shard in run_text


def test_updater_records_evidence_and_freshness() -> None:
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "candidate.json" in raw
    assert "evidence.json" in raw
    assert "status.json" in raw
    assert "known-good pin intact" in raw
    assert "upload-artifact" in raw


def test_updater_leaves_pin_intact_without_candidate() -> None:
    workflow = _workflow()
    assert (
        workflow["jobs"]["qualify"]["if"]
        == "needs.plan.outputs.outcome == 'candidate_available'"
    )
    assert "candidate_available" in workflow["jobs"]["propose"]["if"]
    assert "needs.qualify.result == 'success'" in workflow["jobs"]["propose"]["if"]
