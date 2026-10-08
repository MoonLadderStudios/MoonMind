"""Resolve SecretRefs for managed agent subprocess launches."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select

from moonmind.auth.secret_refs import parse_secret_ref, SecretBackend, VaultSecretResolver, load_vault_token
from moonmind.auth.resolvers import (
    EnvSecretResolver,
    DbEncryptedSecretResolver,
    ExecSecretResolver,
    AdapterVaultSecretResolver,
    RootSecretResolver,
)

#: Principal under which launches read the deployment's recorded repository
#: connections. A single-user instance admits its own runtime at system scope.
_LAUNCH_CONNECTION_PRINCIPAL = "system:managed-runtime-launch"
#: Managed-secret slugs that declare the deployment's GitHub credential, in the
#: precedence migration ``391_legacy_github_cred_4023`` records.
_DEPLOYMENT_GITHUB_SECRET_SLUGS: tuple[str, ...] = ("GITHUB_TOKEN", "GITHUB_PAT")
# Registry endpoint this module's GHCR pull credentials are bound to. Returned
# credentials must only be presented to this endpoint (or its exact image /
# repository scope); never to arbitrary endpoints derived from image strings,
# error messages, or workflow input.
GHCR_REGISTRY = "ghcr.io"
_MANAGED_GHCR_PULL_USER_SLUGS: tuple[str, ...] = ("GHCR_PULL_USER",)
_MANAGED_GHCR_PULL_TOKEN_SLUGS: tuple[str, ...] = ("GHCR_PULL_TOKEN",)

_GITHUB_API_TIMEOUT_SECONDS = 10.0
_GITHUB_USER_RESPONSE_MAX_BYTES = 64 * 1024
#: GHCR authenticates a personal access token by the token alone and ignores
#: the username, so a failed ``/user`` lookup does not have to sink the pull.
_GHCR_TOKEN_USER_PLACEHOLDER = "x-access-token"

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from moonmind.schemas.managed_session_models import (
        ManagedGitHubCredentialDescriptor,
    )

def _normalize_secret_ref_input(
    ref: str | Mapping[str, Any],
    *,
    field_name: str = "MANAGED_API_KEY_REF",
) -> str:
    if isinstance(ref, Mapping):
        direct_ref = str(ref.get("secret_ref") or ref.get("secretRef") or "").strip()
        if direct_ref:
            return direct_ref
        secret_id = str(ref.get("secret_id") or ref.get("secretId") or "").strip()
        backend_type = str(
            ref.get("backend_type") or ref.get("backendType") or ""
        ).strip().lower()
        if secret_id and backend_type in {"db", "db_encrypted"}:
            return f"db://{secret_id}"
        if secret_id and not backend_type:
            return secret_id
        raise ValueError(f"{field_name} must be a string secret reference")
    if not isinstance(ref, str):
        raise ValueError(
            f"{field_name} must be a string secret reference, got {type(ref).__name__}"
        )

    stripped = ref.strip()
    if not stripped:
        raise ValueError(f"{field_name} is empty")
    return stripped

async def load_repository_connection_for_launch(
    connection_ref: str, *, repository: str | None = None
) -> Any:
    """Return the recorded repository connection, or ``None`` when none exists.

    A database failure propagates so callers can tell an unreadable record
    from an absent one, and a deleted or disabled connection raises
    ``RepositoryRouteError``: only true absence may select the deployment
    declaration. A recorded connection admits only a named
    ``repository`` it has a verified assignment for, and only that
    assignment's operations. The connection service classifies the migrated
    default's historical unscoped exception; its identity alone grants none.
    A default read without a repository serves repository-independent
    readiness, registry, and deployment operations, not repository admission.
    """

    from api_service.db.base import async_session_maker
    from api_service.db.models import RepositoryConnectionRecord
    from api_service.services.repository_connections import (
        RepositoryConnectionService,
    )
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
        REPOSITORY_DENIED,
        RepositoryRouteError,
    )

    async with async_session_maker() as session:
        service = RepositoryConnectionService(session)
        connection = await service.get_connection(
            connection_ref,
            principal_ref=_LAUNCH_CONNECTION_PRINCIPAL,
            principal_scope=("system", None),
        )
        if connection is not None:
            if connection_ref == DEFAULT_GIT_CONNECTION_REF and repository is None:
                return connection
            assignment = await service.launch_assignment(connection, repository)
            return connection.model_copy(
                update={
                    "allowed_operations": tuple(
                        operation
                        for operation in connection.allowed_operations
                        if operation in assignment.operations
                    )
                }
            )
        deleted = (
            await session.execute(
                select(RepositoryConnectionRecord.connection_id).where(
                    RepositoryConnectionRecord.connection_id == connection_ref,
                    RepositoryConnectionRecord.tombstone.is_(True),
                )
            )
        ).first()
        if deleted is not None:
            raise RepositoryRouteError(
                REPOSITORY_DENIED, f"repository connection {connection_ref} was deleted"
            )
        return None


async def load_active_managed_github_secret_slug() -> str | None:
    """Return the first active ``GITHUB_TOKEN``/``GITHUB_PAT`` managed secret.

    Reads presence only, never a value. A database failure propagates so an
    unreadable store is not mistaken for no secret.
    """

    from api_service.db.base import async_session_maker
    from api_service.db.models import ManagedSecret, SecretStatus

    async with async_session_maker() as session:
        active = set(
            (
                await session.execute(
                    select(ManagedSecret.slug).where(
                        ManagedSecret.slug.in_(_DEPLOYMENT_GITHUB_SECRET_SLUGS),
                        ManagedSecret.status == SecretStatus.ACTIVE,
                    )
                )
            ).scalars()
        )
    return next(
        (slug for slug in _DEPLOYMENT_GITHUB_SECRET_SLUGS if slug in active), None
    )


def _repository_connections_dir() -> Path:
    runtime_root = os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
    return Path(
        os.environ.get(
            "MOONMIND_REPOSITORY_CONNECTIONS_DIR",
            os.path.join(runtime_root, "repository_connections"),
        )
    )


async def select_git_connection_for_launch(
    connection_ref: str,
    *,
    repository: str | None = None,
    connections_dir: Path | None = None,
    client_policy: Any | None = None,
) -> Any:
    """Return exactly the Git connection a launch selected (#4023).

    A recorded connection is authoritative for the ``repository`` it is
    assigned; given ``client_policy`` it takes the deployment's Git client,
    because the deployment owns its client and the record owns authority.
    Without a record, an explicit reference may name a deployment-owned
    connection file, and the default reference returns ``None``: it derives
    from the deployment's declared GitHub configuration. An unreadable, deleted, disabled, or non-Git record and an
    unrecorded explicit reference raise ``RepositoryContractError``; no other
    connection is substituted.
    """

    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
        REPOSITORY_CONNECTION_MISMATCH,
        RepositoryContractError,
        RepositoryRouteError,
        load_repository_connection,
    )

    try:
        recorded = await load_repository_connection_for_launch(
            connection_ref, repository=repository
        )
    except asyncio.CancelledError:
        raise
    except RepositoryRouteError as exc:
        raise RepositoryContractError(
            "REPOSITORY_CONNECTION_UNAVAILABLE",
            f"{exc}; no other connection is substituted, so select a recorded "
            "connection for this work",
        ) from exc
    except Exception as exc:
        raise RepositoryContractError(
            "REPOSITORY_CONNECTION_UNAVAILABLE",
            f"repository connection {connection_ref!r} could not be read; no "
            "other connection is substituted",
        ) from exc
    if recorded is not None:
        if recorded.provider != "git":
            raise RepositoryContractError(
                REPOSITORY_CONNECTION_MISMATCH,
                "repository target and connection identity/provider do not match",
            )
        if client_policy is None:
            return recorded
        return recorded.model_copy(update={"client_policy": client_policy})
    if connection_ref == DEFAULT_GIT_CONNECTION_REF:
        return None
    directory = connections_dir or _repository_connections_dir()
    for path in sorted(directory.glob("*.json")):
        try:
            connection = load_repository_connection(path, connection_ref)
        except RepositoryContractError:
            continue
        if connection.provider == "git":
            return connection
    raise RepositoryContractError(
        "REPOSITORY_CONNECTION_UNAVAILABLE",
        f"repository connection {connection_ref!r} is not recorded; select a "
        f"recorded connection, or {DEFAULT_GIT_CONNECTION_REF}, which uses the "
        "deployment GitHub credential (GITHUB_TOKEN in .env) while it is not "
        "recorded",
    )


class SelectedGitHubAccess(NamedTuple):
    """One read of a launch's selected Git connection and its credential.

    ``connection`` is ``None`` for the unrecorded default, which derives from
    the deployment's GitHub declaration.
    """

    connection: Any | None
    credential: Any


async def select_github_access_for_launch(
    connection_ref: str,
    *,
    repository: str | None = None,
    connections_dir: Path | None = None,
    client_policy: Any | None = None,
    required_operations: Iterable[str] = (),
) -> SelectedGitHubAccess:
    """Select a launch's Git connection and read only its credential, once.

    Selection raises as :func:`select_git_connection_for_launch` does. A
    selected source that fails yields an unresolved credential carrying its
    correction, never another credential. Required operations are checked
    against the recorded connection and assignment before reading a secret.
    """

    from moonmind.auth.github_credentials import (
        resolve_connection_github_credential,
        resolve_deployment_github_credential,
    )
    from moonmind.workflows.executions.repository_contract import (
        REPOSITORY_CONNECTION_MISMATCH,
        RepositoryContractError,
    )

    connection = await select_git_connection_for_launch(
        connection_ref,
        repository=repository,
        connections_dir=connections_dir,
        client_policy=client_policy,
    )
    if connection is not None:
        for operation in required_operations:
            if operation not in connection.allowed_operations:
                raise RepositoryContractError(
                    REPOSITORY_CONNECTION_MISMATCH,
                    f"connection does not allow operation {operation!r}",
                )
    if connection is None:
        credential = await resolve_deployment_github_credential(repo=repository)
    else:
        credential = await resolve_connection_github_credential(
            connection, repo=repository
        )
    return SelectedGitHubAccess(connection=connection, credential=credential)


async def resolve_selected_github_credential_for_launch(
    connection_ref: str,
    *,
    repo: str | None = None,
    connections_dir: Path | None = None,
) -> Any:
    """Resolve only the credential of the Git connection a launch selected."""

    access = await select_github_access_for_launch(
        connection_ref, repository=repo, connections_dir=connections_dir
    )
    return access.credential


async def _default_github_connection_with_operations(
    *, repo: str | None, required_operations: tuple[str, ...]
) -> tuple[Any, Any]:
    """Read and validate recorded authority without accessing its credential."""
    from moonmind.auth.github_credentials import (
        GitHubCredentialSource,
        ResolvedGitHubCredential,
    )
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
        RepositoryRouteError,
    )

    try:
        connection = await load_repository_connection_for_launch(
            DEFAULT_GIT_CONNECTION_REF, repository=repo
        )
    except asyncio.CancelledError:
        raise
    except RepositoryRouteError as exc:
        return None, ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName=DEFAULT_GIT_CONNECTION_REF,
            repo=repo,
            diagnostic=(
                f"{exc}; MoonMind does not try another GitHub credential or "
                "derive the default from the deployment's GitHub declaration "
                "while that record does not admit this use, so select a recorded "
                "connection for this work."
            ),
        )
    except Exception as exc:
        logger.warning(
            "Default repository connection could not be read: %s",
            type(exc).__name__,
        )
        return None, ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName=DEFAULT_GIT_CONNECTION_REF,
            repo=repo,
            diagnostic=(
                f"{DEFAULT_GIT_CONNECTION_REF} could not be read; MoonMind does "
                "not try another GitHub credential while the recorded "
                "connection is unknown."
            ),
            retryable=True,
        )
    if connection is None:
        return None, None
    missing_operations = tuple(
        operation
        for operation in required_operations
        if operation not in connection.allowed_operations
    )
    if missing_operations:
        return None, ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            sourceName=DEFAULT_GIT_CONNECTION_REF,
            repo=repo,
            diagnostic=(
                f"{DEFAULT_GIT_CONNECTION_REF} does not allow required operations "
                f"{', '.join(missing_operations)} for this repository; "
                "MoonMind does not read or substitute a credential for denied use."
            ),
        )
    return connection, None


async def validate_default_github_connection_operations(
    *, repo: str | None = None, required_operations: tuple[str, ...] = ()
) -> Any:
    """Return a safe denial before host/lease mutation, without reading secrets.

    The token boundary rechecks this same authority immediately before delivery;
    validation is not a reusable grant and does not weaken revocation checks.
    """
    _connection, error = await _default_github_connection_with_operations(
        repo=repo, required_operations=required_operations
    )
    return error


async def resolve_default_github_connection_credential(
    *, repo: str | None = None, required_operations: tuple[str, ...] = ()
) -> Any:
    """Resolve only the default credential after its assigned actions admit use.

    A recorded default is authoritative. Only true absence uses the deployment
    declaration; denied operations and unreadable records never read or replace
    a secret. Assignment narrowing is shared with the metadata-only preflight.
    """
    from moonmind.auth.github_credentials import (
        resolve_connection_github_credential,
        resolve_deployment_github_credential,
    )

    connection, error = await _default_github_connection_with_operations(
        repo=repo, required_operations=required_operations
    )
    if error is not None:
        return error
    if connection is None:
        return await resolve_deployment_github_credential(repo=repo)
    return await resolve_connection_github_credential(connection, repo=repo)


def _github_user_api_url() -> str:
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").strip()
    if not api_base:
        api_base = "https://api.github.com"
    return api_base.rstrip("/") + "/user"


def _fetch_github_login_for_token(token: str) -> str | None:
    request = Request(
        _github_user_api_url(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "MoonMind-GHCR-Pull-Auth",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=_GITHUB_API_TIMEOUT_SECONDS) as response:
        payload = json.loads(
            response.read(_GITHUB_USER_RESPONSE_MAX_BYTES).decode("utf-8")
        )
    login = str(payload.get("login") or "").strip()
    return login or None


async def _resolve_github_login_for_token(token: str) -> str | None:
    normalized = str(token or "").strip()
    if not normalized:
        return None
    try:
        return await asyncio.to_thread(_fetch_github_login_for_token, normalized)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        logger.warning(
            "Failed to resolve GitHub username for GHCR pull authentication; "
            "using the token-only form, which GHCR accepts",
            exc_info=True,
        )
        return None


def github_ghcr_pull_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the deployment GitHub token may authenticate ``ghcr.io`` pulls.

    Reverses the #4012 removal by operator choice: the deployment's GitHub
    credential is the same identity that already reads these packages, so
    requiring a second, separately provisioned registry secret to pull an image
    the deployment itself declared was setup this deployment does not need.

    Only an explicit false disables it, restoring #4012's strict separation. The
    derivation stays bound to ``ghcr.io`` and to deployment-declared images; it
    is never applied to a registry or image reference that came from workflow
    input, which is the exposure #4012 step 5 actually guards against.
    """

    source = os.environ if environ is None else environ
    raw = str(source.get("MOONMIND_GHCR_PULL_FROM_GITHUB_TOKEN_ENABLED", "")).strip()
    return raw.casefold() not in {"false", "0", "no", "off", "disabled"}


async def github_derived_ghcr_credentials(
    environment: Mapping[str, str] | None = None,
    *,
    github_credential: Any | None = None,
) -> tuple[str, str] | None:
    """Derive ``ghcr.io`` pull credentials from the deployment GitHub token."""

    if not github_ghcr_pull_enabled():
        return None
    token = await resolve_github_token_for_launch(
        environment or {}, github_credential=github_credential
    )
    if not token:
        return None
    login = await _resolve_github_login_for_token(token)
    return (login or _GHCR_TOKEN_USER_PLACEHOLDER), token


async def _read_ghcr_pull_pair_once(session: Any) -> tuple[str | None, str | None]:
    """Read the ``GHCR_PULL_USER``/``GHCR_PULL_TOKEN`` slugs once.

    Returns the raw ``(user, token)`` pair with ``None`` for unconfigured
    slugs. Callers must verify coherence across reads; a single read alone is
    never proof of a stable configuration revision.
    """

    from sqlalchemy import select

    from api_service.db.models import ManagedSecret, SecretStatus

    user_value: str | None = None
    token_value: str | None = None
    for slugs, target in (
        (_MANAGED_GHCR_PULL_USER_SLUGS, "user"),
        (_MANAGED_GHCR_PULL_TOKEN_SLUGS, "token"),
    ):
        found: str | None = None
        for slug in slugs:
            normalized = str(slug or "").strip()
            if not normalized:
                continue
            result = await session.execute(
                select(ManagedSecret).where(
                    ManagedSecret.slug == normalized,
                    ManagedSecret.status == SecretStatus.ACTIVE,
                )
            )
            secret = result.scalar_one_or_none()
            candidate = str(secret.ciphertext if secret else "").strip()
            if candidate:
                found = candidate
                break
        if target == "user":
            user_value = found
        else:
            token_value = found
    return user_value, token_value


async def _resolve_managed_ghcr_pull_pair() -> tuple[str | None, str | None]:
    """Read the ``GHCR_PULL_USER``/``GHCR_PULL_TOKEN`` managed slugs coherently.

    Both slugs are read inside one managed-secret store session so the two
    values form one selected configuration revision instead of two independent
    reads that could straddle a rotation. The pair is read twice and compared:
    a change between the reads (rotation, disable, or rewrite landing between
    them) raises instead of returning a mixed pair. Store outages propagate to
    the caller (fail closed); they are never treated as "no credentials".
    """

    from api_service.db.base import async_session_maker

    async with async_session_maker() as session:
        first = await _read_ghcr_pull_pair_once(session)
        second = await _read_ghcr_pull_pair_once(session)
        if first != second:
            raise ValueError(
                "GHCR pull managed secrets changed during resolution "
                "(possible rotation between paired reads); refusing to use "
                "a mixed user/token pair"
            )
    return first


async def resolve_ghcr_pull_credentials_for_launch() -> tuple[str, str] | None:
    """Resolve deployment-scoped GHCR pull credentials for launch boundaries.

    Precedence: explicit secret refs, explicit environment pair, managed
    registry slugs, then the deployment's GitHub credential. That last step
    reverses #4012's removal by operator choice (see
    ``github_ghcr_pull_enabled``); set
    ``MOONMIND_GHCR_PULL_FROM_GITHUB_TOKEN_ENABLED=false`` to restore the strict
    separation. What #4012 established still holds either way: a *configured*
    credential that fails resolves to a failure, never to another identity, an
    ambient Docker login, or an anonymous downgrade, and the derived identity is
    only ever presented to ``ghcr.io`` for deployment-declared images.

    Production pull-boundary inventory (recorded here so deleting this helper
    alone is never mistaken for removing every implicit credential path):

    - managed sessions: DinD sidecar (``docker:27``-style stock image) plus
      session images, launched via ``DockerCodexManagedSessionController``
      against the deployment Docker backend; image selection is deployment
      configuration, auth source is this helper via per-launch ephemeral
      ``DOCKER_CONFIG`` (explicit pair) or an empty ephemeral config for
      public-anonymous acquisition with no ambient fallback.
    - generic/profile-bound Omnigent: server/host images resolved to
      digest-pinned refs by ``moonmind/omnigent/bootstrap/image_resolution.py``
      (``_resolve_via_docker_pull`` / ``_image_build_identity``) via bare
      ``docker pull`` against the deployment backend; no source PAT is passed.
    - container jobs: ``container_job_backend.py`` + ``registry_auth_resolve.py``
      (``registry_authorization`` with an explicit ``registryCredentialRef``,
      per-job ephemeral ``--config`` auth dirs, immediate post-pull cleanup).
    - Compose/bootstrap: ``docker-compose.yaml`` image refs and
      ``api_service/services/omnigent_policies.py`` stock-image acquisition via
      bare ``docker pull`` against the deployment backend; no source PAT.
    - legacy worker container path: ``moonmind/agents/codex_worker/worker.py``
      ``_ensure_container_image`` via bare ``docker pull``; no source PAT.

    Explicit precedence (first fully-specified source wins as one selected
    configuration):

    1. SecretRef pair from deployment process environment
       (``MOONMIND_GHCR_PULL_USER_SECRET_REF`` /
       ``MOONMIND_GHCR_PULL_TOKEN_SECRET_REF`` or the ``WORKFLOW_`` equivalents).
    2. Deployment plaintext pair from process environment (``GHCR_PULL_USER`` +
       ``GHCR_PULL_TOKEN`` as set by the operator for the deployment).
    3. Managed-secret slug pair (``GHCR_PULL_USER`` + ``GHCR_PULL_TOKEN``),
        read coherently in one store session and verified stable across the
        paired reads (a rotation landing between reads raises).
    4. Omitted configuration: return ``None`` for the public-anonymous path,
       which performs no registry authentication and queries no secret store
       beyond the GHCR slug lookup above.

    Outcomes: omitted/public-anonymous returns ``None``; explicitly configured
    pairs return ``(user, token)`` bound to :data:`GHCR_REGISTRY`; incomplete
    pairs, unresolvable SecretRefs, rotation/disable detected between the
    paired reads, managed-store outage, and denied/revoked credentials raise
    ``ValueError`` at the registry boundary. ``None`` is never a signal to try
    another identity.

    Agent-authored launch fields, source PATs, and GitHub actor lookups are
    not registry authentication and are not accepted here: this resolver takes
    no launch mapping or credential descriptor. SecretRef *names* are read
    from the deployment process environment only, never from a launch
    mapping.

    Public defaults: images that are publicly readable are acquired
    anonymously (``None``) with no model/source credential, no managed-secret
    access beyond the GHCR slug check, and no login helper. Explicit
    private-registry setup selects one of the three sources above for
    ``ghcr.io``. Obsolete implicit configuration (any ``GITHUB_TOKEN`` /
    source-connection fallback or GitHub username probing) was removed and
    must not be reintroduced. Safe recovery: reconfigure or restore the
    selected deployment pair and retry the pull; removing the source coupling
    requires no new PAT prompt for unaffected scratch/public work.

    The returned plaintext is for immediate Docker config materialization only
    (per-operation ephemeral config, restricted permissions/lifetime, never
    mounted into the agent or included in snapshots). Callers must not store
    it in workflow history, logs, labels, argv, inspectable runtime env,
    heartbeats, error payloads, plans, or durable metadata.
    """

    user_ref = str(
        os.environ.get("MOONMIND_GHCR_PULL_USER_SECRET_REF")
        or os.environ.get("WORKFLOW_GHCR_PULL_USER_SECRET_REF")
        or ""
    ).strip()
    token_ref = str(
        os.environ.get("MOONMIND_GHCR_PULL_TOKEN_SECRET_REF")
        or os.environ.get("WORKFLOW_GHCR_PULL_TOKEN_SECRET_REF")
        or ""
    ).strip()
    if user_ref or token_ref:
        if not user_ref or not token_ref:
            raise ValueError(
                "GHCR pull authentication requires both user and token secret refs"
            )
        user = await resolve_managed_api_key_reference(
            user_ref,
            field_name="GHCR_PULL_USER_SECRET_REF",
        )
        token = await resolve_managed_api_key_reference(
            token_ref,
            field_name="GHCR_PULL_TOKEN_SECRET_REF",
        )
        if not user.strip() or not token.strip():
            raise ValueError(
                "GHCR pull authentication secret refs resolved to an incomplete pair"
            )
        # Detect rotation/disable of db-backed refs between the paired reads
        # through the existing Secrets System readiness check.
        db_refs = [
            ref
            for ref in (user_ref, token_ref)
            if ref.strip().startswith("db://")
        ]
        if db_refs:
            broken = await inspect_managed_secret_refs_for_launch(db_refs)
            if broken:
                raise ValueError(
                    "GHCR pull authentication secret refs are not active: "
                    + "; ".join(
                        f"{item.secret_ref}={item.status}" for item in broken
                    )
                )
        return user.strip(), token.strip()

    user = str(os.environ.get("GHCR_PULL_USER") or "").strip()
    token = str(os.environ.get("GHCR_PULL_TOKEN") or "").strip()
    if user or token:
        if not user or not token:
            raise ValueError(
                "GHCR pull authentication requires both user and token environment variables"
            )
        return user, token

    try:
        stored_user, stored_token = await _resolve_managed_ghcr_pull_pair()
    except asyncio.CancelledError:
        raise
    except ValueError:
        # Coherence failures (partial pair reads, rotation detected between
        # the paired reads) already describe the registry-boundary outcome;
        # never relabel them as a store outage or fall back to another
        # identity.
        raise
    except Exception as exc:
        raise ValueError(
            "GHCR pull credential store is unavailable; refusing to fall back "
            "to another identity or anonymous acquisition"
        ) from exc

    if stored_user or stored_token:
        if not stored_user or not stored_token:
            raise ValueError(
                "GHCR pull authentication requires both user and token managed secrets"
            )
        return stored_user.strip(), stored_token.strip()

    # Nothing registry-specific is configured. Rather than downgrade to an
    # anonymous pull that a private package will deny, derive the identity from
    # the deployment's own GitHub credential. A configured pair that *fails*
    # still fails closed above: this is the unconfigured case only, so a broken
    # explicit credential never silently becomes a different identity.
    return await github_derived_ghcr_credentials()

async def resolve_github_token_for_launch(
    environment: Mapping[str, str] | None = None,
    *,
    github_credential: Any | None = None,
) -> str | None:
    """Resolve the GitHub token used for launch-time auth seeding.

    When a non-sensitive descriptor is provided, the descriptor controls
    resolution. Legacy environment ``GITHUB_TOKEN`` remains a launch-boundary
    input only so older callers can still be scrubbed before container launch.
    Otherwise only the deployment's default repository connection is used; a
    failed selected source returns ``None`` rather than another credential.
    """

    launch_environment = environment or {}
    token = str(launch_environment.get("GITHUB_TOKEN", "")).strip()
    if token:
        return token

    if github_credential is not None:
        source = str(getattr(github_credential, "source", "") or "").strip()
        required = bool(getattr(github_credential, "required", False))
        if source == "environment":
            env_var = str(
                getattr(github_credential, "env_var", None) or "GITHUB_TOKEN"
            ).strip()
            token = str(os.environ.get(env_var, "")).strip()
            if token:
                return token
            if required:
                raise ValueError(
                    f"GitHub credential environment reference {env_var} is not set"
                )
            return None
        if source == "secret_ref":
            secret_ref = str(getattr(github_credential, "secret_ref", "") or "").strip()
            if not secret_ref:
                if required:
                    raise ValueError("GitHub credential secretRef is not configured")
                return None
            try:
                return await resolve_managed_api_key_reference(
                    secret_ref,
                    field_name="githubCredential.secretRef",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if required:
                    raise ValueError(
                        "GitHub credential secretRef could not be resolved"
                    ) from exc
                logger.warning(
                    "Failed to resolve GitHub credential secret ref for managed "
                    "runtime launch",
                    exc_info=True,
                )
                return None
        if source == "managed_secret":
            # Historical descriptor name for a repository session's own
            # selection, not a search of well-known secret slugs. A session
            # with a repositoryTarget resolves that target's connection before
            # reaching here; without one, this is the default connection.
            resolved = await resolve_default_github_connection_credential()
            if resolved.token:
                return resolved.token
            if required:
                raise ValueError(resolved.safe_summary)
            return None
        raise ValueError(f"Unsupported GitHub credential source: {source or '<blank>'}")

    resolved = await resolve_default_github_connection_credential()
    if resolved.token:
        return resolved.token
    if resolved.diagnostic and resolved.source.value != "missing":
        logger.warning("GitHub launch credential unavailable: %s", resolved.safe_summary)
    return None

def build_github_credential_descriptor_for_launch(
    environment: Mapping[str, str] | None = None,
    *,
    repository_session: bool = False,
) -> "ManagedGitHubCredentialDescriptor | None":
    """Return a non-sensitive GitHub launch credential descriptor.

    An explicit launch ``GITHUB_TOKEN`` stays the launch's own input. A
    repository session otherwise uses the connection its repository
    selection names (the deployment default when it has no
    ``repositoryTarget``); scratch sessions get no GitHub credential.
    """

    from moonmind.schemas.managed_session_models import (
        ManagedGitHubCredentialDescriptor,
    )

    launch_environment = environment or {}
    if str(launch_environment.get("GITHUB_TOKEN", "")).strip():
        return ManagedGitHubCredentialDescriptor(
            source="environment",
            envVar="GITHUB_TOKEN",
            required=False,
        )
    if repository_session:
        return ManagedGitHubCredentialDescriptor(source="managed_secret", required=False)
    return None

async def resolve_managed_api_key_reference(
    ref: str | Mapping[str, Any],
    *,
    field_name: str = "MANAGED_API_KEY_REF",
) -> str:
    """Resolve a profile api_key_ref or secret_ref into the credential string using RootSecretResolver."""

    stripped = _normalize_secret_ref_input(ref, field_name=field_name)
        
    if "://" not in stripped:
        stripped = f"env://{stripped}"

    try:
        parsed = parse_secret_ref(stripped)
    except Exception as e:
        raise ValueError(f"Unable to resolve MANAGED_API_KEY_REF={ref!r}: {e}")

    resolvers = {}
    vault_resolver_instance = None

    if parsed.backend == SecretBackend.ENV:
        resolvers[SecretBackend.ENV] = EnvSecretResolver()

    elif parsed.backend == SecretBackend.DB_ENCRYPTED:
        resolvers[SecretBackend.DB_ENCRYPTED] = DbEncryptedSecretResolver()

    elif parsed.backend == SecretBackend.EXEC:
        resolvers[SecretBackend.EXEC] = ExecSecretResolver()

    elif parsed.backend == SecretBackend.VAULT:
        addr = str(
            os.environ.get("MOONMIND_VAULT_ADDR")
            or os.environ.get("VAULT_ADDR")
            or ""
        ).strip()
        token_file_raw = str(os.environ.get("MOONMIND_VAULT_TOKEN_FILE", "")).strip()
        token_file = Path(token_file_raw) if token_file_raw else None
        direct_token = str(
            os.environ.get("MOONMIND_VAULT_TOKEN")
            or os.environ.get("VAULT_TOKEN")
            or ""
        ).strip()
        token = load_vault_token(
            token=direct_token or None,
            token_file=token_file,
        )
        if not addr or not token:
            raise ValueError(
                "vault:// api_key_ref requires MOONMIND_VAULT_ADDR (or VAULT_ADDR) "
                "and MOONMIND_VAULT_TOKEN / VAULT_TOKEN or MOONMIND_VAULT_TOKEN_FILE"
            )
        namespace = str(
            os.environ.get("MOONMIND_VAULT_NAMESPACE")
            or os.environ.get("VAULT_NAMESPACE")
            or ""
        ).strip() or None
        mounts_csv = str(os.environ.get("MOONMIND_VAULT_ALLOWED_MOUNTS", "kv")).strip()
        allowed = tuple(m.strip() for m in mounts_csv.split(",") if m.strip()) or (
            "kv",
        )
        vault_resolver_instance = VaultSecretResolver(
            address=addr,
            token=token,
            namespace=namespace,
            allowed_mounts=allowed,
        )
        resolvers[SecretBackend.VAULT] = AdapterVaultSecretResolver(vault_resolver_instance)

    root_resolver = RootSecretResolver(resolvers)

    try:
        return await root_resolver.resolve(parsed)
    except Exception as e:
        raise ValueError(f"Unable to resolve MANAGED_API_KEY_REF={ref!r}: {e}")
    finally:
        if vault_resolver_instance is not None:
            await vault_resolver_instance.aclose()

@dataclass(frozen=True)
class BrokenSecretRef:
    """Describes a managed SecretRef that cannot be used for launch.

    The diagnostic is intentionally metadata-only; no plaintext, ciphertext, or
    decryption error detail is surfaced. ``status`` is the managed secret
    lifecycle state ("missing", "disabled", "rotated", "deleted", "invalid").
    """

    secret_ref: str
    slug: str
    status: str
    diagnostic_code: str
    message: str


class SecretRefLaunchBlockedError(ValueError):
    """Raised when one or more managed SecretRefs are not active for launch."""

    def __init__(self, broken: list[BrokenSecretRef]) -> None:
        self.broken = list(broken)
        descriptions = "; ".join(
            f"{item.secret_ref}={item.status}" for item in self.broken
        )
        super().__init__(
            "Refusing to launch: managed SecretRefs are not active: "
            f"{descriptions}"
        )


async def inspect_managed_secret_refs_for_launch(
    refs: Iterable[str],
) -> list[BrokenSecretRef]:
    """Inspect managed (``db://``) SecretRefs and return any broken references.

    Only ``db://`` refs are inspected here; ``env://``, ``exec://``, and
    ``vault://`` validation is left to the regular resolver. Plaintext is never
    read or returned.
    """

    candidates: list[tuple[str, str]] = []
    seen_slugs: set[str] = set()
    for ref in refs:
        if not isinstance(ref, str):
            continue
        stripped = ref.strip()
        if not stripped or not stripped.startswith("db://"):
            continue
        slug = stripped.removeprefix("db://").strip()
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        candidates.append((stripped, slug))

    if not candidates:
        return []

    from api_service.db.base import async_session_maker
    from api_service.db.models import ManagedSecret, SecretStatus

    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(ManagedSecret.slug, ManagedSecret.status).where(
                    ManagedSecret.slug.in_([slug for _, slug in candidates])
                )
            )
            statuses_by_slug: dict[str, str] = {}
            for slug, status in result.all():
                statuses_by_slug[slug] = (
                    status.value if isinstance(status, SecretStatus) else str(status)
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        # Cannot reach the managed-secret store (e.g. unit tests without a DB
        # fixture, transient connection issue). Skip the pre-flight check;
        # the resolver itself will still surface a typed failure when it
        # attempts to read the secret.
        logger.warning(
            "Skipping managed SecretRef readiness check; managed secret store "
            "unreachable.",
            exc_info=True,
        )
        return []

    active_value = SecretStatus.ACTIVE.value
    issues: list[BrokenSecretRef] = []
    for secret_ref, slug in candidates:
        status = statuses_by_slug.get(slug)
        if status is None:
            issues.append(
                BrokenSecretRef(
                    secret_ref=secret_ref,
                    slug=slug,
                    status="missing",
                    diagnostic_code="broken_reference_missing",
                    message=(
                        f"Managed secret '{slug}' is missing; restore it or "
                        "update the reference."
                    ),
                )
            )
            continue
        if status != active_value:
            issues.append(
                BrokenSecretRef(
                    secret_ref=secret_ref,
                    slug=slug,
                    status=status,
                    diagnostic_code=f"broken_reference_{status}",
                    message=(
                        f"Managed secret '{slug}' is {status}; re-enable it "
                        "before launching."
                    ),
                )
            )
    return issues


async def assert_managed_secret_refs_active_for_launch(
    refs: Iterable[str],
) -> None:
    """Raise SecretRefLaunchBlockedError if any managed SecretRef is not active."""

    issues = await inspect_managed_secret_refs_for_launch(refs)
    if issues:
        raise SecretRefLaunchBlockedError(issues)


__all__ = [
    "BrokenSecretRef",
    "GHCR_REGISTRY",
    "SecretRefLaunchBlockedError",
    "SelectedGitHubAccess",
    "assert_managed_secret_refs_active_for_launch",
    "build_github_credential_descriptor_for_launch",
    "inspect_managed_secret_refs_for_launch",
    "load_active_managed_github_secret_slug",
    "load_repository_connection_for_launch",
    "resolve_default_github_connection_credential",
    "resolve_ghcr_pull_credentials_for_launch",
    "resolve_github_token_for_launch",
    "resolve_managed_api_key_reference",
    "resolve_selected_github_credential_for_launch",
    "select_git_connection_for_launch",
    "select_github_access_for_launch",
    "validate_default_github_connection_operations",
]
