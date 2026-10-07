"""Source Control connection routes (#4019) and GitHub App enrollment (#4022).

Mounts the connection list/detail, PAT creation, rotation, disable, and
assignment operations plus the trusted-boundary App setup begin/callback on
the existing authenticated API surface. Every write goes through
``RepositoryConnectionService``, the single writable authority for
connections. PAT creation and rotation store the token as a Managed Secret
in the same transaction; responses never carry tokens or SecretRefs, and
caller identity always comes from the admission dependency.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from moonmind.auth.github_app import GITHUB_REPOSITORY_NAME_PATTERN
from moonmind.auth.github_app_setup import GitHubAppSetupService, SetupConfiguration
from moonmind.workflows.executions.repository_contract import (
    REPOSITORY_POLICY_CONFLICT,
    REPOSITORY_SETUP_REQUIRED,
    RepositoryAssignment,
    RepositoryConnection,
    RepositoryIdentity,
    RepositoryOperation,
    RepositoryRouteError,
    SecretRefCredential,
)

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
    app_id: str = Field(pattern=r"^[0-9]+$", alias="appId")
    # Derived server-side when omitted (#4019): the App identity follows the
    # App ID and the signing key uses the deployment's managed default.
    # Supplying them is the advanced reuse path.
    expected_app_ref: str = Field(default="", alias="expectedAppRef")
    key_secret_ref: str = Field(default="", alias="keySecretRef")
    request_id: str = Field(min_length=1, alias="requestId")
    connection_id: str = Field(min_length=1, alias="connectionId")
    expected_account: str = Field(default="", alias="expectedAccount")
    permitted_repositories: Sequence[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, pattern=GITHUB_REPOSITORY_NAME_PATTERN
            ),
        ]
    ] = Field(min_length=1, alias="permittedRepositories")
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
) -> GitHubAppBeginResponse:
    """Issue single-use setup state plus the provider install URL."""

    service = get_setup_service()
    from moonmind.auth.github_app import _numeric_key
    from moonmind.auth.github_app_wiring import (
        DEFAULT_KEY_SECRET_REF,
        github_api_base_for,
    )

    principal = _admitted_principal(_user)
    request = request.model_copy(
        update={
            "expected_app_ref": request.expected_app_ref.strip()
            or f"github-app:{request.app_id}",
            "key_secret_ref": request.key_secret_ref.strip() or DEFAULT_KEY_SECRET_REF,
        }
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

    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.auth.bound_acquisition import BoundAccessError
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
            permitted_repositories=pending.permitted_repositories,
        )
    except BoundAccessError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
            401,
            403,
            404,
        }:
            raise HTTPException(
                status_code=422,
                detail="GitHub App installation cannot access the requested repositories.",
            ) from exc
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


# -- Source Control connections (#4019) -------------------------------------

_SYSTEM_SCOPE: tuple[str, str | None] = ("system", None)
_CONNECTION_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PAT_SECRET_PROVIDER = "db"

CredentialKind = Literal["personal_access_token", "github_app", "deployment", "other"]
ConnectionDisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class RepositoryAssignmentView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    repository: str
    provider_repo_id: str | None = Field(default=None, alias="providerRepoId")
    operations: list[str]
    revision: int
    verified: bool


class RepositoryConnectionView(BaseModel):
    """Operator-facing connection summary; never a token or SecretRef."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str = Field(alias="displayName")
    hosting_service: str | None = Field(default=None, alias="hostingService")
    endpoint: str
    credential_kind: CredentialKind = Field(alias="credentialKind")
    account: str | None = None
    installation_id: str | None = Field(default=None, alias="installationId")
    permitted_repositories: list[str] = Field(
        default_factory=list, alias="permittedRepositories"
    )
    lifecycle: str
    policy_revision: int = Field(alias="policyRevision")
    credential_revision: int = Field(alias="credentialRevision")
    allowed_operations: list[str] = Field(alias="allowedOperations")
    assignments: list[RepositoryAssignmentView] = Field(default_factory=list)


class RepositoryConnectionListResponse(BaseModel):
    items: list[RepositoryConnectionView]


class ConnectionRequestStatus(BaseModel):
    """Whether the selected connection's exact update request committed."""

    committed: bool


class PatConnectionCreateRequest(BaseModel):
    """Create a named PAT connection; the token becomes a Managed Secret."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=200, alias="requestId")
    connection_id: str = Field(pattern=_CONNECTION_ID_PATTERN, alias="connectionId")
    display_name: ConnectionDisplayName = Field(alias="displayName")
    token: SecretStr
    allowed_operations: list[RepositoryOperation] = Field(
        default_factory=lambda: ["read"], alias="allowedOperations", min_length=1
    )


class ConnectionUpdateRequest(BaseModel):
    """Rename, change operations, or rotate the token of one connection."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=200, alias="requestId")
    expected_policy_revision: int = Field(ge=1, alias="expectedPolicyRevision")
    display_name: ConnectionDisplayName | None = Field(
        default=None, alias="displayName"
    )
    token: SecretStr | None = None
    allowed_operations: list[RepositoryOperation] | None = Field(
        default=None, alias="allowedOperations", min_length=1
    )


class ConnectionActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=200, alias="requestId")


class AssignmentSetRequest(BaseModel):
    """Assign one repository, verified through the connection's own access."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=200, alias="requestId")
    repository: str = Field(min_length=3, max_length=200)
    operations: list[RepositoryOperation] = Field(
        default_factory=lambda: ["read"], min_length=1
    )
    revision: int = Field(default=1, ge=1)


class AssignmentRemoveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=200, alias="requestId")
    provider_repo_id: str = Field(min_length=1, alias="providerRepoId")
    repository: str = Field(min_length=1)


def _connection_error_to_http(exc: RepositoryRouteError) -> HTTPException:
    """Map service outcomes; a stale revision is a conflict the form keeps."""

    code = getattr(exc, "code", "") or ""
    if code == REPOSITORY_POLICY_CONFLICT:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if code == REPOSITORY_SETUP_REQUIRED and "not found" in str(exc):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if code == "REPOSITORY_DENIED":
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return _route_error_to_http(exc)


def _credential_kind(connection: RepositoryConnection) -> CredentialKind:
    source = connection.credential.source
    if source == "secret_ref":
        return "personal_access_token"
    if source == "github_app":
        return "github_app"
    if source == "github_resolver":
        return "deployment"
    return "other"


def _connection_view(
    connection: RepositoryConnection,
    assignments: Sequence[RepositoryAssignment],
) -> RepositoryConnectionView:
    credential = connection.credential
    is_app = credential.source == "github_app"
    return RepositoryConnectionView(
        id=connection.id,
        displayName=connection.display_name,
        hostingService=connection.hosting_service,
        endpoint=connection.endpoint_ref,
        credentialKind=_credential_kind(connection),
        account=(getattr(credential, "account", None) or None) if is_app else None,
        installationId=(
            str(getattr(credential, "installation_ref", "")).split(":")[-1] or None
            if is_app
            else None
        ),
        permittedRepositories=(
            list(getattr(credential, "permitted_repositories", ()) or ())
            if is_app
            else []
        ),
        lifecycle=connection.lifecycle,
        policyRevision=connection.policy_revision,
        credentialRevision=connection.credential_revision,
        allowedOperations=list(connection.allowed_operations),
        assignments=[
            RepositoryAssignmentView(
                repository=assignment.identity.display_name,
                providerRepoId=assignment.identity.provider_repo_id,
                operations=list(assignment.operations),
                revision=assignment.revision,
                verified=assignment.verified,
            )
            for assignment in assignments
        ],
    )


async def _view_for(
    service: Any, connection: RepositoryConnection
) -> RepositoryConnectionView:
    return _connection_view(connection, await service.list_assignments(connection.id))


def _pat_secret_slug(connection_id: str, credential_revision: int) -> str:
    return f"repository-connection/{connection_id}/credential-{credential_revision}"


def _actor_uuid(user: Any) -> UUID | None:
    value = getattr(user, "id", None)
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _transient_token(value: SecretStr | None) -> str:
    token = value.get_secret_value().strip() if value is not None else ""
    if not token:
        raise HTTPException(status_code=422, detail="A token is required.")
    return token


async def _stage_pat_secret(
    db: AsyncSession,
    *,
    slug: str,
    token: str,
    connection_id: str,
    request_id: str,
    user: Any,
) -> None:
    """Stage the token as a Managed Secret in the caller's transaction."""

    from api_service.services.secrets import (
        SecretConflictError,
        SecretFencedError,
        SecretsService,
    )

    try:
        await SecretsService.create_secret(
            db,
            slug,
            token,
            {"purpose": "repository_connection", "connectionId": connection_id},
            request_id=f"{request_id}:credential",
            actor_user_id=_actor_uuid(user),
            reason="Source Control connection credential",
            commit=False,
        )
    except (SecretConflictError, SecretFencedError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The connection credential could not be stored; reload the connection.",
        ) from exc


@router.get(
    "",
    response_model=RepositoryConnectionListResponse,
    response_model_by_alias=True,
    summary="List Source Control connections",
    tags=["RepositoryConnections"],
)
async def list_repository_connections(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> RepositoryConnectionListResponse:
    from api_service.services.repository_connections import RepositoryConnectionService

    principal = _admitted_principal(_user)
    service = RepositoryConnectionService(db)
    connections = await service.list_manageable_connections(
        principal_ref=principal, principal_scope=_SYSTEM_SCOPE
    )
    items = [await _view_for(service, connection) for connection in connections]
    items.sort(key=lambda item: (item.display_name.lower(), item.id))
    return RepositoryConnectionListResponse(items=items)


@router.get(
    "/{connection_id}",
    response_model=RepositoryConnectionView,
    response_model_by_alias=True,
    summary="Read one Source Control connection",
    tags=["RepositoryConnections"],
)
async def get_repository_connection(
    connection_id: str,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> RepositoryConnectionView:
    from api_service.services.repository_connections import RepositoryConnectionService

    principal = _admitted_principal(_user)
    service = RepositoryConnectionService(db)
    connection = await service.get_manageable_connection(
        connection_id, principal_ref=principal, principal_scope=_SYSTEM_SCOPE
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Repository connection not found.")
    return await _view_for(service, connection)


@router.get(
    "/{connection_id}/requests/{request_id}",
    response_model=ConnectionRequestStatus,
    summary="Reconcile a Source Control connection update",
    tags=["RepositoryConnections"],
)
async def get_connection_update_status(
    connection_id: str,
    request_id: str,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> ConnectionRequestStatus:
    """Read the existing audit receipt instead of inferring commit from revision."""

    from api_service.services.repository_connections import RepositoryConnectionService

    principal = _admitted_principal(_user)
    service = RepositoryConnectionService(db)
    try:
        connection = await service.get_manageable_connection(
            connection_id, principal_ref=principal, principal_scope=_SYSTEM_SCOPE
        )
        if connection is None:
            raise HTTPException(
                status_code=404, detail="Repository connection not found."
            )
        committed = await service.recorded_request(
            request_id=request_id,
            action="connection.update",
            connection_id=connection_id,
        )
    except RepositoryRouteError as exc:
        raise _connection_error_to_http(exc) from exc
    return ConnectionRequestStatus(committed=committed)


@router.post(
    "/pat",
    response_model=RepositoryConnectionView,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    summary="Create a personal access token connection",
    tags=["RepositoryConnections"],
)
async def create_pat_connection(
    request: PatConnectionCreateRequest,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> RepositoryConnectionView:
    """Create once per request identity; a retry returns the committed row."""

    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.auth.github_app_setup import _deployment_git_client_policy

    principal = _admitted_principal(_user)
    token = _transient_token(request.token)
    service = RepositoryConnectionService(db)
    slug = _pat_secret_slug(request.connection_id, 1)
    connection = RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": request.connection_id,
            "provider": "git",
            "displayName": request.display_name.strip(),
            "endpointRef": "https://github.com",
            "allowedOperations": list(dict.fromkeys(request.allowed_operations)),
            "clientPolicy": _deployment_git_client_policy(),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": _PAT_SECRET_PROVIDER, "key": slug},
            },
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": principal,
                "scopeType": "system",
                "allowedPrincipalRefs": [principal],
            },
            "hostingService": "github",
        }
    )
    try:
        if not await service.recorded_request(
            request_id=request.request_id,
            action="connection.create",
            connection_id=request.connection_id,
        ):
            await service.require_available_connection_id(request.connection_id)
            await _stage_pat_secret(
                db,
                slug=slug,
                token=token,
                connection_id=request.connection_id,
                request_id=request.request_id,
                user=_user,
            )
        saved = await service.create_connection(
            connection,
            actor_ref=principal,
            request_id=request.request_id,
            principal_ref=principal,
            principal_scope=_SYSTEM_SCOPE,
        )
    except RepositoryRouteError as exc:
        await db.rollback()
        raise _connection_error_to_http(exc) from exc
    return await _view_for(service, saved)


@router.patch(
    "/{connection_id}",
    response_model=RepositoryConnectionView,
    response_model_by_alias=True,
    summary="Rename, change operations, or rotate a connection token",
    tags=["RepositoryConnections"],
)
async def update_repository_connection(
    connection_id: str,
    request: ConnectionUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> RepositoryConnectionView:
    """Compare-and-set on ``expectedPolicyRevision``; never a silent overwrite."""

    from api_service.services.repository_connections import RepositoryConnectionService

    principal = _admitted_principal(_user)
    service = RepositoryConnectionService(db)
    try:
        current = await service.get_manageable_connection(
            connection_id, principal_ref=principal, principal_scope=_SYSTEM_SCOPE
        )
        if current is None:
            raise HTTPException(
                status_code=404, detail="Repository connection not found."
            )
        replayed = await service.recorded_request(
            request_id=request.request_id,
            action="connection.update",
            connection_id=connection_id,
        )
        changes: dict[str, Any] = {}
        if request.display_name is not None:
            changes["display_name"] = request.display_name.strip()
        if request.allowed_operations is not None:
            changes["allowed_operations"] = tuple(
                dict.fromkeys(request.allowed_operations)
            )
        if request.token is not None and not replayed:
            token = _transient_token(request.token)
            if current.credential.source != "secret_ref":
                raise HTTPException(
                    status_code=422,
                    detail="Only personal access token connections accept a new token.",
                )
            if request.expected_policy_revision != current.policy_revision:
                raise RepositoryRouteError(
                    REPOSITORY_POLICY_CONFLICT, "stale policy revision"
                )
            next_revision = current.credential_revision + 1
            slug = _pat_secret_slug(connection_id, next_revision)
            await _stage_pat_secret(
                db,
                slug=slug,
                token=token,
                connection_id=connection_id,
                request_id=request.request_id,
                user=_user,
            )
            changes["credential"] = SecretRefCredential.model_validate(
                {
                    "source": "secret_ref",
                    "credentialRef": {"provider": _PAT_SECRET_PROVIDER, "key": slug},
                }
            )
            changes["credential_revision"] = next_revision
        saved = await service.update_connection(
            current.model_copy(update=changes),
            actor_ref=principal,
            request_id=request.request_id,
            expected_policy_revision=request.expected_policy_revision,
            principal_ref=principal,
            principal_scope=_SYSTEM_SCOPE,
        )
    except RepositoryRouteError as exc:
        await db.rollback()
        raise _connection_error_to_http(exc) from exc
    return await _view_for(service, saved)


@router.post(
    "/{connection_id}/disable",
    response_model=RepositoryConnectionView,
    response_model_by_alias=True,
    summary="Stop new use of a connection without removing its records",
    tags=["RepositoryConnections"],
)
async def disable_repository_connection(
    connection_id: str,
    request: ConnectionActionRequest,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> RepositoryConnectionView:
    from api_service.services.repository_connections import RepositoryConnectionService

    principal = _admitted_principal(_user)
    service = RepositoryConnectionService(db)
    try:
        saved = await service.disable_connection(
            connection_id,
            actor_ref=principal,
            request_id=request.request_id,
            principal_ref=principal,
            principal_scope=_SYSTEM_SCOPE,
        )
    except RepositoryRouteError as exc:
        await db.rollback()
        raise _connection_error_to_http(exc) from exc
    return await _view_for(service, saved)


async def observe_github_repository(
    connection: RepositoryConnection,
    repository: str,
    *,
    revision_reader: Any | None = None,
) -> dict[str, str]:
    """Read one repository's provider identity with the connection's credential.

    Raises ``HTTPException``: 422 when the connection cannot read the
    repository, 503 when GitHub or the credential store is unavailable.
    """

    from moonmind.auth.bound_acquisition import (
        BOUND_ISSUER_FAILED,
        BOUND_UNAVAILABLE,
        BoundAccessError,
    )
    from moonmind.auth.github_app_wiring import github_api_base_for
    from moonmind.auth.github_credentials import resolve_connection_github_credential
    from moonmind.workflows.adapters.github_service import GitHubService

    try:
        # Trust the destination before resolving either kind of credential.
        api_base = github_api_base_for(connection.endpoint_ref)
        if connection.credential.source == "github_app":
            headers = await GitHubService().bound_app_headers_for_connection(
                connection,
                repository=repository,
                operations=("read",),
                revision_reader=revision_reader,
            )
        else:
            credential = await resolve_connection_github_credential(
                connection, repo=repository
            )
            if not credential.token:
                raise HTTPException(
                    status_code=503 if credential.retryable else 422,
                    detail=credential.safe_summary,
                )
            headers = GitHubService._github_headers(credential.token)
    except BoundAccessError as exc:
        raise HTTPException(
            status_code=(
                503 if exc.code in {BOUND_ISSUER_FAILED, BOUND_UNAVAILABLE} else 422
            ),
            detail=str(exc),
        ) from exc
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{api_base}/repos/{repository}",
                headers=headers,
            )
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        raise HTTPException(
            status_code=503,
            detail="GitHub is unavailable; existing assignments are unchanged.",
        ) from exc
    if response.status_code >= 500:
        raise HTTPException(
            status_code=503,
            detail="GitHub is unavailable; existing assignments are unchanged.",
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=422,
            detail=f"This connection cannot read {repository} (HTTP {response.status_code}).",
        )
    payload = response.json()
    return {
        "id": str(payload.get("id") or ""),
        "fullName": str(payload.get("full_name") or repository),
    }


@router.post(
    "/{connection_id}/assignments",
    response_model=RepositoryConnectionView,
    response_model_by_alias=True,
    summary="Assign a repository to a connection",
    tags=["RepositoryConnections"],
)
async def set_repository_assignment(
    connection_id: str,
    request: AssignmentSetRequest,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> RepositoryConnectionView:
    """Assign only a repository this connection itself can read."""

    from api_service.services.repository_connections import RepositoryConnectionService

    principal = _admitted_principal(_user)
    repository = request.repository.strip()
    if not _REPOSITORY_NAME.fullmatch(repository):
        raise HTTPException(status_code=422, detail="Use the owner/name form.")
    service = RepositoryConnectionService(db)
    try:
        connection = await service.get_connection(
            connection_id, principal_ref=principal, principal_scope=_SYSTEM_SCOPE
        )
        if connection is None:
            raise HTTPException(
                status_code=404, detail="Repository connection not found."
            )
        if connection.credential.source == "github_app":

            async def read_active_revision(selected_id: str) -> Any:
                from moonmind.auth.bound_acquisition import (
                    BOUND_REVOKED,
                    BoundAccessError,
                )
                from moonmind.auth.github_app_wiring import revision_reader_for

                # A fresh session observes revocation while provider issuance runs.
                async with AsyncSession(bind=db.bind) as current_db:
                    try:
                        current = await RepositoryConnectionService(
                            current_db
                        ).get_connection(
                            selected_id,
                            principal_ref=principal,
                            principal_scope=_SYSTEM_SCOPE,
                        )
                    except RepositoryRouteError as exc:
                        raise BoundAccessError(
                            BOUND_REVOKED, "Repository connection is unavailable"
                        ) from exc
                if current is None:
                    raise BoundAccessError(
                        BOUND_REVOKED, "Repository connection is unavailable"
                    )
                return await revision_reader_for({selected_id: current})(selected_id)

            observed = await observe_github_repository(
                connection, repository, revision_reader=read_active_revision
            )
        else:
            observed = await observe_github_repository(connection, repository)
        if not observed["id"]:
            raise HTTPException(
                status_code=503,
                detail="GitHub did not report a repository identity; try again.",
            )
        await service.set_assignment(
            RepositoryAssignment(
                connectionId=connection_id,
                identity=RepositoryIdentity(
                    endpoint=connection.endpoint_ref,
                    providerRepoId=observed["id"],
                    displayName=observed["fullName"],
                ),
                operations=tuple(dict.fromkeys(request.operations)),
                revision=request.revision,
                verified=True,
            ),
            actor_ref=principal,
            request_id=request.request_id,
            principal_ref=principal,
            principal_scope=_SYSTEM_SCOPE,
        )
    except RepositoryRouteError as exc:
        await db.rollback()
        raise _connection_error_to_http(exc) from exc
    return await _view_for(service, connection)


@router.post(
    "/{connection_id}/assignments/remove",
    response_model=RepositoryConnectionView,
    response_model_by_alias=True,
    summary="Remove a repository assignment from a connection",
    tags=["RepositoryConnections"],
)
async def remove_repository_assignment(
    connection_id: str,
    request: AssignmentRemoveRequest,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    _user: Annotated[Any, Depends(get_current_user())],
) -> RepositoryConnectionView:
    from api_service.services.repository_connections import RepositoryConnectionService

    principal = _admitted_principal(_user)
    service = RepositoryConnectionService(db)
    try:
        connection = await service.get_connection(
            connection_id, principal_ref=principal, principal_scope=_SYSTEM_SCOPE
        )
        if connection is None:
            raise HTTPException(
                status_code=404, detail="Repository connection not found."
            )
        await service.remove_assignment(
            connection_id=connection_id,
            identity=RepositoryIdentity(
                endpoint=connection.endpoint_ref,
                providerRepoId=request.provider_repo_id,
                displayName=request.repository,
            ),
            actor_ref=principal,
            request_id=request.request_id,
            principal_ref=principal,
            principal_scope=_SYSTEM_SCOPE,
        )
    except RepositoryRouteError as exc:
        await db.rollback()
        raise _connection_error_to_http(exc) from exc
    return await _view_for(service, connection)
