"""Settings catalog and effective-value API routes."""

from __future__ import annotations

from typing import Annotated, Any, get_args
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from api_service.api.schemas import GitHubTokenProbeRequest
from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.services.settings_catalog import (
    SettingsAuditResponse,
    SettingScope,
    SettingSection,
    SettingsCatalogService,
    SettingsError,
    has_settings_permission,
    settings_error,
    settings_permissions_for_user,
)

router = APIRouter(prefix="/settings", tags=["settings"])
SETTINGS_CURRENT_USER_DEP = get_current_user()

VALID_SETTING_SCOPES = set(get_args(SettingScope))
VALID_SETTING_SECTIONS = set(get_args(SettingSection))


def _should_attempt_settings_db() -> bool:
    bind = db_base.async_session_maker.kw.get("bind")
    drivername = getattr(getattr(bind, "url", None), "drivername", "")
    if drivername.startswith("sqlite"):
        return True
    try:
        from moonmind.config.settings import settings

        return not settings.workflow.test_mode
    except Exception:
        return True


class SettingsPatchRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    expected_versions: dict[str, int] = Field(default_factory=dict)
    reason: str | None = None


async def probe_github_token(
    *,
    repo: str,
    mode: str,
    base_branch: str | None = None,
) -> dict[str, Any]:
    from moonmind.workflows.adapters.github_service import GitHubService

    return await GitHubService().probe_token(
        repo=repo,
        mode=mode,
        base_branch=base_branch,
    )


def _permission_denied_response(permission: str) -> JSONResponse:
    return _error_response(
        403,
        settings_error(
            "permission_denied",
            f"Missing required settings permission: {permission}.",
            details={"required_permission": permission},
        ),
    )


def _require_permission(user: Any, permission: str) -> JSONResponse | None:
    if has_settings_permission(user, permission):
        return None
    return _permission_denied_response(permission)


def _uuid_attr(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _service_context_kwargs(user: Any) -> dict[str, UUID]:
    kwargs: dict[str, UUID] = {}
    workspace_id = _uuid_attr(getattr(user, "workspace_id", None))
    user_id = _uuid_attr(getattr(user, "id", None))
    if workspace_id is not None:
        kwargs["workspace_id"] = workspace_id
    if user_id is not None:
        kwargs["user_id"] = user_id
    return kwargs


def _write_permission_for_scope(scope: SettingScope) -> str | None:
    if scope == "user":
        return "settings.user.write"
    if scope == "workspace":
        return "settings.workspace.write"
    if scope == "system":
        return "settings.system.write"
    return None


def _error_response(status_code: int, error: SettingsError) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error.model_dump(mode="json"),
    )


def _settings_db_error_response(*, key: str | None = None, scope: str) -> JSONResponse:
    return _error_response(
        500,
        settings_error(
            "settings_db_unavailable",
            "Settings persistence is unavailable.",
            key=key,
            scope=scope,
        ),
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
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.catalog.read")
    if denied is not None:
        return denied
    resolved_section = _coerce_section(section)
    if isinstance(resolved_section, JSONResponse):
        return resolved_section
    resolved_scope = None
    if scope is not None:
        coerced_scope = _coerce_scope(scope)
        if isinstance(coerced_scope, JSONResponse):
            return coerced_scope
        resolved_scope = coerced_scope
    if resolved_scope is not None and _should_attempt_settings_db():
        try:
            async with db_base.async_session_maker() as session:
                service = SettingsCatalogService(
                    session=session, **_service_context_kwargs(user)
                )
                return await service.catalog_async(
                    section=resolved_section,
                    scope=resolved_scope,
                )
        except SQLAlchemyError:
            return _settings_db_error_response(scope=resolved_scope)
    service = SettingsCatalogService()
    return service.catalog(section=resolved_section, scope=resolved_scope)


@router.get("/effective")
async def get_effective_settings(
    scope: Annotated[str, Query()] = "workspace",
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    if not _should_attempt_settings_db():
        service = SettingsCatalogService()
        return service.effective_values(scope=resolved_scope)
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(
                session=session, **_service_context_kwargs(user)
            )
            return await service.effective_values_async(scope=resolved_scope)
    except SQLAlchemyError:
        return _settings_db_error_response(scope=resolved_scope)


@router.get("/effective/{key}")
async def get_effective_setting(
    key: str,
    scope: Annotated[str, Query()] = "workspace",
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    if not _should_attempt_settings_db():
        try:
            service = SettingsCatalogService()
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
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(
                session=session, **_service_context_kwargs(user)
            )
            return await service.effective_value_async(key, scope=resolved_scope)
    except SQLAlchemyError:
        return _settings_db_error_response(key=key, scope=resolved_scope)
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


@router.get("/diagnostics")
async def get_settings_diagnostics(
    scope: Annotated[str, Query()] = "workspace",
    key: Annotated[str | None, Query()] = None,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    if not _should_attempt_settings_db():
        try:
            service = SettingsCatalogService()
            return await service.diagnostics(scope=resolved_scope, key=key)
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
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(
                session=session, **_service_context_kwargs(user)
            )
            return await service.diagnostics(scope=resolved_scope, key=key)
    except SQLAlchemyError:
        try:
            service = SettingsCatalogService()
            return await service.diagnostics(scope=resolved_scope, key=key)
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


@router.post("/github/token-probe")
async def github_token_probe(
    payload: GitHubTokenProbeRequest,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    return await probe_github_token(
        repo=payload.repo,
        mode=payload.mode,
        base_branch=payload.base_branch,
    )


@router.get("/audit")
async def get_settings_audit(
    key: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.audit.read")
    if denied is not None:
        return denied
    resolved_scope = None
    if scope is not None:
        coerced_scope = _coerce_scope(scope)
        if isinstance(coerced_scope, JSONResponse):
            return coerced_scope
        resolved_scope = coerced_scope
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(
                session=session, **_service_context_kwargs(user)
            )
            return SettingsAuditResponse(
                items=await service.list_audit_events(
                    permissions=settings_permissions_for_user(user),
                    key=key,
                    scope=resolved_scope,
                    limit=limit,
                )
            )
    except SQLAlchemyError:
        return _settings_db_error_response(key=key, scope=scope or "workspace")


@router.patch("/{scope}")
async def patch_settings(
    scope: str,
    payload: SettingsPatchRequest,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    service = SettingsCatalogService()
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    required_permission = _write_permission_for_scope(resolved_scope)
    if required_permission is None:
        return _error_response(
            400,
            settings_error(
                "invalid_scope",
                f"Setting writes are not available at scope {resolved_scope}.",
                scope=resolved_scope,
            ),
        )
    denied = _require_permission(user, required_permission)
    if denied is not None:
        return denied
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
    try:
        async with db_base.async_session_maker() as session:
            write_service = SettingsCatalogService(
                session=session, **_service_context_kwargs(user)
            )
            return await write_service.apply_overrides(
                scope=resolved_scope,
                changes=payload.changes,
                expected_versions=payload.expected_versions,
                reason=payload.reason,
            )
    except ValueError as exc:
        if str(exc) == "version_conflict":
            status_code = 409
            error_code = "version_conflict"
            message = "Expected setting version does not match current version."
        elif str(exc) == "invalid_scope":
            status_code = 400
            error_code = "invalid_scope"
            message = (
                f"One or more settings are not available at scope {resolved_scope}."
            )
        else:
            status_code = 400
            error_code = "invalid_setting_value"
            message = "One or more setting values are invalid."
        return _error_response(
            status_code,
            settings_error(error_code, message, scope=resolved_scope),
        )
    except PermissionError as exc:
        return _error_response(
            423,
            settings_error(
                "read_only_setting",
                str(exc),
                scope=resolved_scope,
            ),
        )


@router.delete("/{scope}/{key:path}")
async def reset_setting(
    scope: str,
    key: str,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    service = SettingsCatalogService()
    resolved_scope = _coerce_scope(scope)
    if isinstance(resolved_scope, JSONResponse):
        return resolved_scope
    required_permission = _write_permission_for_scope(resolved_scope)
    if required_permission is None:
        return _error_response(
            400,
            settings_error(
                "invalid_scope",
                f"Setting writes are not available at scope {resolved_scope}.",
                key=key,
                scope=resolved_scope,
            ),
        )
    denied = _require_permission(user, required_permission)
    if denied is not None:
        return denied
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
    try:
        async with db_base.async_session_maker() as session:
            write_service = SettingsCatalogService(
                session=session, **_service_context_kwargs(user)
            )
            return await write_service.reset_override(key, scope=resolved_scope)
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
