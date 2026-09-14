"""Regression coverage for epic #4264 (packages #4266 + #4275 + model-neutral,
plus bounded #4270/#4271/#4272/#4273 slices).

Covers the bounded slice implemented in this change:

- MoonSpec planning/implementation recovers from repairable checklist, fixture,
  and evidence gaps within existing authority instead of stopping for approval
  or halting on the first failed task (#4266).
- Technical API/protocol/migration acceptance survives specify/align unchanged
  in meaning without invented numbers (#4266).
- Verification resolves helpers through the immutable active bundle and allows
  a valid conflict-free alias instead of blanket-rejecting symlinks (#4275).
- PR repair recovers with bounded retry and blocked evidence instead of
  stopping to ask for a PR number/URL, never invents PR identity, and never
  continues from stale comments (#4270).
- Resulting skills/presets carry no named-model instructions; existing
  runtime/model/effort/billing selections stay opaque intent (epic constraint).

Legitimate service API identifiers (for example ``agents/openai.yaml``) are
not model-specific instructions and are explicitly not banned here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
PRESETS_ROOT = REPO_ROOT / "api_service" / "data" / "presets"

# Named-model version tokens only. Provider names alone (openai, anthropic,
# gemini without a version, bedrock, vertex) are legitimate service/API
# identifiers and must not fail this test.
BANNED_MODEL_PATTERNS = [
    re.compile(r"\bgpt-[2345]\b", re.IGNORECASE),
    re.compile(r"\bclaude-[234]\b", re.IGNORECASE),
    re.compile(r"\bgemini-(?:1\.5|1\.0|2\.0|2\.5)\b", re.IGNORECASE),
    re.compile(r"\bllama-[234]\b", re.IGNORECASE),
    re.compile(r"\bmixtral\b", re.IGNORECASE),
    re.compile(r"\bmistral-large\b", re.IGNORECASE),
    re.compile(r"\bper-model\b", re.IGNORECASE),
    re.compile(r"\bmodel-family\b", re.IGNORECASE),
    re.compile(r"\bmodel migration ruleset\b", re.IGNORECASE),
]

# Companion references, templates, scripts, and examples ship inside the
# portable bundle and carry the same constraint as the entrypoints.
BUNDLE_TEXT_EXTENSIONS = {".md", ".yaml", ".yml", ".py", ".sh", ".json"}


def _catalog_files() -> list[Path]:
    files = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    assert len(files) == 35, f"expected 35 skill entrypoints, found {len(files)}"
    presets = sorted(PRESETS_ROOT.glob("*.yaml"))
    assert len(presets) == 19, f"expected 19 preset definitions, found {len(presets)}"
    files.extend(presets)
    return files


def _bundle_files() -> list[Path]:
    out: list[Path] = []
    for skill_dir in sorted(p for p in SKILLS_ROOT.iterdir() if p.is_dir()):
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in BUNDLE_TEXT_EXTENSIONS:
                out.append(path)
    return out


def test_catalog_entrypoints_and_presets_have_no_named_model_instructions():
    offenders: list[str] = []
    for path in _catalog_files():
        text = path.read_text(encoding="utf-8")
        for pattern in BANNED_MODEL_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{pattern.pattern}")
    assert not offenders, "named-model instructions found:\n" + "\n".join(offenders)


def test_portable_bundle_references_have_no_named_model_instructions():
    offenders: list[str] = []
    for path in _bundle_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in BANNED_MODEL_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{pattern.pattern}")
    assert not offenders, "named-model instructions found:\n" + "\n".join(offenders)


def test_legitimate_service_identifiers_are_not_banned():
    # Guard the guard: the openai host-metadata surface is an existing
    # integration surface, not a model-specific instruction.
    assert (SKILLS_ROOT / "moonspec-assess" / "agents" / "openai.yaml").exists()


def _read_skill(name: str) -> str:
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def test_implement_checklist_gap_recovers_without_fabricated_approval():
    text = _read_skill("moonspec-implement")
    assert "stop and ask whether to proceed" not in text.lower()
    assert "continue only when the user explicitly says yes" not in text.lower()
    assert "repairable gap" in text
    assert "existing authority" in text
    assert "Never fabricate" in text
    assert "without its required evidence" in text


def test_implement_setup_stays_bounded_to_selected_story():
    text = _read_skill("moonspec-implement")
    assert "Limit setup changes to prerequisites required by the selected story" in text
    assert "Preserve unrelated worktree changes" in text


def test_implement_failure_uses_bounded_retry_not_unconditional_halt():
    text = _read_skill("moonspec-implement")
    assert "Halt on failed non-parallel tasks" not in text
    assert "diagnose within existing authority" in text
    assert "unsupported substrate" in text
    assert "budget exhaustion" in text


def test_specify_preserves_technical_acceptance_criteria():
    text = _read_skill("moonspec-specify")
    norm = re.sub(r"\s+", " ", text)
    assert "Define measurable, technology-agnostic success criteria." not in text
    assert "keeps its explicit interface names" in text
    assert "Do not rewrite a technical requirement as a business metric" in norm
    assert "do not invent performance, retention, or concurrency numbers" in norm.lower()


def test_align_preserves_genuine_authority_boundaries():
    text = _read_skill("moonspec-align")
    assert "genuine product decision" in text
    assert "information or" in text and "cannot be obtained safely" in text
    assert "instead of guessing" in text


def test_verify_preflight_allows_valid_alias_and_forbids_destructive_repair():
    text = _read_skill("moonspec-verify")
    assert "MOONMIND_ACTIVE_SKILLS_DIR" in text
    assert "conflict-free alias" in text
    assert "masks no repository-owned source" in text
    assert "Never delete, move," in text
    assert "never create a clean reclone" in text.lower()
    assert "Prefer restoring the tracked repository files or using a clean reclone" not in text
    assert "test ! -L .agents/skills" not in text


def test_fix_comments_pr_failure_is_portable_and_preserves_branch_authority():
    text = _read_skill("fix-comments")
    assert "stop and ask the user for a PR number/URL" not in text.lower()
    assert "pr_resolution_unavailable" in text
    assert "never invent" in text.lower()
    assert "only when it was supplied as a scope constraint" in text
    assert "successfully refreshed" in text
    assert "Do not continue from pre-fetched or stale comments" in text


def test_fix_merge_conflicts_preserves_exact_base_and_task_only_changes():
    text = _read_skill("fix-merge-conflicts")
    assert "or `git add -A`" not in text
    assert "Never use `git add -A`" in text
    assert "Commit any other local changes" not in text
    assert "Commit only intentional task changes" in text
    assert "preserve pre-existing unrelated" in text.lower()
    assert "base_unavailable" in text
    assert "Never silently substitute a default branch" in text
    assert "git_identity_unavailable" in text
    assert "do not invent an author/email" in text.lower()


def test_jira_verify_posts_comment_before_status_transition():
    text = _read_skill("jira-verify")
    assert "evidence-first" in text.lower()
    assert "before any status transition" in text.lower() or "before any completion transition" in text.lower()
    assert "If posting fails" in text
    assert "do not attempt a completion transition" in text.lower() or "do not claim Jira was updated" in text


def test_jira_issue_creator_distinguishes_draft_from_write_intent():
    text = _read_skill("jira-issue-creator")
    assert "strictly non-mutating" in text.lower()
    assert "explicit write intent" in text.lower()
    assert "raw credentials" in text.lower() or "less-constrained" in text.lower()
    assert "POST /rest/api/3/issue" not in text


def test_document_health_update_selects_dedicated_skills():
    preset = (PRESETS_ROOT / "document-health-update.yaml").read_text(encoding="utf-8")
    assert "id: auto" not in preset
    assert "id: document-health-review" in preset
    assert "id: document-health-remediate" in preset


def test_code_improvement_proposal_has_no_mandatory_manual_smoke_test():
    text = _read_skill("code-improvement-proposal")
    assert "Manual smoke test" not in text
    assert "not a mandatory human smoke test" in text.lower() or "executable acceptance" in text.lower()
    assert "no_findings" in text.lower()


def test_jira_breakdown_presets_use_provider_neutral_issue_creation():
    for slug in ("jira-breakdown-implement.yaml", "jira-breakdown-orchestrate.yaml"):
        preset = (PRESETS_ROOT / slug).read_text(encoding="utf-8")
        assert "issueCreation.action" in preset
        assert "jiraCreation.action" not in preset
