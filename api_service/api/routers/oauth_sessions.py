import asyncio
import json
import logging
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api_service.api.schemas_oauth_sessions import (
    CreateOAuthSessionRequest,
    OAuthTerminalAttachResponse,
    OAuthSessionResponse,
    ProviderProfileSummary,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session, get_async_session_context
from api_service.services.provider_profile_service import sync_provider_profile_manager
from api_service.db.models import (
    ManagedAgentOAuthSession,
    OAuthSessionStatus,
    User,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
    ManagedAgentRateLimitPolicy,
)
from moonmind.schemas.agent_runtime_models import validate_codex_oauth_profile_refs
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.temporal.runtime.providers.registry import get_provider_default
from moonmind.workflows.temporal.runtime.terminal_bridge import (
    TerminalBridgeConnection,
    TerminalBridgeFrameError,
    create_docker_exec_pty_adapter,
)

router = APIRouter(prefix="/oauth-sessions", tags=["oauth-sessions"])
logger = logging.getLogger(__name__)
_ACTIVE_SESSION_STATUSES = (
    OAuthSessionStatus.PENDING,
    OAuthSessionStatus.STARTING,
    OAuthSessionStatus.BRIDGE_READY,
    OAuthSessionStatus.AWAITING_USER,
    OAuthSessionStatus.VERIFYING,
    OAuthSessionStatus.REGISTERING_PROFILE,
)
_STALE_ACTIVE_SESSION_MINUTES = 45
_TERMINAL_ATTACH_STATUSES = (
    OAuthSessionStatus.BRIDGE_READY,
    OAuthSessionStatus.AWAITING_USER,
    OAuthSessionStatus.VERIFYING,
)
_oauth_terminal_pty_adapter_factory = create_docker_exec_pty_adapter


async def _handle_oauth_terminal_ws_message(
    message,
    *,
    bridge: TerminalBridgeConnection,
    pty_adapter,
    websocket: WebSocket,
) -> tuple[bool, str | None]:
    if message.get("type") == "websocket.disconnect":
        return True, "client_disconnected"
    if message.get("bytes") is not None:
        data = message["bytes"] or b""
        await pty_adapter.write_bytes(data)
        bridge.input_event_count += 1
        await websocket.send_json({"type": "input_ack", "bytes": len(data)})
        return False, None
    text = message.get("text")
    if text is None:
        await websocket.send_json(
            {"type": "error", "detail": "Frame must be text, bytes, or JSON"}
        )
        await websocket.close(code=4400)
        return True, "invalid_frame_format"
    try:
        frame = json.loads(text)
    except json.JSONDecodeError:
        data = text.encode("utf-8")
        await pty_adapter.write_bytes(data)
        bridge.input_event_count += 1
        await websocket.send_json({"type": "input_ack", "bytes": len(data)})
        return False, None
    if not isinstance(frame, dict):
        await websocket.send_json(
            {"type": "error", "detail": "Frame must be a JSON object"}
        )
        await websocket.close(code=4400)
        return True, "invalid_frame_format"
    try:
        response = await bridge.handle_frame_for_pty(frame, pty_adapter)
    except TerminalBridgeFrameError as exc:
        await websocket.send_json({"type": "error", "detail": str(exc)})
        await websocket.close(code=4400)
        return True, str(exc)
    await websocket.send_json(response)
    if response.get("type") == "close_ack":
        await websocket.close(code=1000)
        return True, "client_closed"
    return False, None


async def _persist_oauth_terminal_close_metadata(
    session_id: str,
    *,
    close_reason: str,
    bridge: TerminalBridgeConnection | None,
) -> None:
    async with get_async_session_context() as db:
        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            )
        )
        session_obj = result.scalars().first()
        if session_obj:
            metadata = dict(session_obj.metadata_json or {})
            metadata["terminal_close_reason"] = close_reason
            metadata["terminal_disconnected_at"] = datetime.now(timezone.utc).isoformat()
            if bridge is not None:
                metadata.update(bridge.safe_metadata())
            session_obj.metadata_json = metadata
            session_obj.disconnected_at = datetime.now(timezone.utc)
            await db.commit()


def _hash_attach_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _oauth_session_is_expired(session: ManagedAgentOAuthSession) -> bool:
    return session.expires_at is not None and _as_aware_utc(session.expires_at) <= _utcnow()


def _oauth_default(runtime_id: str, key: str) -> str | None:
    return get_provider_default(runtime_id, key)


def _provider_profile_summary(
    profile: ManagedAgentProviderProfile | None,
) -> ProviderProfileSummary | None:
    if profile is None:
        return None
    return ProviderProfileSummary(
        profile_id=profile.profile_id,
        runtime_id=profile.runtime_id,
        provider_id=profile.provider_id,
        provider_label=profile.provider_label,
        credential_source=profile.credential_source.value,
        runtime_materialization_mode=profile.runtime_materialization_mode.value,
        account_label=profile.account_label,
        enabled=profile.enabled,
        is_default=profile.is_default,
        rate_limit_policy=profile.rate_limit_policy.value,
    )


def _oauth_session_response(
    session: ManagedAgentOAuthSession,
    *,
    profile: ManagedAgentProviderProfile | None = None,
) -> OAuthSessionResponse:
    return OAuthSessionResponse(
        session_id=session.session_id,
        runtime_id=session.runtime_id,
        profile_id=session.profile_id,
        status=session.status,
        expires_at=session.expires_at,
        terminal_session_id=session.terminal_session_id,
        terminal_bridge_id=session.terminal_bridge_id,
        session_transport=session.session_transport,
        failure_reason=redact_sensitive_text(session.failure_reason),
        created_at=session.created_at,
        profile_summary=_provider_profile_summary(profile),
    )


async def _get_profile_for_session(
    db: AsyncSession,
    session: ManagedAgentOAuthSession,
    *,
    current_user: User,
) -> ManagedAgentProviderProfile | None:
    if not session.profile_id:
        return None
    user_id = getattr(current_user, "id", None)
    visibility_clause = ManagedAgentProviderProfile.owner_user_id.is_(None)
    if user_id is not None:
        visibility_clause = or_(
            visibility_clause,
            ManagedAgentProviderProfile.owner_user_id == user_id,
        )
    result = await db.execute(
        select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.profile_id == session.profile_id,
            visibility_clause,
        )
    )
    return result.scalars().first()


async def _expire_stale_active_sessions(
    db: AsyncSession, *, profile_id: str
) -> None:
    """Expire stale non-terminal sessions so ghost rows don't block new starts."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=_STALE_ACTIVE_SESSION_MINUTES
    )
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.profile_id == profile_id,
            ManagedAgentOAuthSession.status.in_(_ACTIVE_SESSION_STATUSES),
            ManagedAgentOAuthSession.created_at < cutoff,
        )
    )
    stale_sessions = result.scalars().all()
    if not stale_sessions:
        return

    completed_at = datetime.now(timezone.utc)
    for stale_session in stale_sessions:
        stale_session.status = OAuthSessionStatus.EXPIRED
        stale_session.completed_at = completed_at
        if not stale_session.failure_reason:
            stale_session.failure_reason = (
                "Session expired before new start: stale active session"
            )
    await db.commit()

@router.post("", response_model=OAuthSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_oauth_session(
    request: CreateOAuthSessionRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
):
    volume_ref = request.volume_ref or _oauth_default(request.runtime_id, "volume_ref")
    volume_mount_path = request.volume_mount_path or _oauth_default(
        request.runtime_id, "volume_mount_path"
    )
    if not volume_ref:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="volume_ref is required for OAuth sessions.",
        )
    if not volume_mount_path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="volume_mount_path is required for OAuth sessions.",
        )

    await _expire_stale_active_sessions(db, profile_id=request.profile_id)

    profile_result = await db.execute(
        select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.profile_id == request.profile_id
        )
    )
    existing_profile = profile_result.scalars().first()
    if (
        existing_profile
        and existing_profile.owner_user_id is not None
        and str(existing_profile.owner_user_id) != str(current_user.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to use this profile ID.",
        )

    # Check for existing active session for this profile
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.profile_id == request.profile_id,
            ManagedAgentOAuthSession.status.in_(_ACTIVE_SESSION_STATUSES)
        )
    )
    existing_session = result.scalars().first()
    if existing_session:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active OAuth session already exists for this profile."
        )

    session_id = f"oas_{uuid.uuid4().hex[:12]}"
    
    new_session = ManagedAgentOAuthSession(
        session_id=session_id,
        runtime_id=request.runtime_id,
        profile_id=request.profile_id,
        volume_ref=volume_ref,
        volume_mount_path=volume_mount_path,
        account_label=request.account_label,
        status=OAuthSessionStatus.PENDING,
        requested_by_user_id=str(current_user.id),
        metadata_json={
            "provider_id": request.provider_id
            or _oauth_default(request.runtime_id, "provider_id")
            or "unknown",
            "provider_label": request.provider_label
            or _oauth_default(request.runtime_id, "provider_label"),
            "max_parallel_runs": request.max_parallel_runs,
            "cooldown_after_429_seconds": request.cooldown_after_429_seconds,
            "rate_limit_policy": request.rate_limit_policy.value,
        }
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    
    from api_service.services.oauth_session_service import start_oauth_session_workflow

    try:
        await start_oauth_session_workflow(new_session)
    except Exception as exc:
        new_session.status = OAuthSessionStatus.FAILED
        new_session.completed_at = datetime.now(timezone.utc)
        new_session.failure_reason = "Failed to start OAuth session workflow"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to start OAuth session workflow. Please retry.",
        ) from exc

    return _oauth_session_response(new_session, profile=existing_profile)

@router.get("/{session_id}", response_model=OAuthSessionResponse)
async def get_oauth_session(
    session_id: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
):
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.session_id == session_id,
            ManagedAgentOAuthSession.requested_by_user_id == str(current_user.id)
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    profile = await _get_profile_for_session(db, session, current_user=current_user)
    return _oauth_session_response(session, profile=profile)

@router.post("/{session_id}/cancel")
async def cancel_oauth_session(
    session_id: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
):
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.session_id == session_id,
            ManagedAgentOAuthSession.requested_by_user_id == str(current_user.id)
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        
    if session.status not in [
        OAuthSessionStatus.PENDING,
        OAuthSessionStatus.STARTING,
        OAuthSessionStatus.BRIDGE_READY,
        OAuthSessionStatus.AWAITING_USER,
        OAuthSessionStatus.VERIFYING
    ]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session cannot be cancelled in its current state")
        
    session.status = OAuthSessionStatus.CANCELLED
    session.cancelled_at = datetime.now(timezone.utc)
    await db.commit()
    
    from api_service.services.oauth_session_service import cancel_oauth_session_workflow
    await cancel_oauth_session_workflow(session_id)
    
    return {"status": "cancelled"}


@router.post(
    "/{session_id}/terminal/attach",
    response_model=OAuthTerminalAttachResponse,
)
async def attach_oauth_terminal(
    session_id: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
):
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.session_id == session_id,
            ManagedAgentOAuthSession.requested_by_user_id == str(current_user.id),
        )
    )
    session_obj = result.scalars().first()
    if not session_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    if session_obj.status not in _TERMINAL_ATTACH_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth terminal is not attachable in its current state.",
        )
    if _oauth_session_is_expired(session_obj):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="OAuth terminal session has expired.",
        )
    if not session_obj.terminal_session_id or not session_obj.terminal_bridge_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OAuth terminal bridge is not ready.",
        )

    token = secrets.token_urlsafe(32)
    metadata = dict(session_obj.metadata_json or {})
    metadata["terminal_attach_token_sha256"] = _hash_attach_token(token)
    metadata["terminal_attach_token_used"] = False
    metadata["terminal_attach_issued_at"] = datetime.now(timezone.utc).isoformat()
    session_obj.metadata_json = metadata
    await db.commit()

    return OAuthTerminalAttachResponse(
        session_id=session_obj.session_id,
        terminal_session_id=session_obj.terminal_session_id,
        terminal_bridge_id=session_obj.terminal_bridge_id,
        websocket_url=(
            f"/api/v1/oauth-sessions/{session_obj.session_id}/terminal/ws"
            f"?token={token}"
        ),
        attach_token=token,
        expires_at=session_obj.expires_at,
    )


@router.websocket("/{session_id}/terminal/ws")
async def oauth_terminal_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str,
):
    bridge: TerminalBridgeConnection | None = None
    pty_adapter = None
    output_task: asyncio.Task | None = None
    close_reason = "client_disconnected"
    async with get_async_session_context() as db:
        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.session_id == session_id
            ).with_for_update()
        )
        session_obj = result.scalars().first()
        metadata = dict(session_obj.metadata_json or {}) if session_obj else {}
        expected_digest = metadata.get("terminal_attach_token_sha256")
        token_used = metadata.get("terminal_attach_token_used") is True
        if (
            not session_obj
            or session_obj.status not in _TERMINAL_ATTACH_STATUSES
            or _oauth_session_is_expired(session_obj)
            or not session_obj.container_name
            or not expected_digest
            or token_used
            or not secrets.compare_digest(expected_digest, _hash_attach_token(token))
        ):
            await websocket.close(code=4403)
            return

        metadata["terminal_attach_token_used"] = True
        metadata["terminal_connected_at"] = datetime.now(timezone.utc).isoformat()
        session_obj.metadata_json = metadata
        session_obj.connected_at = datetime.now(timezone.utc)
        await db.commit()
        bridge = TerminalBridgeConnection(
            session_id=session_obj.session_id,
            terminal_bridge_id=session_obj.terminal_bridge_id or "",
            owner_user_id=session_obj.requested_by_user_id,
        )
        pty_adapter = _oauth_terminal_pty_adapter_factory(
            container_name=session_obj.container_name,
            runtime_id=session_obj.runtime_id,
        )

    await websocket.accept()
    try:
        await pty_adapter.connect()
    except Exception:
        close_reason = "pty_connect_failed"
        logger.warning(
            "Failed to connect OAuth terminal PTY for session %s",
            session_id,
            exc_info=True,
        )
        await websocket.send_json(
            {"type": "error", "detail": "OAuth terminal bridge is not ready."}
        )
        await websocket.close(code=1011)
        await _persist_oauth_terminal_close_metadata(
            session_id,
            close_reason=close_reason,
            bridge=bridge,
        )
        return

    await websocket.send_json(
        {
            "type": "ready",
            "session_id": session_id,
            "transport": "moonmind_pty_ws",
        }
    )

    async def _send_terminal_output(chunk: bytes) -> None:
        await websocket.send_text(chunk.decode("utf-8", errors="replace"))

    output_task = asyncio.create_task(
        bridge.stream_pty_output(pty_adapter, _send_terminal_output)
    )
    try:
        while True:
            message = await websocket.receive()
            should_close, message_close_reason = await _handle_oauth_terminal_ws_message(
                message,
                bridge=bridge,
                pty_adapter=pty_adapter,
                websocket=websocket,
            )
            if message_close_reason is not None:
                close_reason = message_close_reason
            if should_close:
                break
    except WebSocketDisconnect:
        close_reason = "client_disconnected"
    finally:
        if output_task is not None:
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("OAuth terminal output task failed", exc_info=True)
        if pty_adapter is not None:
            try:
                await pty_adapter.close()
            except Exception:
                logger.debug("OAuth terminal PTY close failed", exc_info=True)
        await _persist_oauth_terminal_close_metadata(
            session_id,
            close_reason=close_reason,
            bridge=bridge,
        )

@router.post("/{session_id}/finalize")
async def finalize_oauth_session(
    session_id: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
):
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.session_id == session_id,
            ManagedAgentOAuthSession.requested_by_user_id == str(current_user.id)
        )
    )
    session_obj = result.scalars().first()
    if not session_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        
    if session_obj.status not in [OAuthSessionStatus.AWAITING_USER, OAuthSessionStatus.VERIFYING]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot finalize session in {session_obj.status.name} state")

    try:
        from moonmind.workflows.temporal.runtime.providers.volume_verifiers import (
            verify_volume_credentials,
        )

        verification = await verify_volume_credentials(
            runtime_id=session_obj.runtime_id,
            volume_ref=session_obj.volume_ref or "",
            volume_mount_path=session_obj.volume_mount_path,
        )
        if not verification.get("verified", False):
            session_obj.status = OAuthSessionStatus.FAILED
            session_obj.completed_at = datetime.now(timezone.utc)
            session_obj.failure_reason = (
                "Volume verification failed: "
                f"{verification.get('reason', 'unknown')}"
            )
            await db.commit()
            await _stop_oauth_auth_runner(session_obj)
            await _fail_oauth_session_workflow(
                session_obj.session_id, session_obj.failure_reason
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=session_obj.failure_reason,
            )
    except HTTPException:
        raise
    except Exception:
        logger.warning(
            "Volume verification unavailable for session %s",
            session_id,
            exc_info=True,
        )
        session_obj.status = OAuthSessionStatus.FAILED
        session_obj.completed_at = datetime.now(timezone.utc)
        session_obj.failure_reason = "Volume verification unavailable"
        await db.commit()
        await _stop_oauth_auth_runner(session_obj)
        await _fail_oauth_session_workflow(
            session_obj.session_id, session_obj.failure_reason
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=session_obj.failure_reason,
        )

    session_obj.status = OAuthSessionStatus.SUCCEEDED
    session_obj.completed_at = datetime.now(timezone.utc)
    
    profile_result = await db.execute(
        select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.profile_id == session_obj.profile_id
        )
    )
    existing_profile = profile_result.scalars().first()

    metadata = session_obj.metadata_json or {}
    policy_str = metadata.get("rate_limit_policy", ManagedAgentRateLimitPolicy.BACKOFF.value)
    
    try:
        policy_enum = ManagedAgentRateLimitPolicy(policy_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported rate_limit_policy: {policy_str}"
        )

    if (
        existing_profile
        and existing_profile.owner_user_id is not None
        and str(existing_profile.owner_user_id) != str(current_user.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this profile",
        )

    profile_data = {
        "runtime_id": session_obj.runtime_id,
        "provider_id": metadata.get("provider_id")
        or _oauth_default(session_obj.runtime_id, "provider_id")
        or "unknown",
        "provider_label": metadata.get("provider_label")
        or _oauth_default(session_obj.runtime_id, "provider_label"),
        "credential_source": ProviderCredentialSource.OAUTH_VOLUME,
        "runtime_materialization_mode": RuntimeMaterializationMode.OAUTH_HOME,
        "volume_ref": session_obj.volume_ref,
        "volume_mount_path": session_obj.volume_mount_path,
        "account_label": session_obj.account_label,
        "max_parallel_runs": metadata.get("max_parallel_runs", 1),
        "cooldown_after_429_seconds": metadata.get("cooldown_after_429_seconds", 900),
        "rate_limit_policy": policy_enum,
        "enabled": True,
    }
    try:
        validate_codex_oauth_profile_refs(
            runtime_id=session_obj.runtime_id,
            credential_source=ProviderCredentialSource.OAUTH_VOLUME.value,
            runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME.value,
            volume_ref=session_obj.volume_ref,
            volume_mount_path=session_obj.volume_mount_path,
            volume_ref_field_name="volume_ref",
            volume_mount_path_field_name="volume_mount_path",
        )
    except ValueError as exc:
        session_obj.status = OAuthSessionStatus.FAILED
        session_obj.completed_at = datetime.now(timezone.utc)
        session_obj.failure_reason = str(exc)
        await db.commit()
        await _stop_oauth_auth_runner(session_obj)
        await _fail_oauth_session_workflow(session_obj.session_id, str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if existing_profile:
        for key, value in profile_data.items():
            setattr(existing_profile, key, value)
    else:
        new_profile = ManagedAgentProviderProfile(
            profile_id=session_obj.profile_id,
            owner_user_id=current_user.id,
            **profile_data
        )
        db.add(new_profile)

    await db.commit()
    
    await sync_provider_profile_manager(session=db, runtime_id=session_obj.runtime_id)
    await _stop_oauth_auth_runner(session_obj)
    await _complete_oauth_session_workflow(session_obj.session_id)
    
    return {"status": "succeeded"}


async def _stop_oauth_auth_runner(session_obj: ManagedAgentOAuthSession) -> None:
    if not session_obj.container_name:
        return
    try:
        from api_service.services.oauth_auth_runner import (
            stop_auth_runner_container,
        )

        await stop_auth_runner_container(
            session_id=session_obj.session_id,
            container_name=session_obj.container_name,
        )
    except Exception:
        logger.warning(
            "Failed to stop OAuth auth runner for session %s",
            session_obj.session_id,
            exc_info=True,
        )


async def _complete_oauth_session_workflow(session_id: str) -> None:
    from api_service.services.oauth_session_service import complete_oauth_session_workflow

    await complete_oauth_session_workflow(session_id)


async def _fail_oauth_session_workflow(session_id: str, reason: str) -> None:
    from api_service.services.oauth_session_service import fail_oauth_session_workflow

    await fail_oauth_session_workflow(session_id, reason)


@router.get("/history/{profile_id}")
async def get_session_history(
    profile_id: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
    limit: int = 20,
):
    """Return the session history for a given profile."""
    from sqlalchemy import desc

    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.profile_id == profile_id,
            ManagedAgentOAuthSession.requested_by_user_id == str(current_user.id),
        ).order_by(desc(ManagedAgentOAuthSession.created_at)).limit(min(limit, 100))
    )
    sessions = result.scalars().all()

    return [
        {
            "session_id": s.session_id,
            "profile_id": s.profile_id,
            "runtime_id": s.runtime_id,
            "status": s.status.value if s.status else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "failure_reason": s.failure_reason,
        }
        for s in sessions
    ]


@router.post("/{session_id}/reconnect", response_model=OAuthSessionResponse, status_code=status.HTTP_201_CREATED)
async def reconnect_oauth_session(
    session_id: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
):
    """Create a new session from an expired, failed, or cancelled predecessor.

    Copies the profile and volume settings from the previous session.
    """
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.session_id == session_id,
            ManagedAgentOAuthSession.requested_by_user_id == str(current_user.id),
        )
    )
    old_session = result.scalars().first()
    if not old_session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    reconnectable_statuses = [
        OAuthSessionStatus.EXPIRED,
        OAuthSessionStatus.FAILED,
        OAuthSessionStatus.CANCELLED,
    ]
    if old_session.status not in reconnectable_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reconnect from {old_session.status.name} state. Only expired/failed/cancelled sessions can be reconnected.",
        )

    new_session_id = f"oas_{uuid.uuid4().hex[:12]}"
    new_session = ManagedAgentOAuthSession(
        session_id=new_session_id,
        profile_id=old_session.profile_id,
        runtime_id=old_session.runtime_id,
        volume_ref=old_session.volume_ref,
        volume_mount_path=old_session.volume_mount_path,
        account_label=old_session.account_label,
        requested_by_user_id=str(current_user.id),
        status=OAuthSessionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        metadata_json=old_session.metadata_json,
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)

    try:
        from api_service.services.oauth_session_service import (
            start_oauth_session_workflow,
        )
        await start_oauth_session_workflow(new_session)
    except Exception:
        logger.exception(
            "Failed to start workflow for reconnected session %s",
            new_session_id,
        )

    profile = await _get_profile_for_session(
        db,
        new_session,
        current_user=current_user,
    )
    return _oauth_session_response(new_session, profile=profile)
