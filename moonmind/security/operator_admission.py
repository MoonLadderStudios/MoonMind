"""Single-user operator-admission boundary (MoonLadderStudios/MoonMind#4347).

Parent: #4345. Source: ``docs/SingleUserApplicationDesign.md`` sections 1-4
and 10.

This module owns the one shared operator admission boundary for the
operator interface. Admission establishes permission to use the operator
interface; it never resolves a persisted person. Business services receive
resources and requested actions, never an always-admin ``User`` substitute.

Boundary rules (exact, see ``docs/Security/AuthenticationContracts.md``
§ operator admission):

* Fresh local operation (no ``MOONMIND_PUBLIC_BASE_URL`` configured) admits
  loopback transport only: the connecting client address must be loopback
  (``127.0.0.0/8`` or ``::1``). No accounts, login, user provisioning, or
  application-session database are consulted. A loopback ``Host`` header
  from a non-loopback peer (for example a workload container reaching the
  backend over the Docker bridge) never admits.
* Remote operation (``MOONMIND_PUBLIC_BASE_URL`` configured) admits only
  through the deployment's approved trusted ingress: the existing
  ``MOONMIND_TRUSTED_INGRESS=1`` + ``MOONMIND_TRUSTED_PROXIES`` proof plus
  the configured proxy identity header as boolean admission. The asserted
  identity is never mapped to a local account, and upstream subjects,
  roles, and membership are never imported. Container-local loopback
  without that proof never admits once a remote URL is configured.
* Caller-controlled forwarding/identity headers (``X-Forwarded-*``,
  ``X-Real-IP``, untrusted ``X-Moonmind-User``), worker credentials
  (execution-fanout markers, worker bearer material), and query-string
  material never confer operator access. This module accepts no query
  input by construction.
* Host/origin validation is independent of any account lifecycle: a
  ``Host`` that disagrees with the configured base URL, or a
  present-but-foreign ``Origin``/``Referer`` on an unsafe method, is
  denied (403) before admission is considered. CORS configuration alone
  is never the admission control.
* Admission outages (misconfigured ingress while remote) return 503
  ``unavailable``; invalid or absent presented credentials return
  401 ``auth_required``/``auth_invalid``. There is no default user,
  account fallback, or weakened access on any path.

Stream policy reuses the existing session-authority bound
(``SESSION_REVOCATION_INTERVAL_SECONDS``): reconnects are admitted again,
revoked admission closes streams within the bound, and losing browser
admission never cancels already-admitted durable work.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

try:  # Reuse the existing bound; never a second session authority.
    from moonmind.security.session_authority_4121 import (
        SESSION_REVOCATION_INTERVAL_SECONDS as _SESSION_BOUND,
    )
except Exception:  # pragma: no cover - import-time fallback only.
    _SESSION_BOUND = 5 * 60

#: Streams revalidate (or close after revocation) within this bound.
STREAM_REVALIDATION_SECONDS = _SESSION_BOUND

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

_DEFAULT_PROXY_HEADER = "X-Moonmind-User"
_STABLE_ID_RE = re.compile(r"^[A-Za-z0-9_.@+=/-]{1,256}$")
_RESERVED_IDENTITIES = frozenset({"local", "__public__"})

# Headers that are never admission material. They are accepted on the wire
# for their owning contracts (machine authority, proxying) but must not
# confer operator access here.
_IGNORED_IDENTITY_HEADERS = frozenset(
    {
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-port",
        "x-real-ip",
        "x-moonmind-execution-fanout",
    }
)

#: Paths intentionally public. Narrow: liveness/readiness and protocol
#: probes only. Everything else requires operator admission.
OPERATOR_PUBLIC_PATHS = frozenset(
    {
        "/healthz",
        "/health",
        "/ready",
        "/api/health",
        "/api/v1/health",
    }
)


class OperatorAdmissionError(Exception):
    """Fail-closed operator-admission denial with a stable code."""

    VALID_CODES = frozenset(
        {
            "auth_required",
            "auth_invalid",
            "host_forbidden",
            "origin_forbidden",
            "misconfigured",
            "unavailable",
        }
    )

    def __init__(self, code: str, detail: str = ""):
        if code not in self.VALID_CODES:
            raise ValueError(f"unknown operator admission code {code!r}")
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code

    @property
    def http_status(self) -> int:
        if self.code in ("auth_required", "auth_invalid"):
            return 401
        if self.code in ("host_forbidden", "origin_forbidden"):
            return 403
        return 503


@dataclass(frozen=True)
class OperatorAdmission:
    """Permission to use the operator interface (no person identity)."""

    via: str  # "loopback" | "trusted_ingress"
    subject: None = None  # Always None: no persisted person is resolved.

    def describe(self) -> str:
        return (
            "operator admission via "
            f"{self.via}; no application user is resolved, imported, "
            "or created."
        )


@dataclass(frozen=True)
class OperatorStreamPolicy:
    """Bounded stream revalidation/closure policy for admitted streams."""

    revalidate_every_seconds: float = STREAM_REVALIDATION_SECONDS
    closes_streams_on_revocation: bool = True
    cancels_admitted_work: bool = False

    def describe(self) -> str:
        return (
            "Streams admitted through the operator boundary revalidate at "
            f"least every {self.revalidate_every_seconds:g}s; revoked "
            "admission closes streams within that bound while durable "
            "already-admitted work continues under its own recorded intent "
            "and scoped machine authority."
        )


def is_operator_public_path(path: str) -> bool:
    """Return True only for the narrow intentionally-public probes."""
    candidate = (path or "").strip().split("?", 1)[0].rstrip("/") or "/"
    if candidate == "/":
        return False
    return candidate in OPERATOR_PUBLIC_PATHS


def _is_loopback_ip(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip().strip("[]")
    # Zone identifiers (fe80::1%eth0) are never loopback for this boundary.
    if "%" in text:
        return False
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _host_part(host_header: str | None) -> str:
    if not host_header:
        return ""
    text = host_header.strip().lower()
    if text.startswith("["):  # bracketed IPv6 with optional port.
        end = text.find("]")
        return text[1:end] if end != -1 else text
    return text.split(":")[0].strip("[] ")


def _is_loopback_host(host_header: str | None) -> bool:
    host = _host_part(host_header)
    if host in ("localhost",):
        return True
    return _is_loopback_ip(host)


def _effective_port(scheme: str, port: int | None) -> int:
    if port:
        return port
    return 443 if scheme.lower() == "https" else 80


def _public_base_url(environ: Mapping | None = None) -> str:
    source = environ if environ is not None else os.environ
    try:
        return (source.get("MOONMIND_PUBLIC_BASE_URL", "") or "").strip()
    except Exception:
        return ""


def _is_loopback_base_url(base_url: str) -> bool:
    """Whether a configured base URL is an explicit loopback URL (local path).

    Reuses the existing base-URL authority
    (``public_base_url_is_loopback``) so ``http://127.0.0.1:7000``,
    ``http://localhost:7000``, and loopback IPv6 stay on the fresh-local
    loopback path instead of forcing remote trusted-ingress proof.
    Blank or unparseable values return False; callers keep blank handling.
    """
    text = (base_url or "").strip()
    if not text:
        return False
    try:
        from moonmind.security.auth_modes_4120 import (
            public_base_url_is_loopback as _authority_is_loopback,
        )

        return bool(_authority_is_loopback(text))
    except Exception:
        try:
            host = urlsplit(text).hostname or ""
        except ValueError:
            return False
        return _is_loopback_host(host)


def _local_origin_denied(*, candidate: str, host_header: str | None) -> bool:
    """Whether a presented local Origin/Referer must be denied.

    Compares the complete origin (scheme, hostname, effective port) and
    rejects any presented value that cannot be parsed (including opaque
    ``null``). Host-only comparison would accept ``http://localhost:3000``
    for ``Host: localhost:7000`` despite different browser origins.
    """
    text = (candidate or "").strip()
    if not text:
        return False
    try:
        parts = urlsplit(text)
    except ValueError:
        return True
    candidate_host = (parts.hostname or "").lower()
    if not candidate_host:
        return True
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return True
    host_text = (host_header or "").strip()
    if not host_text:
        return True
    # Host header carries host[:port] without a scheme; compare hostname
    # plus effective port. The request scheme is unknown here, so accept
    # either http or https default only when the candidate port matches
    # the Host port (or its scheme default).
    request_host = _host_part(host_header)
    if not request_host:
        return True
    if candidate_host != request_host:
        return True
    try:
        host_port: int | None = None
        raw_host = host_text.lower()
        if raw_host.startswith("["):
            end = raw_host.find("]")
            rest = raw_host[end + 1 :] if end != -1 else ""
            if rest.startswith(":"):
                host_port = int(rest[1:].split("/")[0].strip() or 0) or None
        elif ":" in raw_host:
            tail = raw_host.rsplit(":", 1)[1].split("/")[0].strip()
            try:
                host_port = int(tail) or None
            except ValueError:
                host_port = None
    except (ValueError, IndexError):
        host_port = None
    try:
        candidate_port = parts.port
    except ValueError:
        return True
    if host_port is not None:
        return candidate_port != host_port and _effective_port(
            scheme, candidate_port
        ) != host_port and not (
            candidate_port is None
            and _effective_port(scheme, None) == host_port
        )
    return False


def _check_host_and_origin(
    *,
    method: str,
    host_header: str | None,
    origin: str | None,
    referer: str | None,
    base_url: str,
) -> None:
    """Deny hostile Host/origin before admission is considered.

    Independent of any account lifecycle: no session, user, or membership
    state participates. A missing Host/origin is not proof of anything and
    never admits; only present-but-foreign values are denied here.
    """
    upper_method = (method or "GET").upper()
    if base_url:
        try:
            expected = urlsplit(base_url)
            expected_host = (expected.hostname or "").lower()
        except ValueError:
            expected_host = ""
        if expected_host and host_header:
            if _host_part(host_header) != expected_host:
                raise OperatorAdmissionError(
                    "host_forbidden",
                    "Host does not match the configured operator URL.",
                )
        candidate = (origin or "").strip() or (referer or "").strip()
        if candidate and upper_method not in _SAFE_METHODS:
            try:
                parts = urlsplit(candidate)
                candidate_host = (parts.hostname or "").lower()
                same_origin = (
                    candidate_host
                    and candidate_host == expected_host
                    and (parts.scheme or "").lower()
                    == (expected.scheme or "").lower()
                    and _effective_port(parts.scheme, parts.port)
                    == _effective_port(expected.scheme, expected.port)
                )
            except ValueError:
                same_origin = False
            if not same_origin:
                raise OperatorAdmissionError(
                    "origin_forbidden",
                    "Cross-origin mutation is not admitted.",
                )
    else:
        # Local-only deployment: still stop DNS-rebinding-style mutations
        # where a foreign page drives the loopback backend. Only deny when
        # a foreign Origin/Referer is actually presented; CLI/API clients
        # without one are unaffected. The complete origin (scheme, host,
        # effective port) must match the Host; opaque or unparsable values
        # such as ``null`` are denied.
        if upper_method not in _SAFE_METHODS:
            candidate = (origin or "").strip() or (referer or "").strip()
            if candidate:
                if _local_origin_denied(
                    candidate=candidate, host_header=host_header
                ):
                    raise OperatorAdmissionError(
                        "origin_forbidden",
                        "Cross-origin mutation is not admitted.",
                    )


def _trusted_proxies(environ: Mapping | None = None) -> tuple[str, ...]:
    from moonmind.security.auth_modes_4120 import validate_trusted_proxy_config

    source = environ if environ is not None else os.environ
    try:
        raw = source.get("MOONMIND_TRUSTED_PROXIES", "")
    except Exception:
        raw = ""
    if isinstance(raw, str):
        entries = raw
    else:
        entries = list(raw or [])
    try:
        return validate_trusted_proxy_config(entries)
    except Exception:
        return ()


def _client_matches_proxies(client_host: str | None, proxies: tuple[str, ...]) -> bool:
    """Whether a connecting peer matches the configured trusted proxies.

    Reuses the existing trusted-proxy matcher
    (``moonmind.security.trusted_proxy_4124._peer_is_trusted``), which pins
    configured hostnames against numeric peers via DNS. A divergent local
    implementation would skip hostname entries for numeric peers and force
    ``auth_required`` on supported reverse-proxy configurations.
    """
    if not client_host or not proxies:
        return False
    try:
        from moonmind.security.trusted_proxy_4124 import (
            _peer_is_trusted as _existing_peer_is_trusted,
        )

        return bool(_existing_peer_is_trusted(client_host, tuple(proxies)))
    except Exception:
        pass
    text = client_host.strip().strip("[]").split("%")[0]
    try:
        client_ip = ipaddress.ip_address(text)
    except ValueError:
        # Hostnames pin by exact match or by resolving once; unresolvable
        # names never match (fail closed).
        import socket

        lowered = text.lower()
        for entry in proxies:
            if "/" in entry:
                continue
            if entry.lower() == lowered:
                return True
            try:
                infos = socket.getaddrinfo(entry, None)
            except OSError:
                continue
            if any(info[4][0] == text for info in infos):
                return True
        return False
    for entry in proxies:
        entry_text = entry.strip().lower()
        if "/" in entry_text:
            try:
                if client_ip in ipaddress.ip_network(entry_text, strict=False):
                    return True
            except ValueError:
                continue
        else:
            try:
                if client_ip == ipaddress.ip_address(entry_text.strip("[]")):
                    return True
            except ValueError:
                # Configured hostname entries resolve against numeric peers;
                # without DNS resolution every request through that supported
                # proxy configuration would receive ``auth_required``.
                try:
                    import socket as _socket

                    infos = _socket.getaddrinfo(entry_text, None)
                except OSError:
                    continue
                for info in infos:
                    try:
                        if ipaddress.ip_address(
                            str(info[4][0]).strip().strip("[]")
                        ) == client_ip:
                            return True
                    except ValueError:
                        continue
                continue
    return False


def _proxy_identity_header_name(environ: Mapping | None = None) -> str:
    source = environ if environ is not None else os.environ
    try:
        raw = (source.get("MOONMIND_PROXY_IDENTITY_HEADER", "") or "").strip()
    except Exception:
        raw = ""
    return (raw or _DEFAULT_PROXY_HEADER).lower()


def _ensure_allowed_proxy_identity_header(name: str) -> None:
    """Fail closed when the identity header itself is a forwarding header.

    ``_IGNORED_IDENTITY_HEADERS`` declares forwarding and worker headers
    that must never confer operator access. Accepting one of them as the
    configured ``MOONMIND_PROXY_IDENTITY_HEADER`` would let an ordinary
    well-formed address inserted by a trusted proxy become a valid
    assertion even when the proxy supplied no authenticated identity.
    """
    if (name or "").strip().lower() in _IGNORED_IDENTITY_HEADERS:
        raise OperatorAdmissionError(
            "misconfigured",
            "The configured proxy identity header must not be a forwarding "
            "or worker header.",
        )


def _single_header(headers: Mapping, name: str) -> str | None:
    """Return the single header value, or None when absent.

    Duplicated identity assertions fail closed at the caller; this helper
    surfaces the raw transport shape (str vs list) for that decision.
    """
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    value = lowered.get(name.lower())
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if str(v).strip()]
        if len(items) != 1:
            return "__duplicated__"
        return items[0]
    text = str(value).strip()
    return text or None


def _well_formed_proxy_identity(value: str | None) -> bool:
    if not value or value == "__duplicated__":
        return False
    if value in _RESERVED_IDENTITIES:
        return False
    if "@" in value and "/" not in value and "." not in value and "+" not in value:
        # Bare email-shaped identifiers require explicit enrollment policy;
        # the boolean boundary never merges them.
        return False
    return bool(_STABLE_ID_RE.fullmatch(value))


def resolve_operator_admission(
    *,
    client_host: str | None,
    host_header: str | None = None,
    method: str = "GET",
    headers: Mapping | None = None,
    environ: Mapping | None = None,
) -> OperatorAdmission:
    """Resolve operator admission for one request (no I/O, no database).

    ``headers`` maps header names to values (case-insensitive); query
    parameters are never consulted. Raises :class:`OperatorAdmissionError`
    for denial/unavailability. Never returns a default user or account
    fallback.
    """
    source = environ if environ is not None else os.environ
    try:
        trusted_ingress = (source.get("MOONMIND_TRUSTED_INGRESS", "") or "").strip() == "1"
    except Exception:
        trusted_ingress = False
    base_url = _public_base_url(source if isinstance(source, Mapping) else None)
    header_map = {str(k).lower(): v for k, v in dict(headers or {}).items()}
    origin = header_map.get("origin")
    referer = header_map.get("referer")
    if isinstance(origin, (list, tuple)):
        origin = origin[0] if origin else None
    if isinstance(referer, (list, tuple)):
        referer = referer[0] if referer else None

    # Explicit loopback base URLs stay on the fresh-local path: forcing
    # trusted-ingress proof for http://127.0.0.1:7000 (or localhost/::1)
    # would deny loopback-bound development while the equivalent omitted
    # base works. Host/origin checks below use local rules for them.
    loopback_base = _is_loopback_base_url(base_url)
    _check_host_and_origin(
        method=method,
        host_header=host_header,
        origin=str(origin) if origin is not None else None,
        referer=str(referer) if referer is not None else None,
        base_url="" if loopback_base else base_url,
    )

    if not base_url or loopback_base:
        # Fresh local operation: loopback transport plus a loopback Host is
        # the boundary. The peer alone is not enough: during DNS rebinding
        # a page from attacker.example can re-resolve to 127.0.0.1, so the
        # server sees a loopback peer while Host/Origin stay attacker
        # controlled. A loopback Host from a non-loopback peer (workload
        # container over the Docker bridge) never admits either.
        if _is_loopback_ip(client_host) and _is_loopback_host(host_header):
            logger.info("auth_event boundary=operator reason=admitted via=loopback")
            return OperatorAdmission(via="loopback")
        logger.info("auth_event boundary=operator reason=denial code=auth_required")
        raise OperatorAdmissionError(
            "auth_required",
            "Operator access requires loopback transport for a local-only "
            "deployment. Configure the deployment's approved remote access "
            "to admit non-loopback clients.",
        )

    # Remote operation: only the approved trusted ingress admits, using the
    # minimal existing ingress proof. Container-local loopback without that
    # proof never admits here.
    proxies = _trusted_proxies(source if isinstance(source, Mapping) else None)
    if not trusted_ingress or not proxies:
        logger.info("auth_event boundary=operator reason=denial code=unavailable")
        raise OperatorAdmissionError(
            "unavailable",
            "Operator admission is not configured: a remote operator URL "
            "requires MOONMIND_TRUSTED_INGRESS=1 with MOONMIND_TRUSTED_PROXIES "
            "and an audited ingress path.",
        )
    if not _client_matches_proxies(client_host, proxies):
        # Direct-backend, forged-header, and worker-credential attempts all
        # land here: without a trusted-peer source address no header,
        # token, or marker confers operator access.
        logger.info("auth_event boundary=operator reason=denial code=auth_required")
        raise OperatorAdmissionError(
            "auth_required",
            "Operator access requires the configured trusted ingress.",
        )
    identity_header = _proxy_identity_header_name(
        source if isinstance(source, Mapping) else None
    )
    _ensure_allowed_proxy_identity_header(identity_header)
    asserted = _single_header(header_map, identity_header)
    if not _well_formed_proxy_identity(asserted):
        logger.info("auth_event boundary=operator reason=denial code=auth_invalid")
        raise OperatorAdmissionError(
            "auth_invalid",
            "The trusted ingress did not present a valid operator assertion.",
        )
    logger.info("auth_event boundary=operator reason=admitted via=trusted_ingress")
    return OperatorAdmission(via="trusted_ingress")


def is_stream_authorization_stale(*, admitted_at: float, now: float) -> bool:
    """Return True when a stream must revalidate (or close) now."""
    return (now - admitted_at) >= STREAM_REVALIDATION_SECONDS
