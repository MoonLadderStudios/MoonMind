"""Repository helpers for agent queue persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.agent_queue import models


class AgentQueueRepositoryError(Exception):
    """Base class for agent queue repository errors."""


class AgentJobNotFoundError(AgentQueueRepositoryError):
    """Raised when a requested job does not exist."""

    def __init__(self, job_id: UUID) -> None:
        super().__init__(f"Job {job_id} was not found")
        self.job_id = job_id


class AgentJobStateError(AgentQueueRepositoryError):
    """Raised when a transition is not valid for current job status."""


class AgentJobOwnershipError(AgentQueueRepositoryError):
    """Raised when worker ownership does not match claimed job."""


class AgentArtifactNotFoundError(AgentQueueRepositoryError):
    """Raised when a requested artifact does not exist."""

    def __init__(self, artifact_id: UUID) -> None:
        super().__init__(f"Artifact {artifact_id} was not found")
        self.artifact_id = artifact_id


class AgentArtifactJobMismatchError(AgentQueueRepositoryError):
    """Raised when artifact id does not belong to the requested job."""

    def __init__(self, artifact_id: UUID, job_id: UUID) -> None:
        super().__init__(f"Artifact {artifact_id} does not belong to job {job_id}")
        self.artifact_id = artifact_id
        self.job_id = job_id


class AgentWorkerTokenNotFoundError(AgentQueueRepositoryError):
    """Raised when a requested worker token does not exist."""

    def __init__(self, token_id: UUID) -> None:
        super().__init__(f"Worker token {token_id} was not found")
        self.token_id = token_id


class AgentQueueRepository:
    """CRUD and lifecycle operations for queue jobs."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        lease_retry_delay_seconds: int = 30,
    ) -> None:
        self._session = session
        self._lease_retry_delay_seconds = max(1, int(lease_retry_delay_seconds))

    async def commit(self) -> None:
        """Persist transaction changes."""

        await self._session.commit()

    async def create_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        priority: int = 0,
        created_by_user_id: Optional[UUID] = None,
        requested_by_user_id: Optional[UUID] = None,
        affinity_key: Optional[str] = None,
        max_attempts: int = 3,
    ) -> models.AgentJob:
        """Create and persist a new queued job."""

        job = models.AgentJob(
            id=uuid4(),
            type=job_type,
            status=models.AgentJobStatus.QUEUED,
            payload=payload,
            priority=priority,
            created_by_user_id=created_by_user_id,
            requested_by_user_id=requested_by_user_id,
            affinity_key=affinity_key,
            max_attempts=max_attempts,
            attempt=1,
            next_attempt_at=None,
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def get_job(self, job_id: UUID) -> Optional[models.AgentJob]:
        """Return a single job by id."""

        return await self._session.get(models.AgentJob, job_id)

    async def require_job(self, job_id: UUID) -> models.AgentJob:
        """Return an existing job or raise ``AgentJobNotFoundError``."""

        job = await self.get_job(job_id)
        if job is None:
            raise AgentJobNotFoundError(job_id)
        return job

    async def list_jobs(
        self,
        *,
        status: Optional[models.AgentJobStatus] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[models.AgentJob]:
        """Return jobs filtered by optional status and type."""

        if limit < 1:
            raise ValueError("limit must be at least 1")

        stmt: Select[tuple[models.AgentJob]] = select(models.AgentJob)
        if status is not None:
            stmt = stmt.where(models.AgentJob.status == status)
        if job_type is not None:
            stmt = stmt.where(models.AgentJob.type == job_type)

        stmt = stmt.order_by(
            models.AgentJob.created_at.desc(),
            models.AgentJob.id.desc(),
        ).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: Optional[list[str]] = None,
        allowed_repositories: Optional[list[str]] = None,
        worker_capabilities: Optional[list[str]] = None,
    ) -> Optional[models.AgentJob]:
        """Claim the next eligible queued job for a worker."""

        now = datetime.now(UTC)
        await self._requeue_expired_jobs(now=now)

        base_stmt: Select[tuple[models.AgentJob]] = select(models.AgentJob).where(
            models.AgentJob.status == models.AgentJobStatus.QUEUED,
            or_(
                models.AgentJob.next_attempt_at.is_(None),
                models.AgentJob.next_attempt_at <= now,
            ),
        )
        if allowed_types:
            base_stmt = base_stmt.where(models.AgentJob.type.in_(allowed_types))

        batch_size = 200
        cursor_priority: int | None = None
        cursor_created_at: datetime | None = None
        cursor_id: UUID | None = None
        while True:
            stmt = base_stmt
            if (
                cursor_priority is not None
                and cursor_created_at is not None
                and cursor_id is not None
            ):
                stmt = stmt.where(
                    or_(
                        models.AgentJob.priority < cursor_priority,
                        and_(
                            models.AgentJob.priority == cursor_priority,
                            models.AgentJob.created_at > cursor_created_at,
                        ),
                        and_(
                            models.AgentJob.priority == cursor_priority,
                            models.AgentJob.created_at == cursor_created_at,
                            models.AgentJob.id > cursor_id,
                        ),
                    )
                )

            stmt = (
                stmt.order_by(
                    models.AgentJob.priority.desc(),
                    models.AgentJob.created_at.asc(),
                    models.AgentJob.id.asc(),
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await self._session.execute(stmt)
            queued_jobs = list(result.scalars().all())
            if not queued_jobs:
                break

            for candidate in queued_jobs:
                if not self._is_job_claim_eligible(
                    candidate,
                    allowed_repositories=allowed_repositories,
                    worker_capabilities=worker_capabilities,
                ):
                    continue

                lease_expires_at = now + timedelta(seconds=lease_seconds)
                claim_result = await self._session.execute(
                    update(models.AgentJob)
                    .where(
                        models.AgentJob.id == candidate.id,
                        models.AgentJob.status == models.AgentJobStatus.QUEUED,
                        or_(
                            models.AgentJob.next_attempt_at.is_(None),
                            models.AgentJob.next_attempt_at <= now,
                        ),
                    )
                    .values(
                        status=models.AgentJobStatus.RUNNING,
                        claimed_by=worker_id,
                        lease_expires_at=lease_expires_at,
                        next_attempt_at=None,
                        started_at=func.coalesce(models.AgentJob.started_at, now),
                        updated_at=now,
                    )
                )
                if claim_result.rowcount == 1:
                    # Bulk updates can expire ORM fields; refresh before returning.
                    await self._session.flush()
                    await self._session.refresh(candidate)
                    return candidate

            last_candidate = queued_jobs[-1]
            cursor_priority = last_candidate.priority
            cursor_created_at = last_candidate.created_at
            cursor_id = last_candidate.id
            if len(queued_jobs) < batch_size:
                break

        return None

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> models.AgentJob:
        """Extend lease for a running job claimed by the worker."""

        now = datetime.now(UTC)
        job = await self._require_running_owned_job(job_id=job_id, worker_id=worker_id)
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        await self._session.flush()
        return job

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: Optional[str] = None,
    ) -> models.AgentJob:
        """Mark a running job as succeeded."""

        now = datetime.now(UTC)
        job = await self._require_running_owned_job(job_id=job_id, worker_id=worker_id)
        job.status = models.AgentJobStatus.SUCCEEDED
        job.result_summary = result_summary
        job.finished_at = now
        job.claimed_by = None
        job.lease_expires_at = None
        job.next_attempt_at = None
        job.updated_at = now
        await self._session.flush()
        return job

    async def fail_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_message: str,
        retryable: bool = False,
        retry_delay_seconds: Optional[int] = None,
    ) -> models.AgentJob:
        """Mark a running job as failed or requeue it if retryable."""

        now = datetime.now(UTC)
        job = await self._require_running_owned_job(job_id=job_id, worker_id=worker_id)
        job.error_message = error_message

        if retryable and job.attempt < job.max_attempts:
            delay_seconds = max(
                1, int(retry_delay_seconds or self._lease_retry_delay_seconds)
            )
            job.status = models.AgentJobStatus.QUEUED
            job.attempt += 1
            job.claimed_by = None
            job.lease_expires_at = None
            job.finished_at = None
            job.next_attempt_at = now + timedelta(seconds=delay_seconds)
        else:
            job.status = (
                models.AgentJobStatus.DEAD_LETTER
                if retryable and job.attempt >= job.max_attempts
                else models.AgentJobStatus.FAILED
            )
            job.finished_at = now
            job.claimed_by = None
            job.lease_expires_at = None
            job.next_attempt_at = None

        job.updated_at = now
        await self._session.flush()
        return job

    async def create_artifact(
        self,
        *,
        job_id: UUID,
        name: str,
        storage_path: str,
        size_bytes: int,
        content_type: Optional[str] = None,
        digest: Optional[str] = None,
    ) -> models.AgentJobArtifact:
        """Persist artifact metadata for a queue job."""

        await self.require_job(job_id)
        artifact = models.AgentJobArtifact(
            id=uuid4(),
            job_id=job_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            digest=digest,
            storage_path=storage_path,
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def list_artifacts(
        self,
        *,
        job_id: UUID,
        limit: int = 200,
    ) -> list[models.AgentJobArtifact]:
        """List artifacts for a job ordered by creation time."""

        await self.require_job(job_id)
        if limit < 1:
            raise ValueError("limit must be at least 1")

        stmt: Select[tuple[models.AgentJobArtifact]] = (
            select(models.AgentJobArtifact)
            .where(models.AgentJobArtifact.job_id == job_id)
            .order_by(
                models.AgentJobArtifact.created_at.desc(),
                models.AgentJobArtifact.id.desc(),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_artifact(self, artifact_id: UUID) -> models.AgentJobArtifact:
        """Return artifact by id or raise not found."""

        artifact = await self._session.get(models.AgentJobArtifact, artifact_id)
        if artifact is None:
            raise AgentArtifactNotFoundError(artifact_id)
        return artifact

    async def get_artifact_for_job(
        self,
        *,
        job_id: UUID,
        artifact_id: UUID,
    ) -> models.AgentJobArtifact:
        """Return artifact scoped to job id or raise mismatch errors."""

        await self.require_job(job_id)
        artifact = await self.get_artifact(artifact_id)
        if artifact.job_id != job_id:
            raise AgentArtifactJobMismatchError(artifact_id, job_id)
        return artifact

    async def append_event(
        self,
        *,
        job_id: UUID,
        level: models.AgentJobEventLevel,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> models.AgentJobEvent:
        """Append one lifecycle/progress event for a queue job."""

        await self.require_job(job_id)
        now = datetime.now(UTC)
        event = models.AgentJobEvent(
            id=uuid4(),
            job_id=job_id,
            level=level,
            message=message,
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_events(
        self,
        *,
        job_id: UUID,
        limit: int = 200,
        after: Optional[datetime] = None,
    ) -> list[models.AgentJobEvent]:
        """Return ordered events for one queue job with optional cursor."""

        await self.require_job(job_id)
        if limit < 1:
            raise ValueError("limit must be at least 1")

        stmt: Select[tuple[models.AgentJobEvent]] = select(models.AgentJobEvent).where(
            models.AgentJobEvent.job_id == job_id
        )
        if after is not None:
            stmt = stmt.where(models.AgentJobEvent.created_at > after)

        stmt = stmt.order_by(
            models.AgentJobEvent.created_at.asc(),
            models.AgentJobEvent.id.asc(),
        ).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_worker_token(
        self,
        *,
        worker_id: str,
        token_hash: str,
        description: Optional[str] = None,
        allowed_repositories: Optional[list[str]] = None,
        allowed_job_types: Optional[list[str]] = None,
        capabilities: Optional[list[str]] = None,
    ) -> models.AgentWorkerToken:
        """Persist one worker token metadata row."""

        token = models.AgentWorkerToken(
            id=uuid4(),
            worker_id=worker_id,
            token_hash=token_hash,
            description=description,
            allowed_repositories=allowed_repositories,
            allowed_job_types=allowed_job_types,
            capabilities=capabilities,
            is_active=True,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_worker_token(self, token_id: UUID) -> models.AgentWorkerToken:
        """Return worker token by id or raise not found."""

        token = await self._session.get(models.AgentWorkerToken, token_id)
        if token is None:
            raise AgentWorkerTokenNotFoundError(token_id)
        return token

    async def get_worker_token_by_hash(
        self,
        token_hash: str,
    ) -> Optional[models.AgentWorkerToken]:
        """Return worker token metadata by SHA-256 hash."""

        stmt: Select[tuple[models.AgentWorkerToken]] = select(
            models.AgentWorkerToken
        ).where(models.AgentWorkerToken.token_hash == token_hash)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_worker_tokens(
        self, *, limit: int = 200
    ) -> list[models.AgentWorkerToken]:
        """Return worker token metadata rows ordered by creation date."""

        if limit < 1:
            raise ValueError("limit must be at least 1")
        stmt: Select[tuple[models.AgentWorkerToken]] = (
            select(models.AgentWorkerToken)
            .order_by(
                models.AgentWorkerToken.created_at.desc(),
                models.AgentWorkerToken.id.desc(),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def revoke_worker_token(self, *, token_id: UUID) -> models.AgentWorkerToken:
        """Deactivate one worker token."""

        token = await self.get_worker_token(token_id)
        token.is_active = False
        token.updated_at = datetime.now(UTC)
        await self._session.flush()
        return token

    async def _requeue_expired_jobs(self, *, now: datetime) -> None:
        """Move expired running jobs back to queue or dead-letter by retry policy."""

        stmt: Select[tuple[models.AgentJob]] = (
            select(models.AgentJob)
            .where(
                models.AgentJob.status == models.AgentJobStatus.RUNNING,
                models.AgentJob.lease_expires_at.is_not(None),
                models.AgentJob.lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        expired_jobs = list(result.scalars().all())

        for job in expired_jobs:
            if job.attempt >= job.max_attempts:
                job.status = models.AgentJobStatus.DEAD_LETTER
                job.finished_at = now
                job.next_attempt_at = None
                if not job.error_message:
                    job.error_message = (
                        "Lease expired and max attempts reached before reclaim."
                    )
            else:
                job.status = models.AgentJobStatus.QUEUED
                job.attempt += 1
                job.finished_at = None
                job.next_attempt_at = now + timedelta(
                    seconds=self._lease_retry_delay_seconds
                )

            job.claimed_by = None
            job.lease_expires_at = None
            job.updated_at = now

        if expired_jobs:
            await self._session.flush()

    async def _require_running_owned_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
    ) -> models.AgentJob:
        """Return a running job claimed by worker or raise a typed error."""

        job = await self.get_job(job_id)
        if job is None:
            raise AgentJobNotFoundError(job_id)
        if job.status is not models.AgentJobStatus.RUNNING:
            raise AgentJobStateError(
                f"Job {job_id} is {job.status.value} and cannot be mutated"
            )
        if job.claimed_by != worker_id:
            raise AgentJobOwnershipError(
                f"Job {job_id} is owned by {job.claimed_by or 'none'}"
            )
        return job

    @staticmethod
    def _is_job_claim_eligible(
        job: models.AgentJob,
        *,
        allowed_repositories: Optional[list[str]],
        worker_capabilities: Optional[list[str]],
    ) -> bool:
        payload = job.payload or {}

        if allowed_repositories:
            repository = str(payload.get("repository", "")).strip()
            if repository not in set(allowed_repositories):
                return False

        required_caps_raw = payload.get("requiredCapabilities")
        if isinstance(required_caps_raw, list):
            required_caps = {
                str(item).strip() for item in required_caps_raw if str(item).strip()
            }
        else:
            required_caps = set()

        if required_caps:
            available_caps = {
                str(item).strip()
                for item in (worker_capabilities or [])
                if str(item).strip()
            }
            if not required_caps.issubset(available_caps):
                return False

        return True
