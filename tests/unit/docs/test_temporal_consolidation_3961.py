"""Consolidation guardrails for Temporal documentation.

Source: MoonLadderStudios/MoonMind#3961 (consolidate Temporal docs by contract
ownership, not a fixed file quota; parent #3929). These assertions pin the
durable outcomes: one short module entrypoint naming the canonical owner for
each major contract, one surviving-owner map that covers every
``docs/Temporal/`` file, preserved recovery/continue-as-new/replay/pause/
projection-repair/publication/cleanup guarantees, duplicate catalog sections
replaced by owner pointers, and valid internal links/anchors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _semantic_docs_3964 import assert_semantic_present


REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPORAL = REPO_ROOT / "docs" / "Temporal"
ENTRYPOINT = TEMPORAL / "TemporalModuleArchitecture.md"
OWNERSHIP = REPO_ROOT / "docs" / "tmp" / "temporal-consolidation-ownership-map-3961.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    for match in re.finditer(r"^(#{1,6})\s+(.*)", text, re.M):
        slug = match.group(2).strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slugs.add(re.sub(r"\s+", "-", slug))
    return slugs


def test_entrypoint_exists_linking_each_major_contract() -> None:
    text = _read(ENTRYPOINT)
    assert "MoonLadderStudios/MoonMind#3961" in text
    # Module entrypoint defines no new semantics; owners win on conflict.
    assert_semantic_present(
        text,
        ("owner", "disagree"),
        context="entrypoint owner-authority rule",
    )
    # Every major contract from the issue plan is reachable from one page.
    for owner in (
        "WorkflowTypeCatalogAndLifecycle.md",
        "WorkflowTypeCatalogGenerated.md",
        "ActivityCatalogAndWorkerTopology.md",
        "ManagedAndExternalAgentExecutionModel.md",
        "WorkerPauseSystem.md",
        "SourceOfTruthAndProjectionModel.md",
        "WorkflowArtifactSystemDesign.md",
        "WorkflowRunHistoryAndNewRunSemantics.md",
        "ops-runbook.md",
    ):
        assert owner in text, owner
    # Single-inventory authority rule: no second handwritten type list.
    assert_semantic_present(
        text,
        ("exactly one", "inventory"),
        context="entrypoint single-catalog authority rule",
    )


def test_ownership_map_covers_every_temporal_file() -> None:
    text = _read(OWNERSHIP)
    assert "MoonLadderStudios/MoonMind#3961" in text
    for path in sorted(TEMPORAL.glob("*.md")):
        assert path.name in text, path.name


def test_recovery_and_continue_as_new_contract_survives() -> None:
    source = _read(TEMPORAL / "SourceOfTruthAndProjectionModel.md")
    assert_semantic_present(
        source,
        ("continue-as-new", "recovery"),
        context="source-of-truth recovery semantics",
    )
    assert_semantic_present(
        source,
        ("projection repair",),
        context="source-of-truth projection repair",
    )
    architecture = _read(TEMPORAL / "TemporalArchitecture.md")
    assert "## 18." in architecture
    assert_semantic_present(
        architecture,
        ("replay-safe",),
        context="architecture replay-safe evolution",
    )


def test_pause_control_contract_survives() -> None:
    pause = _read(TEMPORAL / "WorkerPauseSystem.md")
    assert "Pause" in pause
    assert "Resume" in pause
    assert "control_state" in pause


def test_duplicates_are_owner_pointers() -> None:
    foundation = _read(TEMPORAL / "TemporalPlatformFoundation.md")
    assert "| `MoonMind.UserWorkflow` | General root execution workflow" not in foundation
    assert "WorkflowTypeCatalogGenerated.md" in foundation
    agent_execution = _read(TEMPORAL / "TemporalAgentExecution.md")
    assert "| `plan.generate` | llm |" not in agent_execution
    assert "ActivityCatalogAndWorkerTopology.md" in agent_execution
    assert "WorkflowTypeCatalogGenerated.md" in agent_execution


def test_internal_links_and_anchors_resolve() -> None:
    checked = 0
    for path in sorted(TEMPORAL.glob("*.md")):
        text = _read(path)
        for match in re.finditer(r"\]\(([^)]+)\)", text):
            link = match.group(1)
            if link.startswith(("http", "mailto:")) or link.startswith("#"):
                continue
            if link.endswith(".md") or ".md#" in link:
                if "#" in link:
                    rel, anchor = link.split("#", 1)
                    target = path.parent / rel if rel else path
                else:
                    target, anchor = path.parent / link, None
                assert target.exists(), f"{path.name}: {link}"
                if anchor and target.suffix == ".md":
                    assert anchor in _slugs(_read(target)), (
                        f"{path.name}: {link}"
                    )
                    checked += 1
                elif target.suffix == ".md":
                    checked += 1
    assert checked > 0
