"""Unit tests for Spec Kit workflow serializers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from moonmind.workflows.speckit_celery import models
from moonmind.workflows.speckit_celery.serializers import (
    serialize_run,
    serialize_task_state,
    serialize_task_summary,
)


def _make_state(
    *,
    workflow_run_id,
    task_name: str,
    status: models.SpecWorkflowTaskStatus,
    attempt: int = 1,
    payload: dict[str, object] | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> models.SpecWorkflowTaskState:
    created = created_at or datetime.now(UTC)
    return models.SpecWorkflowTaskState(
        id=uuid4(),
        workflow_run_id=workflow_run_id,
        task_name=task_name,
        status=status,
        attempt=attempt,
        payload=payload,
        started_at=started_at,
        finished_at=finished_at,
        created_at=created,
        updated_at=updated_at or created,
    )


def test_serialize_task_state_includes_temporal_fields():
    """Task state serialization should surface temporal metadata."""

    run_id = uuid4()
    now = datetime.now(UTC)
    state = _make_state(
        workflow_run_id=run_id,
        task_name="discover_next_phase",
        status=models.SpecWorkflowTaskStatus.RUNNING,
        attempt=1,
        payload={"status": "running"},
        started_at=now,
        created_at=now,
        updated_at=now + timedelta(seconds=5),
    )

    serialized = serialize_task_state(state)

    assert serialized["taskName"] == "discover_next_phase"
    assert serialized["status"] == models.SpecWorkflowTaskStatus.RUNNING.value
    assert serialized["payload"]["status"] == "running"
    assert serialized["createdAt"].endswith("+00:00")
    assert serialized["updatedAt"].endswith("+00:00")


def test_serialize_task_summary_collapses_attempts():
    """Only the latest attempt per task should be returned in summaries."""

    run_id = uuid4()
    base = datetime(2025, 1, 1, tzinfo=UTC)
    states = [
        _make_state(
            workflow_run_id=run_id,
            task_name="discover_next_phase",
            status=models.SpecWorkflowTaskStatus.QUEUED,
            created_at=base,
            updated_at=base,
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="discover_next_phase",
            status=models.SpecWorkflowTaskStatus.SUCCEEDED,
            created_at=base,
            updated_at=base + timedelta(minutes=1),
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="submit_codex_job",
            status=models.SpecWorkflowTaskStatus.RUNNING,
            created_at=base,
            updated_at=base + timedelta(minutes=2),
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="apply_and_publish",
            status=models.SpecWorkflowTaskStatus.FAILED,
            attempt=1,
            created_at=base,
            updated_at=base + timedelta(minutes=3),
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="apply_and_publish",
            status=models.SpecWorkflowTaskStatus.RUNNING,
            attempt=2,
            created_at=base,
            updated_at=base + timedelta(minutes=4),
        ),
    ]

    summary = serialize_task_summary(states)

    assert [item["taskName"] for item in summary] == [
        "discover_next_phase",
        "submit_codex_job",
        "apply_and_publish",
    ]

    apply_entry = next(
        item for item in summary if item["taskName"] == "apply_and_publish"
    )
    assert apply_entry["attempt"] == 2
    assert apply_entry["status"] == models.SpecWorkflowTaskStatus.RUNNING.value


def test_serialize_run_includes_summary_and_paths():
    """Run serialization should include task summary and artifact paths."""

    run_id = uuid4()
    now = datetime.now(UTC)
    run = models.SpecWorkflowRun(
        id=run_id,
        feature_key="001-celery-chain-workflow",
        celery_chain_id="celery-123",
        status=models.SpecWorkflowRunStatus.RUNNING,
        phase=models.SpecWorkflowRunPhase.SUBMIT,
        branch_name=None,
        pr_url=None,
        codex_task_id="codex-42",
        codex_logs_path="/tmp/logs.jsonl",
        codex_patch_path=None,
        artifacts_path="/tmp/artifacts",
        created_by=None,
        started_at=now,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )

    state = _make_state(
        workflow_run_id=run_id,
        task_name="discover_next_phase",
        status=models.SpecWorkflowTaskStatus.SUCCEEDED,
        payload={"status": "succeeded"},
        started_at=now,
        finished_at=now + timedelta(minutes=1),
        created_at=now,
        updated_at=now + timedelta(minutes=1),
    )
    run.task_states = [state]

    serialized_full = serialize_run(run, include_tasks=True)
    assert serialized_full["codexLogsPath"] == "/tmp/logs.jsonl"
    assert serialized_full["taskSummary"]
    assert serialized_full["tasks"][0]["taskName"] == "discover_next_phase"

    serialized_min = serialize_run(run, include_tasks=False, task_states=[state])
    assert "tasks" not in serialized_min
    assert serialized_min["taskSummary"]
    assert serialized_min["taskSummary"][0]["taskName"] == "discover_next_phase"
