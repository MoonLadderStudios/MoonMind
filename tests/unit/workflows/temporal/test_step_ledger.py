from __future__ import annotations

from datetime import UTC, datetime

from moonmind.schemas.temporal_models import (
    ExecutionProgressModel,
    StepLedgerArtifactsModel,
    StepLedgerCheckModel,
    StepLedgerRefsModel,
    StepLedgerRowModel,
    StepLedgerSnapshotModel,
)
from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    build_progress_summary,
    mark_step_execution_manifest_evidence,
    mark_step_checkpoint_evidence,
    upsert_step_check,
    update_step_row,
)

def test_build_initial_step_rows_uses_plan_metadata_and_dependencies() -> None:
    updated_at = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "step-1",
                "tool": {"type": "skill", "name": "repo.setup", "version": "1"},
                "inputs": {"title": "Prepare workspace"},
            },
            {
                "id": "step-2",
                "tool": {"type": "skill", "name": "repo.test", "version": "1"},
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
    assert rows[0]["executionOrdinal"] == 0
    assert rows[0]["dependsOn"] == []
    assert rows[1]["title"] == "Run tests"
    assert rows[1]["status"] == "pending"
    assert rows[1]["dependsOn"] == ["step-1"]
    assert rows[1]["tool"] == {"type": "skill", "name": "repo.test", "version": "1"}
    assert rows[0]["refs"]["latestExecutionManifestRef"] is None
    assert rows[0]["refs"]["executionManifestRefs"] == []


def test_step_ledger_refs_track_latest_and_historical_attempt_manifests() -> None:
    refs = StepLedgerRefsModel.model_validate(
        {
            "childWorkflowId": "child-1",
            "childRunId": "run-child",
            "taskRunId": "task-run",
            "latestExecutionManifestRef": "artifact-attempt-2",
            "executionManifestRefs": ["artifact-attempt-1", "artifact-attempt-2"],
        }
    )

    assert refs.latest_execution_manifest_ref == "artifact-attempt-2"
    assert refs.execution_manifest_refs == [
        "artifact-attempt-1",
        "artifact-attempt-2",
    ]
    assert refs.model_dump(by_alias=True)["latestExecutionManifestRef"] == (
        "artifact-attempt-2"
    )

def test_build_initial_step_rows_skips_blank_node_ids() -> None:
    updated_at = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "",
                "tool": {"type": "skill", "name": "repo.invalid", "version": "1"},
            },
            {
                "id": "step-2",
                "tool": {"type": "skill", "name": "repo.test", "version": "1"},
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
            "tool": {"type": "skill", "name": "repo.test", "version": "1"},
            "dependsOn": [],
            "status": "ready",
            "waitingReason": None,
            "attentionRequired": False,
            "executionOrdinal": 0,
            "startedAt": None,
            "updatedAt": updated_at.isoformat(),
            "summary": None,
            "checks": [],
            "refs": {
                "childWorkflowId": None,
                "childRunId": None,
                "taskRunId": None,
                "latestExecutionManifestRef": None,
                "executionManifestRefs": [],
            },
            "artifacts": {
                "outputSummary": None,
                "outputPrimary": None,
                "runtimeStdout": None,
                "runtimeStderr": None,
                "runtimeMergedLogs": None,
                "runtimeDiagnostics": None,
                "providerSnapshot": None,
                "executionManifestRef": None,
                "executionManifestRefs": [],
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
        "running": 1,
        "awaitingExternal": 0,
        "reviewing": 0,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
        "currentStepTitle": "Run tests",
        "updatedAt": updated_at.isoformat(),
    }

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
            "running": 0,
            "awaitingExternal": 1,
            "reviewing": 1,
            "succeeded": 1,
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
            "tool": {"type": "skill", "name": "repo.run_tests", "version": "1"},
            "dependsOn": ["prepare-workspace"],
            "status": "running",
            "waitingReason": None,
            "attentionRequired": False,
            "executionOrdinal": 2,
            "startedAt": updated_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
            "summary": "Retrying after a bounded failure",
            "checks": [],
            "refs": {"childWorkflowId": None, "childRunId": None, "taskRunId": None},
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
            "tool": {"type": "agent_runtime", "name": "codex", "version": ""},
            "dependsOn": ["run-tests"],
            "status": "awaiting_external",
            "waitingReason": "Awaiting child workflow progress",
            "attentionRequired": False,
            "executionOrdinal": 1,
            "startedAt": updated_at.isoformat(),
            "updatedAt": updated_at.isoformat(),
            "summary": "Child workflow launched",
            "checks": [],
            "refs": {
                "childWorkflowId": "wf-child",
                "childRunId": "run-child",
                "taskRunId": "task-run-1",
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
            "tool": {"type": "skill", "name": "repo.review_patch", "version": "1"},
            "dependsOn": ["delegate-agent"],
            "status": "reviewing",
            "waitingReason": "Awaiting structured review result",
            "attentionRequired": False,
            "executionOrdinal": 1,
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
            "refs": {"childWorkflowId": None, "childRunId": None, "taskRunId": None},
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
    assert child_runtime_row.refs.task_run_id == "task-run-1"
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
        "taskRunId": None,
        "latestExecutionManifestRef": None,
        "executionManifestRefs": [],
    }
    assert artifacts.model_dump(by_alias=True) == {
        "outputSummary": None,
        "outputPrimary": None,
        "runtimeStdout": None,
        "runtimeStderr": None,
        "runtimeMergedLogs": None,
        "runtimeDiagnostics": None,
        "providerSnapshot": None,
        "executionManifestRef": None,
        "executionManifestRefs": [],
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
        status="succeeded",
        artifacts={"outputPrimary": "artifact://output"},
    )

    mark_step_checkpoint_evidence(
        rows,
        "implement",
        updated_at=updated_at,
        state_checkpoint_ref="artifact://checkpoint/implement/1",
    )

    row = StepLedgerRowModel.model_validate(rows[0])
    assert row.state_checkpoint_ref == "artifact://checkpoint/implement/1"
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
        status="succeeded",
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
                "tool": {"type": "skill", "name": "repo.test", "version": "1"},
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
        status="succeeded",
        last_error=None,
    )

    assert rows[0]["status"] == "succeeded"

def test_upsert_step_check_updates_existing_review_state() -> None:
    updated_at = datetime(2026, 4, 7, 12, 15, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "review-patch",
                "tool": {"type": "skill", "name": "repo.review_patch", "version": "1"},
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
                "tool": {"type": "agent_runtime", "name": "codex", "version": ""},
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
            "taskRunId": "550e8400-e29b-41d4-a716-446655440000",
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
        "taskRunId": "550e8400-e29b-41d4-a716-446655440000",
        "latestExecutionManifestRef": None,
        "executionManifestRefs": [],
    }
    assert row["artifacts"] == {
        "outputSummary": "art_summary_1",
        "outputPrimary": "art_primary_1",
        "runtimeStdout": "art_stdout_1",
        "runtimeStderr": None,
        "runtimeMergedLogs": None,
        "runtimeDiagnostics": "art_diag_1",
        "providerSnapshot": None,
        "executionManifestRef": None,
        "executionManifestRefs": [],
    }


def test_mark_step_execution_manifest_evidence_tracks_latest_and_history() -> None:
    updated_at = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    rows = build_initial_step_rows(
        ordered_nodes=[
            {
                "id": "implement",
                "tool": {"type": "skill", "name": "codex", "version": "1"},
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
        status="running",
    )
    mark_step_execution_manifest_evidence(
        rows,
        "implement",
        updated_at=updated_at,
        execution_manifest_ref="artifact://attempt-1",
    )
    assert rows[0]["artifacts"]["executionManifestRef"] == "artifact://attempt-1"
    update_step_row(
        rows,
        "implement",
        updated_at=updated_at,
        increment_attempt=True,
        status="running",
    )
    second = mark_step_execution_manifest_evidence(
        rows,
        "implement",
        updated_at=updated_at,
        execution_manifest_ref="artifact://attempt-2",
    )

    assert second["artifacts"]["executionManifestRef"] == "artifact://attempt-2"
    assert second["artifacts"]["executionManifestRefs"] == [
        "artifact://attempt-1",
        "artifact://attempt-2",
    ]
    assert "schemaVersion" not in second["artifacts"]
