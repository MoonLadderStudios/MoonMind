"""Contract tests for the normal Codex-through-Omnigent product-path reconciliation.

Source: MoonLadderStudios/MoonMind#3565 (Omnigent Milestone 1, item 1.0 —
declarative reconciliation). These assertions pin the durable invariants the
reconciliation owns: one canonical product identity named in every consuming
document, no caller-authored host/daemon authority, no silent fallback,
release-last ordering, evidence-qualified support language, and fail-closed
cutover behavior. Do not delete an assertion to make a doc edit pass — update the
docs so the invariant still holds, or change the invariant deliberately with the
owner's sign-off.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

RECONCILIATION = REPO_ROOT / "docs" / "Omnigent" / "NormalCodexProductPathReconciliation.md"
CUTOVER = REPO_ROOT / "docs" / "Omnigent" / "CodexSupportAndCutover.md"

# Every canonical document that participates in the normal product path must name
# the same canonical identity so support/authority state stays consistent.
IDENTITY_DOCS = (
    REPO_ROOT / "docs" / "Omnigent" / "NormalCodexProductPathReconciliation.md",
    REPO_ROOT / "docs" / "Omnigent" / "CodexCreateToHostContract.md",
    REPO_ROOT / "docs" / "Workflows" / "WorkspaceLocators.md",
    REPO_ROOT / "docs" / "Security" / "ProviderProfiles.md",
    REPO_ROOT / "docs" / "Security" / "SettingsSystem.md",
    REPO_ROOT / "docs" / "UI" / "WorkflowDetailsPage.md",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_reconciliation_doc_exists_and_is_traceable() -> None:
    text = _read(RECONCILIATION)
    assert "MoonLadderStudios/MoonMind#3565" in text
    assert "**Document Class:** Canonical declarative" in text
    assert "**Status:** Accepted" in text


def test_universal_canonical_identity_named_in_every_consuming_doc() -> None:
    for path in IDENTITY_DOCS:
        text = _read(path)
        assert "external" in text and "omnigent" in text, path
        assert "codex-native" in text, path


def test_no_caller_authored_host_or_daemon_authority() -> None:
    text = _read(RECONCILIATION)
    assert "caller-authored `session.hostId`" in text
    assert "No caller authors host, daemon, session, path, or credential authority." in text


def test_no_silent_fallback_is_pinned() -> None:
    text = _read(RECONCILIATION)
    invariant = (
        "An explicit Omnigent selection never silently runs through direct Codex, "
        "another Provider Profile, another host mode, an arbitrary static host, or "
        "a broader network/mount policy."
    )
    assert invariant in text
    assert "fail-closed with no silent fallback" in text


def test_release_last_ordering_is_pinned() -> None:
    text = _read(RECONCILIATION)
    assert "Provider Profile capacity is released last" in text
    assert "no release before cleanup" in text.lower() or "release before cleanup" in text
    assert "auxiliary cleanup/publication failure never overwrites `primaryStatus`" in text


def test_evidence_qualified_support_vocabulary_is_present() -> None:
    text = _read(RECONCILIATION)
    for term in (
        "designed",
        "implemented foundation",
        "repository-verifiable or hermetically verified",
        "protected-live verified",
        "supported",
        "default",
        "deprecated-disabled",
        "retired",
    ):
        assert f"**{term}**" in text, term
    # Unproven rows are not promoted to supported by this reconciliation.
    assert "must not imply completion of checkpoint resume/branching" in text


def test_fail_closed_cutover_is_pinned() -> None:
    text = _read(RECONCILIATION)
    assert "OMNIGENT_CUTOVER_PROMOTION_BLOCKED" in text
    assert "one-step, fresh, version-matched, and fail-closed" in text
    # The reconciliation defers row-by-row support state to the cutover owner.
    assert "CodexSupportAndCutover.md" in text
    cutover = _read(CUTOVER)
    assert "default-selection phase is **1 — explicit selection**" in cutover
    assert "1. `opt_in`: Omnigent is normally available for explicit selection" in cutover


def test_all_twelve_failure_stages_carry_stable_codes() -> None:
    text = _read(RECONCILIATION)
    for code in (
        "OMNIGENT_WORKSPACE_RESOLUTION_FAILED",
        "OMNIGENT_PROFILE_UNAVAILABLE",
        "OMNIGENT_LAUNCH_POLICY_INVALID",
        "OMNIGENT_HOST_LAUNCH_FAILED",
        "OMNIGENT_HOST_REGISTRATION_TIMEOUT",
        "OMNIGENT_BRIDGE_AUTHORIZATION_FAILED",
        "OMNIGENT_FIRST_MESSAGE_AMBIGUOUS",
        "OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
        "OMNIGENT_EVIDENCE_PUBLICATION_FAILED",
        "OMNIGENT_CLEANUP_FAILED",
        "OMNIGENT_SUPPORT_EVIDENCE_INVALID",
        "OMNIGENT_CUTOVER_PROMOTION_BLOCKED",
    ):
        assert code in text, code


def test_reconciled_wire_examples_are_valid_json() -> None:
    text = _read(RECONCILIATION)
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)
    assert len(blocks) >= 4
    for block in blocks:
        json.loads(block)
