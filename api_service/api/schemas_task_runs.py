from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_service.db.models import AgentJobLiveSessionProvider, AgentJobLiveSessionStatus


class TaskRunLiveSessionBase(BaseModel):
    provider: AgentJobLiveSessionProvider
    status: AgentJobLiveSessionStatus
    worker_id: Optional[str] = Field(None, alias="workerId")
    worker_hostname: Optional[str] = Field(None, alias="workerHostname")
    live_session_name: Optional[str] = Field(None, alias="liveSessionName")
    live_session_socket_path: Optional[str] = Field(
        None,
        alias="liveSessionSocketPath",
    )
    attach_ro: Optional[str] = Field(None, alias="attachRo")
    web_ro: Optional[str] = Field(None, alias="webRo")
    ready_at: Optional[datetime] = Field(None, alias="readyAt")
    ended_at: Optional[datetime] = Field(None, alias="endedAt")
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    rw_granted_until: Optional[datetime] = Field(None, alias="rwGrantedUntil")
    error_message: Optional[str] = Field(None, alias="errorMessage")


class TaskRunLiveSessionResponse(TaskRunLiveSessionBase):
    id: UUID
    task_run_id: UUID = Field(alias="taskRunId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    last_heartbeat_at: Optional[datetime] = Field(None, alias="lastHeartbeatAt")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TaskRunLiveSessionWorkerResponse(TaskRunLiveSessionResponse):
    attach_rw: Optional[str] = Field(None, alias="attachRw")
    web_rw: Optional[str] = Field(None, alias="webRw")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TaskRunLiveSessionReportRequest(BaseModel):
    worker_id: str = Field(alias="workerId")
    status: AgentJobLiveSessionStatus
    provider: Optional[AgentJobLiveSessionProvider] = None
    worker_hostname: Optional[str] = Field(None, alias="workerHostname")
    live_session_name: Optional[str] = Field(None, alias="liveSessionName")
    live_session_socket_path: Optional[str] = Field(
        None,
        alias="liveSessionSocketPath",
    )
    attach_ro: Optional[str] = Field(None, alias="attachRo")
    attach_rw: Optional[str] = Field(None, alias="attachRw")
    web_ro: Optional[str] = Field(None, alias="webRo")
    web_rw: Optional[str] = Field(None, alias="webRw")
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    error_message: Optional[str] = Field(None, alias="errorMessage")


class TaskRunLiveSessionHeartbeatRequest(BaseModel):
    worker_id: str = Field(alias="workerId")
