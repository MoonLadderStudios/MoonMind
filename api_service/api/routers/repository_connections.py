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
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from moonmind.auth.github_app_setup import GitHubAppSetupService, SetupConfiguration
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
    """Operator configuration bound to one GitHub App enrollment."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    app_slug: str = Field(min_length=1, alias="appSlug")
    expected_app_ref: str = Field(min_length=1, alias="expectedAppRef")
    app_id: str = Field(pattern=r"^[0-9]+$", alias="appId")
    key_secret_ref: str = Field(min_length=1, alias="keySecretRef")
    request_id: str = Field(min_length=1, alias="requestId")
    connection_id: str = Field(min_length=1, alias="connectionId")
    expected_account: str = Field(default="", alias="expectedAccount")
    permitted_repositories: Sequence[str] = Field(
        default=(), alias="permittedRepositories"
    )
    display_name: str = Field(default="GitHub App connection", alias="displayName")
    endpoint_ref: str = Field(default="https://github.com", alias="endpointRef")
    allowed_operations: Sequence[str] = Field(
        default=("read",), alias="allowedOperations"
    )


class GitHubAppBeginResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    setup_url: str = Field(alias="setupUrl")
    state: str
    request_id: str = Field(alias="requestId")
    connection_id: str = Field(alias="connectionId")


class GitHubAppCallbackRequest(BaseModel):
    """Provider result for an existing server-held enrollment."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    state: str = Field(min_length=1)
    installation_id: str = Field(pattern=r"^[0-9]+$", alias="installationId")
    connection_id: str = Field(min_length=1, alias="connectionId")


class GitHubAppCallbackResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(alias="connectionId")


def _route_error_to_http(exc: RepositoryRouteError) -> HTTPException:
    code = getattr(exc, "code", "") or ""
    if code in {
        "REPOSITORY_DENIED",
        "REPOSITORY_ROUTE_CONFLICT",
        "REPOSITORY_ID_REUSE",
    }:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if code in {"REPOSITORY_SETUP_REQUIRED", "REPOSITORY_POLICY_CONFLICT"}:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _admitted_principal(user: Any) -> str:
    """Use the existing authentication result, never browser identity fields."""

    principal = str(getattr(user, "id", "") or "").strip()
    if not principal:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_required"
        )
    return principal


@router.post(
    "/github-app/begin",
    response_model=GitHubAppBeginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin a GitHub App installation enrollment",
    tags=["RepositoryConnections"],
)
async def begin_github_app_setup(
    request: GitHubAppBeginRequest,
    _user: Annotated[Any, Depends(get_current_user())],
) -> GitHubAppBeginResponse:
    """Issue single-use setup state plus the provider install URL."""

    service = get_setup_service()
    from moonmind.auth.github_app import _numeric_key
    from moonmind.auth.github_app_wiring import github_api_base_for

    principal = _admitted_principal(_user)
    try:
        # Only deployment-owned host trust applies, including for Enterprise.
        api_base = github_api_base_for(request.endpoint_ref)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _numeric_key(request.expected_app_ref) != _numeric_key(request.app_id):
        raise HTTPException(status_code=422, detail="configured App identity mismatch")
    if not request.key_secret_ref.strip():
        raise HTTPException(
            status_code=422, detail="App signing-key reference is required"
        )
    try:
        pending = service.begin_setup(
            request_id=request.request_id.strip(),
            connection_id=request.connection_id.strip(),
            principal_ref=principal,
            principal_scope=("system", None),
            expected_app_ref=request.expected_app_ref.strip(),
            configuration=SetupConfiguration(
                app_id=request.app_id,
                key_secret_ref=request.key_secret_ref.strip(),
                endpoint_ref=request.endpoint_ref.strip() or "https://github.com",
                api_base=api_base,
                display_name=request.display_name.strip() or "GitHub App connection",
                allowed_operations=tuple(request.allowed_operations) or ("read",),
            ),
            expected_account=request.expected_account.strip(),
            permitted_repositories=[
                str(name).strip()
                for name in request.permitted_repositories
                if str(name).strip()
            ],
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    from urllib.parse import quote, urlsplit

    provider_origin = urlsplit(api_base)
    install_origin = (
        "https://github.com"
        if provider_origin.hostname == "api.github.com"
        else f"{provider_origin.scheme}://{provider_origin.netloc}"
    )
    setup_url = (
        f"{install_origin}/apps/{quote(request.app_slug.strip(), safe='')}"
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
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> GitHubAppCallbackResponse:
    """Verify the installation with the provider, then persist it."""

    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.auth.github_app_wiring import (
        default_resolve_secret_ref,
        fetch_installation_record,
        make_github_app_jwt,
    )

    service = get_setup_service()
    principal = _admitted_principal(_user)
    try:
        pending = service.admit_setup_callback(
            state=request.state,
            caller_principal=principal,
            caller_scope=("system", None),
            destination_connection_id=request.connection_id,
            allow_consumed=True,
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    configuration = pending.configuration
    if configuration is None:
        raise HTTPException(status_code=409, detail="restart GitHub App enrollment")
    connection_service = RepositoryConnectionService(db)
    if pending.consumed:
        # A lost response can return its committed result without reissuing
        # a JWT or contacting GitHub. A replay never creates another row.
        existing = await connection_service.get_connection(
            pending.connection_id,
            principal_ref=principal,
            principal_scope=("system", None),
        )
        from moonmind.auth.github_app import _numeric_key

        if existing is None or _numeric_key(
            getattr(existing.credential, "installation_ref", "")
        ) != _numeric_key(request.installation_id):
            raise HTTPException(status_code=409, detail="setup state was already used")
        return GitHubAppCallbackResponse(connectionId=existing.id)
    try:
        key_material = await default_resolve_secret_ref(configuration.key_secret_ref)
        jwt = make_github_app_jwt(
            (
                key_material.encode("utf-8")
                if isinstance(key_material, str)
                else bytes(key_material)
            ),
            app_id=configuration.app_id,
        )
        provider_installation: Mapping[str, Any] = await fetch_installation_record(
            jwt=jwt,
            installation_id=request.installation_id.strip(),
            api_base=configuration.api_base,
        )
    except Exception as exc:
        logger.warning("github_app_provider_fetch_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub App installation verification is unavailable.",
        ) from exc
    try:
        saved = await connection_service.create_github_app_connection(
            setup_service=service,
            provider_installation=provider_installation,
            expected_app_ref=pending.expected_app_ref,
            request_id=pending.request_id,
            connection_id=pending.connection_id,
            state=request.state,
            installation_ref=request.installation_id,
            caller_principal=principal,
            caller_scope=("system", None),
            destination_connection_id=pending.connection_id,
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    return GitHubAppCallbackResponse(connectionId=saved.id)
