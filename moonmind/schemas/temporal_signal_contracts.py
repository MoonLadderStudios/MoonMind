"""Shared Temporal signal payload contracts."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Public Execution Signals
# ---------------------------------------------------------------------------

class ExternalEventSignal(BaseModel):
    """Payload for ExternalEvent signals.

    Carries asynchronous external integration progress or completion into the workflow.
    """
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(..., alias="source")
    event_type: str = Field(..., alias="eventType")
    external_operation_id: Optional[str] = Field(None, alias="externalOperationId")
    provider_event_id: Optional[str] = Field(None, alias="providerEventId")
    normalized_status: Optional[str] = Field(None, alias="normalizedStatus")
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    observed_at: Optional[datetime] = Field(None, alias="observedAt")
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(default_factory=dict, alias="providerSummary")


class RescheduleSignal(BaseModel):
    """Payload for reschedule signals.

    Requests the workflow to adjust its delayed start or next recurrence.
    """
    model_config = ConfigDict(populate_by_name=True)

    scheduled_for: datetime = Field(..., alias="scheduledFor")


# ---------------------------------------------------------------------------
# Internal Coordination Signals
# ---------------------------------------------------------------------------

class RequestSlotSignal(BaseModel):
    """Payload for request_slot signals.

    Enqueues or immediately satisfies a slot request from one MoonMind.AgentRun.
    """
    model_config = ConfigDict(populate_by_name=True)

    requester_workflow_id: str = Field(..., alias="requesterWorkflowId")


class ReleaseSlotSignal(BaseModel):
    """Payload for release_slot signals.

    Returns a previously assigned profile slot or cancels a pending request.
    """
    model_config = ConfigDict(populate_by_name=True)

    requester_workflow_id: str = Field(..., alias="requesterWorkflowId")


class ReportCooldownSignal(BaseModel):
    """Payload for report_cooldown signals.

    Informs the manager that a profile is rate-limited and needs recovery time.
    """
    model_config = ConfigDict(populate_by_name=True)

    requester_workflow_id: str = Field(..., alias="requesterWorkflowId")
    profile_id: str = Field(..., alias="profileId")
    cooldown_seconds: int = Field(..., alias="cooldownSeconds", ge=0)


class SyncProfilesSignal(BaseModel):
    """Payload for sync_profiles signals.

    Requests the manager to reload its target profile definitions.
    """
    model_config = ConfigDict(populate_by_name=True)
    pass


class SlotAssignedSignal(BaseModel):
    """Payload for slot_assigned signals.

    Notifies a waiting AgentRun that it now owns a specific profile slot.
    """
    model_config = ConfigDict(populate_by_name=True)

    profile_id: str = Field(..., alias="profileId")
    assignment_id: str = Field(..., alias="assignmentId")


class ChildStateChangedSignal(BaseModel):
    """Payload for child_state_changed signals.

    Notifies a parent workflow of a child's status change.
    """
    model_config = ConfigDict(populate_by_name=True)

    child_workflow_id: str = Field(..., alias="childWorkflowId")
    status: str = Field(..., alias="status")
    result_artifact_ref: Optional[str] = Field(None, alias="resultArtifactRef")


class ProfileAssignedSignal(BaseModel):
    """Payload for profile_assigned signals.

    Records profile selection.
    """
    model_config = ConfigDict(populate_by_name=True)

    profile_id: str = Field(..., alias="profileId")


class CompletionSignal(BaseModel):
    """Payload for completion_signal signals.

    Accepts an asynchronous terminal completion result.
    """
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(..., alias="status")
    result_artifact_ref: Optional[str] = Field(None, alias="resultArtifactRef")


# ---------------------------------------------------------------------------
# OAuth Session Signals
# ---------------------------------------------------------------------------

class FinalizeSessionSignal(BaseModel):
    """Payload for finalize session signals.

    Requests transition from waiting-for-user to verification and registration.
    """
    model_config = ConfigDict(populate_by_name=True)
    pass


class CancelSessionSignal(BaseModel):
    """Payload for cancel session signals.

    Stops the session before completion.
    """
    model_config = ConfigDict(populate_by_name=True)
    pass
