from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunStatus
from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    RunStatus,
    _request_reserves_slot_for_immediate_followup,
)

def test_coerce_external_status_payload_accepts_canonical_shape() -> None:
    workflow_instance = MoonMindAgentRun()

    status = workflow_instance._coerce_external_status_payload(
        status_payload={
            "runId": "jules-task-001",
            "agentKind": "external",
            "agentId": "jules",
            "status": "running",
        },
        fallback_agent_id="jules",
    )

    assert status.run_id == "jules-task-001"
    assert status.agent_id == "jules"
    assert status.status == RunStatus.running

def test_request_reserves_slot_for_immediate_followup_reads_moonmind_metadata() -> None:
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="run-1",
        idempotencyKey="run-1:step-1",
        parameters={
            "metadata": {
                "moonmind": {
                    "slotContinuity": {
                        "reserveForImmediateFollowup": True,
                    }
                }
            }
        },
    )

    assert _request_reserves_slot_for_immediate_followup(request)

def test_request_reserves_slot_for_immediate_followup_ignores_legacy_metadata() -> None:
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="run-1",
        idempotencyKey="run-1:step-1",
        parameters={
            "metadata": {
                "moonmind": {
                    "slotContinuity": {
                        "hasImmediateManagedFollowup": True,
                    }
                }
            }
        },
    )

    assert not _request_reserves_slot_for_immediate_followup(request)

def test_managed_runtime_id_normalizes_aliases() -> None:
    assert MoonMindAgentRun._managed_runtime_id("Codex-CLI") == "codex_cli"
    assert MoonMindAgentRun._managed_runtime_id("claude") == "claude_code"
    assert MoonMindAgentRun._managed_runtime_id("CLAUDE_CODE") == "claude_code"

def test_resiliency_policy_is_runtime_specific() -> None:
    codex_request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="run-1",
        idempotencyKey="run-1:step-1",
    )
    jules_request = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        correlationId="run-2",
        idempotencyKey="run-2:step-1",
    )
    claude_request = AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        correlationId="run-3",
        idempotencyKey="run-3:step-1",
    )

    codex_policy = MoonMindAgentRun._resiliency_policy_for_request(codex_request)
    jules_policy = MoonMindAgentRun._resiliency_policy_for_request(jules_request)
    claude_policy = MoonMindAgentRun._resiliency_policy_for_request(claude_request)

    assert codex_policy["runtime"] == "codex_cli"
    assert codex_policy["retryPolicy"] == "session_turn_self_heal_then_cooldown_retry"
    assert jules_policy["runtime"] == "jules"
    assert (
        jules_policy["retryPolicy"]
        == "provider_polling_with_human_feedback_escalation"
    )
    assert codex_policy["noProgressTimeoutSeconds"] != jules_policy[
        "noProgressTimeoutSeconds"
    ]
    assert claude_policy["runtime"] == "claude_code"
    assert claude_policy["noProgressTimeoutSeconds"] == 2400
    assert claude_policy["noProgressGraceSeconds"] == 900

def test_status_progress_signature_tracks_metadata_progress_keys() -> None:
    first = AgentRunStatus(
        runId="run-1",
        agentKind="managed",
        agentId="codex_cli",
        status="running",
        metadata={"lastEventId": "1"},
    )
    second = AgentRunStatus(
        runId="run-1",
        agentKind="managed",
        agentId="codex_cli",
        status="running",
        metadata={"lastEventId": "2"},
    )

    assert MoonMindAgentRun._status_progress_signature(first) != (
        MoonMindAgentRun._status_progress_signature(second)
    )

def test_status_progress_signature_ignores_heartbeat_only_changes() -> None:
    first = AgentRunStatus(
        runId="run-1",
        agentKind="managed",
        agentId="claude_code",
        status="running",
        metadata={
            "runtimeId": "claude_code",
            "lastHeartbeatAt": "2026-06-09T20:00:00Z",
        },
    )
    second = AgentRunStatus(
        runId="run-1",
        agentKind="managed",
        agentId="claude_code",
        status="running",
        metadata={
            "runtimeId": "claude_code",
            "lastHeartbeatAt": "2026-06-09T20:00:30Z",
        },
    )

    assert MoonMindAgentRun._status_progress_signature(first) == (
        MoonMindAgentRun._status_progress_signature(second)
    )

def test_status_progress_signature_tracks_log_output_progress() -> None:
    first = AgentRunStatus(
        runId="run-1",
        agentKind="managed",
        agentId="claude_code",
        status="running",
        metadata={"lastLogOffset": 128},
    )
    second = AgentRunStatus(
        runId="run-1",
        agentKind="managed",
        agentId="claude_code",
        status="running",
        metadata={"lastLogOffset": 256},
    )

    assert MoonMindAgentRun._status_progress_signature(first) != (
        MoonMindAgentRun._status_progress_signature(second)
    )

def test_intervention_result_uses_terminal_operator_review_metadata() -> None:
    workflow_instance = MoonMindAgentRun()
    workflow_instance.run_id = "run-1"
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="codex_cloud",
        correlationId="run-1",
        idempotencyKey="run-1:step-1",
    )

    result = workflow_instance._intervention_result(
        summary="Agent requested human feedback.",
        request=request,
        metadata={"reason": "agent_requested_feedback"},
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "intervention_requested"
    assert result.metadata["status"] == "intervention_requested"
    assert result.metadata["reason"] == "agent_requested_feedback"

def test_coerce_external_status_payload_maps_integration_shape() -> None:
    workflow_instance = MoonMindAgentRun()

    status = workflow_instance._coerce_external_status_payload(
        status_payload={
            "external_id": "jules-task-002",
            "status": "QUEUED",
            "normalized_status": "queued",
            "provider_status": "QUEUED",
            "url": "https://jules.google.com/session/jules-task-002",
            "terminal": False,
        },
        fallback_agent_id="jules",
    )

    assert status.run_id == "jules-task-002"
    assert status.agent_id == "jules"
    assert status.status == RunStatus.queued
    assert status.metadata.get("providerStatus") == "QUEUED"
    assert status.metadata.get("normalizedStatus") == "queued"
    assert status.metadata.get("externalUrl") == "https://jules.google.com/session/jules-task-002"
    assert status.metadata.get("terminal") is False

def test_coerce_external_status_payload_maps_terminal_success() -> None:
    workflow_instance = MoonMindAgentRun()

    status = workflow_instance._coerce_external_status_payload(
        status_payload={
            "external_id": "jules-task-003",
            "status": "COMPLETED",
            "normalized_status": "completed",
            "provider_status": "COMPLETED",
            "terminal": True,
        },
        fallback_agent_id="jules",
    )

    assert status.run_id == "jules-task-003"
    assert status.status == RunStatus.completed
    assert status.metadata.get("terminal") is True

def test_coerce_external_status_payload_handles_canonical_payload_with_provider_status() -> None:
    workflow_instance = MoonMindAgentRun()

    status = workflow_instance._coerce_external_status_payload(
        status_payload={
            "runId": "jules-task-004",
            "agentKind": "external",
            "agentId": "jules",
            "status": "QUEUED",
            "metadata": {
                "providerStatus": "QUEUED",
                "normalizedStatus": "queued",
            },
        },
        fallback_agent_id="jules",
    )

    assert status.run_id == "jules-task-004"
    assert status.agent_id == "jules"
    assert status.status == RunStatus.queued
    assert status.metadata.get("providerStatus") == "QUEUED"
    assert status.metadata.get("normalizedStatus") == "queued"

def test_coerce_external_start_status_maps_unknown_to_awaiting_callback() -> None:
    workflow_instance = MoonMindAgentRun()

    status, normalized = workflow_instance._coerce_external_start_status(
        {
            "external_id": "jules-task-005",
            "normalized_status": "unknown",
            "provider_status": "STATE_UNSPECIFIED",
        }
    )

    assert status == RunStatus.awaiting_callback
    assert normalized == "unknown"

def test_coerce_external_start_status_maps_succeeded_to_completed() -> None:
    workflow_instance = MoonMindAgentRun()

    status, normalized = workflow_instance._coerce_external_start_status(
        {
            "external_id": "jules-task-006",
            "normalized_status": "completed",
            "provider_status": "completed",
        }
    )

    assert status == RunStatus.completed
    assert normalized == "completed"
