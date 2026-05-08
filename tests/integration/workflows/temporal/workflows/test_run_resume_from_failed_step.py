from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    materialize_preserved_steps,
    refresh_ready_steps,
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
    assert rows[0]["artifacts"]["outputSummary"] == "artifact://prepare-summary"
    assert rows[0]["stateCheckpointRef"] == "artifact://workspace/prepare"
    assert rows[1]["status"] == "ready"


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
