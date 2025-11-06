"""Serialization helpers for Spec Kit Celery workflow entities."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TypedDict

from moonmind.workflows.speckit_celery import models

_TASK_ORDER: tuple[str, ...] = (
    "discover_next_phase",
    "submit_codex_job",
    "apply_and_publish",
)


class SerializedTaskState(TypedDict, total=False):
    """Structure returned to API consumers for a task state."""

    id: str
    taskName: str
    status: str
    attempt: int
    payload: dict[str, Any]
    startedAt: str | None
    finishedAt: str | None
    createdAt: str | None
    updatedAt: str | None


class SerializedTaskSummary(TypedDict, total=False):
    """Condensed representation of the latest task states."""

    taskName: str
    status: str
    attempt: int
    startedAt: str | None
    finishedAt: str | None
    updatedAt: str | None


class SerializedArtifact(TypedDict):
    """Structure returned to API consumers for workflow artifacts."""

    id: str
    artifactType: str
    path: str
    createdAt: str | None


class SerializedCredentialAudit(TypedDict, total=False):
    """Structure returned to API consumers for credential audits."""

    codexStatus: str
    githubStatus: str
    checkedAt: str | None
    notes: str | None


class SerializedRun(TypedDict, total=False):
    """Structure returned to API consumers for workflow runs."""

    id: str
    featureKey: str
    status: str
    phase: str
    branchName: str | None
    prUrl: str | None
    codexTaskId: str | None
    codexQueue: str | None
    codexVolume: str | None
    codexPreflightStatus: str | None
    codexPreflightMessage: str | None
    codexLogsPath: str | None
    codexPatchPath: str | None
    celeryChainId: str | None
    createdBy: str | None
    startedAt: str | None
    finishedAt: str | None
    artifactsPath: str | None
    createdAt: str | None
    updatedAt: str | None
    tasks: list[SerializedTaskState]
    taskSummary: list[SerializedTaskSummary]
    artifacts: list[SerializedArtifact]
    credentialAudit: SerializedCredentialAudit | None


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.isoformat()


def serialize_task_state(state: models.SpecWorkflowTaskState) -> SerializedTaskState:
    """Convert a task state model into a serializable dictionary."""

    return SerializedTaskState(
        id=str(state.id),
        taskName=state.task_name,
        status=state.status.value,
        attempt=state.attempt,
        payload=dict(state.payload or {}),
        startedAt=_serialize_datetime(state.started_at),
        finishedAt=_serialize_datetime(state.finished_at),
        createdAt=_serialize_datetime(state.created_at),
        updatedAt=_serialize_datetime(state.updated_at),
    )


def _task_state_latest_timestamp(
    state: models.SpecWorkflowTaskState,
) -> datetime | None:
    """Return the most recent timestamp associated with a task state."""

    return state.updated_at or state.finished_at or state.started_at or state.created_at


def _latest_task_states(
    states: Iterable[models.SpecWorkflowTaskState],
) -> list[models.SpecWorkflowTaskState]:
    """Collapse multiple attempts down to the most recent per task."""

    latest: dict[str, models.SpecWorkflowTaskState] = {}
    for state in states:
        existing = latest.get(state.task_name)
        if existing is None:
            latest[state.task_name] = state
            continue

        if state.attempt > existing.attempt:
            latest[state.task_name] = state
            continue

        if state.attempt == existing.attempt:
            current_stamp = _task_state_latest_timestamp(state)
            existing_stamp = _task_state_latest_timestamp(existing)
            if existing_stamp is None or (
                current_stamp is not None and current_stamp > existing_stamp
            ):
                latest[state.task_name] = state

    order_index = {name: idx for idx, name in enumerate(_TASK_ORDER)}
    return sorted(
        latest.values(),
        key=lambda item: (
            order_index.get(item.task_name, len(_TASK_ORDER)),
            item.task_name,
        ),
    )


def serialize_task_summary(
    states: Iterable[models.SpecWorkflowTaskState],
) -> list[SerializedTaskSummary]:
    """Serialize only the latest attempt for each workflow task."""

    collapsed = _latest_task_states(states)
    return [
        SerializedTaskSummary(
            taskName=state.task_name,
            status=state.status.value,
            attempt=state.attempt,
            startedAt=_serialize_datetime(state.started_at),
            finishedAt=_serialize_datetime(state.finished_at),
            updatedAt=_serialize_datetime(state.updated_at),
        )
        for state in collapsed
    ]


def serialize_artifact(artifact: models.WorkflowArtifact) -> SerializedArtifact:
    """Convert an artifact record into API response structure."""

    return SerializedArtifact(
        id=str(artifact.id),
        artifactType=artifact.artifact_type.value,
        path=artifact.path,
        createdAt=_serialize_datetime(artifact.created_at),
    )


def _serialize_credential_audit(
    audit: models.WorkflowCredentialAudit | None,
) -> SerializedCredentialAudit | None:
    if audit is None:
        return None
    return SerializedCredentialAudit(
        codexStatus=audit.codex_status.value,
        githubStatus=audit.github_status.value,
        checkedAt=_serialize_datetime(audit.checked_at),
        notes=audit.notes,
    )


def serialize_run(
    run: models.SpecWorkflowRun,
    *,
    include_tasks: bool = False,
    include_artifacts: bool = False,
    include_credential_audit: bool = False,
    task_states: Iterable[models.SpecWorkflowTaskState] | None = None,
) -> SerializedRun:
    """Serialize a workflow run and optionally its related entities."""

    data: SerializedRun = SerializedRun(
        id=str(run.id),
        featureKey=run.feature_key,
        status=run.status.value,
        phase=run.phase.value,
        branchName=run.branch_name,
        prUrl=run.pr_url,
        codexTaskId=run.codex_task_id,
        codexQueue=run.codex_queue,
        codexVolume=run.codex_volume,
        codexPreflightStatus=getattr(run.codex_preflight_status, "value", None),
        codexPreflightMessage=run.codex_preflight_message,
        codexLogsPath=run.codex_logs_path,
        codexPatchPath=run.codex_patch_path,
        celeryChainId=run.celery_chain_id,
        createdBy=str(run.created_by) if run.created_by else None,
        startedAt=_serialize_datetime(run.started_at),
        finishedAt=_serialize_datetime(run.finished_at),
        artifactsPath=run.artifacts_path,
        createdAt=_serialize_datetime(run.created_at),
        updatedAt=_serialize_datetime(run.updated_at),
    )

    task_state_list: list[models.SpecWorkflowTaskState] = []
    if task_states is not None:
        task_state_list = list(task_states)
    elif "task_states" in run.__dict__:
        task_state_list = list(run.task_states)

    if include_tasks:
        data["tasks"] = [serialize_task_state(task) for task in task_state_list]

    data["taskSummary"] = serialize_task_summary(task_state_list)

    if include_artifacts:
        artifacts: Iterable[models.WorkflowArtifact] = (
            run.artifacts if "artifacts" in run.__dict__ else []
        )
        data["artifacts"] = [serialize_artifact(item) for item in artifacts]

    if include_credential_audit:
        audit = run.credential_audit if "credential_audit" in run.__dict__ else None
        data["credentialAudit"] = _serialize_credential_audit(audit)

    return data


__all__ = [
    "serialize_run",
    "serialize_task_state",
    "serialize_artifact",
]
