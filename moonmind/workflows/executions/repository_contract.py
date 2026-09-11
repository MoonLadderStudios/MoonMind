"""Provider-aware repository compilation and readiness boundary.

MM-1219 replaces repository-shaped authoring aliases with one discriminated
target.  This module owns compilation, connection reconciliation, capability
derivation, and the pre-mutation readiness check so those decisions cannot
drift across authoring and runtime code.

MoonLadderStudios/MoonMind#4005 (slice 1) extends this same contract with
scoped routing semantics:

* Canonical owner: the database/service writer
  (``api_service.services.repository_connections``) is the single writable
  authority for connections, assignments, and route defaults.  Deployment
  JSON files are versioned read-only snapshots or classified legacy input,
  never an independently editable fallback policy.
* Snapshot lifecycle: snapshots carry producer, revision, and digest, are
  published atomically, and runtime consumers must reject stale snapshots
  rather than prefer a filesystem record because the database is temporarily
  unavailable.  The coordinated legacy cutover is owned by #4023.
* Legacy boundary: ``RepositoryConnection.allowed_repository_ids == ()``
  historically means *unrestricted* at the ``validate_connection_and_client``
  boundary (not "no assignments"), and the legacy check compares the authored
  repository *name* even though the field is called IDs.  New scoped admission
  must never inherit that meaning or relabel names as verified provider IDs;
  it lives only in the historical loader/validator below.
* No parallel domain: scoped fields extend ``RepositoryConnection`` in
  place; assignments/routes are supporting types, not a second connection
  identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

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


class SecretRef(BaseModel):
    """Generic provider/key secret locator for repository credentials.

    MoonLadderStudios/MoonMind#4192: relocated here from the retired native
    Manifest schema module (``moonmind.schemas.manifest_models``). The
    repository credential contract is generic workflow functionality and
    does not depend on the removed Manifest product.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    key: str
    extra: dict[str, Any] = Field(default_factory=dict)


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


class GitHubAppCredential(BaseModel):
    """Discriminated GitHub App installation configuration (#4005).

    PATs use typed ``SecretRef`` (``SecretRefCredential``); App configuration
    is a distinct discriminated variant carrying only references (definition
    and installation identity), never raw token material.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    source: Literal["github_app"]
    app_ref: str = Field(alias="appRef", min_length=1)
    installation_ref: str = Field(alias="installationRef", min_length=1)


RepositoryCredential = Annotated[
    GitHubResolverCredential
    | SecretRefCredential
    | TrustedNetworkDevelopmentCredential
    | GitHubAppCredential,
    Field(discriminator="source"),
]


class ConnectionOwnershipPolicy(BaseModel):
    """System/workspace ownership and principal-use policy (#4005).

    ``scope_type="system"`` normalizes ``scope_ref`` to ``None``; workspace
    scope requires an explicit ``scope_ref``.  Knowing a ``SecretRef`` never
    grants use authority; callers must pass an independent authorization
    decision (see ``authorize_connection_use``).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    owner_ref: str = Field(alias="ownerRef", min_length=1)
    scope_type: Literal["system", "workspace"] = Field(alias="scopeType")
    scope_ref: str | None = Field(None, alias="scopeRef")
    allowed_principal_refs: tuple[str, ...] = Field(
        default=(), alias="allowedPrincipalRefs"
    )

    @model_validator(mode="after")
    def _validate_scope(self) -> "ConnectionOwnershipPolicy":
        if self.scope_type == "system" and self.scope_ref is not None:
            raise ValueError("system scope must not carry a scope_ref")
        if self.scope_type == "workspace" and not (self.scope_ref or "").strip():
            raise ValueError("workspace scope requires a scope_ref")
        return self


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
    # --- #4005 scoped-persistence extensions (optional so historical JSON
    # still validates; new writes must populate them via
    # validate_scoped_connection_for_write). ---
    lifecycle: Literal["active", "disabled", "deleted"] = "active"
    policy_revision: int = Field(default=1, alias="policyRevision", ge=1)
    credential_revision: int = Field(default=1, alias="credentialRevision", ge=1)
    ownership: ConnectionOwnershipPolicy | None = None
    hosting_service: Literal["github", "generic_git", "lore"] | None = Field(
        None, alias="hostingService"
    )

    @model_validator(mode="after")
    def _validate_provider_policy(self) -> "RepositoryConnection":
        if (
            self.provider == "git"
            and self.credential.source == "trusted_network_development"
        ):
            raise ValueError("Git connections do not support trusted-network credentials")
        if self.provider == "lore" and self.credential.source in {
            "github_resolver",
            "github_app",
        }:
            raise ValueError(
                "Lore connections do not support GitHub resolver/App credentials"
            )
        if self.provider == "git" and self.credential.source not in {
            "github_resolver",
            "secret_ref",
            "github_app",
        }:
            raise ValueError("Git connections require a GitHub-backed credential")
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


# ---------------------------------------------------------------------------
# #4005 scoped persistence and transactional routing (same contract domain).
#
# Everything below extends RepositoryConnection in place.  There is no
# parallel connection/target domain: new
# writes populate the optional scoped fields on RepositoryConnection and are
# checked by validate_scoped_connection_for_write(); assignments/routes are
# supporting types, not a second connection identity.
# ---------------------------------------------------------------------------

REPOSITORY_SETUP_REQUIRED = "REPOSITORY_SETUP_REQUIRED"
REPOSITORY_ROUTE_AMBIGUOUS = "REPOSITORY_ROUTE_AMBIGUOUS"
REPOSITORY_ROUTE_UNSUPPORTED = "REPOSITORY_ROUTE_UNSUPPORTED"
REPOSITORY_ROUTE_CONFLICT = "REPOSITORY_ROUTE_CONFLICT"
REPOSITORY_DENIED = "REPOSITORY_DENIED"
REPOSITORY_STALE_SNAPSHOT = "REPOSITORY_STALE_SNAPSHOT"
REPOSITORY_POLICY_CONFLICT = "REPOSITORY_POLICY_CONFLICT"
REPOSITORY_ID_REUSE = "REPOSITORY_ID_REUSE"
REPOSITORY_ENDPOINT_RETARGET = "REPOSITORY_ENDPOINT_RETARGET"
REPOSITORY_TRANSFER_REAUTHORIZATION = "REPOSITORY_TRANSFER_REAUTHORIZATION"

CONNECTION_SNAPSHOT_SCHEMA_VERSION = "moonmind.repository-connection-snapshot.v1"
CONNECTION_SNAPSHOT_PRODUCER = "repository-connection-service.v1"


class RepositoryRouteError(ValueError):
    """Stable scoped-routing failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RepositoryIdentity(BaseModel):
    """Endpoint-scoped verified repository identity (#4005 impl-3).

    Exactly one of ``provider_repo_id`` (endpoint-scoped verified identity
    such as a GitHub repository ID) or ``canonical_remote`` (normalized
    endpoint/remote for generic Git without a hosting-service ID) is set.
    ``display_name`` is a mutable alias for display/lookup only and must
    never be fabricated into a provider ID.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    endpoint: str = Field(min_length=1)
    provider_repo_id: str | None = Field(None, alias="providerRepoId")
    canonical_remote: str | None = Field(None, alias="canonicalRemote")
    display_name: str = Field(alias="displayName", min_length=1)

    @model_validator(mode="after")
    def _validate_identity(self) -> "RepositoryIdentity":
        has_id = bool((self.provider_repo_id or "").strip())
        has_remote = bool((self.canonical_remote or "").strip())
        if has_id == has_remote:
            raise ValueError(
                "exactly one of providerRepoId or canonicalRemote must be set"
            )
        return self

    def route_id(self) -> str:
        """Stable routing discriminator (never the display alias)."""
        if (self.provider_repo_id or "").strip():
            return f"id:{normalize_endpoint(self.endpoint)}#{self.provider_repo_id.strip()}"
        return f"remote:{normalize_endpoint(self.endpoint)}#{self.canonical_remote.strip()}"


class RepositoryAssignment(BaseModel):
    """One scoped grant of a connection to a repository identity (#4005)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    connection_id: str = Field(alias="connectionId", min_length=1)
    identity: RepositoryIdentity
    operations: tuple[str, ...] = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    verified: bool = True
    broad_rule: bool = Field(default=False, alias="broadRule")


class ScopedRouteCandidate(BaseModel):
    """One eligible (connection, assignment) pair presented to selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    connection: RepositoryConnection
    assignment: RepositoryAssignment


class ConnectionChangeRequest(BaseModel):
    """Versioned mutation envelope with stable request identity (#4005 impl-8)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.repository-connection-change.v1"] = Field(
        alias="schemaVersion"
    )
    request_id: str = Field(alias="requestId", min_length=1, max_length=256)
    expected_policy_revision: int | None = Field(
        None, alias="expectedPolicyRevision", ge=1
    )
    actor_ref: str = Field(alias="actorRef", min_length=1)
    scope_type: Literal["system", "workspace"] = Field(alias="scopeType")
    scope_ref: str | None = Field(None, alias="scopeRef")


class ConnectionAuditRecord(BaseModel):
    """Metadata-only audit record sharing the mutation transaction (#4005)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.repository-connection-audit.v1"] = Field(
        alias="schemaVersion"
    )
    request_id: str = Field(alias="requestId", min_length=1)
    actor_ref: str = Field(alias="actorRef", min_length=1)
    action: str = Field(min_length=1)
    connection_id: str = Field(alias="connectionId", min_length=1)
    scope_type: str = Field(alias="scopeType", min_length=1)
    scope_ref: str | None = Field(None, alias="scopeRef")
    policy_revision: int | None = Field(None, alias="policyRevision", ge=1)
    observed_at: str = Field(alias="observedAt", min_length=1)


class RepositoryConnectionSnapshot(BaseModel):
    """Versioned read-only snapshot published atomically by the DB writer."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.repository-connection-snapshot.v1"] = Field(
        alias="schemaVersion"
    )
    producer: str = Field(min_length=1)
    revision: int = Field(ge=1)
    digest: str = Field(min_length=1)
    produced_at: str = Field(alias="producedAt", min_length=1)
    connections: tuple[RepositoryConnection, ...] = ()


def normalize_endpoint(endpoint: str) -> str:
    """Normalize an endpoint for routing/identity comparison."""

    raw = (endpoint or "").strip()
    if not raw:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "empty endpoint")
    if "@" in raw.split("/")[0] and "://" not in raw:
        raise RepositoryRouteError(REPOSITORY_DENIED, "endpoint must not embed credentials")
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "unparseable endpoint") from exc
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https", "lore"} or not host:
        # Allow lore-endpoint logical refs (e.g. "lore-endpoint:tactics").
        if raw.startswith("lore-endpoint:") or raw.startswith("trust-bundle:"):
            return raw.lower()
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "unsupported endpoint")
    if parsed.username or parsed.password:
        raise RepositoryRouteError(REPOSITORY_DENIED, "endpoint must not embed credentials")
    port = f":{parsed.port}" if parsed.port not in (None, 80, 443) else ""
    path = parsed.path.rstrip("/")
    return f"{scheme}://{host}{port}{path}".rstrip("/")


def normalize_scope(
    scope_type: str, scope_ref: str | None
) -> tuple[str, str | None]:
    """Normalize system/workspace scope (system always carries None)."""

    normalized_type = (scope_type or "").strip().lower()
    if normalized_type not in {"system", "workspace"}:
        raise RepositoryRouteError(REPOSITORY_DENIED, "unknown scope type")
    if normalized_type == "system":
        return ("system", None)
    ref = (scope_ref or "").strip()
    if not ref:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "workspace scope needs a ref")
    return ("workspace", ref)


def is_legacy_unrestricted_connection(connection: RepositoryConnection) -> bool:
    """Report the historical empty-means-unrestricted semantic.

    ``allowed_repository_ids == ()`` is *unrestricted* at the
    ``validate_connection_and_client`` boundary.  New scoped admission must
    never inherit this meaning; it is preserved only by the historical
    loader/validator.
    """

    return len(connection.allowed_repository_ids) == 0


def validate_scoped_connection_for_write(connection: RepositoryConnection) -> None:
    """Require new-write scoped fields without creating a parallel domain."""

    if connection.lifecycle == "deleted":
        raise RepositoryRouteError(REPOSITORY_DENIED, "deleted connections are not writable")
    if connection.ownership is None:
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED, "scoped connections require ownership"
        )
    normalize_scope(connection.ownership.scope_type, connection.ownership.scope_ref)
    normalize_endpoint(connection.endpoint_ref)
    if connection.hosting_service is None:
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED, "scoped connections require hostingService"
        )
    if connection.provider == "git" and connection.hosting_service == "lore":
        raise RepositoryRouteError(REPOSITORY_DENIED, "git provider cannot use lore hosting")
    if connection.provider == "lore" and connection.hosting_service == "github":
        raise RepositoryRouteError(REPOSITORY_DENIED, "lore provider cannot use github hosting")
    # Credential revision, connection-policy revision, and concrete issuance
    # are distinct: both revisions must advance independently and neither may
    # be zero.  Secret bodies are never persisted here (metadata-only: only
    # SecretRef provider/key or App refs cross this boundary).
    if connection.policy_revision < 1 or connection.credential_revision < 1:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "invalid revision")
    if connection.credential.source == "secret_ref":
        ref = connection.credential.credential_ref
        if not ref.provider.strip() or not ref.key.strip():
            raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "invalid SecretRef")


def route_key_for(
    *,
    scope_type: str,
    scope_ref: str | None,
    identity: RepositoryIdentity,
    capability_bundle: Sequence[str],
) -> str:
    """Define the route key once (shared with #4007 selection)."""

    scope_t, scope_n = normalize_scope(scope_type, scope_ref)
    bundle = ",".join(sorted({str(op).strip().lower() for op in capability_bundle if str(op).strip()}))
    if not bundle:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "empty capability bundle")
    scope_part = scope_t if scope_n is None else f"{scope_t}:{scope_n}"
    return f"{scope_part}|{identity.route_id()}|{bundle}"


def default_scope_for(
    scope_type: str, scope_ref: str | None
) -> tuple[str, str | None]:
    """Return the normalized default scope for route/default uniqueness."""

    return normalize_scope(scope_type, scope_ref)


def authorize_connection_use(
    *,
    principal_ref: str,
    principal_scope: tuple[str, str | None],
    connection: RepositoryConnection,
    action: str,
    has_secret_possession: bool = False,
) -> None:
    """Enforce system/workspace + principal-use authorization.

    ``has_secret_possession`` (knowing a SecretRef) never grants use
    authority; it is accepted only to document that the check was considered
    and ignored.
    """

    _ = has_secret_possession  # possession is explicitly not authority
    if connection.lifecycle != "active":
        raise RepositoryRouteError(REPOSITORY_DENIED, "connection is not active")
    if connection.ownership is None:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection has no ownership")
    conn_scope = normalize_scope(
        connection.ownership.scope_type, connection.ownership.scope_ref
    )
    p_scope_t, p_scope_n = normalize_scope(principal_scope[0], principal_scope[1])
    if conn_scope[0] == "system":
        if p_scope_t not in {"system", "workspace"}:
            raise RepositoryRouteError(REPOSITORY_DENIED, "scope not admitted")
    elif conn_scope != (p_scope_t, p_scope_n):
        raise RepositoryRouteError(REPOSITORY_DENIED, "workspace scope mismatch")
    allowed = {p.strip() for p in connection.ownership.allowed_principal_refs if p.strip()}
    if allowed and principal_ref.strip() not in allowed:
        raise RepositoryRouteError(REPOSITORY_DENIED, "principal is not admitted")
    if not principal_ref.strip() or not action.strip():
        raise RepositoryRouteError(REPOSITORY_DENIED, "principal/action required")


def admit_scoped_route(
    *,
    identity: RepositoryIdentity,
    requested_operations: Sequence[str],
    candidates: Sequence[ScopedRouteCandidate],
    principal_ref: str,
    principal_scope: tuple[str, str | None],
) -> ScopedRouteCandidate:
    """Deterministically select one route for the whole admitted bundle.

    Permission checks precede candidate enumeration; exactly one connection
    must satisfy the entire requested bundle or selection fails as ambiguous
    / unsupported / setup-required.  A read default and a write default must
    not split one role across credentials, and callers must never substitute
    another connection after the selected route fails.
    """

    requested = tuple(
        dict.fromkeys(
            str(op).strip().lower() for op in requested_operations if str(op).strip()
        )
    )
    if not requested:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "empty capability bundle")
    if not candidates:
        # New scoped connections with zero assignments authorize nothing;
        # missing/unverified scope is setup-required, never a wildcard.
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED, "no scoped assignments; scope setup required"
        )
    eligible: list[ScopedRouteCandidate] = []
    for candidate in candidates:
        connection = candidate.connection
        assignment = candidate.assignment
        # Permission check precedes enumeration for every candidate.
        authorize_connection_use(
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            connection=connection,
            action="use",
        )
        if connection.lifecycle != "active":
            continue
        if connection.id != assignment.connection_id:
            continue
        if not assignment.verified:
            continue
        if assignment.broad_rule:
            # A deliberately broad rule needs explicit permission and
            # representation; an unadorned broad flag is unsupported here.
            raise RepositoryRouteError(
                REPOSITORY_ROUTE_UNSUPPORTED, "broad deployment rules need explicit grant"
            )
        if assignment.identity.route_id() != identity.route_id():
            continue
        if any(op not in assignment.operations for op in requested):
            continue
        if any(op not in connection.allowed_operations for op in requested):
            continue
        eligible.append(candidate)
    if not eligible:
        raise RepositoryRouteError(
            REPOSITORY_SETUP_REQUIRED,
            "no eligible scoped route; scope setup required",
        )
    distinct_connections = {c.connection.id for c in eligible}
    if len(distinct_connections) > 1:
        raise RepositoryRouteError(
            REPOSITORY_ROUTE_AMBIGUOUS,
            "multiple connections satisfy the bundle; declare one explicitly",
        )
    # One connection satisfies the whole bundle: deterministic, no subset
    # explosion, no priority-rule engine, no credential split within the role.
    return eligible[0]


def admit_legacy_free_connection(*args: Any, **kwargs: Any) -> None:
    """Refuse to admit legacy empty-means-unrestricted scope as new authority.

    Historical unrestricted-empty behavior cannot enter new admission through
    a legacy serializer: new admission must use ``admit_scoped_route`` with
    verified assignments.
    """

    raise RepositoryRouteError(
        REPOSITORY_SETUP_REQUIRED,
        "legacy unrestricted-empty scope is not admissible; use scoped assignments",
    )


def reconcile_verified_rename(
    assignment: RepositoryAssignment,
    *,
    verified_provider_repo_id: str,
    new_display_name: str,
) -> RepositoryAssignment:
    """Reconcile a verified rename to the same stable object (alias update)."""

    if assignment.identity.provider_repo_id != verified_provider_repo_id:
        raise RepositoryRouteError(
            REPOSITORY_DENIED, "rename must keep the verified provider identity"
        )
    if not new_display_name.strip():
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "rename needs a display name")
    return assignment.model_copy(
        update={
            "identity": assignment.identity.model_copy(
                update={"display_name": new_display_name.strip()}
            )
        }
    )


def invalidate_on_owner_transfer(
    assignment: RepositoryAssignment, *, new_owner_ref: str
) -> RepositoryAssignment:
    """Invalidate policy/capability evidence until reauthorized after transfer."""

    if not new_owner_ref.strip():
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "transfer needs an owner")
    # Transfer invalidates evidence: the assignment becomes unverified until
    # an explicit reauthorization path re-verifies it.
    return assignment.model_copy(update={"verified": False})


def validate_endpoint_retarget(
    *,
    current_endpoint: str,
    proposed_endpoint: str,
    explicit_revision_path: bool,
) -> None:
    """Require an explicit validated revision for endpoint/account retargets.

    Retargeting an existing credential to a different endpoint/account is not
    a cosmetic label edit.  Credentials must never be forwarded to the
    proposed endpoint merely to discover whether it is safe.
    """

    if normalize_endpoint(current_endpoint) == normalize_endpoint(proposed_endpoint):
        return
    if not explicit_revision_path:
        raise RepositoryRouteError(
            REPOSITORY_ENDPOINT_RETARGET,
            "endpoint retarget requires an explicit validated revision/new connection",
        )


def build_connection_audit_record(
    *,
    actor_ref: str,
    request_id: str,
    action: str,
    connection: RepositoryConnection,
) -> ConnectionAuditRecord:
    """Build a metadata-only audit record (no secret bodies)."""

    if connection.ownership is None:
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection has no ownership")
    if not request_id.strip():
        raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "stable request identity required")
    return ConnectionAuditRecord(
        schemaVersion="moonmind.repository-connection-audit.v1",
        requestId=request_id.strip(),
        actorRef=actor_ref.strip(),
        action=action.strip(),
        connectionId=connection.id,
        scopeType=connection.ownership.scope_type,
        scopeRef=connection.ownership.scope_ref,
        policyRevision=connection.policy_revision,
        observedAt=datetime.now(timezone.utc).isoformat(),
    )


def route_diagnostic(code: str, *, authorized_for_details: bool) -> str:
    """Render safe conflict/denial diagnostics without metadata leakage."""

    if authorized_for_details:
        return code
    if code in {REPOSITORY_DENIED, REPOSITORY_SETUP_REQUIRED}:
        return REPOSITORY_DENIED
    return code


def publish_connection_snapshot(
    connections: Sequence[RepositoryConnection], path: Path, *, revision: int
) -> RepositoryConnectionSnapshot:
    """Atomically publish a versioned read-only snapshot (DB writer only)."""

    payload = [c.model_dump(by_alias=True, mode="json") for c in connections]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    snapshot = RepositoryConnectionSnapshot(
        schemaVersion=CONNECTION_SNAPSHOT_SCHEMA_VERSION,
        producer=CONNECTION_SNAPSHOT_PRODUCER,
        revision=revision,
        digest=digest,
        producedAt=datetime.now(timezone.utc).isoformat(),
        connections=tuple(connections),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(snapshot.model_dump(by_alias=True, mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
    return snapshot


def load_connection_snapshot(
    path: Path, *, minimum_revision: int = 1, allow_stale: bool = False
) -> RepositoryConnectionSnapshot:
    """Load a snapshot; fail on stale/digest mismatch, never silently use it.

    Consumers must never prefer a stale filesystem record because the
    database is temporarily unavailable; pass ``allow_stale=True`` only for
    the classified-legacy-input path owned by #4023.
    """

    try:
        snapshot = RepositoryConnectionSnapshot.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise RepositoryRouteError(
            REPOSITORY_STALE_SNAPSHOT, "connection snapshot is unavailable"
        ) from exc
    payload = [
        c.model_dump(by_alias=True, mode="json") for c in snapshot.connections
    ]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    if digest != snapshot.digest:
        raise RepositoryRouteError(REPOSITORY_STALE_SNAPSHOT, "snapshot digest mismatch")
    if snapshot.revision < minimum_revision and not allow_stale:
        raise RepositoryRouteError(
            REPOSITORY_STALE_SNAPSHOT, "snapshot is stale; refresh from the database"
        )
    return snapshot


__all__ = [
    "AuthoredGitRepositoryTarget",
    "AuthoredLoreRepositoryTarget",
    "AuthoredRepositoryTarget",
    "CapabilityReadinessRegistry",
    "ConnectionAuditRecord",
    "ConnectionChangeRequest",
    "ConnectionOwnershipPolicy",
    "CONNECTION_SNAPSHOT_PRODUCER",
    "CONNECTION_SNAPSHOT_SCHEMA_VERSION",
    "DEFAULT_GIT_CONNECTION_REF",
    "GitHubAppCredential",
    "LEGACY_REPOSITORY_DECODER_VERSION",
    "REPOSITORY_DENIED",
    "REPOSITORY_ENDPOINT_RETARGET",
    "REPOSITORY_ID_REUSE",
    "REPOSITORY_POLICY_CONFLICT",
    "REPOSITORY_ROUTE_AMBIGUOUS",
    "REPOSITORY_ROUTE_CONFLICT",
    "REPOSITORY_ROUTE_UNSUPPORTED",
    "REPOSITORY_SETUP_REQUIRED",
    "REPOSITORY_STALE_SNAPSHOT",
    "REPOSITORY_TRANSFER_REAUTHORIZATION",
    "RepositoryAssignment",
    "RepositoryClientEvidence",
    "RepositoryClientPolicy",
    "RepositoryConnectionSnapshot",
    "RepositoryCredential",
    "RepositoryConnection",
    "RepositoryContractError",
    "RepositoryIdentity",
    "RepositoryRouteError",
    "ResolvedRepositoryTarget",
    "ScopedRouteCandidate",
    "admit_legacy_free_connection",
    "admit_scoped_route",
    "authorize_connection_use",
    "build_connection_audit_record",
    "compile_repository_target",
    "decode_legacy_repository_history_v1",
    "default_scope_for",
    "derive_repository_capabilities",
    "ensure_repository_ready",
    "github_repository_name_from_value",
    "invalidate_on_owner_transfer",
    "is_legacy_unrestricted_connection",
    "load_connection_snapshot",
    "load_repository_connection",
    "materialize_resolved_repository_target",
    "normalize_endpoint",
    "normalize_scope",
    "persist_repository_connection",
    "publish_connection_snapshot",
    "reconcile_default_git_connection",
    "reconcile_verified_rename",
    "repository_branch_from_value",
    "repository_name_from_value",
    "resolve_default_git_credential",
    "route_diagnostic",
    "route_key_for",
    "validate_connection_and_client",
    "validate_endpoint_retarget",
    "validate_scoped_connection_for_write",
]
