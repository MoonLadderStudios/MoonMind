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
    REPO_ROOT / "docs" / "ManagedAgents" / "DockerBackendService.md",
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
        assert re.search(
            r"^Last [Uu]pdated: 20\d{2}-\d{2}-\d{2}$",
            text,
            flags=re.MULTILINE,
        ), path

    assert "**Status:** Draft" not in _read(RUN_HISTORY_DOC)
    assert "Status: Normative" in _read(LEDGER_DOC)


def test_manifest_consolidation_claims_follow_current_code_evidence() -> None:
    canonical_text = "\n".join(_read(path) for path in (STEP_DOC, LEDGER_DOC, ROADMAP_DOC))

    assert not TEMP_PLAN.exists()
    if _manifest_builder_is_live():
        assert not re.search(
            r"manifest (?:writer |)consolidation(?: is| remains)? unfinished",
            canonical_text,
            flags=re.IGNORECASE,
        )
    else:
        assert "Manifest writer consolidation is completed" in canonical_text


def test_roadmap_milestones_are_evidence_aligned_and_gated() -> None:
    """Pin roadmap strings that encode durable safety/evidence invariants.

    These assertions are not stylistic. The ``11.1`` line and the "external
    targets stay gated until enforcement exists" phrase encode the PentestGPT
    external-egress safety gate; the ``5.1``/``5.4``/``5.5``/``6.2``/``7.1``
    task labels pin the checkpoint-resume, remediation-evidence, and
    RAG-injection acceptance claims. Do not delete an assertion here to make a
    roadmap edit pass — update the roadmap so the invariant still holds, or
    change the invariant deliberately with the owner's sign-off.
    """
    text = _read(ROADMAP_DOC)

    assert "Completed historical milestones have been removed from the active roadmap" in text
    assert "Omnigent host as the unified managed agent runtime" in text
    assert "checkpoint captures select the `external_state_ref` lane" in text
    assert "- [ ] **5.1 Checkpoint boundary and completeness**" in text
    assert "- [ ] **5.4 Resume-from-checkpoint default flow**" in text
    assert "- [ ] **5.5 Checkpoint Branch UI and runtime-profile gaps**" in text
    assert "- [ ] **6.2 Omnigent remediation context enrichment**" in text
    assert "- [ ] **7.1 Initial context injection for Omnigent**" in text
    assert "- [ ] **11.1 Restricted egress boundary for PentestGPT external targets**" in text
    assert "external targets stay gated until enforcement exists" in text


def test_temp_plan_cleanup_guard_removes_plan_after_final_dod_closes() -> None:
    assert not TEMP_PLAN.exists()

    canonical_text = "\n".join(_read(path) for path in CANONICAL_DOCS)
    assert "Status: Execution plan (disposable; not canonical)" not in canonical_text
    assert "Final definition of done" not in canonical_text


def test_docs_do_not_inline_secrets_or_raw_evidence() -> None:
    checked_paths = [*CANONICAL_DOCS, ROADMAP_DOC]
    for path in checked_paths:
        text = _read(path)
        for pattern in SECRET_PATTERNS:
            assert pattern.search(text) is None, path
        assert "BEGIN_PROVIDER_PAYLOAD" not in text, path
        assert "```diff" not in text, path


def test_conditional_docs_remain_present_after_temp_plan_cleanup() -> None:
    assert not TEMP_PLAN.exists()
    for path in CONDITIONAL_DOCS:
        assert path.exists()
