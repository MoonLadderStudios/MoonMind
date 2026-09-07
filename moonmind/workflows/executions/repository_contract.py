"""Provider-aware repository compilation and readiness boundary.

MM-1219 replaces repository-shaped authoring aliases with one discriminated
target.  This module owns compilation, connection reconciliation, capability
derivation, and the pre-mutation readiness check so those decisions cannot
drift across authoring and runtime code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from moonmind.schemas.manifest_models import SecretRef

DEFAULT_GIT_CONNECTION_REF = "repository-connection:git-default"
LEGACY_REPOSITORY_DECODER_VERSION = "moonmind.repository-legacy-history.v1"
REPOSITORY_CAPABILITY_UNKNOWN = "REPOSITORY_CAPABILITY_UNKNOWN"
REPOSITORY_CONNECTION_MISMATCH = "REPOSITORY_CONNECTION_MISMATCH"
REPOSITORY_CLIENT_MISMATCH = "REPOSITORY_CLIENT_MISMATCH"
REPOSITORY_CREDENTIAL_UNAVAILABLE = "REPOSITORY_CREDENTIAL_UNAVAILABLE"
REPOSITORY_REMOTE_TIP_MISMATCH = "REPOSITORY_REMOTE_TIP_MISMATCH"
_GITHUB_REPOSITORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RepositoryContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RepositoryName(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=2000)


class RepositoryBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=400)


class ResolvedRepositoryRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=2000)
    name: str = Field(min_length=1, max_length=2000)


class ResolvedRepositoryBranchRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    repository_id: str = Field(alias="repositoryId", min_length=1, max_length=2000)
    id: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=400)


class ResolvedWorkBranchRef(ResolvedRepositoryBranchRef):
    origin: Literal["generated", "selected", "review_mapping", "historical_branch"]


class GitRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["git_commit"]
    commit_sha: str = Field(alias="commitSha", min_length=7, max_length=200)


class LoreRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["lore_revision"]
    revision_signature: str = Field(
        alias="revisionSignature", min_length=1, max_length=1000
    )


class AuthoredGitRepositoryTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    provider: Literal["git"]
    connection_ref: str = Field(alias="connectionRef", min_length=1)
    repository: RepositoryName
    branch: RepositoryBranch
    revision: GitRevision | None = None


class AuthoredLoreRepositoryTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    provider: Literal["lore"]
    connection_ref: str = Field(alias="connectionRef", min_length=1)
    repository: RepositoryName
    branch: RepositoryBranch
    revision: LoreRevision | None = None


AuthoredRepositoryTarget = Annotated[
    AuthoredGitRepositoryTarget | AuthoredLoreRepositoryTarget,
    Field(discriminator="provider"),
]
_TARGET_ADAPTER = TypeAdapter(AuthoredRepositoryTarget)


class RepositoryClientPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    pinned_version: str = Field(alias="pinnedVersion", min_length=1)
    compatible_server_versions: tuple[str, ...] = Field(
        default=(), alias="compatibleServerVersions"
    )
    tool_bundle_ref: str = Field(alias="toolBundleRef", min_length=1)
    executable_sha256: str = Field(alias="executableSha256", min_length=1)


class RepositoryClientEvidence(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    tool_bundle_ref: str = Field(alias="toolBundleRef", min_length=1)
    client_version: str = Field(alias="clientVersion", min_length=1)
    executable_sha256: str = Field(alias="executableSha256", min_length=1)
    server_version: str | None = Field(None, alias="serverVersion")


class RepositoryProjectionPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    provider: Literal["github"]
    repository: str = Field(min_length=1, max_length=2000)
    authority: Literal["review_only"]
    status_source_ref: str = Field(alias="statusSourceRef", min_length=1)


class RepositoryMergeCoordinatorPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    endpoint_ref: str = Field(alias="endpointRef", min_length=1)
    policy_ref: str = Field(alias="policyRef", min_length=1)
    supported_protocol_version: str = Field(
        alias="supportedProtocolVersion", min_length=1
    )


class GitHubResolverCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["github_resolver"]


class SecretRefCredential(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    source: Literal["secret_ref"]
    credential_ref: SecretRef = Field(alias="credentialRef")


class TrustedNetworkDevelopmentCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["trusted_network_development"]


class PatSecretRefCredential(BaseModel):
    """PAT-backed credential: typed SecretRef + PAT subtype, no raw value."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    source: Literal["pat_secret_ref"]
    credential_ref: SecretRef = Field(alias="credentialRef")
    pat_subtype: Literal["fine_grained", "classic"] = Field(alias="patSubtype")


class GitHubAppCredential(BaseModel):
    """GitHub App installation configuration (discriminated variant)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    source: Literal["github_app"]
    app_ref: str = Field(alias="appRef", min_length=1)
    installation_id: str = Field(alias="installationId", min_length=1)


RepositoryCredential = Annotated[
    GitHubResolverCredential
    | SecretRefCredential
    | PatSecretRefCredential
    | GitHubAppCredential
    | TrustedNetworkDevelopmentCredential,
    Field(discriminator="source"),
]


class ResolvedRepositoryTarget(BaseModel):
    """Immutable repository identity observed at the pre-mutation boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.resolved-repository-target.v1"] = Field(
        alias="schemaVersion"
    )
    provider: Literal["git", "lore"]
    connection_ref: str = Field(alias="connectionRef", min_length=1)
    repository: ResolvedRepositoryRef
    prepared_revision: GitRevision | LoreRevision = Field(alias="preparedRevision")
    prepared_branch: ResolvedRepositoryBranchRef = Field(alias="preparedBranch")
    base_branch: ResolvedRepositoryBranchRef = Field(alias="baseBranch")
    work_branch: ResolvedWorkBranchRef | None = Field(None, alias="workBranch")
    remote_tip_expectation: dict[str, Any] = Field(alias="remoteTipExpectation")
    client_evidence: RepositoryClientEvidence = Field(alias="clientEvidence")
    compatible_server_versions: tuple[str, ...] = Field(
        default=(), alias="compatibleServerVersions"
    )
    authority: Literal["authoritative"] = "authoritative"
    projection: RepositoryProjectionPolicy | None = None


class RepositoryConnection(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.repository-connection.v1"] = Field(
        alias="schemaVersion"
    )
    id: str = Field(min_length=1)
    provider: Literal["git", "lore"]
    display_name: str = Field(alias="displayName", min_length=1)
    endpoint_ref: str = Field(alias="endpointRef", min_length=1)
    trust_bundle_ref: str | None = Field(None, alias="trustBundleRef", min_length=1)
    hosting_service: Literal["github", "generic_git", "lore"] | None = Field(
        None, alias="hostingService"
    )
    scope: Literal["system", "workspace"] = Field(default="system")
    workspace_id: str | None = Field(None, alias="workspaceId")
    lifecycle_status: Literal["active", "disabled"] = Field(
        default="active", alias="lifecycleStatus"
    )
    policy_revision: int = Field(default=1, alias="policyRevision", ge=1)
    credential_revision: int = Field(default=1, alias="credentialRevision", ge=1)
    allowed_repository_ids: tuple[str, ...] = Field(
        default=(), alias="allowedRepositoryIds"
    )
    allowed_operations: tuple[
        Literal[
            "read",
            "write",
            "branch_write",
            "lock",
            "review_request",
            "merge_request",
        ],
        ...,
    ] = Field(alias="allowedOperations")
    client_policy: RepositoryClientPolicy = Field(alias="clientPolicy")
    projection: RepositoryProjectionPolicy | None = None
    merge_coordinator: RepositoryMergeCoordinatorPolicy | None = Field(
        None, alias="mergeCoordinator"
    )
    credential: RepositoryCredential

    @model_validator(mode="after")
    def _validate_provider_policy(self) -> "RepositoryConnection":
        if (
            self.provider == "git"
            and self.credential.source == "trusted_network_development"
        ):
            raise ValueError("Git connections do not support trusted-network credentials")
        if self.provider == "lore" and self.credential.source in {
            "github_resolver",
            "pat_secret_ref",
            "github_app",
        }:
            raise ValueError(
                "Lore connections do not support GitHub credentials"
            )
        if self.provider == "git" and (
            self.projection is not None or self.merge_coordinator is not None
        ):
            raise ValueError("Lore projection and merge policy require provider=lore")
        if (
            self.merge_coordinator is not None
            and "merge_request" not in self.allowed_operations
        ):
            raise ValueError(
                "mergeCoordinator requires the merge_request operation"
            )
        if self.scope == "workspace" and not (self.workspace_id or "").strip():
            raise ValueError("workspace-scoped connections require workspaceId")
        if self.scope == "system" and self.workspace_id is not None:
            raise ValueError("system-scoped connections must not carry workspaceId")
        if self.hosting_service == "github" and self.provider != "git":
            raise ValueError("hostingService github requires provider=git")
        if self.hosting_service == "lore" and self.provider != "lore":
            raise ValueError("hostingService lore requires provider=lore")
        return self


def compile_repository_target(value: object) -> AuthoredRepositoryTarget:
    """Compile a UI draft, injecting only the well-known common Git connection."""

    if not isinstance(value, Mapping):
        raise RepositoryContractError(
            "REPOSITORY_TARGET_INVALID",
            "repository must be a provider-discriminated object",
        )
    draft = dict(value)
    repository = draft.get("repository")
    if isinstance(repository, Mapping):
        repository = dict(repository)
        name = repository.get("name")
        if isinstance(name, str):
            repository["name"] = name.strip()
        draft["repository"] = repository
    branch = draft.get("branch")
    if isinstance(branch, Mapping):
        branch = dict(branch)
        name = branch.get("name")
        if isinstance(name, str):
            branch["name"] = name.strip()
        draft["branch"] = branch
    if draft.get("provider") == "git" and not str(
        draft.get("connectionRef") or ""
    ).strip():
        draft["connectionRef"] = DEFAULT_GIT_CONNECTION_REF
    try:
        return _TARGET_ADAPTER.validate_python(draft)
    except ValueError as exc:
        raise RepositoryContractError("REPOSITORY_TARGET_INVALID", str(exc)) from exc


def repository_name_from_value(
    value: object,
    *,
    provider: Literal["git", "lore"] | None = None,
) -> str:
    """Project a legacy scalar or authored target to its repository name."""

    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, Mapping):
        return ""
    if provider is not None and value.get("provider") != provider:
        return ""
    repository = value.get("repository")
    if not isinstance(repository, Mapping):
        return ""
    name = repository.get("name")
    return name.strip() if isinstance(name, str) else ""


def github_repository_name_from_value(value: object) -> str:
    """Project supported GitHub repository forms to ``owner/repository``."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    direct = raw.rstrip("/").removesuffix(".git")
    if _GITHUB_REPOSITORY_NAME_PATTERN.fullmatch(direct):
        return direct

    if raw.startswith("git@github.com:"):
        candidate = raw.removeprefix("git@github.com:").rstrip("/")
        candidate = candidate.removesuffix(".git")
        return candidate if _GITHUB_REPOSITORY_NAME_PATTERN.fullmatch(candidate) else ""

    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return ""
    candidate = f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return candidate if _GITHUB_REPOSITORY_NAME_PATTERN.fullmatch(candidate) else ""


def repository_branch_from_value(value: object) -> str:
    """Project an authored target to its branch name."""

    if not isinstance(value, Mapping):
        return ""
    branch = value.get("branch")
    if not isinstance(branch, Mapping):
        return ""
    name = branch.get("name")
    return name.strip() if isinstance(name, str) else ""


def decode_legacy_repository_history_v1(
    repository: str, branch: str | None = None
) -> AuthoredGitRepositoryTarget:
    """Frozen decoder for already-recorded histories; never call for authoring."""

    return AuthoredGitRepositoryTarget(
        provider="git",
        connectionRef=DEFAULT_GIT_CONNECTION_REF,
        repository={"name": repository},
        branch={"name": branch or "main"},
    )


def derive_repository_capabilities(
    target: AuthoredRepositoryTarget,
    *,
    publish_mode: str,
    skill_capabilities: Sequence[object] = (),
    tool_capabilities: Sequence[object] = (),
) -> list[str]:
    required = ["lore" if target.provider == "lore" else "git", "repo.read"]
    if publish_mode == "branch":
        required.extend(("repo.write", "repo.branch.write"))
    elif publish_mode == "pr":
        required.extend(("repo.write", "repo.branch.write"))
        required.append("repo.review.request" if target.provider == "lore" else "gh")
    required.extend(str(item).strip().lower() for item in skill_capabilities)
    required.extend(str(item).strip().lower() for item in tool_capabilities)
    return list(dict.fromkeys(item for item in required if item))


def reconcile_default_git_connection(
    *,
    client_policy: RepositoryClientPolicy,
) -> RepositoryConnection:
    """Return the deployment-owned connection selecting the existing resolver."""

    return RepositoryConnection(
        schemaVersion="moonmind.repository-connection.v1",
        id=DEFAULT_GIT_CONNECTION_REF,
        provider="git",
        displayName="Default GitHub connection",
        endpointRef="https://github.com",
        allowedOperations=("read", "write", "branch_write", "review_request"),
        clientPolicy=client_policy,
        credential={"source": "github_resolver"},
    )


def persist_repository_connection(connection: RepositoryConnection, path: Path) -> None:
    """Atomically reconcile one deployment-owned connection record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(connection.model_dump(by_alias=True, mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_repository_connection(path: Path, connection_ref: str) -> RepositoryConnection:
    """Resolve a previously reconciled connection; never synthesize at launch."""

    try:
        connection = RepositoryConnection.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RepositoryContractError(
            "REPOSITORY_CONNECTION_UNAVAILABLE",
            f"deployment-owned repository connection {connection_ref!r} is unavailable",
        ) from exc
    if connection.id != connection_ref:
        raise RepositoryContractError(
            "REPOSITORY_CONNECTION_UNAVAILABLE",
            f"stored repository connection does not match {connection_ref!r}",
        )
    return connection


def materialize_resolved_repository_target(
    target: AuthoredRepositoryTarget,
    *,
    observed_revision: str,
    evidence: RepositoryClientEvidence,
    client_policy: RepositoryClientPolicy | None = None,
    publish_mode: str = "none",
    repository_id: str | None = None,
    branch_id: str | None = None,
    work_branch: str | None = None,
    work_branch_id: str | None = None,
    work_branch_origin: Literal[
        "generated", "selected", "review_mapping", "historical_branch"
    ] | None = None,
    projection: RepositoryProjectionPolicy | None = None,
) -> ResolvedRepositoryTarget:
    """Freeze exact repository and client observations for durable metadata."""

    revision: GitRevision | LoreRevision
    if target.provider == "git":
        revision = GitRevision(kind="git_commit", commitSha=observed_revision)
    else:
        revision = LoreRevision(kind="lore_revision", revisionSignature=observed_revision)
    resolved_repository_id = repository_id or target.repository.name
    resolved_branch_id = branch_id or (
        f"refs/heads/{target.branch.name}"
        if target.provider == "git"
        else target.branch.name
    )
    expected_revision: dict[str, Any] = {
        "provider": target.provider,
        "repositoryId": resolved_repository_id,
    }
    if target.provider == "git":
        expected_revision["commitSha"] = observed_revision
    else:
        expected_revision["revisionSignature"] = observed_revision
    if target.revision is not None:
        expected: dict[str, Any] = {"kind": "read_only"}
        resolved_work_branch = None
    elif work_branch_origin == "generated":
        expected = {"kind": "must_not_exist"}
        resolved_work_branch = ResolvedWorkBranchRef(
            repositoryId=resolved_repository_id,
            id=work_branch_id or work_branch or "",
            name=work_branch or "",
            origin="generated",
        )
    else:
        expected = {"kind": "must_equal", "revision": expected_revision}
        selected_work_branch = work_branch or (
            target.branch.name if publish_mode in {"branch", "pr"} else None
        )
        resolved_work_branch = (
            ResolvedWorkBranchRef(
                repositoryId=resolved_repository_id,
                id=work_branch_id or resolved_branch_id,
                name=selected_work_branch,
                origin=work_branch_origin or "selected",
            )
            if selected_work_branch
            else None
        )
    resolved_branch = ResolvedRepositoryBranchRef(
        repositoryId=resolved_repository_id,
        id=resolved_branch_id,
        name=target.branch.name,
    )
    return ResolvedRepositoryTarget(
        schemaVersion="moonmind.resolved-repository-target.v1",
        provider=target.provider,
        connectionRef=target.connection_ref,
        repository={"id": resolved_repository_id, "name": target.repository.name},
        preparedRevision=revision,
        preparedBranch=resolved_branch,
        baseBranch=resolved_branch,
        workBranch=resolved_work_branch,
        remoteTipExpectation=expected,
        clientEvidence=evidence,
        compatibleServerVersions=(
            client_policy.compatible_server_versions if client_policy else ()
        ),
        projection=projection,
    )


def validate_connection_and_client(
    target: AuthoredRepositoryTarget,
    connection: RepositoryConnection,
    evidence: RepositoryClientEvidence,
    *,
    operation: str,
) -> None:
    """Fail before mutation unless target, policy, allowlists and evidence agree."""

    if connection.id != target.connection_ref or connection.provider != target.provider:
        raise RepositoryContractError(
            REPOSITORY_CONNECTION_MISMATCH,
            "repository target and connection identity/provider do not match",
        )
    if operation not in connection.allowed_operations:
        raise RepositoryContractError(
            REPOSITORY_CONNECTION_MISMATCH,
            f"connection does not allow operation {operation!r}",
        )
    if (
        target.provider == "git"
        and connection.allowed_repository_ids
        and target.repository.name not in connection.allowed_repository_ids
    ):
        raise RepositoryContractError(
            REPOSITORY_CONNECTION_MISMATCH, "repository is not allowed by connection"
        )
    policy = connection.client_policy
    if (
        evidence.tool_bundle_ref != policy.tool_bundle_ref
        or evidence.client_version != policy.pinned_version
        or evidence.executable_sha256 != policy.executable_sha256
        or (
            policy.compatible_server_versions
            and evidence.server_version not in policy.compatible_server_versions
        )
    ):
        raise RepositoryContractError(
            REPOSITORY_CLIENT_MISMATCH,
            "observed repository client evidence does not match connection policy",
        )


SCOPED_CONNECTIONS_API_VERSION = "repository-connections.v1"
REPOSITORY_SNAPSHOT_PRODUCER = "repository-connection-store"
REPOSITORY_SNAPSHOT_SCHEMA_VERSION = "moonmind.repository-snapshot.v1"
REPOSITORY_ROUTE_AMBIGUOUS = "REPOSITORY_ROUTE_AMBIGUOUS"
REPOSITORY_ROUTE_UNSUPPORTED = "REPOSITORY_ROUTE_UNSUPPORTED"
REPOSITORY_SETUP_REQUIRED = "REPOSITORY_SETUP_REQUIRED"
REPOSITORY_CONFLICT = "REPOSITORY_CONFLICT"
REPOSITORY_DENIED = "REPOSITORY_DENIED"
REPOSITORY_STALE_SNAPSHOT = "REPOSITORY_STALE_SNAPSHOT"
REPOSITORY_UNAVAILABLE = "REPOSITORY_UNAVAILABLE"

# Consequential legacy semantic (confined to the historical file loader):
# ``allowed_repository_ids == ()`` in ``validate_connection_and_client`` means
# unrestricted at that boundary. New scoped admission (``is_repository_admitted``
# / ``RepositoryConnectionStore.resolve``) never honors that meaning: a scoped
# connection with zero verified assignments authorizes nothing.


def normalize_endpoint(value: str) -> str:
    """Normalize an endpoint for identity comparison (host-scoped)."""

    candidate = (value or "").strip().rstrip("/")
    if not candidate:
        raise RepositoryContractError(
            REPOSITORY_DENIED, "endpoint must be a non-empty approved value"
        )
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise RepositoryContractError(REPOSITORY_DENIED, "endpoint is not a valid URI") from exc
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{parsed.path.rstrip('/')}"
    return candidate.lower()


def normalize_scope(
    scope: str, workspace_id: str | None = None
) -> tuple[str, str | None]:
    """Normalize system/workspace scope; workspace requires an id."""

    normalized = (scope or "").strip().lower()
    if normalized not in {"system", "workspace"}:
        raise RepositoryContractError(REPOSITORY_DENIED, f"unknown scope {scope!r}")
    workspace = (workspace_id or "").strip() or None
    if normalized == "workspace" and workspace is None:
        raise RepositoryContractError(
            REPOSITORY_DENIED, "workspace scope requires a workspace id"
        )
    if normalized == "system" and workspace is not None:
        raise RepositoryContractError(
            REPOSITORY_DENIED, "system scope must not carry a workspace id"
        )
    return normalized, workspace


def normalize_capability_bundle(capabilities: Sequence[str]) -> tuple[str, ...]:
    """Normalize a requested role/capability set into one deterministic key."""

    cleaned = sorted({str(item).strip().lower() for item in capabilities if str(item).strip()})
    if not cleaned:
        raise RepositoryContractError(
            REPOSITORY_ROUTE_UNSUPPORTED, "capability bundle must not be empty"
        )
    return tuple(cleaned)


class RepositoryIdentity(BaseModel):
    """Endpoint-scoped verified repository identity.

    ``repository_id`` is the provider-assigned, endpoint-scoped identity.
    Mutable names are aliases/display only and never fabricated IDs.
    Generic Git uses a ``generic_git:`` canonical-remote identity, never a
    fabricated ``owner/repo`` GitHub ID.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    endpoint: str = Field(min_length=1)
    repository_id: str = Field(alias="repositoryId", min_length=1)
    display_name: str | None = Field(None, alias="displayName")

    @model_validator(mode="after")
    def _validate_identity(self) -> "RepositoryIdentity":
        if self.repository_id.startswith("owner/") or self.repository_id == "repo":
            raise ValueError("repositoryId must be a verified identity, not a placeholder")
        return self


class RepositoryAssignment(BaseModel):
    """One verified scoped assignment of a connection to a repository."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    connection_id: str = Field(alias="connectionId", min_length=1)
    endpoint: str = Field(min_length=1)
    repository_id: str = Field(alias="repositoryId", min_length=1)
    display_name: str | None = Field(None, alias="displayName")
    operations: tuple[str, ...] = Field(default=("read",))
    revision: int = Field(default=1, ge=1)
    verified: bool = True


class RepositoryRoute(BaseModel):
    """One transactional default route for a scope/repository/bundle."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    scope: Literal["system", "workspace"] = "system"
    workspace_id: str | None = Field(None, alias="workspaceId")
    repository_id: str | None = Field(None, alias="repositoryId")
    capabilities: tuple[str, ...] = ()
    connection_id: str = Field(alias="connectionId", min_length=1)
    is_default: bool = Field(default=True, alias="isDefault")
    policy_revision: int = Field(default=1, alias="policyRevision", ge=1)


class ScopedAuditRecord(BaseModel):
    """Metadata-only audit record; never carries secret bodies."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    request_id: str = Field(alias="requestId", min_length=1)
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    connection_id: str | None = Field(None, alias="connectionId")
    scope: str = ""
    detail: str = ""


class RepositorySnapshot(BaseModel):
    """Versioned read-only snapshot published atomically by the store."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.repository-snapshot.v1"] = Field(
        alias="schemaVersion"
    )
    producer: str = Field(min_length=1)
    revision: int = Field(ge=1)
    digest: str = Field(min_length=1)
    connections: tuple[RepositoryConnection, ...] = ()
    assignments: tuple[RepositoryAssignment, ...] = ()
    routes: tuple[RepositoryRoute, ...] = ()


def is_repository_admitted(
    connection: RepositoryConnection,
    assignments: Sequence[RepositoryAssignment],
    *,
    endpoint: str,
    repository_id: str,
    operation: str,
) -> bool:
    """New scoped admission: zero/unverified scope grants nothing.

    Unlike the historical ``validate_connection_and_client`` empty-means-
    unrestricted rule, an empty assignment set denies. Legacy behavior is
    confined to the file loader path.
    """

    if connection.lifecycle_status != "active":
        return False
    wanted_endpoint = normalize_endpoint(endpoint)
    wanted_repo = (repository_id or "").strip()
    if not wanted_repo:
        return False
    for assignment in assignments:
        if assignment.connection_id != connection.id or not assignment.verified:
            continue
        try:
            assigned_endpoint = normalize_endpoint(assignment.endpoint)
        except RepositoryContractError:
            continue
        if assigned_endpoint != wanted_endpoint:
            continue
        if assignment.repository_id != wanted_repo:
            continue
        if operation not in assignment.operations:
            continue
        if operation not in connection.allowed_operations:
            continue
        return True
    return False


def connection_api_dict(
    connection: RepositoryConnection,
    assignments: Sequence[RepositoryAssignment] = (),
) -> dict[str, Any]:
    """Restricted versioned API projection (metadata only, redacted refs)."""

    payload = connection.model_dump(by_alias=True, mode="json")
    credential = dict(payload.get("credential") or {})
    if "credentialRef" in credential:
        ref = dict(credential["credentialRef"])
        ref.pop("extra", None)
        credential["credentialRef"] = {"provider": ref.get("provider"), "key": "***"}
        payload["credential"] = credential
    payload["apiVersion"] = SCOPED_CONNECTIONS_API_VERSION
    payload["assignments"] = [
        assignment.model_dump(by_alias=True, mode="json")
        for assignment in assignments
        if assignment.connection_id == connection.id
    ]
    return payload


def scoped_api_schema() -> dict[str, Any]:
    """Generated-type surface for the restricted versioned API."""

    return {
        "apiVersion": SCOPED_CONNECTIONS_API_VERSION,
        "connectionSchema": RepositoryConnection.model_json_schema(by_alias=True),
        "assignmentSchema": RepositoryAssignment.model_json_schema(by_alias=True),
        "routeSchema": RepositoryRoute.model_json_schema(by_alias=True),
        "snapshotSchema": RepositorySnapshot.model_json_schema(by_alias=True),
    }


class RepositoryConnectionStore:
    """One database/service writer for connections, assignments and routes.

    SQLite-backed single writer. Every mutation runs in one ``BEGIN IMMEDIATE``
    transaction that also appends a metadata-only audit record and enforces the
    single-default uniqueness rule. Deployment JSON files are versioned
    read-only snapshots published via :meth:`publish_snapshot`; a stale
    filesystem record is never preferred when the database is unavailable.
    Credential persistence is metadata-only (typed SecretRefs / App refs);
    secret bodies are never loaded or stored here.
    """

    def __init__(self, path: Path | str) -> None:
        import sqlite3
        import threading

        self._sqlite3 = sqlite3
        self._lock = threading.Lock()
        self._path = Path(path)
        if str(path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS connections(
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    policy_revision INTEGER NOT NULL,
                    credential_revision INTEGER NOT NULL,
                    lifecycle TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    workspace_id TEXT,
                    deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS assignments(
                    connection_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(connection_id, endpoint, repository_id)
                );
                CREATE TABLE IF NOT EXISTS routes(
                    scope TEXT NOT NULL,
                    workspace_id TEXT,
                    repository_id TEXT,
                    capabilities_key TEXT NOT NULL,
                    connection_id TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 1,
                    policy_revision INTEGER NOT NULL,
                    PRIMARY KEY(scope, workspace_id, repository_id, capabilities_key)
                );
                CREATE TABLE IF NOT EXISTS requests(
                    request_id TEXT PRIMARY KEY,
                    result TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    connection_id TEXT,
                    scope TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- internal helpers -------------------------------------------------
    def _audit(
        self, *, request_id: str, actor: str, action: str,
        connection_id: str | None, scope: str, detail: str = "",
    ) -> None:
        self._db.execute(
            "INSERT INTO audit(request_id, actor, action, connection_id, scope, detail)"
            " VALUES(?,?,?,?,?,?)",
            (request_id, actor, action, connection_id, scope, detail[:2000]),
        )

    def _get_connection_row(self, connection_id: str) -> Any:
        return self._db.execute(
            "SELECT * FROM connections WHERE id=?", (connection_id,)
        ).fetchone()

    def _check_use_grant(self, *, use_granted: bool, principal: str) -> None:
        # SecretRef possession is not use authority: callers must pass an
        # explicit grant derived from system/workspace + principal-use policy.
        if not use_granted or not (principal or "").strip():
            raise RepositoryContractError(
                REPOSITORY_DENIED,
                "principal lacks explicit use grant for this connection",
            )

    def _load_connection(self, connection_id: str) -> RepositoryConnection:
        row = self._get_connection_row(connection_id)
        if row is None or row["deleted"]:
            raise RepositoryContractError(
                REPOSITORY_UNAVAILABLE, f"connection {connection_id!r} is unavailable"
            )
        return RepositoryConnection.model_validate_json(row["payload"])

    def _assignments_for(self, connection_id: str) -> list[RepositoryAssignment]:
        rows = self._db.execute(
            "SELECT payload FROM assignments WHERE connection_id=?", (connection_id,)
        ).fetchall()
        return [RepositoryAssignment.model_validate_json(row["payload"]) for row in rows]

    # -- writer API (each method is one atomic transaction) ----------------
    def create_connection(
        self,
        connection: RepositoryConnection,
        *,
        actor: str,
        request_id: str,
        principal: str = "",
        use_granted: bool = True,
    ) -> RepositoryConnection:
        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        scope, workspace = normalize_scope(connection.scope, connection.workspace_id)
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                saved = self._db.execute(
                    "SELECT result FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                return RepositoryConnection.model_validate_json(saved["result"])
            existing = self._get_connection_row(connection.id)
            if existing is not None:
                raise RepositoryContractError(
                    REPOSITORY_CONFLICT,
                    f"connection {connection.id!r} already exists; deleted ids are never reused",
                )
            self._db.execute(
                "INSERT INTO connections(id, payload, policy_revision,"
                " credential_revision, lifecycle, scope, workspace_id, deleted)"
                " VALUES(?,?,?,?,?,?,?,0)",
                (
                    connection.id,
                    connection.model_dump_json(by_alias=True),
                    connection.policy_revision,
                    connection.credential_revision,
                    connection.lifecycle_status,
                    scope,
                    workspace,
                ),
            )
            self._audit(
                request_id=request_id, actor=actor, action="connection.create",
                connection_id=connection.id, scope=scope,
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, connection.model_dump_json(by_alias=True)),
            )
            return connection

    def update_connection(
        self,
        connection_id: str,
        *,
        actor: str,
        request_id: str,
        principal: str = "",
        use_granted: bool = True,
        expected_policy_revision: int,
        display_name: str | None = None,
        endpoint_ref: str | None = None,
        validated_endpoint_change: bool = False,
        credential: RepositoryCredential | None = None,
        allowed_operations: Sequence[str] | None = None,
    ) -> RepositoryConnection:
        """Edit a connection with optimistic revision compare.

        Retargeting ``endpoint_ref`` (or the credential's endpoint/account)
        requires ``validated_endpoint_change=True`` through an explicit
        validated revision path; credentials are never forwarded to the
        proposed endpoint to discover safety.
        """

        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                saved = self._db.execute(
                    "SELECT result FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                return RepositoryConnection.model_validate_json(saved["result"])
            current = self._load_connection(connection_id)
            if current.policy_revision != expected_policy_revision:
                raise RepositoryContractError(
                    REPOSITORY_CONFLICT,
                    f"expected policy revision {expected_policy_revision},"
                    f" found {current.policy_revision}",
                )
            data = current.model_dump()
            if display_name is not None:
                data["display_name"] = display_name
            if allowed_operations is not None:
                data["allowed_operations"] = tuple(allowed_operations)
            credential_revision = current.credential_revision
            if endpoint_ref is not None and endpoint_ref != current.endpoint_ref:
                if not validated_endpoint_change:
                    raise RepositoryContractError(
                        REPOSITORY_DENIED,
                        "endpoint change requires an explicit validated revision path",
                    )
                data["endpoint_ref"] = endpoint_ref
            if credential is not None:
                raw = (
                    credential.model_dump()
                    if isinstance(credential, BaseModel)
                    else dict(credential)
                )
                data["credential"] = raw
                credential_revision = current.credential_revision + 1
            updated = RepositoryConnection.model_validate(
                {
                    **data,
                    "policy_revision": current.policy_revision + 1,
                    "credential_revision": credential_revision,
                }
            )
            self._db.execute(
                "UPDATE connections SET payload=?, policy_revision=?,"
                " credential_revision=? WHERE id=?",
                (
                    updated.model_dump_json(by_alias=True),
                    updated.policy_revision,
                    updated.credential_revision,
                    connection_id,
                ),
            )
            self._audit(
                request_id=request_id, actor=actor, action="connection.update",
                connection_id=connection_id, scope=current.scope,
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, updated.model_dump_json(by_alias=True)),
            )
            return updated

    def assign_repository(
        self,
        assignment: RepositoryAssignment,
        *,
        actor: str,
        request_id: str,
        principal: str = "",
        use_granted: bool = True,
    ) -> RepositoryAssignment:
        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        endpoint = normalize_endpoint(assignment.endpoint)
        stored = RepositoryAssignment(
            connectionId=assignment.connection_id,
            endpoint=endpoint,
            repositoryId=assignment.repository_id.strip(),
            displayName=assignment.display_name,
            operations=tuple(assignment.operations),
            revision=assignment.revision,
            verified=assignment.verified,
        )
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                saved = self._db.execute(
                    "SELECT result FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                return RepositoryAssignment.model_validate_json(saved["result"])
            connection = self._load_connection(stored.connection_id)
            if connection.lifecycle_status != "active":
                raise RepositoryContractError(
                    REPOSITORY_DENIED, "cannot assign to a disabled connection"
                )
            self._db.execute(
                "INSERT OR REPLACE INTO assignments(connection_id, endpoint,"
                " repository_id, payload, verified) VALUES(?,?,?,?,?)",
                (
                    stored.connection_id,
                    endpoint,
                    stored.repository_id,
                    stored.model_dump_json(by_alias=True),
                    1 if stored.verified else 0,
                ),
            )
            self._audit(
                request_id=request_id, actor=actor, action="assignment.upsert",
                connection_id=stored.connection_id, scope=connection.scope,
                detail=f"{endpoint} {stored.repository_id}",
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, stored.model_dump_json(by_alias=True)),
            )
            return stored

    def remove_assignment(
        self,
        *,
        connection_id: str,
        endpoint: str,
        repository_id: str,
        actor: str,
        request_id: str,
        principal: str = "",
        use_granted: bool = True,
    ) -> None:
        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        endpoint = normalize_endpoint(endpoint)
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                return
            self._load_connection(connection_id)
            self._db.execute(
                "DELETE FROM assignments WHERE connection_id=? AND endpoint=?"
                " AND repository_id=?",
                (connection_id, endpoint, repository_id.strip()),
            )
            # Never leave a dangling binding: drop defaults that named this
            # connection for the removed repository in the same transaction.
            self._db.execute(
                "DELETE FROM routes WHERE connection_id=? AND repository_id=?",
                (connection_id, repository_id.strip()),
            )
            self._audit(
                request_id=request_id, actor=actor, action="assignment.remove",
                connection_id=connection_id, scope="",
                detail=f"{endpoint} {repository_id.strip()}",
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, "{}"),
            )

    def set_default(
        self,
        *,
        scope: str,
        workspace_id: str | None,
        repository_id: str | None,
        capabilities: Sequence[str],
        connection_id: str,
        actor: str,
        request_id: str,
        principal: str = "",
        use_granted: bool = True,
        expected_policy_revision: int | None = None,
    ) -> RepositoryRoute:
        """Replace the default route transactionally (one valid snapshot or conflict)."""

        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        normalized_scope, workspace = normalize_scope(scope, workspace_id)
        bundle = normalize_capability_bundle(capabilities)
        repo = (repository_id or "").strip() or None
        key = ",".join(bundle)
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                saved = self._db.execute(
                    "SELECT result FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                return RepositoryRoute.model_validate_json(saved["result"])
            connection = self._load_connection(connection_id)
            if connection.lifecycle_status != "active":
                raise RepositoryContractError(
                    REPOSITORY_DENIED, "disabled connections cannot become default"
                )
            if expected_policy_revision is not None and (
                connection.policy_revision != expected_policy_revision
            ):
                raise RepositoryContractError(
                    REPOSITORY_CONFLICT,
                    f"expected policy revision {expected_policy_revision},"
                    f" found {connection.policy_revision}",
                )
            # Permission check precedes candidate enumeration/mutation: the
            # connection scope must admit the requesting principal scope.
            connection_scope, connection_workspace = normalize_scope(
                connection.scope, connection.workspace_id
            )
            if connection_scope == "workspace" and (
                normalized_scope != "workspace" or workspace != connection_workspace
            ):
                raise RepositoryContractError(
                    REPOSITORY_DENIED,
                    "workspace connection cannot serve another scope",
                )
            # Whole-bundle rule: one connection must satisfy the entire admitted
            # bundle (no per-subset defaults, no priority-rule engine), so a
            # read default and a write default cannot split one role's
            # credentials.
            self._require_bundle(connection, bundle)
            route = RepositoryRoute(
                scope=normalized_scope,  # type: ignore[arg-type]
                workspaceId=workspace,
                repositoryId=repo,
                capabilities=bundle,
                connectionId=connection_id,
                policyRevision=connection.policy_revision,
            )
            # Nullable-key behavior: SQLite UNIQUE treats NULLs as distinct,
            # which would allow duplicate defaults. Persist empty-string
            # sentinels so the primary key enforces one default per
            # (scope, workspace, repository, bundle).
            self._db.execute(
                "INSERT OR REPLACE INTO routes(scope, workspace_id, repository_id,"
                " capabilities_key, connection_id, is_default, policy_revision)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    normalized_scope, workspace or "", repo or "", key,
                    connection_id, 1, connection.policy_revision,
                ),
            )
            self._audit(
                request_id=request_id, actor=actor, action="route.set_default",
                connection_id=connection_id, scope=normalized_scope,
                detail=f"{repo or '<default>'} {key}",
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, route.model_dump_json(by_alias=True)),
            )
            return route

    def disable_connection(
        self, *, connection_id: str, actor: str, request_id: str,
        principal: str = "", use_granted: bool = True,
    ) -> RepositoryConnection:
        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                saved = self._db.execute(
                    "SELECT result FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                return RepositoryConnection.model_validate_json(saved["result"])
            current = self._load_connection(connection_id)
            data = current.model_dump()
            data["lifecycle_status"] = "disabled"
            data["policy_revision"] = current.policy_revision + 1
            updated = RepositoryConnection.model_validate(data)
            self._db.execute(
                "UPDATE connections SET payload=?, policy_revision=?, lifecycle=?"
                " WHERE id=?",
                (
                    updated.model_dump_json(by_alias=True),
                    updated.policy_revision,
                    "disabled",
                    connection_id,
                ),
            )
            # A disabled connection admits nothing: drop its defaults in the
            # same transaction so selection can never return a dangling binding.
            self._db.execute(
                "DELETE FROM routes WHERE connection_id=?", (connection_id,)
            )
            self._audit(
                request_id=request_id, actor=actor, action="connection.disable",
                connection_id=connection_id, scope=current.scope,
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, updated.model_dump_json(by_alias=True)),
            )
            return updated

    def delete_connection(
        self, *, connection_id: str, actor: str, request_id: str,
        principal: str = "", use_granted: bool = True,
    ) -> None:
        """Delete (tombstone): the id is never reused by new connections.

        Active cleanup is not broken: assignments/routes referencing the id
        remain visible as dangling-free historical rows that deny admission.
        """

        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                return
            current = self._load_connection(connection_id)
            self._db.execute(
                "UPDATE connections SET deleted=1 WHERE id=?", (connection_id,)
            )
            self._db.execute(
                "DELETE FROM routes WHERE connection_id=?", (connection_id,)
            )
            self._audit(
                request_id=request_id, actor=actor, action="connection.delete",
                connection_id=connection_id, scope=current.scope,
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, "{}"),
            )

    def reconcile_repository_rename(
        self, *, endpoint: str, old_repository_id: str, new_repository_id: str,
        actor: str, request_id: str, principal: str = "", use_granted: bool = True,
    ) -> int:
        """Reconcile a verified rename to the same stable assignment object."""

        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        endpoint = normalize_endpoint(endpoint)
        old_id = (old_repository_id or "").strip()
        new_id = (new_repository_id or "").strip()
        if not old_id or not new_id:
            raise RepositoryContractError(REPOSITORY_DENIED, "rename needs both ids")
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                return 0
            rows = self._db.execute(
                "SELECT connection_id, payload FROM assignments"
                " WHERE endpoint=? AND repository_id=?",
                (endpoint, old_id),
            ).fetchall()
            moved = 0
            for row in rows:
                assignment = RepositoryAssignment.model_validate_json(row["payload"])
                updated = RepositoryAssignment(
                    connectionId=assignment.connection_id,
                    endpoint=endpoint,
                    repositoryId=new_id,
                    displayName=old_id,
                    operations=tuple(assignment.operations),
                    revision=assignment.revision + 1,
                    verified=True,
                )
                self._db.execute(
                    "DELETE FROM assignments WHERE connection_id=? AND endpoint=?"
                    " AND repository_id=?",
                    (assignment.connection_id, endpoint, old_id),
                )
                self._db.execute(
                    "INSERT OR REPLACE INTO assignments(connection_id, endpoint,"
                    " repository_id, payload, verified) VALUES(?,?,?,?,1)",
                    (
                        assignment.connection_id, endpoint, new_id,
                        updated.model_dump_json(by_alias=True),
                    ),
                )
                moved += 1
            self._audit(
                request_id=request_id, actor=actor, action="repository.rename",
                connection_id=None, scope="",
                detail=f"{endpoint} {old_id}->{new_id} moved={moved}",
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, f'{{"moved": {moved}}}'),
            )
            return moved

    def transfer_repository_owner(
        self, *, endpoint: str, repository_id: str,
        actor: str, request_id: str, principal: str = "", use_granted: bool = True,
    ) -> int:
        """Owner transfer invalidates policy/capability evidence until reauthorized."""

        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        endpoint = normalize_endpoint(endpoint)
        repository_id = (repository_id or "").strip()
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                return 0
            rows = self._db.execute(
                "SELECT connection_id, payload FROM assignments"
                " WHERE endpoint=? AND repository_id=?",
                (endpoint, repository_id),
            ).fetchall()
            count = 0
            for row in rows:
                assignment = RepositoryAssignment.model_validate_json(row["payload"])
                updated = RepositoryAssignment(
                    connectionId=assignment.connection_id,
                    endpoint=endpoint,
                    repositoryId=repository_id,
                    displayName=assignment.display_name,
                    operations=tuple(assignment.operations),
                    revision=assignment.revision + 1,
                    verified=False,
                )
                self._db.execute(
                    "UPDATE assignments SET payload=?, verified=0"
                    " WHERE connection_id=? AND endpoint=? AND repository_id=?",
                    (
                        updated.model_dump_json(by_alias=True),
                        assignment.connection_id, endpoint, repository_id,
                    ),
                )
                count += 1
            self._audit(
                request_id=request_id, actor=actor, action="repository.transfer",
                connection_id=None, scope="",
                detail=f"{endpoint} {repository_id} invalidated={count}",
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, f'{{"invalidated": {count}}}'),
            )
            return count

    def reauthorize_assignment(
        self, *, connection_id: str, endpoint: str, repository_id: str,
        actor: str, request_id: str, principal: str = "", use_granted: bool = True,
    ) -> RepositoryAssignment:
        self._check_use_grant(use_granted=use_granted, principal=principal or actor)
        endpoint = normalize_endpoint(endpoint)
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM requests WHERE request_id=?", (request_id,)
            ).fetchone():
                saved = self._db.execute(
                    "SELECT result FROM requests WHERE request_id=?", (request_id,)
                ).fetchone()
                return RepositoryAssignment.model_validate_json(saved["result"])
            row = self._db.execute(
                "SELECT payload FROM assignments WHERE connection_id=?"
                " AND endpoint=? AND repository_id=?",
                (connection_id, endpoint, repository_id.strip()),
            ).fetchone()
            if row is None:
                raise RepositoryContractError(
                    REPOSITORY_SETUP_REQUIRED, "no such assignment to reauthorize"
                )
            assignment = RepositoryAssignment.model_validate_json(row["payload"])
            updated = RepositoryAssignment(
                connectionId=assignment.connection_id,
                endpoint=endpoint,
                repositoryId=assignment.repository_id,
                displayName=assignment.display_name,
                operations=tuple(assignment.operations),
                revision=assignment.revision + 1,
                verified=True,
            )
            self._db.execute(
                "UPDATE assignments SET payload=?, verified=1"
                " WHERE connection_id=? AND endpoint=? AND repository_id=?",
                (
                    updated.model_dump_json(by_alias=True),
                    connection_id, endpoint, assignment.repository_id,
                ),
            )
            self._audit(
                request_id=request_id, actor=actor, action="assignment.reauthorize",
                connection_id=connection_id, scope="",
            )
            self._db.execute(
                "INSERT INTO requests(request_id, result) VALUES(?,?)",
                (request_id, updated.model_dump_json(by_alias=True)),
            )
            return updated

    # -- read paths --------------------------------------------------------
    def resolve(
        self,
        *,
        scope: str,
        workspace_id: str | None,
        identity: RepositoryIdentity,
        capabilities: Sequence[str],
        principal: str,
        use_granted: bool,
        connection_ref: str | None = None,
    ) -> RepositoryConnection:
        """Deterministic routed/explicit selection for one declared role.

        Permission checks precede enumeration; missing/unverified scope is
        setup-required (never a wildcard); ambiguous candidates fail with a
        safe diagnostic instead of falling back to another connection.
        """

        self._check_use_grant(use_granted=use_granted, principal=principal)
        normalized_scope, workspace = normalize_scope(scope, workspace_id)
        bundle = normalize_capability_bundle(capabilities)
        endpoint = normalize_endpoint(identity.endpoint)
        required = _required_operations(bundle)
        with self._lock:
            if connection_ref is not None:
                connection = self._load_connection(connection_ref)
                assignments = self._assignments_for(connection.id)
                if not all(
                    is_repository_admitted(
                        connection, assignments, endpoint=endpoint,
                        repository_id=identity.repository_id, operation=operation,
                    )
                    for operation in required
                ):
                    raise RepositoryContractError(
                        REPOSITORY_DENIED,
                        f"explicit connection {connection_ref!r} does not admit"
                        f" {endpoint} {identity.repository_id}",
                    )
                self._require_bundle(connection, bundle)
                return connection
            rows = self._db.execute(
                "SELECT connection_id, payload, verified FROM assignments"
                " WHERE endpoint=? AND repository_id=?",
                (endpoint, identity.repository_id),
            ).fetchall()
            eligible: list[RepositoryConnection] = []
            for row in rows:
                if not row["verified"]:
                    continue
                try:
                    connection = self._load_connection(row["connection_id"])
                except RepositoryContractError:
                    continue
                if connection.lifecycle_status != "active":
                    continue
                connection_scope, connection_workspace = normalize_scope(
                    connection.scope, connection.workspace_id
                )
                if connection_scope == "workspace" and (
                    normalized_scope != "workspace"
                    or workspace != connection_workspace
                ):
                    continue
                assignments = self._assignments_for(connection.id)
                if not all(
                    is_repository_admitted(
                        connection, assignments, endpoint=endpoint,
                        repository_id=identity.repository_id, operation=operation,
                    )
                    for operation in required
                ):
                    continue
                try:
                    self._require_bundle(connection, bundle)
                except RepositoryContractError:
                    continue
                eligible.append(connection)
            if len(eligible) == 1:
                return eligible[0]
            if not eligible:
                # Single applicable default for the same scope/repository/bundle.
                default = self._find_default(
                    normalized_scope, workspace, endpoint,
                    identity.repository_id, bundle,
                )
                if default is not None:
                    return default
                raise RepositoryContractError(
                    REPOSITORY_SETUP_REQUIRED,
                    "no eligible route for the requested repository and capabilities",
                )
            raise RepositoryContractError(
                REPOSITORY_ROUTE_AMBIGUOUS,
                f"{len(eligible)} eligible connections; selection is ambiguous",
            )

    def _find_default(
        self, scope: str, workspace: str | None, endpoint: str,
        repository_id: str, bundle: tuple[str, ...],
    ) -> RepositoryConnection | None:
        key = ",".join(bundle)
        required = _required_operations(bundle)
        db_workspace = workspace or ""
        # Specific-repository default first, then the scope-wide default.
        for db_repo in (repository_id or "", ""):
            row = self._db.execute(
                "SELECT connection_id FROM routes WHERE scope=? AND"
                " workspace_id=? AND repository_id=? AND capabilities_key=?",
                (scope, db_workspace, db_repo, key),
            ).fetchone()
            if row is not None:
                try:
                    connection = self._load_connection(row["connection_id"])
                except RepositoryContractError:
                    continue
                if connection.lifecycle_status != "active":
                    continue
                assignments = self._assignments_for(connection.id)
                # Even a scope-wide default never admits a repository with
                # zero/unverified assignments: empty scope grants nothing.
                if all(
                    is_repository_admitted(
                        connection, assignments, endpoint=endpoint,
                        repository_id=repository_id, operation=operation,
                    )
                    for operation in required
                ):
                    try:
                        self._require_bundle(connection, bundle)
                    except RepositoryContractError:
                        continue
                    return connection
        return None

    @staticmethod
    def _require_bundle(connection: RepositoryConnection, bundle: tuple[str, ...]) -> None:
        # Either one connection satisfies the whole admitted bundle or
        # selection is explicitly ambiguous/unsupported: read+write defaults
        # must not split credentials within one role.
        _check_provider_token(connection, bundle)
        needed = _required_operations(bundle)
        if not needed.issubset(set(connection.allowed_operations)):
            raise RepositoryContractError(
                REPOSITORY_ROUTE_UNSUPPORTED,
                f"connection {connection.id!r} does not cover the bundle"
                f" {sorted(needed)}",
            )

    def audit_records(self, *, connection_id: str | None = None) -> list[ScopedAuditRecord]:
        with self._lock:
            if connection_id is None:
                rows = self._db.execute(
                    "SELECT request_id, actor, action, connection_id, scope, detail"
                    " FROM audit ORDER BY seq"
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT request_id, actor, action, connection_id, scope, detail"
                    " FROM audit WHERE connection_id=? ORDER BY seq",
                    (connection_id,),
                ).fetchall()
            return [
                ScopedAuditRecord(
                    requestId=row["request_id"], actor=row["actor"],
                    action=row["action"], connectionId=row["connection_id"],
                    scope=row["scope"], detail=row["detail"],
                )
                for row in rows
            ]

    def snapshot(self) -> RepositorySnapshot:
        """Build the current admitted snapshot with producer/revision/digest."""

        import hashlib

        with self._lock:
            connections = [
                RepositoryConnection.model_validate_json(row["payload"])
                for row in self._db.execute(
                    "SELECT payload FROM connections WHERE deleted=0 ORDER BY id"
                ).fetchall()
            ]
            assignments = [
                RepositoryAssignment.model_validate_json(row["payload"])
                for row in self._db.execute(
                    "SELECT payload FROM assignments"
                    " ORDER BY connection_id, endpoint, repository_id"
                ).fetchall()
            ]
            routes: list[RepositoryRoute] = []
            for row in self._db.execute(
                "SELECT scope, workspace_id, repository_id, capabilities_key,"
                " connection_id, policy_revision FROM routes"
                " ORDER BY scope, repository_id, capabilities_key"
            ).fetchall():
                bundle = tuple(row["capabilities_key"].split(",")) if row["capabilities_key"] else ()
                routes.append(
                    RepositoryRoute(
                        scope=row["scope"],  # type: ignore[arg-type]
                        workspaceId=row["workspace_id"] or None,
                        repositoryId=row["repository_id"] or None,
                        capabilities=bundle,
                        connectionId=row["connection_id"],
                        policyRevision=row["policy_revision"],
                    )
                )
            meta = self._db.execute(
                "SELECT value FROM meta WHERE key='snapshot_revision'"
            ).fetchone()
            revision = int(meta["value"]) + 1 if meta else 1
            canonical = json.dumps(
                {
                    "connections": [c.model_dump(by_alias=True, mode="json") for c in connections],
                    "assignments": [a.model_dump(by_alias=True, mode="json") for a in assignments],
                    "routes": [r.model_dump(by_alias=True, mode="json") for r in routes],
                },
                sort_keys=True,
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            return RepositorySnapshot(
                schemaVersion=REPOSITORY_SNAPSHOT_SCHEMA_VERSION,
                producer=REPOSITORY_SNAPSHOT_PRODUCER,
                revision=revision,
                digest=digest,
                connections=tuple(connections),
                assignments=tuple(assignments),
                routes=tuple(routes),
            )

    def publish_snapshot(self, path: Path) -> RepositorySnapshot:
        """Atomically publish a versioned read-only snapshot file."""

        snap = self.snapshot()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(snap.model_dump(by_alias=True, mode="json"), sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('snapshot_revision',?)",
                (str(snap.revision),),
            )
        return snap


def load_snapshot(path: Path, *, expected_digest: str | None = None) -> RepositorySnapshot:
    """Load a published snapshot; stale digests fail instead of being trusted."""

    import hashlib

    try:
        snap = RepositorySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RepositoryContractError(
            REPOSITORY_UNAVAILABLE, "repository snapshot is unavailable"
        ) from exc
    if snap.producer != REPOSITORY_SNAPSHOT_PRODUCER:
        raise RepositoryContractError(
            REPOSITORY_UNAVAILABLE, "snapshot has an unknown producer"
        )
    canonical = json.dumps(
        {
            "connections": [c.model_dump(by_alias=True, mode="json") for c in snap.connections],
            "assignments": [a.model_dump(by_alias=True, mode="json") for a in snap.assignments],
            "routes": [r.model_dump(by_alias=True, mode="json") for r in snap.routes],
        },
        sort_keys=True,
    )
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual != snap.digest:
        raise RepositoryContractError(
            REPOSITORY_UNAVAILABLE, "snapshot digest does not match its content"
        )
    if expected_digest is not None and expected_digest != snap.digest:
        raise RepositoryContractError(
            REPOSITORY_STALE_SNAPSHOT,
            "snapshot is stale for the requested revision",
        )
    return snap


_REPO_CAPABILITY_OPERATIONS = {
    "repo.read": "read",
    "repo.write": "write",
    "repo.branch.write": "branch_write",
    "repo.lock": "lock",
    "repo.review.request": "review_request",
    "gh": "review_request",
    "repo.merge.request": "merge_request",
}
_PROVIDER_CAPABILITY_TOKENS = frozenset({"git", "lore"})


def _required_operations(bundle: Sequence[str]) -> set[str]:
    """Map a requested role/capability bundle to required connection operations.

    Provider tokens (``git``/``lore``) constrain the connection provider and
    are checked separately. Anything else outside the known repository
    capability set is explicitly unsupported, never silently ignored.
    """

    operations: set[str] = set()
    for raw in bundle:
        token = str(raw).strip().lower()
        if not token or token in _PROVIDER_CAPABILITY_TOKENS:
            continue
        operation = _REPO_CAPABILITY_OPERATIONS.get(token)
        if operation is None:
            raise RepositoryContractError(
                REPOSITORY_ROUTE_UNSUPPORTED,
                f"capability {token!r} has no repository-route mapping",
            )
        operations.add(operation)
    if not operations:
        operations.add("read")
    return operations


def _check_provider_token(
    connection: RepositoryConnection, bundle: Sequence[str]
) -> None:
    for raw in bundle:
        token = str(raw).strip().lower()
        if token in _PROVIDER_CAPABILITY_TOKENS and token != connection.provider:
            raise RepositoryContractError(
                REPOSITORY_ROUTE_UNSUPPORTED,
                f"connection {connection.id!r} is provider"
                f" {connection.provider!r}, not {token!r}",
            )


def _capability_to_operation(bundle: Sequence[str]) -> str:
    operations = _required_operations(bundle)
    for preferred in ("write", "merge_request", "review_request", "lock", "read"):
        if preferred in operations:
            return preferred
    return "read"

ReadinessCheck = Callable[[Mapping[str, Any]], bool | Awaitable[bool]]
ConnectionResolver = Callable[
    [AuthoredRepositoryTarget], RepositoryConnection | Awaitable[RepositoryConnection]
]
ClientEvidenceResolver = Callable[
    [RepositoryConnection], RepositoryClientEvidence | Awaitable[RepositoryClientEvidence]
]
CredentialResolver = Callable[[str], object | Awaitable[object]]
RemoteTipVerifier = Callable[
    [AuthoredRepositoryTarget], bool | Awaitable[bool]
]


class CapabilityReadinessRegistry:
    """Fail-closed registry used immediately before repository mutation."""

    def __init__(self, runtime_owned_tokens: Sequence[str] = ()) -> None:
        self._checks: dict[str, ReadinessCheck] = {}
        self._runtime_owned = frozenset(runtime_owned_tokens)

    def register(self, token: str, check: ReadinessCheck) -> None:
        normalized = token.strip().lower()
        if not normalized or normalized in self._checks:
            raise ValueError(f"invalid or duplicate capability token {token!r}")
        self._checks[normalized] = check

    async def check(
        self, tokens: Sequence[str], context: Mapping[str, Any]
    ) -> None:
        for raw in tokens:
            token = str(raw).strip().lower()
            if token in self._runtime_owned:
                continue
            check = self._checks.get(token)
            if check is None:
                raise RepositoryContractError(
                    REPOSITORY_CAPABILITY_UNKNOWN,
                    f"required capability {token!r} has no readiness provider",
                )
            result = check(context)
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
            if not result:
                raise RepositoryContractError(
                    "REPOSITORY_CAPABILITY_UNREADY",
                    f"required capability {token!r} is not ready",
                )


async def resolve_default_git_credential(repository: str) -> object:
    """Invoke the canonical GitHub resolver selected by the default connection."""

    from moonmind.auth.github_credentials import resolve_github_credential

    return await resolve_github_credential(repo=repository)


async def _await_if_needed(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def ensure_repository_ready(
    target: AuthoredRepositoryTarget,
    *,
    publish_mode: str,
    operation: str,
    skill_capabilities: Sequence[object] = (),
    tool_capabilities: Sequence[object] = (),
    connection_resolver: ConnectionResolver,
    evidence_resolver: ClientEvidenceResolver,
    readiness_registry: CapabilityReadinessRegistry,
    credential_resolver: CredentialResolver = resolve_default_git_credential,
    remote_tip_verifier: RemoteTipVerifier | None = None,
) -> RepositoryConnection:
    """Resolve and validate all repository authority before any side effect.

    Callers must complete this composition boundary before workspace
    preparation, runtime launch, or repository Tool execution.
    """

    connection = await _await_if_needed(connection_resolver(target))
    evidence = await _await_if_needed(evidence_resolver(connection))
    validate_connection_and_client(target, connection, evidence, operation=operation)

    context = {
        "target": target,
        "connection": connection,
        "clientEvidence": evidence,
        "operation": operation,
        "publishMode": publish_mode,
    }
    required = derive_repository_capabilities(
        target,
        publish_mode=publish_mode,
        skill_capabilities=skill_capabilities,
        tool_capabilities=tool_capabilities,
    )
    await readiness_registry.check(required, context)

    if connection.credential.source == "github_resolver":
        credential = await _await_if_needed(
            credential_resolver(target.repository.name)
        )
        if not bool(getattr(credential, "resolved", False)):
            safe_summary = str(
                getattr(credential, "safe_summary", "")
                or "GitHub credential resolution returned no usable credential."
            )
            raise RepositoryContractError(
                REPOSITORY_CREDENTIAL_UNAVAILABLE,
                safe_summary,
            )

    if operation != "read":
        if remote_tip_verifier is None:
            raise RepositoryContractError(
                REPOSITORY_REMOTE_TIP_MISMATCH,
                "repository mutation requires an observed remote-tip comparison",
            )
        if not await _await_if_needed(remote_tip_verifier(target)):
            raise RepositoryContractError(
                REPOSITORY_REMOTE_TIP_MISMATCH,
                "observed remote tip does not match the expected provider revision",
            )
    return connection


__all__ = [
    "AuthoredGitRepositoryTarget",
    "AuthoredLoreRepositoryTarget",
    "AuthoredRepositoryTarget",
    "CapabilityReadinessRegistry",
    "DEFAULT_GIT_CONNECTION_REF",
    "LEGACY_REPOSITORY_DECODER_VERSION",
    "REPOSITORY_CONFLICT",
    "REPOSITORY_DENIED",
    "REPOSITORY_ROUTE_AMBIGUOUS",
    "REPOSITORY_ROUTE_UNSUPPORTED",
    "REPOSITORY_SETUP_REQUIRED",
    "REPOSITORY_SNAPSHOT_PRODUCER",
    "REPOSITORY_SNAPSHOT_SCHEMA_VERSION",
    "REPOSITORY_STALE_SNAPSHOT",
    "REPOSITORY_UNAVAILABLE",
    "RepositoryAssignment",
    "RepositoryClientEvidence",
    "RepositoryClientPolicy",
    "RepositoryConnection",
    "RepositoryConnectionStore",
    "RepositoryContractError",
    "RepositoryCredential",
    "RepositoryIdentity",
    "RepositoryRoute",
    "RepositorySnapshot",
    "ResolvedRepositoryTarget",
    "PatSecretRefCredential",
    "GitHubAppCredential",
    "SCOPED_CONNECTIONS_API_VERSION",
    "ScopedAuditRecord",
    "compile_repository_target",
    "connection_api_dict",
    "decode_legacy_repository_history_v1",
    "derive_repository_capabilities",
    "ensure_repository_ready",
    "github_repository_name_from_value",
    "is_repository_admitted",
    "load_repository_connection",
    "load_snapshot",
    "materialize_resolved_repository_target",
    "normalize_capability_bundle",
    "normalize_endpoint",
    "normalize_scope",
    "persist_repository_connection",
    "repository_branch_from_value",
    "repository_name_from_value",
    "reconcile_default_git_connection",
    "resolve_default_git_credential",
    "scoped_api_schema",
    "validate_connection_and_client",
]
