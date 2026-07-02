from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    ExecutionProgressModel,
    StepLedgerArtifactsModel,
    StepLedgerCheckModel,
    StepLedgerRefsModel,
    StepLedgerRowModel,
    StepLedgerSnapshotModel,
)
from moonmind.statuses.step_ledger import step_execution_to_ledger_status
from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    build_progress_summary,
    mark_step_execution_manifest_evidence,
    mark_step_checkpoint_evidence,
    refresh_ready_steps,
    upsert_step_check,
    update_step_row,
)


def test_step_execution_artifact_status_converts_explicitly_to_step_ledger_status() -> None:
    assert step_execution_to_ledger_status("pending") == "pending"
    assert step_execution_to_ledger_status("preparing") == "executing"
    assert step_execution_to_ledger_status("executing") == "executing"
    assert step_execution_to_ledger_status("running") == "executing"
    assert step_execution_to_ledger_status("checking") == "reviewing"
    assert step_execution_to_ledger_status("completed") == "completed"
    assert step_execution_to_ledger_status("succeeded") == "completed"
    assert step_execution_to_ledger_status("blocked") == "awaiting_external"
    assert step_execution_to_ledger_status("superseded") == "skipped"


def test_build_initial_step_rows_uses_plan_metadata_and_dependencies() -> None:
    updated_at = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "step-1",
                "tool": {"type": "skill", "name": "repo.setup"},
                "inputs": {"title": "Prepare workspace"},
            },
            {
                "id": "step-2",
                "tool": {"type": "skill", "name": "repo.test"},
                "inputs": {"title": "Run tests"},
            },
        ],
        dependency_map={
            "step-1": [],
            "step-2": ["step-1"],
        },
        updated_at=updated_at,
    )

    assert [row["logicalStepId"] for row in rows] == ["step-1", "step-2"]
    assert rows[0]["title"] == "Prepare workspace"
    assert rows[0]["status"] == "ready"
    assert rows[0]["attempt"] == 0
    assert rows[0]["dependsOn"] == []
    assert rows[1]["title"] == "Run tests"
    assert rows[1]["status"] == "pending"
    assert rows[1]["dependsOn"] == ["step-1"]
    assert rows[1]["tool"] == {"type": "skill", "name": "repo.test"}
    assert rows[0]["refs"]["latestStepExecutionManifestRef"] is None
    assert rows[0]["refs"]["stepExecutionManifestRefs"] == []
    assert rows[0]["refs"]["latestStepExecutionCheckpointRef"] is None
    assert rows[0]["refs"]["stepExecutionCheckpointRefs"] == []
    assert rows[0]["refs"]["checkpointRefsByBoundary"] == {}


def test_step_ledger_refs_track_latest_and_historical_step_execution_manifests() -> None:
    refs = StepLedgerRefsModel.model_validate(
        {
            "childWorkflowId": "child-1",
            "childRunId": "run-child",
            "agentRunId": "agent-run",
            "latestStepExecutionManifestRef": "artifact-attempt-2",
            "stepExecutionManifestRefs": ["artifact-attempt-1", "artifact-attempt-2"],
            "latestStepExecutionCheckpointRef": "artifact-checkpoint-2",
            "stepExecutionCheckpointRefs": [
                "artifact-checkpoint-1",
                "artifact-checkpoint-2",
            ],
            "checkpointRefsByBoundary": {
                "before_execution": "artifact-checkpoint-1",
                "after_execution": "artifact-checkpoint-2",
            },
        }
    )

    assert refs.latest_step_execution_manifest_ref == "artifact-attempt-2"
    assert refs.step_execution_manifest_refs == [
        "artifact-attempt-1",
        "artifact-attempt-2",
    ]
    assert refs.latest_step_execution_checkpoint_ref == "artifact-checkpoint-2"
    assert refs.step_execution_checkpoint_refs == [
        "artifact-checkpoint-1",
        "artifact-checkpoint-2",
    ]
    assert refs.checkpoint_refs_by_boundary == {
        "before_execution": "artifact-checkpoint-1",
        "after_execution": "artifact-checkpoint-2",
    }
    assert refs.model_dump(by_alias=True)["latestStepExecutionManifestRef"] == (
        "artifact-attempt-2"
    )


def test_step_ledger_row_rejects_removed_status_tokens() -> None:
    updated_at = datetime(2026, 4, 7, 12, 10, tzinfo=UTC)
    base_row = {
        "logicalStepId": "run-tests",
        "order": 1,
        "title": "Run tests",
        "tool": {"type": "skill", "name": "repo.run_tests"},
        "dependsOn": [],
        "waitingReason": None,
        "attentionRequired": False,
        "executionOrdinal": 1,
        "updatedAt": updated_at.isoformat(),
        "summary": None,
        "checks": [],
        "refs": {"childWorkflowId": None, "childRunId": None, "agentRunId": None},
        "artifacts": {},
        "lastError": None,
    }

    for removed_status in ("running", "succeeded"):
        with pytest.raises(ValidationError):
            StepLedgerRowModel.model_validate({**base_row, "status": removed_status})


def test_build_initial_step_rows_skips_blank_node_ids() -> None:
    updated_at = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "",
                "tool": {"type": "skill", "name": "repo.invalid"},
            },
            {
                "id": "step-2",
                "tool": {"type": "skill", "name": "repo.test"},
                "inputs": {"title": "Run tests"},
            },
        ],
        dependency_map={"step-2": []},
        updated_at=updated_at,
    )

    assert rows == [
        {
            "logicalStepId": "step-2",
            "order": 1,
            "title": "Run tests",
            "tool": {"type": "skill", "name": "repo.test"},
            "dependsOn": [],
            "status": "ready",
            "waitingReason": None,
            "attentionRequired": False,
            "attempt": 0,
            "executionOrdinal": 0,
            "startedAt": None,
            "updatedAt": updated_at.isoformat(),
            "summary": None,
            "checks": [],
            "refs": {
                "childWorkflowId": None,
                "childRunId": None,
                "agentRunId": None,
                "latestStepExecutionManifestRef": None,
                "stepExecutionManifestRefs": [],
                "latestStepExecutionCheckpointRef": None,
                "stepExecutionCheckpointRefs": [],
                "checkpointRefsByBoundary": {},
            },
            "artifacts": {
                "outputSummary": None,
                "outputPrimary": None,
                "runtimeStdout": None,
                "runtimeStderr": None,
                "runtimeMergedLogs": None,
                "runtimeDiagnostics": None,
                "providerSnapshot": None,
                "stepExecutionManifestRef": None,
                "stepExecutionManifestRefs": [],
            },
            "workload": None,
            "lastError": None,
        }
    ]

def test_progress_summary_prefers_active_step_title_and_counts_statuses() -> None:
    updated_at = datetime(2026, 4, 7, 12, 5, tzinfo=UTC)
    progress = build_progress_summary(
        [
            {
                "logicalStepId": "step-1",
                "status": "completed",
                "title": "Prepare workspace",
                "updatedAt": updated_at.isoformat(),
            },
            {
                "logicalStepId": "step-2",
                "status": "executing",
                "title": "Run tests",
                "updatedAt": updated_at.isoformat(),
            },
            {
                "logicalStepId": "step-3",
                "status": "pending",
                "title": "Publish summary",
                "updatedAt": updated_at.isoformat(),
            },
        ],
        updated_at=updated_at,
    )

    assert progress == {
        "total": 3,
        "pending": 1,
        "ready": 0,
        "executing": 1,
        "awaitingExternal": 0,
        "reviewing": 0,
        "completed": 1,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
        "currentStepTitle": "Run tests",
        "updatedAt": updated_at.isoformat(),
    }

def test_progress_summary_normalizes_legacy_replayed_statuses() -> None:
    updated_at = datetime(2026, 4, 7, 12, 5, tzinfo=UTC)
    progress = build_progress_summary(
        [
            {
                "logicalStepId": "step-1",
                "status": "succeeded",
                "title": "Prepare workspace",
                "updatedAt": updated_at.isoformat(),
            },
            {
                "logicalStepId": "step-2",
                "status": "running",
                "title": "Run tests",
                "updatedAt": updated_at.isoformat(),
            },
        ],
        updated_at=updated_at,
    )

    assert progress["executing"] == 1
    assert progress["completed"] == 1
    assert progress["currentStepTitle"] == "Run tests"

def test_progress_summary_does_not_treat_ready_step_as_current() -> None:
    updated_at = datetime(2026, 4, 7, 12, 6, tzinfo=UTC)
    progress = build_progress_summary(
        [
            {
                "logicalStepId": "step-1",
                "status": "ready",
                "title": "Move Jira issue to In Progress",
                "updatedAt": updated_at.isoformat(),
            },
            {
                "logicalStepId": "step-2",
                "status": "pending",
                "title": "Run implementation",
                "updatedAt": updated_at.isoformat(),
            },
        ],
        updated_at=updated_at,
    )

    assert progress["ready"] == 1
    assert progress["pending"] == 1
    assert progress["currentStepTitle"] is None

def test_contract_models_accept_representative_rows_and_progress() -> None:
    updated_at = datetime(2026, 4, 7, 12, 10, tzinfo=UTC)

    progress = ExecutionProgressModel.model_validate(
        {
            "total": 3,
            "pending": 0,
            "ready": 0,
            "executing": 0,
            "awaitingExternal": 1,
            "reviewing": 1,
            "completed": 1,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": "Review patch",
            "updatedAt": updated_at.isoformat(),
        }
    )
    retrying_row = StepLedgerRowModel.model_validate(
        {
            "logicalStepId": "run-tests",
            "order": 2,
            "title": "Run tests",
            "tool": {"type": "skill", "name": "repo.run_tests"},
            "dependsOn": ["prepare-workspace"],
            "status": "executing",
            "waitingReason": None,
            "attentionRequired": False,
            "attempt": 2,
            "startedAt": updated_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
            "summary": "Retrying after a bounded failure",
            "checks": [],
            "refs": {"childWorkflowId": None, "childRunId": None, "agentRunId": None},
            "artifacts": {
                "outputSummary": None,
                "outputPrimary": None,
                "runtimeStdout": None,
                "runtimeStderr": None,
                "runtimeMergedLogs": None,
                "runtimeDiagnostics": None,
                "providerSnapshot": None,
            },
            "lastError": "pytest previously failed",
        }
    )
    child_runtime_row = StepLedgerRowModel.model_validate(
        {
            "logicalStepId": "delegate-agent",
            "order": 3,
            "title": "Delegate agent run",
            "tool": {"type": "agent_runtime", "name": "codex"},
            "dependsOn": ["run-tests"],
            "status": "awaiting_external",
            "waitingReason": "Awaiting child workflow progress",
            "attentionRequired": False,
            "attempt": 1,
            "startedAt": updated_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
            "summary": "Child workflow launched",
            "checks": [],
            "refs": {
                "childWorkflowId": "wf-child",
                "childRunId": "run-child",
                "agentRunId": "agent-run-1",
            },
            "artifacts": {
                "outputSummary": None,
                "outputPrimary": None,
                "runtimeStdout": None,
                "runtimeStderr": None,
                "runtimeMergedLogs": None,
                "runtimeDiagnostics": None,
                "providerSnapshot": None,
            },
            "lastError": None,
        }
    )
    reviewed_row = StepLedgerRowModel.model_validate(
        {
            "logicalStepId": "review-patch",
            "order": 4,
            "title": "Review patch",
            "tool": {"type": "skill", "name": "repo.review_patch"},
            "dependsOn": ["delegate-agent"],
            "status": "reviewing",
            "waitingReason": "Awaiting structured review result",
            "attentionRequired": False,
            "attempt": 1,
            "startedAt": updated_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
            "summary": "Structured review in progress",
            "checks": [
                {
                    "kind": "approval_policy",
                    "status": "pending",
                    "summary": None,
                    "retryCount": 0,
                    "artifactRef": None,
                }
            ],
            "refs": {"childWorkflowId": None, "childRunId": None, "agentRunId": None},
            "artifacts": {
                "outputSummary": None,
                "outputPrimary": None,
                "runtimeStdout": None,
                "runtimeStderr": None,
                "runtimeMergedLogs": None,
                "runtimeDiagnostics": None,
                "providerSnapshot": None,
            },
            "lastError": None,
        }
    )
    snapshot = StepLedgerSnapshotModel.model_validate(
        {
            "workflowId": "wf-1",
            "runId": "run-1",
            "runScope": "latest",
            "steps": [
                retrying_row.model_dump(by_alias=True),
                child_runtime_row.model_dump(by_alias=True),
                reviewed_row.model_dump(by_alias=True),
            ],
        }
    )

    assert progress.current_step_title == "Review patch"
    assert retrying_row.execution_ordinal == 2
    assert child_runtime_row.refs.agent_run_id == "agent-run-1"
    assert reviewed_row.checks == [
        StepLedgerCheckModel(
            kind="approval_policy",
            status="pending",
            summary=None,
            retry_count=0,
            artifact_ref=None,
        )
    ]
    assert snapshot.run_scope == "latest"

def test_row_defaults_remain_bounded_and_structured() -> None:
    refs = StepLedgerRefsModel()
    artifacts = StepLedgerArtifactsModel()

    assert refs.model_dump(by_alias=True) == {
        "childWorkflowId": None,
        "childRunId": None,
        "agentRunId": None,
        "latestStepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
        "latestStepExecutionCheckpointRef": None,
        "stepExecutionCheckpointRefs": [],
        "checkpointRefsByBoundary": {},
    }
    assert artifacts.model_dump(by_alias=True) == {
        "outputSummary": None,
        "outputPrimary": None,
        "runtimeStdout": None,
        "runtimeStderr": None,
        "runtimeMergedLogs": None,
        "runtimeDiagnostics": None,
        "providerSnapshot": None,
        "stepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
    }


def test_mark_step_checkpoint_evidence_marks_completed_step_eligible() -> None:
    updated_at = datetime(2026, 4, 7, 12, 15, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "implement", "inputs": {"title": "Implement"}},
        ],
        dependency_map={"implement": []},
        updated_at=updated_at,
    )
    update_step_row(
        rows,
        "implement",
        updated_at=updated_at,
        status="completed",
        artifacts={"outputPrimary": "artifact://output"},
    )

    mark_step_checkpoint_evidence(
        rows,
        "implement",
        updated_at=updated_at,
        state_checkpoint_ref="artifact://checkpoint/implement/1",
        workspace_checkpoint_ref="artifact://workspace/implement/1",
        step_checkpoint_ref="artifact://step/implement/1",
        boundary="after_execution",
    )

    row = StepLedgerRowModel.model_validate(rows[0])
    assert row.state_checkpoint_ref == "artifact://checkpoint/implement/1"
    assert row.workspace_checkpoint_ref == "artifact://workspace/implement/1"
    assert row.step_checkpoint_ref == "artifact://step/implement/1"
    assert row.refs.latest_step_execution_checkpoint_ref == (
        "artifact://step/implement/1"
    )
    assert row.refs.step_execution_checkpoint_refs == [
        "artifact://step/implement/1"
    ]
    assert row.refs.checkpoint_refs_by_boundary == {
        "after_execution": "artifact://step/implement/1"
    }
    assert row.resume_preservation is not None
    assert row.resume_preservation.eligible is True
    assert row.resume_preservation.reason == "complete"


def test_mark_step_checkpoint_evidence_records_bounded_ineligible_reason() -> None:
    updated_at = datetime(2026, 4, 7, 12, 16, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {"id": "plan", "inputs": {"title": "Plan"}},
        ],
        dependency_map={"plan": []},
        updated_at=updated_at,
    )
    update_step_row(
        rows,
        "plan",
        updated_at=updated_at,
        status="completed",
    )

    mark_step_checkpoint_evidence(rows, "plan", updated_at=updated_at)

    row = StepLedgerRowModel.model_validate(rows[0])
    assert row.resume_preservation is not None
    assert row.resume_preservation.eligible is False
    assert row.resume_preservation.reason == "missing_output_refs"
    assert "output ref" in (row.resume_preservation.message or "")

def test_update_step_row_allows_explicit_last_error_clear() -> None:
    updated_at = datetime(2026, 4, 7, 12, 12, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.test"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=updated_at,
    )

    update_step_row(
        rows,
        "run-tests",
        updated_at=updated_at,
        status="failed",
        last_error="pytest failed",
    )
    update_step_row(
        rows,
        "run-tests",
        updated_at=updated_at,
        status="completed",
        last_error=None,
    )

    assert rows[0]["status"] == "completed"
    assert rows[0]["endedAt"] == updated_at.isoformat()

def test_refresh_ready_steps_treats_legacy_succeeded_dependency_as_ready() -> None:
    updated_at = datetime(2026, 4, 7, 12, 12, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "prepare",
                "tool": {"type": "skill", "name": "repo.prepare"},
                "inputs": {"title": "Prepare"},
            },
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.test"},
                "inputs": {"title": "Run tests"},
            },
        ],
        dependency_map={"prepare": [], "run-tests": ["prepare"]},
        updated_at=updated_at,
    )
    rows[0]["status"] = "succeeded"

    refresh_ready_steps(rows, updated_at=updated_at)

    assert rows[1]["status"] == "ready"

def test_upsert_step_check_updates_existing_review_state() -> None:
    updated_at = datetime(2026, 4, 7, 12, 15, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "review-patch",
                "tool": {"type": "skill", "name": "repo.review_patch"},
                "inputs": {"title": "Review patch"},
            }
        ],
        dependency_map={"review-patch": []},
        updated_at=updated_at,
    )

    upsert_step_check(
        rows,
        "review-patch",
        kind="approval_policy",
        status="pending",
        summary="Structured review in progress",
        retry_count=0,
        artifact_ref=None,
    )
    upsert_step_check(
        rows,
        "review-patch",
        kind="approval_policy",
        status="failed",
        summary="Reviewer requested another retry",
        retry_count=1,
        artifact_ref="art_review_1",
    )

    assert rows[0]["checks"] == [
        {
            "kind": "approval_policy",
            "status": "failed",
            "summary": "Reviewer requested another retry",
            "retryCount": 1,
            "artifactRef": "art_review_1",
        }
    ]
    assert rows[0]["lastError"] is None

def test_update_step_row_merges_structured_refs_and_artifacts() -> None:
    updated_at = datetime(2026, 4, 7, 12, 15, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=updated_at,
    )

    row = update_step_row(
        rows,
        "delegate-agent",
        updated_at=updated_at,
        refs={
            "childWorkflowId": "wf-child-1",
            "childRunId": "run-child-1",
            "agentRunId": "550e8400-e29b-41d4-a716-446655440000",
        },
        artifacts={
            "outputSummary": "art_summary_1",
            "outputPrimary": "art_primary_1",
            "runtimeStdout": "art_stdout_1",
            "runtimeDiagnostics": "art_diag_1",
        },
    )

    assert row["refs"] == {
        "childWorkflowId": "wf-child-1",
        "childRunId": "run-child-1",
        "agentRunId": "550e8400-e29b-41d4-a716-446655440000",
        "latestStepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
        "latestStepExecutionCheckpointRef": None,
        "stepExecutionCheckpointRefs": [],
        "checkpointRefsByBoundary": {},
    }
    assert row["artifacts"] == {
        "outputSummary": "art_summary_1",
        "outputPrimary": "art_primary_1",
        "runtimeStdout": "art_stdout_1",
        "runtimeStderr": None,
        "runtimeMergedLogs": None,
        "runtimeDiagnostics": "art_diag_1",
        "providerSnapshot": None,
        "stepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
    }


def test_mark_step_execution_manifest_evidence_tracks_latest_and_history() -> None:
    updated_at = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "implement",
                "tool": {"type": "skill", "name": "codex"},
                "inputs": {"title": "Implement"},
            }
        ],
        dependency_map={"implement": []},
        updated_at=updated_at,
    )

    update_step_row(
        rows,
        "implement",
        updated_at=updated_at,
        increment_attempt=True,
        status="executing",
    )
    mark_step_execution_manifest_evidence(
        rows,
        "implement",
        updated_at=updated_at,
        step_execution_manifest_ref="artifact://attempt-1",
    )
    assert rows[0]["artifacts"]["stepExecutionManifestRef"] == "artifact://attempt-1"
    update_step_row(
        rows,
        "implement",
        updated_at=updated_at,
        increment_attempt=True,
        status="executing",
    )
    second = mark_step_execution_manifest_evidence(
        rows,
        "implement",
        updated_at=updated_at,
        step_execution_manifest_ref="artifact://attempt-2",
    )

    assert second["artifacts"]["stepExecutionManifestRef"] == "artifact://attempt-2"
    assert second["artifacts"]["stepExecutionManifestRefs"] == [
        "artifact://attempt-1",
        "artifact://attempt-2",
    ]
    assert "schemaVersion" not in second["artifacts"]
