"""Operator-invokable GitHub App enrollment (#4022).

Settings CRUD, PAT activation and trusted-boundary setup begin/callback use the existing
authenticated API surface: the operator begins setup (receiving the
GitHub install URL plus single-use state), installs the App in the
browser, and the callback verifies the installation against the
provider before persisting through ``RepositoryConnectionService``,
the single writable authority for connections.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import ManagedSecret
from api_service.services.repository_connections import RepositoryConnectionService
from api_service.services.secrets import (
    SecretConflictError,
    SecretFencedError,
    SecretsService,
)
from api_service.services.settings_catalog import settings_permissions_for_user
from moonmind.auth.github_app_setup import GitHubAppSetupService, SetupConfiguration
from moonmind.workflows.executions.repository_contract import (
    RepositoryAssignment,
    RepositoryConnection,
    RepositoryIdentity,
    RepositoryOperation,
    RepositoryRouteError,
    authorize_connection_use,
    github_repository_name_from_value,
)

logger = structlog.get_logger(__name__)


class _CredentialSafeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def credential_safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "Invalid repository connection request."},
                )

        return credential_safe_handler


router = APIRouter(route_class=_CredentialSafeRoute)

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

    app_connection_id: str | None = Field(default=None, alias="appConnectionId")
    app_slug: str = Field(default="", min_length=1, alias="appSlug")
    expected_app_ref: str = Field(default="", min_length=1, alias="expectedAppRef")
    app_id: str = Field(default="", pattern=r"^[0-9]+$", alias="appId")
    key_secret_ref: str = Field(default="", min_length=1, alias="keySecretRef")
    request_id: str = Field(min_length=1, alias="requestId")
    connection_id: str = Field(min_length=1, alias="connectionId")
    expected_account: str = Field(default="", alias="expectedAccount")
    permitted_repositories: Sequence[str] = Field(
        default=(), alias="permittedRepositories"
    )
    display_name: str = Field(default="GitHub App connection", alias="displayName")
    endpoint_ref: str = Field(default="https://github.com", alias="endpointRef")
    allowed_operations: Sequence[RepositoryOperation] = Field(
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
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> GitHubAppBeginResponse:
    """Issue single-use setup state plus the provider install URL."""

    service = get_setup_service()
    from moonmind.auth.github_app import _numeric_key
    from moonmind.auth.github_app_wiring import github_api_base_for

    principal = _admitted_principal(_user)
    if request.app_connection_id:
        _settings_admission(
            _user, "settings.effective.read", "settings.workspace.write"
        )
        connection = await _settings_connection(
            db, request.app_connection_id, principal
        )
        if (
            connection.credential.source != "github_app"
            or connection.lifecycle != "active"
        ):
            raise HTTPException(
                status_code=422, detail="Select an active configured GitHub App."
            )
        authorize_connection_use(
            principal_ref=principal,
            principal_scope=("system", None),
            connection=connection,
            action="edit",
        )

        from moonmind.auth.github_app_wiring import (
            default_resolve_secret_ref,
            key_secret_ref_for,
            make_github_app_jwt,
        )

        app_id = _numeric_key(connection.credential.app_ref)
        key_ref = key_secret_ref_for(connection)
        api_base = github_api_base_for(connection.endpoint_ref)
        try:
            key = await default_resolve_secret_ref(key_ref)
            jwt = make_github_app_jwt(
                key.encode() if isinstance(key, str) else bytes(key), app_id=app_id
            )
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{api_base}/app",
                    headers={
                        "Authorization": f"Bearer {jwt}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                response.raise_for_status()
                app = response.json()
            if str(app.get("id")) != app_id or not app.get("slug"):
                raise ValueError("configured App mismatch")
        except Exception as exc:
            logger.warning(
                "github_app_configuration_unavailable", error_type=type(exc).__name__
            )
            raise HTTPException(
                status_code=503,
                detail="Configured GitHub App verification is unavailable.",
            ) from None
        request = request.model_copy(
            update={
                "app_slug": app["slug"],
                "expected_app_ref": connection.credential.app_ref,
                "app_id": app_id,
                "key_secret_ref": key_ref,
                "endpoint_ref": connection.endpoint_ref,
            }
        )
    elif not all(
        (
            request.app_slug,
            request.expected_app_ref,
            request.app_id,
            request.key_secret_ref,
        )
    ):
        raise HTTPException(
            status_code=422,
            detail="Select a configured GitHub App or complete the existing App configuration setup.",
        )
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
    app_path = "apps" if provider_origin.hostname == "api.github.com" else "github-apps"
    setup_url = (
        f"{install_origin}/{app_path}/{quote(request.app_slug.strip(), safe='')}"
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


# MoonLadderStudios/MoonMind#4019: the ordinary Settings adapter. Persistence,
# revision fencing and credential activation remain with their existing owners.


class ConnectionSettingsItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    display_name: str = Field(alias="displayName")
    credential_kind: Literal["pat", "github_app"] = Field(alias="credentialKind")
    account: str | None = None
    installation: str | None = None
    repositories: list[str]
    allowed_operations: list[RepositoryOperation] = Field(alias="allowedOperations")
    lifecycle: str
    policy_revision: int = Field(alias="policyRevision")
    credential_revision: int = Field(alias="credentialRevision")


class ConnectionSettingsList(BaseModel):
    items: list[ConnectionSettingsItem]


class ConnectionSettingsReceipt(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    request_id: str = Field(alias="requestId")
    committed: bool
    connection: ConnectionSettingsItem | None = None


class ConnectionSettingsRevision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    request_id: str = Field(
        alias="requestId", min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9:_-]+$"
    )
    expected_policy_revision: int = Field(alias="expectedPolicyRevision", ge=1)
    expected_credential_revision: int = Field(alias="expectedCredentialRevision", ge=1)


class ConnectionSettingsUpdate(ConnectionSettingsRevision):
    display_name: str | None = Field(
        default=None, alias="displayName", min_length=1, max_length=128
    )
    plaintext: SecretStr | None = None
    repositories: list[str] | None = None
    allowed_operations: list[RepositoryOperation] | None = Field(
        default=None, alias="allowedOperations"
    )


class ConnectionSettingsCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    connection_id: str = Field(
        alias="connectionId", min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$"
    )
    request_id: str = Field(
        alias="requestId", min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9:_-]+$"
    )
    display_name: str = Field(alias="displayName", min_length=1, max_length=128)
    plaintext: SecretStr
    repositories: list[str] = Field(default_factory=list)
    allowed_operations: list[RepositoryOperation] = Field(
        default_factory=lambda: ["read"], alias="allowedOperations"
    )

    @field_validator("display_name")
    @classmethod
    def non_sensitive_name(cls, value: str) -> str:
        if value.startswith(("github_pat_", "ghp_")):
            raise ValueError("a connection name cannot contain a credential")
        return value


class ConnectionAppChoice(BaseModel):
    id: str
    label: str


class ConnectionSetupOptions(BaseModel):
    apps: list[ConnectionAppChoice]


def _settings_admission(user: Any, *permissions: str) -> str:
    principal = _admitted_principal(user)
    if not set(permissions).issubset(settings_permissions_for_user(user)):
        raise HTTPException(
            status_code=403, detail="Source Control action is not permitted."
        )
    return principal


async def _settings_item(
    db: AsyncSession, connection: RepositoryConnection, principal: str
) -> ConnectionSettingsItem:
    assignments = await RepositoryConnectionService(db).list_assignments(
        connection.id, principal_ref=principal, principal_scope=("system", None)
    )
    credential = connection.credential
    account = getattr(credential, "account", None)
    if (
        credential.source == "secret_ref"
        and credential.credential_ref.provider == "managed"
    ):
        details = (
            await db.execute(
                select(ManagedSecret.details).where(
                    ManagedSecret.slug == credential.credential_ref.key
                )
            )
        ).scalar_one_or_none() or {}
        account = (details.get("github_account") or {}).get("login")
    return ConnectionSettingsItem(
        id=connection.id,
        displayName=connection.display_name,
        credentialKind="github_app" if credential.source == "github_app" else "pat",
        account=account,
        installation=getattr(credential, "installation_ref", None),
        repositories=[
            a.identity.display_name
            or github_repository_name_from_value(a.identity.canonical_remote or "")
            or ""
            for a in assignments
        ],
        allowedOperations=list(connection.allowed_operations),
        lifecycle=connection.lifecycle,
        policyRevision=connection.policy_revision,
        credentialRevision=connection.credential_revision,
    )


async def _settings_connection(
    db: AsyncSession, connection_id: str, principal: str
) -> RepositoryConnection:
    connection = await RepositoryConnectionService(db).get_connection(
        connection_id,
        principal_ref=principal,
        principal_scope=("system", None),
        include_disabled=True,
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Repository connection not found.")
    if (
        connection.provider != "git"
        or connection.hosting_service != "github"
        or connection.credential.source not in {"secret_ref", "github_app"}
    ):
        raise HTTPException(
            status_code=409, detail="This connection uses a different setup surface."
        )
    return connection


def _settings_revisions(
    connection: RepositoryConnection,
    request: ConnectionSettingsRevision,
    principal: str,
) -> None:
    if (
        connection.policy_revision != request.expected_policy_revision
        or connection.credential_revision != request.expected_credential_revision
    ):
        raise HTTPException(
            status_code=409,
            detail="Connection changed. Refresh and review your preserved draft before saving.",
        )
    authorize_connection_use(
        principal_ref=principal,
        principal_scope=("system", None),
        connection=connection,
        action="edit",
    )


def _repository_names(names: list[str]) -> list[str]:
    import re

    result = []
    for value in names:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise HTTPException(
                status_code=422, detail="Use one explicit owner/repository per line."
            )
        if value.lower() not in {name.lower() for name in result}:
            result.append(value)
    return result


async def _pat_account(token: str) -> dict[str, Any]:
    from moonmind.workflows.adapters.github_service import GitHubService

    account, failure = await GitHubService().get_authenticated_user(token=token)
    if account is None:
        code = (failure or {}).get("httpStatus")
        raise HTTPException(
            status_code=422 if code in {401, 403} else 503,
            detail={
                "code": "account_verification_failed",
                "mutationCommitted": False,
                "message": (
                    "GitHub account verification failed."
                    if code in {401, 403}
                    else "GitHub account verification is unavailable. Your saved connection is unchanged."
                ),
            },
        )
    return account


async def _verify_pat_assignments(
    token: str,
    names: list[str],
    connection_id: str,
    operations: list[RepositoryOperation],
) -> list[RepositoryAssignment]:
    """Verify explicit repository identities for admission, without testing writes."""
    from moonmind.workflows.adapters.github_service import GitHubService

    result = []
    async with httpx.AsyncClient(timeout=15) as client:
        for name in names:
            try:
                response = await client.get(
                    f"https://api.github.com/repos/{name}",
                    headers=GitHubService._github_headers(token),
                )
                response.raise_for_status()
                data = response.json()
                if (
                    not isinstance(data, dict)
                    or not data.get("id")
                    or str(data.get("full_name", "")).lower() != name.lower()
                ):
                    raise ValueError("repository identity mismatch")
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=(
                        503
                        if exc.response.status_code >= 500
                        or exc.response.status_code == 429
                        else 422
                    ),
                    detail={
                        "code": "repository_verification_unavailable",
                        "mutationCommitted": False,
                        "message": (
                            "Repository verification is unavailable; existing assignments are preserved."
                            if exc.response.status_code >= 500
                            or exc.response.status_code == 429
                            else "GitHub did not verify an explicitly selected repository."
                        ),
                    },
                ) from None
            except (httpx.TransportError, ValueError):
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "repository_verification_unavailable",
                        "mutationCommitted": False,
                        "message": "Repository verification is unavailable; existing assignments are preserved.",
                    },
                ) from None
            result.append(
                RepositoryAssignment(
                    connectionId=connection_id,
                    identity=RepositoryIdentity(
                        endpoint="https://github.com",
                        providerRepoId=str(data["id"]),
                        displayName=data["full_name"],
                    ),
                    operations=tuple(operations),
                    revision=1,
                    verified=True,
                )
            )
    return result


async def _replace_settings_assignments(
    service: RepositoryConnectionService,
    connection: RepositoryConnection,
    assignments: list[RepositoryAssignment],
    request_id: str,
    principal: str,
) -> None:
    previous = await service.list_assignments(
        connection.id, principal_ref=principal, principal_scope=("system", None)
    )
    old = {a.identity.display_name.lower(): a for a in previous}
    keep = {a.identity.display_name.lower() for a in assignments}
    for index, assignment in enumerate(assignments):
        existing = old.get(assignment.identity.display_name.lower())
        if existing is not None:
            assignment = assignment.model_copy(
                update={"revision": existing.revision, "identity": existing.identity}
            )
        await service.set_assignment(
            assignment,
            actor_ref=principal,
            request_id=f"{request_id}:assignment:{index}",
            principal_ref=principal,
            principal_scope=("system", None),
            commit=False,
        )
    for index, assignment in enumerate(previous):
        if assignment.identity.display_name.lower() not in keep:
            await service.remove_assignment(
                connection_id=connection.id,
                identity=assignment.identity,
                actor_ref=principal,
                request_id=f"{request_id}:detach:{index}",
                principal_ref=principal,
                principal_scope=("system", None),
                commit=False,
            )


@router.get("", response_model=ConnectionSettingsList, tags=["RepositoryConnections"])
async def list_settings_connections(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionSettingsList:
    principal = _settings_admission(user, "settings.effective.read")
    connections = await RepositoryConnectionService(db).export_snapshot_connections(
        principal_ref=principal, principal_scope=("system", None), include_disabled=True
    )
    return ConnectionSettingsList(
        items=[
            await _settings_item(db, c, principal)
            for c in connections
            if c.provider == "git"
            and c.hosting_service == "github"
            and c.credential.source in {"secret_ref", "github_app"}
        ]
    )


@router.get(
    "/setup-options",
    response_model=ConnectionSetupOptions,
    tags=["RepositoryConnections"],
)
async def settings_setup_options(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionSetupOptions:
    principal = _settings_admission(user, "settings.effective.read")
    connections = await RepositoryConnectionService(db).export_snapshot_connections(
        principal_ref=principal, principal_scope=("system", None)
    )
    return ConnectionSetupOptions(
        apps=[
            ConnectionAppChoice(id=c.id, label=c.display_name)
            for c in connections
            if c.credential.source == "github_app" and c.lifecycle == "active"
        ]
    )


@router.get(
    "/{connection_id}/operations/{request_id}",
    response_model=ConnectionSettingsReceipt,
    tags=["RepositoryConnections"],
)
async def settings_saved_result(
    connection_id: str,
    request_id: str,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionSettingsReceipt:
    principal = _settings_admission(user, "settings.effective.read")
    try:
        receipt = await RepositoryConnectionService(db).get_mutation_receipt(
            connection_id,
            request_id,
            principal_ref=principal,
            principal_scope=("system", None),
        )
        connection = (
            await RepositoryConnectionService(db).get_connection(
                connection_id,
                principal_ref=principal,
                principal_scope=("system", None),
                include_disabled=True,
            )
            if receipt
            else None
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from None
    return ConnectionSettingsReceipt(
        requestId=request_id,
        committed=receipt is not None,
        connection=(
            await _settings_item(db, connection, principal) if connection else None
        ),
    )


@router.get(
    "/{connection_id}",
    response_model=ConnectionSettingsItem,
    tags=["RepositoryConnections"],
)
async def get_settings_connection(
    connection_id: str,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionSettingsItem:
    principal = _settings_admission(user, "settings.effective.read")
    return await _settings_item(
        db, await _settings_connection(db, connection_id, principal), principal
    )


@router.post(
    "",
    response_model=ConnectionSettingsItem,
    status_code=201,
    tags=["RepositoryConnections"],
)
async def create_settings_connection(
    request: ConnectionSettingsCreate,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionSettingsItem:
    principal = _settings_admission(
        user,
        "settings.workspace.write",
        "secrets.value.write",
        "settings.effective.read",
    )
    service = RepositoryConnectionService(db)
    try:
        if await service.get_mutation_receipt(
            request.connection_id,
            request.request_id,
            principal_ref=principal,
            principal_scope=("system", None),
        ):
            return await _settings_item(
                db,
                await _settings_connection(db, request.connection_id, principal),
                principal,
            )
        token = request.plaintext.get_secret_value()
        account = await _pat_account(token)
        names = _repository_names(request.repositories)
        assignments = await _verify_pat_assignments(
            token, names, request.connection_id, request.allowed_operations
        )
        from hashlib import sha256

        from moonmind.workflows.temporal.runtime.launcher import (
            resolve_deployment_git_client_policy,
        )

        slug = (
            "repository-connection-"
            + sha256(request.connection_id.encode()).hexdigest()[:32]
        )
        await SecretsService.create_secret(
            db,
            slug,
            token,
            details={"owner_ref": principal, "github_account": account},
            request_id=request.request_id + ":secret",
            commit=False,
        )
        connection = RepositoryConnection.model_validate(
            {
                "schemaVersion": "moonmind.repository-connection.v1",
                "id": request.connection_id,
                "provider": "git",
                "hostingService": "github",
                "displayName": request.display_name,
                "endpointRef": "https://github.com",
                "allowedOperations": request.allowed_operations,
                "clientPolicy": resolve_deployment_git_client_policy(),
                "credential": {
                    "source": "secret_ref",
                    "credentialRef": {"provider": "managed", "key": slug},
                },
                "ownership": {
                    "ownerRef": principal,
                    "scopeType": "system",
                    "allowedPrincipalRefs": [principal],
                },
            }
        )
        connection = await service.create_connection(
            connection,
            actor_ref=principal,
            request_id=request.request_id,
            principal_ref=principal,
            principal_scope=("system", None),
            commit=False,
        )
        await _replace_settings_assignments(
            service, connection, assignments, request.request_id, principal
        )
        await db.commit()
    except (RepositoryRouteError, SecretFencedError, SecretConflictError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Connection save conflicted. Refresh and review the preserved draft.",
        ) from exc
    return await _settings_item(db, connection, principal)


@router.patch(
    "/{connection_id}",
    response_model=ConnectionSettingsItem,
    tags=["RepositoryConnections"],
)
async def update_settings_connection(
    connection_id: str,
    request: ConnectionSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionSettingsItem:
    principal = _settings_admission(
        user, "settings.workspace.write", "settings.effective.read"
    )
    service = RepositoryConnectionService(db)
    try:
        if await service.get_mutation_receipt(
            connection_id,
            request.request_id,
            principal_ref=principal,
            principal_scope=("system", None),
        ):
            return await _settings_item(
                db, await _settings_connection(db, connection_id, principal), principal
            )
        connection = await _settings_connection(db, connection_id, principal)
        _settings_revisions(connection, request, principal)
        previous = await service.list_assignments(
            connection_id, principal_ref=principal, principal_scope=("system", None)
        )
        old = {a.identity.display_name.lower(): a for a in previous}
        names = (
            _repository_names(request.repositories)
            if request.repositories is not None
            else [a.identity.display_name for a in previous]
        )
        operations = (
            request.allowed_operations
            if request.allowed_operations is not None
            else list(connection.allowed_operations)
        )
        additions = [name for name in names if name.lower() not in old]
        rotation = request.plaintext is not None
        if rotation or additions:
            if connection.credential.source == "secret_ref":
                ref = connection.credential.credential_ref
                if (
                    ref.provider != "managed"
                    or connection.endpoint_ref != "https://github.com"
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Use the existing credential setup surface for this connection.",
                    )
                token = (
                    request.plaintext.get_secret_value()
                    if rotation
                    else await SecretsService.get_secret(db, ref.key)
                )
                if not token:
                    raise HTTPException(
                        status_code=503,
                        detail="The selected credential is unavailable.",
                    )
                if rotation:
                    _settings_admission(user, "secrets.rotate")
                    details = (
                        await db.execute(
                            select(ManagedSecret.details).where(
                                ManagedSecret.slug == ref.key
                            )
                        )
                    ).scalar_one_or_none() or {}

                    async def validate_account(candidate: str) -> bool:
                        account = await _pat_account(candidate)
                        if (details.get("github_account") or {}).get("id") != account[
                            "id"
                        ]:
                            raise HTTPException(
                                status_code=409,
                                detail="Replacement credential must use the saved GitHub account.",
                            )
                        return True

                    validation = await SecretsService.prepare_rotation_validation(
                        db,
                        ref.key,
                        token,
                        validator=validate_account,
                        actor_ref=principal,
                        owner_ref=principal,
                    )
                verified = await _verify_pat_assignments(
                    token, names if rotation else additions, connection_id, operations
                )
            else:
                if rotation:
                    raise HTTPException(
                        status_code=422,
                        detail="GitHub App credentials are managed by the installation.",
                    )
                allowed = {
                    name.lower()
                    for name in connection.credential.permitted_repositories
                }
                if any(name.lower() not in allowed for name in additions):
                    raise HTTPException(
                        status_code=422,
                        detail="Select repositories verified by this App installation.",
                    )
                verified = [
                    RepositoryAssignment(
                        connectionId=connection_id,
                        identity=RepositoryIdentity(
                            endpoint=connection.endpoint_ref,
                            canonicalRemote=f"https://github.com/{name}.git",
                            displayName=name,
                        ),
                        operations=tuple(operations),
                        verified=True,
                    )
                    for name in additions
                ]
        else:
            verified = []
        candidates = {**old, **{a.identity.display_name.lower(): a for a in verified}}
        assignments = [
            candidates[name.lower()].model_copy(
                update={"operations": tuple(operations)}
            )
            for name in names
        ]
        if rotation:
            await SecretsService.rotate_secret(
                db,
                connection.credential.credential_ref.key,
                token,
                expected_credential_revision=request.expected_credential_revision,
                validation=validation,
                request_id=request.request_id + ":secret",
                commit=False,
            )
        updated = connection.model_copy(
            update={
                "display_name": request.display_name or connection.display_name,
                "allowed_operations": tuple(operations),
                "credential_revision": connection.credential_revision + int(rotation),
            }
        )
        connection = await service.update_connection(
            updated,
            actor_ref=principal,
            request_id=request.request_id,
            expected_policy_revision=request.expected_policy_revision,
            principal_ref=principal,
            principal_scope=("system", None),
            commit=False,
        )
        if request.repositories is not None or request.allowed_operations is not None:
            await _replace_settings_assignments(
                service, connection, assignments, request.request_id, principal
            )
        await db.commit()
    except (RepositoryRouteError, SecretFencedError, SecretConflictError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Connection save conflicted. Refresh and review the preserved draft.",
        ) from exc
    return await _settings_item(db, connection, principal)


@router.post(
    "/{connection_id}/disable",
    response_model=ConnectionSettingsItem,
    tags=["RepositoryConnections"],
)
async def disable_settings_connection(
    connection_id: str,
    request: ConnectionSettingsRevision,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionSettingsItem:
    principal = _settings_admission(
        user, "settings.workspace.write", "settings.effective.read"
    )
    service = RepositoryConnectionService(db)
    try:
        if await service.get_mutation_receipt(
            connection_id,
            request.request_id,
            principal_ref=principal,
            principal_scope=("system", None),
        ):
            return await _settings_item(
                db, await _settings_connection(db, connection_id, principal), principal
            )
        connection = await _settings_connection(db, connection_id, principal)
        _settings_revisions(connection, request, principal)
        connection = await service.disable_connection(
            connection_id,
            actor_ref=principal,
            request_id=request.request_id,
            principal_ref=principal,
            principal_scope=("system", None),
            expected_policy_revision=request.expected_policy_revision,
        )
    except RepositoryRouteError as exc:
        raise HTTPException(
            status_code=409, detail="Connection changed; refresh before disabling."
        ) from exc
    return await _settings_item(db, connection, principal)
