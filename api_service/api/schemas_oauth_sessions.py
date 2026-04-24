from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from api_service.db.models import ManagedAgentRateLimitPolicy, OAuthSessionStatus

class ProviderProfileSummary(BaseModel):
    profile_id: str
    runtime_id: str
    provider_id: str
    provider_label: Optional[str] = None
    credential_source: str
    runtime_materialization_mode: str
    account_label: Optional[str] = None
    enabled: bool
    is_default: bool
    rate_limit_policy: str

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
    terminal_session_id: Optional[str] = None
    terminal_bridge_id: Optional[str] = None
    session_transport: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    profile_summary: Optional[ProviderProfileSummary] = None

class OAuthTerminalAttachResponse(BaseModel):
    session_id: str
    terminal_session_id: str
    terminal_bridge_id: str
    websocket_url: str
    attach_token: str
    expires_at: Optional[datetime] = None
