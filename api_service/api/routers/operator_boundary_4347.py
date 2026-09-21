"""Operator-boundary demonstration surfaces (#4347).

Mounts the one operator-admission boundary across representative
operator surfaces: HTTP queries and mutations, artifact-style
previews/downloads, server-sent events, WebSocket handshake/reconnect,
and a control action standing in for chat controls. Every route resolves
through :mod:`api_service.operator_admission` (the thin adapter over the
owning :mod:`moonmind.security.operator_admission` boundary) with no
``User`` table, seeded identity, or app-login service on the path.

Stream policy: reconnects are admitted again, revoked admission closes
streams within ``STREAM_REVALIDATION_SECONDS``, and closing a stream
never cancels already-admitted work (the admitted-work ledger below is
independent of stream lifetime, as durable work is independent of
browser admission).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse

from api_service.operator_admission import (
    admit_websocket,
    require_operator,
)
from moonmind.security.operator_admission import (
    OperatorAdmission,
    OperatorAdmissionError,
    OperatorStreamPolicy,
    is_stream_authorization_stale,
    resolve_operator_admission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/operator", tags=["operator-boundary"])
ws_router = APIRouter(tags=["operator-boundary-ws"])

STREAM_POLICY = OperatorStreamPolicy()

# Admitted-work ledger: records control actions admitted through the
# boundary. Stream/SSE closure never removes entries: losing browser
# admission must not cancel admitted work.
ADMITTED_WORK: dict[str, dict] = {}

_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@router.get("/status")
async def operator_status(
    admission: OperatorAdmission = Depends(require_operator),
) -> dict:
    """Operator bootstrap probe: permitted operators reach this."""
    return {"admitted": True, "via": admission.via}


@router.post("/control")
async def operator_control(
    request: Request,
    admission: OperatorAdmission = Depends(require_operator),
) -> dict:
    """Operator mutation (chat-control class): origin-checked by the boundary."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    action = str((payload or {}).get("action", "")).strip() or "noop"
    work_id = str(uuid.uuid4())
    ADMITTED_WORK[work_id] = {
        "work_id": work_id,
        "action": action,
        "via": admission.via,
        "admitted_at": time.time(),
    }
    logger.info("auth_event boundary=operator reason=work_admitted via=%s", admission.via)
    return {"work_id": work_id, "action": action, "via": admission.via}


@router.get("/artifacts/{name}")
async def operator_artifact_download(
    name: str,
    admission: OperatorAdmission = Depends(require_operator),
) -> StreamingResponse:
    """Artifact preview/download through the real admission code."""
    if not _ARTIFACT_NAME_RE.fullmatch(name or ""):
        raise HTTPException(status_code=404, detail={"code": "artifact_not_found"})

    async def _body():
        yield f"operator artifact {name} via {admission.via}\n".encode()

    return StreamingResponse(_body(), media_type="text/plain")


@router.get("/events")
async def operator_events(
    request: Request,
    admission: OperatorAdmission = Depends(require_operator),
) -> StreamingResponse:
    """Server-sent events with bounded revalidation on the admitting boundary."""
    client_host = request.client.host if request.client else None
    headers = dict(request.headers)
    host_header = request.headers.get("host")
    admitted_at = time.time()

    async def _stream():
        ticks = 0
        while ticks < 3:
            try:
                resolve_operator_admission(
                    client_host=client_host,
                    host_header=host_header,
                    method="GET",
                    headers=headers,
                )
            except OperatorAdmissionError:
                # Revoked admission closes the stream within the bound.
                yield "event: close\ndata: admission_revoked\n\n"
                return
            if is_stream_authorization_stale(admitted_at=admitted_at, now=time.time()):
                yield "event: close\ndata: revalidation_due\n\n"
                return
            yield f"data: tick {ticks} via {admission.via}\n\n"
            ticks += 1
            await asyncio.sleep(0)

    return StreamingResponse(_stream(), media_type="text/event-stream")


_WS_CLOSE_FOR_CODE = {
    "auth_required": 4401,
    "auth_invalid": 4401,
    "host_forbidden": 4403,
    "origin_forbidden": 4403,
    "misconfigured": 4413,
    "unavailable": 4413,
}


@ws_router.websocket("/console")
async def operator_console(websocket: WebSocket) -> None:
    """Operator console socket: handshake + reconnect admission, bounded life."""
    try:
        admission = await admit_websocket(websocket)
    except OperatorAdmissionError as exc:
        # Never accepted, so no close frame can be sent; the handshake is
        # refused by raising (Starlette denies the upgrade).
        raise RuntimeError(f"operator admission denied: {exc.code}") from exc
    await websocket.accept()
    admitted_at = time.time()
    headers = dict(websocket.headers)
    client_host = websocket.client.host if websocket.client else None
    host_header = headers.get("host")
    try:
        while True:
            try:
                resolve_operator_admission(
                    client_host=client_host,
                    host_header=host_header,
                    method="GET",
                    headers=headers,
                )
            except OperatorAdmissionError as exc:
                await websocket.close(
                    code=_WS_CLOSE_FOR_CODE.get(exc.code, 4401),
                    reason=f"admission_revoked:{exc.code}",
                )
                return
            if is_stream_authorization_stale(admitted_at=admitted_at, now=time.time()):
                await websocket.close(code=4401, reason="revalidation_due")
                return
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            except asyncio.TimeoutError:
                await websocket.send_text(f"heartbeat via {admission.via}")
                continue
            if message.strip() == "close":
                await websocket.close(code=1000, reason="operator_close")
                return
            await websocket.send_text(f"echo via {admission.via}: {message}")
    except Exception:
        # Best-effort close after an unexpected stream error: the socket may
        # already be broken, so a failing close must not raise or mask the
        # original failure.
        try:
            await websocket.close(code=1011, reason="operator_error")
        except Exception:
            # The peer is gone or the socket never accepted; nothing to do.
            pass
