"""Repository helpers for agent queue persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Optional
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

    async def require_job_for_update(self, job_id: UUID) -> models.AgentJob:
        """Return an existing job row under ``FOR UPDATE`` lock or raise not found."""

        stmt: Select[tuple[models.AgentJob]] = (
            select(models.AgentJob)
            .where(models.AgentJob.id == job_id)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        job = result.scalars().first()
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

    async def request_cancel(
        self,
        *,
        job_id: UUID,
        requested_by_user_id: UUID | None,
        reason: str | None,
    ) -> tuple[models.AgentJob, str]:
        """Request cancellation for a queued or running job."""

        now = datetime.now(UTC)
        stmt: Select[tuple[models.AgentJob]] = (
            select(models.AgentJob)
            .where(models.AgentJob.id == job_id)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        job = result.scalars().first()
        if job is None:
            raise AgentJobNotFoundError(job_id)

        if job.status is models.AgentJobStatus.QUEUED:
            job.status = models.AgentJobStatus.CANCELLED
            job.cancel_requested_at = now
            job.cancel_requested_by_user_id = requested_by_user_id
            job.cancel_reason = reason
            job.finished_at = now
            job.claimed_by = None
            job.lease_expires_at = None
            job.next_attempt_at = None
            job.updated_at = now
            await self._session.flush()
            return (job, "queued_cancelled")

        if job.status is models.AgentJobStatus.RUNNING:
            if job.cancel_requested_at is not None:
                await self._session.flush()
                return (job, "noop_running_requested")
            job.cancel_requested_at = now
            job.cancel_requested_by_user_id = requested_by_user_id
            job.cancel_reason = reason
            job.updated_at = now
            await self._session.flush()
            return (job, "running_requested")

        if job.status is models.AgentJobStatus.CANCELLED:
            await self._session.flush()
            return (job, "noop_cancelled")

        raise AgentJobStateError(
            f"Job {job_id} is {job.status.value} and cannot be cancelled"
        )

    async def ack_cancel(
        self,
        *,
        job_id: UUID,
        worker_id: str,
    ) -> tuple[models.AgentJob, str]:
        """Acknowledge cancellation for a running job owned by the worker."""

        now = datetime.now(UTC)
        stmt: Select[tuple[models.AgentJob]] = (
            select(models.AgentJob)
            .where(models.AgentJob.id == job_id)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        job = result.scalars().first()
        if job is None:
            raise AgentJobNotFoundError(job_id)

        if job.status is models.AgentJobStatus.CANCELLED:
            if job.claimed_by not in {None, worker_id}:
                raise AgentJobOwnershipError(
                    f"Job {job_id} is owned by {job.claimed_by or 'none'}"
                )
            await self._session.flush()
            return (job, "noop_cancelled")

        if job.status is not models.AgentJobStatus.RUNNING:
            raise AgentJobStateError(
                f"Job {job_id} is {job.status.value} and cannot be cancellation-acked"
            )
        if job.claimed_by != worker_id:
            raise AgentJobOwnershipError(
                f"Job {job_id} is owned by {job.claimed_by or 'none'}"
            )
        if job.cancel_requested_at is None:
            raise AgentJobStateError(
                f"Job {job_id} has no cancellation request to acknowledge"
            )

        job.status = models.AgentJobStatus.CANCELLED
        job.finished_at = now
        job.claimed_by = None
        job.lease_expires_at = None
        job.next_attempt_at = None
        job.updated_at = now
        await self._session.flush()
        return (job, "acknowledged")

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

        if job.cancel_requested_at is not None:
            job.status = models.AgentJobStatus.CANCELLED
            job.finished_at = now
            job.claimed_by = None
            job.lease_expires_at = None
            job.next_attempt_at = None
            job.updated_at = now
            await self._session.flush()
            return job

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
        after_event_id: UUID | None = None,
        before: Optional[datetime] = None,
        before_event_id: UUID | None = None,
        sort: Literal["asc", "desc"] = "asc",
    ) -> list[models.AgentJobEvent]:
        """Return ordered events for one queue job with optional cursor."""

        await self.require_job(job_id)
        if limit < 1:
            raise ValueError("limit must be at least 1")

        stmt: Select[tuple[models.AgentJobEvent]] = select(models.AgentJobEvent).where(
            models.AgentJobEvent.job_id == job_id
        )
        if after is not None and after_event_id is not None:
            stmt = stmt.where(
                or_(
                    models.AgentJobEvent.created_at > after,
                    and_(
                        models.AgentJobEvent.created_at == after,
                        models.AgentJobEvent.id > after_event_id,
                    ),
                )
            )
        elif after is not None:
            stmt = stmt.where(models.AgentJobEvent.created_at > after)
        if before is not None and before_event_id is not None:
            stmt = stmt.where(
                or_(
                    models.AgentJobEvent.created_at < before,
                    and_(
                        models.AgentJobEvent.created_at == before,
                        models.AgentJobEvent.id < before_event_id,
                    ),
                )
            )
        elif before is not None:
            stmt = stmt.where(models.AgentJobEvent.created_at < before)

        if sort == "desc":
            stmt = stmt.order_by(
                models.AgentJobEvent.created_at.desc(),
                models.AgentJobEvent.id.desc(),
            )
        else:
            stmt = stmt.order_by(
                models.AgentJobEvent.created_at.asc(),
                models.AgentJobEvent.id.asc(),
            )
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_live_session(
        self, *, task_run_id: UUID
    ) -> models.TaskRunLiveSession | None:
        """Return one live-session row for the task run when present."""

        stmt: Select[tuple[models.TaskRunLiveSession]] = select(
            models.TaskRunLiveSession
        ).where(models.TaskRunLiveSession.task_run_id == task_run_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def upsert_live_session(
        self,
        *,
        task_run_id: UUID,
        provider: models.AgentJobLiveSessionProvider | None = None,
        status: models.AgentJobLiveSessionStatus | None = None,
        ready_at: datetime | None = None,
        ended_at: datetime | None = None,
        expires_at: datetime | None = None,
        worker_id: str | None = None,
        worker_hostname: str | None = None,
        tmate_session_name: str | None = None,
        tmate_socket_path: str | None = None,
        attach_ro: str | None = None,
        attach_rw: str | None = None,
        web_ro: str | None = None,
        web_rw: str | None = None,
        rw_granted_until: datetime | None = None,
        last_heartbeat_at: datetime | None = None,
        error_message: str | None = None,
    ) -> models.TaskRunLiveSession:
        """Create or update one live-session row scoped to a task run."""

        await self.require_job_for_update(task_run_id)
        now = datetime.now(UTC)
        live = await self.get_live_session(task_run_id=task_run_id)
        if live is None:
            live = models.TaskRunLiveSession(
                id=uuid4(),
                task_run_id=task_run_id,
                provider=(provider or models.AgentJobLiveSessionProvider.TMATE),
                status=(status or models.AgentJobLiveSessionStatus.DISABLED),
            )
            self._session.add(live)

        if provider is not None:
            live.provider = provider
        if status is not None:
            live.status = status
        if ready_at is not None:
            live.ready_at = ready_at
        elif status is models.AgentJobLiveSessionStatus.READY and live.ready_at is None:
            live.ready_at = now
        if ended_at is not None:
            live.ended_at = ended_at
        elif (
            status
            in {
                models.AgentJobLiveSessionStatus.REVOKED,
                models.AgentJobLiveSessionStatus.ENDED,
                models.AgentJobLiveSessionStatus.ERROR,
            }
            and live.ended_at is None
        ):
            live.ended_at = now
        if expires_at is not None:
            live.expires_at = expires_at
        if worker_id is not None:
            live.worker_id = worker_id
        if worker_hostname is not None:
            live.worker_hostname = worker_hostname
        if tmate_session_name is not None:
            live.tmate_session_name = tmate_session_name
        if tmate_socket_path is not None:
            live.tmate_socket_path = tmate_socket_path
        if attach_ro is not None:
            live.attach_ro = attach_ro
        if attach_rw is not None:
            live.attach_rw_encrypted = attach_rw
        if web_ro is not None:
            live.web_ro = web_ro or None
        if web_rw is not None:
            live.web_rw_encrypted = web_rw or None
        if rw_granted_until is not None:
            live.rw_granted_until = rw_granted_until
        if last_heartbeat_at is not None:
            live.last_heartbeat_at = last_heartbeat_at
        if error_message is not None:
            live.error_message = error_message
        live.updated_at = now
        await self._session.flush()
        return live

    async def append_control_event(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        metadata_json: dict[str, Any] | None = None,
    ) -> models.TaskRunControlEvent:
        """Append one control/audit event for the task run."""

        await self.require_job(task_run_id)
        now = datetime.now(UTC)
        event = models.TaskRunControlEvent(
            id=uuid4(),
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            action=action,
            metadata_json=metadata_json,
            created_at=now,
            updated_at=now,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def set_job_live_control(
        self,
        *,
        task_run_id: UUID,
        paused: bool | None = None,
        takeover: bool | None = None,
        last_action: str | None = None,
    ) -> models.AgentJob:
        """Upsert live control flags in the queue payload for worker heartbeat reads."""

        now = datetime.now(UTC)
        job = await self.require_job(task_run_id)
        payload = dict(job.payload or {})
        raw_control = payload.get("liveControl")
        control = dict(raw_control) if isinstance(raw_control, dict) else {}
        if paused is not None:
            control["paused"] = bool(paused)
        if takeover is not None:
            control["takeover"] = bool(takeover)
        if last_action:
            control["lastAction"] = last_action
        control["updatedAt"] = now.isoformat()
        payload["liveControl"] = control
        job.payload = payload
        job.updated_at = now
        await self._session.flush()
        return job

    async def list_jobs_for_telemetry(
        self,
        *,
        since: Optional[datetime] = None,
        limit: int = 5000,
    ) -> list[models.AgentJob]:
        """Return recently created jobs for migration telemetry snapshots."""

        if limit < 1:
            raise ValueError("limit must be at least 1")

        stmt: Select[tuple[models.AgentJob]] = select(models.AgentJob)
        if since is not None:
            stmt = stmt.where(models.AgentJob.created_at >= since)
        stmt = stmt.order_by(
            models.AgentJob.created_at.desc(),
            models.AgentJob.id.desc(),
        ).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_events_for_jobs(
        self,
        *,
        job_ids: list[UUID],
        since: Optional[datetime] = None,
        limit: int = 100000,
    ) -> list[models.AgentJobEvent]:
        """Return events for a set of jobs ordered by job and event time."""

        if not job_ids:
            return []
        if limit < 1:
            raise ValueError("limit must be at least 1")

        stmt: Select[tuple[models.AgentJobEvent]] = select(models.AgentJobEvent).where(
            models.AgentJobEvent.job_id.in_(job_ids)
        )
        if since is not None:
            stmt = stmt.where(models.AgentJobEvent.created_at >= since)
        stmt = stmt.order_by(
            models.AgentJobEvent.job_id.asc(),
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
            if job.cancel_requested_at is not None:
                job.status = models.AgentJobStatus.CANCELLED
                job.finished_at = now
                job.next_attempt_at = None
            elif job.attempt >= job.max_attempts:
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
                str(item).strip().lower()
                for item in required_caps_raw
                if str(item).strip()
            }
        else:
            required_caps = set()

        if not required_caps:
            return False

        available_caps = {
            str(item).strip().lower()
            for item in (worker_capabilities or [])
            if str(item).strip()
        }
        if not required_caps.issubset(available_caps):
            return False

        return True

    async def get_pause_state(self) -> models.SystemWorkerPauseState:
        """Return the singleton worker pause state, creating it if missing."""

        state = await self._session.get(models.SystemWorkerPauseState, 1)
        if state is None:
            state = models.SystemWorkerPauseState(id=1, paused=False, version=1)
            self._session.add(state)
            await self._session.flush()
        return state

    async def get_pause_state_for_update(self) -> models.SystemWorkerPauseState:
        """Return the singleton worker pause state row under FOR UPDATE lock."""

        stmt: Select[tuple[models.SystemWorkerPauseState]] = (
            select(models.SystemWorkerPauseState)
            .where(models.SystemWorkerPauseState.id == 1)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        state = result.scalars().first()
        if state is None:
            state = models.SystemWorkerPauseState(id=1, paused=False, version=1)
            self._session.add(state)
            await self._session.flush()
        return state

    async def update_pause_state(
        self,
        *,
        paused: bool,
        mode: models.WorkerPauseMode | None,
        reason: str | None,
        requested_by_user_id: UUID | None,
        requested_at: datetime | None,
    ) -> models.SystemWorkerPauseState:
        """Update the singleton pause state and bump its version."""

        state = await self.get_pause_state_for_update()
        state.paused = paused
        state.mode = mode
        state.reason = reason
        state.requested_by_user_id = requested_by_user_id
        state.requested_at = requested_at
        state.updated_at = datetime.now(UTC)
        state.version = int(state.version or 0) + 1
        await self._session.flush()
        return state

    async def append_system_control_event(
        self,
        *,
        action: str,
        mode: models.WorkerPauseMode | None,
        reason: str | None,
        actor_user_id: UUID | None,
    ) -> models.SystemControlEvent:
        """Append one audit entry for worker pause controls."""

        event = models.SystemControlEvent(
            id=uuid4(),
            control="worker_pause",
            action=action,
            mode=mode,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_system_control_events(
        self,
        *,
        limit: int = 5,
    ) -> list[models.SystemControlEvent]:
        """Return recent system control audit entries."""

        limit = max(1, min(limit, 50))
        stmt: Select[tuple[models.SystemControlEvent]] = (
            select(models.SystemControlEvent)
            .where(models.SystemControlEvent.control == "worker_pause")
            .order_by(
                models.SystemControlEvent.created_at.desc(),
                models.SystemControlEvent.id.desc(),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def fetch_worker_pause_metrics(self) -> dict[str, int]:
        """Return queued/running/stale-running metrics for worker pause banner."""

        now = datetime.now(UTC)
        queued_stmt = select(func.count()).select_from(models.AgentJob).where(
            models.AgentJob.status == models.AgentJobStatus.QUEUED,
            or_(
                models.AgentJob.next_attempt_at.is_(None),
                models.AgentJob.next_attempt_at <= now,
            ),
        )
        running_stmt = select(func.count()).select_from(models.AgentJob).where(
            models.AgentJob.status == models.AgentJobStatus.RUNNING
        )
        stale_stmt = select(func.count()).select_from(models.AgentJob).where(
            models.AgentJob.status == models.AgentJobStatus.RUNNING,
            or_(
                models.AgentJob.lease_expires_at.is_(None),
                models.AgentJob.lease_expires_at < now,
            ),
        )
        queued_result = await self._session.execute(queued_stmt)
        running_result = await self._session.execute(running_stmt)
        stale_result = await self._session.execute(stale_stmt)
        return {
            "queued": int(queued_result.scalar_one() or 0),
            "running": int(running_result.scalar_one() or 0),
            "stale_running": int(stale_result.scalar_one() or 0),
        }
