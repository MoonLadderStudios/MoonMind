from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    mark_step_checkpoint_evidence,
    materialize_preserved_steps,
    refresh_ready_steps,
    update_step_row,
)
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-resume",
        run_id="run-resume",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["user-1"]},
    )
    logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        isEnabledFor=lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "logger", logger)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)


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


def _workflow_with_resume(
    source: dict[str, object] | None = None,
) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._resume_source = source or _resume_source()
    return workflow


def _ordered_nodes() -> list[dict[str, object]]:
    return [
        {"id": "prepare", "title": "Prepare"},
        {"id": "implement", "title": "Implement"},
        {"id": "verify", "title": "Verify"},
    ]


def _dependency_map() -> dict[str, list[str]]:
    return {"prepare": [], "implement": ["prepare"], "verify": ["implement"]}


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
        "latestAttemptManifestRef": None,
        "attemptManifestRefs": [],
    }
    assert rows[0]["stateCheckpointRef"] == "artifact://child-checkpoint"
    assert rows[0]["resumePreservation"] == {
        "eligible": True,
        "reason": "complete",
        "message": "Step has recoverable output refs and state checkpoint evidence.",
    }

def test_empty_resume_source_is_treated_as_absent() -> None:
    now = datetime.now(UTC)
    workflow = MoonMindRunWorkflow()
    workflow._resume_source = {}

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    assert workflow._resume_failed_step_id is None
    assert workflow._resume_workspace == {}
    assert workflow._step_ledger_rows[0]["status"] == "ready"


def test_step_ledger_row_lookup_uses_initialized_index() -> None:
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=datetime.now(UTC),
    )

    assert workflow._step_ledger_row_for("prepare") is workflow._step_ledger_rows[0]
    assert workflow._step_ledger_row_for("missing") is None


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("sourceWorkflowId", "source workflow"),
        ("sourceRunId", "source run"),
        ("sourceTaskInputSnapshotRef", "task input snapshot"),
        ("failedStepId", "failed step"),
        ("resumeCheckpointRef", "resume checkpoint"),
    ],
)
def test_resume_source_validation_requires_compact_identity_before_execution(
    field: str,
    message: str,
) -> None:
    source = _resume_source(**{field: ""})
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match=message):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_resume_source_validation_requires_plan_identity_before_execution() -> None:
    source = _resume_source(sourcePlanDigest="", sourcePlanRef="")
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match="plan"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_resume_source_rejects_preserved_step_without_recoverable_output_ref() -> None:
    source = _resume_source(
        preservedSteps=[
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "sourceAttempt": 1,
                "artifacts": {},
                "stateCheckpointRef": "artifact://workspace/prepare",
            }
        ]
    )
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match="recoverable output"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_resume_source_restores_workspace_before_failed_step_execution() -> None:
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    restored_ref = workflow._restore_resume_workspace_for_failed_step("implement")

    assert restored_ref == "artifact://workspace/before-implement"
    assert workflow._resume_workspace_restored_ref == restored_ref
    assert workflow._restore_resume_workspace_for_failed_step("verify") is None


@pytest.mark.asyncio
async def test_resume_attempt_manifest_preserves_source_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Resuming")
    await workflow._record_step_attempt_manifest_start(
        "implement",
        updated_at=now,
        reason="resume_from_failed_step",
    )

    manifest = writes[0]["payload"]
    assert manifest["workflowId"] == "wf-resume"
    assert manifest["runId"] == "run-resume"
    assert manifest["attempt"] == 1
    assert manifest["lineage"] == {
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceLogicalStepId": "implement",
        "sourceAttempt": 1,
        "relationship": "resume_from_failed_step",
        "lineageAttemptOrdinal": 2,
    }
    assert manifest["workspace"]["policy"] == "start_from_last_passed_commit"
    assert manifest["workspace"]["checkpointRef"] == (
        "artifact://workspace/before-implement"
    )
    assert manifest["workspace"]["evidenceAccepted"] is True
    assert manifest["workspace"]["sourceAttempt"] == {
        "workflowId": "mm:source",
        "runId": "run-source",
        "logicalStepId": "implement",
        "attempt": 1,
    }


def test_resume_source_rejects_missing_workspace_evidence() -> None:
    source = _resume_source(resumeWorkspace={})
    workflow = _workflow_with_resume(source)

    with pytest.raises(ValueError, match="workspace evidence"):
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=datetime.now(UTC),
        )


def test_resume_source_accepts_branch_commit_workspace_evidence() -> None:
    now = datetime.now(UTC)
    source = _resume_source(resumeWorkspace={"branch": "feature", "commit": "abc123"})
    workflow = _workflow_with_resume(source)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    assert workflow._resume_workspace == {"branch": "feature", "commit": "abc123"}
    assert workflow._restore_resume_workspace_for_failed_step("implement") is None


def test_resume_source_accepts_checkpoint_payload_ref_workspace_evidence() -> None:
    now = datetime.now(UTC)
    source = _resume_source(
        resumeWorkspace={
            "checkpoint_payload_ref": "artifact://checkpoint/payload",
            "inline_checkpoint_metadata": "artifact://checkpoint/metadata",
        }
    )
    workflow = _workflow_with_resume(source)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    restored_ref = workflow._restore_resume_workspace_for_failed_step("implement")

    assert restored_ref == "artifact://checkpoint/payload"
    assert workflow._resume_workspace_restored_ref == restored_ref


def test_preserved_outputs_are_available_to_failed_step_dependencies() -> None:
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    outputs = workflow._preserved_outputs_for_step("implement")

    assert outputs == {
        "prepare": {
            "outputSummary": "artifact://prepare-summary",
            "outputPrimary": "artifact://prepare-output",
        }
    }


def test_retried_failed_step_records_fresh_evidence_without_source_provenance() -> None:
    now = datetime.now(UTC)
    workflow = _workflow_with_resume()
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    update_step_row(
        workflow._step_ledger_rows,
        "implement",
        updated_at=now,
        status="succeeded",
    )
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "outputs": {
                "outputSummaryRef": "artifact://implement-summary-new",
                "outputPrimaryRef": "artifact://implement-output-new",
                "stateCheckpointRef": "artifact://workspace/implement-new",
            }
        },
        updated_at=now,
    )

    implement_row = next(
        row
        for row in workflow._step_ledger_rows
        if row["logicalStepId"] == "implement"
    )
    assert "preservedFrom" not in implement_row
    assert (
        implement_row["artifacts"]["outputSummary"]
        == "artifact://implement-summary-new"
    )
    assert (
        implement_row["artifacts"]["outputPrimary"]
        == "artifact://implement-output-new"
    )
    assert implement_row["stateCheckpointRef"] == "artifact://workspace/implement-new"
