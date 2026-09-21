"""Production wiring for GitHub App credentials (#4022).

Single owner mapping the existing ``RepositoryConnection`` credential union
onto the existing ``BoundCredentialAcquirer`` issuer seam. No separate
credential store, publisher, or lease/receipt service is introduced:
caching, bounded refresh, revocation checks, and exact-effect
reconciliation stay in the bound acquirer and the existing
connection/publication machinery.

* ``secret_ref`` connections map to :class:`PatAdapter` with the
  connection's own ``SecretRef`` (preserved env/db/vault/exec rules).
* ``github_app`` connections map to :class:`GitHubAppAdapter` with a
  managed key-secret-ref, real JWT + installation-token HTTP via the
  configured host, and returned scope/expiry validation without widening
  or PAT fallback.
* ``revision_reader_for`` exposes the ACTIVE-revision read (lifecycle +
  credential/configuration revisions) over an explicitly supplied
  connection map so production can back it with the existing
  ``RepositoryConnectionService`` snapshot without ambient discovery.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Sequence

DEFAULT_KEY_SECRET_REF = "db://github-app-key/default"

_GITHUB_API_BASE = "https://api.github.com"

#: Credential-source -> bound-acquirer adapter kind. Single owner for the
#: mapping shared by :func:`revision_reader_for` and
#: :func:`build_bound_acquirer_for_connection` so the production issuer and
#: the ACTIVE-revision read cannot drift apart. ``github_resolver`` is
#: intentionally absent: legacy ambient resolution stays on the historical
#: ``resolve_github_credential`` path and never enters bound acquisition.
_ADAPTER_KIND_FOR_SOURCE = {
    "secret_ref": "pat",
    "github_app": "github_app",
}


def permitted_repositories_for(connection: Any) -> tuple[str, ...]:
    """Derive the permitted repository set from the connection's scope."""

    allowed = tuple(
        str(name or "").strip()
        for name in (getattr(connection, "allowed_repository_ids", ()) or ())
        if str(name or "").strip()
    )
    return allowed


def key_secret_ref_for(connection: Any, *, default: str = DEFAULT_KEY_SECRET_REF) -> str:
    """Resolve the managed signing-key secret ref (metadata only, never material)."""

    credential = getattr(connection, "credential", None)
    candidate = ""
    if credential is not None and getattr(credential, "source", "") == "github_app":
        candidate = str(getattr(credential, "key_ref", "") or "").strip()
    if candidate:
        return candidate
    if default and str(default).strip():
        return str(default).strip()
    from moonmind.auth.bound_acquisition import BOUND_DENIED, BoundAccessError

    raise BoundAccessError(BOUND_DENIED, "GitHub App connection has no key secret ref")


def expected_account_for(connection: Any, *, override: str = "") -> str:
    """Resolve the expected installation account (explicit override wins)."""

    if override and str(override).strip():
        return str(override).strip()
    credential = getattr(connection, "credential", None)
    pinned = str(getattr(credential, "account", "") or "").strip() if credential else ""
    return pinned


def issuer_for_connection(
    connection: Any,
    *,
    resolve_secret: Callable[[str], Awaitable[str | bytes] | str | bytes],
    make_jwt: Callable[[bytes], Awaitable[str] | str],
    http_post: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    get_installation: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    expected_account: str = "",
    permitted_repositories: Sequence[str] = (),
    key_secret_ref: str = "",
) -> Any:
    """Build the production issuer for one connection on the acquirer seam."""

    from moonmind.auth.bound_acquisition import (
        BOUND_DENIED,
        BoundAccessError,
        PatAdapter,
    )

    credential = getattr(connection, "credential", None)
    source = str(getattr(credential, "source", "") or "").strip()
    if source == "secret_ref":
        ref = getattr(credential, "credential_ref", None)
        provider = str(getattr(ref, "provider", "") or "").strip()
        key = str(getattr(ref, "key", "") or "").strip()
        if not provider or not key:
            raise BoundAccessError(BOUND_DENIED, "PAT connection has no SecretRef")

        async def _resolve_backend(_ref: str) -> str:
            value = resolve_secret(_ref)
            if hasattr(value, "__await__"):
                value = await value  # type: ignore[misc]
            if isinstance(value, (bytes, bytearray)):
                return bytes(value).decode("utf-8", errors="strict")
            return str(value or "")

        return PatAdapter(f"{provider}://{key}", _resolve_backend)
    if source == "github_app":
        from moonmind.auth.github_app import GitHubAppAdapter

        app_ref = str(getattr(credential, "app_ref", "") or "").strip()
        installation_ref = str(getattr(credential, "installation_ref", "") or "").strip()
        if not app_ref or not installation_ref:
            raise BoundAccessError(BOUND_DENIED, "GitHub App connection is incomplete")
        resolved_key_ref = (
            str(key_secret_ref).strip()
            or key_secret_ref_for(connection, default=DEFAULT_KEY_SECRET_REF)
        )

        async def _resolve_key(ref: str) -> bytes:
            value = resolve_secret(ref)
            if hasattr(value, "__await__"):
                value = await value  # type: ignore[misc]
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)
            return str(value or "").encode("utf-8")

        permitted = tuple(
            str(name).strip() for name in permitted_repositories if str(name).strip()
        )
        if not permitted:
            permitted = permitted_repositories_for(connection)
        return GitHubAppAdapter(
            app_ref=app_ref,
            installation_ref=installation_ref,
            key_secret_ref=resolved_key_ref,
            resolve_key=_resolve_key,
            make_jwt=make_jwt,
            http_post=http_post,
            get_installation=get_installation,
            expected_account=expected_account_for(connection, override=expected_account),
            permitted_repositories=permitted,
        )
    raise BoundAccessError(BOUND_DENIED, f"unsupported credential source {source!r}")


def revision_reader_for(connections: Mapping[str, Any]) -> Callable[[str], Any]:
    """Build an ACTIVE-revision reader over an explicit connection map."""

    snapshot = dict(connections or {})

    async def _read(connection_id: str) -> Any:
        from moonmind.auth.bound_acquisition import (
            BOUND_DENIED,
            ActiveRevision,
            BoundAccessError,
        )

        connection = snapshot.get(str(connection_id or "").strip())
        if connection is None:
            raise BoundAccessError(BOUND_DENIED, "unknown connection")
        credential = getattr(connection, "credential", None)
        source = str(getattr(credential, "source", "") or "").strip()
        adapter_kind = _ADAPTER_KIND_FOR_SOURCE.get(source)
        if adapter_kind is None:
            raise BoundAccessError(BOUND_DENIED, f"unsupported credential source {source!r}")
        lifecycle = str(getattr(connection, "lifecycle", "active") or "active").strip()
        if lifecycle == "active":
            status = "active"
        elif lifecycle == "disabled":
            status = "disabled"
        else:
            status = "revoked"
        return ActiveRevision(
            credential_revision=int(getattr(connection, "credential_revision", 1)),
            connection_revision=int(getattr(connection, "policy_revision", 1)),
            policy_revision=int(getattr(connection, "policy_revision", 1)),
            status=status,
            adapter_kind=adapter_kind,
        )

    return _read


# ---------------------------------------------------------------------------
# Production HTTP/JWT defaults (real GitHub contracts, injectable in tests).
# ---------------------------------------------------------------------------


def github_api_base_for(endpoint_ref: str = "") -> str:
    """Derive the API base for the configured host (api.github.com default)."""

    raw = str(endpoint_ref or "").strip().lower()
    if "ghe" in raw or (
        "github" not in raw and raw.startswith("https://") and "." in raw
    ):
        # GitHub Enterprise Server exposes the API under /api/v3.
        base = raw.rstrip("/")
        if not base.endswith("/api/v3"):
            base += "/api/v3"
        return base or _GITHUB_API_BASE
    return _GITHUB_API_BASE


def make_github_app_jwt(
    key_material: bytes,
    *,
    app_id: str,
    ttl_seconds: float = 540.0,
) -> str:
    """Mint a short-lived App JWT (RS256, 10-minute default per GitHub docs)."""

    import jwt as pyjwt

    if not key_material or not bytes(key_material).strip():
        raise ValueError("App signing key is unavailable")
    if not str(app_id or "").strip():
        raise ValueError("App ID is required for JWT issuance")
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + int(max(60.0, float(ttl_seconds))),
        "iss": str(app_id).strip(),
    }
    return pyjwt.encode(payload, key_material, algorithm="RS256")


async def post_installation_token(
    *,
    jwt: str,
    payload: Mapping[str, Any],
    installation_id: str,
    api_base: str = _GITHUB_API_BASE,
) -> Mapping[str, Any]:
    """POST the installation-token request against the current contract."""

    import httpx

    url = f"{api_base.rstrip('/')}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=dict(payload))
        if response.status_code in (401, 403, 404):
            error: Exception = ValueError(f"installation-token failed: {response.status_code}")
            setattr(error, "status", response.status_code)
            setattr(error, "code", "BOUND_REVOKED" if response.status_code != 404 else "BOUND_UNAVAILABLE")
            raise error
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, Mapping):
        raise ValueError("installation-token response is invalid")
    return data


async def fetch_installation_record(
    *,
    jwt: str,
    installation_id: str,
    api_base: str = _GITHUB_API_BASE,
) -> Mapping[str, Any]:
    """Fetch the verified installation/account association record."""

    import httpx

    url = f"{api_base.rstrip('/')}/app/installations/{installation_id}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, Mapping):
        raise ValueError("installation record is invalid")
    return data


__all__ = [
    "DEFAULT_KEY_SECRET_REF",
    "acquire_bound_credential_for_connection",
    "acquire_bound_headers_for_connection",
    "build_bound_acquirer_for_connection",
    "default_get_installation_for",
    "default_http_post_for",
    "default_make_jwt_for",
    "default_resolve_secret_ref",
    "expected_account_for",
    "fetch_installation_record",
    "github_api_base_for",
    "issuer_for_connection",
    "key_secret_ref_for",
    "make_github_app_jwt",
    "parse_app_id",
    "parse_installation_id",
    "permitted_repositories_for",
    "post_installation_token",
    "revision_reader_for",
]


# ---------------------------------------------------------------------------
# Production bound-acquirer construction site (#4022 R2/R6).
#
# The single place that builds a ``BoundCredentialAcquirer`` for an admitted
# operation: ``revision_reader_for`` supplies the ACTIVE-revision read and
# ``issuer_for_connection`` supplies the adapter (``secret_ref`` -> PAT,
# ``github_app`` -> installation-token issuer with the managed key-secret-ref,
# configured-host JWT/HTTP, and returned scope/expiry validation). Production
# consumers (GitHubService reads, publisher flows) acquire through
# :func:`acquire_bound_credential_for_connection` /
# :func:`acquire_bound_headers_for_connection` below instead of constructing
# the acquirer themselves.
# ---------------------------------------------------------------------------


def parse_app_id(app_ref: str, *, override: str = "") -> str:
    """Resolve the numeric GitHub App ID (explicit configuration wins)."""

    from moonmind.auth.bound_acquisition import BOUND_DENIED, BoundAccessError

    if override and str(override).strip():
        return str(override).strip()
    import re as _re

    match = _re.search(r"(\d+)\s*$", str(app_ref or ""))
    if match:
        return match.group(1)
    raise BoundAccessError(
        BOUND_DENIED,
        "GitHub App ID is not configured for App issuance "
        "(pass the numeric App ID explicitly)",
    )


def parse_installation_id(installation_ref: str, *, override: str = "") -> str:
    """Resolve the numeric installation ID (explicit configuration wins)."""

    from moonmind.auth.bound_acquisition import BOUND_DENIED, BoundAccessError

    if override and str(override).strip():
        return str(override).strip()
    import re as _re

    match = _re.search(r"(\d+)\s*$", str(installation_ref or ""))
    if match:
        return match.group(1)
    raise BoundAccessError(
        BOUND_DENIED,
        "GitHub installation ID is not configured for App issuance "
        "(pass the numeric installation ID explicitly)",
    )


async def default_resolve_secret_ref(ref: str) -> str:
    """Resolve a managed secret ref through the existing Secrets System."""

    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
        resolve_managed_api_key_reference,
    )

    return await resolve_managed_api_key_reference(ref)


def default_make_jwt_for(app_id: str) -> Callable[[bytes], str]:
    """Build the production JWT minter for one configured App ID."""

    from moonmind.auth.bound_acquisition import BOUND_DENIED, BoundAccessError

    resolved = str(app_id or "").strip()
    if not resolved:
        raise BoundAccessError(BOUND_DENIED, "GitHub App ID is not configured")

    def _make(key_material: bytes) -> str:
        return make_github_app_jwt(bytes(key_material), app_id=resolved)

    return _make


def default_http_post_for(
    *, installation_id: str, api_base: str = ""
) -> Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]]:
    """Build the production installation-token POST for one installation."""

    from moonmind.auth.bound_acquisition import BOUND_DENIED, BoundAccessError

    resolved = str(installation_id or "").strip()
    if not resolved:
        raise BoundAccessError(BOUND_DENIED, "GitHub installation ID is not configured")
    base = str(api_base or "").strip() or _GITHUB_API_BASE

    async def _post(
        *, jwt: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return await post_installation_token(
            jwt=jwt, payload=payload, installation_id=resolved, api_base=base
        )

    return _post


def default_get_installation_for(
    *, installation_id: str, api_base: str = ""
) -> Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]]:
    """Build the production installation-record fetch for one installation."""

    from moonmind.auth.bound_acquisition import BOUND_DENIED, BoundAccessError

    resolved = str(installation_id or "").strip()
    if not resolved:
        raise BoundAccessError(BOUND_DENIED, "GitHub installation ID is not configured")
    base = str(api_base or "").strip() or _GITHUB_API_BASE

    async def _fetch(*, jwt: str) -> Mapping[str, Any]:
        return await fetch_installation_record(
            jwt=jwt, installation_id=resolved, api_base=base
        )

    return _fetch


async def _unused_issuer_callable(*args: Any, **kwargs: Any) -> Any:
    """Fail-closed placeholder for issuer callables a source never uses."""

    from moonmind.auth.bound_acquisition import BOUND_DENIED, BoundAccessError

    raise BoundAccessError(
        BOUND_DENIED, "issuer callable is not used for this credential source"
    )


def build_bound_acquirer_for_connection(
    connection: Any,
    *,
    cache: Any | None = None,
    resolve_secret: Callable[[str], Awaitable[str | bytes] | str | bytes] | None = None,
    make_jwt: Callable[[bytes], Awaitable[str] | str] | None = None,
    http_post: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    get_installation: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    app_id: str = "",
    installation_id: str = "",
    api_base: str = "",
    key_secret_ref: str = "",
    expected_account: str = "",
    permitted_repositories: Sequence[str] = (),
) -> Any:
    """Build the production ``BoundCredentialAcquirer`` for one connection.

    ``None`` callables select the managed production defaults (Secrets-System
    resolution, RS256 JWT minting for the configured App ID, and
    current-contract installation-token HTTP against the configured host);
    tests inject fakes at the same seam. Only ``secret_ref`` and
    ``github_app`` sources enter bound acquisition here: ``github_resolver``
    stays on the historical ``resolve_github_credential`` path and unknown
    sources fail closed.
    """

    from moonmind.auth.bound_acquisition import (
        BOUND_DENIED,
        BoundAccessError,
        BoundCredentialAcquirer,
        BoundCredentialCache,
    )

    credential = getattr(connection, "credential", None)
    source = str(getattr(credential, "source", "") or "").strip()
    adapter_kind = _ADAPTER_KIND_FOR_SOURCE.get(source)
    if adapter_kind is None:
        raise BoundAccessError(
            BOUND_DENIED,
            f"unsupported credential source {source!r} for bound acquisition",
        )
    if source == "github_app":
        resolved_app_id = parse_app_id(
            str(getattr(credential, "app_ref", "") or ""), override=app_id
        )
        resolved_installation_id = parse_installation_id(
            str(getattr(credential, "installation_ref", "") or ""),
            override=installation_id,
        )
        resolved_base = str(api_base or "").strip() or github_api_base_for(
            str(getattr(connection, "endpoint_ref", "") or "")
        )
        issuer = issuer_for_connection(
            connection,
            resolve_secret=resolve_secret or default_resolve_secret_ref,
            make_jwt=make_jwt or default_make_jwt_for(resolved_app_id),
            http_post=http_post
            or default_http_post_for(
                installation_id=resolved_installation_id, api_base=resolved_base
            ),
            get_installation=get_installation
            or default_get_installation_for(
                installation_id=resolved_installation_id, api_base=resolved_base
            ),
            expected_account=expected_account,
            permitted_repositories=permitted_repositories,
            key_secret_ref=key_secret_ref,
        )
    else:  # secret_ref -> PAT through the connection's own SecretRef.
        issuer = issuer_for_connection(
            connection,
            resolve_secret=resolve_secret or default_resolve_secret_ref,
            make_jwt=make_jwt or _unused_issuer_callable,
            http_post=http_post or _unused_issuer_callable,
            get_installation=get_installation or _unused_issuer_callable,
            expected_account=expected_account,
            permitted_repositories=permitted_repositories,
            key_secret_ref=key_secret_ref,
        )

    def _issuer_for(kind: str) -> Any:
        if str(kind or "").strip() != adapter_kind:
            raise BoundAccessError(
                BOUND_DENIED,
                f"connection {getattr(connection, 'id', '?')!r} does not issue {kind!r}",
            )
        return issuer

    return BoundCredentialAcquirer(
        revision_reader=revision_reader_for({str(connection.id): connection}),
        issuer_for=_issuer_for,
        cache=cache if cache is not None else BoundCredentialCache(),
    )


async def acquire_bound_credential_for_connection(
    connection: Any,
    *,
    operations: Sequence[str],
    principal_ref: str,
    principal_scope: tuple[str, str | None] = ("system", None),
    execution_owner: str,
    operation_id: str = "",
    endpoint: str = "",
    route_id: str = "",
    repository_display: str = "",
    role: str = "reader",
    cache: Any | None = None,
    resolve_secret: Callable[[str], Awaitable[str | bytes] | str | bytes] | None = None,
    make_jwt: Callable[[bytes], Awaitable[str] | str] | None = None,
    http_post: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    get_installation: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    app_id: str = "",
    installation_id: str = "",
    api_base: str = "",
    key_secret_ref: str = "",
    expected_account: str = "",
    permitted_repositories: Sequence[str] = (),
) -> Any:
    """Acquire an ``AcquiredCredential`` for one admitted server-side operation.

    The admitted snapshot is explicit: non-empty operations (empty scope never
    becomes an omitted filter), a named principal, and the connection's own
    recorded revisions. Scope/revision-keyed caching, bounded refresh, and
    revocation/rotation checks stay in the bound acquirer; renewal keeps the
    same installation, repositories, operations, and intent with no PAT
    fallback.
    """

    from datetime import datetime, timezone

    from moonmind.auth.bound_acquisition import (
        BOUND_DENIED,
        AccessMode,
        AcquisitionRequest,
        BoundAccessError,
        SelectionSnapshot,
    )
    from moonmind.workflows.executions.repository_contract import normalize_scope

    admitted = tuple(
        str(op or "").strip().lower()
        for op in (operations or ())
        if str(op or "").strip()
    )
    if not admitted:
        raise BoundAccessError(
            BOUND_DENIED, "bound acquisition requires explicit operations"
        )
    if not (principal_ref or "").strip():
        raise BoundAccessError(
            BOUND_DENIED, "bound acquisition requires an admitted principal"
        )
    if not (execution_owner or "").strip():
        raise BoundAccessError(
            BOUND_DENIED, "bound acquisition requires an execution/use owner"
        )
    scope_type, scope_ref = normalize_scope(principal_scope[0], principal_scope[1])
    display = str(repository_display or "").strip() or str(connection.id)
    acquirer = build_bound_acquirer_for_connection(
        connection,
        cache=cache,
        resolve_secret=resolve_secret,
        make_jwt=make_jwt,
        http_post=http_post,
        get_installation=get_installation,
        app_id=app_id,
        installation_id=installation_id,
        api_base=api_base,
        key_secret_ref=key_secret_ref,
        expected_account=expected_account,
        permitted_repositories=permitted_repositories,
    )
    snapshot = SelectionSnapshot(
        principalRef=principal_ref.strip(),
        scopeType=scope_type,
        scopeRef=scope_ref,
        endpoint=str(endpoint or "").strip() or str(connection.endpoint_ref),
        routeId=str(route_id or "").strip() or display,
        repositoryDisplay=display,
        role=str(role or "").strip() or "reader",
        operations=admitted,
        accessMode=AccessMode.EXPLICIT,
        policyRevision=int(connection.policy_revision),
        connectionId=str(connection.id),
        connectionPolicyRevision=int(connection.policy_revision),
        credentialRevision=int(connection.credential_revision),
        selectionOrigin="github-app-wiring",
        authorizedAt=datetime.now(timezone.utc).isoformat(),
    )
    return await acquirer.acquire(
        AcquisitionRequest(
            snapshot=snapshot,
            execution_owner=execution_owner.strip(),
            operation_id=operation_id.strip() or f"op:{connection.id}",
        )
    )


async def acquire_bound_headers_for_connection(
    connection: Any,
    *,
    operations: Sequence[str],
    principal_ref: str,
    principal_scope: tuple[str, str | None] = ("system", None),
    execution_owner: str,
    operation_id: str = "",
    endpoint: str = "",
    route_id: str = "",
    repository_display: str = "",
    role: str = "reader",
    cache: Any | None = None,
    resolve_secret: Callable[[str], Awaitable[str | bytes] | str | bytes] | None = None,
    make_jwt: Callable[[bytes], Awaitable[str] | str] | None = None,
    http_post: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    get_installation: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    app_id: str = "",
    installation_id: str = "",
    api_base: str = "",
    key_secret_ref: str = "",
    expected_account: str = "",
    permitted_repositories: Sequence[str] = (),
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Acquire bound headers plus redaction values for one admitted operation.

    Builds the real GitHub REST headers inside the trusted ``use_now``
    boundary through the shared ``build_bound_http_headers`` wire shape, so
    PAT and App tokens share one implementation. The token is opaque: no
    length/format assertions. The returned ``AcquiredCredential`` is
    intentionally not released here: leader-issued material may be shared
    with the scope/revision-keyed cache entry, and clearing it would poison
    concurrent holders; the server-held caller drops its reference when the
    operation completes.
    """

    from moonmind.auth.github_app import build_bound_http_headers

    acquired = await acquire_bound_credential_for_connection(
        connection,
        operations=operations,
        principal_ref=principal_ref,
        principal_scope=principal_scope,
        execution_owner=execution_owner,
        operation_id=operation_id,
        endpoint=endpoint,
        route_id=route_id,
        repository_display=repository_display,
        role=role,
        cache=cache,
        resolve_secret=resolve_secret,
        make_jwt=make_jwt,
        http_post=http_post,
        get_installation=get_installation,
        app_id=app_id,
        installation_id=installation_id,
        api_base=api_base,
        key_secret_ref=key_secret_ref,
        expected_account=expected_account,
        permitted_repositories=permitted_repositories,
    )
    headers: dict[str, str] = {}
    redact: list[str] = []

    def _build(raw: bytes) -> None:
        headers.update(build_bound_http_headers(raw))
        redact.append(bytes(raw).decode("utf-8", errors="strict").strip())

    acquired.credential.use_now(_build)
    return dict(headers), tuple(redact)
