from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    RunStatus,
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
            "normalized_status": "succeeded",
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
