"""REST router for preset catalog operations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.dependencies import (
    ensure_preset_catalog_enabled,
)
from api_service.api.schemas import (
    PresetCreateRequestSchema,
    PresetExpandRequestSchema,
    PresetExpandResponseSchema,
    PresetFavoriteRequestSchema,
    PresetListResponseSchema,
    PresetResponseSchema,
    PresetReviewRequestSchema,
    PresetSaveFromWorkflowRequestSchema,
)
from api_service.auth_providers import get_instance_access
from api_service.db.base import get_async_session
from api_service.db.models import PresetReleaseStatus
from api_service.services.presets.catalog import (
    ExpandOptions,
    PresetCatalogService,
    PresetConflictError,
    PresetNotFoundError,
    PresetValidationError,
)
from api_service.services.presets.save import PresetSaveService


async def _reject_legacy_scope(request: Request) -> None:
    if any(key in request.query_params for key in ("scope", "scopeRef", "scope_ref")):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "legacy_preset_requires_refresh",
                "message": "Preset scope was removed. Refresh and select an instance preset.",
            },
        )


router = APIRouter(
    prefix="/api/presets",
    tags=["presets"],
    dependencies=[Depends(get_instance_access), Depends(_reject_legacy_scope)],
)
logger = logging.getLogger(__name__)


def _map_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PresetNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "template_not_found",
                "message": str(exc),
            },
        )
    if isinstance(exc, PresetConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "template_conflict",
                "message": str(exc),
            },
        )
    if isinstance(exc, PresetValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "template_validation_error",
                "message": str(exc),
            },
        )
    logger.exception("Unhandled preset error")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "template_internal_error",
            "message": "Unexpected preset error.",
        },
    )


async def _get_catalog_service(
    session: AsyncSession = Depends(get_async_session),
) -> PresetCatalogService:
    return PresetCatalogService(session)


async def _get_save_service(
    session: AsyncSession = Depends(get_async_session),
) -> PresetSaveService:
    return PresetSaveService(session)


@router.get("", response_model=PresetListResponseSchema)
async def list_templates(
    *,
    tag: str | None = Query(None),
    search: str | None = Query(None),
    favorites_only: bool = Query(False, alias="favoritesOnly"),
    include_inactive: bool = Query(False, alias="includeInactive"),
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> PresetListResponseSchema:
    ensure_preset_catalog_enabled()

    try:
        items = await service.list_templates(
            tag=tag,
            search=search,
            favorites_only=favorites_only,
            include_inactive=include_inactive,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return PresetListResponseSchema(items=items)


@router.post(
    "",
    response_model=PresetResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    payload: PresetCreateRequestSchema,
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> PresetResponseSchema:
    ensure_preset_catalog_enabled()

    try:
        created = await service.create_template(
            slug=payload.slug or payload.title,
            title=payload.title,
            description=payload.description,
            tags=payload.tags,
            inputs_schema=[item.model_dump(by_alias=True) for item in payload.inputs],
            steps=[item.model_dump(by_alias=True) for item in payload.steps],
            annotations=payload.annotations,
            required_capabilities=payload.required_capabilities,
            release_status=PresetReleaseStatus(payload.release_status),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return PresetResponseSchema.model_validate(created)


@router.post(
    "/save-from-workflow",
    response_model=PresetResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def save_from_workflow(
    payload: PresetSaveFromWorkflowRequestSchema,
    service: PresetSaveService = Depends(_get_save_service),
) -> PresetResponseSchema:
    ensure_preset_catalog_enabled()

    try:
        created = await service.save_from_workflow(
            slug=payload.slug,
            title=payload.title,
            description=payload.description,
            steps=[item.model_dump(by_alias=True) for item in payload.steps],
            suggested_inputs=[
                item.model_dump(by_alias=True) for item in payload.suggested_inputs
            ],
            tags=payload.tags,
            release_status=PresetReleaseStatus(payload.release_status),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return PresetResponseSchema.model_validate(created)


@router.post("/{slug}:expand", response_model=PresetExpandResponseSchema)
async def expand_template(
    slug: str,
    payload: PresetExpandRequestSchema,
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> PresetExpandResponseSchema:
    ensure_preset_catalog_enabled()

    try:
        expanded = await service.expand_template(
            slug=slug,
            inputs=payload.inputs,
            context=payload.context,
            options=ExpandOptions(
                should_enforce_step_limit=payload.options.enforce_step_limit
            ),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return PresetExpandResponseSchema.model_validate(expanded)


@router.get("/{slug}", response_model=PresetResponseSchema)
async def get_template(
    slug: str,
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> PresetResponseSchema:
    ensure_preset_catalog_enabled()

    try:
        item = await service.get_template(
            slug=slug,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return PresetResponseSchema.model_validate(item)


@router.put("/{slug}/review", response_model=PresetResponseSchema)
async def review_template(
    slug: str,
    payload: PresetReviewRequestSchema,
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> PresetResponseSchema:
    ensure_preset_catalog_enabled()

    try:
        result = await service.set_release_status(
            slug=slug,
            release_status=PresetReleaseStatus(payload.release_status),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return PresetResponseSchema.model_validate(result)


@router.post("/{slug}:favorite", status_code=status.HTTP_204_NO_CONTENT)
async def favorite_template(
    slug: str,
    payload: PresetFavoriteRequestSchema,
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> Response:
    ensure_preset_catalog_enabled()

    try:
        await service.set_favorite(
            slug=slug,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{slug}:favorite", status_code=status.HTTP_204_NO_CONTENT)
async def unfavorite_template(
    slug: str,
    payload: PresetFavoriteRequestSchema,
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> Response:
    ensure_preset_catalog_enabled()

    try:
        await service.clear_favorite(
            slug=slug,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    slug: str,
    service: PresetCatalogService = Depends(_get_catalog_service),
) -> Response:
    ensure_preset_catalog_enabled()

    try:
        await service.soft_delete_template(
            slug=slug,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
