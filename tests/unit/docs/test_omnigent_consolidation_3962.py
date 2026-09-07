"""Consolidation guardrails for Omnigent documentation.

Source: MoonLadderStudios/MoonMind#3962 (consolidate Omnigent docs while
preserving exact-support and credential contracts). These assertions pin the
durable outcomes: one short module entrypoint, one surviving-owner map that
covers every ``docs/Omnigent/`` file, preserved credential-ownership and
exact-support contracts, duplicate sections replaced by owner pointers, and
valid internal links/anchors.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
OMNIGENT = REPO_ROOT / "docs" / "Omnigent"
ENTRYPOINT = OMNIGENT / "README.md"
OWNERSHIP = OMNIGENT / "ContractOwnership.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    for match in re.finditer(r"^(#{1,6})\s+(.*)", text, re.M):
        slug = match.group(2).strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slugs.add(re.sub(r"\s+", "-", slug))
    return slugs


def test_entrypoint_exists_with_startup_path_and_limitations() -> None:
    text = _read(ENTRYPOINT)
    assert "MoonLadderStudios/MoonMind#3962" in text
    assert "Supported startup path" in text
    assert "Exact limitations" in text
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
    assert "never" in cutover and "supported" in cutover
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
    checked = 0
    for path in sorted(OMNIGENT.glob("*.md")):
        text = _read(path)
        anchors = _slugs(text)
        for match in re.finditer(r"\]\(([^)]+)\)", text):
            link = match.group(1)
            if link.startswith(("http", "mailto:")) or link.startswith("#"):
                continue
            if "#" in link:
                rel, anchor = link.split("#", 1)
                target = path.parent / rel if rel else path
                assert target.exists(), f"{path.name}: {link}"
                if target.suffix == ".md":
                    assert anchor in _slugs(_read(target)), (
                        f"{path.name}: {link}"
                    )
                    checked += 1
            elif link.endswith(".md"):
                assert (path.parent / link).exists(), f"{path.name}: {link}"
                checked += 1
    assert checked > 0
