import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api_service.db.base import get_async_session
from api_service.auth_providers import get_current_user
from api_service.api.schemas_oauth_sessions import CreateOAuthSessionRequest, OAuthSessionResponse
from api_service.db.models import ManagedAgentOAuthSession, OAuthSessionStatus, User

router = APIRouter(prefix="/oauth-sessions", tags=["oauth-sessions"])

@router.post("", response_model=OAuthSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_oauth_session(
    request: CreateOAuthSessionRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
):
    # Check for existing active session for this profile
    result = await db.execute(
        select(ManagedAgentOAuthSession).where(
            ManagedAgentOAuthSession.profile_id == request.profile_id,
            ManagedAgentOAuthSession.status.in_([
                OAuthSessionStatus.PENDING,
                OAuthSessionStatus.STARTING,
                OAuthSessionStatus.TMATE_READY,
                OAuthSessionStatus.AWAITING_USER,
                OAuthSessionStatus.VERIFYING,
                OAuthSessionStatus.REGISTERING_PROFILE,
            ])
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
    await start_oauth_session_workflow(new_session)

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
