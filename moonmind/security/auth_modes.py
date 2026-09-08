"""Canonical MoonMind application-authentication contract (K3 production core).

Source issue: MoonLadderStudios/MoonMind#4120 (parent #4116; depends on
#4117/#4118; integrates the migration contract from #4119).
Plan coverage: K3 configuration and deployment defaults in
``docs/tmp/KeycloakRemovalPlan.md``. Declarative authority remains
``docs/Security/AuthenticationContracts.md``; this module is the executable
production contract behind it.

Scope and non-goals
-------------------
This module owns small, hermetic, dependency-light production decisions so
unit tests stay hermetic (no DB, no network, no upstream import):

- one ``AUTH_PROVIDER`` selector (``accounts``/``oidc``/``header``/``disabled``);
  retired ``keycloak``/``default``/``google``/``local`` selectors fail fast
  with migration guidance, never silently translate or fall back;
- explicit control-plane resolution that never reads ``OMNIGENT_AUTH_*`` or
  any other ambient runtime authority;
- a versioned, persisted migration decision distinguishing a genuinely fresh
  database from a pre-cutover installation with omitted variables (no
  ``no-account-has-a-password`` heuristics);
- durable deployment-owned session-signing secrets with concurrent-bootstrap
  single-generation, placeholder rejection, and rotation support;
- deployment-boundary exposure validation for explicit local (``disabled``)
  mode, including host-port bindings, alternate listeners, and proxy bypasses;
- public base-URL / trusted-proxy / callback-origin / cookie policy
  validation where untrusted forwarded headers can never decide redirects or
  secure-cookie policy;
- distinguishable infrastructure / authentication / operator-setup readiness
  plus redacted, fail-closed diagnostics.

What this module is not: a new secret-management service, an automatic
public-deployment mechanism, a second account database, or permission to
weaken cookie/security rules for convenience. Durable session/revocation,
OIDC protocol flows, and the API cutover live behind the K2 qualification
adapter (``omnigent_auth_qualification.py``) and K4 wiring; this module
supplies the configuration authority those layers consume.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# R1: one selector, correctly scoped here (not under an OIDC-specific owner)
# ---------------------------------------------------------------------------

SUPPORTED_AUTH_MODES: tuple[str, ...] = ("accounts", "oidc", "header", "disabled")
RETIRED_AUTH_SELECTORS: tuple[str, ...] = ("keycloak", "default", "google", "local")

_RETIRED_GUIDANCE: dict[str, str] = {
    "keycloak": (
        "AUTH_PROVIDER='keycloak' was removed with the bundled Keycloak "
        "integration (#4129). Choose 'accounts' for built-in accounts, 'oidc' "
        "with OIDC_ISSUER_URL/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET for generic "
        "external OIDC, 'header' behind a trusted proxy, or explicitly "
        "restricted local 'disabled'. See "
        "docs/Security/AuthenticationContracts.md."
    ),
    "default": (
        "AUTH_PROVIDER='default' was removed. It is not an alias for any "
        "supported mode. Choose an explicit supported mode ('accounts', "
        "'oidc', 'header', or explicitly restricted local 'disabled')."
    ),
    "google": (
        "AUTH_PROVIDER='google' was removed. Use generic 'oidc' with explicit "
        "issuer/client configuration."
    ),
    "local": (
        "AUTH_PROVIDER='local' was retired. Use explicitly restricted local "
        "'disabled' with loopback/trusted-ingress evidence, or 'accounts' "
        "for built-in accounts."
    ),
}


class AuthConfigError(ValueError, RuntimeError):
    """Fail-closed authentication configuration error with migration guidance.

    Subclasses both ``ValueError`` (new canonical contract) and
    ``RuntimeError`` (historical ``OIDCSettings.validate_auth_provider``
    contract) so existing startup/test call sites observing either type keep
    failing closed on the same error.
    """


def validate_auth_provider(mode: str | None) -> str:
    """Validate a MoonMind ``AUTH_PROVIDER`` selector, failing closed.

    Returns the normalized lowercase selector. Retired and unknown selectors
    raise :class:`AuthConfigError` with migration guidance; they are never
    translated to another mode and there is no implicit provider fallback.
    """
    normalized = (mode or "").strip().lower()
    if normalized in SUPPORTED_AUTH_MODES:
        return normalized
    if normalized in _RETIRED_GUIDANCE:
        raise AuthConfigError(_RETIRED_GUIDANCE[normalized])
    raise AuthConfigError(
        f"Unknown AUTH_PROVIDER={mode!r}. Supported modes are "
        f"{', '.join(SUPPORTED_AUTH_MODES)}. See "
        "docs/Security/AuthenticationContracts.md."
    )


def retired_guidance(selector: str) -> str | None:
    """Return migration guidance for a retired selector, else ``None``."""
    return _RETIRED_GUIDANCE.get((selector or "").strip().lower())


# Ambient runtime authorities that must never select MoonMind behavior.
_RUNTIME_AMBIENT_PREFIXES: tuple[str, ...] = ("OMNIGENT_AUTH_", "OMNIGENT_ACCOUNTS_", "OMNIGENT_OIDC_")


def resolve_auth_mode(explicit: str | None = None, *, env: Mapping[str, str] | None = None) -> str:
    """Resolve the MoonMind auth mode from explicit input or MoonMind env.

    Only ``AUTH_PROVIDER`` (or the explicit argument) is consulted. Any
    ``OMNIGENT_AUTH_*`` / ``OMNIGENT_ACCOUNTS_*`` / ``OMNIGENT_OIDC_*`` ambient
    values are ignored by construction; they belong to the runtime server and
    must not accidentally configure MoonMind (R2). The resolved value is
    validated with :func:`validate_auth_provider`, so omitted values fall
    through to the caller-supplied default handling, never to a competing
    enable switch or alias layer.
    """
    source = os.environ if env is None else env
    raw = explicit if explicit is not None else source.get("AUTH_PROVIDER")
    if raw is None:
        raise AuthConfigError(
            "AUTH_PROVIDER is not set. Choose an explicit supported mode "
            f"({', '.join(SUPPORTED_AUTH_MODES)}); fresh installs select "
            "'accounts' through the normal production path."
        )
    return validate_auth_provider(raw)


def ambient_runtime_settings_present(env: Mapping[str, str] | None = None) -> list[str]:
    """List runtime ambient keys present (for diagnostics only, never inputs)."""
    source = os.environ if env is None else env
    return sorted(
        key for key in source if key.startswith(_RUNTIME_AMBIENT_PREFIXES) and source[key]
    )


# ---------------------------------------------------------------------------
# R2: explicit control-plane configuration (never ambient runtime state)
# ---------------------------------------------------------------------------

MOONMIND_PROD_COOKIE = "__Host-mm_session"
MOONMIND_DEV_COOKIE = "mm_session_dev"
MOONMIND_TOKEN_ISSUER = "moonmind-control-plane"
MOONMIND_TOKEN_AUDIENCE = "moonmind-browser-session"


@dataclass(frozen=True)
class ControlPlaneAuthConfig:
    """Explicit MoonMind control-plane authentication configuration.

    Constructed only from MoonMind-owned inputs (selector, cookie policy,
    durable secret). It never reads ``OMNIGENT_AUTH_*`` or any ambient
    runtime environment. Callers pass this object into the K2 qualification
    boundary instead of letting that boundary observe process environment.
    """

    mode: str
    cookie_name: str
    cookie_secret: bytes
    session_ttl_seconds: int = 8 * 3600
    token_issuer: str = MOONMIND_TOKEN_ISSUER
    token_audience: str = MOONMIND_TOKEN_AUDIENCE
    require_secure_cookies: bool = True

    def __post_init__(self) -> None:
        validate_auth_provider(self.mode)
        if not isinstance(self.cookie_secret, (bytes, bytearray)) or len(self.cookie_secret) < 32:
            raise AuthConfigError("cookie_secret must be at least 32 bytes")
        if self.cookie_name in frozenset({"__Host-ap_session", "ap_session"}):
            raise AuthConfigError(
                f"cookie_name={self.cookie_name!r} collides with the upstream "
                "runtime session cookie; control-plane and runtime cookies "
                "must be distinct."
            )
        if not self.token_issuer or not self.token_audience:
            raise AuthConfigError("token_issuer and token_audience must be non-empty")
        if self.session_ttl_seconds <= 0:
            raise AuthConfigError("session_ttl_seconds must be positive")

    def to_qualification_kwargs(self) -> dict[str, Any]:
        """Return kwargs for the K2 ``MoonmindAuthConfig`` boundary."""
        return {
            "mode": validate_auth_provider(self.mode),
            "cookie_name": self.cookie_name,
            "cookie_secret": bytes(self.cookie_secret),
            "session_ttl_seconds": self.session_ttl_seconds,
            "token_issuer": self.token_issuer,
            "token_audience": self.token_audience,
            "require_secure_cookies": self.require_secure_cookies,
        }


def resolve_control_plane_config(
    *,
    mode: str,
    cookie_secret: bytes,
    cookie_name: str | None = None,
    session_ttl_seconds: int = 8 * 3600,
    require_secure_cookies: bool = True,
) -> ControlPlaneAuthConfig:
    """Build an explicit control-plane config from MoonMind-owned inputs.

    ``mode`` and ``cookie_secret`` are required caller-supplied values (the
    secret comes from :func:`resolve_session_secret`). No environment is read
    here, so contradictory ambient ``OMNIGENT_AUTH_*`` values cannot leak in.
    """
    normalized = validate_auth_provider(mode)
    return ControlPlaneAuthConfig(
        mode=normalized,
        cookie_name=cookie_name or MOONMIND_PROD_COOKIE,
        cookie_secret=cookie_secret,
        session_ttl_seconds=session_ttl_seconds,
        require_secure_cookies=require_secure_cookies,
    )


# ---------------------------------------------------------------------------
# R3: versioned, persisted migration decision (fresh vs pre-cutover)
# ---------------------------------------------------------------------------

MIGRATION_DECISION_VERSION = 1
MIGRATION_DECISIONS: tuple[str, ...] = ("accounts", "oidc", "header", "disabled", "pending")


@dataclass(frozen=True)
class AuthMigrationDecision:
    """Versioned, persisted operator migration decision (#4119 contract).

    ``decision`` names the chosen target mode, or ``"pending"`` when the
    operator has not chosen yet. Populated databases with omitted, legacy, or
    contradictory configuration stop actionably while the decision is
    ``"pending"``; they never silently create a new owner.
    """

    decision: str = "pending"
    version: int = MIGRATION_DECISION_VERSION
    decided_by: str = ""
    decided_via: str = "operator-choice"

    def __post_init__(self) -> None:
        if self.version != MIGRATION_DECISION_VERSION:
            raise AuthConfigError(
                f"Unsupported auth migration decision version {self.version!r}; "
                f"expected {MIGRATION_DECISION_VERSION}."
            )
        if self.decision not in MIGRATION_DECISIONS:
            raise AuthConfigError(
                f"Unknown migration decision {self.decision!r}; expected one of "
                f"{', '.join(MIGRATION_DECISIONS)}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "decision": self.decision,
            "decided_by": self.decided_by,
            "decided_via": self.decided_via,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AuthMigrationDecision:
        try:
            return cls(
                decision=str(payload.get("decision", "pending")),
                version=int(payload.get("version", MIGRATION_DECISION_VERSION)),
                decided_by=str(payload.get("decided_by", "") or ""),
                decided_via=str(payload.get("decided_via", "operator-choice") or "operator-choice"),
            )
        except (TypeError, ValueError) as exc:
            raise AuthConfigError(f"Invalid auth migration decision payload: {exc}") from exc


def save_migration_decision(path: str | Path, decision: AuthMigrationDecision) -> Path:
    """Persist a migration decision atomically as JSON (deployment-owned)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp" if target.suffix else ".tmp")
    tmp.write_text(json.dumps(decision.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def load_migration_decision(path: str | Path | None) -> AuthMigrationDecision | None:
    """Load a persisted decision, or ``None`` when absent. Corrupt files fail closed."""
    if not path:
        return None
    target = Path(path)
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthConfigError(
            f"Auth migration decision at {target} is unreadable or corrupt; "
            "require an explicit protected operator choice before proceeding."
        ) from exc
    if not isinstance(payload, dict):
        raise AuthConfigError(f"Auth migration decision at {target} must be a JSON object.")
    return AuthMigrationDecision.from_dict(payload)


@dataclass(frozen=True)
class BootstrapDecision:
    """Outcome of :func:`decide_auth_bootstrap`."""

    action: str  # "proceed" | "require-operator-choice"
    mode: str
    fresh_install: bool
    reason: str


def decide_auth_bootstrap(
    *,
    mode: str,
    mode_explicitly_set: bool,
    db_has_users: bool,
    migration_decision: AuthMigrationDecision | None,
) -> BootstrapDecision:
    """Decide whether startup may proceed or must stop for an operator choice.

    - Genuinely fresh databases (``db_has_users is False``) proceed. An
      omitted selector selects ``accounts`` through the same production code
      as explicit accounts mode (the caller maps the omitted marker before
      calling, or passes ``mode="accounts"`` with
      ``mode_explicitly_set=False``).
    - Populated databases proceed only with an explicit selector **and** a
      matching persisted non-``pending`` migration decision. Omitted, legacy,
      or contradictory configurations stop actionably with
      ``action="require-operator-choice"`` without modifying owners.
    - Heuristics such as "no account has a password" are never consulted.
    """
    normalized = validate_auth_provider(mode)
    decided = migration_decision.decision if migration_decision else "pending"
    if not db_has_users:
        # Fresh path: the normal fresh install selects accounts through the
        # same production code as explicit accounts mode.
        effective = normalized
        if not mode_explicitly_set and normalized == "disabled":
            # An omitted selector that fell through to the legacy disabled
            # default must not silently become local mode on a fresh DB; the
            # fresh path selects accounts explicitly.
            effective = "accounts"
        return BootstrapDecision(
            action="proceed", mode=effective, fresh_install=True,
            reason="fresh database: explicit production mode selected",
        )
    # Populated database: require a protected operator choice unless an
    # explicit selector agrees with a persisted non-pending decision.
    if (
        mode_explicitly_set
        and decided != "pending"
        and decided == normalized
    ):
        return BootstrapDecision(
            action="proceed", mode=normalized, fresh_install=False,
            reason="populated database with explicit selector and matching persisted migration decision",
        )
    return BootstrapDecision(
        action="require-operator-choice", mode=normalized, fresh_install=False,
        reason=(
            "populated database with omitted, legacy, or contradictory "
            "configuration: refusing to silently disable auth, create a new "
            "owner, or lock out existing users. Record an explicit versioned "
            f"migration decision (current: {decided!r}) matching the intended "
            f"AUTH_PROVIDER={normalized!r} before startup proceeds."
        ),
    )


# ---------------------------------------------------------------------------
# R4: durable deployment-owned signing/session secrets
# ---------------------------------------------------------------------------

INSECURE_SECRET_MARKERS: tuple[str, ...] = (
    "devsecret",
    "test_jwt_secret_key",
    "changeme",
    "change-me",
    "placeholder",
    "replace_with",
    "replace-with",
    "default_password_please_change",
    "password123",
    "insecure",
)


def _secret_text_is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in INSECURE_SECRET_MARKERS)


def _read_durable_secret(target: Path) -> bytes:
    """Read a persisted secret, tolerating a concurrent writer's partial write.

    A concurrent bootstrap winner creates the file exclusively and then writes
    32 bytes; a loser racing the write may briefly observe a short file. Retry
    boundedly before failing closed on a genuinely placeholder/truncated value.
    """
    import time as _time

    for _ in range(50):
        try:
            stored = target.read_bytes()
        except OSError as exc:
            raise AuthConfigError(
                f"Session secret file {target} is unreadable; failing closed."
            ) from exc
        if len(stored) >= 32 and not _secret_text_is_placeholder(
            stored.decode("utf-8", "replace")
        ):
            return stored
        _time.sleep(0.02)
    raise AuthConfigError(
        f"Session secret file {target} holds an insecure placeholder or "
        "truncated value; replace it through the documented rotation "
        "path instead of booting with it."
    )


def check_explicit_secret_strength(secret_text: str) -> None:
    """Reject insecure placeholder or incomplete explicit secrets (fail closed)."""
    if _secret_text_is_placeholder(secret_text):
        raise AuthConfigError(
            "Explicit session secret is an insecure placeholder or is incomplete; "
            "set a deployment-owned random secret of at least 32 bytes or leave "
            "it unset for local durable generation. See "
            "docs/Security/AuthenticationContracts.md."
        )
    if len(secret_text.encode("utf-8")) < 32:
        raise AuthConfigError(
            "Explicit session secret must be at least 32 bytes when UTF-8 encoded."
        )


def default_secret_file() -> Path:
    """Return the deployment-owned secret path (``moonmind_secrets`` volume)."""
    override = os.environ.get("MOONMIND_SESSION_SECRET_FILE")
    if override:
        return Path(override)
    return Path("/app/var/secrets/moonmind_session_secret")


def resolve_session_secret(
    explicit: str | None = None,
    *,
    secret_file: str | Path | None = None,
    allow_generate: bool = True,
    env: Mapping[str, str] | None = None,
) -> bytes:
    """Resolve the MoonMind session-signing secret (durable across restarts).

    Precedence: explicit ``MOONMIND_SESSION_SECRET`` (or ``explicit``) >
    durable ``secret_file`` > single local generation persisted to
    ``secret_file``. Generated secrets are 32 random bytes, written atomically
    with exclusive creation (``O_EXCL``) so concurrent bootstrap processes
    converge on one intended generation instead of creating incompatible
    per-process keys; the loser reads the winner's file. File permissions are
    restricted to ``0o600``. Placeholder, short, or incomplete explicit values
    are rejected. Runtime-host, worker, or repository credentials are never
    consulted and must never be shared here.
    """
    source = os.environ if env is None else env
    candidate = explicit if explicit is not None else source.get(
        "MOONMIND_SESSION_SECRET", source.get("JWT_SECRET_KEY", source.get("JWT_SECRET"))
    )
    if candidate is not None and str(candidate).strip() != "":
        check_explicit_secret_strength(str(candidate))
        return str(candidate).encode("utf-8")
    target = Path(secret_file) if secret_file is not None else default_secret_file()
    if target.exists():
        return _read_durable_secret(target)
    if not allow_generate:
        raise AuthConfigError(
            "No session secret is configured and generation is not permitted "
            "in this environment; set MOONMIND_SESSION_SECRET or provide the "
            "deployment-owned secret file."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_bytes(32)
    try:
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Concurrent bootstrap won the race; use the winner's generation.
        return resolve_session_secret(
            explicit=None, secret_file=target, allow_generate=False, env={}
        )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(generated)
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return generated


def resolve_session_secrets(
    explicit: str | None = None,
    *,
    previous: Sequence[str | bytes] | None = None,
    secret_file: str | Path | None = None,
    allow_generate: bool = True,
    env: Mapping[str, str] | None = None,
) -> tuple[bytes, list[bytes]]:
    """Resolve ``(primary, previous_keys)`` supporting documented rotation.

    Rotation happens through the session owner: the operator stages the
    previous key(s) explicitly while the primary stays deployment-owned.
    Regeneration on each boot is never performed here.
    """
    primary = resolve_session_secret(
        explicit, secret_file=secret_file, allow_generate=allow_generate, env=env
    )
    rotated: list[bytes] = []
    for old in previous or []:
        raw = old if isinstance(old, (bytes, bytearray)) else str(old).encode("utf-8")
        if raw and raw != primary and len(raw) >= 32:
            rotated.append(bytes(raw))
    return primary, rotated


# ---------------------------------------------------------------------------
# R5: explicit disabled/local-mode exposure at the deployment boundary
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"})


def is_loopback_host(host: str) -> bool:
    """Return whether a publish host is an explicit loopback address."""
    normalized = (host or "").strip().strip("[]").lower()
    if normalized in _LOOPBACK_HOSTS:
        return True
    if normalized.startswith("127."):
        return True
    return False


def parse_host_port_binding(binding: str) -> tuple[str, str]:
    """Parse a Docker-style host-port binding into ``(host, container_port)``.

    Accepts ``"[host:]hostport:containerport"``, ``"hostport:containerport"``,
    and bare ``"containerport"`` shapes. An empty host means a wildcard
    publish (all interfaces), which is not loopback-safe.
    """
    text = (binding or "").strip().strip("'\"")
    if not text:
        raise AuthConfigError("Empty host-port binding is not a valid exposure claim.")
    parts = text.split(":")
    if len(parts) == 1:
        return "", parts[0]
    if len(parts) == 2:
        return parts[0].strip().strip("[]"), parts[1].strip()
    # IPv6 or host:hostport:containerport; the container port is last, the
    # host portion is everything before the last two segments when numeric.
    host = ":".join(parts[:-2]).strip().strip("[]") if len(parts) > 3 else parts[0].strip().strip("[]")
    return host, parts[-1].strip()


def validate_disabled_exposure(
    bindings: Sequence[str] | None,
    *,
    trusted_ingress_evidence: bool = False,
    alternate_listeners: Sequence[str] | None = None,
    proxy_bypass_possible: bool = False,
) -> None:
    """Enforce explicit disabled/local-mode exposure at the deployment boundary.

    Unauthenticated services may be published only on supported
    loopback/trusted-ingress paths. Every host-port binding must resolve to an
    explicit loopback host unless documented trusted-ingress evidence is
    supplied. A container listening on ``0.0.0.0`` inside its own network
    namespace is not proof of public exposure or of safe loopback, so only the
    published host binding (plus any alternate listeners) is evaluated.
    Proxy bypasses fail closed unless trusted-ingress evidence covers them.
    """
    bindings = list(bindings or [])
    alternates = list(alternate_listeners or [])
    if proxy_bypass_possible and not trusted_ingress_evidence:
        raise AuthConfigError(
            "Disabled (local) mode with a possible proxy bypass requires "
            "documented trusted-ingress evidence; refusing to publish "
            "unauthenticated services on an unvalidated path."
        )
    for extra in alternates:
        if str(extra).strip() and not trusted_ingress_evidence:
            raise AuthConfigError(
                f"Disabled (local) mode exposes an alternate listener {extra!r} "
                "without trusted-ingress evidence; refusing to publish "
                "unauthenticated services."
            )
    if not bindings and not trusted_ingress_evidence:
        raise AuthConfigError(
            "Disabled (local) mode has no validated host-port binding evidence; "
            "publish unauthenticated services only on explicit loopback "
            "(for example '127.0.0.1:7000:8000') or supply documented "
            "trusted-ingress evidence."
        )
    for binding in bindings:
        host, _port = parse_host_port_binding(binding)
        if not host:
            # ``"7000:8000"`` / ``":8000"`` publishes on all interfaces.
            if not trusted_ingress_evidence:
                raise AuthConfigError(
                    f"Disabled (local) mode binding {binding!r} publishes on all "
                    "interfaces; restrict it to explicit loopback (for example "
                    "'127.0.0.1:7000:8000') or supply documented "
                    "trusted-ingress evidence."
                )
            continue
        if host == "0.0.0.0" or host == "::":
            raise AuthConfigError(
                f"Disabled (local) mode binding {binding!r} is a container-"
                "internal wildcard, not proof of safe loopback; validate the "
                "published host binding or supply trusted-ingress evidence."
            )
        if not is_loopback_host(host) and not trusted_ingress_evidence:
            raise AuthConfigError(
                f"Disabled (local) mode binding {binding!r} is not loopback; "
                "publish unauthenticated services only on supported "
                "loopback/trusted-ingress paths."
            )


# ---------------------------------------------------------------------------
# R6: base URL, trusted proxy, callback origins, cookie policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CookiePolicy:
    """Resolved session-cookie policy for one request context."""

    cookie_name: str
    secure: bool
    http_only: bool = True
    same_site: str = "Lax"
    development: bool = False


def resolve_cookie_policy(
    *,
    is_https: bool,
    request_host: str = "",
    explicit_dev_loopback_http: bool = False,
) -> CookiePolicy:
    """Resolve the session-cookie policy without trusting forwarded headers.

    Production always uses the ``__Host-`` cookie with ``Secure`` on HTTPS. A
    separately named development cookie is restricted to explicit loopback
    HTTP: it requires ``explicit_dev_loopback_http`` **and** a loopback
    ``request_host`` over plaintext HTTP. Anything else stays on the
    production cookie (with ``Secure`` whenever the connection is HTTPS).
    """
    host = (request_host or "").strip().strip("[]").lower()
    if (
        not is_https
        and explicit_dev_loopback_http
        and host
        and (is_loopback_host(host))
    ):
        return CookiePolicy(
            cookie_name=MOONMIND_DEV_COOKIE, secure=False, development=True
        )
    return CookiePolicy(cookie_name=MOONMIND_PROD_COOKIE, secure=is_https)


@dataclass(frozen=True)
class ProxyConfig:
    """Explicit trusted-proxy configuration (no implicit trust)."""

    trusted_proxies: tuple[str, ...] = ()
    trust_forwarded_headers: bool = False


def validate_proxy_config(
    proxy_config: ProxyConfig,
    *,
    forwarded_host: str | None = None,
    forwarded_proto: str | None = None,
) -> None:
    """Fail closed when untrusted forwarded headers could decide behavior.

    Unknown or untrusted ``Forwarded``/``X-Forwarded-Host``/``X-Forwarded-Proto``
    values must never decide redirects or secure-cookie policy. Forwarded
    headers are honored only with an explicit non-empty trusted-proxy list and
    ``trust_forwarded_headers=True``; otherwise any presented forwarded header
    is a configuration error.
    """
    presented = bool((forwarded_host or "").strip() or (forwarded_proto or "").strip())
    if not presented:
        return
    if not proxy_config.trust_forwarded_headers or not proxy_config.trusted_proxies:
        raise AuthConfigError(
            "Untrusted forwarded host/proto headers were presented without an "
            "explicit trusted-proxy configuration; refusing to let them decide "
            "redirects or secure-cookie policy."
        )


def validate_public_base_url(base_url: str) -> str:
    """Validate the public base URL used for redirects and callbacks."""
    text = (base_url or "").strip()
    if not text:
        raise AuthConfigError("Public base URL must be configured explicitly.")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise AuthConfigError(f"Public base URL {text!r} must use http(s).")
    if not parsed.hostname:
        raise AuthConfigError(f"Public base URL {text!r} must include a host.")
    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname or ""):
        raise AuthConfigError(
            f"Public base URL {text!r} uses plaintext HTTP on a non-loopback "
            "host; production requires HTTPS."
        )
    if "@" in (parsed.netloc or "") or ".." in (parsed.hostname or ""):
        raise AuthConfigError(f"Public base URL {text!r} is not a valid origin.")
    return text.rstrip("/")


def validate_callback_origin(base_url: str, callback_url: str) -> str:
    """Require an exact configured redirect destination (no open redirects)."""
    base = urlparse(validate_public_base_url(base_url))
    candidate = urlparse((callback_url or "").strip())
    if not candidate.scheme or not candidate.hostname:
        raise AuthConfigError(f"Callback URL {callback_url!r} must be absolute.")
    base_port = base.port or (443 if base.scheme == "https" else 80)
    cand_port = candidate.port or (443 if candidate.scheme == "https" else 80)
    if (
        candidate.scheme != base.scheme
        or (candidate.hostname or "").lower() != (base.hostname or "").lower()
        or cand_port != base_port
    ):
        raise AuthConfigError(
            f"Callback URL {callback_url!r} does not exactly match the "
            f"configured public base URL {base_url!r}; open redirects are rejected."
        )
    if candidate.fragment or candidate.username or candidate.password:
        raise AuthConfigError(f"Callback URL {callback_url!r} carries unexpected components.")
    return callback_url.strip()


# ---------------------------------------------------------------------------
# R7: distinguishable readiness + redacted, fail-closed diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthReadiness:
    """Distinguishable infrastructure / authentication / operator-setup state."""

    infrastructure: str  # "ready" | "degraded"
    authentication: str  # "ready" | "setup-required" | "blocked"
    setup_required: bool
    mode: str
    reason: str


def build_auth_readiness(
    *,
    db_reachable: bool,
    mode: str,
    bootstrap: BootstrapDecision | None,
    migration_pending: bool = False,
) -> AuthReadiness:
    """Build readiness with infra, auth, and operator-setup kept distinct."""
    normalized = validate_auth_provider(mode)
    if not db_reachable:
        return AuthReadiness(
            infrastructure="degraded", authentication="blocked",
            setup_required=False, mode=normalized,
            reason="database unreachable; authentication failing closed",
        )
    if bootstrap is not None and bootstrap.action == "require-operator-choice":
        return AuthReadiness(
            infrastructure="ready", authentication="setup-required",
            setup_required=True, mode=normalized, reason=bootstrap.reason,
        )
    if migration_pending and normalized != "disabled":
        return AuthReadiness(
            infrastructure="ready", authentication="setup-required",
            setup_required=True, mode=normalized,
            reason="protected operator setup is required before first login",
        )
    if normalized == "disabled":
        return AuthReadiness(
            infrastructure="ready", authentication="ready",
            setup_required=False, mode=normalized,
            reason="explicit local single-user mode with restricted ingress",
        )
    return AuthReadiness(
        infrastructure="ready", authentication="ready",
        setup_required=False, mode=normalized,
        reason=f"explicit '{normalized}' mode with persisted configuration",
    )


def redact_value(value: str | None) -> str:
    """Render a diagnostic value with secrets replaced (never echo raw)."""
    if value is None or str(value).strip() == "":
        return "<unset>"
    return "<set>"


def build_auth_diagnostics(
    *,
    mode: str,
    db_reachable: bool,
    bootstrap: BootstrapDecision | None = None,
    secret_configured: bool = False,
    bindings: Sequence[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build redacted startup diagnostics (no secret values, bounded shape)."""
    readiness = build_auth_readiness(
        db_reachable=db_reachable, mode=validate_auth_provider(mode),
        bootstrap=bootstrap,
    )
    diagnostics: dict[str, Any] = {
        "mode": readiness.mode,
        "infrastructure": readiness.infrastructure,
        "authentication": readiness.authentication,
        "setup_required": readiness.setup_required,
        "reason": readiness.reason,
        "db": "connected" if db_reachable else "unreachable",
        "session_secret": "<set>" if secret_configured else "<unset>",
        "bindings": list(bindings or []),
    }
    for key, value in dict(extra or {}).items():
        text = str(value)
        lowered = key.lower()
        if any(marker in lowered for marker in ("secret", "token", "password", "cookie", "key")):
            diagnostics[key] = "<set>" if text.strip() else "<unset>"
        else:
            diagnostics[key] = value
    return diagnostics


__all__ = [
    "AuthConfigError",
    "AuthMigrationDecision",
    "AuthReadiness",
    "BootstrapDecision",
    "ControlPlaneAuthConfig",
    "CookiePolicy",
    "INSECURE_SECRET_MARKERS",
    "MIGRATION_DECISIONS",
    "MIGRATION_DECISION_VERSION",
    "MOONMIND_DEV_COOKIE",
    "MOONMIND_PROD_COOKIE",
    "MOONMIND_TOKEN_AUDIENCE",
    "MOONMIND_TOKEN_ISSUER",
    "ProxyConfig",
    "RETIRED_AUTH_SELECTORS",
    "SUPPORTED_AUTH_MODES",
    "ambient_runtime_settings_present",
    "build_auth_diagnostics",
    "build_auth_readiness",
    "check_explicit_secret_strength",
    "decide_auth_bootstrap",
    "default_secret_file",
    "is_loopback_host",
    "load_migration_decision",
    "parse_host_port_binding",
    "redact_value",
    "resolve_auth_mode",
    "resolve_control_plane_config",
    "resolve_cookie_policy",
    "resolve_session_secret",
    "resolve_session_secrets",
    "retired_guidance",
    "save_migration_decision",
    "validate_auth_provider",
    "validate_callback_origin",
    "validate_disabled_exposure",
    "validate_proxy_config",
    "validate_public_base_url",
]
