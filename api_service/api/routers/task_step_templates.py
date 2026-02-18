"""REST router for task step template catalog operations."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.dependencies import (
    ensure_task_template_catalog_enabled,
    resolve_template_scope_for_user,
)
from api_service.api.schemas import (
    TaskTemplateCreateRequestSchema,
    TaskTemplateExpandRequestSchema,
    TaskTemplateExpandResponseSchema,
    TaskTemplateFavoriteRequestSchema,
    TaskTemplateListResponseSchema,
    TaskTemplateResponseSchema,
    TaskTemplateReviewRequestSchema,
    TaskTemplateSaveFromTaskRequestSchema,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import TaskTemplateReleaseStatus, User
from api_service.services.task_templates.catalog import (
    ExpandOptions,
    TaskTemplateCatalogService,
    TaskTemplateConflictError,
    TaskTemplateNotFoundError,
    TaskTemplateValidationError,
)
from api_service.services.task_templates.save import TaskTemplateSaveService

router = APIRouter(prefix="/api/task-step-templates", tags=["task-step-templates"])
logger = logging.getLogger(__name__)


def _map_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TaskTemplateNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "template_not_found",
                "message": str(exc),
            },
        )
    if isinstance(exc, TaskTemplateConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "template_conflict",
                "message": str(exc),
            },
        )
    if isinstance(exc, TaskTemplateValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "template_validation_error",
                "message": str(exc),
            },
        )
    logger.exception("Unhandled task template error")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "template_internal_error",
            "message": "Unexpected task template error.",
        },
    )


async def _get_catalog_service(
    session: AsyncSession = Depends(get_async_session),
) -> TaskTemplateCatalogService:
    return TaskTemplateCatalogService(session)


async def _get_save_service(
    session: AsyncSession = Depends(get_async_session),
) -> TaskTemplateSaveService:
    return TaskTemplateSaveService(session)


@router.get("", response_model=TaskTemplateListResponseSchema)
async def list_templates(
    *,
    scope: str | None = Query(None),
    scope_ref: str | None = Query(None, alias="scopeRef"),
    tag: str | None = Query(None),
    search: str | None = Query(None),
    favorites_only: bool = Query(False, alias="favoritesOnly"),
    include_inactive: bool = Query(False, alias="includeInactive"),
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> TaskTemplateListResponseSchema:
    ensure_task_template_catalog_enabled()

    resolved_scope = scope
    resolved_scope_ref = scope_ref
    if scope is not None:
        resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
            user=user,
            scope=scope,
            scope_ref=scope_ref,
            write=False,
        )
    try:
        items = await service.list_templates(
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
            tag=tag,
            search=search,
            favorites_only=favorites_only,
            user_id=getattr(user, "id", None),
            include_inactive=include_inactive,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return TaskTemplateListResponseSchema(items=items)


@router.post(
    "",
    response_model=TaskTemplateResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    payload: TaskTemplateCreateRequestSchema,
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> TaskTemplateResponseSchema:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=payload.scope,
        scope_ref=payload.scope_ref,
        write=True,
    )
    try:
        created = await service.create_template(
            slug=payload.slug or payload.title,
            title=payload.title,
            description=payload.description,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
            tags=payload.tags,
            inputs_schema=[item.model_dump(by_alias=True) for item in payload.inputs],
            steps=[item.model_dump(by_alias=True) for item in payload.steps],
            annotations=payload.annotations,
            required_capabilities=payload.required_capabilities,
            created_by=getattr(user, "id", None),
            release_status=(
                TaskTemplateReleaseStatus.ACTIVE
                if resolved_scope == "global"
                else TaskTemplateReleaseStatus.DRAFT
            ),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return TaskTemplateResponseSchema.model_validate(created)


@router.post(
    "/save-from-task",
    response_model=TaskTemplateResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def save_from_task(
    payload: TaskTemplateSaveFromTaskRequestSchema,
    service: TaskTemplateSaveService = Depends(_get_save_service),
    user: User = Depends(get_current_user()),
) -> TaskTemplateResponseSchema:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=payload.scope,
        scope_ref=payload.scope_ref,
        write=True,
    )
    try:
        created = await service.save_from_task(
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
            slug=payload.slug,
            title=payload.title,
            description=payload.description,
            steps=[item.model_dump(by_alias=True) for item in payload.steps],
            suggested_inputs=[item.model_dump(by_alias=True) for item in payload.suggested_inputs],
            tags=payload.tags,
            created_by=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return TaskTemplateResponseSchema.model_validate(created)


@router.post("/{slug}:expand", response_model=TaskTemplateExpandResponseSchema)
async def expand_template(
    slug: str,
    payload: TaskTemplateExpandRequestSchema,
    scope: str = Query(...),
    scope_ref: str | None = Query(None, alias="scopeRef"),
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> TaskTemplateExpandResponseSchema:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=scope,
        scope_ref=scope_ref,
        write=False,
    )
    try:
        expanded = await service.expand_template(
            slug=slug,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
            version=payload.version,
            inputs=payload.inputs,
            context=payload.context,
            options=ExpandOptions(
                should_enforce_step_limit=payload.options.enforce_step_limit
            ),
            user_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return TaskTemplateExpandResponseSchema.model_validate(expanded)


@router.get("/{slug}", response_model=TaskTemplateResponseSchema)
async def get_template(
    slug: str,
    scope: str = Query(...),
    scope_ref: str | None = Query(None, alias="scopeRef"),
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> TaskTemplateResponseSchema:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=scope,
        scope_ref=scope_ref,
        write=False,
    )
    try:
        item = await service.get_template(
            slug=slug,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
            version=None,
            user_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return TaskTemplateResponseSchema.model_validate(item)


@router.get("/{slug}/versions/{version}", response_model=TaskTemplateResponseSchema)
async def get_template_version(
    slug: str,
    version: str,
    scope: str = Query(...),
    scope_ref: str | None = Query(None, alias="scopeRef"),
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> TaskTemplateResponseSchema:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=scope,
        scope_ref=scope_ref,
        write=False,
    )
    try:
        item = await service.get_template(
            slug=slug,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
            version=version,
            user_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return TaskTemplateResponseSchema.model_validate(item)


@router.put("/{slug}/versions/{version}", response_model=TaskTemplateResponseSchema)
async def review_template_version(
    slug: str,
    version: str,
    payload: TaskTemplateReviewRequestSchema,
    scope: str = Query(...),
    scope_ref: str | None = Query(None, alias="scopeRef"),
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> TaskTemplateResponseSchema:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=scope,
        scope_ref=scope_ref,
        write=True,
    )
    try:
        result = await service.set_release_status(
            slug=slug,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
            version=version,
            release_status=TaskTemplateReleaseStatus(payload.release_status),
            reviewer_id=getattr(user, "id", None),
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return TaskTemplateResponseSchema.model_validate(result)


@router.post("/{slug}:favorite", status_code=status.HTTP_204_NO_CONTENT)
async def favorite_template(
    slug: str,
    payload: TaskTemplateFavoriteRequestSchema,
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> Response:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=payload.scope,
        scope_ref=payload.scope_ref,
        write=False,
    )
    user_id = getattr(user, "id", None)
    if not isinstance(user_id, UUID):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "user_id_required",
                "message": "A persisted user id is required for favorites.",
            },
        )
    try:
        await service.set_favorite(
            user_id=user_id,
            slug=slug,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{slug}:favorite", status_code=status.HTTP_204_NO_CONTENT)
async def unfavorite_template(
    slug: str,
    payload: TaskTemplateFavoriteRequestSchema,
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> Response:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=payload.scope,
        scope_ref=payload.scope_ref,
        write=False,
    )
    user_id = getattr(user, "id", None)
    if not isinstance(user_id, UUID):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "user_id_required",
                "message": "A persisted user id is required for favorites.",
            },
        )
    try:
        await service.clear_favorite(
            user_id=user_id,
            slug=slug,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    slug: str,
    scope: str = Query(...),
    scope_ref: str | None = Query(None, alias="scopeRef"),
    service: TaskTemplateCatalogService = Depends(_get_catalog_service),
    user: User = Depends(get_current_user()),
) -> Response:
    ensure_task_template_catalog_enabled()

    resolved_scope, resolved_scope_ref = resolve_template_scope_for_user(
        user=user,
        scope=scope,
        scope_ref=scope_ref,
        write=True,
    )
    try:
        await service.soft_delete_template(
            slug=slug,
            scope=resolved_scope,
            scope_ref=resolved_scope_ref,
        )
    except Exception as exc:  # pragma: no cover - thin API mapping
        raise _map_service_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
