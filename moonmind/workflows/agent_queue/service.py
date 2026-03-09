"""Service layer for Agent Queue operations."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.job_types import (
    CANONICAL_TASK_JOB_TYPE,
    LEGACY_TASK_JOB_TYPES,
    MANIFEST_JOB_TYPE,
    SUPPORTED_QUEUE_JOB_TYPES,
)
from moonmind.workflows.agent_queue.manifest_contract import (
    ManifestContractError,
    normalize_manifest_job_payload,
)
from moonmind.workflows.agent_queue.repositories import (
    AgentArtifactNotFoundError,
    AgentJobStateError,
    AgentQueueRepository,
    AgentWorkerTokenNotFoundError,
)
from moonmind.workflows.agent_queue.runtime_defaults import (
    DEFAULT_REPOSITORY,
    resolve_default_task_runtime,
    resolve_runtime_defaults,
)
from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage
from moonmind.workflows.agent_queue.task_contract import (
    SUPPORTED_EXECUTION_RUNTIMES,
    TaskContractError,
    _normalize_publish_mode,
    has_attachment_mutation_fields,
    normalize_queue_job_payload,
)
from moonmind.workflows.tasks import compile_task_payload_templates

logger = logging.getLogger(__name__)
_TELEMETRY_EVENT_FETCH_LIMIT = 100000
_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ATTACHMENT_NAMESPACE = "inputs/"
_ATTACHMENT_FILENAME_MAX_LENGTH = 120
_ATTACHMENT_EXTENSION_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_RUNTIME_CAPABILITY_RUNTIMES = {"codex", "gemini", "claude", "jules"}
_PUBLISH_PREFLIGHT_VERIFICATION_GAP_TEXT = (
    "publish preflight failed: source-code changes detected but no "
    "verification command result was captured in artifacts"
)
_RESUBMIT_PUBLISH_SKIP_REASON_CATEGORY = "resubmit"
_RESUBMIT_PUBLISH_SKIP_REASON = (
    "Re-run accepted from previous publish preflight failure with no verification "
    "evidence available."
)


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
class AttachmentUpload:
    """In-memory representation of an uploaded attachment."""

    filename: str
    content_type: str | None
    data: bytes
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class _NormalizedAttachment:
    """Validated attachment payload ready for persistence."""

    original_filename: str
    sanitized_filename: str
    content_type: str
    data: bytes
    size_bytes: int
    digest: str
    caption: str | None = None


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
    events_truncated: bool


@dataclass(frozen=True, slots=True)
class WorkerPauseMetrics:
    """Queued/running counters surfaced to operators while paused."""

    queued: int
    running: int
    stale_running: int

    @property
    def is_drained(self) -> bool:
        return self.running == 0 and self.stale_running == 0


@dataclass(frozen=True, slots=True)
class QueueSystemMetadata:
    """Versioned pause metadata attached to claim/heartbeat responses."""

    workers_paused: bool
    mode: models.WorkerPauseMode | None
    reason: str | None
    version: int
    requested_by_user_id: UUID | None
    requested_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkerPauseAuditEvent:
    """Recent system control event surfaced via the worker pause API."""

    id: UUID
    action: str
    mode: models.WorkerPauseMode | None
    reason: str | None
    actor_user_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkerPauseSnapshot:
    """Aggregated worker pause state returned by GET/POST endpoints."""

    system: QueueSystemMetadata
    metrics: WorkerPauseMetrics
    audit_events: tuple[WorkerPauseAuditEvent, ...]


@dataclass(frozen=True, slots=True)
class QueueSystemResponse:
    """Envelope returned by claim/heartbeat paths that includes system metadata."""

    job: models.AgentJob | None
    system: QueueSystemMetadata


@dataclass(frozen=True, slots=True)
class QueueJobPage:
    """One keyset-paginated queue jobs page."""

    items: tuple[models.AgentJob, ...]
    page_size: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class QueueSafeguardJob:
    """Snapshot of a job violating one or more safeguards."""

    job: models.AgentJob
    runtime_seconds: int | None
    lease_overdue_seconds: int | None


@dataclass(frozen=True, slots=True)
class QueueSafeguardSnapshot:
    """Aggregate Safeguard summary returned to operators."""

    generated_at: datetime
    max_runtime_seconds: int
    stale_lease_grace_seconds: int
    timed_out: tuple[QueueSafeguardJob, ...]
    stale: tuple[QueueSafeguardJob, ...]


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
        max_runtime_seconds: int | None = None,
        stale_lease_grace_seconds: int | None = None,
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
        self._attachments_enabled = bool(
            settings.spec_workflow.agent_job_attachment_enabled
        )
        self._attachment_max_count = max(
            0, int(settings.spec_workflow.agent_job_attachment_max_count)
        )
        self._attachment_max_bytes = max(
            1, int(settings.spec_workflow.agent_job_attachment_max_bytes)
        )
        self._attachment_total_max_bytes = max(
            1, int(settings.spec_workflow.agent_job_attachment_total_bytes)
        )
        allowed_types = tuple(
            settings.spec_workflow.agent_job_attachment_allowed_content_types or ()
        )
        self._attachment_allowed_content_types = (
            allowed_types
            if allowed_types
            else ("image/png", "image/jpeg", "image/webp")
        )
        self._retry_backoff_base_seconds = max(1, int(retry_backoff_base_seconds))
        self._retry_backoff_max_seconds = max(
            self._retry_backoff_base_seconds,
            int(retry_backoff_max_seconds),
        )
        default_runtime = int(settings.spec_workflow.agent_job_max_runtime_seconds)
        default_stale_grace = int(
            settings.spec_workflow.agent_job_stale_lease_grace_seconds
        )
        self._max_runtime_seconds = max(
            0, default_runtime if max_runtime_seconds is None else max_runtime_seconds
        )
        self._stale_lease_grace_seconds = max(
            0,
            (
                default_stale_grace
                if stale_lease_grace_seconds is None
                else stale_lease_grace_seconds
            ),
        )

    @staticmethod
    def _clean_optional_str(value: object) -> str | None:
        """Return trimmed string or ``None`` for blank values."""

        text = str(value).strip() if value is not None else ""
        return text or None

    @classmethod
    def _clean_optional_str_max(
        cls,
        value: object,
        *,
        max_length: int,
    ) -> str | None:
        """Return a trimmed optional string bounded to ``max_length`` chars."""

        text = cls._clean_optional_str(value)
        if text is None:
            return None
        return text[:max_length]

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
        default_runtime = resolve_default_task_runtime(settings.spec_workflow)
        repository = self._clean_optional_str(
            enriched.get("repository") or enriched.get("repo")
        )
        if repository is None:
            repository = (
                self._clean_optional_str(settings.spec_workflow.github_repository)
                or DEFAULT_REPOSITORY
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
            or default_runtime
        ).lower()
        runtime["mode"] = runtime_mode
        if task_node is None and (
            self._clean_optional_str(task.get("instructions")) is None
            and self._clean_optional_str(task.get("instruction")) is None
        ):
            task["instructions"] = (
                self._clean_optional_str(enriched.get("instructions"))
                or self._clean_optional_str(enriched.get("instruction"))
                or "Queue job"
            )
        enriched["targetRuntime"] = runtime_mode

        default_model, default_effort = resolve_runtime_defaults(
            runtime_mode,
            spec_workflow_settings=settings.spec_workflow,
        )
        if self._clean_optional_str(runtime.get("model")) is None and default_model:
            runtime["model"] = default_model
        if self._clean_optional_str(runtime.get("effort")) is None and default_effort:
            runtime["effort"] = default_effort

        task["runtime"] = runtime
        enriched["task"] = task
        return enriched

    def normalize_task_job_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize canonical task payloads for downstream reuse."""

        normalized_payload = self._enrich_task_payload_defaults(dict(payload or {}))
        normalized_payload = compile_task_payload_templates(normalized_payload)
        target_runtime = (
            str(normalized_payload.get("targetRuntime") or "").strip().lower()
        )
        if target_runtime == "jules":
            gate_state = settings.jules_runtime_gate
            if not gate_state.enabled:
                raise AgentQueueValidationError(gate_state.error_message)
        try:
            return normalize_queue_job_payload(
                job_type=CANONICAL_TASK_JOB_TYPE,
                payload=normalized_payload,
                default_runtime=normalized_payload.get("targetRuntime"),
            )
        except TaskContractError as exc:
            raise AgentQueueValidationError(str(exc)) from exc

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
        commit: bool = True,
    ) -> models.AgentJob:
        """Create and persist a new queued job."""

        job = await self._create_job_record(
            job_type=job_type,
            payload=payload,
            priority=priority,
            created_by_user_id=created_by_user_id,
            requested_by_user_id=requested_by_user_id,
            affinity_key=affinity_key,
            max_attempts=max_attempts,
        )
        if commit:
            await self._repository.commit()
        return job

    async def _create_job_record(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        priority: int,
        created_by_user_id: Optional[UUID],
        requested_by_user_id: Optional[UUID],
        affinity_key: Optional[str],
        max_attempts: int,
    ) -> models.AgentJob:
        """Create a job row and append queue events without committing."""

        candidate_type = job_type.strip()
        if not candidate_type:
            raise AgentQueueValidationError("type must be a non-empty string")
        if candidate_type not in SUPPORTED_QUEUE_JOB_TYPES:
            supported = ", ".join(sorted(SUPPORTED_QUEUE_JOB_TYPES))
            raise AgentQueueValidationError(f"type must be one of: {supported}")
        if max_attempts < 1:
            raise AgentQueueValidationError("maxAttempts must be >= 1")

        normalized_payload = dict(payload or {})
        if candidate_type == CANONICAL_TASK_JOB_TYPE:
            normalized_payload = self.normalize_task_job_payload(normalized_payload)
        elif candidate_type == MANIFEST_JOB_TYPE:
            try:
                normalized_payload = normalize_manifest_job_payload(normalized_payload)
            except ManifestContractError as exc:
                raise AgentQueueValidationError(str(exc)) from exc
        else:
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
        return job

    def _is_publish_preflight_verification_gap(
        self, source_job: models.AgentJob
    ) -> bool:
        """Detect the specific publish preflight failure mode we can safely recover from."""

        if source_job.type != CANONICAL_TASK_JOB_TYPE:
            return False
        if str(source_job.finish_outcome_code or "").lower() != "failed":
            return False
        if str(source_job.finish_outcome_stage or "").lower() != "publish":
            return False

        error_message = (source_job.error_message or "").lower()
        if _PUBLISH_PREFLIGHT_VERIFICATION_GAP_TEXT in error_message:
            return True

        summary = source_job.finish_summary_json
        if isinstance(summary, dict):
            reason = ""
            publish = summary.get("publish")
            if isinstance(publish, dict):
                reason = str(publish.get("reason") or "")
            return _PUBLISH_PREFLIGHT_VERIFICATION_GAP_TEXT in reason.lower()
        return False

    def _inject_resubmit_publish_skip_reason(
        self,
        *,
        source_job: models.AgentJob,
        payload: dict[str, Any],
    ) -> None:
        """Preserve or synthesize skip reason to avoid brittle resubmit failures."""

        source_payload = source_job.payload
        if not isinstance(source_payload, dict):
            return

        source_task = source_payload.get("task")
        request_task = payload.get("task")
        if not (isinstance(source_task, dict) and isinstance(request_task, dict)):
            return

        source_publish = source_task.get("publish")
        request_publish = request_task.get("publish")
        if not isinstance(request_publish, dict):
            request_publish = {}
            request_task["publish"] = request_publish

        if not isinstance(source_publish, dict):
            return

        if "verificationSkipReason" in request_publish:
            return

        publish_mode = self._clean_optional_str(request_publish.get("mode"))
        if publish_mode is None:
            publish_mode = self._clean_optional_str(source_publish.get("mode"))
        if (publish_mode or "").lower() == "none":
            return

        source_skip_reason = source_publish.get("verificationSkipReason")
        if isinstance(source_skip_reason, dict):
            request_publish["verificationSkipReason"] = copy.deepcopy(
                source_skip_reason
            )
            return

        if self._is_publish_preflight_verification_gap(source_job):
            request_publish["verificationSkipReason"] = {
                "category": _RESUBMIT_PUBLISH_SKIP_REASON_CATEGORY,
                "reason": _RESUBMIT_PUBLISH_SKIP_REASON,
            }

    async def create_job_with_attachments(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        priority: int = 0,
        created_by_user_id: Optional[UUID] = None,
        requested_by_user_id: Optional[UUID] = None,
        affinity_key: Optional[str] = None,
        max_attempts: int = 3,
        attachments: Sequence[AttachmentUpload],
    ) -> tuple[models.AgentJob, list[models.AgentJobArtifact]]:
        """Create a queued job and persist user-provided attachments before claim."""

        if not self._attachments_enabled:
            raise AgentQueueValidationError("attachments are currently disabled")
        if job_type.strip() != CANONICAL_TASK_JOB_TYPE:
            raise AgentQueueValidationError(
                "attachments are only supported for task jobs"
            )

        uploads = list(attachments or [])
        if not uploads:
            raise AgentQueueValidationError(
                "attachments must include at least one file"
            )
        if self._attachment_max_count and len(uploads) > self._attachment_max_count:
            raise AgentQueueValidationError(
                f"attachments exceed max count ({self._attachment_max_count})"
            )

        normalized_uploads: list[_NormalizedAttachment] = []
        total_bytes = 0
        for upload in uploads:
            normalized = self._normalize_attachment_upload(upload)
            normalized_uploads.append(normalized)
            total_bytes += normalized.size_bytes

        if total_bytes > self._attachment_total_max_bytes:
            raise AgentQueueValidationError(
                f"attachments exceed max total bytes ({self._attachment_total_max_bytes})"
            )

        job = await self._create_job_record(
            job_type=job_type,
            payload=payload,
            priority=priority,
            created_by_user_id=created_by_user_id,
            requested_by_user_id=requested_by_user_id,
            affinity_key=affinity_key,
            max_attempts=max_attempts,
        )
        stored = await self._persist_attachments(
            job_id=job.id, uploads=normalized_uploads
        )
        await self._repository.commit()
        return job, stored

    async def update_queued_job(
        self,
        *,
        job_id: UUID,
        actor_user_id: UUID | None,
        actor_is_superuser: bool = False,
        job_type: str,
        payload: dict[str, Any],
        priority: int = 0,
        affinity_key: str | None = None,
        max_attempts: int = 3,
        expected_updated_at: datetime | None = None,
        note: str | None = None,
    ) -> models.AgentJob:
        """Update one queued, never-started task job in place."""

        candidate_type = str(job_type or "").strip()
        if not candidate_type:
            raise AgentQueueValidationError("type must be a non-empty string")
        if max_attempts < 1:
            raise AgentQueueValidationError("maxAttempts must be >= 1")

        job = await self._repository.require_job_for_update(job_id)
        if not actor_is_superuser and actor_user_id not in {
            job.created_by_user_id,
            job.requested_by_user_id,
        }:
            raise AgentQueueJobAuthorizationError(
                f"user '{actor_user_id}' is not authorized for task run {job_id}"
            )
        if job.status is not models.AgentJobStatus.QUEUED:
            raise AgentJobStateError(
                f"Job {job_id} is {job.status.value} and cannot be updated"
            )
        if job.started_at is not None:
            raise AgentJobStateError(f"Job {job_id} has already started")
        if candidate_type != job.type:
            raise AgentJobStateError(
                f"Job {job_id} type mismatch ({job.type} != {candidate_type})"
            )
        if candidate_type != CANONICAL_TASK_JOB_TYPE:
            raise AgentJobStateError(
                f"Job {job_id} type '{job.type}' does not support queued updates"
            )
        if expected_updated_at is not None:
            if self._coerce_utc(expected_updated_at) != self._coerce_utc(
                job.updated_at
            ):
                raise AgentJobStateError(
                    f"Job {job_id} update conflict; refresh and retry"
                )
        if has_attachment_mutation_fields(payload):
            raise AgentQueueValidationError(
                "attachment edits are not supported for queued updates"
            )

        normalized_payload = self.normalize_task_job_payload(dict(payload or {}))
        normalized_affinity = affinity_key.strip() if affinity_key else None
        clean_note = self._clean_optional_str_max(note, max_length=256)

        changed_fields: list[str] = []
        if job.priority != priority:
            changed_fields.append("priority")
        if job.payload != normalized_payload:
            changed_fields.append("payload")
        if job.affinity_key != normalized_affinity:
            changed_fields.append("affinityKey")
        if job.max_attempts != max_attempts:
            changed_fields.append("maxAttempts")

        now = datetime.now(UTC)
        job.priority = priority
        job.payload = normalized_payload
        job.affinity_key = normalized_affinity
        job.max_attempts = max_attempts
        job.updated_at = now
        event_payload: dict[str, Any] = {
            "actorUserId": (str(actor_user_id) if actor_user_id is not None else None),
            "changedFields": changed_fields,
        }
        if clean_note is not None:
            event_payload["note"] = clean_note
        await self._repository.append_event(
            job_id=job.id,
            level=models.AgentJobEventLevel.INFO,
            message="Job updated",
            payload=event_payload,
        )
        await self._repository.commit()
        return job

    async def resubmit_job(
        self,
        *,
        job_id: UUID,
        actor_user_id: UUID | None,
        actor_is_superuser: bool = False,
        job_type: str,
        payload: dict[str, Any],
        priority: int = 0,
        affinity_key: str | None = None,
        max_attempts: int = 3,
        note: str | None = None,
    ) -> models.AgentJob:
        """Create a new queued job from one failed/cancelled task job."""

        candidate_type = str(job_type or "").strip()
        if not candidate_type:
            raise AgentQueueValidationError("type must be a non-empty string")
        if max_attempts < 1:
            raise AgentQueueValidationError("maxAttempts must be >= 1")

        source_job = await self._repository.require_job_for_update(job_id)
        if not actor_is_superuser and actor_user_id not in {
            source_job.created_by_user_id,
            source_job.requested_by_user_id,
        }:
            raise AgentQueueJobAuthorizationError(
                f"user '{actor_user_id}' is not authorized for task run {job_id}"
            )
        if source_job.status not in {
            models.AgentJobStatus.FAILED,
            models.AgentJobStatus.CANCELLED,
        }:
            raise AgentJobStateError(
                f"Job {job_id} is {source_job.status.value} and cannot be resubmitted"
            )
        if candidate_type != source_job.type:
            raise AgentJobStateError(
                f"Job {job_id} type mismatch ({source_job.type} != {candidate_type})"
            )
        if candidate_type != CANONICAL_TASK_JOB_TYPE:
            raise AgentJobStateError(
                f"Job {job_id} type '{source_job.type}' does not support resubmit"
            )
        if has_attachment_mutation_fields(payload):
            raise AgentQueueValidationError(
                "attachment edits are not supported for resubmits"
            )
        clean_note = self._clean_optional_str_max(note, max_length=256)

        effective_created_by = (
            actor_user_id
            if actor_user_id is not None
            else source_job.created_by_user_id
        )
        effective_requested_by = (
            actor_user_id
            if actor_user_id is not None
            else source_job.requested_by_user_id
        )
        normalized_payload = copy.deepcopy(dict(payload or {}))
        self._inject_resubmit_publish_skip_reason(
            source_job=source_job,
            payload=normalized_payload,
        )
        new_job = await self._create_job_record(
            job_type=candidate_type,
            payload=normalized_payload,
            priority=priority,
            created_by_user_id=effective_created_by,
            requested_by_user_id=effective_requested_by,
            affinity_key=affinity_key.strip() if affinity_key else None,
            max_attempts=max_attempts,
        )
        changed_fields: list[str] = []
        if source_job.priority != new_job.priority:
            changed_fields.append("priority")
        if source_job.payload != new_job.payload:
            changed_fields.append("payload")
        if source_job.affinity_key != new_job.affinity_key:
            changed_fields.append("affinityKey")
        if source_job.max_attempts != new_job.max_attempts:
            changed_fields.append("maxAttempts")
        source_event_payload: dict[str, Any] = {
            "newJobId": str(new_job.id),
            "actorUserId": (str(actor_user_id) if actor_user_id is not None else None),
            "changedFields": changed_fields,
        }
        if clean_note is not None:
            source_event_payload["note"] = clean_note
        await self._repository.append_event(
            job_id=source_job.id,
            level=models.AgentJobEventLevel.INFO,
            message="Job resubmitted",
            payload=source_event_payload,
        )
        await self._repository.append_event(
            job_id=new_job.id,
            level=models.AgentJobEventLevel.INFO,
            message="Job resubmitted from",
            payload={
                "sourceJobId": str(source_job.id),
                "actorUserId": (
                    str(actor_user_id) if actor_user_id is not None else None
                ),
            },
        )
        await self._repository.commit()
        return new_job

    def _normalize_attachment_upload(
        self,
        upload: AttachmentUpload,
    ) -> _NormalizedAttachment:
        """Validate attachment payload and return normalized metadata."""

        data = upload.data or b""
        if not data:
            raise AgentQueueValidationError("attachments must not be empty")
        if len(data) > self._attachment_max_bytes:
            raise AgentQueueValidationError(
                f"attachment exceeds max bytes ({self._attachment_max_bytes})"
            )

        detected_type = self._sniff_attachment_content_type(data)
        if detected_type is None:
            raise AgentQueueValidationError(
                "attachment content type must be PNG, JPEG, or WebP"
            )
        if detected_type not in self._attachment_allowed_content_types:
            raise AgentQueueValidationError(
                f"attachment content type '{detected_type}' is not allowed"
            )

        sanitized = self._sanitize_attachment_filename(
            upload.filename or "",
            detected_type,
        )
        digest = hashlib.sha256(data).hexdigest()
        original_name = str(upload.filename or "").strip() or sanitized
        caption = str(upload.caption or "").strip() or None
        return _NormalizedAttachment(
            original_filename=original_name,
            sanitized_filename=sanitized,
            content_type=detected_type,
            data=data,
            size_bytes=len(data),
            digest=f"sha256:{digest}",
            caption=caption,
        )

    async def _persist_attachments(
        self,
        *,
        job_id: UUID,
        uploads: Sequence[_NormalizedAttachment],
    ) -> list[models.AgentJobArtifact]:
        """Write attachment files to storage and persist metadata."""

        stored: list[models.AgentJobArtifact] = []
        for upload in uploads:
            attachment_id = uuid4()
            artifact_name = (
                f"{_ATTACHMENT_NAMESPACE}{attachment_id}/{upload.sanitized_filename}"
            )
            storage_path: str | None = None
            try:
                _, storage_path = self._artifact_storage.write_artifact(
                    job_id=job_id,
                    artifact_name=artifact_name,
                    data=upload.data,
                )

                artifact = await self._repository.create_artifact(
                    job_id=job_id,
                    name=artifact_name,
                    content_type=upload.content_type,
                    size_bytes=upload.size_bytes,
                    digest=upload.digest,
                    storage_path=storage_path,
                )
            except Exception:
                if storage_path is not None:
                    self._artifact_storage.resolve_storage_path(storage_path).unlink(
                        missing_ok=True
                    )
                raise
            stored.append(artifact)
            await self._repository.append_event(
                job_id=job_id,
                level=models.AgentJobEventLevel.INFO,
                message="Attachment uploaded",
                payload={
                    "name": artifact_name,
                    "sizeBytes": upload.size_bytes,
                    "contentType": upload.content_type,
                    "caption": upload.caption,
                },
            )
        return stored

    def _sanitize_attachment_filename(
        self,
        filename: str,
        content_type: str,
    ) -> str:
        """Sanitize attachment filenames to prevent traversal or unsafe chars."""

        candidate = Path(filename or "").name
        if not candidate:
            candidate = "attachment"
        safe_chars = []
        for char in candidate:
            if char.isalnum() or char in {"-", "_", "."}:
                safe_chars.append(char)
            else:
                safe_chars.append("_")
        sanitized = "".join(safe_chars).strip("._-") or "attachment"
        path = Path(sanitized)
        stem = path.stem or "attachment"
        suffix = _ATTACHMENT_EXTENSION_MAP.get(content_type, path.suffix or "")
        max_stem_len = max(1, _ATTACHMENT_FILENAME_MAX_LENGTH - len(suffix))
        trimmed_stem = stem[:max_stem_len]
        return f"{trimmed_stem}{suffix}"

    @staticmethod
    def _sniff_attachment_content_type(data: bytes) -> str | None:
        """Best-effort magic-byte detection for supported attachment types."""

        if len(data) >= 8 and data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if len(data) >= 3 and data[0] == 0xFF and data[1] == 0xD8:
            return "image/jpeg"
        if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    async def get_job(self, job_id: UUID) -> Optional[models.AgentJob]:
        """Fetch a single job by id."""

        return await self._repository.get_job(job_id)

    async def list_jobs(
        self,
        *,
        status: Optional[models.AgentJobStatus] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[models.AgentJob]:
        """List queue jobs with optional filters."""

        if limit < 1 or limit > 201:
            raise AgentQueueValidationError("limit must be between 1 and 201")
        if offset < 0:
            raise AgentQueueValidationError("offset must be >= 0")

        normalized_type = job_type.strip() if job_type else None
        return await self._repository.list_jobs(
            status=status,
            job_type=normalized_type if normalized_type else None,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _encode_job_cursor(*, created_at: datetime, job_id: UUID) -> str:
        payload = json.dumps(
            {
                "created_at": created_at.astimezone(UTC).isoformat(),
                "id": str(job_id),
            }
        ).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    @staticmethod
    def _decode_job_cursor(cursor: str) -> tuple[datetime, UUID]:
        token = str(cursor or "").strip()
        if not token:
            raise AgentQueueValidationError("cursor is invalid")
        if len(token) > 1024:
            raise AgentQueueValidationError("cursor is invalid")
        padding = "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode(token + padding).decode()
            payload = json.loads(decoded)
            created_at = datetime.fromisoformat(str(payload["created_at"]))
            job_id = UUID(str(payload["id"]))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AgentQueueValidationError("cursor is invalid") from exc
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at.astimezone(UTC), job_id

    async def list_jobs_page(
        self,
        *,
        status: Optional[models.AgentJobStatus] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> QueueJobPage:
        """Return one keyset-paginated queue jobs page with opaque cursor token."""

        page_size = max(1, min(int(limit), 200))
        normalized_type = job_type.strip() if job_type else None
        cursor_tuple = self._decode_job_cursor(cursor) if cursor else None
        jobs, has_more = await self._repository.list_jobs_page(
            status=status,
            job_type=normalized_type if normalized_type else None,
            cursor=cursor_tuple,
            limit=page_size,
        )
        next_cursor: str | None = None
        if has_more and jobs:
            tail = jobs[-1]
            next_cursor = self._encode_job_cursor(
                created_at=tail.created_at,
                job_id=tail.id,
            )
        return QueueJobPage(
            items=tuple(jobs),
            page_size=page_size,
            next_cursor=next_cursor,
        )

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: Optional[list[str]] = None,
        allowed_repositories: Optional[list[str]] = None,
        worker_capabilities: Optional[list[str]] = None,
    ) -> QueueSystemResponse:
        """Claim a queued job for a worker and include pause metadata."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        if lease_seconds < 1:
            raise AgentQueueValidationError("leaseSeconds must be >= 1")

        normalized_types = self._normalize_str_list(allowed_types)
        normalized_repositories = self._normalize_str_list(allowed_repositories)
        normalized_capabilities = self._normalize_str_list(worker_capabilities)

        metadata = await self._load_system_metadata()
        if metadata.workers_paused:
            return QueueSystemResponse(job=None, system=metadata)

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
        return QueueSystemResponse(job=job, system=metadata)

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> QueueSystemResponse:
        """Extend the lease for a running job and include pause metadata."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        if lease_seconds < 1:
            raise AgentQueueValidationError("leaseSeconds must be >= 1")

        now = datetime.now(UTC)
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
        await self._maybe_trigger_runtime_timeout(job=job, now=now)
        await self._repository.commit()
        metadata = await self._load_system_metadata()
        return QueueSystemResponse(job=job, system=metadata)

    async def update_runtime_state(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        runtime_state: dict[str, Any] | None,
    ) -> models.AgentJob:
        """Persist worker-owned runtime checkpoint state for a running job."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        if runtime_state is not None and not isinstance(runtime_state, dict):
            raise AgentQueueValidationError("runtimeState must be an object")
        if runtime_state is not None:
            try:
                encoded = json.dumps(runtime_state, separators=(",", ":"))
            except TypeError as exc:
                raise AgentQueueValidationError(
                    "runtimeState must be JSON-serializable"
                ) from exc
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise AgentQueueValidationError(
                    "runtimeState exceeds max bytes (65536)"
                )

        await self._assert_job_worker_ownership(job_id=job_id, worker_id=worker)
        job = await self._repository.set_job_runtime_state(
            job_id=job_id,
            runtime_state=(dict(runtime_state) if runtime_state is not None else None),
        )
        await self._repository.commit()
        return job

    async def get_worker_pause_snapshot(
        self,
        *,
        audit_limit: int = 5,
    ) -> WorkerPauseSnapshot:
        """Return current worker pause metadata, metrics, and audit history."""

        return await self._build_worker_pause_snapshot(audit_limit=audit_limit)

    async def apply_worker_pause_action(
        self,
        *,
        action: Literal["pause", "resume"],
        mode: Optional[str],
        reason: str,
        actor_user_id: UUID | None,
        force_resume: bool = False,
        audit_limit: int = 5,
    ) -> WorkerPauseSnapshot:
        """Toggle worker pause state with validation and audit logging."""

        action_key = (action or "").strip().lower()
        if action_key not in {"pause", "resume"}:
            raise AgentQueueValidationError("action must be one of: pause, resume")

        normalized_reason = self._clean_optional_str(reason)
        if not normalized_reason:
            raise AgentQueueValidationError("reason must be a non-empty string")
        actor = await self._resolve_existing_actor_user_id(actor_user_id)

        state = await self._repository.get_pause_state()
        now = datetime.now(UTC)

        if action_key == "pause":
            mode_value = (mode or "").strip().lower()
            if not mode_value:
                raise AgentQueueValidationError("mode is required when pausing workers")
            try:
                pause_mode = models.WorkerPauseMode(mode_value)
            except ValueError as exc:  # pragma: no cover - defensive conversion
                raise AgentQueueValidationError(
                    "mode must be one of: drain, quiesce"
                ) from exc

            if (
                state.paused
                and state.mode == pause_mode
                and (state.reason or "").strip() == normalized_reason
            ):
                raise AgentQueueValidationError(
                    "workers are already paused in the requested mode"
                )

            requested_at = state.requested_at if state.paused else now
            await self._repository.update_pause_state(
                paused=True,
                mode=pause_mode,
                reason=normalized_reason,
                requested_by_user_id=actor,
                requested_at=requested_at,
            )
            await self._repository.append_system_control_event(
                action="pause",
                mode=pause_mode,
                reason=normalized_reason,
                actor_user_id=actor,
            )
            logger.info(
                "worker pause enabled",
                extra={
                    "mode": pause_mode.value,
                    "operator": str(actor) if actor is not None else None,
                },
            )
        else:
            if not state.paused:
                raise AgentQueueValidationError("workers are not currently paused")
            metrics = await self._build_worker_pause_metrics()
            if not metrics.is_drained and not force_resume:
                raise AgentQueueValidationError(
                    "workers are not drained; set forceResume=true to override"
                )

            await self._repository.update_pause_state(
                paused=False,
                mode=None,
                reason=normalized_reason,
                requested_by_user_id=actor,
                requested_at=None,
            )
            await self._repository.append_system_control_event(
                action="resume",
                mode=None,
                reason=normalized_reason,
                actor_user_id=actor,
            )
            logger.info(
                "worker pause disabled",
                extra={
                    "forceResume": force_resume,
                    "operator": str(actor) if actor is not None else None,
                    "runningBeforeResume": metrics.running,
                    "staleRunningBeforeResume": metrics.stale_running,
                },
            )

        await self._repository.commit()
        return await self._build_worker_pause_snapshot(audit_limit=audit_limit)

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: Optional[str] = None,
        finish_outcome_code: str | None = None,
        finish_outcome_stage: str | None = None,
        finish_outcome_reason: str | None = None,
        finish_summary: dict[str, Any] | None = None,
    ) -> models.AgentJob:
        """Mark a running job as completed."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")

        if finish_summary is not None and not isinstance(finish_summary, dict):
            raise AgentQueueValidationError("finishSummary must be an object")

        job = await self._repository.complete_job(
            job_id=job_id,
            worker_id=worker,
            result_summary=result_summary.strip() if result_summary else None,
            finish_outcome_code=self._clean_optional_str_max(
                finish_outcome_code,
                max_length=64,
            ),
            finish_outcome_stage=self._clean_optional_str_max(
                finish_outcome_stage,
                max_length=32,
            ),
            finish_outcome_reason=self._clean_optional_str_max(
                finish_outcome_reason,
                max_length=256,
            ),
            finish_summary=(
                dict(finish_summary) if finish_summary is not None else None
            ),
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
        finish_outcome_code: str | None = None,
        finish_outcome_stage: str | None = None,
        finish_outcome_reason: str | None = None,
        finish_summary: dict[str, Any] | None = None,
    ) -> models.AgentJob:
        """Mark a running job as failed."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        detail = error_message.strip()
        if not detail:
            raise AgentQueueValidationError("errorMessage must be a non-empty string")
        if finish_summary is not None and not isinstance(finish_summary, dict):
            raise AgentQueueValidationError("finishSummary must be an object")

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
            finish_outcome_code=self._clean_optional_str_max(
                finish_outcome_code,
                max_length=64,
            ),
            finish_outcome_stage=self._clean_optional_str_max(
                finish_outcome_stage,
                max_length=32,
            ),
            finish_outcome_reason=self._clean_optional_str_max(
                finish_outcome_reason,
                max_length=256,
            ),
            finish_summary=(
                dict(finish_summary) if finish_summary is not None else None
            ),
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
        actor_is_superuser: bool = False,
        reason: str | None = None,
    ) -> models.AgentJob:
        """Request cancellation for one queue job."""

        await self._assert_task_run_user_access(
            task_run_id=job_id,
            actor_user_id=requested_by_user_id,
            actor_is_superuser=actor_is_superuser,
        )

        clean_reason = self._clean_optional_str_max(reason, max_length=256)

        job, action = await self._repository.request_cancel(
            job_id=job_id,
            requested_by_user_id=requested_by_user_id,
            reason=clean_reason,
            finish_outcome_reason=clean_reason,
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
        finish_outcome_code: str | None = None,
        finish_outcome_stage: str | None = None,
        finish_outcome_reason: str | None = None,
        finish_summary: dict[str, Any] | None = None,
    ) -> models.AgentJob:
        """Acknowledge cancellation for a running job owned by worker."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        detail = message.strip() if message else None
        if detail == "":
            detail = None
        if finish_summary is not None and not isinstance(finish_summary, dict):
            raise AgentQueueValidationError("finishSummary must be an object")

        job, action = await self._repository.ack_cancel(
            job_id=job_id,
            worker_id=worker,
            finish_outcome_code=self._clean_optional_str_max(
                finish_outcome_code,
                max_length=64,
            ),
            finish_outcome_stage=self._clean_optional_str_max(
                finish_outcome_stage,
                max_length=32,
            ),
            finish_outcome_reason=self._clean_optional_str_max(
                finish_outcome_reason,
                max_length=256,
            ),
            finish_summary=(
                dict(finish_summary) if finish_summary is not None else None
            ),
        )
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
        try:
            normalized_artifact_name = artifact_name.replace("\\", "/").strip()
            while normalized_artifact_name.startswith("./"):
                normalized_artifact_name = normalized_artifact_name[2:]
            if not normalized_artifact_name:
                raise ValueError
        except Exception as exc:
            raise AgentQueueValidationError("name must be a non-empty string") from exc
        if normalized_artifact_name.startswith(_ATTACHMENT_NAMESPACE):
            raise AgentQueueValidationError(
                "artifact name may not use reserved 'inputs/' namespace"
            )
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
            await self._assert_job_worker_ownership(
                job_id=job_id,
                worker_id=worker_id,
                job=job,
            )

        try:
            _, storage_path = self._artifact_storage.write_artifact(
                job_id=job_id,
                artifact_name=normalized_artifact_name,
                data=data,
            )
        except ValueError as exc:
            raise AgentQueueValidationError(str(exc)) from exc

        artifact = await self._repository.create_artifact(
            job_id=job_id,
            name=normalized_artifact_name,
            content_type=content_type.strip() if content_type else None,
            digest=digest.strip() if digest else None,
            size_bytes=len(data),
            storage_path=storage_path,
        )
        await self._repository.append_event(
            job_id=job_id,
            level=models.AgentJobEventLevel.INFO,
            message="Artifact uploaded",
            payload={"name": normalized_artifact_name, "sizeBytes": len(data)},
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

    async def list_attachments_for_user(
        self,
        *,
        job_id: UUID,
        actor_user_id: UUID | None,
        actor_is_superuser: bool = False,
        limit: int = 50,
    ) -> list[models.AgentJobArtifact]:
        """List attachment metadata for a job scoped to the requesting user."""

        if limit < 1 or limit > 500:
            raise AgentQueueValidationError("limit must be between 1 and 500")
        await self._assert_task_run_user_access(
            task_run_id=job_id,
            actor_user_id=actor_user_id,
            actor_is_superuser=actor_is_superuser,
        )
        artifacts = await self._list_input_artifacts(job_id=job_id, limit=limit)
        return artifacts

    async def list_attachments_for_worker(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        limit: int = 50,
    ) -> list[models.AgentJobArtifact]:
        """List attachment metadata for a job claimed by the worker."""

        if limit < 1 or limit > 500:
            raise AgentQueueValidationError("limit must be between 1 and 500")
        await self._assert_job_worker_ownership(job_id=job_id, worker_id=worker_id)
        artifacts = await self._list_input_artifacts(job_id=job_id, limit=limit)
        return artifacts

    async def get_attachment_download_for_user(
        self,
        *,
        job_id: UUID,
        attachment_id: UUID,
        actor_user_id: UUID | None,
        actor_is_superuser: bool = False,
    ) -> ArtifactDownload:
        """Resolve an attachment download for an authorized user."""

        await self._assert_task_run_user_access(
            task_run_id=job_id,
            actor_user_id=actor_user_id,
            actor_is_superuser=actor_is_superuser,
        )
        download = await self._get_attachment_download(
            job_id=job_id, attachment_id=attachment_id
        )
        await self._repository.append_event(
            job_id=job_id,
            level=models.AgentJobEventLevel.INFO,
            message="Attachment downloaded",
            payload={
                "actorUserId": str(actor_user_id) if actor_user_id else None,
                "attachmentId": str(attachment_id),
            },
        )
        await self._repository.commit()
        return download

    async def get_attachment_download_for_worker(
        self,
        *,
        job_id: UUID,
        attachment_id: UUID,
        worker_id: str,
    ) -> ArtifactDownload:
        """Resolve an attachment download for the claiming worker."""

        await self._assert_job_worker_ownership(job_id=job_id, worker_id=worker_id)
        download = await self._get_attachment_download(
            job_id=job_id, attachment_id=attachment_id
        )
        await self._repository.append_event(
            job_id=job_id,
            level=models.AgentJobEventLevel.INFO,
            message="Attachment downloaded",
            payload={"workerId": worker_id, "attachmentId": str(attachment_id)},
        )
        await self._repository.commit()
        return download

    async def _list_input_artifacts(
        self,
        *,
        job_id: UUID,
        limit: int,
    ) -> list[models.AgentJobArtifact]:
        return await self._repository.list_artifacts_with_prefix(
            job_id=job_id,
            prefix=_ATTACHMENT_NAMESPACE,
            limit=limit,
        )

    async def _get_attachment_download(
        self,
        *,
        job_id: UUID,
        attachment_id: UUID,
    ) -> ArtifactDownload:
        download = await self.get_artifact_download(
            job_id=job_id, artifact_id=attachment_id
        )
        if not download.artifact.name.startswith(_ATTACHMENT_NAMESPACE):
            raise AgentArtifactNotFoundError(attachment_id)
        return download

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
        before: Optional[datetime] = None,
        before_event_id: UUID | None = None,
        sort: Literal["asc", "desc"] = "asc",
    ) -> list[models.AgentJobEvent]:
        """List queue events for one job."""

        if limit < 1 or limit > 500:
            raise AgentQueueValidationError("limit must be between 1 and 500")
        if after is not None and before is not None:
            raise AgentQueueValidationError(
                "after and before cursors are mutually exclusive"
            )
        if after_event_id is not None and after is None:
            raise AgentQueueValidationError("afterEventId requires after timestamp")
        if before_event_id is not None and before is None:
            raise AgentQueueValidationError("beforeEventId requires before timestamp")
        if sort not in {"asc", "desc"}:
            raise AgentQueueValidationError("sort must be one of: asc, desc")
        if after is not None and after.tzinfo is None:
            after = after.replace(tzinfo=UTC)
        if before is not None and before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        return await self._repository.list_events(
            job_id=job_id,
            limit=limit,
            after=after,
            after_event_id=after_event_id,
            before=before,
            before_event_id=before_event_id,
            sort=sort,
        )

    async def get_live_session(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None = None,
        actor_is_superuser: bool = False,
    ) -> models.TaskRunLiveSession | None:
        """Fetch current live session state for a task run."""

        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            actor_is_superuser=actor_is_superuser,
        )
        return await self._repository.get_live_session(task_run_id=task_run_id)

    async def create_live_session(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
        actor_is_superuser: bool = False,
    ) -> models.TaskRunLiveSession:
        """Create or enable live session tracking for a task run."""

        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            actor_is_superuser=actor_is_superuser,
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
        actor_is_superuser: bool = False,
        ttl_minutes: int | None = None,
    ) -> LiveSessionWriteGrant:
        """Grant temporary RW reveal for an active live session."""

        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            actor_is_superuser=actor_is_superuser,
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
        actor_is_superuser: bool = False,
        reason: str | None = None,
    ) -> models.TaskRunLiveSession:
        """Revoke live session access and mark it terminally revoked."""

        await self._assert_task_run_user_access(
            task_run_id=task_run_id,
            actor_user_id=actor_user_id,
            actor_is_superuser=actor_is_superuser,
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
        actor_is_superuser: bool = False,
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
            actor_is_superuser=actor_is_superuser,
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
        actor_is_superuser: bool = False,
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
            actor_is_superuser=actor_is_superuser,
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

    async def recover_job(
        self,
        *,
        job_id: UUID,
        actor_user_id: UUID | None,
        actor_is_superuser: bool = False,
        mode: Literal["cancel", "clone"],
    ) -> tuple[models.AgentJob, models.AgentJob | None]:
        """Request cancellation and optionally clone a replacement job."""

        job = await self._repository.require_job_for_update(job_id)
        await self._assert_task_run_user_access(
            task_run_id=job_id,
            actor_user_id=actor_user_id,
            actor_is_superuser=actor_is_superuser,
        )
        if job.status not in {
            models.AgentJobStatus.RUNNING,
            models.AgentJobStatus.QUEUED,
        }:
            raise AgentJobStateError(
                f"Job {job_id} is {job.status.value} and cannot be recovered"
            )

        now = datetime.now(UTC)
        reason = "Operator recovery requested"
        if job.status is models.AgentJobStatus.RUNNING:
            if job.cancel_requested_at is None:
                job.cancel_requested_at = now
                job.cancel_requested_by_user_id = actor_user_id
                job.cancel_reason = reason
        else:
            job.status = models.AgentJobStatus.CANCELLED
            job.cancel_requested_at = now
            job.cancel_requested_by_user_id = actor_user_id
            job.cancel_reason = reason
            job.finished_at = now
            job.claimed_by = None
            job.lease_expires_at = None
            job.next_attempt_at = None
        job.updated_at = now
        await self._repository.append_event(
            job_id=job.id,
            level=models.AgentJobEventLevel.WARN,
            message="task.safeguard.recovery.requested",
            payload={
                "actorUserId": str(actor_user_id) if actor_user_id else None,
                "mode": mode,
            },
        )

        cloned_job: models.AgentJob | None = None
        if mode == "clone":
            source_payload = job.payload if isinstance(job.payload, dict) else {}
            cloned_job = await self.create_job(
                job_type=job.type,
                payload=copy.deepcopy(source_payload),
                priority=job.priority,
                created_by_user_id=actor_user_id or job.created_by_user_id,
                requested_by_user_id=actor_user_id or job.requested_by_user_id,
                affinity_key=job.affinity_key,
                max_attempts=job.max_attempts,
            )
            await self._repository.append_event(
                job_id=cloned_job.id,
                level=models.AgentJobEventLevel.INFO,
                message="task.safeguard.recovery.cloned",
                payload={"sourceJobId": str(job.id)},
            )

        await self._repository.commit()
        return job, cloned_job

    async def get_queue_safeguard_snapshot(
        self,
        *,
        limit: int = 200,
    ) -> QueueSafeguardSnapshot:
        """Return jobs matching safeguard alerts for operator review."""

        now = datetime.now(UTC)
        running_jobs = await self._repository.list_running_jobs(limit=limit)
        timed_out: list[QueueSafeguardJob] = []
        stale: list[QueueSafeguardJob] = []

        for job in running_jobs:
            runtime_seconds = self._job_runtime_seconds(job, now=now)
            lease_overdue = self._job_lease_overdue_seconds(job, now=now)
            if (
                self._max_runtime_seconds > 0
                and runtime_seconds is not None
                and runtime_seconds >= self._max_runtime_seconds
            ):
                timed_out.append(
                    QueueSafeguardJob(
                        job=job,
                        runtime_seconds=runtime_seconds,
                        lease_overdue_seconds=lease_overdue,
                    )
                )
            if (
                self._stale_lease_grace_seconds > 0
                and lease_overdue is not None
                and lease_overdue >= self._stale_lease_grace_seconds
            ):
                stale.append(
                    QueueSafeguardJob(
                        job=job,
                        runtime_seconds=runtime_seconds,
                        lease_overdue_seconds=lease_overdue,
                    )
                )

        return QueueSafeguardSnapshot(
            generated_at=now,
            max_runtime_seconds=self._max_runtime_seconds,
            stale_lease_grace_seconds=self._stale_lease_grace_seconds,
            timed_out=tuple(timed_out),
            stale=tuple(stale),
        )

    async def issue_worker_token(
        self,
        *,
        worker_id: str,
        description: Optional[str] = None,
        allowed_repositories: Optional[list[str]] = None,
        allowed_job_types: Optional[list[str]] = None,
        capabilities: Optional[list[str]] = None,
        runtime_capabilities: Optional[dict[str, Mapping[str, Any]]] = None,
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
            runtime_capabilities=self._normalize_runtime_capabilities(
                runtime_capabilities
            ),
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
        for job in jobs:
            job_volume_by_type[job.type] = job_volume_by_type.get(job.type, 0) + 1

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
            events_truncated=events_truncated,
        )

    async def require_worker_token(self, token_id: UUID) -> models.AgentWorkerToken:
        """Return worker token metadata by id or raise validation error."""

        try:
            return await self._repository.get_worker_token(token_id)
        except AgentWorkerTokenNotFoundError as exc:
            raise AgentQueueValidationError(str(exc)) from exc

    async def replace_worker_runtime_capabilities(
        self,
        *,
        token_id: UUID,
        runtime_capabilities: Optional[dict[str, Mapping[str, Any]]] = None,
    ) -> models.AgentWorkerToken:
        """Replace worker runtime capabilities metadata for an existing token."""

        normalized = self._normalize_runtime_capabilities(runtime_capabilities)
        try:
            token = await self._repository.replace_worker_token_runtime_capabilities(
                token_id=token_id,
                runtime_capabilities=normalized,
            )
        except AgentWorkerTokenNotFoundError as exc:
            raise AgentQueueValidationError(str(exc)) from exc
        await self._repository.commit()
        return token

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
    def _normalize_runtime_capability_values(
        runtime: str,
        payload: Mapping[str, Any],
        field_name: str,
    ) -> tuple[str, ...]:
        raw_values = payload.get(field_name)
        if raw_values is None:
            return ()
        if not isinstance(raw_values, list):
            raise AgentQueueValidationError(
                f"runtimeCapabilities.{runtime}.{field_name} must be a list of strings"
            )
        normalized = []
        for raw_value in raw_values:
            value = str(raw_value).strip()
            if value:
                normalized.append(value)
        return tuple(dict.fromkeys(normalized))

    @classmethod
    def _normalize_runtime_capabilities(
        cls,
        runtime_capabilities: Optional[dict[str, Mapping[str, Any]]],
    ) -> dict[str, Any] | None:
        if runtime_capabilities is None:
            return None
        if not isinstance(runtime_capabilities, Mapping):
            raise AgentQueueValidationError("runtimeCapabilities must be an object")

        normalized: dict[str, dict[str, list[str]]] = {}
        for raw_runtime, raw_payload in runtime_capabilities.items():
            runtime = str(raw_runtime).strip().lower()
            if not runtime:
                continue
            if runtime not in _RUNTIME_CAPABILITY_RUNTIMES:
                raise AgentQueueValidationError(
                    f"runtime '{runtime}' is not supported for runtime capabilities"
                )
            if hasattr(raw_payload, "model_dump"):
                normalized_payload = raw_payload.model_dump()
            elif isinstance(raw_payload, Mapping):
                normalized_payload = raw_payload
            else:
                raise AgentQueueValidationError(
                    f"runtimeCapabilities.{runtime} must be an object"
                )
            normalized_models = cls._normalize_runtime_capability_values(
                runtime=runtime,
                payload=normalized_payload,
                field_name="models",
            )
            normalized_efforts = cls._normalize_runtime_capability_values(
                runtime=runtime,
                payload=normalized_payload,
                field_name="efforts",
            )
            if not normalized_models and not normalized_efforts:
                normalized[runtime] = {"models": [], "efforts": []}
            else:
                normalized[runtime] = {
                    "models": list(normalized_models),
                    "efforts": list(normalized_efforts),
                }
        return normalized or None

    async def _load_system_metadata(self) -> QueueSystemMetadata:
        """Load the latest worker pause metadata snapshot."""

        state = await self._repository.get_pause_state()
        return self._to_queue_system_metadata(state)

    async def _resolve_existing_actor_user_id(
        self,
        actor_user_id: UUID | None,
    ) -> UUID | None:
        """Return actor id only when it still exists in persistence."""

        if actor_user_id is None:
            return None
        if await self._repository.user_exists(actor_user_id):
            return actor_user_id
        logger.warning(
            "worker pause actor missing in user table; recording null actor metadata",
            extra={"actorUserId": str(actor_user_id)},
        )
        return None

    async def _build_worker_pause_metrics(self) -> WorkerPauseMetrics:
        """Compute queued/running/stale counters for worker pause UX."""

        counts = await self._repository.fetch_worker_pause_metrics()
        return WorkerPauseMetrics(
            queued=counts.get("queued", 0),
            running=counts.get("running", 0),
            stale_running=counts.get("stale_running", 0),
        )

    async def _build_worker_pause_snapshot(
        self,
        *,
        audit_limit: int = 5,
    ) -> WorkerPauseSnapshot:
        """Return aggregated worker pause metadata/metrics/audit entries."""

        metadata = await self._load_system_metadata()
        metrics = await self._build_worker_pause_metrics()
        events = await self._repository.list_system_control_events(limit=audit_limit)
        audit = tuple(self._serialize_control_event(event) for event in events)
        return WorkerPauseSnapshot(system=metadata, metrics=metrics, audit_events=audit)

    @staticmethod
    def _to_queue_system_metadata(
        state: models.SystemWorkerPauseState,
    ) -> QueueSystemMetadata:
        """Convert ORM state into transport metadata structure."""

        updated_at = state.updated_at or datetime.now(UTC)
        version = int(state.version or 1)
        return QueueSystemMetadata(
            workers_paused=bool(state.paused),
            mode=state.mode,
            reason=state.reason,
            version=version,
            requested_by_user_id=state.requested_by_user_id,
            requested_at=state.requested_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _serialize_control_event(
        event: models.SystemControlEvent,
    ) -> WorkerPauseAuditEvent:
        """Convert ORM audit event into immutable dataclass."""

        created_at = event.created_at or datetime.now(UTC)
        return WorkerPauseAuditEvent(
            id=event.id,
            action=str(event.action or "").strip(),
            mode=event.mode,
            reason=event.reason,
            actor_user_id=event.actor_user_id,
            created_at=created_at,
        )

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

    async def _assert_job_worker_ownership(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        job: models.AgentJob | None = None,
    ) -> models.AgentJob:
        """Require that the provided worker currently owns the job claim."""

        worker = worker_id.strip()
        if not worker:
            raise AgentQueueValidationError("workerId must be a non-empty string")
        job_ref = job or await self._repository.require_job(job_id)
        if (
            job_ref.status is not models.AgentJobStatus.RUNNING
            or str(job_ref.claimed_by or "").strip() != worker
        ):
            raise AgentQueueAuthorizationError(
                f"worker '{worker}' does not own an active claim for job {job_id}"
            )
        return job_ref

    async def _assert_task_run_user_access(
        self,
        *,
        task_run_id: UUID,
        actor_user_id: UUID | None,
        actor_is_superuser: bool = False,
    ) -> models.AgentJob:
        """Require actor user ownership for live-session and control operations."""

        if actor_is_superuser:
            return await self._repository.require_job(task_run_id)

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
            task_publish.get("mode") or publish.get("mode") or source.get("publishMode")
        )
        try:
            return _normalize_publish_mode(publish)
        except TaskContractError:
            logger.debug(
                "Invalid publish mode %r found in payload for telemetry; defaulting to none",
                publish,
            )
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

    @staticmethod
    def _coerce_utc(dt: datetime) -> datetime:
        """Normalize persisted timestamps to UTC-aware datetimes."""

        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    def _job_runtime_seconds(
        self,
        job: models.AgentJob,
        *,
        now: datetime,
    ) -> int | None:
        if job.started_at is None:
            return None
        started_at = self._coerce_utc(job.started_at)
        return int((now - started_at).total_seconds())

    def _job_lease_overdue_seconds(
        self,
        job: models.AgentJob,
        *,
        now: datetime,
    ) -> int | None:
        if job.lease_expires_at is None:
            return None
        lease_expires_at = self._coerce_utc(job.lease_expires_at)
        if lease_expires_at >= now:
            return None
        return int((now - lease_expires_at).total_seconds())

    async def _maybe_trigger_runtime_timeout(
        self,
        *,
        job: models.AgentJob,
        now: datetime,
    ) -> None:
        """Automatically request cancellation when runtime exceeds limit."""

        if (
            self._max_runtime_seconds <= 0
            or job.started_at is None
            or job.cancel_requested_at is not None
            or job.status is not models.AgentJobStatus.RUNNING
        ):
            return

        runtime_seconds = (now - job.started_at).total_seconds()
        if runtime_seconds < self._max_runtime_seconds:
            return

        reason = (
            f"Job exceeded max runtime ({self._max_runtime_seconds} seconds); "
            "cancellation requested."
        )
        job.cancel_requested_at = now
        job.cancel_requested_by_user_id = None
        job.cancel_reason = reason
        job.error_message = job.error_message or reason
        job.updated_at = now
        await self._repository.append_event(
            job_id=job.id,
            level=models.AgentJobEventLevel.ERROR,
            message="task.safeguard.runtime_timeout",
            payload={
                "runtimeSeconds": int(runtime_seconds),
                "maxRuntimeSeconds": self._max_runtime_seconds,
            },
        )
