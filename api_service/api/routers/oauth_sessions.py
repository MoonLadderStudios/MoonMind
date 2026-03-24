import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api_service.db.base import get_async_session
from api_service.auth_providers import get_current_user
from api_service.api.schemas_oauth_sessions import CreateOAuthSessionRequest, OAuthSessionResponse
from api_service.services.auth_profile_service import sync_auth_profile_manager
from api_service.db.models import ManagedAgentOAuthSession, OAuthSessionStatus, User, ManagedAgentAuthProfile, ManagedAgentAuthMode, ManagedAgentRateLimitPolicy

router = APIRouter(prefix="/oauth-sessions", tags=["oauth-sessions"])

_ACTIVE_SESSION_STATUSES = (
    OAuthSessionStatus.PENDING,
    OAuthSessionStatus.STARTING,
    OAuthSessionStatus.TMATE_READY,
    OAuthSessionStatus.AWAITING_USER,
    OAuthSessionStatus.VERIFYING,
    OAuthSessionStatus.REGISTERING_PROFILE,
)
_STALE_ACTIVE_SESSION_MINUTES = 45


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
    await _expire_stale_active_sessions(db, profile_id=request.profile_id)

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
        volume_ref=request.volume_ref,
        account_label=request.account_label,
        status=OAuthSessionStatus.PENDING,
        requested_by_user_id=str(current_user.id),
        metadata_json={
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

    return OAuthSessionResponse(
        session_id=new_session.session_id,
        runtime_id=new_session.runtime_id,
        profile_id=new_session.profile_id,
        status=new_session.status,
    )

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
        
    return OAuthSessionResponse(
        session_id=session.session_id,
        runtime_id=session.runtime_id,
        profile_id=session.profile_id,
        status=session.status,
        tmate_web_url=session.tmate_web_url,
        tmate_ssh_url=session.tmate_ssh_url,
        expires_at=session.expires_at,
        failure_reason=session.failure_reason,
    )

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
        OAuthSessionStatus.TMATE_READY,
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

    # Attempt volume verification (best-effort — Docker may not be available)
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
            import logging

            logging.getLogger(__name__).warning(
                "Volume verification failed for session %s: %s",
                session_id,
                verification.get("reason", "unknown"),
            )
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Volume verification unavailable for session %s",
            session_id,
            exc_info=True,
        )

    session_obj.status = OAuthSessionStatus.SUCCEEDED
    session_obj.completed_at = datetime.now(timezone.utc)
    
    profile_result = await db.execute(
        select(ManagedAgentAuthProfile).where(
            ManagedAgentAuthProfile.profile_id == session_obj.profile_id
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

    if existing_profile and existing_profile.owner_user_id is not None and str(existing_profile.owner_user_id) != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update this profile")

    profile_data = {
        "runtime_id": session_obj.runtime_id,
        "auth_mode": ManagedAgentAuthMode.OAUTH,
        "volume_ref": session_obj.volume_ref,
        "volume_mount_path": session_obj.volume_mount_path,
        "account_label": session_obj.account_label,
        "max_parallel_runs": metadata.get("max_parallel_runs", 1),
        "cooldown_after_429_seconds": metadata.get("cooldown_after_429_seconds", 300),
        "rate_limit_policy": policy_enum,
        "enabled": True,
    }

    if existing_profile:
        for key, value in profile_data.items():
            setattr(existing_profile, key, value)
    else:
        new_profile = ManagedAgentAuthProfile(
            profile_id=session_obj.profile_id,
            owner_user_id=current_user.id,
            **profile_data
        )
        db.add(new_profile)

    await db.commit()
    
    await sync_auth_profile_manager(session=db, runtime_id=session_obj.runtime_id)
    
    return {"status": "succeeded"}


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
            "tmate_web_url": s.tmate_web_url,
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
        import logging
        logging.getLogger(__name__).exception(
            "Failed to start workflow for reconnected session %s",
            new_session_id,
        )

    return OAuthSessionResponse(
        session_id=new_session.session_id,
        profile_id=new_session.profile_id,
        runtime_id=new_session.runtime_id,
        status=new_session.status.value,
        created_at=new_session.created_at,
        tmate_web_url=new_session.tmate_web_url,
        tmate_ssh_url=new_session.tmate_ssh_url,
        expires_at=new_session.expires_at,
    )
