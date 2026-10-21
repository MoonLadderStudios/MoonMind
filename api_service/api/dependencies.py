from __future__ import annotations

from fastapi import HTTPException, status

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
