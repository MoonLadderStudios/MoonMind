from api_service.services.temporal.adapters.managed import ManagedAgentAdapter
from api_service.services.temporal.workflows.shared import AgentExecutionRequest, AgentRunStatus

def test_managed_adapter_start():
    adapter = ManagedAgentAdapter()
    request = AgentExecutionRequest(
        agent_id="test-managed",
        execution_profile_ref="managed-profile",
        instruction_ref="managed-instruction",
        input_refs=[],
        workspace_spec={"repo": "test-repo"},
        idempotency_key="managed-key"
    )
    handle = adapter.start(request)
    assert handle.run_id == "managed-key"
    assert handle.agent_kind == "managed"
    assert handle.status == AgentRunStatus.launching
    assert handle.poll_hint_seconds == 5

def test_managed_adapter_status():
    adapter = ManagedAgentAdapter()
    status = adapter.status("test-run-id")
    assert status == AgentRunStatus.running

def test_managed_adapter_fetch_result():
    adapter = ManagedAgentAdapter()
    result = adapter.fetch_result("test-run-id")
    assert result.summary == "Managed run complete"
    assert result.output_refs == []

def test_managed_adapter_cancel():
    adapter = ManagedAgentAdapter()
    # Should not raise exception
    adapter.cancel("test-run-id")
