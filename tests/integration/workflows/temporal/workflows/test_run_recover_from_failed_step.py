from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    invalidate_downstream_steps_for_changed_output,
    mark_step_checkpoint_evidence,
    materialize_preserved_steps,
    record_dependency_inputs_for_step,
    refresh_ready_steps,
    update_step_row,
    validate_preserved_dependency_outputs,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _recovery_source(**overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
        "sourcePlanDigest": "sha256:source-plan",
        "failedStepId": "implement",
        "failedStepExecution": 1,
        "recoveryCheckpointRef": "artifact://resume/checkpoint",
        "recoveryWorkspace": {
            "checkpointRef": "artifact://workspace/before-implement",
        },
        "preservedSteps": [
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
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


def _recovery_workflow(source: dict[str, object] | None = None) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = source or _recovery_source()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=datetime.now(UTC),
    )
    return workflow


@pytest.mark.integration
@pytest.mark.integration_ci
def test_failed_step_recovery_preserves_prior_steps_and_unblocks_failed_step() -> None:
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
                "sourceExecutionOrdinal": 1,
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
def test_failed_step_recovery_preserves_only_prior_steps_before_downstream_work() -> None:
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
                "sourceExecutionOrdinal": 1,
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
        "executionOrdinal": 1,
    }
    assert rows[1]["logicalStepId"] == "implement"
    assert rows[1]["status"] == "ready"
    assert "preservedFrom" not in rows[1]
    assert rows[2]["logicalStepId"] == "verify"
    assert rows[2]["status"] == "pending"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_failed_step_recovery_rejects_preserved_step_without_state_checkpoint() -> None:
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
                    "sourceExecutionOrdinal": 1,
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

    assert rows[0]["recoveryPreservation"]["eligible"] is False
    assert rows[0]["recoveryPreservation"]["reason"] == "missing_output_refs"
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

    assert rows[0]["recoveryPreservation"]["eligible"] is False
    assert rows[0]["recoveryPreservation"]["reason"] == "missing_state_checkpoint"
    assert rows[1]["status"] == "ready"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_recovery_initialization_validates_source_identity_before_execution() -> None:
    workflow = _recovery_workflow()

    assert workflow._recovery_failed_step_id == "implement"
    assert workflow._step_ledger_rows[0]["preservedFrom"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "prepare",
        "executionOrdinal": 1,
    }
    assert workflow._step_ledger_rows[1]["status"] == "ready"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_recovery_restores_workspace_checkpoint_before_failed_step_starts() -> None:
    workflow = _recovery_workflow()

    restored_ref = workflow._restore_recovery_workspace_for_failed_step("implement")

    assert restored_ref == "artifact://workspace/before-implement"
    assert workflow._recovery_workspace_restored_ref == restored_ref


@pytest.mark.integration
@pytest.mark.integration_ci
def test_recovery_preserved_outputs_are_injected_for_failed_step_continuation() -> None:
    workflow = _recovery_workflow()

    outputs = workflow._preserved_outputs_for_step("implement")

    assert outputs["prepare"]["outputSummary"] == "artifact://prepare-summary"
    assert outputs["prepare"]["outputPrimary"] == "artifact://prepare-output"
    assert outputs["prepare"]["producingAttempt"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "prepare",
        "executionOrdinal": 1,
    }


@pytest.mark.integration
@pytest.mark.integration_ci
def test_recovery_failed_step_first_then_downstream_continues_after_success() -> None:
    workflow = _recovery_workflow()
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
def test_dependency_inputs_pin_specific_producing_attempt_outputs() -> None:
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
                "sourceExecutionOrdinal": 2,
                "artifacts": {
                    "outputSummary": "artifact://prepare-summary",
                    "outputPrimary": "artifact://prepare-output",
                },
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ],
        updated_at=now,
    )

    signatures = record_dependency_inputs_for_step(
        rows,
        "implement",
        workflow_id="mm:resume",
        run_id="run-resume",
        updated_at=now,
    )

    assert signatures["prepare"] == {
        "producingAttempt": {
            "workflowId": "mm:source",
            "runId": "run-source",
            "logicalStepId": "prepare",
            "executionOrdinal": 2,
        },
        "outputRefs": {
            "outputSummary": "artifact://prepare-summary",
            "outputPrimary": "artifact://prepare-output",
        },
    }
    assert rows[1]["dependencyInputs"] == signatures


@pytest.mark.integration
@pytest.mark.integration_ci
def test_changed_upstream_output_marks_executed_downstream_for_revalidation() -> None:
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
    update_step_row(
        rows,
        "implement",
        updated_at=now,
        status="succeeded",
        increment_attempt=True,
        artifacts={"outputPrimary": "artifact://implement-output-v1"},
    )
    record_dependency_inputs_for_step(
        rows,
        "verify",
        workflow_id="mm:resume",
        run_id="run-resume",
        updated_at=now,
    )
    update_step_row(
        rows,
        "verify",
        updated_at=now,
        status="succeeded",
        increment_attempt=True,
        artifacts={"outputPrimary": "artifact://verify-output-v1"},
    )
    update_step_row(
        rows,
        "implement",
        updated_at=now,
        status="succeeded",
        increment_attempt=True,
        artifacts={"outputPrimary": "artifact://implement-output-v2"},
    )

    invalidated = invalidate_downstream_steps_for_changed_output(
        rows,
        "implement",
        workflow_id="mm:resume",
        run_id="run-resume",
        updated_at=now,
    )

    assert invalidated == ["verify"]
    assert rows[2]["status"] == "pending"
    assert rows[2]["waitingReason"] == "requires_revalidation"
    assert rows[2]["dependencyReuseGate"]["status"] == "requires_revalidation"


@pytest.mark.integration
@pytest.mark.integration_ci
def test_changed_upstream_output_clears_preserved_downstream_revalidation_marker() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "implement", "title": "Implement"},
            {"id": "verify", "title": "Verify"},
        ],
        dependency_map={"verify": ["implement"]},
        updated_at=now,
    )
    materialize_preserved_steps(
        rows,
        source_workflow_id="mm:source",
        source_run_id="run-source",
        preserved_steps=[
            {
                "logicalStepId": "verify",
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"outputPrimary": "artifact://verify-output-old"},
                "stateCheckpointRef": "artifact://workspace/verify-old",
                "dependencyInputs": {
                    "implement": {
                        "producingAttempt": {
                            "workflowId": "mm:resume",
                            "runId": "run-resume",
                            "logicalStepId": "implement",
                            "executionOrdinal": 1,
                        },
                        "outputRefs": {
                            "outputPrimary": "artifact://implement-output-old"
                        },
                    }
                },
            },
        ],
        updated_at=now,
    )
    update_step_row(
        rows,
        "implement",
        updated_at=now,
        status="succeeded",
        increment_attempt=True,
        artifacts={"outputPrimary": "artifact://implement-output-new"},
    )

    invalidated = invalidate_downstream_steps_for_changed_output(
        rows,
        "implement",
        workflow_id="mm:resume",
        run_id="run-resume",
        updated_at=now,
    )

    assert invalidated == ["verify"]
    assert rows[1]["status"] == "pending"
    assert rows[1]["waitingReason"] == "requires_revalidation"
    assert "preservedFrom" not in rows[1]
    assert "recoveryPreservation" not in rows[1]
    assert "stateCheckpointRef" not in rows[1]


@pytest.mark.integration
@pytest.mark.integration_ci
def test_preserved_downstream_reuse_requires_matching_dependency_gate() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "implement", "title": "Implement"},
            {"id": "verify", "title": "Verify"},
        ],
        dependency_map={"verify": ["implement"]},
        updated_at=now,
    )
    materialize_preserved_steps(
        rows,
        source_workflow_id="mm:source",
        source_run_id="run-source",
        preserved_steps=[
            {
                "logicalStepId": "implement",
                "status": "succeeded",
                "sourceExecutionOrdinal": 2,
                "artifacts": {"outputPrimary": "artifact://implement-output-new"},
                "stateCheckpointRef": "artifact://workspace/implement-new",
            },
            {
                "logicalStepId": "verify",
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"outputPrimary": "artifact://verify-output-old"},
                "stateCheckpointRef": "artifact://workspace/verify-old",
                "dependencyInputs": {
                    "implement": {
                        "producingAttempt": {
                            "workflowId": "mm:source",
                            "runId": "run-source",
                            "logicalStepId": "implement",
                            "executionOrdinal": 1,
                        },
                        "outputRefs": {
                            "outputPrimary": "artifact://implement-output-old"
                        },
                    }
                },
            },
        ],
        updated_at=now,
    )

    with pytest.raises(ValueError, match="requires revalidation"):
        validate_preserved_dependency_outputs(rows, updated_at=now)

    assert rows[1]["status"] == "pending"
    assert rows[1]["waitingReason"] == "requires_revalidation"
    assert "preservedFrom" not in rows[1]
    assert "recoveryPreservation" not in rows[1]
    assert "stateCheckpointRef" not in rows[1]


@pytest.mark.integration
@pytest.mark.integration_ci
def test_invalid_recovery_restoration_fails_without_full_rerun() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = _recovery_source(recoveryWorkspace={})

    with pytest.raises(ValueError, match="workspace evidence"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )

    assert workflow._step_ledger_rows == []


@pytest.mark.integration
@pytest.mark.integration_ci
def test_recovery_initialization_accepts_existing_workspace_evidence_formats() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = _recovery_source(
        recoveryWorkspace={"branch": "feature", "commit": "abc123"}
    )

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=datetime.now(UTC),
    )

    assert workflow._recovery_workspace == {"branch": "feature", "commit": "abc123"}
    assert workflow._restore_recovery_workspace_for_failed_step("implement") is None
