"""The managed CLI must reach scoped machine auth, never operator admission."""

from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers import mcp_tools
from api_service.services.container_jobs import ContainerJobService
from moonmind import container_job_cli
from moonmind.workflows.temporal.artifacts import ArtifactTransactionOwnership
from moonmind.schemas.container_job_models import (
    ContainerJobAccepted,
    ContainerJobLogPage,
    ContainerJobStatus,
    OwnerIdentity,
)
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from tests.helpers.account_source import User

_SECRET = "test-container-cli-machine-secret"
_OWNER = OwnerIdentity(principalId="workflow-1", principalType="service")
_JOB_ID = "container-job:" + "1" * 32
_BASE_URL = "http://api:8000/api"
_ENV = {
    "MOONMIND_URL": _BASE_URL + "/",
    "MOONMIND_AGENT_RUN_ID": "run-1",
    "MOONMIND_RUNTIME_ID": "codex_cli",
    "MOONMIND_TASK_WORKFLOW_ID": "workflow-1",
    "MOONMIND_CONTAINER_JOBS_SESSION_ID": "session-1",
}
_SPEC = {
    "imageSourceRef": "moonmind-python-tests",
    "command": ["true"],
    "resources": {"cpuMillis": 100, "memoryMiB": 64},
}


def _capability(**overrides):
    return mint_container_job_session_capability(
        **{
            "secret": _SECRET,
            "owner": _OWNER,
            "agent_run_id": "run-1",
            "workflow_id": "workflow-1",
            "session_id": "session-1",
            "runtime_id": "codex_cli",
            "lifetime_seconds": 300,
            **overrides,
        }
    )


@pytest.fixture(params=[False, True], ids=["default-endpoint", "explicit-endpoint"])
def machine_route(request, monkeypatch):
    # An available local operator must never rescue an invalid machine token.
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = User(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        email="operator@example.test",
        is_active=True,
    )
    service = AsyncMock(spec=ContainerJobService)
    service.submit.return_value = ContainerJobAccepted(
        jobId=_JOB_ID,
        state="queued",
        createdAt=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service.status.return_value = ContainerJobStatus(
        jobId=_JOB_ID,
        state="succeeded",
        terminal={"exitCode": 0},
        updatedAt=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service.logs.return_value = ContainerJobLogPage(jobId=_JOB_ID, entries=[])
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(mcp_tools, "ContainerJobService", service_factory)
    def artifact_service(_session, *, transaction_ownership):
        assert transaction_ownership is ArtifactTransactionOwnership.CALLER
        return None

    monkeypatch.setattr(mcp_tools, "get_temporal_artifact_service", artifact_service)
    monkeypatch.setattr(mcp_tools, "container_jobs_ready", lambda: True)
    monkeypatch.setattr(mcp_tools.settings.security, "JWT_SECRET_KEY", _SECRET)
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")

    app = FastAPI()
    app.include_router(mcp_tools.router, prefix="/api")

    async def get_session():
        yield session

    # Only the database edge is replaced; both auth routes remain mounted.
    app.dependency_overrides[mcp_tools.get_async_session] = get_session
    requests = []
    with TestClient(app) as server:

        def forward(request):
            requests.append(request)
            response = server.request(
                request.method,
                str(request.url),
                headers=request.headers,
                content=request.content,
            )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=response.content,
            )

        monkeypatch.setattr(
            container_job_cli,
            "ContainerJobMcpClient",
            partial(
                container_job_cli.ContainerJobMcpClient,
                transport=httpx.MockTransport(forward),
            ),
        )
        env = dict(_ENV)
        if request.param:
            env["MOONMIND_CONTAINER_JOBS_MCP_URL"] = (
                "http://issuer:8000/api/mcp/container"
            )
        yield SimpleNamespace(
            env=env,
            session=session,
            service=service,
            service_factory=service_factory,
            requests=requests,
        )


def test_container_cli_preserves_service_capability_without_user_lookup(machine_route):
    route = machine_route
    token = _capability()
    result = container_job_cli.run_container_job(
        _SPEC, env={**route.env, "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": token}
    )

    assert (result.job_id, result.state, result.exit_code) == (_JOB_ID, "succeeded", 0)
    assert result.log_error is None
    route.session.get.assert_not_awaited()
    route.session.execute.assert_not_awaited()
    assert len(route.requests) == 3
    for request in route.requests:
        endpoint = route.env.get(
            "MOONMIND_CONTAINER_JOBS_MCP_URL", _BASE_URL + "/mcp/container"
        )
        assert str(request.url) == endpoint + "/tools/call"
        assert request.headers["authorization"] == f"Bearer {token}"
    for call in route.service_factory.call_args_list:
        assert call.kwargs["capability"].owner == _OWNER
    route.service.submit.assert_awaited_once()
    route.service.status.assert_awaited_once_with(owner=_OWNER, job_id=_JOB_ID)
    assert route.service.submit.await_args.kwargs["owner"] == _OWNER
    assert route.service.logs.await_args.kwargs["owner"] == _OWNER


@pytest.mark.parametrize(
    "credential,status,message",
    [
        ("missing", 401, "managed-session container capability is required"),
        ("expired", 401, "expired container-job capability"),
        ("forged", 401, "invalid container-job capability"),
        ("wrong-scope", 403, "exceeds the session capability"),
    ],
)
def test_container_cli_refuses_bad_capabilities_without_operator_fallback(
    machine_route, credential, status, message
):
    route = machine_route
    env = dict(route.env)
    if credential != "missing":
        claims = {
            "expired": {"now": 1},
            "forged": {"secret": "untrusted-test-signer"},
            "wrong-scope": {"session_id": "other"},
        }
        env["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"] = _capability(
            **claims[credential]
        )

    with pytest.raises(container_job_cli.ContainerJobCliError) as error:
        container_job_cli.run_container_job(_SPEC, env=env)

    assert f"HTTP {status}" in str(error.value)
    assert message in str(error.value)
    assert len(route.requests) == 1
    assert route.requests[0].url.path == "/api/mcp/container/tools/call"
    route.session.get.assert_not_awaited()
    route.session.execute.assert_not_awaited()
    route.service_factory.assert_not_called()
