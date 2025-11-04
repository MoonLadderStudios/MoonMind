"""Database repositories for the Spec Kit Celery workflow."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from moonmind.workflows.speckit_celery import models

_UNSET: object = object()
_DEFAULT_ARTIFACT_RETENTION = timedelta(days=7)


def _coerce_phase(value: models.SpecAutomationPhase | str) -> models.SpecAutomationPhase:
    """Return the phase enum for ``value`` allowing string inputs."""

    if isinstance(value, models.SpecAutomationPhase):
        return value
    return models.SpecAutomationPhase(str(value))


def _coerce_status(
    value: models.SpecAutomationTaskStatus | str,
) -> models.SpecAutomationTaskStatus:
    """Return the task status enum for ``value`` allowing string inputs."""

    if isinstance(value, models.SpecAutomationTaskStatus):
        return value
    return models.SpecAutomationTaskStatus(str(value))


def _coerce_run_status(
    value: models.SpecAutomationRunStatus | str,
) -> models.SpecAutomationRunStatus:
    """Return the run status enum for ``value`` allowing string inputs."""

    if isinstance(value, models.SpecAutomationRunStatus):
        return value
    return models.SpecAutomationRunStatus(str(value))


class SpecWorkflowRepository:
    """Repository exposing CRUD helpers for workflow runs and related records."""

    _UPDATABLE_RUN_FIELDS = {
        "celery_chain_id",
        "status",
        "phase",
        "branch_name",
        "pr_url",
        "codex_task_id",
        "codex_logs_path",
        "codex_patch_path",
        "artifacts_path",
        "started_at",
        "finished_at",
    }

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

        stmt: Select[tuple[models.SpecWorkflowRun]] = select(
            models.SpecWorkflowRun
        ).where(models.SpecWorkflowRun.id == run_id)
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

        stmt = (
            select(models.SpecWorkflowRun)
            .where(
                models.SpecWorkflowRun.feature_key == feature_key,
                models.SpecWorkflowRun.status.in_(
                    [
                        models.SpecWorkflowRunStatus.PENDING,
                        models.SpecWorkflowRunStatus.RUNNING,
                    ]
                ),
            )
            .order_by(models.SpecWorkflowRun.created_at.desc())
        )
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
            if field not in self._UPDATABLE_RUN_FIELDS:
                raise AttributeError(
                    f"SpecWorkflowRun field '{field}' cannot be updated or does not exist."
                )
            setattr(run, field, value)

        await self._session.flush()
        return run

    # ------------------------------------------------------------------
    # Task states
    # ------------------------------------------------------------------
    async def ensure_task_state_placeholders(
        self,
        *,
        workflow_run_id: UUID,
        task_names: Iterable[str],
        attempt: int = 1,
    ) -> Sequence[models.SpecWorkflowTaskState]:
        """Ensure placeholder task state rows exist for the given tasks.

        The UI expects every step in the Celery chain to have a visible state as
        soon as a workflow run is triggered. This helper guarantees that a
        ``queued`` record exists for each task/attempt combination while
        avoiding duplicate inserts when invoked multiple times.
        """

        unique_names: list[str] = []
        seen: set[str] = set()
        for name in task_names:
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)
            unique_names.append(name)

        if not unique_names:
            return []

        stmt = select(models.SpecWorkflowTaskState).where(
            models.SpecWorkflowTaskState.workflow_run_id == workflow_run_id,
            models.SpecWorkflowTaskState.task_name.in_(unique_names),
            models.SpecWorkflowTaskState.attempt == attempt,
        )
        existing = await self._session.execute(stmt)
        existing_by_name = {
            state.task_name: state for state in existing.scalars().all()
        }

        now = datetime.now(UTC)
        created = False
        for name in unique_names:
            if name in existing_by_name:
                continue
            placeholder = models.SpecWorkflowTaskState(
                id=uuid4(),
                workflow_run_id=workflow_run_id,
                task_name=name,
                status=models.SpecWorkflowTaskStatus.QUEUED,
                attempt=attempt,
                payload={},
                created_at=now,
                updated_at=now,
            )
            self._session.add(placeholder)
            existing_by_name[name] = placeholder
            created = True

        if created:
            await self._session.flush()
        return [existing_by_name[name] for name in unique_names]

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

    async def list_task_states_for_runs(
        self, run_ids: Iterable[UUID]
    ) -> dict[UUID, list[models.SpecWorkflowTaskState]]:
        """Return task states for the provided workflow run identifiers."""

        id_list = [run_id for run_id in run_ids]
        if not id_list:
            return {}

        stmt = (
            select(models.SpecWorkflowTaskState)
            .where(models.SpecWorkflowTaskState.workflow_run_id.in_(id_list))
            .order_by(
                models.SpecWorkflowTaskState.workflow_run_id,
                models.SpecWorkflowTaskState.created_at.asc(),
            )
        )
        result = await self._session.execute(stmt)
        grouped: dict[UUID, list[models.SpecWorkflowTaskState]] = {
            run_id: [] for run_id in id_list
        }
        for state in result.scalars().all():
            grouped[state.workflow_run_id].append(state)
        return grouped

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

        existing_stmt = select(models.WorkflowArtifact).where(
            models.WorkflowArtifact.workflow_run_id == workflow_run_id,
            models.WorkflowArtifact.artifact_type == artifact_type,
            models.WorkflowArtifact.path == path,
        )
        existing_result = await self._session.execute(existing_stmt)
        artifact = existing_result.scalar_one_or_none()
        if artifact is not None:
            return artifact

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

        now = datetime.now(UTC)
        new_artifacts = [
            models.WorkflowArtifact(
                id=uuid4(),
                workflow_run_id=workflow_run_id,
                artifact_type=artifact_type,
                path=path,
                created_at=now,
            )
            for artifact_type, path in artifacts
        ]

        if not new_artifacts:
            return []

        self._session.add_all(new_artifacts)

        await self._session.flush()
        return new_artifacts

    async def list_artifacts(
        self, workflow_run_id: UUID
    ) -> Sequence[models.WorkflowArtifact]:
        """Fetch artifacts for the given workflow run."""

        stmt = select(models.WorkflowArtifact).where(
            models.WorkflowArtifact.workflow_run_id == workflow_run_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


class SpecAutomationRepository:
    """Persistence helpers for Spec Kit automation runs and related entities."""

    _UPDATABLE_RUN_FIELDS = {
        "status",
        "branch_name",
        "pull_request_url",
        "result_summary",
        "started_at",
        "completed_at",
        "worker_hostname",
        "job_container_id",
        "base_branch",
        "external_ref",
    }

    _FIELD_CONVERTERS: dict[str, Any] = {
        "status": _coerce_run_status,
    }

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    async def create_run(
        self,
        *,
        repository: str,
        requested_spec_input: str,
        base_branch: str = "main",
        external_ref: Optional[str] = None,
        status: models.SpecAutomationRunStatus | str = models.SpecAutomationRunStatus.QUEUED,
        branch_name: Optional[str] = None,
        pull_request_url: Optional[str] = None,
        result_summary: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        worker_hostname: Optional[str] = None,
        job_container_id: Optional[str] = None,
        run_id: Optional[UUID] = None,
    ) -> models.SpecAutomationRun:
        """Persist a new Spec Automation run record."""

        run = models.SpecAutomationRun(
            id=run_id or uuid4(),
            external_ref=external_ref,
            repository=repository,
            base_branch=base_branch,
            branch_name=branch_name,
            pull_request_url=pull_request_url,
            status=_coerce_run_status(status),
            result_summary=result_summary,
            requested_spec_input=requested_spec_input,
            started_at=started_at,
            completed_at=completed_at,
            worker_hostname=worker_hostname,
            job_container_id=job_container_id,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(
        self, run_id: UUID, *, with_relations: bool = False
    ) -> Optional[models.SpecAutomationRun]:
        """Retrieve a Spec Automation run by identifier."""

        stmt: Select[tuple[models.SpecAutomationRun]] = select(
            models.SpecAutomationRun
        ).where(models.SpecAutomationRun.id == run_id)
        if with_relations:
            stmt = stmt.options(
                selectinload(models.SpecAutomationRun.task_states),
                selectinload(models.SpecAutomationRun.artifacts),
                selectinload(models.SpecAutomationRun.agent_configuration),
            )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_external_ref(
        self, external_ref: str, *, repository: Optional[str] = None
    ) -> Optional[models.SpecAutomationRun]:
        """Locate the most recent run for an external reference."""

        stmt: Select[tuple[models.SpecAutomationRun]] = (
            select(models.SpecAutomationRun)
            .where(models.SpecAutomationRun.external_ref == external_ref)
            .order_by(models.SpecAutomationRun.created_at.desc())
        )
        if repository is not None:
            stmt = stmt.where(models.SpecAutomationRun.repository == repository)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_runs(
        self,
        *,
        status: Optional[models.SpecAutomationRunStatus] = None,
        repository: Optional[str] = None,
        limit: int = 25,
        with_relations: bool = False,
    ) -> Sequence[models.SpecAutomationRun]:
        """List automation runs ordered by creation time."""

        stmt: Select[tuple[models.SpecAutomationRun]] = (
            select(models.SpecAutomationRun)
            .order_by(models.SpecAutomationRun.created_at.desc())
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(
                models.SpecAutomationRun.status
                == _coerce_run_status(status)
            )
        if repository is not None:
            stmt = stmt.where(models.SpecAutomationRun.repository == repository)
        if with_relations:
            stmt = stmt.options(
                selectinload(models.SpecAutomationRun.task_states),
                selectinload(models.SpecAutomationRun.artifacts),
                selectinload(models.SpecAutomationRun.agent_configuration),
            )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update_run(
        self, run_id: UUID, **changes: Any
    ) -> Optional[models.SpecAutomationRun]:
        """Apply updates to an automation run and return the refreshed entity."""

        run = await self._session.get(models.SpecAutomationRun, run_id)
        if run is None:
            return None

        for field, value in changes.items():
            if field not in self._UPDATABLE_RUN_FIELDS:
                raise AttributeError(
                    f"SpecAutomationRun field '{field}' cannot be updated or does not exist."
                )
            converter = self._FIELD_CONVERTERS.get(field)
            setattr(run, field, converter(value) if converter else value)

        await self._session.flush()
        return run

    # ------------------------------------------------------------------
    # Task states
    # ------------------------------------------------------------------
    async def ensure_task_state_placeholders(
        self,
        *,
        run_id: UUID,
        phases: Iterable[models.SpecAutomationPhase | str],
        attempt: int = 1,
    ) -> Sequence[models.SpecAutomationTaskState]:
        """Ensure placeholder task state rows exist for the given phases."""

        unique_phases: list[models.SpecAutomationPhase] = []
        seen: set[models.SpecAutomationPhase] = set()
        for raw_phase in phases:
            phase = _coerce_phase(raw_phase)
            if phase in seen:
                continue
            seen.add(phase)
            unique_phases.append(phase)

        if not unique_phases:
            return []

        stmt = select(models.SpecAutomationTaskState).where(
            models.SpecAutomationTaskState.run_id == run_id,
            models.SpecAutomationTaskState.phase.in_(unique_phases),
            models.SpecAutomationTaskState.attempt == attempt,
        )
        existing = await self._session.execute(stmt)
        existing_by_phase = {
            state.phase: state for state in existing.scalars().all()
        }

        now = datetime.now(UTC)
        created = False
        for phase in unique_phases:
            if phase in existing_by_phase:
                continue
            placeholder = models.SpecAutomationTaskState(
                id=uuid4(),
                run_id=run_id,
                phase=phase,
                status=models.SpecAutomationTaskStatus.PENDING,
                attempt=attempt,
                created_at=now,
                updated_at=now,
            )
            self._session.add(placeholder)
            existing_by_phase[phase] = placeholder
            created = True

        if created:
            await self._session.flush()
        return [existing_by_phase[phase] for phase in unique_phases]

    async def upsert_task_state(
        self,
        *,
        run_id: UUID,
        phase: models.SpecAutomationPhase | str,
        status: models.SpecAutomationTaskStatus | str,
        attempt: int = 1,
        started_at: datetime | None | object = _UNSET,
        completed_at: datetime | None | object = _UNSET,
        stdout_path: str | None | object = _UNSET,
        stderr_path: str | None | object = _UNSET,
        metadata: Optional[dict[str, Any]] | object = _UNSET,
    ) -> models.SpecAutomationTaskState:
        """Create or update a task state record for a phase attempt."""

        phase_enum = _coerce_phase(phase)
        status_enum = _coerce_status(status)

        stmt = select(models.SpecAutomationTaskState).where(
            models.SpecAutomationTaskState.run_id == run_id,
            models.SpecAutomationTaskState.phase == phase_enum,
            models.SpecAutomationTaskState.attempt == attempt,
        )
        existing = await self._session.execute(stmt)
        state = existing.scalar_one_or_none()

        now = datetime.now(UTC)
        if state is None:
            state = models.SpecAutomationTaskState(
                id=uuid4(),
                run_id=run_id,
                phase=phase_enum,
                status=status_enum,
                attempt=attempt,
                created_at=now,
                updated_at=now,
            )
            if started_at is not _UNSET:
                state.started_at = None if started_at is None else started_at
            if completed_at is not _UNSET:
                state.completed_at = None if completed_at is None else completed_at
            if stdout_path is not _UNSET:
                state.stdout_path = None if stdout_path is None else stdout_path
            if stderr_path is not _UNSET:
                state.stderr_path = None if stderr_path is None else stderr_path
            if metadata is not _UNSET:
                state.metadata_payload = None if metadata is None else dict(metadata)
            self._session.add(state)
        else:
            state.status = status_enum
            if started_at is not _UNSET:
                state.started_at = None if started_at is None else started_at
            if completed_at is not _UNSET:
                state.completed_at = None if completed_at is None else completed_at
            if stdout_path is not _UNSET:
                state.stdout_path = None if stdout_path is None else stdout_path
            if stderr_path is not _UNSET:
                state.stderr_path = None if stderr_path is None else stderr_path
            if metadata is not _UNSET:
                state.metadata_payload = None if metadata is None else dict(metadata)
            state.updated_at = now

        await self._session.flush()
        return state

    async def list_task_states(
        self, run_id: UUID
    ) -> Sequence[models.SpecAutomationTaskState]:
        """Return task states ordered by creation time."""

        stmt = (
            select(models.SpecAutomationTaskState)
            .where(models.SpecAutomationTaskState.run_id == run_id)
            .order_by(models.SpecAutomationTaskState.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------
    async def create_artifact(
        self,
        *,
        run_id: UUID,
        name: str,
        artifact_type: models.SpecAutomationArtifactType | str,
        storage_path: str,
        task_state_id: Optional[UUID] = None,
        content_type: Optional[str] = None,
        size_bytes: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        source_phase: models.SpecAutomationPhase | str | None = None,
    ) -> models.SpecAutomationArtifact:
        """Persist an artifact reference for a Spec Automation run."""

        artifact_type_enum = (
            artifact_type
            if isinstance(artifact_type, models.SpecAutomationArtifactType)
            else models.SpecAutomationArtifactType(str(artifact_type))
        )
        source_phase_enum = (
            _coerce_phase(source_phase) if source_phase is not None else None
        )

        stmt = select(models.SpecAutomationArtifact).where(
            models.SpecAutomationArtifact.run_id == run_id,
            models.SpecAutomationArtifact.artifact_type == artifact_type_enum,
            models.SpecAutomationArtifact.storage_path == storage_path,
        )
        existing = await self._session.execute(stmt)
        artifact = existing.scalar_one_or_none()

        now = datetime.now(UTC)
        if artifact is None:
            artifact = models.SpecAutomationArtifact(
                id=uuid4(),
                run_id=run_id,
                task_state_id=task_state_id,
                name=name,
                artifact_type=artifact_type_enum,
                storage_path=storage_path,
                content_type=content_type,
                size_bytes=size_bytes,
                expires_at=expires_at or now + _DEFAULT_ARTIFACT_RETENTION,
                source_phase=source_phase_enum,
                created_at=now,
            )
            self._session.add(artifact)
        else:
            artifact.name = name
            artifact.task_state_id = task_state_id
            artifact.content_type = content_type
            artifact.size_bytes = size_bytes
            artifact.expires_at = (
                expires_at
                if expires_at is not None
                else artifact.expires_at
            )
            artifact.source_phase = source_phase_enum

        await self._session.flush()
        return artifact

    async def list_artifacts(
        self, run_id: UUID
    ) -> Sequence[models.SpecAutomationArtifact]:
        """Return artifacts persisted for the specified run."""

        stmt = (
            select(models.SpecAutomationArtifact)
            .where(models.SpecAutomationArtifact.run_id == run_id)
            .order_by(models.SpecAutomationArtifact.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


__all__ = ["SpecWorkflowRepository", "SpecAutomationRepository"]
