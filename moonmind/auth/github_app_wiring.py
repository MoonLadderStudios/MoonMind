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
        if source == "secret_ref":
            adapter_kind = "pat"
        elif source == "github_app":
            adapter_kind = "github_app"
        elif source == "github_resolver":
            adapter_kind = "pat"
        else:
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
            policy_revision=1,
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
    "expected_account_for",
    "fetch_installation_record",
    "github_api_base_for",
    "issuer_for_connection",
    "key_secret_ref_for",
    "make_github_app_jwt",
    "permitted_repositories_for",
    "post_installation_token",
    "revision_reader_for",
]
