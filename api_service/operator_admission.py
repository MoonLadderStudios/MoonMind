"""FastAPI integration for the operator-admission boundary (#4347).

Thin adapter over :mod:`moonmind.security.operator_admission` (the owning
boundary). No ``User`` table, session store, or account-lifecycle imports
appear on this path: admission is transport + trusted-ingress proof only.

* :func:`require_operator` — strict dependency for operator HTTP routes
  (queries, mutations, artifact previews/downloads, SSE, chat controls).
* :func:`require_operator_optional` — worker-tolerant counterpart: absent
  admission signals proceed as ``None`` for separately authenticated
  machine paths; a bad presented credential is never swallowed.
* :func:`admit_websocket` — WebSocket handshake/reconnect admission.
  Query-string material is never consulted (credentials do not travel in
  URLs); only handshake headers and the connecting peer decide.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path

from fastapi import HTTPException, Request

from moonmind.security.operator_admission import (
    OperatorAdmission,
    OperatorAdmissionError,
    resolve_operator_admission,
)

logger = logging.getLogger(__name__)


def _http_exception(exc: OperatorAdmissionError) -> HTTPException:
    logger.info(
        "auth_event boundary=operator reason=denial code=%s status=%s",
        exc.code,
        exc.http_status,
    )
    if exc.http_status == 403:
        return HTTPException(status_code=403, detail={"code": exc.code})
    if exc.http_status == 503:
        return HTTPException(status_code=503, detail=exc.detail)
    return HTTPException(status_code=exc.http_status, detail={"code": exc.code})


def _request_inputs(request: Request) -> dict:
    headers = dict(request.headers)
    client_host = request.client.host if request.client else None
    return {
        "client_host": client_host,
        "host_header": request.headers.get("host"),
        "method": request.method or "GET",
        "headers": headers,
        "local_host_gateway": _local_compose_host_gateway(),
    }


def _local_compose_host_gateway() -> str | None:
    """Identify the Docker host peer for this instance's loopback publication.

    Docker port publication arrives from the host gateway, not 127.0.0.1.
    Trust only the container's single kernel default gateway, and only for a
    Compose deployment configured to publish loopback. Ordinary bridge peers
    and caller-supplied forwarding headers provide no such proof. Host-native
    servers and ambiguous/unreadable container routing remain loopback-only.
    """
    if (
        not os.environ.get("MOONMIND_DEPLOYMENT_PROJECT_NAME")
        or not Path("/.dockerenv").is_file()
    ):
        return None
    publish_host = os.environ.get("MOONMIND_API_PUBLISH_HOST", "").strip().strip("[]")
    try:
        loopback_publish = (
            publish_host == "localhost"
            or ipaddress.ip_address(publish_host).is_loopback
        )
    except ValueError:
        loopback_publish = False
    if not loopback_publish:
        return None
    try:
        gateways = {
            str(ipaddress.IPv4Address(bytes.fromhex(fields[2])[::-1]))
            for line in Path("/proc/net/route").read_text().splitlines()[1:]
            if (
                len(fields := line.split()) >= 8
                and fields[1] == "00000000"
                and fields[7] == "00000000"
                and int(fields[3], 16) & 3 == 3
                and fields[2] != "00000000"
            )
        }
    except (OSError, ValueError):
        return None
    return next(iter(gateways)) if len(gateways) == 1 else None


async def require_operator(request: Request) -> OperatorAdmission:
    """Strict operator boundary: every operator-facing path resolves here."""
    try:
        return resolve_operator_admission(**_request_inputs(request))
    except OperatorAdmissionError as exc:
        raise _http_exception(exc) from exc


async def enforce_disabled_mode_ingress(request: Request) -> None:
    """Apply the same admission to retained UUID consumers before resolution."""
    await require_operator(request)


async def require_operator_optional(request: Request) -> OperatorAdmission | None:
    """Optional counterpart for separately authenticated machine paths.

    Missing admission signals return ``None``; an invalid presented
    credential raises instead of falling back to anonymous success.
    """
    from moonmind.security.operator_admission import _proxy_identity_header_name

    inputs = _request_inputs(request)
    headers = {str(k).lower(): v for k, v in inputs["headers"].items()}
    try:
        loopback = ipaddress.ip_address(
            (inputs["client_host"] or "").strip().strip("[]").split("%")[0]
        ).is_loopback
    except ValueError:
        loopback = False
    host_peer = (
        bool(inputs["local_host_gateway"])
        and inputs["client_host"] == inputs["local_host_gateway"]
    )
    if not loopback and not host_peer and not headers.get(_proxy_identity_header_name()):
        # No admission signal presented: the separately authenticated
        # machine path authorizes below; nothing is swallowed.
        return None
    try:
        return resolve_operator_admission(**inputs)
    except OperatorAdmissionError as exc:
        raise _http_exception(exc) from exc


async def admit_websocket(websocket) -> OperatorAdmission:
    """Admit a WebSocket handshake through the operator boundary.

    Only handshake headers and the connecting peer are consulted; URL
    query parameters are ignored entirely so token-in-URL reconnects
    cannot acquire operator authority. The handshake is treated as
    origin-sensitive (CORS does not protect WebSockets): a presented
    foreign Origin/Referer is denied even though the upgrade itself is a
    GET, so a page on a foreign origin cannot open the loopback socket.
    """
    headers = dict(websocket.headers)
    client_host = websocket.client.host if websocket.client else None
    try:
        return resolve_operator_admission(
            client_host=client_host,
            host_header=headers.get("host"),
            # WebSocket upgrades are origin-sensitive; validating as an
            # unsafe method forces Origin/Referer checks that CORS alone
            # would never provide for sockets.
            method="POST",
            headers=headers,
            local_host_gateway=_local_compose_host_gateway(),
        )
    except OperatorAdmissionError as exc:
        logger.info(
            "auth_event boundary=operator reason=ws_denial code=%s",
            exc.code,
        )
        raise
