"""Settings catalog and effective-value API routes."""

from __future__ import annotations

from typing import Annotated, Any, get_args

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

VALID_SETTING_SCOPES = set(get_args(SettingScope))
VALID_SETTING_SECTIONS = set(get_args(SettingSection))


class SettingsPatchRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    expected_versions: dict[str, int] = Field(default_factory=dict)
    reason: str | None = None


def _error_response(status_code: int, error: SettingsError) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error.model_dump(mode="json"),
    )


def _invalid_scope_response(scope: str) -> JSONResponse:
    return _error_response(
        400,
        settings_error(
            "invalid_scope",
            f"Unknown settings scope: {scope}.",
            scope=scope,
            details={"allowed_scopes": sorted(VALID_SETTING_SCOPES)},
        ),
    )


def _coerce_scope(scope: str) -> SettingScope | JSONResponse:
    if scope not in VALID_SETTING_SCOPES:
        return _invalid_scope_response(scope)
    return scope  # type: ignore[return-value]


def _coerce_section(section: str | None) -> SettingSection | JSONResponse | None:
    if section is not None and section not in VALID_SETTING_SECTIONS:
        return _error_response(
            400,
            settings_error(
                "invalid_section",
                f"Unknown settings section: {section}.",
                details={"allowed_sections": sorted(VALID_SETTING_SECTIONS)},
            ),
        )
    return section  # type: ignore[return-value]


@router.get("/catalog")
async def get_settings_catalog(
    section: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
):
    service = SettingsCatalogService()
    resolved_section = _coerce_section(section)
    if isinstance(resolved_section, JSONResponse):
        return resolved_section
    resolved_scope = None
    if scope is not None:
        coerced_scope = _coerce_scope(scope)
        if isinstance(coerced_scope, JSONResponse):
            return coerced_scope
        resolved_scope = coerced_scope
    return service.catalog(section=resolved_section, scope=resolved_scope)


@router.get("/effective")
async def get_effective_settings(scope: Annotated[str, Query()] = "workspace"):
    service = SettingsCatalogService()
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    return service.effective_values(scope=resolved_scope)


@router.get("/effective/{key}")
async def get_effective_setting(
    key: str,
    scope: Annotated[str, Query()] = "workspace",
):
    service = SettingsCatalogService()
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    try:
        return service.effective_value(key, scope=resolved_scope)
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
async def patch_settings(scope: str, payload: SettingsPatchRequest):
    service = SettingsCatalogService()
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    if not payload.changes:
        return _error_response(
            400,
            settings_error(
                "no_settings_changed",
                "No settings were changed.",
                scope=resolved_scope,
            ),
        )
    for key in payload.changes:
        try:
            service.ensure_write_allowed(key, scope=resolved_scope)
        except KeyError:
            return _error_response(
                404,
                settings_error(
                    "setting_not_exposed",
                    f"Setting {key} is not exposed through the Settings API.",
                    key=key,
                    scope=resolved_scope,
                ),
            )
        except ValueError:
            return _error_response(
                400,
                settings_error(
                    "invalid_scope",
                    f"Setting {key} is not available at scope {resolved_scope}.",
                    key=key,
                    scope=resolved_scope,
                ),
            )
        except PermissionError as exc:
            return _error_response(
                423,
                settings_error(
                    "read_only_setting",
                    str(exc),
                    key=key,
                    scope=resolved_scope,
                ),
            )
    return _error_response(
        501,
        settings_error(
            "settings_write_unavailable",
            "Scoped override persistence is not enabled for this story.",
            scope=resolved_scope,
        ),
    )
