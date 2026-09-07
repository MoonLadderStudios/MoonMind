"""Consolidation guardrails for Temporal documentation.

Source: MoonLadderStudios/MoonMind#3961 (consolidate Temporal docs by
contract ownership, not a fixed file quota). These assertions pin the
durable outcomes: one short module entrypoint, one surviving-owner map that
covers every ``docs/Temporal/`` file, preserved recovery/pause/projection/
publication/cleanup guarantees, duplicate catalog sections replaced by owner
pointers, and valid internal links/anchors.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPORAL = REPO_ROOT / "docs" / "Temporal"
ENTRYPOINT = TEMPORAL / "TemporalModuleArchitecture.md"
OWNERSHIP = TEMPORAL / "ContractOwnership.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    for match in re.finditer(r"^(#{1,6})\s+(.*)", text, re.M):
        slug = match.group(2).strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slugs.add(re.sub(r"\s+", "-", slug))
    return slugs


def test_entrypoint_exists_with_runtime_model_and_owners() -> None:
    text = _read(ENTRYPOINT)
    assert "MoonLadderStudios/MoonMind#3961" in text
    # Runtime model stated once, concisely.
    assert "Workflows orchestrate" in text or "Workflows orchestrate" in text
    assert "workflowId" in text
    assert "MoonMind.AgentRun" in text
    # Entry point routes to owners; it is short, not a second hub.
    assert len(text.split()) < 1200, len(text.split())
    for owner in (
        "TemporalArchitecture.md",
        "WorkflowTypeCatalogAndLifecycle.md",
        "ActivityCatalogAndWorkerTopology.md",
        "ManagedAndExternalAgentExecutionModel.md",
        "TemporalSignalsSystem.md",
        "TemporalScheduling.md",
        "WorkerPauseSystem.md",
        "SourceOfTruthAndProjectionModel.md",
        "WorkflowArtifactSystemDesign.md",
        "WorkflowRunHistoryAndNewRunSemantics.md",
        "ContractOwnership.md",
    ):
        assert owner in text, owner


def test_ownership_map_covers_every_temporal_file() -> None:
    text = _read(OWNERSHIP)
    assert "MoonLadderStudios/MoonMind#3961" in text
    for path in sorted(TEMPORAL.glob("*.md")):
        assert path.name in text, path.name


def test_recovery_pause_projection_guarantees_preserved() -> None:
    truth = _read(TEMPORAL / "SourceOfTruthAndProjectionModel.md")
    assert "Continue-As-New" in truth
    assert "Normative steady-state contract" in truth
    pause = _read(TEMPORAL / "WorkerPauseSystem.md")
    assert "Normative contract" in pause
    history = _read(TEMPORAL / "WorkflowRunHistoryAndNewRunSemantics.md")
    assert "RequestRerun" in history
    catalog = _read(TEMPORAL / "WorkflowTypeCatalogAndLifecycle.md")
    assert "Continue-As-New must preserve" in catalog
    assert "Workflow ID" in catalog


def test_duplicates_are_owner_pointers() -> None:
    agent = _read(TEMPORAL / "TemporalAgentExecution.md")
    assert "ActivityCatalogAndWorkerTopology.md" in agent
    catalog = _read(TEMPORAL / "WorkflowTypeCatalogAndLifecycle.md")
    assert "TemporalSignalsSystem.md" in catalog
    arch = _read(TEMPORAL / "TemporalArchitecture.md")
    assert "WorkflowTypeCatalogAndLifecycle.md" in arch
    assert "TemporalModuleArchitecture.md" in arch
    foundation = _read(TEMPORAL / "TemporalPlatformFoundation.md")
    assert "WorkflowTypeCatalogAndLifecycle.md" in foundation
    guide = _read(TEMPORAL / "WorkflowSchedulingGuide.md")
    assert "TemporalScheduling.md" in guide
    research = _read(TEMPORAL / "TemporalSignalsResearch.md")
    assert "TemporalSignalsSystem.md" in research


def test_internal_links_and_anchors_resolve() -> None:
    checked = 0
    for path in sorted(TEMPORAL.glob("*.md")):
        text = _read(path)
        # Strip fenced code blocks: example payloads are not link targets.
        text = re.sub(r"```.*?```", "", text, flags=re.S)
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
