"""REST router for agent queue operations."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.schemas import QueueSystemMetadataModel
from api_service.auth_providers import (
    get_auth_manager,
    get_current_user,
    get_current_user_optional,
)
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.schemas.agent_queue_models import (
    AppendJobEventRequest,
    ArtifactListResponse,
    ArtifactModel,
    CancelJobAckRequest,
    CancelJobRequest,
    ClaimJobRequest,
    ClaimJobResponse,
    CompleteJobRequest,
    CreateJobRequest,
    CreateWorkerTokenRequest,
    FailJobRequest,
    GrantTaskRunLiveSessionWriteRequest,
    HeartbeatRequest,
    JobEventListResponse,
    JobEventModel,
    JobListResponse,
    JobModel,
    JobWithAttachmentsResponse,
    ManifestSecretProfileValue,
    ManifestSecretResolutionRequest,
    ManifestSecretResolutionResponse,
    ManifestSecretVaultValue,
    MigrationTelemetryResponse,
    QueueSafeguardJobModel,
    QueueSafeguardResponse,
    RecoverJobRequest,
    RecoverJobResponse,
    ResubmitJobRequest,
    RevokeTaskRunLiveSessionRequest,
    RuntimeCapabilities,
    TaskRunControlEventModel,
    TaskRunControlRequest,
    TaskRunLiveSessionModel,
    TaskRunLiveSessionResponse,
    TaskRunLiveSessionWriteGrantResponse,
    TaskRunOperatorMessageRequest,
    UpdateQueuedJobRequest,
    WorkerRuntimeCapabilitiesRequest,
    WorkerRuntimeCapabilitiesResponse,
    WorkerRuntimeStateRequest,
    WorkerTokenCreateResponse,
    WorkerTokenListResponse,
    WorkerTokenModel,
)
from moonmind.workflows import get_agent_queue_repository
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.job_types import MANIFEST_JOB_TYPE
from moonmind.workflows.agent_queue.manifest_contract import ManifestContractError
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
    AgentQueueJobAuthorizationError,
    AgentQueueService,
    AgentQueueValidationError,
    AttachmentUpload,
    LiveSessionNotFoundError,
    LiveSessionStateError,
    QueueMigrationTelemetry,
    QueueSafeguardJob,
    QueueSafeguardSnapshot,
    QueueSystemMetadata,
    WorkerAuthPolicy,
)

router = APIRouter(prefix="/api/queue", tags=["agent-queue"])
logger = logging.getLogger(__name__)

_RUNTIME_CAPABILITY_RUNTIMES = ("codex", "gemini", "claude", "jules")

_QUEUE_LIST_TASK_INSTRUCTION_MAX_CHARS = 400


def _log_queue_validation_failure(
    request: Request,
    *,
    user_id: UUID | None,
    job_type: str | None,
    error: AgentQueueValidationError,
    payload_summary: dict[str, Any] | None = None,
) -> None:
    request_id = request.headers.get("x-request-id") or request.headers.get(
        "x-correlation-id"
    )
    client_host = request.client.host if request.client else None
    context = {
        "path": request.url.path,
        "method": request.method,
        "request_id": request_id,
        "user_id": str(user_id) if user_id is not None else None,
        "job_type": job_type,
        "client_host": client_host,
        "payload_summary": payload_summary or {},
    }
    logger.warning(
        "Queue task validation failed: %s | context=%s",
        str(error),
        context,
    )


@dataclass(frozen=True, slots=True)
class _WorkerRequestAuth:
    """Resolved worker auth context used by mutation endpoints."""

    auth_source: str
    worker_id: Optional[str]
    allowed_repositories: tuple[str, ...]
    allowed_job_types: tuple[str, ...]
    capabilities: tuple[str, ...]
    token_id: Optional[UUID] = None


async def _get_repository(
    session: AsyncSession = Depends(get_async_session),
) -> AgentQueueRepository:
    return get_agent_queue_repository(session)


async def _get_service(
    repository: AgentQueueRepository = Depends(_get_repository),
) -> AgentQueueService:
    return AgentQueueService(repository)


def _require_queue_operator(user: User) -> None:
    """Require superuser role for queue operator-only endpoints."""

    if not bool(getattr(user, "is_superuser", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "operator_role_required",
                "message": "Operator privileges are required.",
            },
        )


def _has_operator_override(user: User) -> bool:
    """Treat disabled-auth mode as operator-equivalent for queue mutations."""

    return settings.oidc.AUTH_PROVIDER == "disabled" or bool(
        getattr(user, "is_superuser", False)
    )


def _serialize_job(
    job: models.AgentJob,
    system: QueueSystemMetadata | None = None,
) -> JobModel:
    return _build_job_model(job=job, payload=job.payload, system=system)


def _build_job_model(
    *,
    job: models.AgentJob,
    payload: Any,
    system: QueueSystemMetadata | None = None,
) -> JobModel:
    model_payload: dict[str, Any]
    if isinstance(payload, dict):
        model_payload = payload
    else:
        logger.warning(
            "Queue job %s returned non-dict payload (%s); serializing as empty payload.",
            job.id,
            type(payload).__name__,
        )
        model_payload = {}
    return JobModel(
        id=job.id,
        type=job.type,
        status=job.status,
        priority=job.priority,
        payload=model_payload,
        created_by_user_id=job.created_by_user_id,
        requested_by_user_id=job.requested_by_user_id,
        cancel_requested_by_user_id=job.cancel_requested_by_user_id,
        cancel_requested_at=job.cancel_requested_at,
        cancel_reason=job.cancel_reason,
        affinity_key=job.affinity_key,
        claimed_by=job.claimed_by,
        lease_expires_at=job.lease_expires_at,
        next_attempt_at=job.next_attempt_at,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        result_summary=job.result_summary,
        error_message=job.error_message,
        finish_outcome_code=getattr(job, "finish_outcome_code", None),
        finish_outcome_stage=getattr(job, "finish_outcome_stage", None),
        finish_outcome_reason=getattr(job, "finish_outcome_reason", None),
        finish_summary=getattr(job, "finish_summary_json", None),
        artifacts_path=job.artifacts_path,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        system=_serialize_system_metadata(system) if system is not None else None,
    )


def _serialize_system_metadata(
    metadata: QueueSystemMetadata,
) -> QueueSystemMetadataModel:
    return QueueSystemMetadataModel.from_service_metadata(metadata)


def _serialize_artifact(artifact: models.AgentJobArtifact) -> ArtifactModel:
    return ArtifactModel.model_validate(artifact)


def _serialize_event(event: models.AgentJobEvent) -> JobEventModel:
    return JobEventModel.model_validate(event)


def _serialize_worker_token(token: models.AgentWorkerToken) -> WorkerTokenModel:
    return WorkerTokenModel.model_validate(token)


def _normalize_runtime_capability_values(
    raw_values: object,
) -> list[str]:
    """Normalize runtime capability values to a stable ordered unique list."""

    if not isinstance(raw_values, (list, tuple)):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _normalize_runtime_capability_entry(
    raw_payload: object,
) -> tuple[list[str], list[str]]:
    """Extract and normalize one runtime's model/effort option list."""

    if not isinstance(raw_payload, dict):
        return [], []
    return (
        _normalize_runtime_capability_values(raw_payload.get("models")),
        _normalize_runtime_capability_values(raw_payload.get("efforts")),
    )


def _merge_runtime_capability_values(
    *,
    base: list[str],
    additions: list[str],
) -> list[str]:
    """Merge ordered string values while preserving prior items and uniqueness."""

    merged = list(base)
    for value in additions:
        if value not in merged:
            merged.append(value)
    return merged


def _serialize_safeguard_job(
    entry: QueueSafeguardJob,
) -> QueueSafeguardJobModel:
    job = entry.job
    return QueueSafeguardJobModel(
        id=job.id,
        status=job.status,
        claimed_by=job.claimed_by,
        started_at=job.started_at,
        lease_expires_at=job.lease_expires_at,
        cancel_requested_at=job.cancel_requested_at,
        cancel_reason=job.cancel_reason,
        runtime_seconds=entry.runtime_seconds,
        lease_overdue_seconds=entry.lease_overdue_seconds,
    )


def _coerce_summary_text(
    value: object,
    *,
    max_chars: int | None = None,
) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if max_chars is not None and len(normalized) > max_chars:
        return normalized[:max_chars]
    return normalized


def _summarize_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    summary_payload: dict[str, Any] = {}
    summary_task: dict[str, Any] = {}

    direct_runtime = None
    for key in ("targetRuntime", "target_runtime", "runtime"):
        if (value := payload.get(key)) is not None:
            direct_runtime = value
            break
    direct_runtime_value = _coerce_summary_text(direct_runtime)
    if direct_runtime_value:
        summary_payload["runtime"] = direct_runtime_value

    direct_publish_payload = payload.get("publish")
    direct_publish_mode_value = None
    if isinstance(direct_publish_payload, dict):
        direct_publish_mode_value = _coerce_summary_text(
            direct_publish_payload.get("mode")
        )
    if direct_publish_mode_value is None:
        direct_publish_mode_value = _coerce_summary_text(payload.get("publishMode"))
    if direct_publish_mode_value:
        summary_payload["publish"] = {"mode": direct_publish_mode_value}

    task_payload = payload.get("task")
    if isinstance(task_payload, dict):
        runtime_payload = task_payload.get("runtime")
        runtime_value = None
        if isinstance(runtime_payload, dict):
            runtime_value = _coerce_summary_text(runtime_payload.get("mode"))
        if runtime_value is None:
            for key in ("targetRuntime", "target_runtime", "runtime"):
                if value := _coerce_summary_text(task_payload.get(key)):
                    runtime_value = value
                    break
        if runtime_value is not None:
            summary_task["runtime"] = {"mode": runtime_value}

        skill_payload = task_payload.get("skill")
        if isinstance(skill_payload, dict):
            skill_id = _coerce_summary_text(skill_payload.get("id"))
            if skill_id:
                summary_task["skill"] = {"id": skill_id}

        publish_payload = task_payload.get("publish")
        if isinstance(publish_payload, dict):
            publish_mode = _coerce_summary_text(publish_payload.get("mode"))
            if publish_mode:
                summary_task["publish"] = {"mode": publish_mode}

        task_instructions = _coerce_summary_text(
            task_payload.get("instructions"),
            max_chars=_QUEUE_LIST_TASK_INSTRUCTION_MAX_CHARS,
        )
        if task_instructions:
            summary_task["instructions"] = task_instructions

    if summary_task:
        summary_payload["task"] = summary_task

    payload_instruction = _coerce_summary_text(
        payload.get("instruction"),
        max_chars=_QUEUE_LIST_TASK_INSTRUCTION_MAX_CHARS,
    )
    if payload_instruction:
        summary_payload["instruction"] = payload_instruction

    # Keep the summary payload intentionally narrow to reduce serialization overhead.
    return summary_payload


def _extract_manifest_secret_refs(
    payload: dict[str, Any] | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return normalized profile/vault secret refs from a manifest queue payload."""

    if not isinstance(payload, dict):
        return ([], [])
    refs_obj = payload.get("manifestSecretRefs")
    if not isinstance(refs_obj, dict):
        return ([], [])

    profile_refs: list[dict[str, str]] = []
    seen_profile: set[str] = set()
    profile_payload = refs_obj.get("profile")
    if not isinstance(profile_payload, list):
        profile_payload = []
    for entry in profile_payload:
        if not isinstance(entry, dict):
            continue
        env_key = str(entry.get("envKey") or "").strip()
        if not env_key:
            continue
        dedupe_key = str(entry.get("normalized") or env_key).strip().lower()
        if dedupe_key in seen_profile:
            continue
        seen_profile.add(dedupe_key)
        profile_refs.append(
            {
                "provider": str(entry.get("provider") or "").strip(),
                "field": str(entry.get("field") or "").strip(),
                "envKey": env_key,
                "normalized": str(entry.get("normalized") or "").strip(),
            }
        )

    vault_refs: list[dict[str, str]] = []
    seen_vault: set[str] = set()
    vault_payload = refs_obj.get("vault")
    if not isinstance(vault_payload, list):
        vault_payload = []
    for entry in vault_payload:
        if not isinstance(entry, dict):
            continue
        ref = str(entry.get("ref") or "").strip()
        if not ref or ref in seen_vault:
            continue
        seen_vault.add(ref)
        vault_refs.append(
            {
                "mount": str(entry.get("mount") or "").strip(),
                "path": str(entry.get("path") or "").strip(),
                "field": str(entry.get("field") or "").strip(),
                "ref": ref,
            }
        )

    return (profile_refs, vault_refs)


def _serialize_job_for_list(
    job: models.AgentJob,
    *,
    compact_payload: bool,
) -> JobModel:
    if not compact_payload:
        return _serialize_job(job)
    payload = (
        _summarize_job_payload(job.payload) if isinstance(job.payload, dict) else {}
    )
    return _build_job_model(job=job, payload=payload)


async def _require_worker_auth(
    worker_token: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    service: AgentQueueService = Depends(_get_service),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> _WorkerRequestAuth:
    """Resolve worker auth from dedicated token or authenticated OIDC principal."""

    if worker_token:
        try:
            policy: WorkerAuthPolicy = await service.resolve_worker_token(worker_token)
        except Exception as exc:
            raise _to_http_exception(exc) from exc
        return _WorkerRequestAuth(
            auth_source=policy.auth_source,
            worker_id=policy.worker_id,
            allowed_repositories=policy.allowed_repositories,
            allowed_job_types=policy.allowed_job_types,
            capabilities=policy.capabilities,
            token_id=getattr(policy, "token_id", None),
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
            token_id=None,
        )

    raise _to_http_exception(
        AgentQueueAuthenticationError(
            "worker authentication is required via X-MoonMind-Worker-Token or OIDC/JWT"
        )
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
    if isinstance(exc, AgentQueueJobAuthorizationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "job_not_authorized",
                "message": "User is not authorized for this job.",
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
    if isinstance(exc, LiveSessionNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "live_session_not_found",
                "message": "Live session is not enabled for this task run.",
            },
        )
    if isinstance(exc, LiveSessionStateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "live_session_state_conflict",
                "message": "Live session is not in a valid state for this action.",
            },
        )
    if isinstance(exc, AgentQueueValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        code = "invalid_queue_payload"
        message = "Queue request payload is invalid."
        raw_message = str(exc).strip()
        detail: dict[str, Any] = {
            "code": code,
            "message": message,
            "debugMessage": raw_message,
        }
        lowered = str(exc).lower()
        if "targetruntime=claude requires anthropic_api_key" in lowered:
            return HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "claude_runtime_disabled",
                    "message": "targetRuntime=claude is not available in the current server configuration",
                },
            )
        if "targetruntime=jules requires jules_enabled=true" in lowered:
            return HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "jules_runtime_disabled",
                    "message": "targetRuntime=jules is not available in the current server configuration",
                },
            )
        if "attachments exceed max count" in lowered:
            code = "attachments_too_many"
            message = "Too many attachments were provided."
            detail["code"] = code
        elif "attachments exceed max total bytes" in lowered:
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            code = "attachment_total_too_large"
            message = "Combined attachment size exceeds the maximum allowed total."
            detail["code"] = code
        elif "attachment exceeds max bytes" in lowered:
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            code = "attachment_too_large"
            message = "Attachment exceeds the maximum allowed size."
            detail["code"] = code
        elif (
            "attachment content type" in lowered
            or "attachment type" in lowered
            or "attachment format" in lowered
            or "attachment content type must be" in lowered
            or "content type must be" in lowered
        ):
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            code = "attachment_type_not_allowed"
            message = "Attachment content type is not allowed."
            detail["code"] = code
        elif "artifact exceeds max bytes" in lowered or "exceeds max bytes" in lowered:
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            code = "artifact_too_large"
            message = "Artifact exceeds the maximum allowed size."
            detail["code"] = code
        elif "does not exist on disk" in lowered:
            status_code = status.HTTP_404_NOT_FOUND
            code = "artifact_file_missing"
            message = "Artifact file is missing from storage."
            detail["code"] = code
        else:
            cause = getattr(exc, "__cause__", None)
            while isinstance(cause, Exception):
                if isinstance(cause, ManifestContractError):
                    # Surface actionable manifest contract failures to API clients.
                    message = raw_message
                    break
                cause = getattr(cause, "__cause__", None)

        detail["code"] = code
        detail["message"] = message
        detail["code"] = code
        return HTTPException(status_code=status_code, detail=detail)
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
    request: Request,
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
    except AgentQueueValidationError as exc:
        payload_summary: dict[str, Any] = {
            "payload_type": type(payload.payload).__name__,
            "payload_keys": (
                list(payload.payload.keys())
                if isinstance(payload.payload, dict)
                else []
            ),
            "has_affinity_key": payload.affinity_key is not None,
            "priority": payload.priority,
            "max_attempts": payload.max_attempts,
        }
        _log_queue_validation_failure(
            request=request,
            user_id=user_id,
            job_type=payload.type,
            error=exc,
            payload_summary=payload_summary,
        )
        if payload.type == MANIFEST_JOB_TYPE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_manifest_job",
                    "message": str(exc),
                },
            ) from exc
        raise _to_http_exception(exc) from exc
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.put("/jobs/{job_id}", response_model=JobModel)
async def update_queued_job(
    job_id: UUID,
    payload: UpdateQueuedJobRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> JobModel:
    """Update one queued, never-started task job in place."""

    try:
        user_id = getattr(user, "id", None)
        job = await service.update_queued_job(
            job_id=job_id,
            actor_user_id=user_id,
            actor_is_superuser=_has_operator_override(user),
            job_type=payload.type,
            payload=payload.payload,
            priority=payload.priority,
            affinity_key=payload.affinity_key,
            max_attempts=payload.max_attempts,
            expected_updated_at=payload.expected_updated_at,
            note=payload.note,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post(
    "/jobs/{job_id}/resubmit",
    response_model=JobModel,
    status_code=status.HTTP_201_CREATED,
)
async def resubmit_job(
    job_id: UUID,
    payload: ResubmitJobRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> JobModel:
    """Create a new queued task job from a failed/cancelled source job."""

    try:
        user_id = getattr(user, "id", None)
        job = await service.resubmit_job(
            job_id=job_id,
            actor_user_id=user_id,
            actor_is_superuser=_has_operator_override(user),
            job_type=payload.type,
            payload=payload.payload,
            priority=payload.priority,
            affinity_key=payload.affinity_key,
            max_attempts=payload.max_attempts,
            note=payload.note,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post(
    "/jobs/with-attachments",
    response_model=JobWithAttachmentsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_job_with_attachments(
    request: Request,
    request_payload: str = Form(..., alias="request"),
    files: list[UploadFile] = File(..., alias="files"),
    captions_json: str | None = Form(None, alias="captions"),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> JobWithAttachmentsResponse:
    """Create a queued job that includes user-provided attachments."""

    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "attachments_required",
                "message": "At least one attachment must be provided.",
            },
        )

    try:
        create_request = CreateJobRequest.model_validate_json(request_payload)
    except ValidationError as exc:
        logger.warning(
            "Invalid attachment queue payload for path=%s request_id=%s: %s",
            request.url.path,
            request.headers.get("x-request-id")
            or request.headers.get("x-correlation-id"),
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_queue_payload",
                "message": "Queue request payload is invalid.",
            },
        ) from exc

    captions: dict[str, str] = {}
    if captions_json:
        try:
            parsed = json.loads(captions_json)
        except json.JSONDecodeError as exc:  # pragma: no cover - invalid form payload
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_attachment_captions",
                    "message": "Attachment captions must be valid JSON.",
                },
            ) from exc
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                caption_text = str(value or "").strip()
                if caption_text:
                    captions[str(key or "").strip()] = caption_text
        elif parsed is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_attachment_captions",
                    "message": "Attachment captions must be provided as a JSON object.",
                },
            )

    max_count = max(1, int(settings.spec_workflow.agent_job_attachment_max_count))
    if len(files) > max_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "attachments_too_many",
                "message": "Too many attachments were provided.",
            },
        )

    if captions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "attachment_captions_not_supported",
                "message": "Attachment captions are not yet supported.",
            },
        )

    per_file_limit = max(1, int(settings.spec_workflow.agent_job_attachment_max_bytes))
    total_limit = max(1, int(settings.spec_workflow.agent_job_attachment_total_bytes))
    attachments: list[AttachmentUpload] = []
    total_bytes = 0

    for upload in files:
        try:
            payload = await upload.read(per_file_limit + 1)
        finally:
            await upload.close()
        if len(payload) > per_file_limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "attachment_too_large",
                    "message": "Attachment exceeds the maximum allowed size.",
                },
            )
        total_bytes += len(payload)
        if total_bytes > total_limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "attachment_total_too_large",
                    "message": "Combined attachment size exceeds the maximum allowed total.",
                },
            )
        attachments.append(
            AttachmentUpload(
                filename=upload.filename or "",
                content_type=upload.content_type,
                data=payload,
                caption=captions.get((upload.filename or "").strip()),
            )
        )

    try:
        user_id = getattr(user, "id", None)
        job, stored = await service.create_job_with_attachments(
            job_type=create_request.type,
            payload=create_request.payload,
            priority=create_request.priority,
            created_by_user_id=user_id,
            requested_by_user_id=user_id,
            affinity_key=create_request.affinity_key,
            max_attempts=create_request.max_attempts,
            attachments=attachments,
        )
    except AgentQueueValidationError as exc:
        payload_summary = {
            "request_payload_keys": (
                list(create_request.payload.keys())
                if isinstance(create_request.payload, dict)
                else []
            ),
            "attachment_count": len(attachments),
            "total_bytes": total_bytes,
            "has_affinity_key": create_request.affinity_key is not None,
            "priority": create_request.priority,
            "max_attempts": create_request.max_attempts,
        }
        _log_queue_validation_failure(
            request=request,
            user_id=user_id,
            job_type=create_request.type,
            error=exc,
            payload_summary=payload_summary,
        )
        raise _to_http_exception(exc) from exc
    except Exception as exc:  # pragma: no cover - service layer
        raise _to_http_exception(exc) from exc

    return JobWithAttachmentsResponse(
        job=_serialize_job(job),
        attachments=[_serialize_artifact(item) for item in stored],
    )


@router.get(
    "/jobs",
    response_model=JobListResponse,
    response_model_exclude={"items": {"__all__": {"finish_summary"}}},
)
async def list_jobs(
    *,
    status_filter: Optional[str] = Query(None, alias="status"),
    type_filter: Optional[str] = Query(None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, alias="cursor"),
    offset: int | None = Query(None, ge=0),
    summary: bool = Query(False, alias="summary"),
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

    cursor_token = str(cursor).strip() if cursor is not None else None
    if cursor_token == "":
        cursor_token = None
    if cursor_token is not None and offset is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_pagination_args",
                "message": "cursor and offset cannot be used together.",
            },
        )

    try:
        if cursor_token is not None or offset is None:
            page = await service.list_jobs_page(
                status=parsed_status,
                job_type=type_filter,
                limit=limit,
                cursor=cursor_token,
            )
            items = list(page.items)
            next_cursor = page.next_cursor
            has_more = next_cursor is not None
            effective_offset = 0
        else:
            fetch_limit = limit + 1
            jobs = await service.list_jobs(
                status=parsed_status,
                job_type=type_filter,
                limit=fetch_limit,
                offset=offset,
            )
            has_more = len(jobs) > limit
            items = jobs[:limit]
            next_cursor = None
            effective_offset = offset
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    return JobListResponse(
        items=[_serialize_job_for_list(job, compact_payload=summary) for job in items],
        offset=effective_offset,
        limit=limit,
        has_more=has_more,
        page_size=limit,
        next_cursor=next_cursor,
    )


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
        eventsTruncated=snapshot.events_truncated,
        jobVolumeByType=snapshot.job_volume_by_type,
        failureCountsByRuntimeStage=snapshot.failure_counts_by_runtime_stage,
        publishOutcomes=snapshot.publish_outcomes,
    )


@router.get("/telemetry/safeguards", response_model=QueueSafeguardResponse)
async def queue_safeguards(
    *,
    limit: int = Query(200, ge=1, le=500),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> QueueSafeguardResponse:
    """Return queue safeguard alerts for operator triage."""

    _require_queue_operator(user)

    try:
        snapshot: QueueSafeguardSnapshot = await service.get_queue_safeguard_snapshot(
            limit=limit
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    return QueueSafeguardResponse(
        generated_at=snapshot.generated_at,
        max_runtime_seconds=snapshot.max_runtime_seconds,
        stale_lease_grace_seconds=snapshot.stale_lease_grace_seconds,
        timed_out=[_serialize_safeguard_job(entry) for entry in snapshot.timed_out],
        stale_leases=[_serialize_safeguard_job(entry) for entry in snapshot.stale],
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


@router.get("/jobs/{job_id}/finish-summary", response_model=dict[str, Any])
async def get_job_finish_summary(
    job_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    """Return structured finish summary for a queue job when available."""

    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "job_not_found",
                "message": f"Job {job_id} was not found",
            },
        )
    summary = getattr(job, "finish_summary_json", None)
    if isinstance(summary, dict):
        return summary
    return {}


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
        claim_result = await service.claim_job(
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
    serialized_job = (
        _serialize_job(claim_result.job) if claim_result.job is not None else None
    )
    system_model = _serialize_system_metadata(claim_result.system)
    return ClaimJobResponse(job=serialized_job, system=system_model)


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
        heartbeat_result = await service.heartbeat(
            job_id=job_id,
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(heartbeat_result.job, heartbeat_result.system)


@router.post("/jobs/{job_id}/runtime-state", response_model=JobModel)
async def update_job_runtime_state(
    job_id: UUID,
    payload: WorkerRuntimeStateRequest,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> JobModel:
    """Persist worker runtime checkpoint state for a running claimed job."""

    try:
        _ensure_worker_identity(payload.worker_id, worker_auth)
        job = await service.update_runtime_state(
            job_id=job_id,
            worker_id=payload.worker_id,
            runtime_state=payload.runtime_state,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post(
    "/jobs/{job_id}/manifest/secrets",
    response_model=ManifestSecretResolutionResponse,
)
async def resolve_manifest_job_secrets(
    job_id: UUID,
    payload: ManifestSecretResolutionRequest,
    service: AgentQueueService = Depends(_get_service),
    auth_manager=Depends(get_auth_manager),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> ManifestSecretResolutionResponse:
    """Resolve manifest profile references for the running, claimed worker job."""

    try:
        if worker_auth.auth_source != "worker_token" or not worker_auth.worker_id:
            raise AgentQueueAuthorizationError(
                "manifest secret resolution requires worker-token authentication"
            )
        available_caps = {
            str(token or "").strip().lower() for token in worker_auth.capabilities
        }
        if MANIFEST_JOB_TYPE not in available_caps:
            raise AgentQueueAuthorizationError(
                "worker token does not allow manifest secret resolution"
            )

        job = await service.get_job(job_id)
        if job is None:
            raise AgentJobNotFoundError(job_id)
        if job.type != MANIFEST_JOB_TYPE:
            raise AgentQueueValidationError(
                "manifest secret resolution only supports manifest jobs"
            )
        if job.status is not models.AgentJobStatus.RUNNING:
            raise AgentJobStateError(
                f"Job {job_id} is {job.status.value} and cannot resolve secrets"
            )
        if job.claimed_by != worker_auth.worker_id:
            raise AgentQueueAuthorizationError(
                f"Job {job_id} is owned by {job.claimed_by or 'none'}"
            )

        profile_refs, vault_refs = _extract_manifest_secret_refs(job.payload)
        profile_items: list[ManifestSecretProfileValue] = []
        unresolved: list[str] = []
        requester_user = (
            SimpleNamespace(id=job.requested_by_user_id)
            if job.requested_by_user_id is not None
            else None
        )
        if payload.include_profile:
            for ref in profile_refs:
                env_key = ref["envKey"]
                value = await auth_manager.get_secret(
                    "profile",
                    key=env_key,
                    user=requester_user,
                )
                if not value:
                    unresolved.append(env_key)
                    continue
                profile_items.append(
                    ManifestSecretProfileValue(
                        provider=ref["provider"] or None,
                        field=ref["field"] or None,
                        env_key=env_key,
                        normalized=ref["normalized"] or None,
                        value=str(value),
                    )
                )

            if unresolved:
                unresolved_text = ", ".join(sorted(set(unresolved)))
                raise AgentQueueValidationError(
                    f"manifest profile secret references could not be resolved: {unresolved_text}"
                )

        vault_items: list[ManifestSecretVaultValue] = []
        if payload.include_vault:
            vault_items = [
                ManifestSecretVaultValue(
                    mount=ref["mount"] or None,
                    path=ref["path"] or None,
                    field=ref["field"] or None,
                    ref=ref["ref"],
                )
                for ref in vault_refs
            ]

        return ManifestSecretResolutionResponse(
            profile=profile_items,
            vault=vault_items,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc


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
            finish_outcome_code=payload.finish_outcome_code,
            finish_outcome_stage=payload.finish_outcome_stage,
            finish_outcome_reason=payload.finish_outcome_reason,
            finish_summary=payload.finish_summary,
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
            finish_outcome_code=payload.finish_outcome_code,
            finish_outcome_stage=payload.finish_outcome_stage,
            finish_outcome_reason=payload.finish_outcome_reason,
            finish_summary=payload.finish_summary,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobModel)
async def cancel_job(
    job_id: UUID,
    payload: CancelJobRequest | None = Body(None),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> JobModel:
    """Request cancellation for a queued or running queue job."""

    try:
        user_id = getattr(user, "id", None)
        job = await service.request_cancel(
            job_id=job_id,
            requested_by_user_id=user_id,
            reason=(payload.reason if payload is not None else None),
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post("/jobs/{job_id}/cancel/ack", response_model=JobModel)
async def ack_cancel_job(
    job_id: UUID,
    payload: CancelJobAckRequest,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> JobModel:
    """Acknowledge cancellation for a running job owned by the worker."""

    try:
        _ensure_worker_identity(payload.worker_id, worker_auth)
        job = await service.ack_cancel(
            job_id=job_id,
            worker_id=payload.worker_id,
            message=payload.message,
            finish_outcome_code=payload.finish_outcome_code,
            finish_outcome_stage=payload.finish_outcome_stage,
            finish_outcome_reason=payload.finish_outcome_reason,
            finish_summary=payload.finish_summary,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post("/jobs/{job_id}/recover", response_model=RecoverJobResponse)
async def recover_job(
    job_id: UUID,
    payload: RecoverJobRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> RecoverJobResponse:
    """Cancel a stuck job and optionally clone a replacement."""

    try:
        recovered, cloned = await service.recover_job(
            job_id=job_id,
            actor_user_id=getattr(user, "id", None),
            actor_is_operator=bool(getattr(user, "is_superuser", False)),
            mode=payload.mode,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    return RecoverJobResponse(
        recovered_job=_serialize_job(recovered),
        cloned_job=_serialize_job(cloned) if cloned else None,
    )


@router.post(
    "/jobs/{job_id}/live-session",
    response_model=TaskRunLiveSessionResponse,
)
async def create_job_live_session(
    job_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionResponse:
    """Idempotently create/enable live session state for one queue task run."""

    try:
        live = await service.create_live_session(
            task_run_id=job_id,
            actor_user_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )


@router.get(
    "/jobs/{job_id}/live-session",
    response_model=TaskRunLiveSessionResponse,
)
async def get_job_live_session(
    job_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionResponse:
    """Fetch live session state for one queue task run."""

    try:
        live = await service.get_live_session(
            task_run_id=job_id,
            actor_user_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    if live is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "live_session_not_found",
                "message": "Live session is not enabled for this task run.",
            },
        )
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )


@router.post(
    "/jobs/{job_id}/live-session/grant-write",
    response_model=TaskRunLiveSessionWriteGrantResponse,
)
async def grant_job_live_session_write(
    job_id: UUID,
    payload: GrantTaskRunLiveSessionWriteRequest = Body(
        default_factory=GrantTaskRunLiveSessionWriteRequest
    ),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionWriteGrantResponse:
    """Return temporary RW attach details for one queue task run."""

    try:
        grant = await service.grant_live_session_write(
            task_run_id=job_id,
            actor_user_id=getattr(user, "id", None),
            ttl_minutes=payload.ttl_minutes,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionWriteGrantResponse(
        session=TaskRunLiveSessionModel.model_validate(grant.session),
        attach_rw=grant.attach_rw,
        web_rw=grant.web_rw,
        granted_until=grant.granted_until,
    )


@router.post(
    "/jobs/{job_id}/live-session/revoke",
    response_model=TaskRunLiveSessionResponse,
)
async def revoke_job_live_session(
    job_id: UUID,
    payload: RevokeTaskRunLiveSessionRequest = Body(
        default_factory=RevokeTaskRunLiveSessionRequest
    ),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunLiveSessionResponse:
    """Force revoke one queue task-run live session."""

    try:
        live = await service.revoke_live_session(
            task_run_id=job_id,
            actor_user_id=getattr(user, "id", None),
            reason=payload.reason,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunLiveSessionResponse(
        session=TaskRunLiveSessionModel.model_validate(live),
    )


@router.post("/jobs/{job_id}/control", response_model=JobModel)
async def apply_job_control_action(
    job_id: UUID,
    payload: TaskRunControlRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> JobModel:
    """Apply pause/resume/takeover controls to one queue task run."""

    try:
        job = await service.apply_control_action(
            task_run_id=job_id,
            actor_user_id=getattr(user, "id", None),
            action=payload.action,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_job(job)


@router.post(
    "/jobs/{job_id}/operator-messages",
    response_model=TaskRunControlEventModel,
    status_code=status.HTTP_201_CREATED,
)
async def append_job_operator_message(
    job_id: UUID,
    payload: TaskRunOperatorMessageRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskRunControlEventModel:
    """Append one operator message to the queue task run control stream."""

    try:
        event = await service.append_operator_message(
            task_run_id=job_id,
            actor_user_id=getattr(user, "id", None),
            message=payload.message,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return TaskRunControlEventModel.model_validate(event)


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


@router.get("/jobs/{job_id}/attachments", response_model=ArtifactListResponse)
async def list_job_attachments(
    job_id: UUID,
    *,
    limit: int = Query(50, ge=1, le=500),
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ArtifactListResponse:
    """List attachment metadata for a queue job (user auth)."""

    try:
        attachments = await service.list_attachments_for_user(
            job_id=job_id,
            actor_user_id=getattr(user, "id", None),
            limit=limit,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return ArtifactListResponse(
        items=[_serialize_artifact(item) for item in attachments]
    )


@router.get("/jobs/{job_id}/attachments/{attachment_id}/download")
async def download_job_attachment(
    job_id: UUID,
    attachment_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> FileResponse:
    """Download attachment binary for a queue job (user auth)."""

    try:
        download = await service.get_attachment_download_for_user(
            job_id=job_id,
            attachment_id=attachment_id,
            actor_user_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    return FileResponse(
        path=download.file_path,
        filename=download.artifact.name.split("/")[-1],
        media_type=download.artifact.content_type or "application/octet-stream",
    )


@router.get(
    "/jobs/{job_id}/attachments/worker",
    response_model=ArtifactListResponse,
)
async def list_job_attachments_worker(
    job_id: UUID,
    *,
    limit: int = Query(50, ge=1, le=500),
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> ArtifactListResponse:
    """List attachment metadata for a queue job (worker auth)."""

    worker_id = worker_auth.worker_id
    if not worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "worker_identity_required",
                "message": "Worker identity is required for attachment access.",
            },
        )
    try:
        attachments = await service.list_attachments_for_worker(
            job_id=job_id,
            worker_id=worker_id,
            limit=limit,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return ArtifactListResponse(
        items=[_serialize_artifact(item) for item in attachments]
    )


@router.get("/jobs/{job_id}/attachments/{attachment_id}/download/worker")
async def download_job_attachment_worker(
    job_id: UUID,
    attachment_id: UUID,
    service: AgentQueueService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> FileResponse:
    """Download attachment binary for a queue job (worker auth)."""

    worker_id = worker_auth.worker_id
    if not worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "worker_identity_required",
                "message": "Worker identity is required for attachment access.",
            },
        )
    try:
        download = await service.get_attachment_download_for_worker(
            job_id=job_id,
            attachment_id=attachment_id,
            worker_id=worker_id,
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
    after_event_id: UUID | None = Query(None, alias="afterEventId"),
    before: Optional[datetime] = Query(None, alias="before"),
    before_event_id: UUID | None = Query(None, alias="beforeEventId"),
    sort: Literal["asc", "desc"] = Query("asc", alias="sort"),
    limit: int = Query(200, ge=1, le=500),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> JobEventListResponse:
    """List queue job events for polling-based progress updates."""

    try:
        events = await service.list_events(
            job_id=job_id,
            limit=limit,
            after=after,
            after_event_id=after_event_id,
            before=before,
            before_event_id=before_event_id,
            sort=sort,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return JobEventListResponse(items=[_serialize_event(event) for event in events])


@router.get("/jobs/{job_id}/events/stream")
async def stream_job_events(
    job_id: UUID,
    request: Request,
    *,
    after: Optional[datetime] = Query(None, alias="after"),
    after_event_id: UUID | None = Query(None, alias="afterEventId"),
    limit: int = Query(200, ge=1, le=500),
    poll_interval_ms: int = Query(1000, alias="pollIntervalMs", ge=100, le=10000),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> StreamingResponse:
    """Stream queue job events as Server-Sent Events with incremental cursor polling."""

    async def _event_stream() -> AsyncIterator[str]:
        cursor_after = after
        cursor_after_event_id = after_event_id
        poll_seconds = float(poll_interval_ms) / 1000.0
        keepalive_seconds = max(5.0, poll_seconds * 3.0)
        loop = asyncio.get_running_loop()
        last_keepalive = loop.time()
        while True:
            if await request.is_disconnected():
                break
            try:
                events = await service.list_events(
                    job_id=job_id,
                    limit=limit,
                    after=cursor_after,
                    after_event_id=cursor_after_event_id,
                )
            except Exception as exc:  # pragma: no cover - thin mapping layer
                http_exc = _to_http_exception(exc)
                detail = (
                    http_exc.detail
                    if isinstance(http_exc.detail, dict)
                    else {"message": str(http_exc.detail)}
                )
                yield (
                    "event: error\n"
                    f"data: {json.dumps(detail, ensure_ascii=True)}\n\n"
                )
                break

            if events:
                for event in events:
                    serialized = _serialize_event(event).model_dump(
                        mode="json", by_alias=True
                    )
                    yield (
                        "event: queue_event\n"
                        f"id: {serialized['id']}\n"
                        f"data: {json.dumps(serialized, ensure_ascii=True)}\n\n"
                    )
                    cursor_after = event.created_at
                    cursor_after_event_id = event.id
                continue

            now = loop.time()
            if now - last_keepalive >= keepalive_seconds:
                yield ": keep-alive\n\n"
                last_keepalive = now
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
            runtime_capabilities=payload.runtime_capabilities,
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


@router.put(
    "/workers/tokens/capabilities",
    response_model=WorkerTokenModel,
)
@router.post(
    "/workers/tokens/capabilities",
    response_model=WorkerTokenModel,
)
async def sync_worker_token_capabilities(
    payload: WorkerRuntimeCapabilitiesRequest,
    auth: _WorkerRequestAuth = Depends(_require_worker_auth),
    service: AgentQueueService = Depends(_get_service),
) -> WorkerTokenModel:
    """Replace runtime capabilities metadata for the authenticated worker token."""

    if auth.auth_source != "worker_token" or auth.token_id is None:
        raise _to_http_exception(
            AgentQueueAuthorizationError(
                "worker token authentication is required for capability sync"
            )
        )
    try:
        token = await service.replace_worker_runtime_capabilities(
            token_id=auth.token_id,
            runtime_capabilities=payload.runtime_capabilities,
        )
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc
    return _serialize_worker_token(token)


@router.get(
    "/workers/runtime-capabilities",
    response_model=WorkerRuntimeCapabilitiesResponse,
)
async def list_worker_runtime_capabilities(
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> WorkerRuntimeCapabilitiesResponse:
    """Return aggregated runtime model/effort options from active worker tokens."""

    try:
        token_rows = await service.list_worker_tokens(limit=500)
    except Exception as exc:  # pragma: no cover - thin mapping layer
        raise _to_http_exception(exc) from exc

    runtime_capabilities: dict[str, dict[str, list[str]]] = {
        runtime: {"models": [], "efforts": []}
        for runtime in _RUNTIME_CAPABILITY_RUNTIMES
    }
    for token in token_rows:
        if not token.is_active:
            continue
        token_caps = getattr(token, "runtime_capabilities", None)
        if not isinstance(token_caps, dict):
            continue
        for raw_runtime, raw_payload in token_caps.items():
            runtime = str(raw_runtime).strip().lower()
            if runtime not in _RUNTIME_CAPABILITY_RUNTIMES:
                continue
            models, efforts = _normalize_runtime_capability_entry(raw_payload)
            current = runtime_capabilities.setdefault(
                runtime,
                {"models": [], "efforts": []},
            )
            current["models"] = _merge_runtime_capability_values(
                base=current["models"],
                additions=models,
            )
            current["efforts"] = _merge_runtime_capability_values(
                base=current["efforts"],
                additions=efforts,
            )

    return WorkerRuntimeCapabilitiesResponse(
        items={
            runtime: RuntimeCapabilities(
                models=payload["models"], efforts=payload["efforts"]
            )
            for runtime, payload in runtime_capabilities.items()
        },
    )
