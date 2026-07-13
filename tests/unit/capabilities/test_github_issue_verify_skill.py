from pathlib import Path

from moonmind.capabilities.input_contracts import (
    parse_skill_capability_input_contract,
)


def test_github_issue_verify_skill_exposes_batch_bindable_input_contract() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    markdown = (
        repo_root / ".agents" / "skills" / "github-issue-verify" / "SKILL.md"
    ).read_text(encoding="utf-8")

    contract = parse_skill_capability_input_contract(
        skill_id="github-issue-verify",
        label="GitHub Issue Verify",
        markdown=markdown,
    )

    assert contract["inputSchema"]["required"] == ["github_issue"]
    properties = contract["inputSchema"]["properties"]
    issue = properties["github_issue"]
    assert issue["x-moonmind-provider"] == "github"
    assert issue["required"] == ["repository", "number"]
    assert properties["verification_mode"]["enum"] == ["auto", "branch", "main"]
    assert properties["mark_completed_if_pass"]["default"] is False
    assert contract["uiSchema"]["github_issue"]["widget"] == "github.issue-picker"
    assert contract["uiSchema"]["constraints"]["widget"] == "textarea"
    assert contract["defaults"] == {
        "verification_mode": "auto",
        "mark_completed_if_pass": False,
    }
    assert contract["diagnostics"] == []
