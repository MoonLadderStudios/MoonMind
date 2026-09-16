"""Portable-bundle packaging for #4273 R10.

Per the epic packaging contract (#4275 immutable-bundle availability plus
#4276 progressive disclosure), selected-provider command catalogs and payload
templates live in portable ``references/`` files inside the skill bundle, not
inline in the root entrypoint. The root keeps portable intent: goal,
applicability, required inputs, authority/evidence rules, and a link that
loads the reference only when that provider path is needed.
"""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"

# Skills whose roots held inline provider command catalogs/templates at the
# 293888bcb candidate (verifier R10 evidence).
_PACKAGED_SKILLS = (
    "code-improvement-proposal",
    "jira-verify",
    "github-issue-verify",
    "github-issue-to-jira",
    "jira-pr-verify",
)


def _root(skill: str) -> str:
    return (_SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


def _reference(skill: str) -> str:
    path = _SKILLS_DIR / skill / "references" / "provider-commands.md"
    assert path.exists(), f"portable provider reference missing: {path}"
    return path.read_text(encoding="utf-8")


def test_portable_provider_references_exist_and_roots_link_them() -> None:
    for skill in _PACKAGED_SKILLS:
        text = _root(skill)
        assert "references/provider-commands.md" in text, skill
        # The reference itself must exist and be non-trivial.
        assert len(_reference(skill)) > 200, skill


def test_roots_hold_no_fenced_provider_command_catalogs() -> None:
    for skill in _PACKAGED_SKILLS:
        text = _root(skill)
        for catalog in (
            "gh auth status",
            "gh repo view",
            "gh issue list",
            "gh issue create",
            "gh issue comment",
            "gh issue close",
            "gh issue edit",
            "gh label create",
            "gh pr view",
            "gh pr comment",
            "gh pr checks",
            "gh pr diff",
            "curl -fsS",
            "curl -sS",
            "POST $MOONMIND_URL/mcp/tools/call",
            '"issuetype": {"name": "Task"}',
        ):
            assert catalog not in text, f"{skill}: inline catalog {catalog!r}"


def test_references_carry_the_moved_catalogs() -> None:
    cip = _reference("code-improvement-proposal")
    assert "gh issue create" in cip
    assert '"issuetype"' in cip
    assert "Atlassian Document Format" in cip

    assert "POST $MOONMIND_URL/mcp/tools/call" in _reference("jira-verify")

    gh_verify = _reference("github-issue-verify")
    assert "gh auth status" in gh_verify
    assert "gh issue comment" in gh_verify

    to_jira = _reference("github-issue-to-jira")
    assert "gh issue view" in to_jira
    assert "gh issue comment" in to_jira

    pr_verify = _reference("jira-pr-verify")
    assert "gh pr view" in pr_verify
    assert "jira.get_issue" in pr_verify


def test_roots_keep_portable_intent_authority_and_evidence() -> None:
    for skill in (
        "code-improvement-proposal",
        "jira-verify",
        "github-issue-verify",
        "github-issue-to-jira",
    ):
        lowered = _root(skill).lower()
        # Authority boundaries and secret hygiene stay visible in the root.
        assert "printenv" in lowered, skill
        # Success evidence stays visible in the root.
        assert "receipt" in lowered or "partial success" in lowered, skill

    pr_verify = _root("jira-pr-verify").lower()
    assert "printenv" in pr_verify
    assert "comment url" in pr_verify or "ledger" in pr_verify

    cip = _root("code-improvement-proposal").lower()
    assert "dry_run" in cip
    assert "trusted jira tool surface" in cip

    assert "evidence-first" in _root("jira-verify").lower()

    to_jira = _root("github-issue-to-jira").lower()
    assert "exactly one terminal action" in to_jira
