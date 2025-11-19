"""Persistence helpers for the MoonMind orchestrator workflow."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.db import models as db_models
from moonmind.workflows.speckit_celery import models as workflow_models


def _to_enum(value: Any, enum_cls):
    """Coerce ``value`` into ``enum_cls`` preserving ``None``."""

    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    return enum_cls(value)


_PLAN_STATUS_TO_WORKFLOW_STATUS = {
    db_models.OrchestratorPlanStepStatus.PENDING: workflow_models.SpecWorkflowTaskStatus.QUEUED,
    db_models.OrchestratorPlanStepStatus.IN_PROGRESS: workflow_models.SpecWorkflowTaskStatus.RUNNING,
    db_models.OrchestratorPlanStepStatus.SUCCEEDED: workflow_models.SpecWorkflowTaskStatus.SUCCEEDED,
    db_models.OrchestratorPlanStepStatus.FAILED: workflow_models.SpecWorkflowTaskStatus.FAILED,
    db_models.OrchestratorPlanStepStatus.SKIPPED: workflow_models.SpecWorkflowTaskStatus.SKIPPED,
}


class OrchestratorRepository:
    """Repository encapsulating orchestrator persistence operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        """Persist pending changes for the current transaction."""

        await self._session.commit()

    # ------------------------------------------------------------------
    # Action plan helpers
    # ------------------------------------------------------------------
    async def create_action_plan(
        self,
        *,
        steps: Sequence[Mapping[str, Any]],
        service_context: Mapping[str, Any],
        generated_by: db_models.OrchestratorPlanOrigin = db_models.OrchestratorPlanOrigin.SYSTEM,
    ) -> db_models.OrchestratorActionPlan:
        """Persist a new orchestrator action plan snapshot."""

        serialized_steps = [dict(step) for step in steps]
        plan = db_models.OrchestratorActionPlan(
            id=uuid4(),
            steps=serialized_steps,
            service_context=dict(service_context),
            generated_by=_to_enum(generated_by, db_models.OrchestratorPlanOrigin),
        )
        self._session.add(plan)
        await self._session.flush()
        return plan

    # ------------------------------------------------------------------
    # Run helpers
    # ------------------------------------------------------------------
    async def create_run(
        self,
        *,
        instruction: str,
        target_service: str,
        action_plan: db_models.OrchestratorActionPlan,
        artifact_root: Optional[str] = None,
        priority: db_models.OrchestratorRunPriority = db_models.OrchestratorRunPriority.NORMAL,
        status: db_models.OrchestratorRunStatus = db_models.OrchestratorRunStatus.PENDING,
        approval_gate_id: Optional[UUID] = None,
        approval_token: Optional[str] = None,
        metrics_snapshot: Optional[dict[str, Any]] = None,
    ) -> db_models.OrchestratorRun:
        """Create a run record backed by the provided ``action_plan``."""

        run = db_models.OrchestratorRun(
            id=uuid4(),
            instruction=instruction,
            target_service=target_service,
            priority=_to_enum(priority, db_models.OrchestratorRunPriority),
            status=_to_enum(status, db_models.OrchestratorRunStatus),
            artifact_root=artifact_root,
            approval_gate_id=approval_gate_id,
            approval_token=approval_token,
            metrics_snapshot=metrics_snapshot,
        )
        run.action_plan = action_plan
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(
        self,
        run_id: UUID,
        *,
        with_relations: bool = False,
    ) -> Optional[db_models.OrchestratorRun]:
        """Fetch a run optionally including plan, artifacts, and task state."""

        stmt: Select[tuple[db_models.OrchestratorRun]] = select(
            db_models.OrchestratorRun
        ).where(db_models.OrchestratorRun.id == run_id)
        if with_relations:
            stmt = stmt.options(
                selectinload(db_models.OrchestratorRun.action_plan),
                selectinload(db_models.OrchestratorRun.artifacts),
                selectinload(db_models.OrchestratorRun.task_states),
                selectinload(db_models.OrchestratorRun.approval_gate),
            )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_runs(
        self,
        *,
        status: db_models.OrchestratorRunStatus | str | None = None,
        target_service: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[db_models.OrchestratorRun]:
        """Return orchestrator runs filtered by status/service with pagination."""

        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        stmt = select(db_models.OrchestratorRun).order_by(
            db_models.OrchestratorRun.queued_at.desc()
        )
        if status is not None:
            stmt = stmt.where(
                db_models.OrchestratorRun.status
                == _to_enum(status, db_models.OrchestratorRunStatus)
            )
        if target_service:
            stmt = stmt.where(
                db_models.OrchestratorRun.target_service == target_service
            )

        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_run(
        self,
        run: db_models.OrchestratorRun,
        *,
        status: db_models.OrchestratorRunStatus | str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        approval_token: Optional[str] = None,
        metrics_snapshot: Optional[dict[str, Any]] = None,
    ) -> db_models.OrchestratorRun:
        """Apply partial updates to ``run`` and flush the session."""

        if status is not None:
            run.status = _to_enum(status, db_models.OrchestratorRunStatus)
        if started_at is not None:
            run.started_at = started_at
        if completed_at is not None:
            run.completed_at = completed_at
        if approval_token is not None:
            run.approval_token = approval_token
        if metrics_snapshot is not None:
            run.metrics_snapshot = metrics_snapshot
        await self._session.flush()
        return run

    # ------------------------------------------------------------------
    # Task state helpers
    # ------------------------------------------------------------------
    async def initialize_plan_states(
        self,
        run: db_models.OrchestratorRun,
        steps: Iterable[Mapping[str, Any] | str | db_models.OrchestratorPlanStep],
    ) -> list[workflow_models.SpecWorkflowTaskState]:
        """Seed task state rows for each plan step with ``pending`` status."""

        created: list[workflow_models.SpecWorkflowTaskState] = []
        for step in steps:
            plan_step = self._coerce_plan_step(step)
            task_state = workflow_models.SpecWorkflowTaskState(
                id=uuid4(),
                workflow_run_id=None,
                orchestrator_run_id=run.id,
                task_name=plan_step.value,
                status=workflow_models.SpecWorkflowTaskStatus.QUEUED,
                attempt=1,
                plan_step=plan_step,
                plan_step_status=db_models.OrchestratorPlanStepStatus.PENDING,
                celery_state=None,
                celery_task_id=None,
                message=None,
                artifact_paths=[],
            )
            self._session.add(task_state)
            created.append(task_state)
        await self._session.flush()
        return created

    async def upsert_plan_step_state(
        self,
        *,
        run_id: UUID,
        plan_step: db_models.OrchestratorPlanStep | str,
        attempt: int = 1,
        plan_step_status: db_models.OrchestratorPlanStepStatus | str | None = None,
        celery_state: db_models.OrchestratorTaskState | str | None = None,
        celery_task_id: Optional[str] = None,
        message: Optional[str] = None,
        artifact_paths: Optional[Sequence[UUID]] = None,
        payload: Optional[Mapping[str, Any]] = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> workflow_models.SpecWorkflowTaskState:
        """Create or update a plan step execution record."""

        step_enum = self._coerce_plan_step(plan_step)
        attempt = max(attempt, 1)

        insert_values: dict[str, Any] = {
            "id": uuid4(),
            "workflow_run_id": None,
            "orchestrator_run_id": run_id,
            "task_name": step_enum.value,
            "status": workflow_models.SpecWorkflowTaskStatus.QUEUED,
            "attempt": attempt,
            "plan_step": step_enum,
        }
        update_values: dict[str, Any] = {}

        if plan_step_status is not None:
            plan_status = _to_enum(
                plan_step_status, db_models.OrchestratorPlanStepStatus
            )
            insert_values["plan_step_status"] = plan_status
            update_values["plan_step_status"] = plan_status
            mapped_status = _PLAN_STATUS_TO_WORKFLOW_STATUS.get(plan_status)
            if mapped_status is not None:
                insert_values["status"] = mapped_status
                update_values["status"] = mapped_status
        if celery_state is not None:
            celery_status = _to_enum(celery_state, db_models.OrchestratorTaskState)
            insert_values["celery_state"] = celery_status
            update_values["celery_state"] = celery_status
        if celery_task_id is not None:
            insert_values["celery_task_id"] = celery_task_id
            update_values["celery_task_id"] = celery_task_id
        if message is not None:
            insert_values["message"] = message
            update_values["message"] = message
        if artifact_paths is not None:
            refs = [str(ref) for ref in artifact_paths]
            insert_values["artifact_refs"] = refs
            update_values["artifact_refs"] = refs
        if payload is not None:
            serialized_payload = dict(payload)
            insert_values["payload"] = serialized_payload
            update_values["payload"] = serialized_payload
        if started_at is not None:
            insert_values["started_at"] = started_at
            update_values["started_at"] = started_at
        if finished_at is not None:
            insert_values["finished_at"] = finished_at
            update_values["finished_at"] = finished_at

        insert_stmt = pg_insert(workflow_models.SpecWorkflowTaskState).values(
            insert_values
        )
        conflict_cols = (
            workflow_models.SpecWorkflowTaskState.orchestrator_run_id,
            workflow_models.SpecWorkflowTaskState.plan_step,
            workflow_models.SpecWorkflowTaskState.attempt,
        )
        if update_values:
            insert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=conflict_cols,
                set_=update_values,
            )
        else:
            insert_stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=conflict_cols
            )

        await self._session.execute(insert_stmt)
        await self._session.flush()

        state_result = await self._session.execute(
            select(workflow_models.SpecWorkflowTaskState).where(
                workflow_models.SpecWorkflowTaskState.orchestrator_run_id == run_id,
                workflow_models.SpecWorkflowTaskState.plan_step == step_enum,
                workflow_models.SpecWorkflowTaskState.attempt == attempt,
            )
        )
        return state_result.scalar_one()

    # ------------------------------------------------------------------
    # Artifact helpers
    # ------------------------------------------------------------------
    async def add_artifact(
        self,
        *,
        run_id: UUID,
        artifact_type: db_models.OrchestratorRunArtifactType | str,
        path: str,
        checksum: Optional[str] = None,
        size_bytes: Optional[int] = None,
    ) -> db_models.OrchestratorRunArtifact:
        """Persist an artifact reference associated with ``run_id``."""

        artifact = db_models.OrchestratorRunArtifact(
            id=uuid4(),
            run_id=run_id,
            artifact_type=_to_enum(
                artifact_type, db_models.OrchestratorRunArtifactType
            ),
            path=path,
            checksum=checksum,
            size_bytes=size_bytes,
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def list_artifacts(
        self, run_id: UUID
    ) -> list[db_models.OrchestratorRunArtifact]:
        """Return artifacts stored for ``run_id`` ordered chronologically."""

        stmt = (
            select(db_models.OrchestratorRunArtifact)
            .where(db_models.OrchestratorRunArtifact.run_id == run_id)
            .order_by(db_models.OrchestratorRunArtifact.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_plan_step(
        value: Mapping[str, Any] | str | db_models.OrchestratorPlanStep,
    ) -> db_models.OrchestratorPlanStep:
        if isinstance(value, Mapping):
            candidate = value.get("name")
        else:
            candidate = value
        if candidate is None:
            raise ValueError("Plan step definition must include a 'name' field")
        return _to_enum(candidate, db_models.OrchestratorPlanStep)


__all__ = ["OrchestratorRepository"]
