from __future__ import annotations

import json
from pathlib import Path


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "provider"
    / "mm1063_lifecycle"
)


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_mm1063_recorded_trusted_tool_lifecycle_evidence_is_complete() -> None:
    fixtures = [
        _load_fixture("github_issue_creation.json"),
        _load_fixture("github_workflow_handoff.json"),
        _load_fixture("github_status_update.json"),
        _load_fixture("jira_lifecycle_transition.json"),
        _load_fixture("pr_publication_gate.json"),
    ]

    assert {(item["provider"], item["boundary"]) for item in fixtures} == {
        ("github", "creation"),
        ("github", "handoff"),
        ("github", "status_update"),
        ("jira", "lifecycle_transition"),
        ("github", "pr_publication_gate"),
    }
    assert all(item["mode"] == "recorded_trusted_tool" for item in fixtures)

    creation = _load_fixture("github_issue_creation.json")
    creation_text = json.dumps(creation)
    assert "issueCreation" in creation_text
    assert "jiraCreation" not in creation_text
    assert creation["input"]["storyOutput"]["dependencyMode"] == "none"
    assert creation["output"]["storyOutput"]["dependencyMode"] == "none"
    assert creation["output"]["storyOutput"]["dependencyCount"] == 0

    handoff = _load_fixture("github_workflow_handoff.json")
    handoff_text = json.dumps(handoff)
    assert "breakdown workflow" in handoff_text
    assert "breakdown task" not in handoff_text
    assert "MoonMind task" not in handoff_text

    github_status = _load_fixture("github_status_update.json")
    assert github_status["trustedArtifacts"]["verification"]["verdict"] == (
        "FULLY_IMPLEMENTED"
    )
    assert "verificationArtifactPath" in github_status["input"]
    assert github_status["output"]["targetStatus"] == "code_review"

    jira_transition = _load_fixture("jira_lifecycle_transition.json")
    assert jira_transition["trustedArtifacts"]["verification"]["verdict"] == (
        "FULLY_IMPLEMENTED"
    )
    assert jira_transition["output"]["transitioned"] is True

    pr_gate = _load_fixture("pr_publication_gate.json")
    assert pr_gate["trustedArtifacts"]["verification"]["verdict"] != (
        "FULLY_IMPLEMENTED"
    )
    assert pr_gate["output"]["created"] is False
    assert pr_gate["output"]["status"] == "BLOCKED"


def test_mm1063_recorded_lifecycle_fixtures_document_residual_live_provider_risk() -> None:
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")

    assert "Residual live-provider risk" in readme
    assert "token scope validity" in readme
    assert "organization-specific label/status policies" in readme
