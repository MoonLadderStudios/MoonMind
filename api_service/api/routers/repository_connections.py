"""Source Control Settings routes over the existing connection owners.

* GitHub App enrollment (#4022): the operator begins setup (receiving the
  GitHub install URL plus single-use state), installs the App in the
  browser, and the callback verifies the installation against the provider
  before persisting through ``RepositoryConnectionService``.
* Named connections (#4019): list, PAT setup/rotation, repository
  assignment and discovery, Test Connection, and removal. PAT setup creates
  the Managed Secret internally, so the operator never handles SecretRefs.
  Every provider call uses only the selected connection's credential (or
  the candidate being validated); the ambient token chain is never read.

``RepositoryConnectionService`` stays the single writable authority for
connections and ``SecretsService`` for secret material.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal, Mapping, Sequence

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from moonmind.auth.github_app_setup import GitHubAppSetupService
from moonmind.workflows.executions.repository_contract import (
    REPOSITORY_SETUP_REQUIRED,
    RepositoryAssignment,
    RepositoryConnection,
    RepositoryContractError,
    RepositoryIdentity,
    RepositoryRouteError,
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


def _error(
    status_code: int, *, kind: str, message: str, code: str = ""
) -> HTTPException:
    """Structured, secret-free error: ``kind`` names the fact that failed."""

    return HTTPException(
        status_code=status_code,
        detail={"kind": kind, "code": code or kind, "message": message},
    )


def _route_error_to_http(exc: RepositoryRouteError) -> HTTPException:
    code = getattr(exc, "code", "") or ""
    message = str(exc).split(": ", 1)[-1]
    if code in {
        "REPOSITORY_DENIED",
        "REPOSITORY_ROUTE_CONFLICT",
        "REPOSITORY_ID_REUSE",
        "REPOSITORY_POLICY_CONFLICT",
    }:
        return _error(
            status.HTTP_409_CONFLICT, kind="conflict", code=code, message=message
        )
    if code == "REPOSITORY_SETUP_REQUIRED":
        return _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            code=code,
            message=message,
        )
    return _error(
        status.HTTP_400_BAD_REQUEST, kind="validation", code=code, message=message
    )


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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
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


# ---------------------------------------------------------------------------
# Source Control Settings (#4019)
# ---------------------------------------------------------------------------

#: The single-user admission boundary: the admitted operator administers
#: every instance connection as the stable ``operator`` principal.
_SETTINGS_PRINCIPAL = "operator"
_SETTINGS_SCOPE: tuple[str, str | None] = ("system", None)
_ADMISSION: dict[str, Any] = {
    "principal_ref": _SETTINGS_PRINCIPAL,
    "principal_scope": _SETTINGS_SCOPE,
}
_GITHUB_ENDPOINT = "https://github.com"
#: Managed Secret slug owned by one Settings PAT connection.
_PAT_SECRET_PREFIX = "repository-connection-"
_CONNECTION_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_OPERATION = Literal["read", "write"]


class RepositoryAssignmentView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    repository: str
    provider_repo_id: str | None = Field(None, alias="providerRepoId")
    operations: list[str]
    revision: int


class RepositoryConnectionView(BaseModel):
    """Operator-facing projection of one named connection (no secret refs)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str = Field(alias="displayName")
    credential_kind: Literal["pat", "github_app", "other"] = Field(
        alias="credentialKind"
    )
    account: str | None = None
    installation: str | None = None
    endpoint: str
    lifecycle: str
    state: Literal["ready", "no_repositories", "disabled", "credential_unavailable"]
    state_summary: str = Field(alias="stateSummary")
    allowed_operations: list[str] = Field(alias="allowedOperations")
    assignments: list[RepositoryAssignmentView]
    policy_revision: int = Field(alias="policyRevision")
    credential_revision: int = Field(alias="credentialRevision")
    secret_revision: int | None = Field(None, alias="secretRevision")


class ProbeModeView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: str
    label: str
    description: str
    required_permissions: dict[str, str] = Field(alias="requiredPermissions")
    optional_permissions: dict[str, str] = Field(alias="optionalPermissions")


class RepositoryConnectionListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[RepositoryConnectionView]
    probe_modes: list[ProbeModeView] = Field(alias="probeModes")


class PatConnectionSetupRequest(BaseModel):
    """Protected PAT setup submission.

    Non-token fields default so a missing field can never make request
    validation echo the submitted body (and its token) back; the handler
    checks them instead.
    """

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field("", max_length=256, alias="requestId")
    connection_id: str = Field(
        "", max_length=64, pattern=_CONNECTION_ID_PATTERN, alias="connectionId"
    )
    display_name: str = Field("", max_length=200, alias="displayName")
    token: SecretStr = SecretStr("")
    allowed_operations: list[_OPERATION] = Field(
        default_factory=lambda: ["read"], alias="allowedOperations"
    )


class PatRotationRequest(BaseModel):
    """Protected rotation submission (same echo-safe shape as setup)."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field("", max_length=256, alias="requestId")
    token: SecretStr = SecretStr("")
    expected_secret_revision: int | None = Field(
        None, ge=1, alias="expectedSecretRevision"
    )


class RepositoryAssignmentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(min_length=1, max_length=256, alias="requestId")
    repository: str = Field(min_length=3, max_length=200)
    operations: list[_OPERATION] = Field(default_factory=lambda: ["read"])
    expected_revision: int | None = Field(None, ge=1, alias="expectedRevision")


class RepositoryAssignmentRemoveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(min_length=1, max_length=256, alias="requestId")
    provider_repo_id: str = Field(min_length=1, alias="providerRepoId")
    repository: str = ""


class ConnectionProbeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    repo: str = Field(min_length=3, max_length=200)
    mode: str = Field(min_length=1)
    base_branch: str | None = Field(None, max_length=255, alias="baseBranch")


class ProbeChecklistItem(BaseModel):
    permission: str
    level: str
    required: bool
    status: str


class ProbeDiagnostic(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation: str
    kind: str
    http_status: int | None = Field(None, alias="httpStatus")
    message: str | None = None
    retryable: bool = False


class ConnectionProbeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    policy_revision: int = Field(alias="policyRevision")
    credential_revision: int = Field(alias="credentialRevision")
    secret_revision: int | None = Field(None, alias="secretRevision")
    repo: str
    mode: str
    repository_accessible: bool | None = Field(None, alias="repositoryAccessible")
    default_branch_accessible: bool | None = Field(
        None, alias="defaultBranchAccessible"
    )
    pull_request_accessible: bool | None = Field(None, alias="pullRequestAccessible")
    resolved_branch: str | None = Field(None, alias="resolvedBranch")
    branch_source: Literal["requested", "remote_default"] | None = Field(
        None, alias="branchSource"
    )
    write_verified: bool = Field(False, alias="writeVerified")
    permission_checklist: list[ProbeChecklistItem] = Field(alias="permissionChecklist")
    diagnostics: list[ProbeDiagnostic]
    limitations: list[str]


class DiscoveredRepository(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_repo_id: str = Field(alias="providerRepoId")
    full_name: str = Field(alias="fullName")
    default_branch: str | None = Field(None, alias="defaultBranch")
    private: bool = False


class RepositoryDiscoveryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    repositories: list[DiscoveredRepository]
    complete: bool
    pages_read: int = Field(alias="pagesRead")
    diagnostics: list[ProbeDiagnostic]


class ConnectionRemovalResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    credential_removed: bool | None = Field(alias="credentialRemoved")


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message=f"{field_name} is required.",
        )
    return text


def _repository_name(value: str) -> str:
    repository = str(value or "").strip()
    if not _REPOSITORY_RE.fullmatch(repository):
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message="Enter the repository as owner/name.",
        )
    return repository


def _managed_secret_slug(connection: RepositoryConnection) -> str | None:
    credential = connection.credential
    if getattr(credential, "source", "") != "secret_ref":
        return None
    ref = credential.credential_ref  # type: ignore[union-attr]
    return ref.key if ref.provider == "managed" else None


def _status_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _connection_view(
    connection: RepositoryConnection,
    assignments: Sequence[RepositoryAssignment],
    secrets: Mapping[str, Any],
) -> RepositoryConnectionView:
    credential = connection.credential
    source = getattr(credential, "source", "")
    account: str | None = None
    installation: str | None = None
    secret_revision: int | None = None
    credential_ok = True
    if source == "secret_ref":
        kind: Literal["pat", "github_app", "other"] = "pat"
        slug = _managed_secret_slug(connection)
        if slug is not None:
            row = secrets.get(slug)
            if row is None or _status_value(row.status) != "active":
                credential_ok = False
            else:
                account = str((row.details or {}).get("githubLogin") or "") or None
                secret_revision = int(row.credential_revision or 1)
    elif source == "github_app":
        kind = "github_app"
        account = getattr(credential, "account", None) or None
        installation = getattr(credential, "installation_ref", None) or None
    else:
        kind = "other"
    count = len(assignments)
    if connection.lifecycle == "disabled":
        state: Any = "disabled"
        summary = "Disabled. Workflows cannot use this connection."
    elif not credential_ok:
        state = "credential_unavailable"
        summary = (
            "The stored token is missing or inactive. Rotate the token to "
            "restore access."
        )
    elif count == 0:
        state = "no_repositories"
        summary = (
            "No repositories assigned. This connection grants no repository "
            "access until you assign one."
        )
    else:
        state = "ready"
        summary = (
            f"Assigned to {count} {'repository' if count == 1 else 'repositories'}."
        )
    return RepositoryConnectionView(
        id=connection.id,
        displayName=connection.display_name,
        credentialKind=kind,
        account=account,
        installation=installation,
        endpoint=connection.endpoint_ref,
        lifecycle=connection.lifecycle,
        state=state,
        stateSummary=summary,
        allowedOperations=list(connection.allowed_operations),
        assignments=[
            RepositoryAssignmentView(
                repository=assignment.identity.display_name,
                providerRepoId=assignment.identity.provider_repo_id,
                operations=list(assignment.operations),
                revision=assignment.revision,
            )
            for assignment in assignments
        ],
        policyRevision=connection.policy_revision,
        credentialRevision=connection.credential_revision,
        secretRevision=secret_revision,
    )


async def _administered(
    db: AsyncSession,
) -> tuple[
    list[tuple[RepositoryConnection, list[RepositoryAssignment]]], dict[str, Any]
]:
    from api_service.services.repository_connections import RepositoryConnectionService
    from api_service.services.secrets import SecretsService

    connections = await RepositoryConnectionService(db).list_administered_connections(
        **_ADMISSION
    )
    secrets = {row.slug: row for row in await SecretsService.list_metadata(db)}
    return connections, secrets


async def _selected(
    db: AsyncSession, connection_id: str
) -> tuple[RepositoryConnection, list[RepositoryAssignment], dict[str, Any]]:
    connections, secrets = await _administered(db)
    for connection, assignments in connections:
        if connection.id == connection_id:
            return connection, assignments, secrets
    raise _error(
        status.HTTP_404_NOT_FOUND,
        kind="not_found",
        message=f"Connection {connection_id!r} does not exist.",
    )


async def _selected_view(
    db: AsyncSession, connection_id: str
) -> RepositoryConnectionView:
    connection, assignments, secrets = await _selected(db, connection_id)
    return _connection_view(connection, assignments, secrets)


def _require_active(connection: RepositoryConnection) -> None:
    if connection.lifecycle != "active":
        raise _error(
            status.HTTP_409_CONFLICT,
            kind="disabled",
            message="This connection is disabled; it cannot reach GitHub.",
        )


async def _connection_headers(
    connection: RepositoryConnection, *, repository: str = ""
) -> dict[str, str]:
    """Acquire request headers from the selected connection only."""

    from moonmind.auth.github_app_wiring import acquire_bound_headers_for_connection

    is_app = getattr(connection.credential, "source", "") == "github_app"
    try:
        headers, _redact = await acquire_bound_headers_for_connection(
            connection,
            operations=("read",),
            execution_owner=f"settings:source-control:{connection.id}",
            repository=repository if is_app else "",
            repository_display=repository or connection.id,
            **_ADMISSION,
        )
    except Exception as exc:  # reported as one safe fact
        logger.warning(
            "source_control_credential_unavailable",
            connection_id=connection.id,
            error_type=exc.__class__.__name__,
        )
        raise _error(
            status.HTTP_409_CONFLICT,
            kind="credential_unavailable",
            message=(
                "MoonMind could not load this connection's credential. Rotate "
                "the token or check the App installation."
            ),
        ) from exc
    return headers


_FAILURE_STATUS = {
    "rate_limited": status.HTTP_503_SERVICE_UNAVAILABLE,
    "unavailable": status.HTTP_502_BAD_GATEWAY,
}


def _provider_failure(
    diagnostic: Mapping[str, Any] | None, *, subject: str
) -> HTTPException:
    kind = str((diagnostic or {}).get("kind") or "rejected")
    messages = {
        "authentication": f"GitHub rejected this connection's credential while reading {subject}.",
        "permission": f"This connection's credential cannot read {subject}.",
        "not_found": f"{subject} was not found, or this connection cannot see it.",
        "rate_limited": (
            f"GitHub is rate limiting this connection; {subject} was not read. "
            "Try again after the limit resets."
        ),
        "unavailable": f"GitHub could not be reached while reading {subject}. Try again later.",
    }
    return _error(
        _FAILURE_STATUS.get(kind, status.HTTP_422_UNPROCESSABLE_CONTENT),
        kind=kind,
        message=messages.get(kind, f"GitHub did not return {subject}."),
    )


async def _validate_candidate(token: str) -> dict[str, Any]:
    """Validate only the submitted candidate token against GitHub."""

    from moonmind.workflows.adapters.github_service import GitHubService

    identity, failure = await GitHubService().get_authenticated_user(token=token)
    if identity is not None:
        return identity
    reason = str((failure or {}).get("reasonCode") or "")
    if reason == "identity_auth_failure":
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="authentication",
            message="GitHub rejected this token. Nothing was saved.",
        )
    if reason == "provider_rate_limited":
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            kind="rate_limited",
            message=(
                "GitHub is rate limiting requests. Nothing was saved; try again "
                "after the limit resets."
            ),
        )
    if reason == "provider_unavailable":
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            kind="unavailable",
            message=(
                "GitHub could not be reached to validate the token. Nothing was "
                "saved; try again when GitHub is reachable."
            ),
        )
    raise _error(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        kind="validation",
        message=(
            "GitHub did not identify a user or bot account for this token. "
            "Nothing was saved."
        ),
    )


def _pat_connection(
    *, connection_id: str, display_name: str, operations: Sequence[str], slug: str
) -> RepositoryConnection:
    from moonmind.workflows.temporal.runtime.launcher import (
        resolve_deployment_git_client_policy,
    )

    try:
        client_policy = resolve_deployment_git_client_policy()
    except RepositoryContractError as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            kind="unavailable",
            message="The deployment's Git client could not be inspected. Nothing was saved.",
        ) from exc
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": connection_id,
            "provider": "git",
            "displayName": display_name,
            "endpointRef": _GITHUB_ENDPOINT,
            "allowedOperations": list(operations),
            "clientPolicy": client_policy.model_dump(by_alias=True, mode="json"),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "managed", "key": slug},
            },
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": _SETTINGS_PRINCIPAL,
                "scopeType": "system",
                "allowedPrincipalRefs": [],
            },
            "hostingService": "github",
        }
    )


def _settings_connection_has_active_bindings(_connection_id: str) -> bool:
    """Binding authority for Settings-managed (database) connections.

    Workflow executions still resolve connections from the deployment
    registry files rather than these rows (#2619 owns workflow inputs), so no
    execution can hold a binding to one yet; assignments and route defaults,
    which the service checks itself, are the only references. Replace this
    with the execution binding lookup when runs bind database connections.
    """

    return False


@router.get(
    "",
    response_model=RepositoryConnectionListResponse,
    summary="List named repository connections for Source Control Settings",
    tags=["RepositoryConnections"],
)
async def list_repository_connections(
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> RepositoryConnectionListResponse:
    from moonmind.workflows.adapters.github_service import GitHubService

    connections, secrets = await _administered(db)
    return RepositoryConnectionListResponse(
        items=[
            _connection_view(connection, assignments, secrets)
            for connection, assignments in connections
        ],
        probeModes=[
            ProbeModeView.model_validate(mode)
            for mode in GitHubService.probe_mode_catalog()
        ],
    )


@router.post(
    "/pat",
    response_model=RepositoryConnectionView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a named connection from a personal access token",
    tags=["RepositoryConnections"],
)
async def create_pat_connection(
    request: PatConnectionSetupRequest,
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> RepositoryConnectionView:
    """Validate the candidate, then save its secret and connection atomically.

    A retried request that already committed returns the committed
    connection without calling GitHub again; a reused ID is a conflict,
    never a suffixed new record.
    """

    from api_service.services.repository_connections import RepositoryConnectionService
    from api_service.services.secrets import (
        SecretConflictError,
        SecretFencedError,
        SecretsService,
    )

    request_id = _required_text(request.request_id, "requestId")
    connection_id = _required_text(request.connection_id, "connectionId")
    display_name = _required_text(request.display_name, "displayName")
    token = request.token.get_secret_value().strip()
    if not token:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message="Enter a token.",
        )
    service = RepositoryConnectionService(db)
    try:
        replayed = await service.replayed_connection(
            request_id=request_id,
            action="connection.create",
            connection_id=connection_id,
            **_ADMISSION,
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    if replayed is not None:
        return await _selected_view(db, connection_id)

    identity = await _validate_candidate(token)
    slug = f"{_PAT_SECRET_PREFIX}{connection_id}"
    operations = sorted({"read", *request.allowed_operations})
    connection = _pat_connection(
        connection_id=connection_id,
        display_name=display_name,
        operations=operations,
        slug=slug,
    )
    try:
        # Staged without commit: the connection writer's commit makes the
        # secret and the connection durable together, or neither.
        await SecretsService.create_secret(
            db,
            slug,
            token,
            details={
                "purpose": "repository_connection",
                "repositoryConnectionId": connection_id,
                "githubLogin": identity["login"],
                "githubUserId": identity["id"],
            },
            request_id=f"{request_id}:secret",
            reason="Source Control connection setup",
            commit=False,
        )
        await service.create_connection(
            connection,
            actor_ref=_SETTINGS_PRINCIPAL,
            request_id=request_id,
            **_ADMISSION,
        )
    except RepositoryRouteError as exc:
        await db.rollback()
        raise _route_error_to_http(exc) from exc
    except (SecretConflictError, SecretFencedError) as exc:
        await db.rollback()
        raise _error(
            status.HTTP_409_CONFLICT,
            kind="conflict",
            message=(
                f"Connection ID {connection_id!r} is already in use or belonged to "
                "a removed connection. Choose another ID."
            ),
        ) from exc
    return await _selected_view(db, connection_id)


@router.post(
    "/{connection_id}/rotate",
    response_model=RepositoryConnectionView,
    summary="Rotate a connection's personal access token",
    tags=["RepositoryConnections"],
)
async def rotate_pat_connection(
    connection_id: str,
    request: PatRotationRequest,
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> RepositoryConnectionView:
    """Replace the token for the same GitHub account, fenced by revision."""

    from api_service.services.secrets import (
        SecretConflictError,
        SecretFencedError,
        SecretRepairRequiredError,
        SecretsService,
    )

    request_id = _required_text(request.request_id, "requestId")
    token = request.token.get_secret_value().strip()
    if not token:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message="Enter the replacement token.",
        )
    if request.expected_secret_revision is None:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message="expectedSecretRevision is required.",
        )
    connection, _assignments, secrets = await _selected(db, connection_id)
    slug = _managed_secret_slug(connection)
    if slug is None:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message="Only token connections created in Source Control rotate here.",
        )
    row = secrets.get(slug)
    recorded_user_id = (
        (row.details or {}).get("githubUserId") if row is not None else None
    )
    recorded_login = (row.details or {}).get("githubLogin") if row is not None else None

    async def _same_account(candidate: str) -> bool:
        identity = await _validate_candidate(candidate)
        if recorded_user_id is not None and identity["id"] != recorded_user_id:
            raise _error(
                status.HTTP_409_CONFLICT,
                kind="account_mismatch",
                message=(
                    f"This token signs in as {identity['login']}, but the "
                    f"connection uses {recorded_login or 'another account'}. "
                    "Create a separate connection for a different account."
                ),
            )
        return True

    try:
        rotated = await SecretsService.rotate_secret(
            db,
            slug,
            token,
            expected_credential_revision=request.expected_secret_revision,
            validator=_same_account,
            request_id=request_id,
            reason="Source Control token rotation",
        )
    except SecretFencedError as exc:
        await db.rollback()
        raise _error(
            status.HTTP_409_CONFLICT,
            kind="conflict",
            message=(
                "The stored token changed since this form loaded. Reload the "
                "connection and try again."
            ),
        ) from exc
    except (SecretConflictError, SecretRepairRequiredError) as exc:
        await db.rollback()
        raise _error(
            status.HTTP_409_CONFLICT,
            kind="conflict",
            message="This rotation request conflicts with the stored token. Reload and try again.",
        ) from exc
    if rotated is None:
        raise _error(
            status.HTTP_409_CONFLICT,
            kind="credential_unavailable",
            message="The stored token is missing, so it cannot be rotated.",
        )
    return await _selected_view(db, connection_id)


@router.post(
    "/{connection_id}/assignments",
    response_model=RepositoryConnectionView,
    summary="Assign a repository verified through the selected connection",
    tags=["RepositoryConnections"],
)
async def assign_repository(
    connection_id: str,
    request: RepositoryAssignmentRequest,
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> RepositoryConnectionView:
    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.workflows.adapters.github_service import GitHubService

    repository = _repository_name(request.repository)
    operations = sorted({"read", *request.operations})
    connection, _assignments, _secrets = await _selected(db, connection_id)
    _require_active(connection)
    disallowed = [op for op in operations if op not in connection.allowed_operations]
    if disallowed:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message=f"This connection does not allow: {', '.join(disallowed)}.",
        )
    headers = await _connection_headers(connection, repository=repository)
    identity, failure = await GitHubService().read_repository_identity(
        repo=repository, headers=headers
    )
    if identity is None:
        raise _provider_failure(failure, subject=repository)
    assignment = RepositoryAssignment(
        connectionId=connection.id,
        identity=RepositoryIdentity(
            endpoint=connection.endpoint_ref,
            providerRepoId=identity["providerRepoId"],
            displayName=identity["fullName"],
        ),
        operations=tuple(operations),
        revision=request.expected_revision or 1,
        verified=True,
    )
    try:
        await RepositoryConnectionService(db).set_assignment(
            assignment,
            actor_ref=_SETTINGS_PRINCIPAL,
            request_id=request.request_id.strip(),
            **_ADMISSION,
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    return await _selected_view(db, connection_id)


@router.post(
    "/{connection_id}/assignments/remove",
    response_model=RepositoryConnectionView,
    summary="Remove one repository assignment",
    tags=["RepositoryConnections"],
)
async def remove_repository_assignment(
    connection_id: str,
    request: RepositoryAssignmentRemoveRequest,
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> RepositoryConnectionView:
    from api_service.services.repository_connections import RepositoryConnectionService

    connection, _assignments, _secrets = await _selected(db, connection_id)
    provider_repo_id = request.provider_repo_id.strip()
    try:
        await RepositoryConnectionService(db).remove_assignment(
            connection_id=connection.id,
            identity=RepositoryIdentity(
                endpoint=connection.endpoint_ref,
                providerRepoId=provider_repo_id,
                displayName=request.repository.strip() or provider_repo_id,
            ),
            actor_ref=_SETTINGS_PRINCIPAL,
            request_id=request.request_id.strip(),
            **_ADMISSION,
        )
    except RepositoryRouteError as exc:
        raise _route_error_to_http(exc) from exc
    return await _selected_view(db, connection_id)


@router.get(
    "/{connection_id}/repositories",
    response_model=RepositoryDiscoveryResponse,
    summary="Discover repositories visible to the selected connection",
    tags=["RepositoryConnections"],
)
async def discover_connection_repositories(
    connection_id: str,
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> RepositoryDiscoveryResponse:
    """Bounded discovery; partial pages are reported, never treated as denial."""

    from moonmind.workflows.adapters.github_service import GitHubService

    connection, _assignments, _secrets = await _selected(db, connection_id)
    _require_active(connection)
    headers = await _connection_headers(connection)
    found = await GitHubService().discover_repositories(
        headers=headers,
        installation=getattr(connection.credential, "source", "") == "github_app",
    )
    return RepositoryDiscoveryResponse.model_validate(
        {"connectionId": connection.id, **found}
    )


@router.post(
    "/{connection_id}/probe",
    response_model=ConnectionProbeResponse,
    summary="Test Connection: read-only probe with the selected connection",
    tags=["RepositoryConnections"],
)
async def probe_repository_connection(
    connection_id: str,
    request: ConnectionProbeRequest,
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> ConnectionProbeResponse:
    from moonmind.workflows.adapters.github_service import GitHubService

    mode = request.mode.strip()
    if mode not in GitHubService.github_permission_profiles():
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            kind="validation",
            message=f"Unknown test mode {mode!r}.",
        )
    repository = _repository_name(request.repo)
    connection, _assignments, secrets = await _selected(db, connection_id)
    _require_active(connection)
    headers = await _connection_headers(connection, repository=repository)
    result = await GitHubService().probe_token(
        repo=repository,
        mode=mode,
        base_branch=request.base_branch,
        headers=headers,
        credential_source={
            "sourceKind": "repository_connection",
            "sourceName": connection.id,
            "resolved": True,
        },
    )
    view = _connection_view(connection, _assignments, secrets)
    return ConnectionProbeResponse.model_validate(
        {
            **result,
            "connectionId": connection.id,
            "policyRevision": connection.policy_revision,
            "credentialRevision": connection.credential_revision,
            "secretRevision": view.secret_revision,
        }
    )


@router.delete(
    "/{connection_id}",
    response_model=ConnectionRemovalResponse,
    summary="Remove a connection and the token it created",
    tags=["RepositoryConnections"],
)
async def remove_repository_connection(
    connection_id: str,
    request_id: str = Query(..., min_length=1, max_length=256, alias="requestId"),
    db: AsyncSession = Depends(get_async_session),
    _user: Any = Depends(get_current_user()),
) -> ConnectionRemovalResponse:
    """Delete an unassigned connection, then its now-unreferenced secret.

    The internal Managed Secret is removed only when the Secrets System's
    complete consumer inventory proves nothing else references it.
    """

    from api_service.services.repository_connections import RepositoryConnectionService
    from api_service.services.secrets import SecretsService

    connections, _secrets = await _administered(db)
    current = next((c for c, _a in connections if c.id == connection_id), None)
    slug = _managed_secret_slug(current) if current is not None else None
    try:
        await RepositoryConnectionService(db).delete_connection(
            connection_id,
            actor_ref=_SETTINGS_PRINCIPAL,
            request_id=request_id.strip(),
            has_active_bindings=_settings_connection_has_active_bindings,
            **_ADMISSION,
        )
    except RepositoryRouteError as exc:
        if exc.code == REPOSITORY_SETUP_REQUIRED:
            raise _error(
                status.HTTP_404_NOT_FOUND,
                kind="not_found",
                message=f"Connection {connection_id!r} does not exist.",
            ) from exc
        raise _route_error_to_http(exc) from exc
    if current is None:
        # A replay of a removal that already committed.
        return ConnectionRemovalResponse(
            connectionId=connection_id, credentialRemoved=None
        )
    removed = False
    if slug is not None:
        try:
            removed = await SecretsService.delete_secret(
                db,
                slug,
                request_id=f"{request_id.strip()}:secret",
                reason="Source Control connection removed",
            )
        except Exception as exc:  # noqa: BLE001 - the connection removal stands
            logger.warning(
                "source_control_secret_cleanup_failed",
                connection_id=connection_id,
                error_type=exc.__class__.__name__,
            )
    return ConnectionRemovalResponse(
        connectionId=connection_id, credentialRemoved=removed
    )
