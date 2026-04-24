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
        get_pull_request_url: str | None = None,
        resolve_raises: bool = False,
    ) -> None:
        self.create_status = create_status
        self.get_status = get_status
        self.get_pull_request_url = get_pull_request_url
        self.resolve_raises = resolve_raises
        self.created: list[object] = []
        self.lookups: list[object] = []
        self.resolved: list[object] = []
        self.sent_messages: list[object] = []
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
        outputs = []
        if self.get_pull_request_url is not None:
            outputs = [{"pullRequest": {"url": self.get_pull_request_url}}]
        return JulesTaskResponse(
            task_id=request.task_id,
            status=self.get_status,
            url=f"https://jules.example.test/tasks/{request.task_id}",
            outputs=outputs,
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

    async def send_message(self, request):
        self.sent_messages.append(request)
        return None

def _request(*, idempotency_key: str = "idem-1") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
        workspaceSpec={"repository": "owner/repo"},
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
    assert status.metadata["normalizedStatus"] == "completed"
    assert status.terminal is True
    assert result.summary is not None
    assert "completed" in result.summary
    assert result.failure_class is None

async def test_status_and_fetch_result_prefer_pull_request_url():
    client = _FakeJulesAdapterClient(
        get_status="in_progress",
        get_pull_request_url="https://github.com/org/repo/pull/123",
    )
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    status = await adapter.status("task-pr")
    result = await adapter.fetch_result("task-pr")

    assert status.status == "completed"
    assert status.metadata["normalizedStatus"] == "completed"
    assert status.metadata["externalUrl"] == "https://github.com/org/repo/pull/123"
    assert status.metadata["pullRequestUrl"] == "https://github.com/org/repo/pull/123"
    assert result.metadata["normalizedStatus"] == "completed"
    assert result.metadata["externalUrl"] == "https://github.com/org/repo/pull/123"
    assert result.metadata["pullRequestUrl"] == "https://github.com/org/repo/pull/123"

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

async def test_start_without_workspace_spec_raises_value_error():
    """Jules requires workspace_spec.repository, so missing it must raise ValueError."""
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

    with pytest.raises(ValueError, match="workspace_spec.repository"):
        await adapter.start(req)

    assert len(client.created) == 0

async def test_start_defaults_automation_mode_for_pr_publish():
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="profile:jules-default",
        correlationId="corr-1",
        idempotencyKey="idem-pr",
        workspaceSpec={"repository": "owner/repo"},
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
        workspaceSpec={"repository": "owner/repo"},
        parameters={"description": "foo", "publishMode": "branch"},
    )

    handle = await adapter.start(req)

    assert len(client.created) == 1
    create_req = client.created[0]
    assert create_req.automation_mode == "AUTO_CREATE_PR"
    assert handle.metadata["automationMode"] == "AUTO_CREATE_PR"
    assert handle.metadata["publishMode"] == "branch"

async def test_send_message_returns_running_status():
    """send_message() should call the client and return a running status."""
    client = _FakeJulesAdapterClient()
    adapter = JulesAgentAdapter(client_factory=lambda: client)

    status = await adapter.send_message(run_id="session-42", prompt="Continue with step 2.")

    assert status.run_id == "session-42"
    assert status.status == "running"
    assert status.metadata["normalizedStatus"] == "running"
    assert len(client.sent_messages) == 1
    sent_req = client.sent_messages[0]
    assert sent_req.session_id == "session-42"
    assert sent_req.prompt == "Continue with step 2."
