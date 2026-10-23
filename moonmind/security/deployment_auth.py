"""Deployment-owned signing keys and operator transport configuration."""

from __future__ import annotations

import base64
import ipaddress
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class DeploymentAuthError(ValueError):
    """Invalid deployment access or signing configuration."""


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

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


def resolve_signing_key(
    *,
    explicit_secret: str | None = None,
    key_path: Path,
    allow_generate: bool = True,
    for_remote_production: bool = False,
) -> bytes:
    """Resolve the MoonMind signing secret, failing closed.

    - Explicit values that are blank, placeholders, or under 32 bytes are
      rejected with actionable guidance (rotation belongs to the deployment,
      never silent regeneration).
    - Omitted values load the durable key at ``key_path`` when present, so
      restarts and concurrent replicas share one intended generation.
    - Omitted values with no durable key publish a fully written private
      file using an atomic, non-overwriting hard link. Concurrent bootstrap
      losers read back the winner's complete key instead of minting
      incompatible per-process keys. Remote production
      (``for_remote_production=True``) never generates implicitly; it
      requires explicit operator material.
    - Runtime-host, worker, and repository credentials are never consulted;
      only the explicit MoonMind secret and the deployment-owned key file
      are inputs.
    """
    path = Path(key_path)
    if explicit_secret is not None:
        # An explicitly provided blank is a misconfiguration, not an
        # omission: looks_like_placeholder_secret rejects blanks alongside known
        # placeholder shapes so callers cannot silently fall through to
        # durable generation with an operator intent to provide material.
        if looks_like_placeholder_secret(str(explicit_secret)):
            raise DeploymentAuthError(
                "The provided MoonMind signing secret is an insecure placeholder. "
                "Provide explicit operator-generated key material of at least "
                "32 bytes (or unset it for deployment-owned durable generation "
                "on loopback fresh installs). See "
                "docs/Security/AuthenticationContracts.md."
            )
        secret = _secret_to_bytes(str(explicit_secret))
        if len(secret) < _MIN_SECRET_BYTES:
            raise DeploymentAuthError(
                "The provided MoonMind signing secret is too short: at least "
                f"{_MIN_SECRET_BYTES} bytes of key material are required."
            )
        return secret
    # Omitted explicit secret: durable deployment-owned material wins.
    if path.is_file():
        try:
            stored = path.read_bytes()
        except OSError as exc:
            raise DeploymentAuthError(
                f"Unable to read the MoonMind signing key at {path}: {exc}. "
                "Failing closed rather than regenerating."
            ) from exc
        candidate = _durable_key_bytes(stored)
        if candidate is None:
            raise DeploymentAuthError(
                f"The MoonMind signing key at {path} is incomplete "
                f"({len(stored)} bytes); at least {_MIN_SECRET_BYTES} bytes "
                "are required. Rotate through the signing-key owner with fresh "
                "operator-generated material."
            )
        return candidate
    if for_remote_production:
        raise DeploymentAuthError(
            "No MoonMind signing secret is configured for a remote production "
            "deployment. Set an explicit operator-generated secret or provision "
            f"the deployment-owned key file at {path}; refusing to generate "
            "implicitly or accept a placeholder."
        )
    if not allow_generate:
        raise DeploymentAuthError(
            f"No MoonMind signing secret is available at {path} and implicit "
            "generation is disabled for this context. Failing closed."
        )
    # The authoritative path must never expose an empty or partially written
    # key. Write privately first, then atomically publish exactly one winner.
    generated = secrets.token_bytes(_MIN_SECRET_BYTES)
    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(generated)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            # Another replica published first. Reuse the canonical reader;
            # never replace or silently regenerate the winning generation.
            return resolve_signing_key(key_path=path, allow_generate=False)
    except OSError as exc:
        raise DeploymentAuthError(
            f"Unable to persist the MoonMind signing key at {path}: {exc}."
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                # A private temporary-file cleanup failure must not invalidate
                # an already published, authoritative key.
                pass
    return generated


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
            raise DeploymentAuthError(
                "MOONMIND_TRUSTED_PROXIES cannot be '*': trusting every proxy "
                "would let untrusted forwarded headers decide redirects and "
                "secure-cookie policy."
            )
        normalized.append(item.lower())
    return tuple(normalized)


def validate_public_base_url(base_url: str | None) -> None:
    """Require an explicit HTTPS remote URL or an HTTP loopback URL."""
    try:
        parts = urlsplit((base_url or "").strip())
        valid = bool(parts.hostname) and parts.scheme in {"https", "http"}
        valid = valid and (parts.scheme == "https" or _is_loopback_host(parts.hostname))
        # Accessing port validates malformed numeric/range input too.
        parts.port
    except ValueError:
        valid = False
    if not valid:
        raise DeploymentAuthError(
            "MOONMIND_PUBLIC_BASE_URL requires HTTPS or explicit HTTP loopback"
        )
