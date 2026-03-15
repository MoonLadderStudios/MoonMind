from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class AgentRunStatus(str, Enum):
    queued = "queued"
    launching = "launching"
    running = "running"
    awaiting_callback = "awaiting_callback"
    awaiting_approval = "awaiting_approval"
    intervention_requested = "intervention_requested"
    collecting_results = "collecting_results"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"

class AgentExecutionRequest(BaseModel):
    agent_kind: str
    agent_id: str
    execution_profile_ref: Optional[str] = None
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    instruction_ref: Optional[str] = None
    input_refs: List[str] = Field(default_factory=list)
    expected_output_schema: Optional[Dict[str, Any]] = None
    workspace_spec: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    timeout_policy: Optional[Dict[str, Any]] = None
    retry_policy: Optional[Dict[str, Any]] = None
    approval_policy: Optional[Dict[str, Any]] = None
    callback_policy: Optional[Dict[str, Any]] = None

class AgentRunHandle(BaseModel):
    run_id: str
    agent_kind: str
    agent_id: str
    status: AgentRunStatus
    started_at: str
    poll_hint_seconds: Optional[int] = None

class AgentRunResult(BaseModel):
    output_refs: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None
    diagnostics_ref: Optional[str] = None
    failure_class: Optional[str] = None
    provider_error_code: Optional[str] = None
    retry_recommendation: Optional[bool] = None
