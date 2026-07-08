from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


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
STEP_EXECUTIONS = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "step_executions.py"
CHECKPOINTS = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "step_checkpoints.py"
TEMPORAL_MODELS = REPO_ROOT / "moonmind" / "schemas" / "temporal_models.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canonical_docs_and_runtime_evidence_agree_on_manifest_and_checkpoint_state() -> None:
    runtime_text = "\n".join(
        _read(path) for path in (RUN_WORKFLOW, STEP_EXECUTIONS, CHECKPOINTS, TEMPORAL_MODELS)
    )
    docs_text = "\n".join(_read(path) for path in (STEP_DOC, LEDGER_DOC, ACTIVITY_DOC, RUN_HISTORY_DOC))

    assert "STEP_EXECUTION_MANIFEST_CONTENT_TYPE" in runtime_text
    assert "STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE" in runtime_text
    assert "_record_step_execution_manifest" in runtime_text
    assert "build_step_execution_manifest_payload" not in runtime_text

    assert "application/vnd.moonmind.step-execution+json;version=1" in docs_text
    assert "application/vnd.moonmind.step-execution-checkpoint+json;version=1" in docs_text
    assert "implementation gap backlog" not in docs_text.lower()


def test_temp_plan_cleanup_matches_closed_final_definition_of_done() -> None:
    docs_text = "\n".join(
        _read(path) for path in (STEP_DOC, LEDGER_DOC, ACTIVITY_DOC, RUN_HISTORY_DOC)
    )
    roadmap_text = _read(ROADMAP_DOC)

    assert not TEMP_PLAN.exists()
    assert "Status: Execution plan (disposable; not canonical)" not in docs_text
    assert "Final definition of done" not in docs_text
    assert "- [ ] **5.2 Resume-from-checkpoint default flow**" in roadmap_text


def test_roadmap_and_implementation_evidence_remain_consistent() -> None:
    """Keep the roadmap's evidence claims consistent with shipped runtime code.

    The pinned roadmap substrings below encode durable invariants, not prose:
    the Omnigent-host direction, the checkpoint ``external_state_ref`` lane and
    Checkpoint Branch API claims, the ``5.2``/``6.2``/``7.1`` acceptance tasks,
    and the ``11.1`` PentestGPT external-egress safety gate. Update the roadmap
    to preserve the invariant rather than deleting an assertion here.
    """
    roadmap_text = _read(ROADMAP_DOC)
    runtime_text = "\n".join(_read(path) for path in (RUN_WORKFLOW, STEP_EXECUTIONS))

    assert "_record_step_execution_manifest" in runtime_text
    assert "build_step_execution_manifest_payload" not in runtime_text
    assert "Omnigent host as the unified managed agent runtime" in roadmap_text
    assert "checkpoint captures select the `external_state_ref` lane" in roadmap_text
    assert "Checkpoint Branch API and persistence model already support" in roadmap_text
    assert "- [ ] **5.2 Resume-from-checkpoint default flow**" in roadmap_text
    assert "- [ ] **6.2 Omnigent remediation context enrichment**" in roadmap_text
    assert "- [ ] **7.1 Initial context injection for Omnigent**" in roadmap_text
    assert "- [ ] **11.1 Restricted egress boundary for PentestGPT external targets**" in roadmap_text
