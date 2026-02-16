"""REST router for agent queue operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.schemas.agent_queue_models import (
    AppendJobEventRequest,
    ArtifactListResponse,
    ArtifactModel,
    ClaimJobRequest,
    ClaimJobResponse,
    CompleteJobRequest,
    CreateJobRequest,
    CreateWorkerTokenRequest,
    FailJobRequest,
    HeartbeatRequest,
    JobEventListResponse,
    JobEventModel,
    JobListResponse,
    JobModel,
    MigrationTelemetryResponse,
    WorkerTokenCreateResponse,
    WorkerTokenListResponse,
    WorkerTokenModel,
)
from moonmind.workflows import get_agent_queue_repository
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import (
    AgentArtifactJobMismatchError,
    AgentArtifactNotFoundError,
    AgentJobNotFoundError,
    AgentJobOwnershipError,
    AgentJobStateError,
    AgentQueueRepository,
    AgentWorkerTokenNotFoundError,
)
from moonmind.workflows.agent_queue.service import (
    AgentQueueAuthenticationError,
    AgentQueueAuthorizationError,
    AgentQueueService,
    AgentQueueValidationError,
    QueueMigrationTelemetry,
    WorkerAuthPolicy,
)

router = APIRouter(prefix="/api/queue", tags=["agent-queue"])
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _WorkerRequestAuth:
    """Resolved worker auth context used by mutation endpoints."""

    auth_source: str
    worker_id: Optional[str]
    allowed_repositories: tuple[str, ...]
    allowed_job_types: tuple[str, ...]
    capabilities: tuple[str, ...]


async def _get_repository(
    session: AsyncSession = Depends(get_async_session),
) -> AgentQueueRepository:
    return get_agent_queue_repository(session)


async def _get_service(
    repository: AgentQueueRepository = Depends(_get_repository),
) -> AgentQueueService:
    return AgentQueueService(repository)


def _serialize_job(job: models.AgentJob) -> JobModel:
    return JobModel.model_validate(job)


def _serialize_artifact(artifact: models.AgentJobArtifact) -> ArtifactModel:
    return ArtifactModel.model_validate(artifact)


def _serialize_event(event: models.AgentJobEvent) -> JobEventModel:
    return JobEventModel.model_validate(event)


def _serialize_worker_token(token: models.AgentWorkerToken) -> WorkerTokenModel:
    return WorkerTokenModel.model_validate(token)


async def _require_worker_auth(
    worker_token: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    service: AgentQueueService = Depends(_get_service),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> _WorkerRequestAuth:
    """Resolve worker auth from dedicated token or authenticated OIDC principal."""

    if worker_token:
        policy: WorkerAuthPolicy = await service.resolve_worker_token(worker_token)
        return _WorkerRequestAuth(
            auth_source=policy.auth_source,
            worker_id=policy.worker_id,
            allowed_repositories=policy.allowed_repositories,
            allowed_job_types=policy.allowed_job_types,
            capabilities=policy.capabilities,
        )

    # OIDC/JWT path: require non-disabled provider and authenticated user id.
    if (
        settings.oidc.AUTH_PROVIDER != "disabled"
        and getattr(user, "id", None) is not None
    ):
        return _WorkerRequestAuth(
            auth_source="oidc",
            worker_id=None,
            allowed_repositories=(),
            allowed_job_types=(),
            capabilities=(),
        )

    raise AgentQueueAuthenticationError(
        "worker authentication is required via X-MoonMind-Worker-Token or OIDC/JWT"
    )


def _ensure_worker_identity(worker_id: str, auth: _WorkerRequestAuth) -> None:
    """Enforce token-bound worker id match when token auth is used."""

    if (
        auth.auth_source == "worker_token"
        and auth.worker_id
        and worker_id != auth.worker_id
    ):
        raise AgentQueueAuthorizationError(
            f"workerId '{worker_id}' does not match token worker '{auth.worker_id}'"
        )


def _merge_allowed_types(
    request_types: Optional[list[str]],
    auth_types: tuple[str, ...],
) -> Optional[list[str]]:
    """Intersect request types with auth policy types when both exist."""

    cleaned_request = [item for item in (request_types or []) if str(item).strip()]
    if not auth_types:
        return cleaned_request or None
    if not cleaned_request:
        return list(auth_types)
    intersection = [item for item in cleaned_request if item in set(auth_types)]
    if not intersection:
        raise AgentQueueAuthorizationError(
            "requested allowedTypes do not overlap with worker token policy"
        )
    return intersection


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentQueueAuthenticationError):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "worker_auth_failed",
                "message": "Worker authentication failed.",
            },
        )
    if isinstance(exc, AgentQueueAuthorizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "worker_not_authorized",
                "message": "Worker is not authorized for this action.",
            },
        )
    if isinstance(exc, AgentJobNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "job_not_found",
                "message": "The requested job was not found.",
            },
        )
    if isinstance(exc, AgentJobOwnershipError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "job_ownership_mismatch",
                "message": "The job is not owned by this worker.",
            },
        )
    if isinstance(exc, AgentJobStateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "job_state_conflict",
                "message": "The job state does not permit this action.",
            },
        )
    if isinstance(exc, AgentArtifactNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "artifact_not_found",
                "message": "The requested artifact was not found.",
            },
        )
    if isinstance(exc, AgentArtifactJobMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "artifact_job_mismatch",
                "message": "The artifact does not belong to the requested job.",
            },
        )
    if isinstance(exc, AgentWorkerTokenNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "worker_token_not_found",
                "message": "The requested worker token was not found.",
            },
        )
    if isinstance(exc, AgentQueueValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        code = "invalid_queue_payload"
        message = "Queue request payload is invalid."
        lowered = str(exc).lower()
        if "exceeds max bytes" in lowered:
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            code = "artifact_too_large"
            message = "Artifact exceeds the maximum allowed size."
        elif "does not exist on disk" in lowered:
            status_code = status.HTTP_404_NOT_FOUND
            code = "artifact_file_missing"
            message = "Artifact file is missing from storage."
        return HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
            },
        )
    logger.exception("Unhandled agent queue exception")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "queue_internal_error",
            "message": "An unexpected queue error occurred.",
        },
    )


@router.post(
    "/jobs",
    response_model=JobModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_job(
    payload: CreateJobRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> JobModel:
    """Create a queued job for worker execution."""

    try:
        user_id = getattr(user, "id", None)
        job = await service.create_job(
            job_type=payload.type,
            payload=payload.payload,
            priority=payload.priority,
            created_by_user_id=user_id,
            requested_by_user_id=user_id,
            affinity_key=payload.affinity_key,
            max_attempts=payload.max_attempts,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    *,
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter: Optional[str] = Query(None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> JobListResponse:
    """List queue jobs with optional status and type filters."""

    parsed_status = None
    if status_filter is not None:
        try:
            parsed_status = models.AgentJobStatus(status_filter)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_status_filter",
                    "message": f"Unsupported status '{status_filter}'.",
                },
            ) from exc

    try:
        jobs = await service.list_jobs(
            status=parsed_status,
            job_type=type_filter,
            limit=limit,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return JobListResponse(items=[_serialize_job(job) for job in jobs])


@router.get("/telemetry/migration", response_model=MigrationTelemetryResponse)
async def migration_telemetry(
    *,
    window_hours: int = Query(168, alias="windowHours", ge=1, le=24 * 365),
    limit: int = Query(5000, ge=1, le=20000),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> MigrationTelemetryResponse:
    """Return migration telemetry for mixed-fleet and legacy deprecation rollout."""

    try:
        snapshot: QueueMigrationTelemetry = await service.get_migration_telemetry(
            window_hours=window_hours,
            limit=limit,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    return MigrationTelemetryResponse(
        generatedAt=snapshot.generated_at,
        windowHours=snapshot.window_hours,
        totalJobs=snapshot.total_jobs,
        legacyJobSubmissions=snapshot.legacy_job_submissions,
        eventsTruncated=snapshot.events_truncated,
        jobVolumeByType=snapshot.job_volume_by_type,
        failureCountsByRuntimeStage=snapshot.failure_counts_by_runtime_stage,
        publishOutcomes=snapshot.publish_outcomes,
    )


@router.get("/jobs/{job_id}", response_model=JobModel)
async def get_job(
    job_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> JobModel:
    """Return a single queue job."""

    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "job_not_found",
                "message": f"Job {job_id} was not found",
            },
        )
    return _serialize_job(job)


@router.post("/jobs/claim", response_model=ClaimJobResponse)
async def claim_job(
    payload: ClaimJobRequest,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> ClaimJobResponse:
    """Claim the next eligible job for a worker."""

    try:
        _ensure_worker_identity(payload.worker_id, worker_auth)
        merged_types = _merge_allowed_types(
            payload.allowed_types,
            worker_auth.allowed_job_types,
        )
        claim_capabilities = (
            list(worker_auth.capabilities)
            if worker_auth.capabilities
            else payload.worker_capabilities
        )
        job = await service.claim_job(
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
            allowed_types=merged_types,
            allowed_repositories=(
                list(worker_auth.allowed_repositories)
                if worker_auth.allowed_repositories
                else None
            ),
            worker_capabilities=claim_capabilities,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return ClaimJobResponse(job=_serialize_job(job) if job else None)


@router.post("/jobs/{job_id}/heartbeat", response_model=JobModel)
async def heartbeat_job(
    job_id: UUID,
    payload: HeartbeatRequest,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> JobModel:
    """Extend lease for a running job."""

    try:
        _ensure_worker_identity(payload.worker_id, worker_auth)
        job = await service.heartbeat(
            job_id=job_id,
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post("/jobs/{job_id}/complete", response_model=JobModel)
async def complete_job(
    job_id: UUID,
    payload: CompleteJobRequest,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> JobModel:
    """Mark a running job as completed."""

    try:
        _ensure_worker_identity(payload.worker_id, worker_auth)
        job = await service.complete_job(
            job_id=job_id,
            worker_id=payload.worker_id,
            result_summary=payload.result_summary,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post("/jobs/{job_id}/fail", response_model=JobModel)
async def fail_job(
    job_id: UUID,
    payload: FailJobRequest,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> JobModel:
    """Mark a running job as failed."""

    try:
        _ensure_worker_identity(payload.worker_id, worker_auth)
        job = await service.fail_job(
            job_id=job_id,
            worker_id=payload.worker_id,
            error_message=payload.error_message,
            retryable=payload.retryable,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post(
    "/jobs/{job_id}/artifacts/upload",
    response_model=ArtifactModel,
    status_code=status.HTTP_201_CREATED,
)
async def upload_artifact(
    job_id: UUID,
    file: UploadFile = File(...),
    worker_id: str = Form(..., alias="workerId"),
    name: str = Form(...),
    content_type: Optional[str] = Form(None, alias="contentType"),
    digest: Optional[str] = Form(None),
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> ArtifactModel:
    """Upload and persist artifact metadata for a queue job."""

    try:
        _ensure_worker_identity(worker_id, worker_auth)
        max_bytes = max(1, int(settings.spec_workflow.agent_job_artifact_max_bytes))
        payload = await file.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise AgentQueueValidationError(f"artifact exceeds max bytes ({max_bytes})")
        artifact = await service.upload_artifact(
            job_id=job_id,
            name=name,
            data=payload,
            content_type=content_type or file.content_type,
            digest=digest,
            worker_id=worker_id,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    finally:
        await file.close()
    return _serialize_artifact(artifact)


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    job_id: UUID,
    *,
    limit: int = Query(200, ge=1, le=500),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> ArtifactListResponse:
    """List artifact metadata for a queue job."""

    try:
        artifacts = await service.list_artifacts(job_id=job_id, limit=limit)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return ArtifactListResponse(items=[_serialize_artifact(item) for item in artifacts])


@router.get("/jobs/{job_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    job_id: UUID,
    artifact_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> FileResponse:
    """Download artifact binary for a queue job."""

    try:
        download = await service.get_artifact_download(
            job_id=job_id,
            artifact_id=artifact_id,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    return FileResponse(
        path=download.file_path,
        filename=download.artifact.name.split("/")[-1],
        media_type=download.artifact.content_type or "application/octet-stream",
    )


@router.post(
    "/jobs/{job_id}/events",
    response_model=JobEventModel,
    status_code=status.HTTP_201_CREATED,
)
async def append_job_event(
    job_id: UUID,
    payload: AppendJobEventRequest,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> JobEventModel:
    """Append one queue job event."""

    try:
        _ensure_worker_identity(payload.worker_id, worker_auth)
        event = await service.append_event(
            job_id=job_id,
            level=payload.level,
            message=payload.message,
            payload=payload.payload,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_event(event)


@router.get("/jobs/{job_id}/events", response_model=JobEventListResponse)
async def list_job_events(
    job_id: UUID,
    *,
    after: Optional[datetime] = Query(None, alias="after"),
    limit: int = Query(200, ge=1, le=500),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> JobEventListResponse:
    """List queue job events for polling-based progress updates."""

    try:
        events = await service.list_events(job_id=job_id, limit=limit, after=after)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return JobEventListResponse(items=[_serialize_event(event) for event in events])


@router.post(
    "/workers/tokens",
    response_model=WorkerTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_worker_token(
    payload: CreateWorkerTokenRequest,
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> WorkerTokenCreateResponse:
    """Create a worker token and return one-time raw secret."""

    try:
        issued = await service.issue_worker_token(
            worker_id=payload.worker_id,
            description=payload.description,
            allowed_repositories=payload.allowed_repositories,
            allowed_job_types=payload.allowed_job_types,
            capabilities=payload.capabilities,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    return WorkerTokenCreateResponse(
        token=issued.raw_token,
        worker_token=_serialize_worker_token(issued.token_record),
    )


@router.get("/workers/tokens", response_model=WorkerTokenListResponse)
async def list_worker_tokens(
    *,
    limit: int = Query(200, ge=1, le=500),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> WorkerTokenListResponse:
    """List worker token metadata."""

    try:
        items = await service.list_worker_tokens(limit=limit)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return WorkerTokenListResponse(
        items=[_serialize_worker_token(item) for item in items]
    )


@router.post("/workers/tokens/{token_id}/revoke", response_model=WorkerTokenModel)
async def revoke_worker_token(
    token_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> WorkerTokenModel:
    """Revoke one worker token."""

    try:
        token = await service.revoke_worker_token(token_id=token_id)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_worker_token(token)
