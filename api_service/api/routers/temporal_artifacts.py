"""Temporal artifact API endpoints for local-dev artifact workflows."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth import _DEFAULT_USER_ID
from api_service.auth_providers import get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.schemas.temporal_artifact_models import (
    ArtifactExecutionLinkModel,
    ArtifactListResponse,
    ArtifactMetadataModel,
    ArtifactRefModel,
    CompleteArtifactRequest,
    CreateArtifactRequest,
    CreateArtifactResponse,
    LinkArtifactRequest,
    PinArtifactRequest,
    PresignDownloadResponse,
    PresignUploadPartRequest,
    PresignUploadPartResponse,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactNotFoundError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
    TemporalArtifactValidationError,
    build_artifact_ref,
)

router = APIRouter(tags=["temporal-artifacts"])


def _attachment_upload_diagnostic_event(
    *,
    event: str,
    status_value: str,
    artifact,
    metadata: object,
) -> dict[str, object] | None:
    if not isinstance(metadata, dict):
        return None
    target_kind = str(
        metadata.get("targetKind") or metadata.get("target_kind") or ""
    ).strip()
    if target_kind not in {"objective", "step"}:
        return None
    payload: dict[str, object] = {
        "event": event,
        "status": status_value,
        "targetKind": target_kind,
        "artifactId": artifact.artifact_id,
        "contentType": artifact.content_type,
        "sizeBytes": artifact.size_bytes,
    }
    filename = str(metadata.get("filename") or "").strip()
    if filename:
        payload["filename"] = filename
    if target_kind == "step":
        step_ref = str(metadata.get("stepRef") or metadata.get("step_ref") or "").strip()
        if step_ref:
            payload["stepRef"] = step_ref
    return payload


def _attachment_upload_diagnostics(
    *,
    event: str,
    status_value: str,
    artifact,
    metadata: object,
) -> dict[str, object] | None:
    diagnostic_event = _attachment_upload_diagnostic_event(
        event=event,
        status_value=status_value,
        artifact=artifact,
        metadata=metadata,
    )
    if diagnostic_event is None:
        return None
    return {"events": [diagnostic_event]}


def _raise_temporal_artifact_http(error: Exception) -> None:
    if isinstance(error, TemporalArtifactNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "artifact_not_found", "message": str(error)},
        ) from error
    if isinstance(error, TemporalArtifactAuthorizationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "artifact_forbidden", "message": str(error)},
        ) from error
    if isinstance(error, TemporalArtifactStateError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "artifact_state_error", "message": str(error)},
        ) from error
    if isinstance(error, TemporalArtifactValidationError):
        lowered = str(error).lower()
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        code = "invalid_artifact_payload"
        if "max bytes" in lowered:
            status_code = status.HTTP_413_CONTENT_TOO_LARGE
            code = "artifact_too_large"
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, "message": str(error)},
        ) from error
    raise error


def _serialize_metadata(
    *,
    artifact,
    links,
    pinned: bool,
    read_policy,
    download_url: str | None = None,
    download_expires_at: datetime | None = None,
) -> ArtifactMetadataModel:
    def _artifact_ref_payload(value: object) -> dict:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, dict):
            return dict(value)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return dict(model_dump())
        return {
            "artifact_ref_v": getattr(value, "artifact_ref_v"),
            "artifact_id": getattr(value, "artifact_id"),
            "sha256": getattr(value, "sha256"),
            "size_bytes": getattr(value, "size_bytes"),
            "content_type": getattr(value, "content_type"),
            "encryption": getattr(value, "encryption"),
        }

    preview_ref = (
        ArtifactRefModel(**_artifact_ref_payload(read_policy.preview_artifact_ref))
        if read_policy.preview_artifact_ref is not None
        else None
    )
    default_read_ref = ArtifactRefModel(
        **_artifact_ref_payload(read_policy.default_read_ref)
    )
    return ArtifactMetadataModel(
        artifact_id=artifact.artifact_id,
        created_at=artifact.created_at,
        created_by_principal=artifact.created_by_principal,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        storage_backend=artifact.storage_backend,
        storage_key=artifact.storage_key,
        encryption=artifact.encryption,
        status=artifact.status,
        retention_class=artifact.retention_class,
        expires_at=artifact.expires_at,
        redaction_level=artifact.redaction_level,
        metadata=dict(artifact.metadata_json or {}),
        links=[
            ArtifactExecutionLinkModel(
                namespace=item.namespace,
                workflow_id=item.workflow_id,
                run_id=item.run_id,
                link_type=item.link_type,
                label=item.label,
                created_at=item.created_at,
                created_by_activity_type=item.created_by_activity_type,
                created_by_worker=item.created_by_worker,
            )
            for item in links
        ],
        pinned=pinned,
        artifact_ref=ArtifactRefModel(**asdict(build_artifact_ref(artifact))),
        preview_artifact_ref=preview_ref,
        raw_access_allowed=read_policy.raw_access_allowed,
        default_read_ref=default_read_ref,
        download_url=download_url,
        download_expires_at=download_expires_at,
    )


async def _get_temporal_artifact_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalArtifactService:
    return TemporalArtifactService(TemporalArtifactRepository(session))


async def _resolve_principal(
    user: Optional[User] = Depends(get_current_user_optional()),
) -> str:
    if settings.oidc.AUTH_PROVIDER == "disabled":
        user_id = getattr(user, "id", None)
        return str(user_id or settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID)

    if user is None or getattr(user, "id", None) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "authentication_required",
                "message": "Authentication is required for artifact operations.",
            },
        )
    return str(user.id)


@router.post(
    "/api/artifacts",
    response_model=CreateArtifactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_artifact(
    request: Request,
    payload: CreateArtifactRequest,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> CreateArtifactResponse:
    try:
        artifact, upload = await service.create(
            principal=principal,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
            sha256=payload.sha256,
            retention_class=payload.retention_class,
            link=payload.link.model_dump() if payload.link else None,
            metadata_json=payload.metadata,
            encryption=payload.encryption,
            redaction_level=payload.redaction_level,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise

    upload_url = upload.upload_url
    if upload_url and upload_url.startswith("/"):
        upload_url = str(request.base_url).rstrip("/") + upload_url

    return CreateArtifactResponse(
        artifact_ref=ArtifactRefModel(**asdict(build_artifact_ref(artifact))),
        upload={
            "mode": upload.mode,
            "upload_url": upload_url,
            "upload_id": upload.upload_id,
            "expires_at": upload.expires_at,
            "max_size_bytes": upload.max_size_bytes,
            "required_headers": dict(upload.required_headers),
        },
        diagnostics=_attachment_upload_diagnostics(
            event="attachment_upload_started",
            status_value="started",
            artifact=artifact,
            metadata=payload.metadata,
        ),
    )


@router.put("/api/artifacts/{artifact_id}/content", response_model=ArtifactRefModel)
async def upload_artifact_content(
    artifact_id: str,
    request: Request,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactRefModel:
    payload = await request.body()
    content_type = request.headers.get("content-type")
    try:
        artifact = await service.write_complete(
            artifact_id=artifact_id,
            principal=principal,
            payload=payload,
            content_type=content_type,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return ArtifactRefModel(
        **asdict(build_artifact_ref(artifact)),
        diagnostics=_attachment_upload_diagnostics(
            event="attachment_upload_completed",
            status_value="completed",
            artifact=artifact,
            metadata=getattr(artifact, "metadata_json", None),
        ),
    )


@router.post(
    "/api/artifacts/{artifact_id}/presign-upload-part",
    response_model=PresignUploadPartResponse,
)
async def presign_artifact_upload_part(
    artifact_id: str,
    payload: PresignUploadPartRequest,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> PresignUploadPartResponse:
    try:
        grant = await service.presign_upload_part(
            artifact_id=artifact_id,
            principal=principal,
            part_number=payload.part_number,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return PresignUploadPartResponse(
        part_number=grant.part_number,
        url=grant.url,
        expires_at=grant.expires_at,
        required_headers=grant.required_headers,
    )


@router.post("/api/artifacts/{artifact_id}/complete", response_model=ArtifactRefModel)
async def complete_artifact_upload(
    artifact_id: str,
    request: CompleteArtifactRequest,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactRefModel:
    try:
        artifact = await service.complete(
            artifact_id=artifact_id,
            principal=principal,
            parts=[part.model_dump() for part in request.parts],
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return ArtifactRefModel(**asdict(build_artifact_ref(artifact)))


@router.get("/api/artifacts/{artifact_id}", response_model=ArtifactMetadataModel)
async def get_artifact_metadata(
    artifact_id: str,
    include_download: bool = Query(False, alias="include_download"),
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactMetadataModel:
    try:
        artifact, links, pinned, read_policy = await service.get_metadata(
            artifact_id=artifact_id,
            principal=principal,
        )
        download_url = None
        download_expires_at = None
        if include_download and read_policy.raw_access_allowed:
            _artifact, expires_at, url = await service.presign_download(
                artifact_id=artifact_id,
                principal=principal,
            )
            download_url = url
            download_expires_at = expires_at
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return _serialize_metadata(
        artifact=artifact,
        links=links,
        pinned=pinned,
        read_policy=read_policy,
        download_url=download_url,
        download_expires_at=download_expires_at,
    )


@router.post(
    "/api/artifacts/{artifact_id}/presign-download",
    response_model=PresignDownloadResponse,
)
async def presign_artifact_download(
    artifact_id: str,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> PresignDownloadResponse:
    try:
        _artifact, expires_at, url = await service.presign_download(
            artifact_id=artifact_id,
            principal=principal,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return PresignDownloadResponse(url=url, expires_at=expires_at)


@router.get("/api/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
):
    try:
        artifact, path = await service.read_path(
            artifact_id=artifact_id,
            principal=principal,
        )
        is_json = (artifact.content_type or "").split(";", 1)[0].strip().lower() == "application/json"
        filename = f"{artifact.artifact_id}.json" if is_json else artifact.artifact_id
        return FileResponse(
            path,
            filename=filename,
            media_type=artifact.content_type or "application/octet-stream",
        )
    except TemporalArtifactValidationError:
        pass
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise

    try:
        artifact, chunks = await service.read_chunks(
            artifact_id=artifact_id,
            principal=principal,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise

    is_json = (artifact.content_type or "").split(";", 1)[0].strip().lower() == "application/json"
    filename = f"{artifact.artifact_id}.json" if is_json else artifact.artifact_id
    return StreamingResponse(
        chunks,
        media_type=artifact.content_type or "application/octet-stream",
        headers={
            "content-disposition": f'attachment; filename="{filename}"'
        },
    )


@router.post("/api/artifacts/{artifact_id}/links", response_model=ArtifactMetadataModel)
async def link_artifact(
    artifact_id: str,
    payload: LinkArtifactRequest,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactMetadataModel:
    try:
        await service.link_artifact(
            artifact_id=artifact_id,
            principal=principal,
            execution_ref=payload.model_dump(),
        )
        artifact, links, pinned, read_policy = await service.get_metadata(
            artifact_id=artifact_id,
            principal=principal,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return _serialize_metadata(
        artifact=artifact,
        links=links,
        pinned=pinned,
        read_policy=read_policy,
    )


@router.get(
    "/api/executions/{namespace}/{workflow_id}/{run_id}/artifacts",
    response_model=ArtifactListResponse,
)
async def list_execution_artifacts(
    namespace: str,
    workflow_id: str,
    run_id: str,
    link_type: str | None = Query(None),
    latest_only: bool = Query(False),
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactListResponse:
    try:
        artifacts = await service.list_for_execution(
            namespace=namespace,
            workflow_id=workflow_id,
            run_id=run_id,
            principal=principal,
            link_type=link_type,
            latest_only=latest_only,
        )
        serialized = []
        for artifact in artifacts:
            item, links, pinned, read_policy = await service.get_metadata(
                artifact_id=artifact.artifact_id,
                principal=principal,
            )
            serialized.append(
                _serialize_metadata(
                    artifact=item,
                    links=links,
                    pinned=pinned,
                    read_policy=read_policy,
                )
            )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return ArtifactListResponse(artifacts=serialized)


@router.post("/api/artifacts/{artifact_id}/pin", response_model=ArtifactMetadataModel)
async def pin_artifact(
    artifact_id: str,
    payload: PinArtifactRequest,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactMetadataModel:
    try:
        await service.pin(
            artifact_id=artifact_id,
            principal=principal,
            reason=payload.reason,
        )
        artifact, links, pinned, read_policy = await service.get_metadata(
            artifact_id=artifact_id,
            principal=principal,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return _serialize_metadata(
        artifact=artifact,
        links=links,
        pinned=pinned,
        read_policy=read_policy,
    )


@router.delete("/api/artifacts/{artifact_id}/pin", response_model=ArtifactMetadataModel)
async def unpin_artifact(
    artifact_id: str,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactMetadataModel:
    try:
        await service.unpin(artifact_id=artifact_id, principal=principal)
        artifact, links, pinned, read_policy = await service.get_metadata(
            artifact_id=artifact_id,
            principal=principal,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return _serialize_metadata(
        artifact=artifact,
        links=links,
        pinned=pinned,
        read_policy=read_policy,
    )


@router.delete("/api/artifacts/{artifact_id}", response_model=ArtifactMetadataModel)
async def delete_artifact(
    artifact_id: str,
    principal: str = Depends(_resolve_principal),
    service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactMetadataModel:
    try:
        await service.soft_delete(artifact_id=artifact_id, principal=principal)
        artifact, links, pinned, read_policy = await service.get_metadata(
            artifact_id=artifact_id,
            principal=principal,
        )
    except Exception as exc:  # pragma: no cover - mapped below
        _raise_temporal_artifact_http(exc)
        raise
    return _serialize_metadata(
        artifact=artifact,
        links=links,
        pinned=pinned,
        read_policy=read_policy,
    )
