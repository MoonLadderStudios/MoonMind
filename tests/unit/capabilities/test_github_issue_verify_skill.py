from pathlib import Path

from moonmind.capabilities.input_contracts import (
    parse_skill_capability_input_contract,
    validate_capability_inputs,
)


def _load_contract() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    markdown = (
        repo_root / ".agents" / "skills" / "github-issue-verify" / "SKILL.md"
    ).read_text(encoding="utf-8")

    return parse_skill_capability_input_contract(
        skill_id="github-issue-verify",
        label="GitHub Issue Verify",
        markdown=markdown,
    )


def test_github_issue_verify_skill_exposes_batch_bindable_input_contract() -> None:
    contract = _load_contract()

    assert contract["inputSchema"]["required"] == ["github_issue"]
    properties = contract["inputSchema"]["properties"]
    issue = properties["github_issue"]
    assert issue["x-moonmind-provider"] == "github"
    assert issue["x-moonmind-semantic-type"] == "issue-reference"
    # github_issue accepts either the structured issue object (repository plus
    # number) or a manual reference string such as "owner/repo#123" or a URL.
    branches = issue["anyOf"]
    object_branch = next(branch for branch in branches if branch.get("type") == "object")
    assert object_branch["required"] == ["repository", "number"]
    assert any(branch.get("type") == "string" for branch in branches)
    assert properties["verification_mode"]["enum"] == ["auto", "branch", "main"]
    assert properties["mark_completed_if_pass"]["default"] is False
    assert contract["uiSchema"]["github_issue"]["widget"] == "github.issue-picker"
    assert contract["uiSchema"]["constraints"]["widget"] == "textarea"
    assert contract["defaults"] == {
        "verification_mode": "auto",
        "mark_completed_if_pass": False,
    }
    assert contract["diagnostics"] == []


def test_github_issue_verify_accepts_manual_and_structured_issue_refs() -> None:
    contract = _load_contract()

    # Advertised manual reference forms must launch through the shared backend
    # contract, not only the normalized object shape.
    for manual_ref in ("MoonLadderStudios/MoonMind#123", "https://github.com/o/r/issues/9"):
        result = validate_capability_inputs(
            contract=contract,
            values={"github_issue": manual_ref},
        )
        assert result["errors"] == []

    structured = validate_capability_inputs(
        contract=contract,
        values={"github_issue": {"repository": "MoonLadderStudios/MoonMind", "number": 123}},
    )
    assert structured["errors"] == []

    # A github_issue that is neither a usable reference string nor an object with
    # a numeric issue number is still rejected.
    rejected = validate_capability_inputs(
        contract=contract,
        values={"github_issue": {"repository": "MoonLadderStudios/MoonMind"}},
    )
    assert rejected["errors"]
