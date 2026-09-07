from __future__ import annotations

import re
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
TRACKER_DOC = REPO_ROOT / "docs" / "tmp" / "MoonMindRoadmapExecutionTracker.md"
TEMP_PLAN = REPO_ROOT / "docs" / "tmp" / "StepExecutionsCheckpointingGapPlan.md"
RUN_WORKFLOW = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "workflows" / "run.py"
STEP_EXECUTIONS = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "step_executions.py"
CHECKPOINTS = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "step_checkpoints.py"
TEMPORAL_MODELS = REPO_ROOT / "moonmind" / "schemas" / "temporal_models.py"

# Stable acceptance-claim identifiers (MoonLadderStudios/MoonMind#3965).
# Each entry maps a claim to its key semantic phrases and its canonical
# successor document(s). Tests assert meaning and unresolved status, never
# exact roadmap wording or layout.
CLAIM_SUCCESSORS = {
    "5.1": {
        "meaning": ("Checkpoint boundary", "acceptance evidence"),
        "successors": ("StepExecutionsAndCheckpointing", "CheckpointResumePromotion"),
        "tracking": "#3510",
    },
    "5.4": {
        "meaning": ("Resume-from-checkpoint", "reattach", "cold restore"),
        "successors": ("CheckpointResumePromotion",),
        "tracking": "#3510",
    },
    "5.5": {
        "meaning": ("Checkpoint Branch", "compare", "promote"),
        "successors": ("CheckpointBranchSystem",),
        "tracking": "#3510",
    },
    "6.2": {
        "meaning": ("remediation", "target-authorized", "typed action"),
        "successors": ("WorkflowRemediation",),
        "tracking": "#3512",
    },
    "7.1": {
        "meaning": ("context injection", "ContextPack", "first-message"),
        "successors": ("WorkflowRag",),
        "tracking": "#3514",
    },
}

# Active execution owners that must not be lost when the roadmap is shortened.
REQUIRED_TRACKED_ISSUES = (
    "#3507",
    "#3508",
    "#3510",
    "#3512",
    "#3514",
    "#3516",
    "#3517",
    "#3518",
)

# Closed-but-incomplete residuals that still need an explicit owner.
REQUIRED_RESIDUAL_ISSUES = ("#3509", "#3511", "#3513", "#3515", "#3519", "#3520")


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
    # Concise entrypoint: editorial target is ~120 lines; allow headroom but
    # fail if the full multi-hundred-line contract dump returns.
    assert len(roadmap_text.splitlines()) <= 150, len(roadmap_text.splitlines())
    # Every stable claim keeps its identifier, unresolved status, meaning, and
    # traceable successor without depending on exact bold wording or layout.
    for claim_id, spec in CLAIM_SUCCESSORS.items():
        assert claim_id in roadmap_text, claim_id
        for phrase in spec["meaning"]:
            haystack = roadmap_text
            if phrase not in haystack:
                haystack = "\n".join(
                    _read(REPO_ROOT / "docs" / name)
                    for name in (
                        "Steps/StepExecutionsAndCheckpointing.md",
                        "Temporal/CheckpointResumePromotion.md",
                        "Workflows/CheckpointBranchSystem.md",
                        "Workflows/WorkflowRemediation.md",
                        "Rag/WorkflowRag.md",
                    )
                )
            assert phrase.lower() in haystack.lower(), (claim_id, phrase)
        for successor in spec["successors"]:
            assert successor in roadmap_text, (claim_id, successor)
        assert spec["tracking"] in roadmap_text, (claim_id, spec["tracking"])
    # No false completion: claims stay unchecked and are never labeled supported.
    for claim_id in CLAIM_SUCCESSORS:
        assert f"[x] **{claim_id}" not in roadmap_text, claim_id
        assert f"[X] **{claim_id}" not in roadmap_text, claim_id
    assert "5.4" in roadmap_text
    assert re.search(r"5\.4.{0,300}supported", roadmap_text, flags=re.IGNORECASE) is None


def test_roadmap_links_and_tracker_handoff_resolve() -> None:
    roadmap_text = _read(ROADMAP_DOC)
    # Roadmap points at canonical contracts and the disposable handoff.
    assert "PrimaryRuntimeProviderStrategy" in roadmap_text
    assert "OmnigentHarnessPlatformDesign" in roadmap_text
    assert "RuntimeProviderRollout" in roadmap_text
    assert "MoonMindRoadmapExecutionTracker" in roadmap_text
    assert TRACKER_DOC.exists()
    tracker_text = _read(TRACKER_DOC)
    for issue in REQUIRED_TRACKED_ISSUES:
        assert issue in tracker_text, issue
    for issue in REQUIRED_RESIDUAL_ISSUES:
        assert issue in tracker_text, issue
    # Handoff preserves unresolved status; merge counts alone never close items.
    assert "unresolved" in tracker_text.lower() or "open" in tracker_text.lower()
    assert "3971" in tracker_text  # canceled demo is explicitly not recreated
    # Every relative markdown link from the roadmap resolves to a real file.
    docs_dir = REPO_ROOT / "docs"
    for match in re.finditer(r"\]\(([^)#\s]+\.md)\)", roadmap_text):
        target = (docs_dir / match.group(1)).resolve()
        assert str(target).startswith(str(REPO_ROOT)), match.group(1)
        assert target.exists(), match.group(1)
