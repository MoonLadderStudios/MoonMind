"""Unit tests for canonical Jules external-agent adapter behavior."""

from __future__ import annotations

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.jules_models import JulesTaskResponse
from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
from moonmind.workflows.adapters.jules_client import JulesClientError

pytestmark = [pytest.mark.asyncio]


class _FakeJulesAdapterClient:
    def __init__(
        self,
        *,
        create_status: str = "pending",
        get_status: str = "completed",
        resolve_raises: bool = False,
    ) -> None:
        self.create_status = create_status
        self.get_status = get_status
        self.resolve_raises = resolve_raises
        self.created: list[object] = []
        self.lookups: list[object] = []
        self.resolved: list[object] = []
        self.closed_count = 0

    async def create_task(self, request):
        self.created.append(request)
        return JulesTaskResponse(
            task_id="task-123",
            status=self.create_status,
            url="https://jules.example.test/tasks/task-123",
        )

    async def get_task(self, request):
        self.lookups.append(request)
        return JulesTaskResponse(
            task_id=request.task_id,
            status=self.get_status,
            url=f"https://jules.example.test/tasks/{request.task_id}",
        )

    async def resolve_task(self, request):
        if self.resolve_raises:
            raise JulesClientError("cancel unavailable")
        self.resolved.append(request)
        return JulesTaskResponse(
            task_id=request.task_id,
            status="canceled",
            url=f"https://jules.example.test/tasks/{request.task_id}",
        )

    async def aclose(self):
        self.closed_count += 1


def _request(*, idempotency_key: str = "idem-1") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
        parameters={
            "title": "MoonMind task",
            "description": "Run integration checks",
            "metadata": {"origin": "unit-test"},
        },
    )


async def test_start_returns_canonical_handle_and_reuses_idempotency_key():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    first = await adapter.start(_request(idempotency_key="idem-shared"))
    second = await adapter.start(_request(idempotency_key="idem-shared"))

    assert first.run_id == "task-123"
    assert first.status == "queued"
    assert first.metadata["providerStatus"] == "pending"
    assert first.metadata["normalizedStatus"] == "queued"
    assert second.run_id == first.run_id
    assert len(client.created) == 1


async def test_status_and_fetch_result_normalize_provider_states():
    client = _FakeJulesAdapterClient(get_status="completed")
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    status = await adapter.status("task-abc")
    result = await adapter.fetch_result("task-abc")

    assert status.run_id == "task-abc"
    assert status.status == "completed"
    assert status.metadata["normalizedStatus"] == "succeeded"
    assert status.terminal is True
    assert result.summary is not None
    assert "completed" in result.summary
    assert result.failure_class is None


async def test_cancel_returns_intervention_requested_when_provider_rejects_cancel():
    client = _FakeJulesAdapterClient(resolve_raises=True)
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    status = await adapter.cancel("task-cancel")

    assert status.run_id == "task-cancel"
    assert status.status == "intervention_requested"
    assert status.metadata["cancelAccepted"] is False


async def test_start_passes_repository_in_source_context():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey="idem-repo",
        workspaceSpec={"repository": "owner/repo"},
        parameters={"description": "foo"}
    )

    await adapter.start(req)

    assert len(client.created) == 1
    create_req = client.created[0]
    # Must match Jules API SourceContext format
    assert create_req.source_context is not None
    assert create_req.source_context.source == "sources/github/owner/repo"
    assert create_req.source_context.github_repo_context.starting_branch == "main"


async def test_start_passes_explicit_branch_in_source_context():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey="idem-branch",
        workspaceSpec={"repository": "owner/repo", "branch": "develop"},
        parameters={"description": "foo"}
    )

    await adapter.start(req)

    assert len(client.created) == 1
    create_req = client.created[0]
    assert create_req.source_context.source == "sources/github/owner/repo"
    assert create_req.source_context.github_repo_context.starting_branch == "develop"


@pytest.mark.asyncio
async def test_start_passes_starting_branch_in_source_context():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey="idem-starting-branch",
        workspaceSpec={"repository": "owner/repo", "startingBranch": "feature-branch"},
        parameters={"description": "foo"}
    )

    await adapter.start(req)

    assert len(client.created) == 1
    create_req = client.created[0]
    assert create_req.source_context.source == "sources/github/owner/repo"
    assert create_req.source_context.github_repo_context.starting_branch == "feature-branch"


async def test_start_without_workspace_spec_sends_no_source_context():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey="idem-no-ws",
        parameters={"description": "foo"}
    )

    await adapter.start(req)

    assert len(client.created) == 1
    create_req = client.created[0]
    assert create_req.source_context is None


async def test_start_defaults_automation_mode_for_pr_publish():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey="idem-pr",
        parameters={"description": "foo", "publishMode": "pr"}
    )

    await adapter.start(req)

    assert len(client.created) == 1
    create_req = client.created[0]
    assert create_req.automation_mode == "AUTO_CREATE_PR"


async def test_start_defaults_automation_mode_for_branch_publish():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey="idem-branch-pub",
        parameters={"description": "foo", "publishMode": "branch"},
    )

    await adapter.start(req)

    assert len(client.created) == 1
    create_req = client.created[0]
    assert create_req.automation_mode == "AUTO_CREATE_PR"

