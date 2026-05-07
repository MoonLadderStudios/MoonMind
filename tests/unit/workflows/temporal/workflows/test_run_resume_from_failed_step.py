from __future__ import annotations

from datetime import UTC, datetime

from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    materialize_preserved_steps,
    refresh_ready_steps,
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
        "attempt": 2,
    }
    assert rows[0]["artifacts"]["outputSummary"] == "artifact://summary"
    assert rows[1]["status"] == "ready"
