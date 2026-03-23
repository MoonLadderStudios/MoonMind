"""REST router for manifest registry operations."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.tasks.manifest_contract import ManifestContractError
from api_service.api.routers.worker_auth import _require_worker_auth, _WorkerRequestAuth
from api_service.api.schemas import (
    ManifestDetailModel,
    ManifestListResponse,
    ManifestRunMetadataModel,
    ManifestRunQueueMetadata,
    ManifestRunRequest,
    ManifestRunResponse,
    ManifestStateModel,
    ManifestStateUpdateRequest,
    ManifestSummaryModel,
    ManifestUpsertRequest,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.manifests_service import (
    ManifestRegistryNotFoundError,
    ManifestsService,
)
from moonmind.config.settings import settings
from moonmind.workflows.tasks.routing import TemporalSubmitDisabledError
from moonmind.workflows.temporal import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactStateError,
    TemporalArtifactValidationError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
)

router = APIRouter(prefix="/api/manifests", tags=["manifests"])
logger = logging.getLogger(__name__)


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> ManifestsService:
    queue_service = None
    execution_service = TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )
    artifact_service = get_temporal_artifact_service(session)
    return ManifestsService(
        session,
        queue_service,
        execution_service=execution_service,
        artifact_service=artifact_service,
    )


def _serialize_summary(record) -> ManifestSummaryModel:
    return ManifestSummaryModel(
        name=record.name,
        version=record.version,
        content_hash=record.content_hash,
        updated_at=record.updated_at,
        last_run_job_id=getattr(record, "last_run_job_id", None),
        last_run_source=getattr(record, "last_run_source", None),
        last_run_status=getattr(record, "last_run_status", None),
        state_updated_at=getattr(record, "state_updated_at", None),
    )


def _serialize_detail(record) -> ManifestDetailModel:
    state_model = ManifestStateModel(
        state_json=record.state_json,
        state_updated_at=record.state_updated_at,
    )
    last_run: Optional[ManifestRunMetadataModel] = None
    last_run_source = getattr(record, "last_run_source", None)
    last_run_workflow_id = getattr(record, "last_run_workflow_id", None)
    if record.last_run_job_id or record.last_run_status or last_run_workflow_id:
        task_id = last_run_workflow_id if last_run_source == "temporal" else None
        link = f"/tasks/{task_id}?source=temporal" if task_id else None
        last_run = ManifestRunMetadataModel(
            source=last_run_source,
            job_id=getattr(record, "last_run_job_id", None),
            status=getattr(record, "last_run_status", None),
            task_id=task_id,
            workflow_id=last_run_workflow_id,
            temporal_run_id=getattr(record, "last_run_temporal_run_id", None),
            workflow_type=(
                "MoonMind.ManifestIngest" if last_run_source == "temporal" else None
            ),
            temporal_status=(
                _temporal_status_for_manifest_status(
                    getattr(record, "last_run_status", None),
                    finished_at=getattr(record, "last_run_finished_at", None),
                )
                if last_run_source == "temporal"
                else None
            ),
            manifest_artifact_ref=getattr(record, "last_run_manifest_ref", None),
            link=link,
            started_at=getattr(record, "last_run_started_at", None),
            finished_at=getattr(record, "last_run_finished_at", None),
        )
    return ManifestDetailModel(
        name=record.name,
        version=record.version,
        content=record.content,
        content_hash=record.content_hash,
        updated_at=record.updated_at,
        last_run=last_run,
        state=state_model,
    )


@router.get("", response_model=ManifestListResponse)
async def list_manifests(
    *,
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    service: ManifestsService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> ManifestListResponse:
    records = await service.list_manifests(limit=limit, search=search)
    return ManifestListResponse(
        items=[_serialize_summary(record) for record in records]
    )


@router.get("/{name}", response_model=ManifestDetailModel)
async def get_manifest(
    name: str,
    service: ManifestsService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> ManifestDetailModel:
    record = await service.get_manifest(name)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "manifest_not_found",
                "message": f"Manifest '{name}' not found",
            },
        )
    return _serialize_detail(record)


@router.put("/{name}", response_model=ManifestDetailModel)
async def upsert_manifest(
    name: str,
    payload: ManifestUpsertRequest,
    service: ManifestsService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> ManifestDetailModel:
    try:
        record = await service.upsert_manifest(name=name, content=payload.content)
    except ManifestContractError as exc:
        logger.warning("Manifest upsert validation failed.", exc_info=True)
        message = str(exc).strip() or "Invalid manifest payload"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_manifest", "message": message},
        ) from exc
    return _serialize_detail(record)


@router.post(
    "/{name}/runs",
    response_model=ManifestRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_manifest_run(
    name: str,
    payload: ManifestRunRequest,
    service: ManifestsService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ManifestRunResponse:
    action = payload.action
    options_payload: dict[str, Any] | None = None
    if payload.options is not None:
        options_payload = payload.options.to_payload()
    try:
        job = await service.submit_manifest_run(
            name=name,
            action=action,
            options=options_payload,
            user_id=getattr(user, "id", None),
            title=payload.title,
            failure_policy=payload.failure_policy,
            max_concurrency=payload.max_concurrency,
            tags=payload.tags,
            idempotency_key=payload.idempotency_key,
        )
    except ManifestRegistryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "manifest_not_found",
                "message": f"Manifest '{name}' not found",
            },
        ) from exc
    except TemporalArtifactAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "manifest_forbidden", "message": str(exc)},
        ) from exc
    except TemporalSubmitDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "temporal_submit_disabled", "message": str(exc)},
        ) from exc
    except ManifestContractError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_manifest_job", "message": str(exc)},
        ) from exc
    except (
        TemporalArtifactStateError,
        TemporalArtifactValidationError,
        TemporalExecutionValidationError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_manifest_job", "message": str(exc)},
        ) from exc

    if job.source == "queue":
        queue_job = job.job
        assert queue_job is not None
        queue_payload = queue_job.payload or {}
        queue_metadata = ManifestRunQueueMetadata(
            type=queue_job.type,
            required_capabilities=list(queue_payload.get("requiredCapabilities", [])),
            manifest_hash=queue_payload.get("manifestHash"),
        )
        return ManifestRunResponse(
            source="queue",
            job_id=queue_job.id,
            queue=queue_metadata,
        )

    execution = ManifestRunMetadataModel(
        source="temporal",
        status=job.status,
        task_id=job.workflow_id,
        workflow_id=job.workflow_id,
        temporal_run_id=job.run_id,
        workflow_type=job.workflow_type,
        temporal_status=job.temporal_status,
        manifest_artifact_ref=job.manifest_artifact_ref,
        link=f"/tasks/{job.workflow_id}?source=temporal" if job.workflow_id else None,
    )
    return ManifestRunResponse(
        source="temporal",
        execution=execution,
    )


def _temporal_status_for_manifest_status(
    status_value: str | None,
    *,
    finished_at,
) -> str:
    normalized = (status_value or "").strip().lower()
    if normalized == "succeeded":
        return "completed"
    if normalized == "canceled":
        return "canceled"
    if normalized == "failed":
        return "failed"
    if finished_at is not None:
        return "failed"
    return "running"


@router.post("/{name}/state", response_model=ManifestDetailModel)
async def update_manifest_state(
    name: str,
    payload: ManifestStateUpdateRequest,
    service: ManifestsService = Depends(_get_service),
    worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> ManifestDetailModel:
    """Persist worker checkpoint state and optional run metadata."""

    if worker_auth.auth_source != "worker_token":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "worker_not_authorized",
                "message": "manifest state callback requires worker-token authentication",
            },
        )

    try:
        record = await service.update_manifest_state(
            name=name,
            state_json=payload.state_json,
            last_run_job_id=payload.last_run_job_id,
            last_run_status=payload.last_run_status,
            last_run_started_at=payload.last_run_started_at,
            last_run_finished_at=payload.last_run_finished_at,
        )
    except ManifestRegistryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "manifest_not_found",
                "message": f"Manifest '{name}' not found",
            },
        ) from exc
    return _serialize_detail(record)
