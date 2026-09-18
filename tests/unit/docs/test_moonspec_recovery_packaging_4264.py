"""Regression coverage for epic #4264 (packages #4266 + #4275 + model-neutral,
plus bounded #4270/#4271/#4272/#4273 slices).

Covers the bounded slice implemented in this change (repo-native skills and
presets only):

- MoonSpec planning/implementation recovers from repairable checklist, fixture,
  and evidence gaps within existing authority instead of stopping for approval
  or halting on the first failed task (#4266). [Moved upstream: moonspec-*
  entrypoint wording is owned by the pinned MoonSpec bundle projection and
  covered by tools/sync_moonspec.py --check, not asserted here.]
- Technical API/protocol/migration acceptance survives specify/align unchanged
  in meaning without invented numbers (#4266). [Same: moonspec-specify/align
  wording lives in the bundle; repo-native preset/skill coverage below.]
- Verification resolves helpers through the immutable active bundle and allows
  a valid conflict-free alias instead of blanket-rejecting symlinks (#4275).
  [Same: moonspec-verify wording lives in the bundle.]
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
# identifiers and must not fail this test. Versioned naming grammars
# (gpt-4o, claude-sonnet-4-6, gemini-3.1-pro) match as well as the bare
# legacy prefixes, so versioned forms cannot be added as instructions.
BANNED_MODEL_PATTERNS = [
    re.compile(r"\bgpt-[2345](?:[.-]?[a-z0-9]+)*", re.IGNORECASE),
    re.compile(r"\bclaude(?:-[a-z]+)*-[0-9](?:[.-]?[a-z0-9]+)*", re.IGNORECASE),
    re.compile(r"\bgemini-[0-9](?:\.[0-9]+)?(?:[.-]?[a-z0-9]+)*", re.IGNORECASE),
    re.compile(r"\bllama-[234](?:[.-]?[a-z0-9]+)*", re.IGNORECASE),
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


# NOTE: moonspec-* entrypoint wording is owned by the pinned MoonSpec bundle
# projection (see tools/sync_moonspec.py). It must not be hand-edited or
# asserted here; the drift gate covers it. The wording slice belongs upstream
# in the MoonSpec repository.


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
    assert "Commit and push only intentional task changes" in text
    assert "leave pre-existing staged, unstaged, and untracked" in text.lower()
    assert "base_unavailable" in text
    assert "Never silently substitute a default branch" in text
    assert "git config --get user.name" in text
    assert "Do not invent, default, or export a fallback author/email" in text
    assert "git merge --autostash" in text
    assert "never stage or commit unrelated" in text.lower()


def test_jira_verify_posts_comment_before_status_transition():
    text = _read_skill("jira-verify")
    assert "evidence-first" in text.lower()
    assert "before any status transition" in text.lower() or "before any completion transition" in text.lower()
    assert "If posting fails" in text
    assert "do not attempt a completion transition" in text.lower() or "do not claim jira was updated" in text.lower()
    assert "planned/pending execution" in text.lower()
    assert "follow-up jira comment" in text.lower()
    assert "Do not claim `transitioned` or `failed` here" in text


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
    assert "`no_findings`" in text
    assert "instead of inventing a ticket to fill a quota" in text.lower()


def test_jira_breakdown_presets_use_provider_neutral_issue_creation():
    for slug in ("jira-breakdown-implement.yaml", "jira-breakdown-orchestrate.yaml"):
        preset = (PRESETS_ROOT / slug).read_text(encoding="utf-8")
        assert "issueCreation.action" in preset
        assert "jiraCreation.action" not in preset


def test_tactics_test_defines_phase_scoped_terminal_outcomes():
    text = _read_skill("tactics-test")
    assert "requested phase" in text
    assert "stale evidence" in text.lower()
    assert "do not run tests against a build that did not succeed" in text.lower()
    assert "--dry-run" in text and "strictly non-mutating" in text.lower()
    assert "never deletes or overwrites prior timestamped" in text.lower()


def test_update_moonmind_defines_receipt_readiness_and_dry_run_outcomes():
    text = _read_skill("update-moonmind")
    assert "terminal release receipt" in text.lower()
    assert "pinned repository digest" in text.lower()
    assert "verified installed" in text.lower()
    assert "--dry-run" in text and "strictly non-mutating" in text.lower()
    assert "preserve" in text.lower() and "deployment-owned" in text.lower()
    assert "sourceRevision" in text
    assert "releaseReadinessArtifactRef" in text


def test_versioned_model_name_grammars_are_banned():
    cases = {
        0: ["gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-5"],
        1: ["claude-3", "claude-3-5-sonnet", "claude-sonnet-4-6", "claude-opus-4-1"],
        2: ["gemini-1.5", "gemini-2.0", "gemini-2.5-flash", "gemini-3.1-pro"],
        3: ["llama-2", "llama-3.1", "llama-4-scout"],
    }
    for index, names in cases.items():
        for name in names:
            assert BANNED_MODEL_PATTERNS[index].search(name), name


def test_legitimate_identifiers_are_not_versioned_models():
    allowed = [
        "openai",
        "anthropic",
        "gemini",
        "bedrock",
        "vertex",
        "agents/openai.yaml",
        "gpt",
        "claude",
        "llama",
    ]
    for name in allowed:
        assert not any(pattern.search(name) for pattern in BANNED_MODEL_PATTERNS), name
