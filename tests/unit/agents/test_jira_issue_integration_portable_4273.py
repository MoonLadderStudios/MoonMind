"""Portable access and evidence-first mutations for #4273.

Executable-behavior coverage for the four concrete defects named in
MoonLadderStudios/MoonMind#4273:

- R1/R4: jira-issue-creator draft mode + trusted-path-only access.
- R2: jira-implement without native-RAG commands or hardcoded repo recipes.
- R6: jira-verify evidence-first ordering (comment before transition).
- R9: code-improvement-proposal without an obligatory manual smoke test.
"""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"


def _read(skill: str) -> str:
    return (_SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


# R2: jira-implement de-RAG + portable verification discovery.


def test_jira_implement_has_no_native_rag_instructions() -> None:
    text = _read("jira-implement")
    assert "moonmind rag search" not in text
    assert "moonmind rag" not in text.lower()
    assert "embedding_provider_not_configured" not in text


def test_jira_implement_has_no_hardcoded_repo_test_recipes() -> None:
    text = _read("jira-implement")
    assert "./tools/test_unit.sh" not in text
    assert "./tools/test_integration.sh" not in text


def test_jira_implement_discovers_verification_through_repo_conventions() -> None:
    text = _read("jira-implement").lower()
    assert "verification" in text
    assert "repo" in text
    # Optional enrichment failure stays a disclosed limitation, never a blocker.
    assert "optional" in text


# R1/R4: jira-issue-creator portable access model + draft/write intent.


def test_jira_issue_creator_defines_non_mutating_draft_mode() -> None:
    text = _read("jira-issue-creator")
    lowered = text.lower()
    assert "draft" in lowered
    assert "dry_run" in lowered or "dry-run" in lowered or "dry run" in lowered
    # Draft/proposal requests must perform zero writes.
    assert "zero" in lowered or "non-mutating" in lowered or "no writes" in lowered


def test_jira_issue_creator_has_no_direct_credential_fallback() -> None:
    text = _read("jira-issue-creator")
    assert "POST /rest/api/3/issue" not in text
    assert "ATLASSIAN_API_KEY" not in text
    assert "API token/OAuth/local secret" not in text
    lowered = text.lower()
    assert "raw-secret" in lowered or "raw secret" in lowered or "raw credentials" in lowered


def test_jira_issue_creator_requires_explicit_write_intent() -> None:
    text = _read("jira-issue-creator").lower()
    assert "explicit" in text
    assert "write intent" in text or "write-intent" in text


# R6: jira-verify evidence-first mutation ordering.


def test_jira_verify_posts_evidence_before_transition() -> None:
    text = _read("jira-verify")
    lowered = text.lower()
    assert "before" in lowered
    # The completion transition must not run before the evidence comment posts.
    assert "do not transition before" in lowered or "evidence-first" in lowered
    # Structural ordering: comment drafting/posting precedes the transition call.
    draft_pos = lowered.find("draft the jira comment")
    post_pos = lowered.find("post to jira")
    transition_exec = lowered.find("execute the selected transition")
    if transition_exec == -1:
        transition_exec = lowered.find("transition_issue`, then record")
    assert draft_pos != -1 and post_pos != -1 and transition_exec != -1
    assert draft_pos < post_pos < transition_exec


def test_jira_verify_reconciles_unknown_outcomes_before_retry() -> None:
    text = _read("jira-verify").lower()
    assert "reconcile" in text
    assert "unknown" in text


# R9: code-improvement-proposal hygiene.


def test_code_improvement_proposal_has_no_obligatory_manual_smoke_test() -> None:
    text = _read("code-improvement-proposal")
    assert "Manual smoke test for" not in text


def test_code_improvement_proposal_validation_is_executable() -> None:
    text = _read("code-improvement-proposal")
    assert "## Suggested validation" in text
    assert "Unit tests for" in text
