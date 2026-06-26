"""Unit tests for canonical Codex Cloud external-agent adapter behavior."""

from __future__ import annotations

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.codex_cloud_agent_adapter import CodexCloudAgentAdapter
from moonmind.workflows.adapters.codex_cloud_client import CodexCloudClientError

pytestmark = [pytest.mark.asyncio]

class _FakeCodexCloudClient:
    def __init__(
        self,
        *,
        create_status: str = "pending",
        get_status: str = "completed",
        cancel_raises: bool = False,
    ) -> None:
        self.create_status = create_status
        self.get_status = get_status
        self.cancel_raises = cancel_raises
        self.created: list[object] = []
        self.lookups: list[str] = []
        self.cancelled: list[str] = []
        self.closed_count = 0

    async def create_task(self, *, title, description, metadata=None):
        call_record = {"title": title, "description": description, "metadata": metadata}
        self.created.append(call_record)
        return {
            "taskId": "cc-task-123",
            "status": self.create_status,
            "url": "https://codex-cloud.example.test/tasks/cc-task-123",
        }

    async def get_task(self, task_id):
        self.lookups.append(task_id)
        return {
            "taskId": task_id,
            "status": self.get_status,
            "url": f"https://codex-cloud.example.test/tasks/{task_id}",
        }

    async def cancel_task(self, task_id):
        if self.cancel_raises:
            raise CodexCloudClientError(
                "cancel unavailable", status_code=405, request_path=f"/tasks/{task_id}/cancel"
            )
        self.cancelled.append(task_id)
        return {
            "taskId": task_id,
            "status": "canceled",
            "url": f"https://codex-cloud.example.test/tasks/{task_id}",
        }

    async def aclose(self):
        self.closed_count += 1

def _request(*, idempotency_key: str = "idem-1") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="codex_cloud",
        executionProfileRef="profile:codex-cloud-default",
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
        parameters={
            "title": "MoonMind task",
            "description": "Run integration checks",
            "metadata": {"origin": "unit-test"},
        },
    )

async def test_start_returns_canonical_handle_and_reuses_idempotency_key():
    client = _FakeCodexCloudClient()
    adapter = CodexCloudAgentAdapter(client_factory=lambda: client)

    first = await adapter.start(_request(idempotency_key="idem-shared"))
    second = await adapter.start(_request(idempotency_key="idem-shared"))

    assert first.run_id == "cc-task-123"
    assert first.status == "queued"
    assert first.metadata["providerStatus"] == "pending"
    assert first.metadata["normalizedStatus"] == "queued"
    assert second.run_id == first.run_id
    assert len(client.created) == 1

async def test_status_normalizes_provider_states():
    client = _FakeCodexCloudClient(get_status="completed")
    adapter = CodexCloudAgentAdapter(client_factory=lambda: client)

    status = await adapter.status("task-abc")

    assert status.run_id == "task-abc"
    assert status.status == "completed"
    assert status.metadata["normalizedStatus"] == "completed"

async def test_fetch_result_returns_summary():
    client = _FakeCodexCloudClient(get_status="completed")
    adapter = CodexCloudAgentAdapter(client_factory=lambda: client)

    result = await adapter.fetch_result("task-abc")

    assert result.summary is not None
    assert "completed" in result.summary
    assert result.failure_class is None

async def test_fetch_result_includes_failure_class_on_failure():
    client = _FakeCodexCloudClient(get_status="failed")
    adapter = CodexCloudAgentAdapter(client_factory=lambda: client)

    result = await adapter.fetch_result("task-abc")

    assert result.failure_class == "integration_error"

async def test_cancel_returns_intervention_requested_when_provider_rejects():
    client = _FakeCodexCloudClient(cancel_raises=True)
    adapter = CodexCloudAgentAdapter(client_factory=lambda: client)

    status = await adapter.cancel("task-cancel")

    assert status.run_id == "task-cancel"
    assert status.status == "intervention_requested"
    assert status.metadata["cancelAccepted"] is False

async def test_cancel_returns_success_when_accepted():
    client = _FakeCodexCloudClient(cancel_raises=False)
    adapter = CodexCloudAgentAdapter(client_factory=lambda: client)

    status = await adapter.cancel("task-cancel")

    assert status.run_id == "task-cancel"
    assert status.metadata["cancelAccepted"] is True
    assert status.metadata["normalizedStatus"] == "canceled"
