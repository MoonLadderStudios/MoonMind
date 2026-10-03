"""A scoped session bearer cannot read or cancel unrelated same-owner jobs."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import mcp_tools
from api_service.db.models import Base
from api_service.services.container_jobs import ContainerJobService
from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/scope.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["status", "logs", "artifacts", "cancel"])
@pytest.mark.parametrize("kind", ["managed_session", "omnigent"])
@pytest.mark.parametrize(
    "mismatch",
    ["session", "agentRunId", "workflowId", "stepId", "workspace", "missing_source"],
)
async def test_scoped_endpoint_binds_all_operations_to_saved_job(
    session_factory,
    monkeypatch,
    tool,
    kind,
    mismatch,
):
    owner = OwnerIdentity(principalId="single-operator", principalType="user")
    source = {
        "source": kind,
        "workflowId": "workflow-1",
        "agentRunId": "run-1",
        "stepId": "step-1",
        (
            "managedSessionId"
            if kind == "managed_session"
            else "omnigentConversationId"
        ): "session-1",
    }
    workspace = (
        {
            "kind": "managed_runtime",
            "agentRunId": "run-1",
            "runtimeId": "codex_cli",
            "relativePath": "repo",
        }
        if kind == "managed_session"
        else {"kind": "sandbox", "workspaceId": "sandbox-1", "relativePath": "repo"}
    )
    own = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "own",
            "source": source,
            "spec": {
                "image": "alpine",
                "workspaceRef": workspace,
                "resources": {"cpuMillis": 100, "memoryMiB": 64},
            },
        }
    )
    other_payload = deepcopy(own.model_dump(mode="json", by_alias=True))
    other_payload["idempotencyKey"] = "other"
    if mismatch == "session":
        key = (
            "managedSessionId"
            if kind == "managed_session"
            else "omnigentConversationId"
        )
        other_payload["source"][key] = "session-other"
    elif mismatch == "workspace":
        other_payload["spec"]["workspaceRef"]["relativePath"] = "other"
    elif mismatch != "missing_source":
        other_payload["source"][mismatch] = "other"
    other = ContainerJobSubmitRequest.model_validate(other_payload)
    async with session_factory() as session:
        repository = ContainerJobService(session).repository
        own_record, _ = await repository.create_or_replay(owner=owner, request=own)
        other_record, _ = await repository.create_or_replay(owner=owner, request=other)
        if mismatch == "missing_source":
            other_record.source_json = {}
        own_id, other_id = own_record.job_id, other_record.job_id
        await session.commit()

    temporal = AsyncMock()
    evidence = AsyncMock()
    monkeypatch.setattr(
        mcp_tools.settings.feature_flags, "container_jobs_enabled", True
    )
    monkeypatch.setattr(
        mcp_tools.settings.security, "JWT_SECRET_KEY", "test-scoped-job-secret"
    )
    monkeypatch.setattr(
        mcp_tools, "get_temporal_artifact_service", lambda _session: evidence
    )
    monkeypatch.setattr(
        mcp_tools,
        "ContainerJobService",
        lambda session, **kwargs: ContainerJobService(
            session, temporal=temporal, **kwargs
        ),
    )
    token = mint_container_job_session_capability(
        secret="test-scoped-job-secret",
        owner=owner,
        agent_run_id="run-1",
        workflow_id="workflow-1",
        step_id="step-1",
        session_id="session-1",
        runtime_id="codex_cli",
        source_kind=kind,
        workspace_kind=workspace["kind"],
        workspace_id="sandbox-1",
        lifetime_seconds=300,
    )
    app = FastAPI()
    app.include_router(mcp_tools.router)

    async def get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[mcp_tools.get_async_session] = get_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:

        async def call(job_id):
            args = {"jobId": job_id}
            if tool == "cancel":
                args["idempotencyKey"] = "cancel"
            return await client.post(
                "/mcp/container/tools/call",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool": f"container.{tool}", "arguments": args},
            )

        allowed = await call(own_id)
        assert allowed.status_code == 200, allowed.text
        temporal.reset_mock()
        evidence.reset_mock()
        denied = await call(other_id)
        assert denied.status_code == 404, denied.text
        temporal.signal_container_job_cancel.assert_not_called()
        evidence.read.assert_not_called()
    async with session_factory() as session:
        record = await ContainerJobService(session).repository.get_for_owner(
            owner=owner, job_id=other_id
        )
        assert record.state == "queued"
