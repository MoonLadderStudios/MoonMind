from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_service.db.models import AgentJobLiveSessionProvider, AgentJobLiveSessionStatus


class TaskRunLiveSessionBase(BaseModel):
    provider: AgentJobLiveSessionProvider
    status: AgentJobLiveSessionStatus
    worker_id: Optional[str] = None
    worker_hostname: Optional[str] = None
    tmate_session_name: Optional[str] = None
    tmate_socket_path: Optional[str] = None
    attach_ro: Optional[str] = None
    web_ro: Optional[str] = None
    ready_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    rw_granted_until: Optional[datetime] = None
    error_message: Optional[str] = None


class TaskRunLiveSessionResponse(TaskRunLiveSessionBase):
    id: UUID
    task_run_id: UUID
    created_at: datetime
    updated_at: datetime
    last_heartbeat_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TaskRunLiveSessionWorkerResponse(TaskRunLiveSessionResponse):
    attach_rw: Optional[str] = None
    web_rw: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TaskRunLiveSessionReportRequest(BaseModel):
    worker_id: str = Field(alias="workerId")
    status: AgentJobLiveSessionStatus
    provider: Optional[AgentJobLiveSessionProvider] = None
    worker_hostname: Optional[str] = Field(None, alias="workerHostname")
    tmate_session_name: Optional[str] = Field(None, alias="tmateSessionName")
    tmate_socket_path: Optional[str] = Field(None, alias="tmateSocketPath")
    attach_ro: Optional[str] = Field(None, alias="attachRo")
    attach_rw: Optional[str] = Field(None, alias="attachRw")
    web_ro: Optional[str] = Field(None, alias="webRo")
    web_rw: Optional[str] = Field(None, alias="webRw")
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    error_message: Optional[str] = Field(None, alias="errorMessage")


class TaskRunLiveSessionHeartbeatRequest(BaseModel):
    worker_id: str = Field(alias="workerId")
