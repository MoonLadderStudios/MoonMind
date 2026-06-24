from pathlib import Path

from moonmind.services.skill_resolution import (
    extract_required_capabilities_from_skill_markdown,
)

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"
_SKILL_PATH = _SKILLS_DIR / "code-improvement-proposal" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def test_code_improvement_proposal_skill_frontmatter_resolves() -> None:
    text = _skill_text()

    assert "name: code-improvement-proposal" in text
    # Frontmatter must parse through the real resolution helper and keep the
    # default dry-run/GitHub path schedulable without Jira access.
    capabilities = extract_required_capabilities_from_skill_markdown(
        text,
        skill_name="code-improvement-proposal",
    )
    assert capabilities == ("git", "gh")


def test_code_improvement_proposal_skill_is_proposal_only_not_auto_refactor() -> None:
    text = _skill_text()

    assert "## Non-goals" in text
    assert "does not modify code" in text
    assert "Do not modify repository source files" in text
    # An "implement issue" skill is the separate code-changing follow-up.
    assert '"implement issue"' in text


def test_code_improvement_proposal_skill_enforces_evidence_gate() -> None:
    text = _skill_text()

    assert "evidence gate" in text
    assert "## Review principles" in text
    assert "tied to exact files and line ranges" in text


def test_code_improvement_proposal_skill_defaults_to_dry_run_with_dedupe() -> None:
    text = _skill_text()

    assert "mode: dry_run" in text
    assert "publish_requires_explicit_request: true" in text
    assert "dedupe_existing_issues: true" in text
    assert "Prefer `dry_run` unless the user explicitly asked to publish." in text


def test_code_improvement_proposal_skill_documents_github_and_jira_publishing() -> None:
    text = _skill_text()

    # GitHub routes to a repository; Jira routes to a project/issue type/component.
    assert "POST /rest/api/3/issue" in text
    assert "Atlassian Document Format" in text
    assert (
        "GitHub routes to a repository; Jira routes to a project, issue type, "
        "component, and labels" in text
    )
    assert "gh issue create" in text
    assert "fetch create metadata" in text.lower()


def test_code_improvement_proposal_skill_keeps_security_guardrails() -> None:
    text = _skill_text()

    assert "Do not include secrets" in text
    assert "ghp_" in text
    assert "do not run `printenv`" in text
    assert "Do not use bare heredocs" in text
    assert "Do not paste large source files into issues" in text


def test_code_improvement_proposal_skill_defines_terminal_statuses() -> None:
    text = _skill_text()

    for status in (
        "`dry_run`",
        "`published`",
        "`duplicate`",
        "`needs_routing`",
        "`blocked`",
    ):
        assert status in text
