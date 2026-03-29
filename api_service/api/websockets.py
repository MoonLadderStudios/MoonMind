import asyncio
import logging
import uuid
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from api_service.db.base import get_async_session
from api_service.db.models import User, ManagedAgentOAuthSession
from api_service.auth import get_jwt_strategy, get_user_manager, UserManager
import docker

logger = logging.getLogger(__name__)

router = APIRouter()

async def get_current_user_ws(
    token: str = Query(...),
    user_manager: UserManager = Depends(get_user_manager)
) -> User:
    strategy = get_jwt_strategy()
    user = await strategy.read_token(token, user_manager)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user

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

    # Validate session_id belongs to user
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
        
    container_name = f"moonmind_auth_{session_id}"
    
    # Proxy implementation bridging WebSocket to Docker exec PTY
    try:
        client = docker.from_env()
        # Ensure container exists
        try:
            container = client.containers.get(container_name)
        except docker.errors.NotFound:
            await websocket.send_text(f"Terminal session {session_id} is not ready or has expired.\r\n")
            await websocket.close(code=1000)
            return

        # Start a sh process attached to PTY
        exec_instance = client.api.exec_create(
            container=container.id,
            cmd="/bin/sh",
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
                    await loop.sock_sendall(raw_sock, message["text"].encode("utf-8"))

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
            if 'raw_sock' in locals():
                raw_sock.close()
            await websocket.close(code=1000)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("WebSocket cleanup error: %s", exc)
