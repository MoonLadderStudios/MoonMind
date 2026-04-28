"""Settings catalog and effective-value API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api_service.services.settings_catalog import (
    SettingScope,
    SettingSection,
    SettingsCatalogService,
    SettingsError,
    settings_error,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsPatchRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    expected_versions: dict[str, int] = Field(default_factory=dict)
    reason: str | None = None


def _error_response(status_code: int, error: SettingsError) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error.model_dump(mode="json"),
    )


@router.get("/catalog")
async def get_settings_catalog(
    section: Annotated[SettingSection | None, Query()] = None,
    scope: Annotated[SettingScope | None, Query()] = None,
):
    service = SettingsCatalogService()
    return service.catalog(section=section, scope=scope)


@router.get("/effective")
async def get_effective_settings(scope: Annotated[SettingScope, Query()] = "workspace"):
    service = SettingsCatalogService()
    return service.effective_values(scope=scope)


@router.get("/effective/{key}")
async def get_effective_setting(
    key: str,
    scope: Annotated[SettingScope, Query()] = "workspace",
):
    service = SettingsCatalogService()
    try:
        return service.effective_value(key, scope=scope)
    except KeyError:
        return _error_response(
            404,
            settings_error(
                "unknown_setting",
                f"Unknown setting: {key}.",
                key=key,
                scope=scope,
            ),
        )
    except ValueError:
        return _error_response(
            400,
            settings_error(
                "invalid_scope",
                f"Setting {key} is not available at scope {scope}.",
                key=key,
                scope=scope,
            ),
        )


@router.patch("/{scope}")
async def patch_settings(scope: SettingScope, payload: SettingsPatchRequest):
    service = SettingsCatalogService()
    for key in payload.changes:
        try:
            service.ensure_write_allowed(key, scope=scope)
        except KeyError:
            return _error_response(
                404,
                settings_error(
                    "setting_not_exposed",
                    f"Setting {key} is not exposed through the Settings API.",
                    key=key,
                    scope=scope,
                ),
            )
        except ValueError:
            return _error_response(
                400,
                settings_error(
                    "invalid_scope",
                    f"Setting {key} is not available at scope {scope}.",
                    key=key,
                    scope=scope,
                ),
            )
        except PermissionError as exc:
            return _error_response(
                423,
                settings_error(
                    "read_only_setting",
                    str(exc),
                    key=key,
                    scope=scope,
                ),
            )
    return _error_response(
        400,
        settings_error(
            "no_settings_changed",
            "No settings were changed.",
            scope=scope,
        ),
    )
