"""Pure helpers for Step Attempt identity, manifests, and idempotency."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from moonmind.schemas.step_attempt_models import (
    AttemptReason,
    AttemptStatus,
    StepAttemptIdentityModel,
    StepAttemptManifestModel,
)


def step_attempt_id(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    attempt: int,
) -> str:
    return StepAttemptIdentityModel(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId=logical_step_id,
        attempt=attempt,
    ).step_attempt_id


def step_attempt_operation_idempotency_key(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    attempt: int,
    operation: str,
) -> str:
    operation_id = str(operation or "").strip()
    if not operation_id:
        raise ValueError("operation must be a non-empty string")
    identity = step_attempt_id(
        workflow_id=workflow_id,
        run_id=run_id,
        logical_step_id=logical_step_id,
        attempt=attempt,
    )
    return f"{identity}:{operation_id}"


def build_step_attempt_manifest_payload(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    attempt: int,
    reason: AttemptReason,
    status: AttemptStatus,
    updated_at: datetime,
    started_at: datetime | None = None,
    summary: str | None = None,
    lineage: Mapping[str, Any] | None = None,
    input_refs: Sequence[str] = (),
    context: Mapping[str, Any] | None = None,
    workspace: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    outputs: Mapping[str, Any] | None = None,
    checks: Sequence[Mapping[str, Any]] = (),
    side_effects: Mapping[str, Any] | None = None,
    dependency_effects: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bounded_outputs = dict(outputs or {})
    if summary is not None:
        bounded_outputs.setdefault("summary", summary[:500])
    prepared_refs = [str(ref).strip() for ref in input_refs if str(ref).strip()]
    input_payload: dict[str, Any] = {}
    if prepared_refs:
        input_payload["preparedInputRefs"] = prepared_refs
    manifest = StepAttemptManifestModel(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId=logical_step_id,
        attempt=attempt,
        lineage=dict(lineage) if lineage is not None else None,
        reason=reason,
        status=status,
        startedAt=started_at or updated_at,
        updatedAt=updated_at,
        input=input_payload,
        context=dict(context or {}),
        workspace=dict(workspace or {}),
        execution=dict(execution or {}),
        outputs=bounded_outputs,
        checks=[dict(check) for check in checks],
        sideEffects=dict(side_effects or {}),
        dependencyEffects=dict(dependency_effects or {}),
        budget=dict(budget or {}),
    )
    return manifest.model_dump(by_alias=True, mode="json")
