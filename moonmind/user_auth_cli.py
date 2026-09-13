"""Thin user-client auth contract for surviving API/CLI consumers.

MoonLadderStudios/MoonMind#4126 (parent #4116, K4): the minimum documented
secure user-client acquisition/renewal path that surviving API/CLI
consumers use against the qualified shared session boundary
(``moonmind.security.omnigent_auth_qualification`` + ``#4121`` authority).
``#3939`` owns new run/status/logs commands; this module owns only the auth
contract and a thin conformance client so Keycloak removal is not blocked
on a new command suite.

Contract (see ``docs/Security/AuthenticationContracts.md`` §§4-6, 8, 10):

* User CLI authority is a MoonMind session (cookie or bearer), never a
  worker capability (execution-fanout / container-job), runtime JWT, model
  or repository credential. Presenting a machine credential as user login
  is rejected with ``user_auth_conflict`` (never silently accepted).
* Secrets never travel in argv, URLs, or global login caches. Session
  tokens are read from ``MOONMIND_SESSION_TOKEN`` or
  ``MOONMIND_SESSION_TOKEN_FILE`` (0600, same-host file), never from
  ``--token`` flags, query strings, or ``~/.moonmind`` style shared caches.
* Acquisition/renewal/logout go to the same-origin API only
  (``MOONMIND_URL`` / ``MOONMIND_PUBLIC_BASE_URL``). Cross-host redirects
  are rejected before any credential is sent.
* Unsupported flows (browser OIDC code exchange, trusted-proxy assertion,
  runtime delegation, worker-token login) fail closed with an actionable
  error naming the supported path instead of attempting them.
* Diagnostics are redacted via the ``#4120`` owner; raw tokens never appear
  in errors, logs, Temporal payloads, or bridge evidence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

SUPPORTED_GRANTS = ("session", "refresh")
USER_TOKEN_ENV = "MOONMIND_SESSION_TOKEN"
USER_TOKEN_FILE_ENV = "MOONMIND_SESSION_TOKEN_FILE"
BASE_URL_ENV_VARS = ("MOONMIND_URL", "MOONMIND_PUBLIC_BASE_URL")


class UserAuthCliError(RuntimeError):
    """Actionable user-client auth failure (no secret material)."""


@dataclass(frozen=True, slots=True)
class UserAuthConfig:
    """Resolved same-origin user-client configuration (no secrets)."""

    base_url: str

    @property
    def session_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/api/v1/auth/session"

    @property
    def refresh_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/api/v1/auth/refresh"

    @property
    def logout_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/api/v1/auth/logout"


def _redacted_config_diagnostics(config: UserAuthConfig) -> dict[str, Any]:
    from moonmind.security.auth_modes_4120 import redacted_diagnostics

    return redacted_diagnostics({"base_url": config.base_url})


def resolve_base_url(explicit: str | None = None) -> str:
    """Resolve the same-origin API base URL (no credential in URL)."""
    candidates: list[str] = []
    if explicit is not None and str(explicit).strip():
        candidates.append(str(explicit).strip())
    for env_key in BASE_URL_ENV_VARS:
        value = str(os.environ.get(env_key) or "").strip()
        if value:
            candidates.append(value)
    if not candidates:
        raise UserAuthCliError(
            "No API base URL configured; set MOONMIND_URL (or "
            "MOONMIND_PUBLIC_BASE_URL) to the same-origin API, e.g. "
            "MOONMIND_URL=http://127.0.0.1:7000"
        )
    base_url = candidates[0]
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UserAuthCliError(
            f"API base URL {base_url!r} must be an absolute http(s) URL; "
            "refusing to send user credentials elsewhere."
        )
    if parts.username or parts.password:
        raise UserAuthCliError(
            "API base URL must not embed credentials; pass the session token "
            "via MOONMIND_SESSION_TOKEN_FILE instead."
        )
    return base_url.rstrip("/")


def resolve_user_config(explicit_base_url: str | None = None) -> UserAuthConfig:
    return UserAuthConfig(base_url=resolve_base_url(explicit_base_url))


def load_session_token(
    *,
    env: Mapping[str, str] | None = None,
    explicit_file: str | Path | None = None,
) -> str:
    """Load the user session token without argv/URL/global-cache inputs.

    Precedence: explicit file argument (a per-invocation path, never a
    global cache) > ``MOONMIND_SESSION_TOKEN_FILE`` > ``MOONMIND_SESSION_TOKEN``.
    Rejects empty material, worker/machine-shaped tokens, and world-readable
    token files with actionable errors.
    """
    source = os.environ if env is None else env
    file_selector = (
        str(explicit_file).strip()
        if explicit_file is not None and str(explicit_file).strip()
        else str(source.get(USER_TOKEN_FILE_ENV) or "").strip()
    )
    if file_selector:
        path = Path(file_selector).expanduser()
        try:
            mode = path.stat().st_mode & 0o777
        except OSError as exc:
            raise UserAuthCliError(
                f"{USER_TOKEN_FILE_ENV}={file_selector!r} is unavailable: {exc}"
            ) from exc
        if mode & 0o077:
            raise UserAuthCliError(
                f"{USER_TOKEN_FILE_ENV}={file_selector!r} is readable by "
                f"group/other (mode {oct(mode)}); restrict it to 0600 before "
                "storing a session token there."
            )
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise UserAuthCliError(
                f"{USER_TOKEN_FILE_ENV}={file_selector!r} could not be read: {exc}"
            ) from exc
        if not value:
            raise UserAuthCliError(
                f"{USER_TOKEN_FILE_ENV}={file_selector!r} is empty; "
                "acquire a session through the documented login path first."
            )
        return _reject_machine_token_shape(value, source="token file")
    value = str(source.get(USER_TOKEN_ENV) or "").strip()
    if not value:
        raise UserAuthCliError(
            f"{USER_TOKEN_ENV} and {USER_TOKEN_FILE_ENV} are both unset; "
            "acquire a session through the documented login path first "
            "(never pass secrets via argv or URLs)."
        )
    return _reject_machine_token_shape(value, source="environment")


def _reject_machine_token_shape(token: str, *, source: str) -> str:
    """Refuse worker/runtime credentials as user login (no silent acceptance)."""
    lowered = token.strip().lower()
    if not token.strip():
        raise UserAuthCliError(f"User session token from {source} is empty.")
    # Execution-fanout and container-job capabilities are base64url.payload
    # + signature shapes or long opaque bearers minted for machine scope;
    # a MoonMind user session is a signed JWT (three segments). Rather than
    # sniffing lengths, reject tokens that verify as a machine capability
    # and reject obvious non-session shapes actionably.
    segments = token.strip().split(".")
    if len(segments) == 2:
        raise UserAuthCliError(
            "The presented credential is a machine capability (two-segment "
            "capability shape), not a MoonMind user session; user CLI "
            f"authority from {source} requires a user session token "
            "(worker_authorization_required)."
        )
    if lowered.startswith(("mm-proxy-token:", "omnigent-", "sk-")):
        raise UserAuthCliError(
            "The presented credential is a provider/proxy/runtime token, not "
            f"a MoonMind user session; user CLI authority from {source} "
            "requires a user session token."
        )
    _ = lowered
    return token.strip()


def build_auth_headers(token: str, *, as_cookie: bool = False) -> dict[str, str]:
    """Build transport headers for one request (caller redacts before logging)."""
    normalized = str(token or "").strip()
    if not normalized:
        raise UserAuthCliError("No user session token available for this request.")
    if as_cookie:
        from moonmind.security.omnigent_auth_qualification import MOONMIND_PROD_COOKIE

        return {"Cookie": f"{MOONMIND_PROD_COOKIE}={normalized}"}
    return {"Authorization": f"Bearer {normalized}"}


def assert_same_origin(url: str, *, base_url: str) -> str:
    """Reject cross-host redirects before any credential is sent."""
    base = urlsplit(base_url.rstrip("/"))
    target = urlsplit(url)

    def _port(parts) -> int:
        try:
            if parts.port is not None:
                return parts.port
        except ValueError:
            pass
        return 443 if parts.scheme == "https" else 80

    if (
        target.scheme.lower() != base.scheme.lower()
        or (target.hostname or "").lower() != (base.hostname or "").lower()
        or _port(target) != _port(base)
    ):
        raise UserAuthCliError(
            f"Refusing cross-origin user-auth request to {url!r}: user "
            f"sessions are same-origin with {base_url!r} only."
        )
    return url


def acquire_session(
    *,
    grant: str = "session",
    base_url: str | None = None,
    token: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Describe the supported acquisition request (no network, no secrets logged).

    This thin client does not implement IdP browser exchanges, proxy
    assertion, or Token refresh against upstream providers. ``grant`` is one
    of ``session`` (present an enrolled session token) or ``refresh``
    (renew via the session boundary with the current token). Anything else
    -- ``authorization_code``, ``client_credentials``, ``delegated``,
    ``worker_token`` -- is rejected actionably so Keycloak removal is not
    blocked on an invented flow.
    """
    normalized_grant = str(grant or "").strip().lower()
    if normalized_grant not in SUPPORTED_GRANTS:
        raise UserAuthCliError(
            f"Unsupported user-auth grant {grant!r}; supported grants are "
            f"{', '.join(SUPPORTED_GRANTS)}. Browser OIDC exchange happens "
            "in the browser against /api/v1/auth/oidc/*, proxy identity "
            "arrives per request in header mode, and worker capabilities are "
            "never user login."
        )
    config = resolve_user_config(base_url)
    endpoint = (
        config.session_endpoint if normalized_grant == "session" else config.refresh_endpoint
    )
    resolved_token = token.strip() if token and token.strip() else load_session_token(env=env)
    assert_same_origin(endpoint, base_url=config.base_url)
    headers = build_auth_headers(resolved_token)
    diagnostics = _redacted_config_diagnostics(config)
    return {
        "grant": normalized_grant,
        "endpoint": endpoint,
        "headers_present": sorted(headers.keys()),
        "diagnostics": diagnostics,
    }


__all__ = [
    "BASE_URL_ENV_VARS",
    "SUPPORTED_GRANTS",
    "USER_TOKEN_ENV",
    "USER_TOKEN_FILE_ENV",
    "UserAuthCliError",
    "UserAuthConfig",
    "acquire_session",
    "assert_same_origin",
    "build_auth_headers",
    "load_session_token",
    "resolve_base_url",
    "resolve_user_config",
]
