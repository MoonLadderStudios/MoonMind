from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    mark_step_checkpoint_evidence,
    materialize_preserved_steps,
    refresh_ready_steps,
    update_step_row,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _resume_source(**overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
        "sourcePlanDigest": "sha256:source-plan",
        "failedStepId": "implement",
        "failedStepAttempt": 1,
        "resumeCheckpointRef": "artifact://resume/checkpoint",
        "resumeWorkspace": {
            "checkpointRef": "artifact://workspace/before-implement",
        },
        "preservedSteps": [
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceAttempt": 1,
                "artifacts": {
                    "outputSummary": "artifact://prepare-summary",
                    "outputPrimary": "artifact://prepare-output",
                },
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ],
    }
    source.update(overrides)
    return source


def _ordered_nodes() -> list[dict[str, object]]:
    return [
        {"id": "prepare", "title": "Prepare"},
        {"id": "implement", "title": "Implement"},
        {"id": "verify", "title": "Verify"},
    ]


def _dependency_map() -> dict[str, list[str]]:
    return {"prepare": [], "implement": ["prepare"], "verify": ["implement"]}


def _resume_workflow(source: dict[str, object] | None = None) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._resume_source = source or _resume_source()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=datetime.now(UTC),
    )
    return workflow


@pytest.mark.integration
@pytest.mark.integration_ci
def test_failed_step_resume_preserves_prior_steps_and_unblocks_failed_step() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "prepare", "title": "Prepare"},
            {"id": "implement", "title": "Implement"},
        ],
        dependency_map={"implement": ["prepare"]},
        updated_at=now,
    )

    materialize_preserved_steps(
        rows,
        source_workflow_id="mm:source",
        source_run_id="run-source",
        preserved_steps=[
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceAttempt": 1,
                "artifacts": {"outputSummary": "artifact://prepare-summary"},
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ],
        updated_at=now,
    )
    refresh_ready_steps(rows, updated_at=now)

    assert rows[0]["preservedFrom"]["workflowId"] == "mm:source"
    assert rows[0]["preservedFrom"]["logicalStepId"] == "prepare"
    assert rows[0]["artifacts"]["outputSummary"] == "artifact://prepare-summary"
    assert rows[0]["stateCheckpointRef"] == "artifact://workspace/prepare"
    assert rows[1]["status"] == "ready"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_failed_step_resume_preserves_only_prior_steps_before_downstream_work() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "prepare", "title": "Prepare"},
            {"id": "implement", "title": "Implement"},
            {"id": "verify", "title": "Verify"},
        ],
        dependency_map={"implement": ["prepare"], "verify": ["implement"]},
        updated_at=now,
    )

    materialize_preserved_steps(
        rows,
        source_workflow_id="mm:source",
        source_run_id="run-source",
        preserved_steps=[
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceAttempt": 1,
                "artifacts": {"outputSummary": "artifact://prepare-summary"},
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ],
        updated_at=now,
    )
    refresh_ready_steps(rows, updated_at=now)

    assert rows[0]["attempt"] == 0
    assert rows[0]["preservedFrom"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "prepare",
        "attempt": 1,
    }
    assert rows[1]["logicalStepId"] == "implement"
    assert rows[1]["status"] == "ready"
    assert "preservedFrom" not in rows[1]
    assert rows[2]["logicalStepId"] == "verify"
    assert rows[2]["status"] == "pending"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_failed_step_resume_rejects_preserved_step_without_state_checkpoint() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "prepare", "title": "Prepare"},
            {"id": "implement", "title": "Implement"},
        ],
        dependency_map={"implement": ["prepare"]},
        updated_at=now,
    )

    with pytest.raises(ValueError, match="state checkpoint"):
        materialize_preserved_steps(
            rows,
            source_workflow_id="mm:source",
            source_run_id="run-source",
            preserved_steps=[
                {
                    "logicalStepId": "prepare",
                    "status": "succeeded",
                    "sourceAttempt": 1,
                    "artifacts": {"outputSummary": "artifact://prepare-summary"},
                }
            ],
            updated_at=now,
        )


@pytest.mark.integration
@pytest.mark.integration_ci
def test_source_run_marks_missing_completed_step_evidence_ineligible() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "prepare", "title": "Prepare"},
            {"id": "implement", "title": "Implement"},
        ],
        dependency_map={"implement": ["prepare"]},
        updated_at=now,
    )
    update_step_row(rows, "prepare", updated_at=now, status="succeeded")

    mark_step_checkpoint_evidence(rows, "prepare", updated_at=now)
    refresh_ready_steps(rows, updated_at=now)

    assert rows[0]["resumePreservation"]["eligible"] is False
    assert rows[0]["resumePreservation"]["reason"] == "missing_output_refs"
    assert rows[1]["status"] == "ready"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_source_run_marks_completed_step_without_checkpoint_ineligible() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "prepare", "title": "Prepare"},
            {"id": "implement", "title": "Implement"},
        ],
        dependency_map={"implement": ["prepare"]},
        updated_at=now,
    )
    update_step_row(
        rows,
        "prepare",
        updated_at=now,
        status="succeeded",
        artifacts={"outputPrimary": "artifact://prepare-output"},
    )

    mark_step_checkpoint_evidence(rows, "prepare", updated_at=now)
    refresh_ready_steps(rows, updated_at=now)

    assert rows[0]["resumePreservation"]["eligible"] is False
    assert rows[0]["resumePreservation"]["reason"] == "missing_state_checkpoint"
    assert rows[1]["status"] == "ready"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_resume_initialization_validates_source_identity_before_execution() -> None:
    workflow = _resume_workflow()

    assert workflow._resume_failed_step_id == "implement"
    assert workflow._step_ledger_rows[0]["preservedFrom"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "prepare",
        "attempt": 1,
    }
    assert workflow._step_ledger_rows[1]["status"] == "ready"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_resume_restores_workspace_checkpoint_before_failed_step_starts() -> None:
    workflow = _resume_workflow()

    restored_ref = workflow._restore_resume_workspace_for_failed_step("implement")

    assert restored_ref == "artifact://workspace/before-implement"
    assert workflow._resume_workspace_restored_ref == restored_ref


@pytest.mark.integration
@pytest.mark.integration_ci
def test_resume_preserved_outputs_are_injected_for_failed_step_continuation() -> None:
    workflow = _resume_workflow()

    outputs = workflow._preserved_outputs_for_step("implement")

    assert outputs["prepare"]["outputSummary"] == "artifact://prepare-summary"
    assert outputs["prepare"]["outputPrimary"] == "artifact://prepare-output"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_resume_failed_step_first_then_downstream_continues_after_success() -> None:
    workflow = _resume_workflow()
    now = datetime.now(UTC)

    ready_steps = [
        row["logicalStepId"]
        for row in workflow._step_ledger_rows
        if row["status"] == "ready"
    ]
    assert ready_steps == ["implement"]

    update_step_row(
        workflow._step_ledger_rows,
        "implement",
        updated_at=now,
        status="succeeded",
        artifacts={"outputPrimary": "artifact://implement-output-new"},
    )
    mark_step_checkpoint_evidence(
        workflow._step_ledger_rows,
        "implement",
        updated_at=now,
        state_checkpoint_ref="artifact://workspace/implement-new",
    )
    refresh_ready_steps(workflow._step_ledger_rows, updated_at=now)

    verify_row = next(
        row for row in workflow._step_ledger_rows if row["logicalStepId"] == "verify"
    )
    assert verify_row["status"] == "ready"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_invalid_resume_restoration_fails_without_full_rerun() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._resume_source = _resume_source(resumeWorkspace={})

    with pytest.raises(ValueError, match="workspace evidence"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )

    assert workflow._step_ledger_rows == []


@pytest.mark.integration
@pytest.mark.integration_ci
def test_resume_initialization_accepts_existing_workspace_evidence_formats() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._resume_source = _resume_source(
        resumeWorkspace={"branch": "feature", "commit": "abc123"}
    )

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=datetime.now(UTC),
    )

    assert workflow._resume_workspace == {"branch": "feature", "commit": "abc123"}
    assert workflow._restore_resume_workspace_for_failed_step("implement") is None
