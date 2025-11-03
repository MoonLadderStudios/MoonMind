"""Database repositories for the Spec Kit Celery workflow."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from moonmind.workflows.speckit_celery import models


class SpecWorkflowRepository:
    """Repository exposing CRUD helpers for workflow runs and related records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Workflow runs
    # ------------------------------------------------------------------
    async def create_run(
        self,
        *,
        feature_key: str,
        created_by: Optional[UUID] = None,
        artifacts_path: Optional[str] = None,
        celery_chain_id: Optional[str] = None,
        status: models.SpecWorkflowRunStatus = models.SpecWorkflowRunStatus.PENDING,
        phase: models.SpecWorkflowRunPhase = models.SpecWorkflowRunPhase.DISCOVER,
    ) -> models.SpecWorkflowRun:
        """Create a new workflow run row."""

        run = models.SpecWorkflowRun(
            id=uuid4(),
            feature_key=feature_key,
            created_by=created_by,
            artifacts_path=artifacts_path,
            status=status,
            phase=phase,
            celery_chain_id=celery_chain_id,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(
        self, run_id: UUID, *, with_relations: bool = False
    ) -> Optional[models.SpecWorkflowRun]:
        """Return a workflow run optionally including related state."""

        stmt: Select[tuple[models.SpecWorkflowRun]] = select(models.SpecWorkflowRun).where(
            models.SpecWorkflowRun.id == run_id
        )
        if with_relations:
            stmt = stmt.options(
                selectinload(models.SpecWorkflowRun.task_states),
                selectinload(models.SpecWorkflowRun.artifacts),
                selectinload(models.SpecWorkflowRun.credential_audit),
            )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_active_run_for_feature(
        self, feature_key: str
    ) -> Optional[models.SpecWorkflowRun]:
        """Locate a pending or running workflow for the given feature."""

        stmt = select(models.SpecWorkflowRun).where(
            models.SpecWorkflowRun.feature_key == feature_key,
            models.SpecWorkflowRun.status.in_(
                [
                    models.SpecWorkflowRunStatus.PENDING,
                    models.SpecWorkflowRunStatus.RUNNING,
                ]
            ),
        ).order_by(models.SpecWorkflowRun.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_runs(
        self,
        *,
        status: Optional[models.SpecWorkflowRunStatus] = None,
        feature_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
        limit: int = 25,
        with_relations: bool = False,
    ) -> Sequence[models.SpecWorkflowRun]:
        """Return workflow runs filtered by the provided parameters."""

        stmt: Select[tuple[models.SpecWorkflowRun]] = (
            select(models.SpecWorkflowRun)
            .order_by(models.SpecWorkflowRun.created_at.desc())
            .limit(limit)
        )

        if status is not None:
            stmt = stmt.where(models.SpecWorkflowRun.status == status)
        if feature_key is not None:
            stmt = stmt.where(models.SpecWorkflowRun.feature_key == feature_key)
        if created_by is not None:
            stmt = stmt.where(models.SpecWorkflowRun.created_by == created_by)
        if with_relations:
            stmt = stmt.options(
                selectinload(models.SpecWorkflowRun.task_states),
                selectinload(models.SpecWorkflowRun.artifacts),
                selectinload(models.SpecWorkflowRun.credential_audit),
            )

        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update_run(
        self,
        run_id: UUID,
        **changes: object,
    ) -> Optional[models.SpecWorkflowRun]:
        """Apply updates to a workflow run and return the refreshed entity."""

        run = await self._session.get(models.SpecWorkflowRun, run_id)
        if run is None:
            return None

        for field, value in changes.items():
            if not hasattr(run, field):
                raise AttributeError(f"SpecWorkflowRun has no field '{field}'")
            setattr(run, field, value)

        await self._session.flush()
        return run

    # ------------------------------------------------------------------
    # Task states
    # ------------------------------------------------------------------
    async def upsert_task_state(
        self,
        *,
        workflow_run_id: UUID,
        task_name: str,
        status: models.SpecWorkflowTaskStatus,
        attempt: int = 1,
        payload: Optional[dict[str, object]] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ) -> models.SpecWorkflowTaskState:
        """Create or update a task state for the given run/task/attempt."""

        stmt = select(models.SpecWorkflowTaskState).where(
            models.SpecWorkflowTaskState.workflow_run_id == workflow_run_id,
            models.SpecWorkflowTaskState.task_name == task_name,
            models.SpecWorkflowTaskState.attempt == attempt,
        )
        existing = await self._session.execute(stmt)
        state = existing.scalar_one_or_none()

        now = datetime.now(UTC)
        if state is None:
            state = models.SpecWorkflowTaskState(
                id=uuid4(),
                workflow_run_id=workflow_run_id,
                task_name=task_name,
                status=status,
                attempt=attempt,
                payload=payload or {},
                started_at=started_at,
                finished_at=finished_at,
                created_at=now,
                updated_at=now,
            )
            self._session.add(state)
        else:
            state.status = status
            if payload is not None:
                state.payload = payload
            if started_at is not None:
                state.started_at = started_at
            if finished_at is not None:
                state.finished_at = finished_at
            state.updated_at = now

        await self._session.flush()
        return state

    async def list_task_states(
        self, workflow_run_id: UUID
    ) -> Sequence[models.SpecWorkflowTaskState]:
        """Return task states ordered by creation time."""

        stmt = (
            select(models.SpecWorkflowTaskState)
            .where(models.SpecWorkflowTaskState.workflow_run_id == workflow_run_id)
            .order_by(models.SpecWorkflowTaskState.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Credential audits & artifacts
    # ------------------------------------------------------------------
    async def upsert_credential_audit(
        self,
        *,
        workflow_run_id: UUID,
        codex_status: models.CodexCredentialStatus,
        github_status: models.GitHubCredentialStatus,
        notes: Optional[str] = None,
        checked_at: Optional[datetime] = None,
    ) -> models.WorkflowCredentialAudit:
        """Persist credential audit data for a workflow run."""

        stmt = select(models.WorkflowCredentialAudit).where(
            models.WorkflowCredentialAudit.workflow_run_id == workflow_run_id
        )
        result = await self._session.execute(stmt)
        audit = result.scalar_one_or_none()
        timestamp = checked_at or datetime.now(UTC)

        if audit is None:
            audit = models.WorkflowCredentialAudit(
                id=uuid4(),
                workflow_run_id=workflow_run_id,
                codex_status=codex_status,
                github_status=github_status,
                notes=notes,
                checked_at=timestamp,
            )
            self._session.add(audit)
        else:
            audit.codex_status = codex_status
            audit.github_status = github_status
            audit.notes = notes
            audit.checked_at = timestamp

        await self._session.flush()
        return audit

    async def add_artifact(
        self,
        *,
        workflow_run_id: UUID,
        artifact_type: models.WorkflowArtifactType,
        path: str,
        created_at: Optional[datetime] = None,
    ) -> models.WorkflowArtifact:
        """Store a new artifact reference for the workflow run."""

        artifact = models.WorkflowArtifact(
            id=uuid4(),
            workflow_run_id=workflow_run_id,
            artifact_type=artifact_type,
            path=path,
            created_at=created_at or datetime.now(UTC),
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def add_artifacts(
        self,
        workflow_run_id: UUID,
        artifacts: Iterable[tuple[models.WorkflowArtifactType, str]],
    ) -> Sequence[models.WorkflowArtifact]:
        """Bulk insert artifacts returning the persisted objects."""

        persisted: list[models.WorkflowArtifact] = []
        now = datetime.now(UTC)
        for artifact_type, path in artifacts:
            obj = models.WorkflowArtifact(
                id=uuid4(),
                workflow_run_id=workflow_run_id,
                artifact_type=artifact_type,
                path=path,
                created_at=now,
            )
            self._session.add(obj)
            persisted.append(obj)

        await self._session.flush()
        return persisted

    async def list_artifacts(
        self, workflow_run_id: UUID
    ) -> Sequence[models.WorkflowArtifact]:
        """Fetch artifacts for the given workflow run."""

        stmt = select(models.WorkflowArtifact).where(
            models.WorkflowArtifact.workflow_run_id == workflow_run_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


__all__ = ["SpecWorkflowRepository"]
