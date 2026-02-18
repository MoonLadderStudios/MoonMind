"""Service layer for Agent Queue operations."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit
from uuid import UUID

from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import (
    AgentQueueRepository,
    AgentWorkerTokenNotFoundError,
)
from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage
from moonmind.workflows.agent_queue.task_contract import (
    CANONICAL_TASK_JOB_TYPE,
    LEGACY_TASK_JOB_TYPES,
    SUPPORTED_EXECUTION_RUNTIMES,
    TaskContractError,
    normalize_queue_job_payload,
)
from moonmind.workflows.tasks import compile_task_payload_templates

logger = logging.getLogger(__name__)
_SUPPORTED_QUEUE_JOB_TYPES = {CANONICAL_TASK_JOB_TYPE, *LEGACY_TASK_JOB_TYPES}
_TELEMETRY_EVENT_FETCH_LIMIT = 100000
_DEFAULT_TASK_RUNTIME = "codex"
_DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
_DEFAULT_CODEX_EFFORT = "high"
_DEFAULT_REPOSITORY = "MoonLadderStudios/MoonMind"
_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class AgentQueueValidationError(ValueError):
    """Raised when client-supplied queue payloads are invalid."""


class AgentQueueAuthenticationError(RuntimeError):
    """Raised when worker authentication fails."""


class AgentQueueAuthorizationError(RuntimeError):
    """Raised when authenticated workers exceed allowed policy scope."""


class AgentQueueJobAuthorizationError(RuntimeError):
    """Raised when authenticated users attempt to access unauthorized jobs."""


class LiveSessionNotFoundError(AgentQueueValidationError):
    """Raised when a task run has no persisted live-session state."""


class LiveSessionStateError(AgentQueueValidationError):
    """Raised when the current live-session state cannot satisfy a request."""


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    """Represents a download-ready artifact with file path and metadata."""

    artifact: models.AgentJobArtifact
    file_path: Path


@dataclass(frozen=True, slots=True)
class WorkerAuthPolicy:
    """Resolved worker identity and policy constraints."""

    worker_id: str
    auth_source: str
    token_id: UUID | None
    allowed_repositories: tuple[str, ...]
    allowed_job_types: tuple[str, ...]
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkerTokenIssueResult:
    """One-time worker token issuance payload."""

    token_record: models.AgentWorkerToken
    raw_token: str


@dataclass(frozen=True, slots=True)
class LiveSessionWriteGrant:
    """RW reveal response returned by grant-write operations."""

    session: models.TaskRunLiveSession
    attach_rw: str
    web_rw: str | None
    granted_until: datetime


@dataclass(frozen=True, slots=True)
class QueueMigrationTelemetry:
    """Aggregated migration telemetry snapshot for queue rollout tracking."""

    generated_at: datetime
    window_hours: int
    total_jobs: int
    job_volume_by_type: dict[str, int]
    failure_counts_by_runtime_stage: list[dict[str, Any]]
    publish_outcomes: dict[str, Any]
    legacy_job_submissions: int
    events_truncated: bool


class AgentQueueService:
    """Application service exposing validated queue operations."""

    def __init__(
        self,
        repository: AgentQueueRepository,
        *,
        artifact_storage: AgentQueueArtifactStorage | None = None,
        artifact_max_bytes: int | None = None,
        retry_backoff_base_seconds: int = 15,
        retry_backoff_max_seconds: int = 600,
    ) -> None:
        self._repository = repository
        self._artifact_storage = artifact_storage or AgentQueueArtifactStorage(
            settings.spec_workflow.agent_job_artifact_root
        )
        configured_limit = (
            artifact_max_bytes
            if artifact_max_bytes is not None
            else settings.spec_workflow.agent_job_artifact_max_bytes
        )
        self._artifact_max_bytes = max(1, int(configured_limit))
        self._retry_backoff_base_seconds = max(1, int(retry_backoff_base_seconds))
        self._retry_backoff_max_seconds = max(
            self._retry_backoff_base_seconds,
            int(retry_backoff_max_seconds),
        )

    @staticmethod
    def _clean_optional_str(value: object) -> str | None:
        """Return trimmed string or ``None`` for blank values."""

        text = str(value).strip() if value is not None else ""
        return text or None

    @classmethod
    def _validate_repository_reference(cls, repository: str) -> None:
        """Validate repository values align with accepted worker clone formats."""

        if _OWNER_REPO_PATTERN.fullmatch(repository):
            return
        if repository.startswith("http://") or repository.startswith("https://"):
            parsed = urlsplit(repository)
            if parsed.username is not None or parsed.password is not None:
                raise AgentQueueValidationError(
                    "repository URL must not include embedded credentials"
                )
            if not parsed.netloc or not parsed.path or parsed.path == "/":
                raise AgentQueueValidationError(
                    "repository URL must include a host and repository path"
                )
            return
        if repository.startswith("git@"):
            return
        raise AgentQueueValidationError(
            "repository must be owner/repo, https://<host>/<path>, or git@<host>:<path>"
        )

    def _enrich_task_payload_defaults(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fill missing canonical task payload fields from configured defaults."""

        enriched = dict(payload)
        repository = self._clean_optional_str(
            enriched.get("repository") or enriched.get("repo")
        )
        if repository is None:
            repository = (
                self._clean_optional_str(settings.spec_workflow.github_repository)
                or _DEFAULT_REPOSITORY
            )
        self._validate_repository_reference(repository)
        enriched["repository"] = repository

        task_node = enriched.get("task")
        task = dict(task_node) if isinstance(task_node, dict) else {}
        runtime_node = task.get("runtime")
        runtime = dict(runtime_node) if isinstance(runtime_node, dict) else {}

        runtime_mode = (
            self._clean_optional_str(runtime.get("mode"))
            or self._clean_optional_str(enriched.get("targetRuntime"))
            or self._clean_optional_str(enriched.get("target_runtime"))
            or _DEFAULT_TASK_RUNTIME
        ).lower()
        runtime["mode"] = runtime_mode
        enriched["targetRuntime"] = runtime_mode

        if runtime_mode == "codex":
            if self._clean_optional_str(runtime.get("model")) is None:
                runtime["model"] = (
                    self._clean_optional_str(settings.spec_workflow.codex_model)
                    or _DEFAULT_CODEX_MODEL
                )
            if self._clean_optional_str(runtime.get("effort")) is None:
                runtime["effort"] = (
                    self._clean_optional_str(settings.spec_workflow.codex_effort)
                    or _DEFAULT_CODEX_EFFORT
                )

        task["runtime"] = runtime
        enriched["task"] = task
        return enriched

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

        candidate_type = job_type.strip()
        if not candidate_type:
            raise AgentQueueValidationError("type must be a non-empty string")
        if candidate_type not in _SUPPORTED_QUEUE_JOB_TYPES:
            supported = ", ".join(sorted(_SUPPORTED_QUEUE_JOB_TYPES))
            raise AgentQueueValidationError(f"type must be one of: {supported}")
        if max_attempts < 1:
            raise AgentQueueValidationError("maxAttempts must be >= 1")

        normalized_payload = dict(payload or {})
        if candidate_type == CANONICAL_TASK_JOB_TYPE:
            normalized_payload = self._enrich_task_payload_defaults(normalized_payload)
            normalized_payload = compile_task_payload_templates(normalized_payload)
        try:
            normalized_payload = normalize_queue_job_payload(
                job_type=candidate_type,
                payload=normalized_payload,
            )
        except TaskContractError as exc:
            raise AgentQueueValidationError(str(exc)) from exc

        job = await self._repository.create_job(
            job_type=candidate_type,
            payload=normalized_payload,
            priority=priority,
            created_by_user_id=created_by_user_id,
            requested_by_user_id=requested_by_user_id,
            affinity_key=affinity_key.strip() if affinity_key else None,
            max_attempts=max_attempts,
        )
        await self._repository.append_event(
            job_id=job.id,
            level=models.AgentJobEventLevel.INFO,
            message="Job queued",
            payload={
                "type": candidate_type,
                "createdByUserId": (
                    str(created_by_user_id) if created_by_user_id is not None else None
                ),
                "requestedByUserId": (
                    str(requested_by_user_id)
                    if requested_by_user_id is not None
                    else None
                ),
            },
        )
        if candidate_type in LEGACY_TASK_JOB_TYPES:
            warning_payload = {
                "jobType": candidate_type,
                "recommendedType": "task",
                "migrationPhase": "phase4",
            }
            await self._repository.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.WARN,
                message="Legacy job type submitted",
                payload=warning_payload,
            )
            logger.warning(
                "Legacy agent queue job submission detected: job_id=%s type=%s",
                job.id,
                candidate_type,
            )
        await self._repository.commit()
        return job

    async def get_job(self, job_id: UUID) -> Optional[models.AgentJob]:
        """Fetch a single job by id."""

        return await self._repository.get_job(job_id)

    async def list_jobs(
        self,
        *,
        status: Optional[models.AgentJobStatus] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[models.AgentJob]:
        """List queue jobs with optional filters."""

        if limit < 1 or limit > 200:
            raise AgentQueueValidationError("limit must be between 1 and 200")

        normalized_type = job_type.strip() if job_type else None
        return await self._repository.list_jobs(
            status=status,
            job_type=normalized_type if normalized_type else None,
            limit=limit,
        )

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: Optional[list[str]] = None,
        allowed_repositories: Optional[list[str]] = None,
        worker_capabilities: Optional[list[str]] = None,
    ) -> Optional[models.AgentJob]:
        """Claim a queued job for a worker."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        if lease_seconds < 1:
            raise AgentQueueValidationError("leaseSeconds must be >= 1")

        normalized_types = self._normalize_str_list(allowed_types)
        normalized_repositories = self._normalize_str_list(allowed_repositories)
        normalized_capabilities = self._normalize_str_list(worker_capabilities)

        job = await self._repository.claim_job(
            worker_id=worker,
            lease_seconds=lease_seconds,
            allowed_types=list(normalized_types) if normalized_types else None,
            allowed_repositories=(
                list(normalized_repositories) if normalized_repositories else None
            ),
            worker_capabilities=(
                list(normalized_capabilities) if normalized_capabilities else None
            ),
        )

        if job is not None:
            await self._repository.append_event(
                job_id=job.id,
                level=models.AgentJobEventLevel.INFO,
                message="Job claimed",
                payload={"workerId": worker},
            )
        await self._repository.commit()
        return job

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> models.AgentJob:
        """Extend the lease for a running job."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        if lease_seconds < 1:
            raise AgentQueueValidationError("leaseSeconds must be >= 1")

        job = await self._repository.heartbeat(
            job_id=job_id,
            worker_id=worker,
            lease_seconds=lease_seconds,
        )
        await self._repository.append_event(
            job_id=job_id,
            level=models.AgentJobEventLevel.INFO,
            message="Heartbeat received",
            payload={"workerId": worker, "leaseSeconds": lease_seconds},
        )
        await self._repository.commit()
        return job

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: Optional[str] = None,
    ) -> models.AgentJob:
        """Mark a running job as completed."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")

        job = await self._repository.complete_job(
            job_id=job_id,
            worker_id=worker,
            result_summary=result_summary.strip() if result_summary else None,
        )
        await self._repository.append_event(
            job_id=job_id,
            level=models.AgentJobEventLevel.INFO,
            message="Job completed",
            payload={"workerId": worker},
        )
        await self._repository.commit()
        return job

    async def fail_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_message: str,
        retryable: bool = False,
    ) -> models.AgentJob:
        """Mark a running job as failed."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        detail = error_message.strip()
        if not detail:
            raise AgentQueueValidationError("errorMessage must be a non-empty string")

        current_job = await self._repository.require_job(job_id)
        retry_delay_seconds = None
        if retryable and current_job.attempt < current_job.max_attempts:
            retry_delay_seconds = self._compute_retry_delay_seconds(current_job.attempt)

        job = await self._repository.fail_job(
            job_id=job_id,
            worker_id=worker,
            error_message=detail,
            retryable=retryable,
            retry_delay_seconds=retry_delay_seconds,
        )
        if job.status is models.AgentJobStatus.CANCELLED:
            await self._repository.append_event(
                job_id=job_id,
                level=models.AgentJobEventLevel.WARN,
                message="Job cancelled",
                payload={
                    "workerId": worker,
                    "source": "fail_job",
                    "reason": "cancellation_requested",
                },
            )
            await self._repository.commit()
            return job

        event_level = (
            models.AgentJobEventLevel.WARN
            if retryable
            else models.AgentJobEventLevel.ERROR
        )
        await self._repository.append_event(
            job_id=job_id,
            level=event_level,
            message="Job failed" if not retryable else "Job failed (retryable)",
            payload={
                "workerId": worker,
                "retryable": retryable,
                "status": job.status.value,
                "nextAttemptAt": (
                    job.next_attempt_at.isoformat() if job.next_attempt_at else None
                ),
            },
        )
        await self._repository.commit()
        return job

    async def request_cancel(
        self,
        *,
        job_id: UUID,
        requested_by_user_id: UUID | None,
        reason: str | None = None,
    ) -> models.AgentJob:
        """Request cancellation for one queue job."""

        clean_reason = reason.strip() if reason else None
        if clean_reason == "":
            clean_reason = None

        job, action = await self._repository.request_cancel(
            job_id=job_id,
            requested_by_user_id=requested_by_user_id,
            reason=clean_reason,
        )
        if action == "queued_cancelled":
            await self._repository.append_event(
                job_id=job_id,
                level=models.AgentJobEventLevel.INFO,
                message="Job cancelled",
                payload={
                    "requestedByUserId": (
                        str(requested_by_user_id)
                        if requested_by_user_id is not None
                        else None
                    ),
                    "reason": clean_reason,
                },
            )
        elif action == "running_requested":
            await self._repository.append_event(
                job_id=job_id,
                level=models.AgentJobEventLevel.WARN,
                message="Cancellation requested",
                payload={
                    "requestedByUserId": (
                        str(requested_by_user_id)
                        if requested_by_user_id is not None
                        else None
                    ),
                    "reason": clean_reason,
                },
            )
        await self._repository.commit()
        return job

    async def ack_cancel(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        message: str | None = None,
    ) -> models.AgentJob:
        """Acknowledge cancellation for a running job owned by worker."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        detail = message.strip() if message else None
        if detail == "":
            detail = None

        job, action = await self._repository.ack_cancel(job_id=job_id, worker_id=worker)
        if action == "acknowledged":
            await self._repository.append_event(
                job_id=job_id,
                level=models.AgentJobEventLevel.INFO,
                message="Job cancelled",
                payload={"workerId": worker, "message": detail},
            )
        await self._repository.commit()
        return job

    async def upload_artifact(
        self,
        *,
        job_id: UUID,
        name: str,
        data: bytes,
        content_type: Optional[str] = None,
        digest: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> models.AgentJobArtifact:
        """Validate and persist an uploaded artifact for a job."""

        artifact_name = name.strip()
        if not artifact_name:
            raise AgentQueueValidationError("name must be a non-empty string")
        if not data:
            raise AgentQueueValidationError("file must not be empty")
        if len(data) > self._artifact_max_bytes:
            raise AgentQueueValidationError(
                f"artifact exceeds max bytes ({self._artifact_max_bytes})"
            )

        # Validate job existence before writing bytes so missing jobs do not
        # leave orphaned files on disk.
        job = await self._repository.require_job(job_id)

        if worker_id is not None:
            worker = worker_id.strip()
            if not worker:
                raise AgentQueueValidationError("workerId must be a non-empty string")
            if (
                job.status is not models.AgentJobStatus.RUNNING
                or job.claimed_by != worker
            ):
                raise AgentQueueAuthorizationError(
                    f"worker '{worker}' does not own an active claim for job {job_id}"
                )

        try:
            _, storage_path = self._artifact_storage.write_artifact(
                job_id=job_id,
                artifact_name=artifact_name,
                data=data,
            )
        except ValueError as exc:
            raise AgentQueueValidationError(str(exc)) from exc

        artifact = await self._repository.create_artifact(
            job_id=job_id,
            name=artifact_name,
            content_type=content_type.strip() if content_type else None,
            digest=digest.strip() if digest else None,
            size_bytes=len(data),
            storage_path=storage_path,
        )
        await self._repository.append_event(
            job_id=job_id,
            level=models.AgentJobEventLevel.INFO,
            message="Artifact uploaded",
            payload={"name": artifact_name, "sizeBytes": len(data)},
        )
        await self._repository.commit()
        return artifact

    async def list_artifacts(
        self,
        *,
        job_id: UUID,
        limit: int = 200,
    ) -> list[models.AgentJobArtifact]:
        """Return artifact metadata list for a job."""

        if limit < 1 or limit > 500:
            raise AgentQueueValidationError("limit must be between 1 and 500")
        return await self._repository.list_artifacts(job_id=job_id, limit=limit)

    async def get_artifact_download(
        self,
        *,
        job_id: UUID,
        artifact_id: UUID,
    ) -> ArtifactDownload:
        """Return artifact metadata and resolved file path for download."""

        artifact = await self._repository.get_artifact_for_job(
            job_id=job_id,
            artifact_id=artifact_id,
        )
        try:
            file_path = self._artifact_storage.resolve_storage_path(
                artifact.storage_path
            )
        except ValueError as exc:
            raise AgentQueueValidationError(str(exc)) from exc
        if not file_path.exists():
            raise AgentQueueValidationError(
                f"artifact file does not exist on disk: {artifact.storage_path}"
            )
        return ArtifactDownload(artifact=artifact, file_path=file_path)

    async def append_event(
        self,
        *,
        job_id: UUID,
        level: models.AgentJobEventLevel,
        message: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> models.AgentJobEvent:
        """Append one queue event with basic validation."""

        detail = message.strip()
        if not detail:
            raise AgentQueueValidationError("message must be a non-empty string")
        event = await self._repository.append_event(
            job_id=job_id,
            level=level,
            message=detail,
            payload=payload,
        )
        await self._repository.commit()
        return event

    async def list_events(
        self,
        *,
        job_id: UUID,
        limit: int = 200,
        after: Optional[datetime] = None,
        after_event_id: UUID | None = None,
    ) -> list[models.AgentJobEvent]:
        """List queue events for one job."""

        if limit < 1 or limit > 500:
            raise AgentQueueValidationError("limit must be between 1 and 500")
        if after_event_id is not None and after is None:
            raise AgentQueueValidationError("afterEventId requires after timestamp")
        if after is not None and after.tzinfo is None:
            after = after.replace(tzinfo=UTC)
        return await self._repository.list_events(
            job_id=job_id,
            limit=limit,
            after=after,
            after_event_id=after_event_id,
        )

    async def get_live_session(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> models.TaskRunLiveSession | None:
        """Fetch current live session state for a task run."""

        if actor_user_id is None:
            await self._repository.require_job(task_run_id)
        else:
            await self._assert_task_run_user_access(
                task_run_id=task_run_id,
                actor_user_id=actor_user_id,
            )
        return await self._repository.get_live_session(task_run_id=task_run_id)

    async def create_live_session(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
    ) -> models.TaskRunLiveSession:
        """Create or enable live session tracking for a task run."""

        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
        )
        existing = await self._repository.get_live_session(task_run_id=task_run_id)
        if existing is not None and existing.status in {
            models.AgentJobLiveSessionStatus.STARTING,
            models.AgentJobLiveSessionStatus.READY,
        }:
            return existing

        provider = self._resolve_live_session_provider()
        now = datetime.now(UTC)
        ttl_minutes = max(1, int(settings.spec_workflow.live_session_ttl_minutes))
        live = await self._repository.upsert_live_session(
            task_run_id=task_run_id,
            provider=provider,
            status=models.AgentJobLiveSessionStatus.STARTING,
            expires_at=now + timedelta(minutes=ttl_minutes),
            error_message="",
        )
        await self._repository.append_control_event(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            action="create_session",
            metadata_json={
                "provider": provider.value,
                "expiresAt": (
                    live.expires_at.isoformat() if live.expires_at is not None else None
                ),
            },
        )
        await self._repository.append_event(
            job_id=task_run_id,
            level=models.AgentJobEventLevel.INFO,
            message="task.live_session",
            payload={
                "status": "starting",
                "provider": provider.value,
            },
        )
        await self._repository.commit()
        return live

    async def report_live_session(
        self,
        *,
        task_run_id: UUID,
        worker_id: str,
        worker_hostname: str | None,
        status: models.AgentJobLiveSessionStatus,
        provider: models.AgentJobLiveSessionProvider | None = None,
        attach_ro: str | None = None,
        attach_rw: str | None = None,
        web_ro: str | None = None,
        web_rw: str | None = None,
        tmate_session_name: str | None = None,
        tmate_socket_path: str | None = None,
        expires_at: datetime | None = None,
        error_message: str | None = None,
    ) -> models.TaskRunLiveSession:
        """Worker-side report/update hook for live session lifecycle and links."""

        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        await self._assert_live_session_worker_ownership(
            task_run_id=task_run_id,
            worker_id=normalized_worker_id,
            allow_terminal_report=status
            in {
                models.AgentJobLiveSessionStatus.REVOKED,
                models.AgentJobLiveSessionStatus.ENDED,
                models.AgentJobLiveSessionStatus.ERROR,
            },
        )
        allow_web = self._live_session_web_allowed()
        resolved_web_ro = (web_ro or "").strip() if allow_web else ""
        resolved_web_rw = (web_rw or "").strip() if allow_web else ""

        live = await self._repository.upsert_live_session(
            task_run_id=task_run_id,
            provider=provider or self._resolve_live_session_provider(),
            status=status,
            worker_id=normalized_worker_id,
            worker_hostname=(worker_hostname or "").strip() or None,
            attach_ro=(attach_ro or "").strip() or None,
            attach_rw=(attach_rw or "").strip() or None,
            web_ro=resolved_web_ro,
            web_rw=resolved_web_rw,
            tmate_session_name=(tmate_session_name or "").strip() or None,
            tmate_socket_path=(tmate_socket_path or "").strip() or None,
            expires_at=expires_at,
            last_heartbeat_at=datetime.now(UTC),
            error_message=(error_message or "").strip() or None,
        )
        await self._repository.append_event(
            job_id=task_run_id,
            level=(
                models.AgentJobEventLevel.ERROR
                if status is models.AgentJobLiveSessionStatus.ERROR
                else models.AgentJobEventLevel.INFO
            ),
            message="task.live_session.reported",
            payload={
                "status": status.value,
                "provider": live.provider.value,
                "workerId": normalized_worker_id,
                "attachRo": bool(live.attach_ro),
                "attachRw": bool(live.attach_rw_encrypted),
                "webRo": bool(live.web_ro),
                "webRw": bool(live.web_rw_encrypted),
                "error": live.error_message,
            },
        )
        await self._repository.commit()
        return live

    async def heartbeat_live_session(
        self,
        *,
        task_run_id: UUID,
        worker_id: str,
    ) -> models.TaskRunLiveSession:
        """Update live-session heartbeat timestamp."""

        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        await self._assert_live_session_worker_ownership(
            task_run_id=task_run_id,
            worker_id=normalized_worker_id,
            allow_terminal_report=False,
        )
        live = await self._repository.get_live_session(task_run_id=task_run_id)
        if live is None:
            raise LiveSessionNotFoundError("live session is not enabled for this task")
        updated = await self._repository.upsert_live_session(
            task_run_id=task_run_id,
            worker_id=normalized_worker_id,
            last_heartbeat_at=datetime.now(UTC),
        )
        await self._repository.commit()
        return updated

    async def grant_live_session_write(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
        ttl_minutes: int | None = None,
    ) -> LiveSessionWriteGrant:
        """Grant temporary RW reveal for an active live session."""

        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
        )
        live = await self._repository.get_live_session(task_run_id=task_run_id)
        if live is None:
            raise LiveSessionNotFoundError("live session is not enabled for this task")
        if live.status is not models.AgentJobLiveSessionStatus.READY:
            raise LiveSessionStateError("live session is not ready")
        attach_rw = str(live.attach_rw_encrypted or "").strip()
        if not attach_rw:
            raise LiveSessionStateError(
                "live session does not currently have an RW endpoint"
            )

        now = datetime.now(UTC)
        requested_ttl = (
            int(ttl_minutes)
            if ttl_minutes is not None
            else int(settings.spec_workflow.live_session_rw_grant_ttl_minutes)
        )
        effective_ttl = max(1, min(requested_ttl, 240))
        granted_until = now + timedelta(minutes=effective_ttl)
        updated = await self._repository.upsert_live_session(
            task_run_id=task_run_id,
            rw_granted_until=granted_until,
            last_heartbeat_at=now,
        )
        await self._repository.append_control_event(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            action="grant_rw",
            metadata_json={
                "ttlMinutes": effective_ttl,
                "grantedUntil": granted_until.isoformat(),
            },
        )
        await self._repository.append_event(
            job_id=task_run_id,
            level=models.AgentJobEventLevel.WARN,
            message="task.live_session.grant_write",
            payload={
                "grantedUntil": granted_until.isoformat(),
                "ttlMinutes": effective_ttl,
            },
        )
        await self._repository.commit()
        return LiveSessionWriteGrant(
            session=updated,
            attach_rw=attach_rw,
            web_rw=(
                (str(updated.web_rw_encrypted or "").strip() or None)
                if self._live_session_web_allowed()
                else None
            ),
            granted_until=granted_until,
        )

    async def revoke_live_session(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
        reason: str | None = None,
    ) -> models.TaskRunLiveSession:
        """Revoke live session access and mark it terminally revoked."""

        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
        )
        live = await self._repository.get_live_session(task_run_id=task_run_id)
        if live is None:
            raise LiveSessionNotFoundError("live session is not enabled for this task")

        updated = await self._repository.upsert_live_session(
            task_run_id=task_run_id,
            status=models.AgentJobLiveSessionStatus.REVOKED,
            rw_granted_until=datetime.now(UTC),
        )
        await self._repository.append_control_event(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            action="revoke_session",
            metadata_json={"reason": (reason or "").strip() or None},
        )
        await self._repository.append_event(
            job_id=task_run_id,
            level=models.AgentJobEventLevel.WARN,
            message="task.live_session.revoked",
            payload={"reason": (reason or "").strip() or None},
        )
        await self._repository.commit()
        return updated

    async def apply_control_action(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
        action: str,
    ) -> models.AgentJob:
        """Apply pause/resume/takeover controls to a task run."""

        normalized = action.strip().lower()
        if normalized not in {"pause", "resume", "takeover"}:
            raise AgentQueueValidationError(
                "action must be one of: pause, resume, takeover"
            )
        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
        )

        if normalized == "pause":
            job = await self._repository.set_job_live_control(
                task_run_id=task_run_id,
                paused=True,
                last_action=normalized,
            )
        elif normalized == "resume":
            job = await self._repository.set_job_live_control(
                task_run_id=task_run_id,
                paused=False,
                takeover=False,
                last_action=normalized,
            )
        else:
            job = await self._repository.set_job_live_control(
                task_run_id=task_run_id,
                paused=True,
                takeover=True,
                last_action=normalized,
            )

        await self._repository.append_control_event(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            action=normalized,
            metadata_json={"action": normalized},
        )
        await self._repository.append_event(
            job_id=task_run_id,
            level=models.AgentJobEventLevel.WARN,
            message="task.control",
            payload={"action": normalized},
        )
        await self._repository.commit()
        return job

    async def append_operator_message(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
        message: str,
    ) -> models.TaskRunControlEvent:
        """Persist and broadcast an operator message for a running task run."""

        normalized_message = message.strip()
        if not normalized_message:
            raise AgentQueueValidationError("message must be a non-empty string")
        if len(normalized_message) > 4000:
            raise AgentQueueValidationError("message must be 4000 chars or fewer")
        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
        )

        event = await self._repository.append_control_event(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            action="send_message",
            metadata_json={"message": normalized_message},
        )
        await self._repository.append_event(
            job_id=task_run_id,
            level=models.AgentJobEventLevel.INFO,
            message="task.operator.message",
            payload={
                "actorUserId": (
                    str(actor_user_id) if actor_user_id is not None else None
                ),
                "message": normalized_message,
            },
        )
        await self._repository.commit()
        return event

    async def issue_worker_token(
        self,
        *,
        worker_id: str,
        description: Optional[str] = None,
        allowed_repositories: Optional[list[str]] = None,
        allowed_job_types: Optional[list[str]] = None,
        capabilities: Optional[list[str]] = None,
    ) -> WorkerTokenIssueResult:
        """Create a worker token and return one-time raw token value."""

        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise AgentQueueValidationError("workerId must be a non-empty string")

        raw_token = f"mmwt_{secrets.token_hex(24)}"
        token_hash = self._hash_token(raw_token)
        token_record = await self._repository.create_worker_token(
            worker_id=normalized_worker_id,
            token_hash=token_hash,
            description=description.strip() if description else None,
            allowed_repositories=list(self._normalize_str_list(allowed_repositories))
            or None,
            allowed_job_types=list(self._normalize_str_list(allowed_job_types)) or None,
            capabilities=list(self._normalize_str_list(capabilities)) or None,
        )
        await self._repository.commit()
        return WorkerTokenIssueResult(token_record=token_record, raw_token=raw_token)

    async def list_worker_tokens(
        self, *, limit: int = 200
    ) -> list[models.AgentWorkerToken]:
        """List worker token metadata (without raw token values)."""

        if limit < 1 or limit > 500:
            raise AgentQueueValidationError("limit must be between 1 and 500")
        return await self._repository.list_worker_tokens(limit=limit)

    async def revoke_worker_token(self, *, token_id: UUID) -> models.AgentWorkerToken:
        """Deactivate one worker token by id."""

        token = await self._repository.revoke_worker_token(token_id=token_id)
        await self._repository.commit()
        return token

    async def resolve_worker_token(self, raw_token: str) -> WorkerAuthPolicy:
        """Resolve a raw worker token to enforced worker policy."""

        candidate = raw_token.strip()
        if not candidate:
            raise AgentQueueAuthenticationError("worker token is required")

        token_hash = self._hash_token(candidate)
        token = await self._repository.get_worker_token_by_hash(token_hash)
        if token is None:
            raise AgentQueueAuthenticationError("invalid worker token")
        if not token.is_active:
            raise AgentQueueAuthenticationError("worker token is inactive")

        return WorkerAuthPolicy(
            worker_id=token.worker_id,
            auth_source="worker_token",
            token_id=token.id,
            allowed_repositories=tuple(token.allowed_repositories or ()),
            allowed_job_types=tuple(token.allowed_job_types or ()),
            capabilities=tuple(token.capabilities or ()),
        )

    async def get_migration_telemetry(
        self,
        *,
        window_hours: int = 168,
        limit: int = 5000,
    ) -> QueueMigrationTelemetry:
        """Return aggregate telemetry for task migration and mixed-fleet rollout."""

        if window_hours < 1 or window_hours > 24 * 365:
            raise AgentQueueValidationError("windowHours must be between 1 and 8760")
        if limit < 1 or limit > 20000:
            raise AgentQueueValidationError("limit must be between 1 and 20000")

        generated_at = datetime.now(UTC)
        since = generated_at - timedelta(hours=window_hours)
        jobs = await self._repository.list_jobs_for_telemetry(since=since, limit=limit)

        job_volume_by_type: dict[str, int] = {}
        legacy_job_submissions = 0
        for job in jobs:
            job_volume_by_type[job.type] = job_volume_by_type.get(job.type, 0) + 1
            if job.type in LEGACY_TASK_JOB_TYPES:
                legacy_job_submissions += 1

        events_by_job, events_truncated = await self._load_events_by_job(
            jobs=jobs,
            since=since,
        )

        failure_counts: dict[tuple[str, str], int] = {}
        publish_requested = 0
        publish_published = 0
        publish_skipped = 0
        publish_failed = 0
        publish_unknown = 0

        for job in jobs:
            runtime = self._extract_runtime(job.payload)
            publish_mode = self._extract_publish_mode(job.payload)
            job_events = events_by_job.get(job.id, [])

            if job.status in {
                models.AgentJobStatus.FAILED,
                models.AgentJobStatus.DEAD_LETTER,
            }:
                failed_stage = self._extract_failed_stage(job_events)
                key = (runtime, failed_stage)
                failure_counts[key] = failure_counts.get(key, 0) + 1

            if publish_mode != "none":
                publish_requested += 1
                outcome = self._extract_publish_outcome(job_events)
                if outcome == "published":
                    publish_published += 1
                elif outcome == "skipped":
                    publish_skipped += 1
                elif outcome == "failed":
                    publish_failed += 1
                else:
                    publish_unknown += 1

        failure_counts_by_runtime_stage = [
            {"runtime": runtime, "stage": stage, "count": count}
            for (runtime, stage), count in sorted(
                failure_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
        ]
        publish_denominator = publish_requested if publish_requested > 0 else 1
        publish_outcomes = {
            "requested": publish_requested,
            "published": publish_published,
            "skipped": publish_skipped,
            "failed": publish_failed,
            "unknown": publish_unknown,
            "publishedRate": round(publish_published / publish_denominator, 4),
            "skippedRate": round(publish_skipped / publish_denominator, 4),
            "failedRate": round(publish_failed / publish_denominator, 4),
        }

        return QueueMigrationTelemetry(
            generated_at=generated_at,
            window_hours=window_hours,
            total_jobs=len(jobs),
            job_volume_by_type=job_volume_by_type,
            failure_counts_by_runtime_stage=failure_counts_by_runtime_stage,
            publish_outcomes=publish_outcomes,
            legacy_job_submissions=legacy_job_submissions,
            events_truncated=events_truncated,
        )

    async def require_worker_token(self, token_id: UUID) -> models.AgentWorkerToken:
        """Return worker token metadata by id or raise validation error."""

        try:
            return await self._repository.get_worker_token(token_id)
        except AgentWorkerTokenNotFoundError as exc:
            raise AgentQueueValidationError(str(exc)) from exc

    def _compute_retry_delay_seconds(self, attempt: int) -> int:
        """Compute exponential backoff delay for the next retry."""

        power = max(0, attempt - 1)
        raw_delay = self._retry_backoff_base_seconds * (2**power)
        return min(self._retry_backoff_max_seconds, raw_delay)

    @staticmethod
    def _normalize_str_list(values: Optional[list[str]]) -> tuple[str, ...]:
        if values is None:
            return ()
        normalized = []
        for value in values:
            item = str(value).strip()
            if item:
                normalized.append(item)
        return tuple(dict.fromkeys(normalized))

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    @staticmethod
    def _resolve_live_session_provider() -> models.AgentJobLiveSessionProvider:
        provider = (
            str(settings.spec_workflow.live_session_provider or "").strip().lower()
        )
        if provider == "tmate" or not provider:
            return models.AgentJobLiveSessionProvider.TMATE
        raise AgentQueueValidationError("live session provider must be one of: tmate")

    @staticmethod
    def _live_session_web_allowed() -> bool:
        return bool(settings.spec_workflow.live_session_allow_web)

    async def _assert_live_session_worker_ownership(
        self,
        *,
        task_run_id: UUID,
        worker_id: str,
        allow_terminal_report: bool,
    ) -> None:
        """Require active ownership, or prior live-session ownership for terminal reports."""

        job = await self._repository.require_job(task_run_id)
        if (
            job.status is models.AgentJobStatus.RUNNING
            and str(job.claimed_by or "").strip() == worker_id
        ):
            return
        if allow_terminal_report:
            live = await self._repository.get_live_session(task_run_id=task_run_id)
            if live is not None and str(live.worker_id or "").strip() == worker_id:
                return
        raise AgentQueueAuthorizationError(
            f"worker '{worker_id}' does not own task run {task_run_id}"
        )

    async def _assert_task_run_user_access(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
    ) -> models.AgentJob:
        """Require actor user ownership for live-session and control operations."""

        if actor_user_id is None:
            raise AgentQueueJobAuthorizationError("authenticated user id is required")
        job = await self._repository.require_job(task_run_id)
        if actor_user_id in {job.created_by_user_id, job.requested_by_user_id}:
            return job
        raise AgentQueueJobAuthorizationError(
            f"user '{actor_user_id}' is not authorized for task run {task_run_id}"
        )

    async def _load_events_by_job(
        self,
        *,
        jobs: list[models.AgentJob],
        since: datetime,
        event_limit: int = _TELEMETRY_EVENT_FETCH_LIMIT,
    ) -> tuple[dict[UUID, list[models.AgentJobEvent]], bool]:
        if not jobs:
            return {}, False
        fetch_limit = max(1, event_limit)
        events = await self._repository.list_events_for_jobs(
            job_ids=[job.id for job in jobs],
            since=since,
            limit=fetch_limit + 1,
        )
        events_truncated = len(events) > fetch_limit
        if events_truncated:
            events = events[:fetch_limit]
        grouped: dict[UUID, list[models.AgentJobEvent]] = {}
        for event in events:
            grouped.setdefault(event.job_id, []).append(event)
        return grouped, events_truncated

    @staticmethod
    def _extract_runtime(payload: dict[str, Any] | None) -> str:
        source = payload if isinstance(payload, dict) else {}
        task_node = source.get("task")
        task = task_node if isinstance(task_node, dict) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, dict) else {}
        runtime = (
            source.get("targetRuntime")
            or source.get("target_runtime")
            or runtime.get("mode")
            or source.get("runtime")
            or "unknown"
        )
        normalized = str(runtime).strip().lower()
        if normalized in SUPPORTED_EXECUTION_RUNTIMES:
            return normalized
        return "unknown"

    @staticmethod
    def _extract_publish_mode(payload: dict[str, Any] | None) -> str:
        source = payload if isinstance(payload, dict) else {}
        task_node = source.get("task")
        task = task_node if isinstance(task_node, dict) else {}
        task_publish_node = task.get("publish")
        task_publish = task_publish_node if isinstance(task_publish_node, dict) else {}
        publish_node = source.get("publish")
        publish = publish_node if isinstance(publish_node, dict) else {}
        publish = (
            task_publish.get("mode")
            or publish.get("mode")
            or source.get("publishMode")
            or "none"
        )
        mode = str(publish).strip().lower()
        if mode in {"none", "branch", "pr"}:
            return mode
        return "none"

    @staticmethod
    def _event_stage_marker(event: models.AgentJobEvent) -> str:
        payload = event.payload or {}
        stage = str(payload.get("stage") or "").strip()
        if stage:
            return stage
        return str(event.message or "").strip()

    @classmethod
    def _extract_failed_stage(cls, events: list[models.AgentJobEvent]) -> str:
        for event in reversed(events):
            marker = cls._event_stage_marker(event)
            payload = event.payload or {}
            status = str(payload.get("status") or "").strip().lower()
            is_failed = (
                event.level is models.AgentJobEventLevel.ERROR or status == "failed"
            )
            if not is_failed:
                continue
            if marker.startswith("moonmind.task.prepare"):
                return "prepare"
            if marker.startswith("moonmind.task.execute"):
                return "execute"
            if marker.startswith("moonmind.task.publish"):
                return "publish"
        return "unknown"

    @classmethod
    def _extract_publish_outcome(cls, events: list[models.AgentJobEvent]) -> str:
        for event in reversed(events):
            marker = cls._event_stage_marker(event)
            payload = event.payload or {}
            if marker == "moonmind.task.publish":
                status = str(payload.get("status") or "").strip().lower()
                if status in {"published", "skipped"}:
                    return status
            if marker.startswith("moonmind.task.publish"):
                stage_status = str(payload.get("status") or "").strip().lower()
                if (
                    event.level is models.AgentJobEventLevel.ERROR
                    or stage_status == "failed"
                ):
                    return "failed"
        return "unknown"
