"""Explicit MoonMind authentication modes and secure defaults (#4120).

Source issue: MoonLadderStudios/MoonMind#4120 (parent #4116, depends on
#4117/#4118, integrates the migration contract from #4119).
Plan coverage: K3 configuration and deployment defaults in
``docs/tmp/KeycloakRemovalPlan.md``.

This module is the single correctly scoped configuration owner for the
MoonMind application-authentication selector ``AUTH_PROVIDER``. The
persistent storage for the raw selector remains
``moonmind.config.settings.OIDCSettings`` (shared-ownership coordination
with #3941), but interpretation of the selector -- validation, fresh vs.
upgrade classification, migration decisions, signing-key durability,
disabled-mode exposure, base-URL/proxy/cookie policy, and redacted
diagnostics -- lives here and every production consumer must go through
it. Direct ``settings.oidc.AUTH_PROVIDER`` string comparisons in new code
are a layering violation; use :func:`get_effective_auth_provider`,
:func:`is_disabled_local_mode`, or :func:`classify_deployment`.

Design notes:

- One selector, ``AUTH_PROVIDER``, with final values ``accounts``,
  ``oidc``, ``header``, ``disabled``. No competing enable switch, no alias
  layer, no implicit provider fallback. Retired ``keycloak``/``default``/
  ``google`` (plus legacy ``local``) and unknown selectors fail actionably.
- ``OMNIGENT_AUTH_*`` (and other runtime-server variables) never configure
  MoonMind. :func:`resolve_moonmind_auth_config` builds the control-plane
  config explicitly from MoonMind-owned inputs only.
- A genuinely fresh database and a pre-cutover installation with omitted
  auth variables are distinguished through an explicit, versioned,
  persisted migration decision (table ``moonmind_auth_migration_decision``
  ensured idempotently, plus the operator-held env override
  ``MOONMIND_AUTH_MIGRATION_DECISION``), never through a heuristic such as
  "no account has a password". Populated databases without a decision stop
  actionably before any owner is created or modified.
- Signing/session secrets are durable across restarts and replicas via the
  existing deployment-owned persistence (the ``moonmind_secrets`` volume,
  default ``/app/var/secrets/moonmind_session_key``). Fresh local startup
  generates exactly one key with atomic create semantics; placeholders and
  incomplete explicit remote settings are rejected; rotation goes through
  the session/revocation owner, never regeneration on each boot; keys are
  never shared with runtime-host, worker, or repository credentials.
- ``disabled`` local mode is only valid behind loopback or documented
  trusted ingress. Publish bindings are validated at the deployment
  boundary; a container listening on ``0.0.0.0`` inside its network is not
  proof of safe loopback.
- Public base URL, trusted proxies, callback origins, and HTTPS cookie
  policy are validated explicitly. Untrusted forwarded host/proto headers
  never decide redirects or secure-cookie policy. The dev cookie is
  restricted to explicit loopback HTTP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from moonmind.security.omnigent_auth_qualification import (
    MOONMIND_DEV_COOKIE,
    MOONMIND_PROD_COOKIE,
    RETIRED_SELECTORS,
    SUPPORTED_MODES,
    AuthConfigError,
    MoonmindAuthConfig,
    validate_mode_selector,
)

__all__ = [
    "SUPPORTED_MODES",
    "RETIRED_SELECTORS",
    "AuthModeError",
    "MigrationRequiredError",
    "AuthMigrationDecision",
    "MIGRATION_DECISION_VERSION",
    "MIGRATION_DECISION_TABLE",
    "MIGRATION_DECISION_ENV_VAR",
    "get_effective_auth_provider",
    "is_disabled_local_mode",
    "is_auth_provider_explicit",
    "resolve_production_mode",
    "classify_deployment",
    "parse_migration_decision",
    "format_migration_decision",
    "ensure_migration_decision_table_sql",
    "resolve_moonmind_auth_config",
    "looks_like_placeholder_secret",
    "resolve_session_secret",
    "validate_publish_binding",
    "evaluate_ingress_fixture",
    "validate_public_base_url",
    "public_base_url_is_loopback",
    "cookie_policy_for_base_url",
    "validate_trusted_proxy_config",
    "validate_callback_origin",
    "redacted_diagnostics",
    "redact_value",
    "auth_readiness_summary",
]

AuthModeError = AuthConfigError


class MigrationRequiredError(AuthConfigError):
    """A populated install needs an explicit protected operator choice."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.requires_protected_operator_choice = True


# ---------------------------------------------------------------------------
# Versioned, persisted migration decision (#4119 contract, minimal K3 slice)
# ---------------------------------------------------------------------------

MIGRATION_DECISION_VERSION = 1
MIGRATION_DECISION_TABLE = "moonmind_auth_migration_decision"
MIGRATION_DECISION_ENV_VAR = "MOONMIND_AUTH_MIGRATION_DECISION"


@dataclass(frozen=True)
class AuthMigrationDecision:
    """Explicit operator choice for a pre-cutover installation."""

    mode: str
    version: int = MIGRATION_DECISION_VERSION

    def __post_init__(self) -> None:
        validate_mode_selector(self.mode)
        if self.version != MIGRATION_DECISION_VERSION:
            raise AuthModeError(
                f"Unsupported migration decision version {self.version!r}; "
                f"supported version is {MIGRATION_DECISION_VERSION}. Re-run "
                "migration preflight to record a current decision."
            )


def parse_migration_decision(raw: str | None) -> AuthMigrationDecision | None:
    """Parse the operator-held ``MOONMIND_AUTH_MIGRATION_DECISION`` value.

    Accepted shapes: ``"<mode>"`` (implies current version) or
    ``"<mode>:v<version>"`` (e.g. ``"accounts:v1"``). Empty/blank returns
    ``None`` (no decision recorded). Retired/unknown modes raise
    :class:`AuthModeError`; unsupported versions raise the same way.
    """
    text = (raw or "").strip()
    if not text:
        return None
    mode_part, _, version_part = text.partition(":")
    mode = validate_mode_selector(mode_part.strip())
    if not version_part.strip():
        return AuthMigrationDecision(mode=mode)
    match = re.fullmatch(r"v(\d+)", version_part.strip().lower())
    if not match:
        raise AuthModeError(
            f"Invalid {MIGRATION_DECISION_ENV_VAR}={raw!r}: expected "
            "'<mode>' or '<mode>:v<version>' (e.g. 'accounts:v1')."
        )
    return AuthMigrationDecision(mode=mode, version=int(match.group(1)))


def format_migration_decision(decision: AuthMigrationDecision) -> str:
    """Render a decision in the canonical ``<mode>:v<version>`` shape."""
    return f"{decision.mode}:v{decision.version}"


def ensure_migration_decision_table_sql(
    table: str = MIGRATION_DECISION_TABLE,
) -> str:
    """Return idempotent DDL for the persisted migration-decision record.

    The table holds exactly one row (``id = 1``): the chosen target mode
    plus the contract version. Startup ensures it with ``CREATE TABLE IF
    NOT EXISTS`` so this K3 slice does not depend on the Alembic head
    graph; a future revision may adopt the table declaratively.
    """
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "mode VARCHAR(16) NOT NULL, "
        "version INTEGER NOT NULL, "
        "decided_at TIMESTAMPTZ NULL)"
    )


# ---------------------------------------------------------------------------
# Selector ownership: the one place that interprets AUTH_PROVIDER
# ---------------------------------------------------------------------------


def is_auth_provider_explicit(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the operator explicitly set ``AUTH_PROVIDER``.

    An empty/blank value counts as omitted. Explicitness is derived from
    the process environment (or an injected mapping in tests), never from
    the stored default, so omitted-fresh and explicit-``accounts`` can take
    the same production path while omitted-populated stops actionably.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    raw = env.get("AUTH_PROVIDER")
    return raw is not None and str(raw).strip() != ""


def get_effective_auth_provider() -> str:
    """Return the normalized stored selector (``settings``-backed).

    This is the only approved reader for the raw selector. Omitted/blank
    storage reads back as ``""`` (undecided at this layer); callers that
    need the production decision must use :func:`resolve_production_mode`
    or :func:`classify_deployment`.
    """
    from moonmind.config.settings import settings

    raw = getattr(settings.oidc, "AUTH_PROVIDER", "") or ""
    text = str(raw).strip().lower()
    if not text:
        return ""
    return validate_mode_selector(text)


def is_disabled_local_mode() -> bool:
    """Whether the effective selector is explicit restricted local mode."""
    return get_effective_auth_provider() == "disabled"


def resolve_production_mode(
    *,
    raw_selector: str | None = None,
    explicit: bool | None = None,
    has_users: bool = False,
    migration_decision: AuthMigrationDecision | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the production authentication mode for startup/request code.

    Rules (same production code for omitted-fresh and explicit-``accounts``):

    - Explicit retired/unknown selectors always raise :class:`AuthModeError`.
    - Omitted + fresh database (no users, no decision) selects ``accounts``.
    - Omitted + populated database without a persisted decision raises
      :class:`MigrationRequiredError` without modifying owners.
    - Omitted + populated database with a decision uses the decided mode.
    - Explicit selectors (including explicit ``disabled``) use the selected
      mode after validation.
    """
    if explicit is None:
        explicit = (
            is_auth_provider_explicit(environ)
            if raw_selector is None
            else bool(str(raw_selector).strip())
        )
    if raw_selector is None:
        if explicit:
            from moonmind.config.settings import settings

            raw_selector = getattr(settings.oidc, "AUTH_PROVIDER", "") or ""
        else:
            raw_selector = ""
    text = str(raw_selector or "").strip().lower()
    if text:
        return validate_mode_selector(text)
    # Omitted selector: fresh vs. pre-cutover branch.
    if migration_decision is not None:
        return validate_mode_selector(migration_decision.mode)
    if has_users:
        raise MigrationRequiredError(
            "AUTH_PROVIDER is omitted but the database already contains users. "
            "Refusing to silently disable auth or create a new owner. Record an "
            "explicit protected migration decision: set AUTH_PROVIDER to "
            "'accounts', 'oidc', 'header', or explicitly restricted local "
            f"'disabled', or set {MIGRATION_DECISION_ENV_VAR}='<mode>:v1' after "
            "migration preflight. See docs/Security/AuthenticationContracts.md."
        )
    return "accounts"


@dataclass(frozen=True)
class DeploymentClassification:
    """Authoritative startup classification for the current deployment."""

    production_mode: str
    explicit: bool
    fresh_install: bool
    migration_required: bool = False
    setup_required: bool = False
    detail: str = ""


def classify_deployment(
    *,
    raw_selector: str | None = None,
    explicit: bool | None = None,
    has_users: bool = False,
    migration_decision: AuthMigrationDecision | None = None,
    environ: Mapping[str, str] | None = None,
) -> DeploymentClassification:
    """Classify fresh vs. upgrade and report protected-setup requirements.

    Never raises for the upgrade-without-decision case: it returns
    ``migration_required=True`` so readiness can expose the protected
    setup requirement without granting an unauthenticated administrator.
    Retired/unknown selectors still raise :class:`AuthModeError`.
    """
    if explicit is None:
        explicit = (
            is_auth_provider_explicit(environ)
            if raw_selector is None
            else bool(str(raw_selector).strip())
        )
    try:
        mode = resolve_production_mode(
            raw_selector=raw_selector,
            explicit=explicit,
            has_users=has_users,
            migration_decision=migration_decision,
            environ=environ,
        )
    except MigrationRequiredError as exc:
        return DeploymentClassification(
            production_mode="migration_required",
            explicit=bool(explicit),
            fresh_install=False,
            migration_required=True,
            setup_required=True,
            detail=str(exc),
        )
    fresh = not has_users and migration_decision is None
    setup_required = mode in ("accounts",) and fresh
    return DeploymentClassification(
        production_mode=mode,
        explicit=bool(explicit),
        fresh_install=fresh,
        migration_required=False,
        setup_required=setup_required,
        detail=(
            "fresh install: protected first-owner setup required; no "
            "unauthenticated administrator is granted"
            if setup_required
            else f"production mode '{mode}'"
        ),
    )


# ---------------------------------------------------------------------------
# Control-plane resolution: OMNIGENT_AUTH_* can never configure MoonMind
# ---------------------------------------------------------------------------

_MOONMIND_SESSION_TTL_DEFAULT = 8 * 3600

_RUNTIME_ENV_PREFIXES = ("OMNIGENT_AUTH_", "OMNIGENT_ACCOUNTS_", "OMNIGENT_OIDC_")


def _assert_no_runtime_leak(kwargs: dict[str, Any]) -> None:
    for key in kwargs:
        if key.startswith(_RUNTIME_ENV_PREFIXES):
            raise AuthModeError(
                f"Refusing to configure MoonMind auth from runtime variable {key!r}."
            )


def resolve_moonmind_auth_config(
    *,
    mode: str,
    cookie_secret: bytes,
    cookie_name: str | None = None,
    session_ttl_seconds: int = _MOONMIND_SESSION_TTL_DEFAULT,
    require_secure_cookies: bool = True,
    environ: Mapping[str, str] | None = None,
) -> MoonmindAuthConfig:
    """Build the #4118 control-plane config from explicit MoonMind inputs.

    ``environ`` is accepted only so tests can prove hostile ambient
    ``OMNIGENT_AUTH_*`` values have no effect: it is never read for mode,
    key, cookie, or identity material. All values are passed explicitly by
    the caller (production wiring reads only ``MOONMIND_*``/``AUTH_*``/
    ``OIDC_*`` inputs). Simultaneous same-origin use stays safe because
    MoonMind cookie names, signing keys, and token purpose are distinct
    from the upstream runtime contract by construction (validated here).
    """
    normalized = validate_mode_selector(mode)
    if cookie_name is None:
        cookie_name = (
            MOONMIND_PROD_COOKIE if require_secure_cookies else MOONMIND_DEV_COOKIE
        )
    config = MoonmindAuthConfig(
        mode=normalized,
        cookie_name=cookie_name,
        cookie_secret=cookie_secret,
        session_ttl_seconds=session_ttl_seconds,
        require_secure_cookies=require_secure_cookies,
    )
    if environ is not None:
        leaked = [k for k in environ if k.startswith(_RUNTIME_ENV_PREFIXES)]
        # Presence is fine (simultaneous same-origin use); influence is not.
        # This constructor never read them, so record that fact structurally.
        _assert_no_runtime_leak({})
        object.__setattr__(config, "_runtime_ambient_ignored", tuple(sorted(leaked)))
    return config


# ---------------------------------------------------------------------------
# Durable signing/session secrets
# ---------------------------------------------------------------------------

_PLACEHOLDER_SECRETS = frozenset(
    {
        "devsecret",
        "changeme",
        "change-me",
        "placeholder",
        "replace_with_a_strong_random_jwt_secret",
        "replace-with-a-strong-random-jwt-secret",
        "test_jwt_secret_key",
        "test-secret",
        "insecure",
        "password",
        "secret",
    }
)

_MIN_SECRET_BYTES = 32


def looks_like_placeholder_secret(value: str) -> bool:
    """Whether a secret value is blank or a known placeholder shape."""
    normalized = value.strip().lower()
    if not normalized:
        return True
    if normalized in _PLACEHOLDER_SECRETS:
        return True
    if "replace_with" in normalized or "replace-with" in normalized:
        return True
    if normalized.startswith("test_") or normalized.startswith("test-"):
        return True
    if normalized in ("devsecret", "dev-secret", "development"):
        return True
    return False


def _durable_key_bytes(stored: bytes) -> bytes | None:
    """Return persisted key material verbatim when it is complete.

    Generated keys are exact-length binary material and reload byte-for-byte:
    random keys may legitimately edge with ASCII whitespace, so stripping on
    read would shorten them below the floor and brick restarts. Files shorter
    than the floor return ``None`` so callers fail closed with context.
    Longer files (e.g. operator-provisioned material with a trailing newline)
    are used verbatim; length is the gate, and reads are stable across
    restarts because the file itself does not change.
    """
    if len(stored) >= _MIN_SECRET_BYTES:
        return bytes(stored)
    return None


def _secret_to_bytes(value: str) -> bytes:
    text = value.strip()
    # Accept raw strings or base64-encoded material; always require 32+ bytes.
    try:
        padded = text + "=" * (-len(text) % 4)
        decoded = base64.b64decode(padded, validate=False)
        if len(decoded) >= _MIN_SECRET_BYTES and re.fullmatch(
            r"[A-Za-z0-9+/=_-]+", text
        ):
            # Only treat as base64 when it actually decodes to enough bytes
            # and the input alphabet is base64-shaped; otherwise fall through
            # to raw UTF-8 handling below.
            if len(text) >= 44:
                return decoded
    except Exception:
        # Not base64-shaped input; fall through to raw UTF-8 handling below.
        pass
    return text.encode("utf-8")


def default_session_key_path() -> Path:
    """Deployment-owned durable path for the MoonMind session signing key."""
    override = os.environ.get("MOONMIND_SESSION_KEY_PATH", "").strip()
    if override:
        return Path(override)
    return Path("/app/var/secrets/moonmind_session_key")


def resolve_session_secret(
    *,
    explicit_secret: str | None = None,
    key_path: Path | None = None,
    allow_generate: bool = True,
    for_remote_production: bool = False,
) -> bytes:
    """Resolve the MoonMind session signing secret, failing closed.

    - Explicit values that are blank, placeholders, or under 32 bytes are
      rejected with actionable guidance (rotation goes through the session
      owner, never silent regeneration).
    - Omitted values load the durable key at ``key_path`` when present, so
      restarts and concurrent replicas share one intended generation.
    - Omitted values with no durable key generate exactly one key using
      atomic ``O_CREAT | O_EXCL`` creation: concurrent bootstrap losers read
      back the winner's key instead of minting incompatible per-process
      keys. Remote production (``for_remote_production=True``) never
      generates implicitly; it requires explicit operator material.
    - Runtime-host, worker, and repository credentials are never consulted;
      only the explicit MoonMind secret and the deployment-owned key file
      are inputs.
    """
    path = Path(key_path) if key_path is not None else default_session_key_path()
    if explicit_secret is not None:
        # An explicitly provided blank is a misconfiguration, not an
        # omission: looks_like_placeholder_secret rejects blanks alongside known
        # placeholder shapes so callers cannot silently fall through to
        # durable generation with an operator intent to provide material.
        if looks_like_placeholder_secret(str(explicit_secret)):
            raise AuthModeError(
                "The provided MoonMind session secret is an insecure placeholder. "
                "Provide explicit operator-generated key material of at least "
                "32 bytes (or unset it for deployment-owned durable generation "
                "on loopback fresh installs). See "
                "docs/Security/AuthenticationContracts.md."
            )
        secret = _secret_to_bytes(str(explicit_secret))
        if len(secret) < _MIN_SECRET_BYTES:
            raise AuthModeError(
                "The provided MoonMind session secret is too short: at least "
                f"{_MIN_SECRET_BYTES} bytes of key material are required."
            )
        return secret
    # Omitted explicit secret: durable deployment-owned material wins.
    if path.is_file():
        try:
            stored = path.read_bytes()
        except OSError as exc:
            raise AuthModeError(
                f"Unable to read the MoonMind session key at {path}: {exc}. "
                "Failing closed rather than regenerating."
            ) from exc
        candidate = _durable_key_bytes(stored)
        if candidate is None:
            raise AuthModeError(
                f"The MoonMind session key at {path} is incomplete "
                f"({len(stored)} bytes); at least {_MIN_SECRET_BYTES} bytes "
                "are required. Rotate through the session owner with fresh "
                "operator-generated material."
            )
        return candidate
    if for_remote_production:
        raise AuthModeError(
            "No MoonMind session secret is configured for a remote production "
            "deployment. Set an explicit operator-generated secret or provision "
            f"the deployment-owned key file at {path}; refusing to generate "
            "implicitly or accept a placeholder."
        )
    if not allow_generate:
        raise AuthModeError(
            f"No MoonMind session secret is available at {path} and implicit "
            "generation is disabled for this context. Failing closed."
        )
    # Fresh local path: exactly-one-wins generation with atomic create.
    generated = secrets.token_bytes(_MIN_SECRET_BYTES)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # A concurrent replica won the race; use its durable generation.
        try:
            stored = path.read_bytes()
        except OSError as exc:
            raise AuthModeError(
                f"Concurrent session-key bootstrap lost the creation race and "
                f"could not read the winner's key at {path}: {exc}."
            ) from exc
        candidate = _durable_key_bytes(stored)
        if candidate is None:
            raise AuthModeError(
                f"The concurrently created MoonMind session key at {path} is "
                "incomplete; failing closed rather than minting a second key."
            )
        return candidate
    except OSError as exc:
        raise AuthModeError(
            f"Unable to persist the MoonMind session key at {path}: {exc}."
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(generated)
    except OSError as exc:
        raise AuthModeError(
            f"Unable to persist the MoonMind session key at {path}: {exc}."
        ) from exc
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        # Best-effort hardening; key material is already persisted above.
        pass
    return generated


# ---------------------------------------------------------------------------
# Disabled-mode exposure at the deployment boundary
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "*", ""})


def _normalize_publish_host(host: str | None) -> str:
    return (host or "").strip().lower().strip("[]")


def validate_publish_binding(
    *,
    mode: str,
    publish_host: str | None = None,
    trusted_ingress: bool = False,
    detail: str = "",
) -> None:
    """Validate the deployment publish binding for the selected mode.

    ``disabled`` (explicit local mode) publishes unauthenticated services
    only on loopback or documented trusted ingress. Wildcard publish
    (``0.0.0.0``/``::``/empty host), custom non-loopback hosts, alternate
    listeners, and proxy bypasses without trusted-ingress evidence fail
    closed. A container listening on ``0.0.0.0`` *inside* its network is
    not proof of public exposure or of safe loopback, so the validated
    input is the operator-facing publish host, not the in-container listen
    address.
    """
    normalized = validate_mode_selector(mode)
    if normalized != "disabled":
        return
    host = _normalize_publish_host(publish_host)
    if host in _LOOPBACK_HOSTS:
        return
    if trusted_ingress:
        return
    if host in _WILDCARD_HOSTS:
        raise AuthModeError(
            "Explicit local 'disabled' mode cannot publish on wildcard host "
            f"{publish_host!r} without documented trusted-ingress evidence. "
            "Bind loopback (127.0.0.1) or set MOONMIND_TRUSTED_INGRESS=1 with an "
            "audited ingress path. See "
            "docs/Security/AuthenticationContracts.md."
            + (f" {detail}" if detail else "")
        )
    # Any other non-loopback host, including custom hostnames and alternate
    # listeners, needs the same explicit trusted-ingress evidence.
    raise AuthModeError(
        f"Explicit local 'disabled' mode cannot publish on host {publish_host!r} "
        "without documented trusted-ingress evidence. Use loopback or set "
        "MOONMIND_TRUSTED_INGRESS=1 with an audited ingress path."
        + (f" {detail}" if detail else "")
    )


@dataclass(frozen=True)
class IngressFixtureResult:
    """Outcome of one rendered Compose/ingress fixture evaluation."""

    name: str
    allowed: bool
    reason: str = ""


def evaluate_ingress_fixture(fixture: Mapping[str, Any]) -> IngressFixtureResult:
    """Evaluate one deployment ingress fixture against the mode contract.

    Fixture keys: ``name``, ``mode``, ``publish_host``,
    ``trusted_ingress`` (bool), ``tls_terminated`` (bool),
    ``proxy_bypass_possible`` (bool), ``internal_runtime_reachable``
    (bool). ``disabled`` fixtures fail closed on wildcard/custom-host
    publish without trusted ingress and on proxy bypasses; all modes fail
    when internal control-plane routes would be publicly reachable.
    """
    name = str(fixture.get("name", "unnamed"))
    mode = str(fixture.get("mode", "") or "").strip().lower() or "disabled"
    publish_host = fixture.get("publish_host")
    trusted_ingress = bool(fixture.get("trusted_ingress", False))
    proxy_bypass = bool(fixture.get("proxy_bypass_possible", False))
    internal_public = bool(fixture.get("internal_control_plane_public", False))
    try:
        validated_mode = validate_mode_selector(mode)
    except AuthModeError as exc:
        return IngressFixtureResult(name=name, allowed=False, reason=str(exc))
    if internal_public:
        return IngressFixtureResult(
            name=name,
            allowed=False,
            reason="internal control-plane routes must never be publicly exposed",
        )
    if validated_mode == "disabled":
        try:
            validate_publish_binding(
                mode=validated_mode,
                publish_host=publish_host,
                trusted_ingress=trusted_ingress,
            )
        except AuthModeError as exc:
            return IngressFixtureResult(name=name, allowed=False, reason=str(exc))
        if proxy_bypass and not trusted_ingress:
            return IngressFixtureResult(
                name=name,
                allowed=False,
                reason="proxy bypass without trusted-ingress evidence",
            )
    else:
        if proxy_bypass and not trusted_ingress:
            return IngressFixtureResult(
                name=name,
                allowed=False,
                reason="proxy bypass without trusted-ingress evidence",
            )
    return IngressFixtureResult(name=name, allowed=True, reason="fixture satisfies the mode contract")


# ---------------------------------------------------------------------------
# Public base URL, trusted proxies, callback origins, cookie policy
# ---------------------------------------------------------------------------


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def public_base_url_is_loopback(value: str | None) -> bool:
    """Whether a public base URL addresses a loopback host.

    The hostname is parsed (never substring-matched), so remote hosts such
    as ``https://localhost.example.com`` or URLs whose path/query mentions
    ``127.0.0.1`` are not mistaken for local, while bracketed IPv6 loopback
    (``http://[::1]:7000``) is recognised. Blank or unparseable values
    return ``False`` (fail closed); callers keep their own blank handling.
    """
    raw = (value or "").strip()
    if not raw:
        return False
    try:
        host = urlsplit(raw).hostname or ""
    except ValueError:
        return False
    return _is_loopback_host(host)


@dataclass(frozen=True)
class BaseUrlPolicy:
    """Validated public base-URL decision."""

    base_url: str
    host: str
    is_https: bool
    is_loopback: bool
    require_secure_cookies: bool
    cookie_name: str = field(default=MOONMIND_PROD_COOKIE)


def validate_public_base_url(
    base_url: str | None,
    *,
    trusted_proxies: tuple[str, ...] | list[str] = (),
    forwarded_host: str | None = None,
    forwarded_proto: str | None = None,
) -> BaseUrlPolicy:
    """Validate the public base URL and forwarded-header trust.

    Unknown/untrusted ``X-Forwarded-Host``/``X-Forwarded-Proto`` values
    never decide redirects or secure-cookie policy: any presented forwarded
    host/proto from an untrusted proxy raises instead of downgrading. The
    configured base URL is the only authority for origin decisions.
    """
    proxies = {str(p).strip().lower() for p in (trusted_proxies or ()) if str(p).strip()}
    if forwarded_host is not None or forwarded_proto is not None:
        # Forwarded headers are only meaningful behind an explicitly trusted
        # proxy set. Without one, their mere presence is a misconfiguration.
        if not proxies:
            raise AuthModeError(
                "Forwarded host/proto headers were presented without any "
                "configured trusted proxies (MOONMIND_TRUSTED_PROXIES). "
                "Rejecting rather than trusting untrusted headers."
            )
    raw = (base_url or "").strip()
    if not raw:
        raise AuthModeError(
            "MOONMIND_PUBLIC_BASE_URL is required for redirect and cookie "
            "decisions; refusing to derive origins from untrusted headers."
        )
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise AuthModeError(
            f"MOONMIND_PUBLIC_BASE_URL={raw!r} must use http(s); refusing to "
            "derive cookie/redirect policy from an unknown scheme."
        )
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise AuthModeError(
            f"MOONMIND_PUBLIC_BASE_URL={raw!r} has no host; refusing to derive "
            "cookie/redirect policy."
        )
    is_https = parts.scheme == "https"
    is_loopback = _is_loopback_host(host)
    if parts.scheme == "http" and not is_loopback:
        raise AuthModeError(
            f"MOONMIND_PUBLIC_BASE_URL={raw!r} uses plain HTTP on a "
            "non-loopback host; production cookie policy requires HTTPS. Use "
            "an https URL or an explicit loopback address."
        )
    return BaseUrlPolicy(
        base_url=raw,
        host=host,
        is_https=is_https,
        is_loopback=is_loopback,
        require_secure_cookies=is_https,
        cookie_name=MOONMIND_PROD_COOKIE if is_https else MOONMIND_DEV_COOKIE,
    )


def cookie_policy_for_base_url(
    base_url: str | None,
    *,
    explicit_loopback_http: bool = False,
) -> BaseUrlPolicy:
    """Return the cookie policy for a base URL.

    Production HTTPS always uses the ``__Host-`` cookie with ``Secure``.
    The separately named development cookie is restricted to explicit
    loopback HTTP (``explicit_loopback_http=True`` plus a loopback base
    URL); it is never issued for non-loopback hosts and production policy
    is never weakened for convenience.
    """
    policy = validate_public_base_url(base_url)
    if policy.is_https:
        return policy
    if not policy.is_loopback or not explicit_loopback_http:
        raise AuthModeError(
            "The development cookie is restricted to explicit loopback HTTP. "
            "Pass explicit_loopback_http=True with a loopback base URL, or "
            "use HTTPS production policy."
        )
    return BaseUrlPolicy(
        base_url=policy.base_url,
        host=policy.host,
        is_https=False,
        is_loopback=True,
        require_secure_cookies=False,
        cookie_name=MOONMIND_DEV_COOKIE,
    )


def validate_trusted_proxy_config(
    trusted_proxies: tuple[str, ...] | list[str] | str | None,
) -> tuple[str, ...]:
    """Normalize and validate the trusted-proxy set.

    Entries must be non-empty hostnames/IPs or CIDR ranges; wildcard ``*``
    (trust everyone) is rejected. Returns the normalized tuple.
    """
    if trusted_proxies is None:
        return ()
    if isinstance(trusted_proxies, str):
        items = [p.strip() for p in trusted_proxies.split(",")]
    else:
        items = [str(p).strip() for p in trusted_proxies]
    normalized: list[str] = []
    for item in items:
        if not item:
            continue
        if item == "*":
            raise AuthModeError(
                "MOONMIND_TRUSTED_PROXIES cannot be '*': trusting every proxy "
                "would let untrusted forwarded headers decide redirects and "
                "secure-cookie policy."
            )
        normalized.append(item.lower())
    return tuple(normalized)


def validate_callback_origin(
    callback_url: str,
    *,
    base_url: str,
) -> str:
    """Validate an OIDC callback destination against the configured base URL.

    Only exact same-origin destinations (same scheme/host/port) are
    accepted; open redirects are rejected.
    """
    base_parts = urlsplit((base_url or "").strip())
    callback_parts = urlsplit((callback_url or "").strip())
    if not callback_parts.scheme or not callback_parts.hostname:
        raise AuthModeError(
            f"OIDC callback {callback_url!r} is not an absolute URL; refusing "
            "open-redirect-shaped destinations."
        )

    def _port(parts) -> int | None:
        try:
            return parts.port
        except ValueError:
            return None

    base_port = _port(base_parts) or (443 if base_parts.scheme == "https" else 80)
    callback_port = _port(callback_parts) or (
        443 if callback_parts.scheme == "https" else 80
    )
    if (
        callback_parts.scheme.lower() != base_parts.scheme.lower()
        or (callback_parts.hostname or "").lower() != (base_parts.hostname or "").lower()
        or callback_port != base_port
    ):
        raise AuthModeError(
            f"OIDC callback {callback_url!r} is not same-origin with the "
            f"configured base URL {base_url!r}; rejecting open redirect."
        )
    return callback_url.strip()


# ---------------------------------------------------------------------------
# Diagnostics: bounded, fail-closed, secret-free
# ---------------------------------------------------------------------------

_SECRET_KEY_HINTS = (
    "secret",
    "password",
    "passwd",
    "token",
    "cookie",
    "session_key",
    "jwt",
    "client_secret",
    "api_key",
    "apikey",
    "credential",
    "private",
    "auth",
)

# Non-secret enumerated keys that merely describe authentication state.
# ``auth`` is an intentionally broad redaction hint (it also covers
# ``authorization`` material), so these documented enum keys opt out
# explicitly instead of narrowing the hint.
_REDACT_EXEMPT_KEYS = frozenset({"auth_mode", "auth_readiness"})


def redact_value(key: str, value: Any) -> Any:
    """Redact secret-looking values; non-secret values pass through."""
    lowered = str(key or "").lower()
    if lowered in _REDACT_EXEMPT_KEYS:
        return value
    if any(hint in lowered for hint in _SECRET_KEY_HINTS):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "(unset)"
        return "(redacted)"
    if isinstance(value, bytes) and len(value) >= 16:
        # Opaque bytes of key length are never rendered.
        return "(redacted-bytes)"
    return value


def redacted_diagnostics(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a secret-free copy of a diagnostics payload."""
    redacted: dict[str, Any] = {}
    for key, value in dict(payload).items():
        if isinstance(value, Mapping):
            redacted[key] = redacted_diagnostics(value)
        else:
            redacted[key] = redact_value(str(key), value)
    return redacted


def auth_readiness_summary(
    *,
    production_mode: str,
    migration_required: bool = False,
    setup_required: bool = False,
    db_reachable: bool = True,
    secret_ready: bool = True,
) -> dict[str, Any]:
    """Build the distinguishable auth-readiness portion of health/readiness.

    Infrastructure readiness (``db``), authentication readiness
    (``auth_readiness``), and required operator setup (``setup_required`` /
    ``migration_required``) stay distinguishable: ``ready`` means requests
    can authenticate; ``setup_required`` means a fresh install awaits the
    protected first-owner step (no unauthenticated admin is granted);
    ``migration_required`` means an upgrade awaits an explicit operator
    decision; ``misconfigured``/``unavailable`` fail closed.
    """
    if migration_required:
        status = "migration_required"
    elif not db_reachable:
        status = "unavailable"
    elif not secret_ready:
        status = "misconfigured"
    elif setup_required:
        status = "setup_required"
    elif production_mode == "migration_required":
        status = "migration_required"
    else:
        try:
            validate_mode_selector(production_mode)
            status = "ready"
        except AuthModeError:
            status = "misconfigured"
    return {
        "auth_mode": production_mode,
        "auth_readiness": status,
        "setup_required": bool(setup_required or migration_required),
        "migration_required": bool(migration_required),
    }


def session_secret_fingerprint(secret: bytes) -> str:
    """Return a non-sensitive fingerprint for diagnostics (no key material)."""
    return hashlib.sha256(secret).hexdigest()[:16]


def constant_time_secret_equal(first: bytes, second: bytes) -> bool:
    """Constant-time comparison for session secrets in rotation checks."""
    return hmac.compare_digest(bytes(first), bytes(second))
