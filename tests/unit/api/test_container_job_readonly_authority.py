"""Repository access authority survives the scoped container-job handoff."""

from __future__ import annotations

import hashlib
import os
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import mcp_tools
from api_service.db.models import Base
from api_service.services.container_jobs import ContainerJobService
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.workspace_sources import issue_existing_workspace_grant
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.security.container_job_capabilities import (
    verify_container_job_session_capability,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from tests.unit.omnigent.test_generic_platform_production_services import _plan


@pytest_asyncio.fixture
async def job_sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize(
    "attachment", [None, {}, {"accessMode": "rw"}, {"accessMode": []}]
)
def test_container_capability_requires_materialized_access_mode(attachment):
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "step-1",
            "parameters": {"requiredCapabilities": ["docker"]},
            "workspaceSpec": {
                "workspaceLocator": {"kind": "sandbox", "workspaceId": "sandbox-1"}
            },
        }
    )
    with pytest.raises(HarnessPlatformError, match="workspace access mode"):
        OmnigentRuntimeEnvironmentService(
            moonmind_url="http://api:8000", signing_secret="test-container-secret"
        ).build(
            request=request,
            plan=_plan("test/model"),
            host_lease_ref="lease-1",
            launch_policy=get_launch_policy("omnigent-on-demand@1"),
            workspace_attachment=attachment,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness_id", ["codex-native", "claude-code-native", "opencode-native"]
)
@pytest.mark.parametrize(
    "mutation,grant_mode,expected_read_only",
    [
        ("allowed", None, False),
        ("read_only", None, True),
        ("allowed", "read_only", True),
    ],
)
@pytest.mark.parametrize("requested_read_only", [None, False, True])
@pytest.mark.parametrize("volume_name", [None, "agent_workspaces"])
async def test_workspace_authority_survives_api_persistence_and_worker_launch(
    tmp_path,
    monkeypatch,
    job_sessions,
    harness_id,
    mutation,
    grant_mode,
    expected_read_only,
    requested_read_only,
    volume_name,
):
    workspace_root = tmp_path / "workspaces"
    workspace_id = hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]
    repository = workspace_root / "temporal_sandbox" / workspace_id / "repo"
    repository.mkdir(parents=True)
    (repository / "source.txt").write_text("authoritative source", encoding="utf-8")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "local")
    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", "test-grant-secret")
    SandboxWorkspaceRecordStore(workspace_root).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id="workflow-1",
            step_execution_id="step-1",
            relative_path="repo",
        )
    )
    locator = {"kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"}
    workspace_spec = {"workspaceLocator": locator}
    if grant_mode:
        grant = issue_existing_workspace_grant(
            workspace_id=workspace_id,
            owner_workflow_id="workflow-1",
            owner_step_execution_id="step-1",
            grantee_workflow_id="workflow-1",
            mode=grant_mode,
            generation=1,
            secret="test-grant-secret",
        )
        workspace_spec["workspaceSource"] = {
            "kind": "existing_workspace",
            "existingWorkspaceGrant": {
                "workspaceId": grant.workspace_id,
                "ownerWorkflowId": grant.owner_workflow_id,
                "ownerStepExecutionId": grant.owner_step_execution_id,
                "generation": grant.generation,
                "mode": grant.mode,
                "expiresAt": grant.expires_at.isoformat(),
                "grantDigest": grant.grant_digest,
            },
        }
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": "step-1",
            "parameters": {"requiredCapabilities": ["docker"]},
            "workspaceSpec": workspace_spec,
        }
    )
    plan = create_execution_plan_envelope(
        {
            **_plan("test/model").payload.model_dump(mode="json", by_alias=True),
            "harnessId": harness_id,
            "workspaceMutation": mutation,
        }
    )
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=AsyncMock(return_value=(0, "", "")),
        workspace_root=workspace_root,
    )
    attachment = await materializer.materialize(
        request,
        mutation=plan.payload.workspaceMutation,
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="http://api:8000", signing_secret="test-container-secret"
    ).build(
        request=request,
        plan=plan,
        host_lease_ref="lease-1",
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        workspace_attachment=attachment,
    )
    token = environment["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"]
    capability = verify_container_job_session_capability(
        token, secret="test-container-secret"
    )
    assert capability.workspace_read_only is expected_read_only
    expected_mount_read_only = bool(expected_read_only or requested_read_only)

    submission = {
        "idempotencyKey": "test-readonly",
        "source": {
            "source": "omnigent",
            "workflowId": capability.workflow_id,
            "stepId": capability.step_id,
            "agentRunId": capability.agent_run_id,
            "omnigentConversationId": capability.session_id,
        },
        "spec": {
            "image": "alpine",
            "workspaceRef": locator,
            "command": ["cat", "/workspace/source.txt"],
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    }
    if requested_read_only is not None:
        submission["spec"]["workspaceReadOnly"] = requested_read_only
    temporal = AsyncMock()
    monkeypatch.setattr(
        mcp_tools.settings.security, "JWT_SECRET_KEY", "test-container-secret"
    )
    monkeypatch.setattr(mcp_tools, "container_jobs_ready", lambda: True)
    monkeypatch.setattr(mcp_tools, "get_temporal_artifact_service", lambda _session: None)
    monkeypatch.setattr(
        mcp_tools,
        "ContainerJobService",
        lambda session, **_kwargs: ContainerJobService(session, temporal=temporal),
    )
    app = FastAPI()
    app.include_router(mcp_tools.router, prefix="/api")
    async with job_sessions() as session:

        async def session_dependency():
            yield session

        app.dependency_overrides[mcp_tools.get_async_session] = session_dependency
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mcp/container/tools/call",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool": "container.submit", "arguments": submission},
            )
        assert response.status_code == 200, response.text
        workflow_input = temporal.start_container_job.await_args.args[0]
        record = await ContainerJobService(session).repository.get_for_owner(
            owner=capability.owner, job_id=workflow_input.job_id
        )
        assert (
            bool(record.request_json["spec"].get("workspaceReadOnly"))
            is expected_mount_read_only
        )
        assert (
            bool(workflow_input.request.spec.workspace_read_only)
            is expected_mount_read_only
        )

    commands = []

    async def daemon(argv):
        commands.append(tuple(argv))
        if tuple(argv[:3]) == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"No such container"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=workspace_root,
        workspace_volume_name=volume_name,
        command_runner=daemon,
    )
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)

    async def invoke_activity(name, payload, **_kwargs):
        return await getattr(activities, name.replace(".", "_"))(payload)

    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.container_job.workflow.execute_activity",
        invoke_activity,
    )
    activity_request = ContainerJobActivityRequest(
        jobId=workflow_input.job_id,
        owner=workflow_input.owner,
        ownershipToken=workflow_input.ownership_token,
        request=workflow_input.request,
    )
    workflow = MoonMindContainerJobWorkflow()
    resolved = await workflow._activity(
        "container_job.resolve_workspace", activity_request
    )
    activity_request.resolved_workspace_ref = resolved.resolved_workspace_ref
    activity_request.resolved_workspace_volume_name = (
        resolved.resolved_workspace_volume_name
    )
    activity_request.resolved_workspace_volume_subpath = (
        resolved.resolved_workspace_volume_subpath
    )
    activity_request.resolved_image_ref = "sha256:" + "a" * 64
    await workflow._activity("container_job.create_container", activity_request)
    created = next(command for command in commands if command[0] == "create")
    mount = created[created.index("--mount") + 1]
    assert mount.endswith(",readonly") is expected_mount_read_only
    assert mount.startswith("type=volume" if volume_name else "type=bind")
