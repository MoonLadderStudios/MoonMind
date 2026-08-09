from __future__ import annotations

from fastapi import HTTPException, status

from api_service.db.models import User
from moonmind.config.settings import settings

def ensure_preset_catalog_enabled() -> None:
    """Raise a 404 when the preset catalog is disabled."""

    if settings.feature_flags.preset_catalog_enabled:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "preset_catalog_disabled",
            "message": "Preset catalog is disabled in this environment.",
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

    if normalized_scope not in {"global", "personal"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_template_scope",
                "message": "scope must be one of: global, personal",
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "template_scope_ref_required",
                "message": "scopeRef is required when scope is personal.",
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
