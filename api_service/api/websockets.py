import asyncio
import json
import logging
import shlex
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from api_service.db.base import get_async_session
from api_service.db.models import OAuthSessionStatus, User, ManagedAgentOAuthSession
from api_service.auth import get_jwt_strategy, get_user_manager, UserManager
import docker

logger = logging.getLogger(__name__)

router = APIRouter()

_ATTACHABLE_STATUSES = {
    OAuthSessionStatus.BRIDGE_READY,
    OAuthSessionStatus.AWAITING_USER,
    OAuthSessionStatus.VERIFYING,
}

async def get_current_user_ws(
    token: str = Query(...),
    user_manager: UserManager = Depends(get_user_manager)
) -> User:
    strategy = get_jwt_strategy()
    user = await strategy.read_token(token, user_manager)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user


def _session_is_expired(session: ManagedAgentOAuthSession) -> bool:
    if not session.expires_at:
        return False
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _terminal_close_reason(session: ManagedAgentOAuthSession) -> str | None:
    if session.status not in _ATTACHABLE_STATUSES:
        return f"Session is not attachable in {session.status.value} state"
    if _session_is_expired(session):
        return "Session has expired"
    if not session.container_name:
        return "Session terminal is not ready"
    return None


def _provider_bootstrap_command(runtime_id: str) -> list[str]:
    from moonmind.workflows.temporal.runtime.providers.registry import get_provider

    provider = get_provider(runtime_id)
    if provider is None:
        raise ValueError(f"Unsupported OAuth runtime: {runtime_id}")
    command = provider.get("bootstrap_command") or []
    if not command:
        raise ValueError(f"OAuth runtime {runtime_id} has no bootstrap command")
    return list(command)


def _command_for_docker_exec(runtime_id: str) -> str:
    command = _provider_bootstrap_command(runtime_id)
    return " ".join(shlex.quote(part) for part in command)


def _json_frame_from_text(text: str) -> dict[str, Any] | None:
    try:
        frame = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(frame, dict):
        return None
    return frame


def _resize_dimensions(frame: dict[str, Any]) -> tuple[int, int] | None:
    try:
        cols = int(frame.get("cols", 80))
        rows = int(frame.get("rows", 24))
    except (TypeError, ValueError):
        return None
    return cols, rows


async def _mark_terminal_connection(
    db: AsyncSession,
    session: ManagedAgentOAuthSession,
    *,
    connected: bool,
) -> None:
    now = datetime.now(timezone.utc)
    if connected:
        session.connected_at = now
    else:
        session.disconnected_at = now
    await db.commit()

@router.websocket("/terminal/{session_id}")
async def terminal_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
    user_manager: UserManager = Depends(get_user_manager),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        user = await get_current_user_ws(token, user_manager)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    await websocket.accept()

    from sqlalchemy import select
    stmt = select(ManagedAgentOAuthSession).where(
        ManagedAgentOAuthSession.session_id == session_id,
        ManagedAgentOAuthSession.requested_by_user_id == str(user.id)
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    
    if not session:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found or forbidden")
        return

    close_reason = _terminal_close_reason(session)
    if close_reason:
        await websocket.send_text(close_reason + "\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=close_reason)
        return

    container_name = session.container_name
    try:
        exec_command = _command_for_docker_exec(session.runtime_id)
    except ValueError as exc:
        await websocket.send_text(str(exc) + "\r\n")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc))
        return
    await _mark_terminal_connection(db, session, connected=True)
    
    try:
        client = docker.from_env()
        try:
            container = client.containers.get(container_name)
        except docker.errors.NotFound:
            await websocket.send_text(f"Terminal session {session_id} is not ready or has expired.\r\n")
            await websocket.close(code=1000)
            return

        # Start a sh process attached to PTY
        exec_instance = client.api.exec_create(
            container=container.id,
            cmd=["/bin/sh", "-lc", exec_command],
            tty=True,
            stdin=True,
            stdout=True,
            stderr=True
        )
        
        sock = client.api.exec_start(exec_instance["Id"], socket=True, tty=True)
        # Wait for socket
        if hasattr(sock, '_sock'):
            raw_sock = sock._sock
        else:
            raw_sock = sock
            
        raw_sock.setblocking(False)

        async def _read_from_docker():
            loop = asyncio.get_event_loop()
            while True:
                data = await loop.sock_recv(raw_sock, 4096)
                if not data:
                    break
                # Only string representation allowed natively backwards
                if isinstance(data, bytes):
                    await websocket.send_bytes(data)
                else:
                    await websocket.send_text(data)

        async def _read_from_ws():
            loop = asyncio.get_event_loop()
            while True:
                message = await websocket.receive()
                if "bytes" in message:
                    await loop.sock_sendall(raw_sock, message["bytes"])
                elif "text" in message:
                    text = message["text"]
                    frame = _json_frame_from_text(text)
                    if frame is None:
                        await loop.sock_sendall(raw_sock, text.encode("utf-8"))
                        continue

                    frame_type = frame.get("type")
                    if frame_type == "heartbeat":
                        await websocket.send_text(json.dumps({"type": "heartbeat_ack"}))
                    elif frame_type == "resize":
                        dimensions = _resize_dimensions(frame)
                        if dimensions is None:
                            logger.warning(
                                "Invalid resize frame received for session %s",
                                session_id,
                            )
                            continue
                        cols, rows = dimensions
                        client.api.exec_resize(exec_instance["Id"], height=rows, width=cols)
                    elif frame_type == "input":
                        await loop.sock_sendall(
                            raw_sock, str(frame.get("data", "")).encode("utf-8")
                        )
                    else:
                        await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                        break

        done, pending = await asyncio.wait(
            [asyncio.create_task(_read_from_docker()), asyncio.create_task(_read_from_ws())],
            return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        try:
            await _mark_terminal_connection(db, session, connected=False)
        except Exception:
            logger.debug("Terminal disconnect metadata update failed", exc_info=True)
        try:
            if 'raw_sock' in locals():
                raw_sock.close()
            await websocket.close(code=1000)
        except Exception as exc:
            logging.getLogger(__name__).debug("WebSocket cleanup error: %s", exc)
