"""Serialization helpers for Spec Kit Celery workflow entities."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TypedDict

from sqlalchemy.orm import attributes

from moonmind.workflows.speckit_celery import models


class SerializedTaskState(TypedDict, total=False):
    """Structure returned to API consumers for a task state."""

    id: str
    taskName: str
    status: str
    attempt: int
    payload: dict[str, Any]
    startedAt: str | None
    finishedAt: str | None


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
    celeryChainId: str | None
    createdBy: str | None
    startedAt: str | None
    finishedAt: str | None
    artifactsPath: str | None
    createdAt: str | None
    updatedAt: str | None
    tasks: list[SerializedTaskState]
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
    )


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
        celeryChainId=run.celery_chain_id,
        createdBy=str(run.created_by) if run.created_by else None,
        startedAt=_serialize_datetime(run.started_at),
        finishedAt=_serialize_datetime(run.finished_at),
        artifactsPath=run.artifacts_path,
        createdAt=_serialize_datetime(run.created_at),
        updatedAt=_serialize_datetime(run.updated_at),
    )

    if include_tasks:
        tasks: Iterable[models.SpecWorkflowTaskState] = (
            run.task_states
            if attributes.is_attribute_loaded(run, "task_states")
            else []
        )
        data["tasks"] = [serialize_task_state(task) for task in tasks]

    if include_artifacts:
        artifacts: Iterable[models.WorkflowArtifact] = (
            run.artifacts if attributes.is_attribute_loaded(run, "artifacts") else []
        )
        data["artifacts"] = [serialize_artifact(item) for item in artifacts]

    if include_credential_audit:
        audit = (
            run.credential_audit
            if attributes.is_attribute_loaded(run, "credential_audit")
            else None
        )
        data["credentialAudit"] = _serialize_credential_audit(audit)

    return data


__all__ = [
    "serialize_run",
    "serialize_task_state",
    "serialize_artifact",
]
