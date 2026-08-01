"""Provider-aware repository compilation and readiness boundary.

MM-1219 replaces repository-shaped authoring aliases with one discriminated
target.  This module owns compilation, connection reconciliation, capability
derivation, and the pre-mutation readiness check so those decisions cannot
drift across authoring and runtime code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

DEFAULT_GIT_CONNECTION_REF = "repository-connection:git-default"
LEGACY_REPOSITORY_DECODER_VERSION = "moonmind.repository-legacy-history.v1"
REPOSITORY_CAPABILITY_UNKNOWN = "REPOSITORY_CAPABILITY_UNKNOWN"
REPOSITORY_CONNECTION_MISMATCH = "REPOSITORY_CONNECTION_MISMATCH"
REPOSITORY_CLIENT_MISMATCH = "REPOSITORY_CLIENT_MISMATCH"
REPOSITORY_REMOTE_TIP_MISMATCH = "REPOSITORY_REMOTE_TIP_MISMATCH"


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


class ResolvedRepositoryTarget(BaseModel):
    """Immutable repository identity observed at the pre-mutation boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.resolved-repository-target.v1"] = Field(
        alias="schemaVersion"
    )
    provider: Literal["git", "lore"]
    connection_ref: str = Field(alias="connectionRef", min_length=1)
    repository: RepositoryName
    prepared_revision: GitRevision | LoreRevision = Field(alias="preparedRevision")
    prepared_branch: RepositoryBranch = Field(alias="preparedBranch")
    base_branch: RepositoryBranch = Field(alias="baseBranch")
    work_branch: RepositoryBranch = Field(alias="workBranch")
    remote_tip_expectation: dict[str, Any] = Field(alias="remoteTipExpectation")
    client_evidence: RepositoryClientEvidence = Field(alias="clientEvidence")


class RepositoryConnection(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    schema_version: Literal["moonmind.repository-connection.v1"] = Field(
        alias="schemaVersion"
    )
    id: str = Field(min_length=1)
    provider: Literal["git", "lore"]
    display_name: str = Field(alias="displayName", min_length=1)
    endpoint_ref: str = Field(alias="endpointRef", min_length=1)
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
    credential_source: Literal[
        "github_resolver", "secret_ref", "trusted_network_development"
    ] = Field(alias="credentialSource")


def compile_repository_target(value: object) -> AuthoredRepositoryTarget:
    """Compile a UI draft, injecting only the well-known common Git connection."""

    if not isinstance(value, Mapping):
        raise RepositoryContractError(
            "REPOSITORY_TARGET_INVALID",
            "repository must be a provider-discriminated object",
        )
    draft = dict(value)
    if draft.get("provider") == "git" and not str(
        draft.get("connectionRef") or ""
    ).strip():
        draft["connectionRef"] = DEFAULT_GIT_CONNECTION_REF
    try:
        return _TARGET_ADAPTER.validate_python(draft)
    except ValueError as exc:
        raise RepositoryContractError("REPOSITORY_TARGET_INVALID", str(exc)) from exc


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
        credentialSource="github_resolver",
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
    work_branch: str | None = None,
) -> ResolvedRepositoryTarget:
    """Freeze exact repository and client observations for durable metadata."""

    revision: GitRevision | LoreRevision
    if target.provider == "git":
        revision = GitRevision(kind="git_commit", commitSha=observed_revision)
    else:
        revision = LoreRevision(kind="lore_revision", revisionSignature=observed_revision)
    expected_revision: dict[str, Any] = {
        "provider": target.provider,
        "repositoryId": target.repository.name,
    }
    if target.provider == "git":
        expected_revision["commitSha"] = observed_revision
    else:
        expected_revision["revisionSignature"] = observed_revision
    expected = {"kind": "must_equal", "revision": expected_revision}
    return ResolvedRepositoryTarget(
        schemaVersion="moonmind.resolved-repository-target.v1",
        provider=target.provider,
        connectionRef=target.connection_ref,
        repository=target.repository,
        preparedRevision=revision,
        preparedBranch=target.branch,
        baseBranch=target.branch,
        workBranch={"name": work_branch or target.branch.name},
        remoteTipExpectation=expected,
        clientEvidence=evidence,
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
        connection.allowed_repository_ids
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

    if connection.credential_source == "github_resolver":
        await _await_if_needed(credential_resolver(target.repository.name))

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
    "RepositoryClientEvidence",
    "RepositoryClientPolicy",
    "RepositoryConnection",
    "RepositoryContractError",
    "ResolvedRepositoryTarget",
    "compile_repository_target",
    "decode_legacy_repository_history_v1",
    "derive_repository_capabilities",
    "ensure_repository_ready",
    "load_repository_connection",
    "materialize_resolved_repository_target",
    "persist_repository_connection",
    "reconcile_default_git_connection",
    "resolve_default_git_credential",
    "validate_connection_and_client",
]
