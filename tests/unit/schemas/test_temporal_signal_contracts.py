import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from moonmind.schemas.temporal_signal_contracts import (
    CancelSessionSignal,
    ChildStateChangedSignal,
    CompletionSignal,
    ExternalEventSignal,
    FinalizeSessionSignal,
    ProfileAssignedSignal,
    ReleaseSlotSignal,
    ReportCooldownSignal,
    RequestSlotSignal,
    RescheduleSignal,
    SlotAssignedSignal,
    SyncProfilesSignal,
)

def test_external_event_signal_valid():
    data = {
        "source": "github",
        "eventType": "pull_request",
        "externalOperationId": "op-123",
        "providerEventId": "evt-456",
        "normalizedStatus": "completed",
        "providerStatus": "merged",
        "observedAt": "2023-10-27T10:00:00Z",
        "externalUrl": "https://github.com/pr",
        "providerSummary": {"foo": "bar"}
    }
    model = ExternalEventSignal.model_validate(data)
    assert model.source == "github"
    assert model.event_type == "pull_request"
    assert model.external_operation_id == "op-123"
    assert model.provider_event_id == "evt-456"
    assert model.normalized_status == "completed"
    assert model.provider_status == "merged"
    assert model.observed_at == datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc)
    assert model.external_url == "https://github.com/pr"
    assert model.provider_summary == {"foo": "bar"}

    dump = model.model_dump(by_alias=True)
    assert dump["source"] == "github"
    assert dump["eventType"] == "pull_request"
    assert dump["externalOperationId"] == "op-123"
    assert dump["providerEventId"] == "evt-456"
    assert dump["normalizedStatus"] == "completed"
    assert dump["providerStatus"] == "merged"
    assert dump["observedAt"] == datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc)
    assert dump["externalUrl"] == "https://github.com/pr"
    assert dump["providerSummary"] == {"foo": "bar"}

def test_external_event_signal_minimal():
    data = {
        "source": "github",
        "eventType": "pull_request"
    }
    model = ExternalEventSignal.model_validate(data)
    assert model.source == "github"
    assert model.event_type == "pull_request"
    assert model.external_operation_id is None
    assert model.provider_event_id is None
    assert model.provider_summary == {}

def test_reschedule_signal_valid():
    data = {
        "scheduledFor": "2023-10-27T10:00:00Z"
    }
    model = RescheduleSignal.model_validate(data)
    assert model.scheduled_for == datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc)

def test_request_slot_signal_valid():
    data = {
        "requesterWorkflowId": "wf-123"
    }
    model = RequestSlotSignal.model_validate(data)
    assert model.requester_workflow_id == "wf-123"

def test_release_slot_signal_valid():
    data = {
        "requesterWorkflowId": "wf-123"
    }
    model = ReleaseSlotSignal.model_validate(data)
    assert model.requester_workflow_id == "wf-123"

def test_report_cooldown_signal_valid():
    data = {
        "requesterWorkflowId": "wf-123",
        "profileId": "prof-1",
        "cooldownSeconds": 60
    }
    model = ReportCooldownSignal.model_validate(data)
    assert model.requester_workflow_id == "wf-123"
    assert model.profile_id == "prof-1"
    assert model.cooldown_seconds == 60

def test_report_cooldown_signal_invalid_negative_cooldown():
    data = {
        "requesterWorkflowId": "wf-123",
        "profileId": "prof-1",
        "cooldownSeconds": -10
    }
    with pytest.raises(ValidationError):
        ReportCooldownSignal.model_validate(data)

def test_sync_profiles_signal_valid():
    model = SyncProfilesSignal.model_validate({})
    assert isinstance(model, SyncProfilesSignal)

def test_slot_assigned_signal_valid():
    data = {
        "profileId": "prof-1",
        "assignmentId": "ass-123"
    }
    model = SlotAssignedSignal.model_validate(data)
    assert model.profile_id == "prof-1"
    assert model.assignment_id == "ass-123"

def test_child_state_changed_signal_valid():
    data = {
        "childWorkflowId": "cwf-123",
        "status": "running"
    }
    model = ChildStateChangedSignal.model_validate(data)
    assert model.child_workflow_id == "cwf-123"
    assert model.status == "running"
    assert model.result_artifact_ref is None

    data_with_ref = {
        "childWorkflowId": "cwf-123",
        "status": "completed",
        "resultArtifactRef": "ref-789"
    }
    model2 = ChildStateChangedSignal.model_validate(data_with_ref)
    assert model2.result_artifact_ref == "ref-789"

def test_profile_assigned_signal_valid():
    data = {
        "profileId": "prof-1"
    }
    model = ProfileAssignedSignal.model_validate(data)
    assert model.profile_id == "prof-1"

def test_completion_signal_valid():
    data = {
        "status": "failed",
    }
    model = CompletionSignal.model_validate(data)
    assert model.status == "failed"
    assert model.result_artifact_ref is None

def test_finalize_session_signal_valid():
    model = FinalizeSessionSignal.model_validate({})
    assert isinstance(model, FinalizeSessionSignal)

def test_cancel_session_signal_valid():
    model = CancelSessionSignal.model_validate({})
    assert isinstance(model, CancelSessionSignal)
