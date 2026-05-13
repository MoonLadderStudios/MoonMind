from __future__ import annotations

from datetime import UTC, datetime

from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    mark_step_checkpoint_evidence,
    materialize_preserved_steps,
    refresh_ready_steps,
    update_step_row,
)


def test_materialize_preserved_steps_marks_source_provenance_without_new_attempt() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "plan", "title": "Plan"},
            {"id": "implement", "title": "Implement"},
        ],
        dependency_map={"implement": ["plan"]},
        updated_at=now,
    )

    materialize_preserved_steps(
        rows,
        source_workflow_id="mm:source",
        source_run_id="run-source",
        preserved_steps=[
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "succeeded",
                "sourceAttempt": 2,
                "artifacts": {"outputSummary": "artifact://summary"},
                "stateCheckpointRef": "artifact://workspace/before-plan",
            }
        ],
        updated_at=now,
    )
    refresh_ready_steps(rows, updated_at=now)

    assert rows[0]["status"] == "succeeded"
    assert rows[0]["attempt"] == 0
    assert rows[0]["summary"] == "Preserved from source run."
    assert rows[0]["preservedFrom"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "plan",
        "attempt": 2,
    }
    assert rows[0]["artifacts"]["outputSummary"] == "artifact://summary"
    assert rows[0]["stateCheckpointRef"] == "artifact://workspace/before-plan"
    assert rows[1]["status"] == "ready"


def test_materialize_preserved_steps_keeps_outputs_for_downstream_steps() -> None:
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
                "order": 1,
                "status": "succeeded",
                "sourceAttempt": 1,
                "artifacts": {
                    "outputSummary": "artifact://prepare-summary",
                    "outputPrimary": "artifact://prepare-output",
                },
                "stateCheckpointRef": "artifact://workspace/before-prepare",
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
    assert rows[0]["artifacts"]["outputSummary"] == "artifact://prepare-summary"
    assert rows[0]["artifacts"]["outputPrimary"] == "artifact://prepare-output"
    assert rows[0]["stateCheckpointRef"] == "artifact://workspace/before-prepare"
    assert rows[1]["status"] == "ready"
    assert rows[2]["status"] == "pending"


def test_parent_owned_checkpoint_evidence_survives_child_runtime_projection() -> None:
    now = datetime.now(UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "delegate-agent", "title": "Delegate agent"},
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )

    update_step_row(
        rows,
        "delegate-agent",
        updated_at=now,
        status="succeeded",
        refs={"childWorkflowId": "wf-child", "childRunId": "run-child"},
        artifacts={"outputPrimary": "artifact://child-output"},
    )
    mark_step_checkpoint_evidence(
        rows,
        "delegate-agent",
        updated_at=now,
        state_checkpoint_ref="artifact://child-checkpoint",
    )

    assert rows[0]["refs"] == {
        "childWorkflowId": "wf-child",
        "childRunId": "run-child",
        "taskRunId": None,
    }
    assert rows[0]["stateCheckpointRef"] == "artifact://child-checkpoint"
    assert rows[0]["resumePreservation"] == {
        "eligible": True,
        "reason": "complete",
        "message": "Step has recoverable output refs and state checkpoint evidence.",
    }
