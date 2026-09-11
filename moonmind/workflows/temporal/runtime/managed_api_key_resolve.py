"""Resolve SecretRefs for managed agent subprocess launches."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from sqlalchemy import select

from moonmind.auth.secret_refs import parse_secret_ref, SecretBackend, VaultSecretResolver, load_vault_token
from moonmind.auth.resolvers import (
    EnvSecretResolver,
    DbEncryptedSecretResolver,
    ExecSecretResolver,
    AdapterVaultSecretResolver,
    RootSecretResolver,
)

# Slugs tried when no profile secret_refs / env token / WORKFLOW_GITHUB_TOKEN_SECRET_REF
# produced a token (matches api_service startup seeding and dashboard hints).
_MANAGED_GITHUB_TOKEN_SLUGS: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GITHUB_PAT",
)
# Registry endpoint this module's GHCR pull credentials are bound to. Returned
# credentials must only be presented to this endpoint (or its exact image /
# repository scope); never to arbitrary endpoints derived from image strings,
# error messages, or workflow input.
GHCR_REGISTRY = "ghcr.io"
_MANAGED_GHCR_PULL_USER_SLUGS: tuple[str, ...] = ("GHCR_PULL_USER",)
_MANAGED_GHCR_PULL_TOKEN_SLUGS: tuple[str, ...] = ("GHCR_PULL_TOKEN",)

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

async def resolve_managed_github_token_from_store() -> str | None:
    """Return an active GitHub PAT from managed secrets (Settings), if any.

    This is separate from provider profile ``secret_refs``: operators store one
    org-wide token under a well-known slug without binding it to each profile.
    """
    from api_service.db.base import async_session_maker
    from api_service.db.models import ManagedSecret, SecretStatus

    async with async_session_maker() as session:
        for slug in _MANAGED_GITHUB_TOKEN_SLUGS:
            # Probe well-known slugs quietly so an expected "not configured"
            # path does not emit one warning per candidate.
            result = await session.execute(
                select(ManagedSecret).where(
                    ManagedSecret.slug == slug,
                    ManagedSecret.status == SecretStatus.ACTIVE,
                )
            )
            secret = result.scalar_one_or_none()
            candidate = str(secret.ciphertext if secret else "").strip()
            if candidate:
                return candidate
    return None

async def _resolve_managed_ghcr_pull_pair() -> tuple[str | None, str | None]:
    """Read the ``GHCR_PULL_USER``/``GHCR_PULL_TOKEN`` managed slugs coherently.

    Both slugs are read inside one managed-secret store session so the two
    values form one selected configuration revision instead of two independent
    reads that could straddle a rotation. Store outages propagate to the
    caller (fail closed); they are never treated as "no credentials".
    """

    from api_service.db.base import async_session_maker
    from api_service.db.models import ManagedSecret, SecretStatus

    async with async_session_maker() as session:
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


async def resolve_ghcr_pull_credentials_for_launch(
    environment: Mapping[str, str] | None = None,
    *,
    github_credential: Any | None = None,
) -> tuple[str, str] | None:
    """Resolve deployment-scoped GHCR pull credentials for launch boundaries.

    MoonLadderStudios/MoonMind#4012: source repository/model credentials are
    never implicit registry credentials. This resolver compiles authentication
    only from trusted deployment configuration and never converts a source
    GitHub PAT into pull credentials, never probes the GitHub username for a
    token, and never falls back to another identity, ambient Docker login, or
    an anonymous downgrade when a configured credential fails.

    Production pull-boundary inventory (recorded here so deleting this helper
    alone is never mistaken for removing every implicit credential path):

    - managed sessions: DinD sidecar (``docker:27``-style stock image) plus
      session images, launched via ``DockerCodexManagedSessionController``
      against the deployment Docker backend; image selection is deployment
      configuration, auth source is this helper (explicit pair) or ambient
      daemon config for public images only.
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
       read coherently in one store session.
    4. Omitted configuration: return ``None`` for the public-anonymous path,
       which performs no registry authentication and queries no secret store
       beyond the GHCR slug lookup above.

    Outcomes: omitted/public-anonymous returns ``None``; explicitly configured
    pairs return ``(user, token)`` bound to :data:`GHCR_REGISTRY`; incomplete
    pairs, unresolvable SecretRefs, rotation/disable detected between the
    paired reads, managed-store outage, and denied/revoked credentials raise
    ``ValueError`` at the registry boundary. ``None`` is never a signal to try
    another identity.

    The ``environment`` launch mapping and ``github_credential`` descriptor are
    accepted for signature compatibility but are never consulted for registry
    secrets: agent-authored launch fields, source PATs, and GitHub actor
    lookups are not registry authentication. SecretRef *names* are read from
    the deployment process environment only, never from the launch mapping.

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

    _ = environment
    _ = github_credential

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

    return None

async def resolve_github_token_for_launch(
    environment: Mapping[str, str] | None = None,
    *,
    github_credential: Any | None = None,
) -> str | None:
    """Resolve the GitHub token used for launch-time auth seeding.

    When a non-sensitive descriptor is provided, the descriptor controls
    resolution. Legacy environment ``GITHUB_TOKEN`` remains a launch-boundary
    input only so older callers can still be scrubbed before container launch.
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
            try:
                resolved = await resolve_managed_github_token_from_store()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if required:
                    raise ValueError(
                        "GitHub credential managed secret could not be resolved"
                    ) from exc
                logger.warning(
                    "Failed to resolve GitHub token from managed secrets store",
                    exc_info=True,
                )
                return None
            if resolved:
                return resolved
            if required:
                raise ValueError("GitHub credential managed secret is not configured")
            return None
        raise ValueError(f"Unsupported GitHub credential source: {source or '<blank>'}")

    from moonmind.auth.github_credentials import resolve_github_credential

    resolved = await resolve_github_credential()
    if resolved.token:
        return resolved.token

    from moonmind.config.settings import settings as _mm_settings

    secret_ref = str(
        getattr(_mm_settings.github, "github_token_secret_ref", "") or ""
    ).strip()
    if secret_ref:
        try:
            return await resolve_managed_api_key_reference(secret_ref)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Failed to resolve GitHub token secret ref for managed runtime launch",
                exc_info=True,
            )

    try:
        return await resolve_managed_github_token_from_store()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "Failed to resolve GitHub token from managed secrets store",
            exc_info=True,
        )
        return None

def build_github_credential_descriptor_for_launch(
    environment: Mapping[str, str] | None = None,
    *,
    ambient_github_token: str | None = None,
    enable_managed_secret_fallback: bool = False,
) -> "ManagedGitHubCredentialDescriptor | None":
    """Return a non-sensitive GitHub launch credential descriptor."""

    from moonmind.config.settings import settings as _mm_settings
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

    ambient_token = str(ambient_github_token or "").strip()
    if enable_managed_secret_fallback and ambient_token:
        return ManagedGitHubCredentialDescriptor(
            source="environment",
            envVar="GITHUB_TOKEN",
            required=False,
        )

    secret_ref = str(
        getattr(_mm_settings.github, "github_token_secret_ref", "") or ""
    ).strip()
    if enable_managed_secret_fallback and secret_ref:
        return ManagedGitHubCredentialDescriptor(
            source="secret_ref",
            secretRef=secret_ref,
            required=False,
        )

    if enable_managed_secret_fallback:
        return ManagedGitHubCredentialDescriptor(source="managed_secret", required=False)
    return None

async def shape_launch_github_auth_environment(
    environment: Mapping[str, str] | None = None,
    *,
    ambient_github_token: str | None = None,
) -> dict[str, str]:
    """Return launch env with GitHub auth seeded using explicit precedence."""

    shaped_environment = {
        str(key): str(value) for key, value in (environment or {}).items()
    }
    ambient_token = str(ambient_github_token or "").strip()

    if ambient_token and not str(shaped_environment.get("GITHUB_TOKEN", "")).strip():
        shaped_environment["GITHUB_TOKEN"] = ambient_token

    github_token = await resolve_github_token_for_launch(shaped_environment)
    if github_token:
        shaped_environment["GITHUB_TOKEN"] = github_token
        shaped_environment.setdefault("GIT_TERMINAL_PROMPT", "0")

    return shaped_environment

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
    "assert_managed_secret_refs_active_for_launch",
    "build_github_credential_descriptor_for_launch",
    "inspect_managed_secret_refs_for_launch",
    "resolve_ghcr_pull_credentials_for_launch",
    "resolve_github_token_for_launch",
    "resolve_managed_api_key_reference",
    "resolve_managed_github_token_from_store",
    "shape_launch_github_auth_environment",
]
