"""Production GitHub App installation issuance through the bound-acquirer seam.

MoonLadderStudios/MoonMind#4022: plug the existing ``CredentialIssuer``
boundary (``moonmind.auth.bound_acquisition``) with a real App adapter.
No separate credential store, publisher, or lease/receipt service is added:
caching, bounded refresh, revocation/rotation checks, and
exact-effect reconciliation stay in the bound acquirer and the existing
connection/publication machinery.

Contracts follow the current official GitHub endpoints:

* setup URL flow: ``about-the-setup-url``
* installation access tokens: ``generating-an-installation-access-token-for-a-github-app``

All network/JWT operations ride injectable callables so unit scope uses
local provider-response fakes with synthetic secrets. Tokens are opaque
byte strings: no fixed-length PAT assertions and no local JWT-decode
authorization claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

SETUP_URL_DOC = (
    "https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url"
)
INSTALLATION_TOKEN_DOC = (
    "https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/"
    "generating-an-installation-access-token-for-a-github-app"
)


def _bound_error(code: str, message: str) -> ValueError:
    from moonmind.auth.bound_acquisition import BoundAccessError

    return BoundAccessError(code, message)


# ---------------------------------------------------------------------------
# Operation -> GitHub permission restriction mapping.
# ---------------------------------------------------------------------------

#: Admitted MoonMind operations mapped to the minimal GitHub permission set
#: required for the installation-token request. Unknown operations fail
#: closed: they never serialize as omitted permission filters.
_OPERATION_PERMISSIONS: dict[str, dict[str, str]] = {
    "read": {"contents": "read", "metadata": "read"},
    "write": {"contents": "write"},
    "branch_write": {"contents": "write"},
    "lock": {"issues": "read"},
    "review_request": {"pull_requests": "read"},
    "merge_request": {"pull_requests": "write", "contents": "write"},
}


def operations_to_permissions(operations: Sequence[str]) -> dict[str, str]:
    """Map admitted operations to explicit GitHub permissions (fail closed)."""

    from moonmind.auth.bound_acquisition import BOUND_DENIED

    merged: dict[str, str] = {}
    rank = {"read": 0, "write": 1}
    for raw in operations:
        op = str(raw or "").strip().lower()
        if not op:
            raise _bound_error(BOUND_DENIED, "empty operation in App issuance scope")
        mapping = _OPERATION_PERMISSIONS.get(op)
        if mapping is None:
            raise _bound_error(BOUND_DENIED, f"unknown operation {op!r} for App issuance")
        for permission, level in mapping.items():
            current = merged.get(permission)
            if current is None or rank.get(level, 0) > rank.get(current, 0):
                merged[permission] = level
    if not merged:
        raise _bound_error(BOUND_DENIED, "App issuance requires explicit operations")
    return merged


def build_installation_token_request(
    *,
    operations: Sequence[str],
    repositories: Sequence[str],
) -> dict[str, Any]:
    """Serialize the exact installation-token restriction payload.

    Empty or unknown scope never serializes as omitted filters: both fail
    closed so the provider can never grant broad installation access.
    """

    from moonmind.auth.bound_acquisition import BOUND_DENIED

    repos = [str(name or "").strip() for name in repositories or []]
    repos = [name for name in repos if name]
    if not repos:
        raise _bound_error(
            BOUND_DENIED, "App issuance requires explicit repositories"
        )
    permissions = operations_to_permissions(operations)
    if not permissions:
        raise _bound_error(
            BOUND_DENIED, "App issuance requires explicit permissions"
        )
    return {"repositories": list(repos), "permissions": dict(permissions)}


def parse_installation_token_response(payload: Mapping[str, Any]) -> tuple[bytes, datetime | None]:
    """Validate the provider token response; treat the token as opaque."""

    from moonmind.auth.bound_acquisition import BOUND_ISSUER_FAILED

    if not isinstance(payload, Mapping):
        raise _bound_error(BOUND_ISSUER_FAILED, "invalid installation-token response")
    token = payload.get("token")
    if not isinstance(token, str) or not token.strip():
        raise _bound_error(BOUND_ISSUER_FAILED, "installation-token response has no token")
    # Opaque: no length/format assertion, no local decode.
    material = token.strip().encode()
    expires_at: datetime | None = None
    raw_expiry = payload.get("expires_at") or payload.get("expiresAt")
    if raw_expiry:
        try:
            parsed = datetime.fromisoformat(str(raw_expiry).strip())
        except ValueError as exc:
            raise _bound_error(
                BOUND_ISSUER_FAILED, "installation-token expiry is invalid"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        expires_at = parsed
        if expires_at <= datetime.now(timezone.utc):
            raise _bound_error(
                BOUND_ISSUER_FAILED, "installation-token response is expired"
            )
    else:
        raise _bound_error(
            BOUND_ISSUER_FAILED, "installation-token response has no expiry"
        )
    return material, expires_at


def _provider_error_code(status: int | None, *, suspended: bool = False) -> str:
    from moonmind.auth.bound_acquisition import (
        BOUND_DISABLED,
        BOUND_ISSUER_FAILED,
        BOUND_REVOKED,
        BOUND_UNAVAILABLE,
    )

    if suspended:
        return BOUND_DISABLED
    if status in (401, 403):
        return BOUND_REVOKED
    if status == 404:
        return BOUND_UNAVAILABLE
    return BOUND_ISSUER_FAILED


@dataclass(frozen=True, slots=True)
class VerifiedInstallation:
    app_ref: str
    installation_ref: str
    account: str
    repositories: tuple[str, ...]
    suspended: bool = False


class GitHubAppAdapter:
    """Production installation-token issuer on the ``CredentialIssuer`` boundary.

    ``kind`` is ``"github_app"`` so bound-acquirer bindings record the real
    adapter. Renewal keeps the same installation/repositories/operations via
    the acquirer's renewal key; there is no PAT fallback and no exactly-once
    promise (duplicate reconciliation stays in the acquirer).
    """

    kind = "github_app"

    def __init__(
        self,
        *,
        app_ref: str,
        installation_ref: str,
        key_secret_ref: str,
        resolve_key: Callable[[str], Awaitable[bytes] | bytes],
        make_jwt: Callable[[bytes], Awaitable[str] | str],
        http_post: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
        get_installation: Callable[..., Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
        expected_account: str = "",
        permitted_repositories: Sequence[str] = (),
    ) -> None:
        if not (app_ref or "").strip() or not (installation_ref or "").strip():
            raise ValueError("GitHubAppAdapter requires explicit App/installation refs")
        if not (key_secret_ref or "").strip():
            raise ValueError("GitHubAppAdapter requires a managed key secret ref")
        self._app_ref = app_ref.strip()
        self._installation_ref = installation_ref.strip()
        self._key_secret_ref = key_secret_ref.strip()
        self._resolve_key = resolve_key
        self._make_jwt = make_jwt
        self._http_post = http_post
        self._get_installation = get_installation
        self._expected_account = (expected_account or "").strip()
        self._permitted = tuple(r.strip() for r in permitted_repositories if str(r).strip())

    async def _await(self, value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value

    async def _verified_installation(self, jwt: str) -> VerifiedInstallation:
        from moonmind.auth.bound_acquisition import BOUND_DENIED, BOUND_DISABLED

        try:
            record = await self._await(self._get_installation(jwt=jwt))
        except Exception as exc:
            if isinstance(exc, ValueError) and getattr(exc, "code", ""):
                raise
            raise _bound_error(
                "BOUND_ISSUER_FAILED",
                "installation verification failed",
            ) from exc
        if not isinstance(record, Mapping):
            raise _bound_error("BOUND_ISSUER_FAILED", "installation verification failed")
        app_ref = str(record.get("app_ref") or "").strip()
        installation_ref = str(record.get("installation_ref") or "").strip()
        account = str(record.get("account") or "").strip()
        repos = tuple(
            str(name or "").strip()
            for name in (record.get("repositories") or [])
            if str(name or "").strip()
        )
        suspended = bool(record.get("suspended", False))
        if app_ref != self._app_ref or installation_ref != self._installation_ref:
            raise _bound_error(BOUND_DENIED, "installation does not match configured App")
        if self._expected_account and account != self._expected_account:
            raise _bound_error(BOUND_DENIED, "installation account is not permitted")
        if self._permitted and any(repo not in self._permitted for repo in repos if repo):
            # Provider reports repositories outside the permitted set: fail
            # closed rather than acquiring across them.
            raise _bound_error(BOUND_DENIED, "installation repositories are not permitted")
        if suspended:
            raise _bound_error(BOUND_DISABLED, "installation is suspended")
        return VerifiedInstallation(
            app_ref=app_ref,
            installation_ref=installation_ref,
            account=account,
            repositories=repos,
            suspended=suspended,
        )

    async def __call__(self, binding: Any) -> Any:
        from moonmind.auth.bound_acquisition import BOUND_DENIED, Issuance

        operations = tuple(getattr(binding, "operations", ()) or ())
        if not operations:
            raise _bound_error(BOUND_DENIED, "App issuance requires explicit operations")
        # Selected repositories are the permitted set configured for this
        # connection; empty scope never becomes an omitted filter.
        payload = build_installation_token_request(
            operations=operations, repositories=list(self._permitted)
        )
        key_material = await self._await(self._resolve_key(self._key_secret_ref))
        if not isinstance(key_material, (bytes, bytearray)) or not bytes(key_material).strip():
            raise _bound_error(BOUND_DENIED, "App signing key is unavailable")
        jwt = await self._await(self._make_jwt(bytes(key_material)))
        if not str(jwt or "").strip():
            raise _bound_error(BOUND_DENIED, "App JWT issuance failed")
        verified = await self._verified_installation(str(jwt))
        try:
            response = await self._await(self._http_post(jwt=str(jwt), payload=payload))
        except Exception as exc:
            code = getattr(exc, "code", None)
            status = getattr(exc, "status", getattr(exc, "status_code", None))
            if isinstance(code, str) and code.startswith("BOUND_"):
                raise
            raise _bound_error(
                _provider_error_code(status if isinstance(status, int) else None),
                "installation-token issuance failed",
            ) from exc
        if not isinstance(response, Mapping):
            raise _bound_error("BOUND_ISSUER_FAILED", "installation-token issuance failed")
        material, expires_at = parse_installation_token_response(response)
        # Provider limits are honored without widening: the acquirer's
        # post-issuance scope check rejects narrowed authority; here we
        # additionally require the response to name the requested repos.
        returned_repos = {
            str(name or "").strip()
            for name in (response.get("repositories") or [])
            if str(name or "").strip()
        }
        if returned_repos and any(
            repo not in returned_repos for repo in self._permitted if self._permitted
        ):
            # Narrowed repository set: surface scope mismatch so the caller
            # retries within authority instead of widening.
            raise _bound_error(
                "BOUND_SCOPE_MISMATCH", "provider narrowed installation repositories"
            )
        returned_permissions = response.get("permissions")
        if isinstance(returned_permissions, Mapping):
            requested = set(payload["permissions"].keys())
            returned_names = set(str(name) for name in returned_permissions.keys())
            if not requested.issubset(returned_names):
                raise _bound_error(
                    "BOUND_SCOPE_MISMATCH", "provider narrowed installation permissions"
                )
        _ = verified
        scope = ",".join(sorted({str(op).strip().lower() for op in operations if str(op).strip()}))
        return Issuance(
            identity=f"installation:{self._installation_ref}",
            scope=scope,
            expires_at=expires_at,
            material=material,
        )


# ---------------------------------------------------------------------------
# Bound consumer helpers (real HTTP/Git/gh shapes, secret-safe).
# ---------------------------------------------------------------------------


def build_bound_http_headers(token_material: bytes) -> dict[str, str]:
    """Build real GitHub HTTP headers from opaque token bytes (trusted boundary)."""

    if not token_material or not bytes(token_material).strip():
        raise ValueError("credential material is empty")
    token = bytes(token_material).decode("utf-8", errors="strict").strip()
    if not token or "\n" in token or "\r" in token:
        raise ValueError("credential material is invalid")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def build_bound_git_env(token_material: bytes, *, repository: str) -> dict[str, Any]:
    """Build a bound Git env projection for one admitted repository."""

    if not (repository or "").strip():
        raise ValueError("repository is required for bound Git use")
    if not token_material or not bytes(token_material).strip():
        raise ValueError("credential material is empty")
    raw = bytes(token_material)
    if b"\n" in raw or b"\r" in raw:
        raise ValueError("credential material is invalid")
    token = raw.decode("utf-8", errors="strict").strip()
    repo = repository.strip()
    return {
        "env": {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_USERNAME": "x-access-token",
            "GIT_PASSWORD": token,
        },
        "credential_url": f"https://x-access-token@{repo}",
        "redact": [token],
        "repository": repo,
    }


def build_gh_env(token_material: bytes) -> dict[str, Any]:
    """Build the server-held ``gh`` env projection with redaction values."""

    if not token_material or not bytes(token_material).strip():
        raise ValueError("credential material is empty")
    raw = bytes(token_material)
    if b"\n" in raw or b"\r" in raw:
        raise ValueError("credential material is invalid")
    token = raw.decode("utf-8", errors="strict").strip()
    return {"env": {"GH_TOKEN": token, "GITHUB_TOKEN": token}, "redact": [token]}


def redacted_diagnostic(binding: Any) -> dict[str, Any]:
    """Render a metadata-only diagnostic for an App binding (never token material)."""

    def _field(name: str, default: Any = "") -> Any:
        return getattr(binding, name, default)

    return {
        "bindingDigest": _field("binding_digest"),
        "connectionId": _field("connection_id"),
        "operationId": _field("operation_id"),
        "adapterKind": _field("adapter_kind"),
        "routeId": _field("route_id"),
    }


__all__ = [
    "INSTALLATION_TOKEN_DOC",
    "SETUP_URL_DOC",
    "GitHubAppAdapter",
    "VerifiedInstallation",
    "build_bound_git_env",
    "build_bound_http_headers",
    "build_gh_env",
    "build_installation_token_request",
    "operations_to_permissions",
    "parse_installation_token_response",
    "redacted_diagnostic",
]
