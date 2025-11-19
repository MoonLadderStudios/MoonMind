"""Serialization helpers for orchestrator API responses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional
from uuid import UUID

from api_service.db import models as db_models
from moonmind.schemas.workflow_models import (
    OrchestratorActionPlanModel,
    OrchestratorPlanStepDefinition,
    OrchestratorPlanStepStateModel,
    OrchestratorRunArtifactModel,
    OrchestratorRunDetailModel,
    OrchestratorRunListResponse,
    OrchestratorRunSummaryModel,
)
from moonmind.workflows.speckit_celery import models as workflow_models

if TYPE_CHECKING:  # pragma: no cover - import used for type checking only
    from moonmind.schemas.workflow_models import OrchestratorApprovalStatus


def _resolve_approval_state(
    run: db_models.OrchestratorRun,
) -> tuple[bool, "OrchestratorApprovalStatus"]:
    from moonmind.schemas.workflow_models import OrchestratorApprovalStatus

    if run.approval_gate_id is None:
        return False, OrchestratorApprovalStatus.NOT_REQUIRED
    if run.approval_token:
        return True, OrchestratorApprovalStatus.GRANTED
    return True, OrchestratorApprovalStatus.AWAITING


def _convert_plan_definition(
    plan: Optional[db_models.OrchestratorActionPlan],
) -> Optional[OrchestratorActionPlanModel]:
    if plan is None:
        return None
    step_defs: list[OrchestratorPlanStepDefinition] = []
    for entry in plan.steps or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name is None:
            continue
        parameters = entry.get("parameters")
        step_defs.append(
            OrchestratorPlanStepDefinition(
                name=db_models.OrchestratorPlanStep(name),
                parameters=parameters if isinstance(parameters, dict) else None,
            )
        )
    return OrchestratorActionPlanModel(
        plan_id=plan.id,
        generated_at=plan.generated_at,
        generated_by=plan.generated_by,
        steps=step_defs,
    )


def _convert_artifacts(
    artifacts: Iterable[db_models.OrchestratorRunArtifact],
) -> list[OrchestratorRunArtifactModel]:
    serialized: list[OrchestratorRunArtifactModel] = []
    for artifact in artifacts:
        serialized.append(
            OrchestratorRunArtifactModel(
                artifact_id=artifact.id,
                type=artifact.artifact_type,
                path=artifact.path,
                checksum=artifact.checksum,
                size_bytes=artifact.size_bytes,
                created_at=artifact.created_at,
            )
        )
    return serialized


def _convert_task_states(
    states: Iterable[workflow_models.SpecWorkflowTaskState],
) -> list[OrchestratorPlanStepStateModel]:
    serialized: list[OrchestratorPlanStepStateModel] = []
    for state in states:
        step = state.plan_step
        if step is None and state.task_name:
            step = db_models.OrchestratorPlanStep(state.task_name)
        if step is None:
            continue
        artifact_refs: list[UUID] = []
        if state.artifact_paths:
            for ref in state.artifact_paths:
                try:
                    artifact_refs.append(UUID(ref))
                except (TypeError, ValueError):
                    continue
        serialized.append(
            OrchestratorPlanStepStateModel(
                name=step,
                status=state.plan_step_status,
                started_at=state.started_at,
                completed_at=state.finished_at,
                celery_task_id=state.celery_task_id,
                celery_state=state.celery_state,
                message=state.message,
                artifact_refs=artifact_refs,
            )
        )
    return serialized


def serialize_run_summary(run: db_models.OrchestratorRun) -> OrchestratorRunSummaryModel:
    approval_required, approval_status = _resolve_approval_state(run)
    return OrchestratorRunSummaryModel(
        run_id=run.id,
        status=run.status,
        priority=run.priority,
        target_service=run.target_service,
        instruction=run.instruction,
        queued_at=run.queued_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        approval_required=approval_required,
        approval_status=approval_status,
    )


def serialize_run_detail(run: db_models.OrchestratorRun) -> OrchestratorRunDetailModel:
    summary = serialize_run_summary(run)
    return OrchestratorRunDetailModel(
        **summary.model_dump(),
        action_plan=_convert_plan_definition(run.action_plan),
        steps=_convert_task_states(run.task_states or []),
        artifacts=_convert_artifacts(run.artifacts or []),
        metrics_snapshot=run.metrics_snapshot,
    )


def serialize_run_list(
    runs: Iterable[db_models.OrchestratorRun],
) -> OrchestratorRunListResponse:
    summaries = [serialize_run_summary(run) for run in runs]
    return OrchestratorRunListResponse(runs=summaries)


def serialize_artifacts(
    artifacts: Iterable[db_models.OrchestratorRunArtifact],
) -> list[OrchestratorRunArtifactModel]:
    return _convert_artifacts(artifacts)


__all__ = [
    "serialize_run_summary",
    "serialize_run_detail",
    "serialize_run_list",
    "serialize_artifacts",
]
