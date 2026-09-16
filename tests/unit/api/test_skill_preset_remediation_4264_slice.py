"""Bounded remediation slice for MoonLadderStudios/MoonMind#4264.

Covers two safe, immediately-testable sub-slices without claiming the full
epic or any single child issue:

- #4265 (candidate vs landed): locks the existing sequencing rule that an
  unmerged candidate / absent PR / open issue are expected verification
  inputs, not implementation gaps, and that candidate-only success never
  proves landing. This is a regression lock on wording already present in
  moonspec-verify / moonspec-assess, not a full acceptance-rules rewrite.
- #4277 (trustworthy terminal outcomes): requires tactics-test and
  update-moonmind entrypoints to state terminal evidence explicitly in
  task-neutral wording: dry-run output never establishes completion,
  gating consumes only the current run's gate artifact, and stale artifacts
  must not be reused.

All assertions are model-neutral: they describe task intent, authorized
capabilities, constraints, and completion evidence without naming models,
model families, or per-model procedures. Opaque runtime/account/model
selections and legitimate service identifiers elsewhere are out of scope.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"


def _skill_text(skill: str) -> str:
    return (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


def test_candidate_completion_is_distinguished_from_landed_work():
    verify = _skill_text("moonspec-verify")
    assert "unmerged candidate" in verify
    assert "Do not require downstream commit" in verify or (
        "downstream commit" in verify and "not" in verify
    )
    # Candidate success alone cannot close/transition: assessment wording lock.
    assess = _skill_text("moonspec-assess")
    assert "cannot replace objective verification or prove landing" in assess


def test_tactics_test_defines_trustworthy_terminal_outcome():
    text = _skill_text("tactics-test")
    # Dry-run must never satisfy publish gating.
    assert "SKIPPED" in text, "tactics-test must name the dry-run SKIPPED outcome"
    assert "never satisfies" in text or "never establishes" in text or (
        "dry-run" in text.lower() and "must not" in text.lower()
    )
    # Gating consumes the current run's gate artifact, not a stale file.
    assert "resultsDir" in text or "results_dir" in text or "timestamped" in text
    assert "stale" in text.lower()


def test_update_moonmind_defines_trustworthy_terminal_outcome():
    text = _skill_text("update-moonmind")
    assert "terminal release receipt" in text
    assert "verified installed readiness" in text
    # Dry-run explicitly proves nothing and process/container start alone
    # does not establish completion.
    assert "dry-run" in text.lower()
    assert "alone does not" in text or "never" in text.lower()
