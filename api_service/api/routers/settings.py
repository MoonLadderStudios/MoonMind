"""Settings catalog and effective-value API routes."""

from __future__ import annotations

import logging
from typing import Annotated, Any, get_args

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from api_service.api.instance_access import access_principal
from api_service.api.schemas import GitHubTokenProbeRequest
from api_service.auth_providers import get_instance_access
from api_service.db import base as db_base
from api_service.services.settings_catalog import (
    SettingsAuditResponse,
    SettingsPreviewResponse,
    SettingSection,
    SettingsCatalogService,
    SettingsError,
    SettingsValidationResponse,
    SettingsValidationError,
    has_settings_permission,
    settings_error,
    settings_permissions_for_user,
)
from moonmind.workflows.executions.repository_contract import RepositoryRouteError

router = APIRouter(prefix="/settings", tags=["settings"])
SETTINGS_CURRENT_USER_DEP = get_instance_access
logger = logging.getLogger(__name__)

VALID_SETTING_SECTIONS = set(get_args(SettingSection))
_MAX_SETTINGS_REQUEST_ID_LENGTH = 255
# Retained account sessions keep their existing write permission. Operator
# admission holds it; the server still enforces locks and read-only settings.
_WRITE_PERMISSION = "settings.workspace.write"


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
    confirmation: str | None = None


class SettingsProposalRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    expected_versions: dict[str, int] = Field(default_factory=dict)
    confirmation: str | None = None


async def probe_github_token(
    *,
    repo: str,
    mode: str,
    connection: Any,
    base_branch: str | None = None,
    revision_reader: Any | None = None,
    admitted_repositories: tuple[str, ...] = (),
) -> dict[str, Any]:
    from moonmind.workflows.adapters.github_service import GitHubService

    return await GitHubService().probe_token(
        repo=repo,
        mode=mode,
        base_branch=base_branch,
        connection=connection,
        revision_reader=revision_reader,
        admitted_repositories=admitted_repositories,
    )


async def _selected_probe_connection(connection_id: str, *, user: Any) -> Any | None:
    """Load only the connection this principal may discover and probe.

    Keep its endpoint and typed credential together through acquisition;
    neither a missing connection nor acquisition failure permits fallback.
    """

    from api_service.services.repository_connections import (
        RepositoryConnectionService,
    )

    principal = access_principal(user).strip()
    if not principal:
        return None
    async with db_base.async_session_maker() as session:
        try:
            return await RepositoryConnectionService(session).get_connection(
                connection_id,
                principal_ref=principal,
                principal_scope=("system", None),
            )
        except RepositoryRouteError:
            return None


async def _readable_assigned_repositories(connection_id: str) -> tuple[str, ...]:
    """Repositories saved on the selected connection for reads."""

    from api_service.services.repository_connections import (
        RepositoryConnectionService,
    )

    async with db_base.async_session_maker() as session:
        assignments = await RepositoryConnectionService(session).list_assignments(
            connection_id
        )
    return tuple(
        assignment.identity.display_name
        for assignment in assignments
        if assignment.verified and "read" in assignment.operations
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


def _error_response(status_code: int, error: SettingsError) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error.model_dump(mode="json"),
    )


def _settings_db_error_response(*, key: str | None = None) -> JSONResponse:
    return _error_response(
        500,
        settings_error(
            "settings_db_unavailable",
            "Settings persistence is unavailable.",
            key=key,
        ),
    )


def _unknown_setting_response(key: str | None) -> JSONResponse:
    return _error_response(
        404,
        settings_error("unknown_setting", f"Unknown setting: {key}.", key=key),
    )


def _settings_request_id(request: Request) -> str | None:
    request_id = request.headers.get("x-request-id") or request.headers.get(
        "x-correlation-id"
    )
    if request_id is None:
        return None
    return request_id[:_MAX_SETTINGS_REQUEST_ID_LENGTH]


async def _record_rejected_write_audit(
    *,
    changes: dict[str, Any],
    reason: str | None,
    request_id: str | None,
) -> None:
    if not changes:
        return
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(session=session)
            await service.record_rejected_write_audit(
                changes=changes,
                reason=reason,
                request_id=request_id,
            )
    except Exception:
        logger.exception("Failed to record rejected settings write audit event")
        return


def _validation_blocker_response(
    response: SettingsValidationResponse,
) -> JSONResponse | None:
    if response.accepted:
        return None
    route_blockers = {
        "setting_not_exposed": (404, "setting_not_exposed"),
        "read_only_setting": (423, "read_only_setting"),
        "operator_locked": (423, "operator_locked"),
        "requires_confirmation": (428, "requires_confirmation"),
        "version_conflict": (409, "version_conflict"),
    }
    if not response.issues:
        return None
    blocker_codes = {issue.code for issue in response.issues}
    if len(blocker_codes) != 1:
        return None
    issue = response.issues[0]
    route = route_blockers.get(issue.code)
    if route is None:
        return None
    status_code, error_code = route
    details = issue.model_dump(mode="json")
    details.pop("key", None)
    details.pop("message", None)
    return _error_response(
        status_code,
        settings_error(
            error_code,
            issue.message,
            key=issue.key,
            details=details,
        ),
    )


def _settings_validation_status(error: SettingsError) -> int:
    if error.error == "operator_locked":
        return 423
    if error.error == "requires_confirmation":
        return 428
    return 400


def _proposal_error(payload: SettingsProposalRequest, user: Any) -> JSONResponse | None:
    denied = _require_permission(user, _WRITE_PERMISSION)
    if denied is not None:
        return denied
    if not payload.changes:
        return _error_response(
            400,
            settings_error("no_settings_changed", "No settings were changed."),
        )
    return None


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
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.catalog.read")
    if denied is not None:
        return denied
    resolved_section = _coerce_section(section)
    if isinstance(resolved_section, JSONResponse):
        return resolved_section
    if not _should_attempt_settings_db():
        return SettingsCatalogService().catalog(section=resolved_section)
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(session=session)
            return await service.catalog_async(section=resolved_section)
    except SQLAlchemyError:
        return _settings_db_error_response()


@router.get("/effective")
async def get_effective_settings(
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    if not _should_attempt_settings_db():
        return SettingsCatalogService().effective_values()
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(session=session)
            return await service.effective_values_async()
    except SQLAlchemyError:
        return _settings_db_error_response()


@router.get("/effective/{key}")
async def get_effective_setting(
    key: str,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    try:
        if not _should_attempt_settings_db():
            return SettingsCatalogService().effective_value(key)
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(session=session)
            return await service.effective_value_async(key)
    except SQLAlchemyError:
        return _settings_db_error_response(key=key)
    except KeyError:
        return _unknown_setting_response(key)


@router.get("/diagnostics")
async def get_settings_diagnostics(
    key: Annotated[str | None, Query()] = None,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    try:
        if not _should_attempt_settings_db():
            return await SettingsCatalogService().diagnostics(key=key)
        try:
            async with db_base.async_session_maker() as session:
                service = SettingsCatalogService(session=session)
                return await service.diagnostics(key=key)
        except SQLAlchemyError:
            return await SettingsCatalogService().diagnostics(key=key)
    except KeyError:
        return _unknown_setting_response(key)


@router.post("/validate")
async def validate_settings(
    payload: SettingsProposalRequest,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    rejected = _proposal_error(payload, user)
    if rejected is not None:
        return rejected
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(session=session)
            response = await service.validate_changes(
                changes=payload.changes,
                expected_versions=payload.expected_versions,
                confirmation=payload.confirmation,
            )
    except SQLAlchemyError:
        return _settings_db_error_response()
    blocker = _validation_blocker_response(response)
    if blocker is not None:
        return blocker
    return response


@router.post("/preview")
async def preview_settings(
    payload: SettingsProposalRequest,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    rejected = _proposal_error(payload, user)
    if rejected is not None:
        return rejected
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(session=session)
            response: SettingsPreviewResponse = await service.preview_changes(
                changes=payload.changes,
                expected_versions=payload.expected_versions,
                confirmation=payload.confirmation,
            )
    except SQLAlchemyError:
        return _settings_db_error_response()
    blocker = _validation_blocker_response(response)
    if blocker is not None:
        return blocker
    return response


@router.post("/github/token-probe")
async def github_token_probe(
    payload: GitHubTokenProbeRequest,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.effective.read")
    if denied is not None:
        return denied
    connection_id = payload.connection_id
    connection = await _selected_probe_connection(connection_id, user=user)
    if connection is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Repository connection not found."},
        )

    async def _read_active_revision(selected_id: str) -> Any:
        from moonmind.auth.bound_acquisition import BOUND_REVOKED, BoundAccessError
        from moonmind.auth.github_app_wiring import revision_reader_for

        current = await _selected_probe_connection(selected_id, user=user)
        if current is None:
            raise BoundAccessError(
                BOUND_REVOKED, "Repository connection is unavailable"
            )
        return await revision_reader_for({selected_id: current})(selected_id)

    # Bound App issuance enforces its own repository restriction; any other
    # credential is tested only against this connection's assignments.
    admitted = (
        ()
        if connection.credential.source == "github_app"
        else await _readable_assigned_repositories(connection_id)
    )
    result = await probe_github_token(
        repo=payload.repo,
        mode=payload.mode,
        base_branch=payload.base_branch,
        connection=connection,
        revision_reader=_read_active_revision,
        admitted_repositories=admitted,
    )
    return {**result, "connectionId": connection_id}


@router.get("/audit")
async def get_settings_audit(
    key: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    denied = _require_permission(user, "settings.audit.read")
    if denied is not None:
        return denied
    try:
        async with db_base.async_session_maker() as session:
            service = SettingsCatalogService(session=session)
            return SettingsAuditResponse(
                items=await service.list_audit_events(
                    permissions=settings_permissions_for_user(user),
                    key=key,
                    limit=limit,
                )
            )
    except SQLAlchemyError:
        return _settings_db_error_response(key=key)


@router.patch("")
async def patch_settings(
    payload: SettingsPatchRequest,
    request: Request,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    service = SettingsCatalogService()
    request_id = _settings_request_id(request)

    async def rejected(status_code: int, error: SettingsError) -> JSONResponse:
        await _record_rejected_write_audit(
            changes=payload.changes,
            reason=payload.reason,
            request_id=request_id,
        )
        return _error_response(status_code, error)

    denied = _require_permission(user, _WRITE_PERMISSION)
    if denied is not None:
        await _record_rejected_write_audit(
            changes=payload.changes,
            reason=payload.reason,
            request_id=request_id,
        )
        return denied
    if not payload.changes:
        return _error_response(
            400,
            settings_error("no_settings_changed", "No settings were changed."),
        )
    for key in payload.changes:
        try:
            service.ensure_write_allowed(key)
        except KeyError:
            return await rejected(
                404,
                settings_error(
                    "setting_not_exposed",
                    f"Setting {key} is not exposed through the Settings API.",
                    key=key,
                ),
            )
        except PermissionError as exc:
            return await rejected(
                423,
                settings_error(service.write_lock_error_code(key), str(exc), key=key),
            )
    try:
        async with db_base.async_session_maker() as session:
            write_service = SettingsCatalogService(session=session)
            return await write_service.apply_overrides(
                changes=payload.changes,
                expected_versions=payload.expected_versions,
                reason=payload.reason,
                request_id=request_id,
                confirmation=payload.confirmation,
            )
    except SettingsValidationError as exc:
        error = exc.to_settings_error()
        return await rejected(_settings_validation_status(error), error)
    except ValueError as exc:
        if str(exc) == "version_conflict":
            return await rejected(
                409,
                settings_error(
                    "version_conflict",
                    "Expected setting version does not match current version.",
                ),
            )
        return await rejected(
            400,
            settings_error(
                "invalid_setting_value", "One or more setting values are invalid."
            ),
        )
    except PermissionError as exc:
        return await rejected(423, settings_error("read_only_setting", str(exc)))


@router.delete("/{key:path}")
async def reset_setting(
    key: str,
    user: Any = Depends(SETTINGS_CURRENT_USER_DEP),
):
    service = SettingsCatalogService()
    denied = _require_permission(user, _WRITE_PERMISSION)
    if denied is not None:
        return denied
    try:
        service.ensure_write_allowed(key)
    except KeyError:
        return _error_response(
            404,
            settings_error(
                "setting_not_exposed",
                f"Setting {key} is not exposed through the Settings API.",
                key=key,
            ),
        )
    except PermissionError as exc:
        return _error_response(
            423,
            settings_error("read_only_setting", str(exc), key=key),
        )
    async with db_base.async_session_maker() as session:
        write_service = SettingsCatalogService(session=session)
        return await write_service.reset_override(key)
