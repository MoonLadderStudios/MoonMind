"""MoonLadderStudios/MoonMind#4273: portable Jira/issue-proposal skill contracts.

Covers the bounded implementation backlog from the PARTIALLY_IMPLEMENTED
assessment: capability-scoped access without credential fallback, removed
native-RAG/repo recipes, draft/write intent, metadata-driven fields,
evidence-first mutations, receipt-bound retries with unknown-outcome
reconciliation, shared acceptance semantics, read-only proposals, and the
portable provider-commands bundle layout.
"""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"


def _read(skill: str) -> str:
    return (_SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


def _ref(skill: str) -> str:
    ref = _SKILLS_DIR / skill / "references" / "provider-commands.md"
    assert ref.is_file(), f"{skill} must ship references/provider-commands.md"
    return ref.read_text(encoding="utf-8")


def test_jira_implement_has_no_native_rag_or_repo_recipes() -> None:
    text = _read("jira-implement")

    assert "moonmind rag search" not in text
    assert "./tools/test_unit.sh" not in text
    assert "./tools/test_integration.sh" not in text
    assert "references/provider-commands.md" in text
    assert "$MOONMIND_ACTIVE_SKILLS_DIR" in text
    # Stale canonical-active-path wording must be gone.
    assert "preserve `.agents/skills` as the canonical active path" not in text
    # Optional enrichment remains a disclosed limitation.
    assert "optional enrichment" in text.lower()


def test_jira_issue_creator_draft_mode_is_non_mutating() -> None:
    text = _read("jira-issue-creator")

    assert "`draft` (default, strictly non-mutating" in text
    assert "zero issue/comment/status writes" in text.lower()
    assert "existing explicit write intent" in text
    assert "no second routine confirmation" in text.lower()
    assert "`draft`" in text and "`created`" in text
    # Direct-credential REST fallback must be gone from the root.
    assert "POST /rest/api/3/issue" not in text
    assert "API token" not in text or "raw" in text.lower() or "Never" in text
    assert "raw credentials" in text.lower() or "raw `ATLASSIAN" in text
    _ref("jira-issue-creator")


def test_jira_issue_creator_reconciles_unknown_outcomes() -> None:
    text = _read("jira-issue-creator")

    assert "accepted but its outcome is unknown" in text.lower() or (
        "accepted-but-unknown" in text.lower()
    )
    assert "An incomplete search is not proof" in text


def test_jira_verify_posts_evidence_before_transition() -> None:
    text = _read("jira-verify")

    draft_pos = text.find("Draft the Jira comment")
    post_pos = text.find("Scan and post to Jira (evidence first")
    transition_pos = text.find("decide and execute the Jira status update only after")
    assert draft_pos != -1 and post_pos != -1 and transition_pos != -1
    assert draft_pos < post_pos < transition_pos
    assert "comment failure prevents the associated completion transition" in text.lower()
    assert "resume/retry only the unfinished transition" in text.lower()
    # Old transition-before-comment ordering must be gone.
    assert "transition only through `jira.transition_issue` with JSON" not in text
    _ref("jira-verify")


def test_jira_verify_and_creator_bind_retries_to_receipts() -> None:
    for skill in ("jira-verify", "jira-issue-updater", "github-issue-verify"):
        text = _read(skill)
        assert "operation identity" in text.lower(), skill
        assert "An incomplete search is not proof" in text, skill


def test_provider_commands_live_in_portable_bundle() -> None:
    # Roots keep intent/hygiene/authority/evidence; provider commands move out.
    expectations = {
        "jira-implement": "curl -fsS",
        "jira-verify": "jira.get_issue",
        "jira-issue-updater": "jira.get_transitions",
        "github-issue-verify": "gh issue comment",
        "github-issue-to-jira": 'gh issue view',
        "jira-pr-verify": "gh pr view",
        "code-improvement-proposal": "gh issue create",
    }
    for skill, marker in expectations.items():
        ref_text = _ref(skill)
        assert marker in ref_text, f"{skill} reference must contain {marker!r}"

    # No inline provider command blocks remain in the slimmed roots.
    assert "gh auth status" not in _read("github-issue-verify")
    assert "gh auth status" not in _read("github-issue-to-jira")
    assert "github_pr_preflight.py --repo" not in _read("jira-pr-verify")
    assert "curl -sS -X POST" not in _read("jira-issue-updater")


def test_github_issue_to_jira_single_terminal_path_and_partial_success() -> None:
    text = _read("github-issue-to-jira")

    assert "exactly one authorized terminal path" in text.lower()
    assert "partial success" in text.lower()
    assert "Candidate-only evidence never counts as landed" in text


def test_verify_skills_share_acceptance_policy_without_forks() -> None:
    for skill in ("jira-verify", "jira-pr-verify"):
        text = _read(skill)
        assert "shared policy" in text.lower()
        assert "per-skill acceptance forks are not maintained" in text.lower()
