import pytest
from api_service.services.temporal.adapters.external import ExternalAgentAdapter
from api_service.services.temporal.workflows.shared import AgentExecutionRequest, AgentRunStatus

def test_external_adapter_start():
    adapter = ExternalAgentAdapter()
    request = AgentExecutionRequest(
        agent_kind="external",
        agent_id="test-agent",
        execution_profile_ref="test-profile",
        instruction_ref="test-instruction",
        input_refs=["input-1"],
        idempotency_key="test-key"
    )
    handle = adapter.start(request)
    assert handle.run_id == "test-key"
    assert handle.agent_kind == "external"
    assert handle.status == AgentRunStatus.launching
    assert handle.poll_hint_seconds == 15

@pytest.mark.asyncio
async def test_external_adapter_status():
    adapter = ExternalAgentAdapter()
    status = await adapter.status("test-run-id")
    assert status == AgentRunStatus.running

@pytest.mark.asyncio
async def test_external_adapter_fetch_result():
    adapter = ExternalAgentAdapter()
    result = await adapter.fetch_result("test-run-id")
    assert result.summary == "External run complete"
    assert result.diagnostics_ref == "diag-test-run-id"

@pytest.mark.asyncio
async def test_external_adapter_cancel():
    adapter = ExternalAgentAdapter()
    # Should not raise exception
    await adapter.cancel("test-run-id")
