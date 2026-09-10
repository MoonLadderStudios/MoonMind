"""Trusted-header advanced identity source (MoonLadderStudios/MoonMind#4124).

Parent: #4116. Depends on #4118, #4119, #4120, #4121. Plan coverage: K4
trusted-proxy mode in ``docs/tmp/KeycloakRemovalPlan.md``.

Portable, runtime-neutral capability: trust is defined by the connecting
proxy/network (explicit trusted ingress + trusted proxy set), never by a
header claiming it is trusted. The ingress strips and replaces identity
headers; this module validates what the trusted ingress asserted and maps
it through the #4119 identity authority.

Contract (AuthenticationContracts §§3-4,6):

* Enabled only behind explicitly trusted ingress
  (``MOONMIND_TRUSTED_INGRESS=1``) with a non-empty trusted-proxy set
  (``MOONMIND_TRUSTED_PROXIES``); wildcard ``*`` is rejected by the #4120
  owner. Direct API connections and alternative listeners never accept
  user-controlled identity headers.
* One operator-configured identity namespace
  (``MOONMIND_PROXY_IDENTITY_NAMESPACE``) plus one stable asserted
  identifier per request, mapped as ``(issuer, subject)`` with
  ``issuer = "proxy:<namespace>"`` through #4119. Identity is never
  derived from email.
* Duplicated, missing, malformed, reserved (``local``/``__public__``),
  or unknown header identities fail closed. Authenticated deployments
  never invoke ``local``/``__public__`` fallback.
* Email-only proxy integrations require explicit enrollment policy and
  cannot silently merge existing users (enforced in the service layer
  via #4119 ``email_taken`` semantics; this module rejects bare
  ``@``-shaped identifiers unless ``allow_email_identities`` is set).
* Attacker-controlled ``X-Forwarded-Host``/``X-Forwarded-Proto`` values
  never decide origins: any such values from an untrusted peer are
  rejected, and even from a trusted peer they never override the
  configured base URL.
* The asserted header and unrelated runtime credentials are never
  forwarded to other services (see :func:`safe_outbound_headers`).
* Proxy logout/revocation semantics are honest: upstream assertions
  continue while the proxy sends them; local revocation (session revoke,
  account disablement) still blocks every request at validation time.
  There is no IdP-wide logout to claim.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_PROXY_HEADER = "X-Moonmind-User"
MAX_STABLE_ID_LENGTH = 256
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_STABLE_ID_RE = re.compile(r"^[A-Za-z0-9_.@+=/-]{1,256}$")
_RESERVED_IDENTITIES = frozenset({"local", "__public__"})


class ProxyConfigError(ValueError):
    """Fail-closed trusted-proxy configuration error."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "misconfigured"


class ProxyAuthError(RuntimeError):
    """Fail-closed proxy assertion failure with a stable code."""

    def __init__(self, code: str, detail: str = ""):
        if code not in ("auth_required", "auth_invalid", "misconfigured"):
            raise ValueError(f"unknown proxy error code {code!r}")
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


@dataclass(frozen=True)
class TrustedProxyConfig:
    """Explicit trusted-proxy configuration."""

    namespace: str
    header_name: str = DEFAULT_PROXY_HEADER
    trusted_proxies: tuple[str, ...] = ()
    trusted_ingress: bool = False
    allow_email_identities: bool = False

    def __post_init__(self) -> None:
        namespace = (self.namespace or "").strip()
        if not namespace or not _NAMESPACE_RE.fullmatch(namespace):
            raise ProxyConfigError(
                "MOONMIND_PROXY_IDENTITY_NAMESPACE must be 1-64 lowercase "
                "alphanumeric/dash characters starting alphanumerically."
            )
        if not (self.header_name or "").strip():
            raise ProxyConfigError("proxy identity header name is required.")
        if not self.trusted_ingress:
            raise ProxyConfigError(
                "Trusted-header mode requires MOONMIND_TRUSTED_INGRESS=1 with "
                "an audited ingress path; refusing to trust headers without it."
            )
        if not self.trusted_proxies:
            raise ProxyConfigError(
                "Trusted-header mode requires MOONMIND_TRUSTED_PROXIES with at "
                "least one trusted proxy; refusing to trust headers without it."
            )


def resolve_trusted_proxy_config(
    *,
    namespace: str | None,
    trusted_proxies: tuple[str, ...] | list[str] | str | None,
    trusted_ingress: bool,
    header_name: str = DEFAULT_PROXY_HEADER,
    allow_email_identities: bool = False,
) -> TrustedProxyConfig:
    """Build a validated trusted-proxy config from explicit inputs."""
    from moonmind.security.auth_modes_4120 import validate_trusted_proxy_config

    try:
        proxies = validate_trusted_proxy_config(trusted_proxies)
    except Exception as exc:
        raise ProxyConfigError(str(exc)) from exc
    return TrustedProxyConfig(
        namespace=(namespace or "").strip().lower(),
        header_name=(header_name or DEFAULT_PROXY_HEADER).strip(),
        trusted_proxies=proxies,
        trusted_ingress=bool(trusted_ingress),
        allow_email_identities=bool(allow_email_identities),
    )


def proxy_issuer_for_namespace(namespace: str) -> str:
    """Return the #4119 issuer for a proxy namespace (``proxy:<ns>``)."""
    text = (namespace or "").strip().lower()
    if not text or not _NAMESPACE_RE.fullmatch(text):
        raise ProxyConfigError("invalid proxy identity namespace.")
    return f"proxy:{text}"


def _peer_is_trusted(peer: str, trusted_proxies: tuple[str, ...]) -> bool:
    candidate = (peer or "").strip().lower().strip("[]")
    if not candidate:
        return False
    for entry in trusted_proxies:
        item = (entry or "").strip().lower()
        if not item:
            continue
        if "/" in item:
            try:
                network = ipaddress.ip_network(item, strict=False)
            except ValueError:
                continue
            try:
                if ipaddress.ip_address(candidate) in network:
                    return True
            except ValueError:
                continue
        elif candidate == item:
            return True
    return False


def validate_ingress_forwarded_headers(
    *,
    forwarded_host: str | None,
    forwarded_proto: str | None,
    peer_ip: str,
    trusted_proxies: tuple[str, ...],
) -> None:
    """Reject untrusted forwarded host/proto values (fail-closed).

    Forwarded values from an untrusted peer are rejected outright. Values
    from a trusted peer are tolerated on the wire but must never override
    the configured base URL (callers keep using ``MOONMIND_PUBLIC_BASE_URL``).
    """
    if forwarded_host is None and forwarded_proto is None:
        return
    if not _peer_is_trusted(peer_ip, trusted_proxies):
        raise ProxyAuthError(
            "auth_invalid", "untrusted forwarded host/proto headers"
        )


def extract_proxy_identity(
    *,
    headers_multi: list[tuple[str, str]],
    peer_ip: str,
    config: TrustedProxyConfig,
    forwarded_host: str | None = None,
    forwarded_proto: str | None = None,
):
    """Validate the asserted proxy identity, returning a ValidatedIdentity.

    ``headers_multi`` preserves duplicates (a dict would hide them):
    zero or 2+ identity headers fail closed. The peer must be a trusted
    proxy; direct connections are rejected before any header is read.
    Reserved, malformed, and (by default) email-shaped identifiers fail
    closed. Unknown identifiers are *not* resolved here; the service
    layer maps through #4119 and fails closed on unknown/unenrolled
    identities.
    """
    from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

    if not _peer_is_trusted(peer_ip, config.trusted_proxies):
        raise ProxyAuthError("auth_invalid", "untrusted proxy peer")
    validate_ingress_forwarded_headers(
        forwarded_host=forwarded_host,
        forwarded_proto=forwarded_proto,
        peer_ip=peer_ip,
        trusted_proxies=config.trusted_proxies,
    )
    wanted = config.header_name.strip().lower()
    asserted = [v for (k, v) in headers_multi if str(k).strip().lower() == wanted]
    if not asserted:
        raise ProxyAuthError("auth_required", "missing proxy identity")
    if len(asserted) > 1:
        raise ProxyAuthError("auth_invalid", "duplicated proxy identity")
    stable_id = (asserted[0] or "").strip()
    if not stable_id:
        raise ProxyAuthError("auth_required", "missing proxy identity")
    if stable_id in _RESERVED_IDENTITIES:
        raise ProxyAuthError("auth_invalid", "reserved proxy identity")
    if len(stable_id) > MAX_STABLE_ID_LENGTH or not _STABLE_ID_RE.fullmatch(stable_id):
        raise ProxyAuthError("auth_invalid", "malformed proxy identity")
    if "@" in stable_id and not config.allow_email_identities:
        # Email-only proxy integrations need explicit enrollment policy;
        # without it a bare email here would invite silent account merges
        # in a looser service layer. Fail closed at the boundary.
        raise ProxyAuthError("auth_invalid", "email identity requires enrollment policy")
    issuer = proxy_issuer_for_namespace(config.namespace)
    try:
        return ValidatedIdentity(issuer=issuer, subject=stable_id)
    except Exception as exc:
        raise ProxyAuthError("auth_invalid", "reserved proxy identity") from exc


def safe_outbound_headers(headers: dict[str, str], *, proxy_header: str) -> dict[str, str]:
    """Strip the asserted identity header before calling other services.

    A trusted header is a transport assertion for this ingress only; it
    must never be forwarded as browser/worker authority, and unrelated
    runtime credentials (``Authorization``, ``Cookie``) are never
    forwarded implicitly by this helper either.
    """
    unwanted = {proxy_header.strip().lower(), "authorization", "cookie"}
    return {k: v for k, v in headers.items() if k.strip().lower() not in unwanted}


PROXY_LOGOUT_LIMITATION = (
    "Proxy logout clears the local MoonMind session only. The upstream proxy "
    "may continue asserting the same identity on the next request; upstream "
    "revocation is owned by the proxy. Local account disablement still blocks "
    "every request at validation time."
)

OIDC_LOGOUT_LIMITATION = (
    "MoonMind logout always invalidates the local session even when the "
    "optional IdP end-session call fails. IdP-wide logout (single logout "
    "across all clients) is not guaranteed and must not be claimed."
)
