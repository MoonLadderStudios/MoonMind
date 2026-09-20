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
        # without one are unaffected.
        if upper_method not in _SAFE_METHODS:
            candidate = (origin or "").strip() or (referer or "").strip()
            if candidate and host_header:
                try:
                    candidate_host = (urlsplit(candidate).hostname or "").lower()
                except ValueError:
                    candidate_host = ""
                if candidate_host and candidate_host != _host_part(host_header):
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
    if not client_host or not proxies:
        return False
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
                continue
    return False


def _proxy_identity_header_name(environ: Mapping | None = None) -> str:
    source = environ if environ is not None else os.environ
    try:
        raw = (source.get("MOONMIND_PROXY_IDENTITY_HEADER", "") or "").strip()
    except Exception:
        raw = ""
    return (raw or _DEFAULT_PROXY_HEADER).lower()


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

    _check_host_and_origin(
        method=method,
        host_header=host_header,
        origin=str(origin) if origin is not None else None,
        referer=str(referer) if referer is not None else None,
        base_url=base_url,
    )

    if not base_url:
        # Fresh local operation: loopback transport is the boundary. The
        # client address (never forwarding headers, never the Host text)
        # decides; no new always-on service is required.
        if _is_loopback_ip(client_host):
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
    asserted = _single_header(
        header_map, _proxy_identity_header_name(source if isinstance(source, Mapping) else None)
    )
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
