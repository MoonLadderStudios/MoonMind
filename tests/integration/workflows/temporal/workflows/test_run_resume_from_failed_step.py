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
