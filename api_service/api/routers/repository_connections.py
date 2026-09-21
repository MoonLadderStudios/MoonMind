"""Operator-invokable GitHub App enrollment (#4022).

Mounts the trusted-boundary setup begin/callback on the existing
authenticated API surface: the operator begins setup (receiving the
GitHub install URL plus single-use state), installs the App in the
browser, and the callback verifies the installation against the
provider before persisting through ``RepositoryConnectionService``,
the single writable authority for connections.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from moonmind.auth.github_app_setup import GitHubAppSetupService
from moonmind.workflows.executions.repository_contract import RepositoryRouteError

logger = structlog.get_logger(__name__)

router = APIRouter()

_SETUP_SECRET_ENV_VAR = "MOONMIND_GITHUB_APP_SETUP_SECRET"

_setup_service: GitHubAppSetupService | None = None


def get_setup_service() -> GitHubAppSetupService:
    """Return the process-wide setup-state service (server-held secret)."""

    global _setup_service
    if _setup_service is None:
        secret = str(os.environ.get(_SETUP_SECRET_ENV_VAR) or "").strip()
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "GitHub App enrollment is not configured on this deployment "
                    f"({_SETUP_SECRET_ENV_VAR} is unset)."
                ),
            )
        _setup_service = GitHubAppSetupService(server_secret=secret)
    return _setup_service


class GitHubAppBeginRequest(BaseModel):
    """Operator request to begin one GitHub App enrollment."""

    model_config = ConfigDict(populate_by_name=True)

    app_slug: str = Field(min_length=1, alias="appSlug")
    expected_app_ref: str = Field(min_length=1, alias="expectedAppRef")
    request_id: str = Field(min_length=1, alias="requestId")
    connection_id: str = Field(min_length=1, alias="connectionId")
    principal_ref: str = Field(min_length=1, alias="principalRef")
    principal_scope_type: str = Field(default="system", alias="principalScopeType")
    principal_scope_ref: str | None = Field(default=None, alias="principalScopeRef")
    expected_account: str = Field(default="", alias="expectedAccount")
    permitted_repositories: Sequence[str] = Field(
        default=(), alias="permittedRepositories"
    )


class GitHubAppBeginResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    setup_url: str = Field(alias="setupUrl")
    state: str
    request_id: str = Field(alias="requestId")
    connection_id: str = Field(alias="connectionId")


class GitHubAppCallbackRequest(BaseModel):
    """Browser callback payload after the operator installs the App."""

    model_config = ConfigDict(populate_by_name=True)

    state: str = Field(min_length=1)
    installation_id: str = Field(min_length=1, alias="installationId")
    expected_app_ref: str = Field(min_length=1, alias="expectedAppRef")
    request_id: str = Field(min_length=1, alias="requestId")
    connection_id: str = Field(min_length=1, alias="connectionId")
    app_id: str = Field(min_length=1, alias="appId")
    key_secret_ref: str = Field(min_length=1, alias="keySecretRef")
    expected_account: str = Field(default="", alias="expectedAccount")
    permitted_repositories: Sequence[str] = Field(
        default=(), alias="permittedRepositories"
    )
    allowed_api_hosts: Sequence[str] = Field(default=(), alias="allowedApiHosts")
    display_name: str = Field(default="GitHub App connection", alias="displayName")
    endpoint_ref: str = Field(default="https://github.com", alias="endpointRef")
    allowed_operations: Sequence[str] = Field(default=("read",), alias="allowedOperations")
    owner_ref: str = Field(default="", alias="ownerRef")
    principal_ref: str = Field(default="", alias="principalRef")
    caller_scope_type: str = Field(default="system", alias="callerScopeType")
    caller_scope_ref: str | None = Field(default=None, alias="callerScopeRef")
    actor_ref: str = Field(default="", alias="actorRef")
    key_ref: str | None = Field(default=None, alias="keyRef")


class GitHubAppCallbackResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(alias="connectionId")


def _route_error_to_http(exc: RepositoryRouteError) -> HTTPException:
    code = getattr(exc, "code", "") or ""
    if code in {"REPOSITORY_DENIED", "REPOSITORY_ROUTE_CONFLICT", "REPOSITORY_ID_REUSE"}:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if code in {"REPOSITORY_SETUP_REQUIRED", "REPOSITORY_POLICY_CONFLICT"}:
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/github-app/begin",
    response_model=GitHubAppBeginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin a GitHub App installation enrollment",
    tags=["RepositoryConnections"],
)
async def begin_github_app_setup(
    request: GitHubAppBeginRequest,
    _user: Any = Depends(get_current_user()),
) -> GitHubAppBeginResponse:
    """Issue single-use setup state plus the provider install URL."""

    service = get_setup_service()
    scope_ref = (request.principal_scope_ref or "").strip() or None
    try:
        pending = service.begin_setup(
            request_id=request.request_id.strip(),
            connection_id=request.connection_id.strip(),
            principal_ref=request.principal_ref.strip(),
            principal_scope=(request.principal_scope_type.strip() or "system", scope_ref),
            expected_app_ref=request.expected_app_ref.strip(),
            expected_account=request.expected_account.strip(),
            permitted_repositories=[
                str(name).strip()
                for name in request.permitted_repositories
                if str(name).strip()
            ],
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    from urllib.parse import quote

    setup_url = (
        f"https://github.com/apps/{quote(request.app_slug.strip(), safe='')}"
        f"/installations/new?state={quote(pending.state, safe='')}"
    )
    return GitHubAppBeginResponse(
        setupUrl=setup_url,
        state=pending.state,
        requestId=pending.request_id,
        connectionId=pending.connection_id,
    )


@router.post(
    "/github-app/callback",
    response_model=GitHubAppCallbackResponse,
    summary="Complete a GitHub App installation enrollment",
    tags=["RepositoryConnections"],
)
async def complete_github_app_setup(
    request: GitHubAppCallbackRequest,
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> GitHubAppCallbackResponse:
    """Verify the installation with the provider, then persist it."""

    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.auth.github_app_wiring import (
        default_resolve_secret_ref,
        fetch_installation_record,
        github_api_base_for,
        make_github_app_jwt,
    )

    service = get_setup_service()
    try:
        api_base = github_api_base_for(
            request.endpoint_ref, allowed_hosts=list(request.allowed_api_hosts)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        key_material = await default_resolve_secret_ref(request.key_secret_ref.strip())
        jwt = make_github_app_jwt(
            key_material.encode("utf-8")
            if isinstance(key_material, str)
            else bytes(key_material),
            app_id=request.app_id.strip(),
        )
        provider_installation: Mapping[str, Any] = await fetch_installation_record(
            jwt=jwt,
            installation_id=request.installation_id.strip(),
            api_base=api_base,
        )
    except Exception as exc:
        logger.warning("github_app_provider_fetch_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub App installation verification is unavailable.",
        ) from exc
    connection_service = RepositoryConnectionService(db)
    caller_scope_ref = (request.caller_scope_ref or "").strip() or None
    try:
        saved = await connection_service.create_github_app_connection(
            setup_service=service,
            provider_installation=provider_installation,
            expected_app_ref=request.expected_app_ref.strip(),
            request_id=request.request_id.strip(),
            connection_id=request.connection_id.strip(),
            expected_account=request.expected_account.strip(),
            permitted_repositories=[
                str(name).strip()
                for name in request.permitted_repositories
                if str(name).strip()
            ],
            state=request.state,
            installation_ref=request.installation_id.strip(),
            caller_principal=request.principal_ref.strip(),
            caller_scope=(
                request.caller_scope_type.strip() or "system",
                caller_scope_ref,
            ),
            destination_connection_id=request.connection_id.strip(),
            display_name=request.display_name.strip() or "GitHub App connection",
            endpoint_ref=request.endpoint_ref.strip() or "https://github.com",
            allowed_operations=list(request.allowed_operations) or ["read"],
            owner_ref=request.owner_ref.strip(),
            principal_ref=request.principal_ref.strip(),
            principal_scope=(
                request.caller_scope_type.strip() or "system",
                caller_scope_ref,
            ),
            actor_ref=request.actor_ref.strip(),
            key_ref=request.key_ref,
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    return GitHubAppCallbackResponse(connectionId=saved.id)
