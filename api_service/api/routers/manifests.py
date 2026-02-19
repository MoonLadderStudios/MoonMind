"""REST router for manifest registry operations."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.schemas import (
    ManifestDetailModel,
    ManifestListResponse,
    ManifestRunMetadataModel,
    ManifestRunQueueMetadata,
    ManifestRunRequest,
    ManifestRunResponse,
    ManifestStateModel,
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
from moonmind.workflows import get_agent_queue_service
from moonmind.workflows.agent_queue.manifest_contract import ManifestContractError
from moonmind.workflows.agent_queue.service import AgentQueueValidationError

router = APIRouter(prefix="/api/manifests", tags=["manifests"])


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> ManifestsService:
    queue_service = get_agent_queue_service(session)
    return ManifestsService(session, queue_service)


def _serialize_summary(record) -> ManifestSummaryModel:
    return ManifestSummaryModel(
        name=record.name,
        version=record.version,
        content_hash=record.content_hash,
        updated_at=record.updated_at,
        last_run_job_id=record.last_run_job_id,
        last_run_status=record.last_run_status,
        state_updated_at=record.state_updated_at,
    )


def _serialize_detail(record) -> ManifestDetailModel:
    state_model = ManifestStateModel(
        state_json=record.state_json,
        state_updated_at=record.state_updated_at,
    )
    last_run: Optional[ManifestRunMetadataModel] = None
    if record.last_run_job_id or record.last_run_status:
        last_run = ManifestRunMetadataModel(
            job_id=record.last_run_job_id,
            status=record.last_run_status,
            started_at=record.last_run_started_at,
            finished_at=record.last_run_finished_at,
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_manifest", "message": str(exc)},
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
    action = (payload.action or "run").strip().lower()
    options_payload: dict[str, Any] | None = None
    if payload.options is not None:
        options_payload = payload.options.to_payload()
    try:
        job = await service.submit_manifest_run(
            name=name,
            action=action,
            options=options_payload,
            user_id=getattr(user, "id", None),
        )
    except ManifestRegistryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "manifest_not_found",
                "message": f"Manifest '{name}' not found",
            },
        ) from exc
    except AgentQueueValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_manifest_job", "message": str(exc)},
        ) from exc

    queue_payload = job.payload or {}
    queue_metadata = ManifestRunQueueMetadata(
        type=job.type,
        required_capabilities=list(queue_payload.get("requiredCapabilities", [])),
        manifest_hash=queue_payload.get("manifestHash"),
    )
    return ManifestRunResponse(job_id=job.id, queue=queue_metadata)
