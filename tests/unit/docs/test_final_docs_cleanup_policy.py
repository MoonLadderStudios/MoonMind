from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

STEP_DOC = REPO_ROOT / "docs" / "Steps" / "StepExecutionsAndCheckpointing.md"
LEDGER_DOC = REPO_ROOT / "docs" / "Temporal" / "StepLedgerAndProgressModel.md"
ACTIVITY_DOC = REPO_ROOT / "docs" / "Temporal" / "ActivityCatalogAndWorkerTopology.md"
RUN_HISTORY_DOC = (
    REPO_ROOT / "docs" / "Temporal" / "WorkflowRunHistoryAndNewRunSemantics.md"
)
ROADMAP_DOC = REPO_ROOT / "docs" / "MoonMindRoadmap.md"
TEMP_PLAN = REPO_ROOT / "docs" / "tmp" / "StepExecutionsCheckpointingGapPlan.md"
RUN_WORKFLOW = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "workflows" / "run.py"
CONDITIONAL_DOCS = (
    REPO_ROOT / "docs" / "ManagedAgents" / "DockerSidecarRuntime.md",
    REPO_ROOT / "docs" / "Security" / "SecretsSystem.md",
    REPO_ROOT / "docs" / "Steps" / "PentestTool.md",
)


CANONICAL_DOCS = (
    STEP_DOC,
    LEDGER_DOC,
    ACTIVITY_DOC,
    RUN_HISTORY_DOC,
)

SECRET_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"ghp_[A-Za-z0-9_]+",
        r"github_pat_[A-Za-z0-9_]+",
        r"AIza[A-Za-z0-9_\-]+",
        r"ATATT[A-Za-z0-9_\-]+",
        r"AKIA[A-Z0-9]+",
        r"token\s*=",
        r"password\s*=",
        r"BEGIN .*PRIVATE KEY",
    )
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _manifest_builder_is_live() -> bool:
    return "build_step_execution_manifest_payload" in _read(RUN_WORKFLOW)


def test_canonical_docs_are_declarative_not_migration_tracking_surfaces() -> None:
    for path in CANONICAL_DOCS:
        text = _read(path)
        assert "Implementation tracking:" not in text, path
        assert "migration checklists in canonical `docs/`" not in text, path
        assert "Last updated: 2026-06-13" in text or "Last Updated: 2026-06-13" in text, path

    assert "**Status:** Draft" not in _read(RUN_HISTORY_DOC)
    assert "Status: Normative" in _read(LEDGER_DOC)


def test_manifest_consolidation_claims_follow_current_code_evidence() -> None:
    temp_text = _read(TEMP_PLAN)
    canonical_text = "\n".join(_read(path) for path in (STEP_DOC, LEDGER_DOC, ROADMAP_DOC))

    if _manifest_builder_is_live():
        assert "Legacy duplicate start manifest path | Still present" in temp_text
        assert "WP1" in temp_text
        assert not re.search(
            r"manifest (?:writer |)consolidation(?: is| remains)? unfinished",
            canonical_text,
            flags=re.IGNORECASE,
        )
    else:
        assert "build_step_execution_manifest_payload" not in temp_text


def test_roadmap_milestones_are_evidence_aligned_and_gated() -> None:
    text = _read(ROADMAP_DOC)

    assert "- [ ] **13.1** Resume-from-checkpoint as the default recovery path" in text
    assert "checkpoint restore logic exists but is not yet the primary operator flow" in text
    assert "- [ ] **3.2** Automatic RAG context injection per step" in text
    assert "- [ ] **5.3** Context pack assembly wired into agent runs" in text
    assert "- [ ] **13.3** Mission Control remediation panels" in text
    assert "- [ ] **7.7** Remediation panels - Tracked with 13.3" in text
    assert "- [ ] **13.4** Autonomous remediation supervisor" in text
    assert "Gated on:** 12.1" in text


def test_temp_plan_cleanup_guard_retains_active_plan_when_final_dod_is_open() -> None:
    assert TEMP_PLAN.exists()
    text = _read(TEMP_PLAN)

    assert "Status: Execution plan (disposable; not canonical)" in text
    assert "Final definition of done" in text
    if _manifest_builder_is_live():
        assert "deleted in the closing PR" in text
        assert "build_step_execution_manifest_payload" in text


def test_docs_do_not_inline_secrets_or_raw_evidence() -> None:
    checked_paths = [*CANONICAL_DOCS, ROADMAP_DOC, TEMP_PLAN]
    for path in checked_paths:
        text = _read(path)
        for pattern in SECRET_PATTERNS:
            assert pattern.search(text) is None, path
        assert "BEGIN_PROVIDER_PAYLOAD" not in text, path
        assert "```diff" not in text, path


def test_conditional_docs_are_left_untouched_without_behavioral_impact() -> None:
    temp_text = _read(TEMP_PLAN)

    for path in CONDITIONAL_DOCS:
        assert path.exists()
        assert f"`{path.relative_to(REPO_ROOT)}`: no Phase 12 update required" in temp_text
