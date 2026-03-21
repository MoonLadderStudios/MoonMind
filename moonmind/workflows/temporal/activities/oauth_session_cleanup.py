"""OAuth session cleanup activity.

Scans for stale sessions (stuck in non-terminal states beyond TTL)
and marks them as expired.  Designed to be called periodically by the
Temporal scheduling infrastructure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from temporalio import activity

from api_service.db.base import get_async_session_context
from api_service.db.models import (
    ManagedAgentOAuthSession,
    OAuthSessionStatus,
)

logger = logging.getLogger(__name__)

# Sessions older than this are considered stale and eligible for cleanup.
_DEFAULT_STALE_THRESHOLD_MINUTES = 45


@activity.defn(name="oauth_session.cleanup_stale")
async def oauth_session_cleanup_stale(
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Expire stale OAuth sessions.

    Scans for sessions in non-terminal states that were created more
    than ``stale_threshold_minutes`` ago and transitions them to
    ``expired``.
    """
    request = request or {}
    threshold_minutes = request.get(
        "stale_threshold_minutes", _DEFAULT_STALE_THRESHOLD_MINUTES
    )
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)

    non_terminal_statuses = [
        OAuthSessionStatus.PENDING,
        OAuthSessionStatus.STARTING,
        OAuthSessionStatus.TMATE_READY,
        OAuthSessionStatus.AWAITING_USER,
        OAuthSessionStatus.VERIFYING,
        OAuthSessionStatus.REGISTERING_PROFILE,
    ]

    expired_count = 0
    expired_ids: list[str] = []

    async with get_async_session_context() as db:
        from sqlalchemy.future import select

        result = await db.execute(
            select(ManagedAgentOAuthSession).where(
                ManagedAgentOAuthSession.status.in_(non_terminal_statuses),
                ManagedAgentOAuthSession.created_at < cutoff,
            )
        )
        stale_sessions = result.scalars().all()

        for session in stale_sessions:
            session.status = OAuthSessionStatus.EXPIRED
            session.completed_at = datetime.now(timezone.utc)
            session.failure_reason = (
                f"Session expired: inactive for {threshold_minutes} minutes"
            )
            expired_ids.append(session.session_id)
            expired_count += 1

        if expired_count > 0:
            await db.commit()

    logger.info(
        "Cleaned up %d stale OAuth sessions (threshold: %d min)",
        expired_count,
        threshold_minutes,
    )

    return {
        "expired_count": expired_count,
        "expired_session_ids": expired_ids,
        "threshold_minutes": threshold_minutes,
    }
