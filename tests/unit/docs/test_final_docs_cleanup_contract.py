"""Static document/source contracts, collected by unit-fast (#3964)."""

from __future__ import annotations
from pathlib import Path

import pytest

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

    assert not TEMP_PLAN.exists()
    assert "Status: Execution plan (disposable; not canonical)" not in docs_text
    assert "Final definition of done" not in docs_text


def test_resume_acceptance_identifier_is_preserved() -> None:
    # The roadmap explicitly declares this a durable identifier. Checkbox state
    # and the explanation after it are progress, not part of that identity.
    assert "5.4 Resume-from-checkpoint default flow" in _read(ROADMAP_DOC)


@pytest.mark.parametrize("remove_identifier", [False, True])
def test_resume_identifier_guard_ignores_progress_but_detects_removal(
    monkeypatch, remove_identifier
) -> None:
    text = _read(ROADMAP_DOC).replace("- [ ]", "- [x]")
    if remove_identifier:
        text = text.replace("5.4 Resume-from-checkpoint default flow", "")
    monkeypatch.setattr(f"{__name__}._read", lambda _path: text)
    if remove_identifier:
        with pytest.raises(AssertionError):
            test_resume_acceptance_identifier_is_preserved()
    else:
        test_resume_acceptance_identifier_is_preserved()


def test_file_only_contract_is_collected_by_required_unit_shard(request) -> None:
    from tools.select_test_suites import select_suites
    from tools.verify_test_shard_ownership import CollectedNode, owners

    node = CollectedNode(
        request.node.nodeid,
        Path(__file__).relative_to(REPO_ROOT).as_posix(),
        frozenset(marker.name for marker in request.node.iter_markers()),
    )
    assert owners(node) == {"unit-fast"}
    for path in (STEP_DOC, LEDGER_DOC, ACTIVITY_DOC, RUN_HISTORY_DOC, ROADMAP_DOC):
        assert select_suites(
            [path.relative_to(REPO_ROOT).as_posix()], event_name="pull_request"
        ).unit_fast
