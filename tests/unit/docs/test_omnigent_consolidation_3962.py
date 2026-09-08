"""Consolidation guardrails for Omnigent documentation.

Source: MoonLadderStudios/MoonMind#3962 (consolidate Omnigent docs while
preserving exact-support and credential contracts). These assertions pin the
durable outcomes: one short module entrypoint, one surviving-owner map that
covers every ``docs/Omnigent/`` file, preserved credential-ownership and
exact-support contracts, duplicate sections replaced by owner pointers, and
valid internal links/anchors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_documentation_links import DocFile, run_checks

REPO_ROOT = Path(__file__).resolve().parents[3]
OMNIGENT = REPO_ROOT / "docs" / "Omnigent"
ENTRYPOINT = OMNIGENT / "README.md"
OWNERSHIP = OMNIGENT / "ContractOwnership.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_entrypoint_exists_with_startup_path_and_limitations() -> None:
    text = _read(ENTRYPOINT)
    assert "MoonLadderStudios/MoonMind#3962" in text
    # Status vocabulary: registered through selected-by-default.
    for term in (
        "registered",
        "discovered",
        "installed",
        "admitted",
        "qualified",
        "selected-by-default",
    ):
        assert term in text, term
    # A shared image never authorizes every installed harness.
    assert "never authorizes every installed runtime" in text
    # Credential ownership distinction is summarized, not merged.
    assert "detached but preserved" in text
    # ManagedAgents boundary is cross-linked, not copied.
    assert "ManagedAgents/DockerBackendService.md" in text


def test_ownership_map_covers_every_omnigent_file() -> None:
    text = _read(OWNERSHIP)
    assert "MoonLadderStudios/MoonMind#3962" in text
    for path in sorted(OMNIGENT.glob("*.md")):
        assert path.name in text, path.name


def test_credential_ownership_contract_survives() -> None:
    shared = _read(OMNIGENT / "SharedHostImage.md")
    assert "destroyed on cleanup" in shared
    assert "detached but preserved on cleanup" in shared
    assert "generation-marker fenced" in shared or "generation" in shared
    oauth = _read(OMNIGENT / "OmnigentHostOAuth.md")
    assert "no Claude/OpenCode" in oauth or "credential attachments" in oauth


def test_exact_support_contract_survives() -> None:
    strategy = _read(OMNIGENT / "PrimaryRuntimeProviderStrategy.md")
    assert "evidence-gated" in strategy
    assert "never silently" in strategy or "No silent fallback" in strategy
    cutover = _read(OMNIGENT / "CodexSupportAndCutover.md")
    assert "## Support and conformance matrix v1" in cutover
    assert "REQUIRED_ROW_CATALOG" in cutover
    assert "REQUIRED_MATRIX_ROWS" in cutover
    assert "codex-omnigent-support-matrix/v1" in cutover
    assert "## Operator remediation support matrix v1" in cutover
    assert "## Release thresholds and telemetry" in cutover
    assert "RuntimeProviderRollout.md" in cutover


def test_duplicates_are_owner_pointers() -> None:
    strategy = _read(OMNIGENT / "PrimaryRuntimeProviderStrategy.md")
    assert "The shared image must:" not in strategy
    assert "SharedHostImage.md" in strategy
    oauth = _read(OMNIGENT / "OmnigentHostOAuth.md")
    assert "A Codex host must prove:" not in oauth
    assert "SharedHostImage.md" in oauth
    opencode = _read(OMNIGENT / "OpenCodeHost.md")
    assert "The default image mapping is:" not in opencode
    assert "The shared image must:" not in opencode
    assert "SharedHostImage.md" in opencode


def test_internal_links_and_anchors_resolve() -> None:
    docs = [
        DocFile(path.relative_to(REPO_ROOT).as_posix(), _read(path))
        for path in sorted(OMNIGENT.glob("*.md"))
    ]
    assert docs
    findings, _ = run_checks(docs, root=REPO_ROOT)
    assert findings == []


@pytest.mark.parametrize(
    ("target", "rule"),
    [
        ("./Missing3964.md", "broken-local-link"),
        ("./README.md#missing-3964-anchor", "broken-local-anchor"),
        ("#missing-3964-anchor", "broken-local-anchor"),
    ],
)
def test_shipped_link_guard_detects_mutated_target(monkeypatch, target, rule) -> None:
    original_read = _read

    def mutated_read(path: Path) -> str:
        text = original_read(path)
        if path == ENTRYPOINT:
            text += f"\n[Regression link]({target})\n"
        return text

    monkeypatch.setattr(f"{__name__}._read", mutated_read)
    with pytest.raises(AssertionError, match=rule):
        test_internal_links_and_anchors_resolve()
