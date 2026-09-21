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


def _account_local_name(repository: str) -> str:
    """Reduce an owner-qualified name to the account-local repository name.

    GitHub's installation-token ``repositories`` filter takes repository
    *names* belonging to the installation account, not owner-qualified
    ``owner/name`` values or provider IDs.
    """

    return str(repository or "").strip().rsplit("/", 1)[-1].strip()


def split_repo_restrictions(
    repositories: Sequence[str],
) -> tuple[list[str], list[int]]:
    """Partition stored repository identities into names and numeric IDs.

    Owner-qualified values (``acme/repo``) become account-local names;
    all-digit values become ``repository_ids`` entries. Anything else that
    is non-empty is passed through as a name so the provider validates it.
    """

    names: list[str] = []
    ids: list[int] = []
    for raw in repositories or []:
        candidate = str(raw or "").strip()
        if not candidate:
            continue
        if candidate.isdigit():
            ids.append(int(candidate))
        else:
            local = _account_local_name(candidate)
            if local:
                names.append(local)
    return names, ids


def provider_repo_candidates(entry: Any) -> set[str]:
    """Enumerate the display forms one provider repository object may take.

    GitHub returns repository objects (``{"full_name": "acme/repo", ...}``)
    in installation-token responses; older fakes and narrow mocks may
    return plain strings. Every observed form is a match candidate so a
    real response is never misread as a narrowed scope.
    """

    candidates: set[str] = set()
    if isinstance(entry, Mapping):
        for key in ("full_name", "name"):
            value = str(entry.get(key) or "").strip()
            if value:
                candidates.add(value)
        raw_id = entry.get("id")
        if raw_id is not None and str(raw_id).strip():
            candidates.add(str(raw_id).strip())
    elif isinstance(entry, (bytes, bytearray)):
        text = bytes(entry).decode("utf-8", errors="replace").strip()
        if text:
            candidates.add(text)
    elif entry is not None:
        text = str(entry).strip()
        if text:
            candidates.add(text)
    return candidates


def permitted_repo_covered(permitted: str, candidates: set[str]) -> bool:
    """Check one allowlisted repository against provider-reported candidates."""

    wanted = str(permitted or "").strip()
    if not wanted or not candidates:
        return False
    if wanted in candidates:
        return True
    # Provider may report the account-local name while the allowlist stores
    # the owner-qualified form (or vice versa for legacy fakes).
    local = _account_local_name(wanted)
    return bool(local) and (
        local in candidates or any(c.endswith(f"/{local}") for c in candidates)
    )


def build_installation_token_request(
    *,
    operations: Sequence[str],
    repositories: Sequence[str],
) -> dict[str, Any]:
    """Serialize the exact installation-token restriction payload.

    Empty or unknown scope never serializes as omitted filters: both fail
    closed so the provider can never grant broad installation access.
    Repository identities serialize in GitHub's accepted form:
    owner-qualified names become account-local ``repositories`` entries and
    numeric provider IDs become ``repository_ids`` entries.
    """

    from moonmind.auth.bound_acquisition import BOUND_DENIED

    names, ids = split_repo_restrictions(repositories)
    if not names and not ids:
        raise _bound_error(
            BOUND_DENIED, "App issuance requires explicit repositories"
        )
    permissions = operations_to_permissions(operations)
    if not permissions:
        raise _bound_error(
            BOUND_DENIED, "App issuance requires explicit permissions"
        )
    payload: dict[str, Any] = {"permissions": dict(permissions)}
    if names:
        payload["repositories"] = list(dict.fromkeys(names))
    if ids:
        payload["repository_ids"] = list(dict.fromkeys(ids))
    return payload


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


@dataclass(frozen=True, slots=True)
class NormalizedInstallation:
    """Provider installation record in one comparable shape.

    Accepts the raw ``GET /app/installations/{id}`` payload (numeric
    ``id``/``app_id``, ``account`` object with ``login``, ``suspended_at``,
    ``repository_selection``) as well as the already-normalized synthetic
    shape used by tests and the setup boundary (``app_ref``,
    ``installation_ref``, scalar ``account``, ``repositories`` list,
    ``suspended`` flag). Repository entries may be plain names or provider
    repository objects; they normalize to comparable display forms.
    """

    app_key: str
    installation_key: str
    account: str
    repositories: tuple[str, ...]
    suspended: bool
    repository_selection: str = ""


def _numeric_key(value: Any) -> str:
    import re as _re

    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text
    match = _re.search(r"(\d+)\s*$", text)
    return match.group(1) if match else text


def normalize_installation_record(
    record: Mapping[str, Any],
    *,
    repositories: Sequence[Any] | None = None,
) -> NormalizedInstallation:
    """Normalize a provider installation record for authority checks."""

    if not isinstance(record, Mapping):
        raise _bound_error("BOUND_ISSUER_FAILED", "installation verification failed")
    raw_app_id = record.get("app_id", record.get("appId"))
    raw_app_ref = record.get("app_ref", record.get("appRef"))
    app_key = _numeric_key(raw_app_id) or _numeric_key(raw_app_ref)
    raw_installation_id = record.get("id", record.get("installation_id"))
    raw_installation_ref = record.get("installation_ref", record.get("installationRef"))
    installation_key = _numeric_key(raw_installation_id) or _numeric_key(
        raw_installation_ref
    )
    raw_account = record.get("account")
    if isinstance(raw_account, Mapping):
        account = str(
            raw_account.get("login", raw_account.get("name", "")) or ""
        ).strip()
    else:
        account = str(raw_account or "").strip()
    suspended = bool(
        record.get("suspended", False)
        or record.get("suspended_at", record.get("suspendedAt"))
        or record.get("suspended_by", record.get("suspendedBy"))
    )
    selection = str(
        record.get("repository_selection", record.get("repositorySelection", ""))
        or ""
    ).strip().lower()
    raw_repos = record.get("repositories") if repositories is None else repositories
    normalized_repos: list[str] = []
    for entry in raw_repos or ():
        for candidate in sorted(provider_repo_candidates(entry)):
            if candidate not in normalized_repos:
                normalized_repos.append(candidate)
    return NormalizedInstallation(
        app_key=app_key,
        installation_key=installation_key,
        account=account,
        repositories=tuple(normalized_repos),
        suspended=suspended,
        repository_selection=selection,
    )


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
        target_repository: str = "",
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
        self._target_repository = (target_repository or "").strip()

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
        # The production seam returns the raw provider payload (numeric
        # ``id``/``app_id``, ``account`` object, ``suspended_at``); the
        # setup boundary and tests return the normalized shape. Normalize
        # before comparing so a real acquisition is not rejected while
        # fakes pass.
        normalized = normalize_installation_record(record)
        if _numeric_key(self._app_ref) != normalized.app_key or (
            _numeric_key(self._installation_ref) != normalized.installation_key
        ):
            raise _bound_error(BOUND_DENIED, "installation does not match configured App")
        if self._expected_account and normalized.account != self._expected_account:
            raise _bound_error(BOUND_DENIED, "installation account is not permitted")
        if self._permitted:
            verified_set = set(normalized.repositories)
            if normalized.repository_selection == "all" and normalized.account:
                # The installation covers every repository of the verified
                # account: the account check above already binds scope, so
                # no per-repository containment applies.
                pass
            elif any(
                not permitted_repo_covered(repo, verified_set)
                for repo in self._permitted
            ):
                # Fail closed in the correct subset direction: every
                # connection-permitted repository must exist in the verified
                # installation set. An empty reported set therefore denies
                # a non-empty allowlist instead of passing it.
                raise _bound_error(BOUND_DENIED, "installation repositories are not permitted")
        if normalized.suspended:
            raise _bound_error(BOUND_DISABLED, "installation is suspended")
        return VerifiedInstallation(
            app_ref=normalized.app_key,
            installation_ref=normalized.installation_key,
            account=normalized.account,
            repositories=normalized.repositories,
            suspended=normalized.suspended,
        )

    def _request_repositories(self, binding: Any) -> list[str]:
        """Derive the token restriction from the admitted route (0647).

        The binding represents one admitted route: when it names a target
        repository inside the connection allowlist, the installation token
        is restricted to that repository instead of the whole allowlist.
        An explicit construction-time target wins over the binding display;
        anything outside the allowlist fails closed.
        """

        from moonmind.auth.bound_acquisition import BOUND_DENIED

        candidates: list[str] = []
        if self._target_repository:
            candidates.append(self._target_repository)
        display = ""
        if binding is not None:
            if isinstance(binding, Mapping):
                display = str(
                    binding.get("repositoryDisplay", binding.get("repository_display", ""))
                    or ""
                ).strip()
            else:
                display = str(getattr(binding, "repository_display", "") or "").strip()
        if display:
            candidates.append(display)
        for candidate in candidates:
            if candidate not in self._permitted:
                raise _bound_error(
                    BOUND_DENIED,
                    "requested repository is outside the connection allowlist",
                )
            return [candidate]
        return list(self._permitted)

    async def __call__(self, binding: Any) -> Any:
        from moonmind.auth.bound_acquisition import BOUND_DENIED, Issuance

        operations = tuple(getattr(binding, "operations", ()) or ())
        if not operations:
            raise _bound_error(BOUND_DENIED, "App issuance requires explicit operations")
        # Selected repositories follow the admitted route: the binding's
        # target repository inside the connection allowlist, else the full
        # permitted set. Empty scope never becomes an omitted filter.
        payload = build_installation_token_request(
            operations=operations, repositories=self._request_repositories(binding)
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
        # The provider returns repository objects, so compare against every
        # observed display form instead of stringifying whole mappings.
        returned_candidates: set[str] = set()
        for entry in response.get("repositories") or []:
            returned_candidates.update(provider_repo_candidates(entry))
        requested_names = set(payload.get("repositories") or ())
        requested_ids = {str(value) for value in payload.get("repository_ids") or ()}
        if (requested_names or requested_ids) and any(
            not permitted_repo_covered(repo, returned_candidates)
            for repo in list(requested_names) + list(requested_ids)
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


def build_bound_git_env(
    token_material: bytes,
    *,
    repository: str,
    endpoint: str = "https://github.com",
) -> dict[str, Any]:
    """Build a bound Git env projection for one admitted repository.

    ``repository`` is the owner-qualified ``owner/name`` identity and
    ``endpoint`` the trusted deployment host: the remote is constructed
    from those two values so an ``owner/name`` value can never be
    misread as a host. Authentication rides the shared in-memory
    credential-helper contract (no ``GIT_USERNAME``/``GIT_PASSWORD``
    inputs, which Git does not consume, and no secret embedded in the
    URL); the token only enters the helper's environment with redaction.
    """

    from urllib.parse import urlsplit

    if not (repository or "").strip():
        raise ValueError("repository is required for bound Git use")
    if not token_material or not bytes(token_material).strip():
        raise ValueError("credential material is empty")
    raw = bytes(token_material)
    if b"\n" in raw or b"\r" in raw:
        raise ValueError("credential material is invalid")
    token = raw.decode("utf-8", errors="strict").strip()
    owner, sep, name = repository.strip().strip("/").partition("/")
    if not sep or not owner.strip() or not name.strip() or "/" in name.strip():
        raise ValueError("repository must be an owner/name identity")
    owner, name = owner.strip(), name.strip()
    candidate = str(endpoint or "").strip() or "https://github.com"
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise ValueError("endpoint is not a valid Git host") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint is not a valid Git host")
    if parsed.username or parsed.password:
        raise ValueError("endpoint must not embed credentials")
    host = parsed.hostname.lower()
    base = f"{parsed.scheme.lower()}://{host}"
    if parsed.port:
        base += f":{parsed.port}"
    remote = f"{base}/{owner}/{name}"

    from moonmind.workflows.temporal.runtime.git_auth import (
        build_github_token_git_environment,
    )

    env = build_github_token_git_environment(token, host=host)
    return {
        "env": env,
        "credential_url": remote,
        "redact": [token],
        "repository": f"{owner}/{name}",
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
    "NormalizedInstallation",
    "VerifiedInstallation",
    "build_bound_git_env",
    "build_bound_http_headers",
    "build_gh_env",
    "build_installation_token_request",
    "normalize_installation_record",
    "operations_to_permissions",
    "parse_installation_token_response",
    "permitted_repo_covered",
    "provider_repo_candidates",
    "redacted_diagnostic",
    "split_repo_restrictions",
]
