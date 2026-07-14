"""Owner-scoped durable container-job operations for MoonMind#3252."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ContainerJobRecord
from api_service.services.container_job_workspace_authorizer import (
    ContainerJobWorkspaceAuthorizer,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobAccepted,
    ContainerJobCancelRequest,
    ContainerJobCancelResult,
    ContainerJobState,
    ContainerJobStatus,
    ContainerJobSubmitRequest,
    ContainerJobWorkflowInput,
    ImageObservation,
    OwnerIdentity,
    TerminalOutcome,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter


class ContainerJobNotFoundError(RuntimeError):
    pass


class ContainerJobIdempotencyConflictError(RuntimeError):
    pass


class ContainerJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_or_replay(self, *, owner: OwnerIdentity, request: ContainerJobSubmitRequest) -> tuple[ContainerJobRecord, bool]:
        existing = await self._by_idempotency(owner, request.idempotency_key)
        request_json = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        if existing is not None:
            if existing.request_json != request_json:
                raise ContainerJobIdempotencyConflictError("idempotency key was already used with a different request")
            return existing, True
        now = datetime.now(timezone.utc)
        record = ContainerJobRecord(
            job_id=f"container-job:{uuid4().hex}",
            owner_id=owner.principal_id,
            owner_type=owner.principal_type,
            idempotency_key=request.idempotency_key,
            source_json=request.source.model_dump(mode="json", by_alias=True, exclude_none=True),
            request_json=request_json,
            state=ContainerJobState.QUEUED.value,
            publication_outcome_json={"state": "not_attempted"},
            cleanup_outcome_json={"state": "not_attempted"},
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(record)
                await self._session.flush()
            return record, False
        except IntegrityError:
            existing = await self._by_idempotency(owner, request.idempotency_key)
            if existing is None:
                raise
            if existing.request_json != request_json:
                raise ContainerJobIdempotencyConflictError("idempotency key was already used with a different request")
            return existing, True

    async def _by_idempotency(self, owner: OwnerIdentity, key: str) -> ContainerJobRecord | None:
        result = await self._session.execute(select(ContainerJobRecord).where(ContainerJobRecord.owner_id == owner.principal_id, ContainerJobRecord.owner_type == owner.principal_type, ContainerJobRecord.idempotency_key == key))
        return result.scalar_one_or_none()

    async def find_exact_replay(
        self, *, owner: OwnerIdentity, request: ContainerJobSubmitRequest
    ) -> ContainerJobRecord | None:
        existing = await self._by_idempotency(owner, request.idempotency_key)
        if existing is None:
            return None
        request_json = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        if existing.request_json != request_json:
            raise ContainerJobIdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        return existing

    async def get_for_owner(self, *, owner: OwnerIdentity, job_id: str) -> ContainerJobRecord | None:
        result = await self._session.execute(select(ContainerJobRecord).where(ContainerJobRecord.job_id == job_id, ContainerJobRecord.owner_id == owner.principal_id, ContainerJobRecord.owner_type == owner.principal_type))
        return result.scalar_one_or_none()

    async def record_observation(
        self, *, owner: OwnerIdentity, job_id: str, state: ContainerJobState,
        backend_kind: str | None = None, backend_ref: str | None = None,
        image: ImageObservation | None = None, terminal: TerminalOutcome | None = None,
        publication: AuxiliaryOutcome | None = None, cleanup: AuxiliaryOutcome | None = None,
        logs_ref: str | None = None, artifacts_ref: str | None = None,
    ) -> ContainerJobRecord:
        record = await self.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        record.state = state.value
        if backend_kind is not None:
            record.backend_kind = backend_kind
        if backend_ref is not None:
            record.backend_ref = backend_ref
        if image is not None:
            record.image_observation_json = image.model_dump(mode="json", by_alias=True, exclude_none=True)
        if terminal is not None:
            record.terminal_outcome_json = terminal.model_dump(mode="json", by_alias=True, exclude_none=True)
        if publication is not None:
            record.publication_outcome_json = publication.model_dump(mode="json", by_alias=True, exclude_none=True)
        if cleanup is not None:
            record.cleanup_outcome_json = cleanup.model_dump(mode="json", by_alias=True, exclude_none=True)
        if logs_ref is not None:
            record.logs_ref = logs_ref
        if artifacts_ref is not None:
            record.artifacts_ref = artifacts_ref
        await self._session.flush()
        return record


class ContainerJobService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        temporal: TemporalClientAdapter | None = None,
        workspace_authorizer: ContainerJobWorkspaceAuthorizer | None = None,
    ) -> None:
        self.repository = ContainerJobRepository(session)
        self._temporal = temporal or TemporalClientAdapter()
        self._workspace_authorizer = workspace_authorizer

    async def submit(self, *, owner: OwnerIdentity, request: ContainerJobSubmitRequest) -> ContainerJobAccepted:
        existing = await self.repository.find_exact_replay(owner=owner, request=request)
        if existing is not None:
            created_at = existing.created_at or datetime.now(timezone.utc)
            return ContainerJobAccepted(
                jobId=existing.job_id, replayed=True, createdAt=created_at
            )
        # Authorize the logical workspace reference against its canonical
        # durable ownership record before any durable job identity or workflow
        # is created (MoonMind#3255). Absent, terminally deleted, cross-user,
        # and cross-session references fail closed here with a stable
        # classification and never reach the workflow or a Docker backend.
        if self._workspace_authorizer is not None:
            await self._workspace_authorizer.authorize(owner=owner, request=request)
        record, replayed = await self.repository.create_or_replay(owner=owner, request=request)
        await self._temporal.start_container_job(
            ContainerJobWorkflowInput(jobId=record.job_id, owner=owner, request=request)
        )
        created_at = record.created_at or datetime.now(timezone.utc)
        return ContainerJobAccepted(jobId=record.job_id, replayed=replayed, createdAt=created_at)

    async def status(self, *, owner: OwnerIdentity, job_id: str) -> ContainerJobStatus:
        record = await self.repository.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        return ContainerJobStatus(
            jobId=record.job_id, state=record.state, backendKind=record.backend_kind,
            backendRef=record.backend_ref, image=record.image_observation_json,
            terminal=record.terminal_outcome_json, publication=record.publication_outcome_json,
            cleanup=record.cleanup_outcome_json, logsRef=record.logs_ref,
            artifactsRef=record.artifacts_ref, updatedAt=record.updated_at or record.created_at,
        )

    async def cancel(self, *, owner: OwnerIdentity, job_id: str, request: ContainerJobCancelRequest) -> ContainerJobCancelResult:
        record = await self.repository.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        replayed = record.cancel_idempotency_key == request.idempotency_key
        terminal = record.state in {"succeeded", "failed", "canceled", "timed_out", "rejected"}
        if not terminal and not replayed:
            record.cancel_idempotency_key = request.idempotency_key
            record.state = ContainerJobState.CANCELING.value
            await self.repository._session.flush()
            await self._temporal.signal_container_job_cancel(job_id)
        return ContainerJobCancelResult(jobId=job_id, state=record.state, accepted=not terminal, replayed=replayed)
