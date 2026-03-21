import logging
from typing import Optional

from fastapi import HTTPException, Request, status
from llama_index.core import (
    Settings,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)

from api_service.db.models import User
from moonmind.config.settings import settings


def get_chat_provider(request: Request):
    return request.app.state.chat_provider


def get_embed_model(request: Request):
    return request.app.state.embed_model


def get_embed_dimensions(request: Request):
    return request.app.state.embed_dimensions


def get_vector_store(request: Request):
    return request.app.state.vector_store


def get_storage_context(request: Request):
    return request.app.state.storage_context


def get_service_context(request: Request):
    # Return the global Settings object instead of ServiceContext
    return request.app.state.settings


def get_vector_index(request: Request) -> Optional[VectorStoreIndex]:
    logger_dep = logging.getLogger(__name__)
    storage_context: Optional[StorageContext] = getattr(
        request.app.state, "storage_context", None
    )
    service_settings: Optional[Settings] = getattr(request.app.state, "settings", None)

    if not storage_context or not service_settings:
        logger_dep.error(
            "StorageContext or LlamaIndex Settings not available in app state. Cannot provide VectorStoreIndex."
        )
        return None

    try:
        # Always try to load from storage to get the freshest index
        index = load_index_from_storage(storage_context=storage_context)
        logger_dep.debug(
            "Successfully reloaded VectorStoreIndex from storage in dependency."
        )
        # A basic check if the loaded index has content
        if not index.docstore.docs:
            logger_dep.warning("Dependency: Reloaded index appears to be empty.")
        return index
    except ValueError:
        logger_dep.warning(
            "Could not load VectorStoreIndex from storage in dependency (may be empty/new). "
            "Returning the index from app.state (which might be an empty initialized one)."
        )
        # Fallback to the one initialized at startup (which could be an empty one)
        return getattr(request.app.state, "vector_index", None)
    except Exception as e:
        logger_dep.exception(
            f"Unexpected error loading VectorStoreIndex in dependency: {e}"
        )
        return None


def ensure_task_template_catalog_enabled() -> None:
    """Raise a 404 when the task template catalog is disabled."""

    if settings.feature_flags.task_template_catalog_enabled:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "task_template_catalog_disabled",
            "message": "Task template catalog is disabled in this environment.",
        },
    )


def resolve_template_scope_for_user(
    *,
    user: User,
    scope: str,
    scope_ref: str | None,
    write: bool = False,
) -> tuple[str, str | None]:
    """Normalize and authorize scope access for template catalog requests."""

    normalized_scope = str(scope or "").strip().lower()
    normalized_scope_ref = str(scope_ref or "").strip() or None
    user_id = str(getattr(user, "id", "") or "")
    is_superuser = bool(getattr(user, "is_superuser", False))

    if normalized_scope not in {"global", "team", "personal"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_template_scope",
                "message": "scope must be one of: global, team, personal",
            },
        )

    if normalized_scope == "global":
        if write and not is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "template_scope_forbidden",
                    "message": "Only admins can modify global templates.",
                },
            )
        return "global", None

    if normalized_scope_ref is None:
        normalized_scope_ref = user_id or None
    if normalized_scope_ref is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "template_scope_ref_required",
                "message": "scopeRef is required when scope is team/personal.",
            },
        )

    if normalized_scope == "personal":
        if not is_superuser and normalized_scope_ref != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "template_scope_forbidden",
                    "message": "Personal templates are only accessible to their owner.",
                },
            )
        return "personal", normalized_scope_ref

    # Team scope access is limited to owner/admin until a dedicated membership model is available.
    if not is_superuser and normalized_scope_ref != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "template_scope_forbidden",
                "message": (
                    "Team templates are only accessible to the template owner "
                    "or an admin."
                ),
            },
        )
    return "team", normalized_scope_ref
