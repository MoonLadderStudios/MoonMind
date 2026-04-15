from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from api_service.db.models import ManagedAgentRateLimitPolicy, OAuthSessionStatus


class CreateOAuthSessionRequest(BaseModel):
    runtime_id: str
    profile_id: str
    volume_ref: Optional[str] = None
    volume_mount_path: Optional[str] = None
    provider_id: Optional[str] = None
    provider_label: Optional[str] = None
    account_label: str
    max_parallel_runs: int = 1
    cooldown_after_429_seconds: int = 900
    rate_limit_policy: ManagedAgentRateLimitPolicy = ManagedAgentRateLimitPolicy.BACKOFF


class OAuthSessionResponse(BaseModel):
    session_id: str
    runtime_id: str
    profile_id: str
    status: OAuthSessionStatus
    expires_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    created_at: Optional[datetime] = None
