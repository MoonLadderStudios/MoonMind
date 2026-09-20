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

import logging

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
    }


async def require_operator(request: Request) -> OperatorAdmission:
    """Strict operator boundary: every operator-facing path resolves here."""
    try:
        return resolve_operator_admission(**_request_inputs(request))
    except OperatorAdmissionError as exc:
        raise _http_exception(exc) from exc


async def require_operator_optional(request: Request) -> OperatorAdmission | None:
    """Optional counterpart for separately authenticated machine paths.

    Missing admission signals return ``None``; an invalid presented
    credential raises instead of falling back to anonymous success.
    """
    import ipaddress

    from moonmind.security.operator_admission import _proxy_identity_header_name

    inputs = _request_inputs(request)
    headers = {str(k).lower(): v for k, v in inputs["headers"].items()}
    try:
        loopback = ipaddress.ip_address(
            (inputs["client_host"] or "").strip().strip("[]").split("%")[0]
        ).is_loopback
    except ValueError:
        loopback = False
    if not loopback and not headers.get(_proxy_identity_header_name()):
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
    cannot acquire operator authority.
    """
    headers = dict(websocket.headers)
    client_host = websocket.client.host if websocket.client else None
    try:
        return resolve_operator_admission(
            client_host=client_host,
            host_header=headers.get("host"),
            method="GET",
            headers=headers,
        )
    except OperatorAdmissionError as exc:
        logger.info(
            "auth_event boundary=operator reason=ws_denial code=%s",
            exc.code,
        )
        raise
