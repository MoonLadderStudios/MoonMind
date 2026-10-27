"""Scoped assessment workload profiles through transport, service and worker.

GitHub issue #5035. The operator-admitted scope enters at the trusted Omnigent
launch boundary, travels only in the signed session capability, and is
enforced by the production MCP transport, Container Job service and Docker
worker. The Docker daemon is a deterministic command fixture.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import mcp_tools
from api_service.db.models import Base, ContainerJobRecord
from api_service.services.container_jobs import ContainerJobService
from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.container_job_cli import container_job_submission
from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.security.container_job_capabilities import (
    verify_container_job_session_capability,
)
from moonmind.security.repository_scanners import RepositoryScan, build_scanner_job
from moonmind.security.workload_profiles import WORKLOAD_PROFILES_ENV_KEY
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from tests.unit.omnigent.test_generic_platform_production_services import _plan

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SECRET = "assessment-profile-secret"
STEP = "workflow-1:run-1:assess:execution:1"
IMAGE = "sha256:" + "b" * 64

PROFILES = [
    {
        "profileRef": "source-scan",
        "imageSourceRef": "repository-security",
        "entrypoint": ["scanner"],
        "modes": [
            {
                "name": "scan",
                "actionClass": "source-analysis",
                "argv": [
                    "scan",
                    "--severity={severity}",
                    "{target}",
                    "--report=/outputs/report.json",
                ],
                "arguments": [
                    {"name": "severity", "type": "enum", "values": ["low", "high"]},
                    {"name": "target", "type": "input", "required": True},
                ],
            },
            {
                "name": "probe",
                "actionClass": "live-probe",
                "argv": ["probe", "{target}"],
                "arguments": [{"name": "target", "type": "input", "required": True}],
            },
        ],
        "environment": {"SCANNER_IGNORE_REPOSITORY_CONFIG": "1"},
        "caches": [{"cacheRef": "scanner-rules", "target": "/opt/rules"}],
        "configurationExceptions": [
            {"name": "inline-suppressions", "argv": ["--honor-inline-suppressions"]}
        ],
        "outputs": [{"name": "report", "relativePath": "report.json"}],
        "maxCpuMillis": 2000,
        "maxMemoryMiB": 2048,
        "maxTimeoutSeconds": 900,
    },
    {
        "profileRef": "image-scan",
        "imageSourceRef": "repository-security",
        "modes": [
            {
                "name": "scan",
                "actionClass": "source-analysis",
                "argv": ["image", "{target}"],
                "arguments": [{"name": "target", "type": "input", "required": True}],
            }
        ],
        "maxCpuMillis": 2000,
        "maxMemoryMiB": 2048,
        "maxTimeoutSeconds": 900,
    },
]

SCOPE = {
    "assessmentRef": "assessment-1",
    "profileRefs": ["source-scan"],
    "actionClasses": ["source-analysis"],
    "targets": [{"workspacePath": "app"}],
    "exclusions": ["app/vendor"],
    "maxJobs": 2,
    "maxJobSeconds": 600,
    "evidenceIntent": "Source findings for the admitted app routes",
}


def backend_settings(profiles=PROFILES):
    return resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [{"sourceRef": "repository-security", "image": "ghcr.io/x/scan:1"}]
            ),
            CACHE_SOURCES_ENV_KEY: json.dumps(
                [
                    {
                        "cacheRef": "scanner-rules",
                        "volumeName": "scanner_rules",
                        "target": "/opt/rules",
                        "readOnly": True,
                    }
                ]
            ),
            WORKLOAD_PROFILES_ENV_KEY: json.dumps(profiles),
        }
    )


def profile_spec(arguments=None, **selection):
    return {
        "workloadProfile": {
            "profileRef": "source-scan",
            "mode": "scan",
            "arguments": {"target": "source"} if arguments is None else arguments,
            **selection,
        },
        "inputAttachments": [{"name": "source", "workspacePath": "app/routes"}],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 300,
    }


@pytest_asyncio.fixture
async def journey(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    workspace_id = hashlib.sha256(f"workflow-1:{STEP}".encode()).hexdigest()[:24]
    repository = workspace_root / "temporal_sandbox" / workspace_id / "repo"
    (repository / "app" / "routes").mkdir(parents=True)
    (repository / "app" / "routes" / "invoices.py").write_text("return invoices[id]\n")
    (repository / ".scanner.yml").write_text("disable: all\n")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "local")
    SandboxWorkspaceRecordStore(workspace_root).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id="workflow-1",
            step_execution_id=STEP,
            relative_path="repo",
        )
    )
    locator = {"kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"}

    # Trusted boundary: the run workflow placed the operator's scope in the
    # step policy; the launch boundary signs it into the session capability.
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "workflow-1",
            "idempotencyKey": STEP,
            "parameters": {"requiredCapabilities": ["docker"]},
            "workspaceSpec": {"workspaceLocator": locator},
            "stepExecution": {
                "workflowId": "workflow-1",
                "runId": "run-1",
                "logicalStepId": "assess",
                "executionOrdinal": 1,
                "stepExecutionId": STEP,
                "runtimeContextPolicy": "fresh_agent_run",
                "skillSourcePolicy": {"assessmentScope": SCOPE},
            },
        }
    )
    environment = dict(
        OmnigentRuntimeEnvironmentService(
            moonmind_url="http://api:8000", signing_secret=SECRET
        ).build(
            request=request,
            plan=_plan("test/model"),
            host_lease_ref="lease-1",
            launch_policy=get_launch_policy("omnigent-on-demand@1"),
            workspace_attachment={"accessMode": "read-write"},
        )
    )
    token = environment["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"]
    capability = verify_container_job_session_capability(token, secret=SECRET)
    assert capability.assessment_scope is not None

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    temporal = AsyncMock()
    settings = backend_settings()
    monkeypatch.setattr(mcp_tools.settings.security, "JWT_SECRET_KEY", SECRET)
    monkeypatch.setattr(mcp_tools, "container_jobs_ready", lambda: True)
    monkeypatch.setattr(mcp_tools, "get_temporal_artifact_service", lambda _s: None)
    monkeypatch.setattr(
        mcp_tools,
        "ContainerJobService",
        lambda session, **kwargs: ContainerJobService(
            session, temporal=temporal, backend_settings=settings, **kwargs
        ),
    )
    app = FastAPI()
    app.include_router(mcp_tools.router)

    async def session_dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[mcp_tools.get_async_session] = session_dependency

    class Journey:
        pass

    state = Journey()
    state.environment = environment
    state.token = token
    state.capability = capability
    state.temporal = temporal
    state.sessions = sessions
    state.workspace_root = workspace_root
    state.settings = settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def submit(arguments, *, raw=False):
            # The CLI builds a submission from the session environment; a raw
            # MCP caller can send any envelope with the same bearer.
            body = (
                arguments
                if raw
                else container_job_submission(
                    arguments,
                    env=environment,
                    request_id=hashlib.sha256(
                        json.dumps(arguments, sort_keys=True).encode()
                    ).hexdigest(),
                )
            )
            return await client.post(
                "/mcp/container/tools/call",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool": "container.submit", "arguments": body},
            )

        async def jobs():
            async with sessions() as session:
                return await session.scalar(
                    select(func.count()).select_from(ContainerJobRecord)
                )

        state.submit = submit
        state.jobs = jobs
        yield state
    await engine.dispose()


async def launch(state, workflow_input, *, settings=None, commands=None):
    """Drive the production worker Activities up to ``docker create``."""

    commands = [] if commands is None else commands

    async def daemon(argv):
        commands.append(tuple(argv))
        if argv[0] == "inspect":
            return 1, b"", b"No such container"
        if argv[0] == "logs":
            return 0, b"scan complete\n", b""
        return 0, b"", b""

    published = {}

    async def publish(_request, name, payload):
        published[name] = payload
        return f"art_{hashlib.sha256(name.encode()).hexdigest()[:20]}"

    backend = DockerContainerJobBackend(
        workspace_root=state.workspace_root,
        command_runner=daemon,
        evidence_publisher=publish,
        settings=settings or state.settings,
    )
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    workflow = MoonMindContainerJobWorkflow()

    async def invoke_activity(name, payload, **_kwargs):
        return await getattr(activities, name.replace(".", "_"))(payload)

    import moonmind.workflows.temporal.workflows.container_job as container_job

    original = container_job.workflow.execute_activity
    container_job.workflow.execute_activity = invoke_activity
    try:
        request = ContainerJobActivityRequest(
            jobId=workflow_input.job_id,
            owner=workflow_input.owner,
            ownershipToken=workflow_input.ownership_token,
            request=workflow_input.request,
        )
        resolved = await workflow._activity("container_job.resolve_workspace", request)
        request.resolved_workspace_ref = resolved.resolved_workspace_ref
        request.resolved_image_ref = IMAGE
        await workflow._activity("container_job.create_container", request)
    finally:
        container_job.workflow.execute_activity = original
    return backend, request, commands, published


def created_command(commands):
    created = next(command for command in commands if command[0] == "create")
    return created, list(created[created.index(IMAGE) + 1 :])


async def test_admitted_profile_variations_execute_and_return_scoped_evidence(journey):
    expected = {
        "default": ["scan", "/inputs/source", "--report=/outputs/report.json"],
        "high": [
            "scan",
            "--severity=high",
            "/inputs/source/invoices.py",
            "--report=/outputs/report.json",
        ],
    }
    for name, arguments in (
        ("default", {"target": "source"}),
        ("high", {"target": "source/invoices.py", "severity": "high"}),
    ):
        response = await journey.submit(profile_spec(arguments))
        assert response.status_code == 200, response.text
        workflow_input = journey.temporal.start_container_job.await_args.args[0]
        spec = workflow_input.request.spec
        # The persisted and started request carries the rendered content and
        # the signed assessment identity, not just the caller's label.
        assert spec.workload_profile.assessment_ref == "assessment-1"
        assert spec.image_source_ref == "repository-security"
        assert spec.command == expected[name]
        async with journey.sessions() as session:
            record = await ContainerJobService(session).repository.get_for_owner(
                owner=journey.capability.owner, job_id=workflow_input.job_id
            )
            assert record.request_json["spec"]["command"] == expected[name]

        backend, request, commands, published = await launch(journey, workflow_input)
        created, command = created_command(commands)
        assert command == expected[name]
        assert created[created.index("--entrypoint") + 1] == "scanner"
        assert created[created.index("--network") + 1] == "none"
        assert "SCANNER_IGNORE_REPOSITORY_CONFIG=1" in created
        assert "--privileged" not in created

        # The captured snapshot is all the tool can read; its report lands in
        # the job-owned output location and is collected as scoped evidence.
        storage = storage_for(journey.workspace_root, workflow_input.job_id)
        assert (storage.inputs / "source" / "invoices.py").read_text() == (
            "return invoices[id]\n"
        )
        assert not (storage.inputs / "source" / ".scanner.yml").exists()
        (storage.outputs / "report.json").write_text(json.dumps({"variation": name}))
        request.container_ref = "fixture-container"
        request.terminal_state = "succeeded"
        request.exit_code = 0
        result = await backend.publish_evidence(request)
        assert result.artifacts_ref
        assert json.loads(published[f"{workflow_input.job_id}-output-report.json"]) == {
            "variation": name
        }


@pytest.mark.parametrize(
    "attempt",
    [
        "direct_image",
        "image_source_entrypoint",
        "entrypoint_override",
        "shell_argument_override",
        "repository_configuration_environment",
        "repository_scanner_job",
        "other_assessment",
        "profile_outside_scope",
        "action_class_outside_scope",
        "unadmitted_configuration_exception",
        "target_outside_scope",
        "excluded_target",
        "raw_mcp_generic_job",
    ],
)
async def test_bypass_attempts_are_refused_before_any_job_exists(journey, attempt):
    generic = {
        "image": "alpine",
        "command": ["sh", "-c", "cat /workspace/.scanner.yml"],
        "resources": {"cpuMillis": 100, "memoryMiB": 64},
    }
    body = {
        "direct_image": generic,
        "image_source_entrypoint": {
            **{k: v for k, v in generic.items() if k != "image"},
            "imageSourceRef": "repository-security",
            "entrypoint": ["sh"],
        },
        "entrypoint_override": {**profile_spec(), "entrypoint": ["sh"]},
        "shell_argument_override": {
            **profile_spec(),
            "command": ["sh", "-c", "scanner scan /inputs/source"],
        },
        "repository_configuration_environment": {
            **profile_spec(),
            "environment": [
                {"name": "SCANNER_CONFIG", "value": "/inputs/source/.scanner.yml"}
            ],
        },
        "repository_scanner_job": build_scanner_job(
            RepositoryScan(scanner="gitleaks", outputDirectory="out")
        ),
        "other_assessment": profile_spec(assessmentRef="assessment-2"),
        "profile_outside_scope": {
            **profile_spec(),
            "workloadProfile": {
                "profileRef": "image-scan",
                "mode": "scan",
                "arguments": {"target": "source"},
            },
        },
        "action_class_outside_scope": {
            **profile_spec(),
            "workloadProfile": {
                "profileRef": "source-scan",
                "mode": "probe",
                "arguments": {"target": "source"},
            },
        },
        "unadmitted_configuration_exception": profile_spec(
            configurationExceptions=["inline-suppressions"]
        ),
        "target_outside_scope": {
            **profile_spec(),
            "inputAttachments": [{"name": "source", "workspacePath": "."}],
        },
        "excluded_target": {
            **profile_spec(),
            "inputAttachments": [{"name": "source", "workspacePath": "app/vendor"}],
        },
    }
    if attempt == "raw_mcp_generic_job":
        envelope = container_job_submission(
            profile_spec(), env=journey.environment, request_id="raw"
        )
        envelope["spec"] = {**generic, "workspaceRef": envelope["spec"]["workspaceRef"]}
        response = await journey.submit(envelope, raw=True)
    else:
        response = await journey.submit(body[attempt])
    assert response.status_code in {403, 422}, response.text
    assert await journey.jobs() == 0
    journey.temporal.start_container_job.assert_not_called()


async def test_budget_replay_and_changed_authority_stay_denied(journey):
    first = profile_spec({"target": "source"})
    assert (await journey.submit(first)).status_code == 200
    started = journey.temporal.start_container_job.await_args.args[0]
    assert (
        await journey.submit(profile_spec({"target": "source", "severity": "low"}))
    ).status_code == 200
    assert await journey.jobs() == 2

    # The budget admits no third job, and the refusal does not fall back to a
    # generic job or another profile.
    third = await journey.submit(profile_spec({"target": "source", "severity": "high"}))
    assert third.status_code == 403, third.text
    assert third.json()["detail"]["code"] == "assessment_scope_denied"
    generic = await journey.submit(
        {"image": "alpine", "resources": {"cpuMillis": 100, "memoryMiB": 64}}
    )
    assert generic.status_code == 403
    assert await journey.jobs() == 2

    # An exact replay consumes no budget; a changed request under the same
    # idempotency key is a conflict rather than a new authority.
    replay = await journey.submit(first)
    assert replay.status_code == 200 and replay.json()["result"]["replayed"] is True
    changed = container_job_submission(
        profile_spec({"target": "source", "severity": "high"}),
        env=journey.environment,
        request_id=hashlib.sha256(
            json.dumps(first, sort_keys=True).encode()
        ).hexdigest(),
    )
    conflict = await journey.submit(changed, raw=True)
    assert conflict.status_code == 409, conflict.text
    assert await journey.jobs() == 2

    # A profile edited after admission no longer authorizes the persisted
    # content: the worker refuses before the container exists.
    edited = json.loads(json.dumps(PROFILES))
    edited[0]["modes"][0]["argv"].insert(1, "--allow-network")
    # A persisted request altered outside admission is refused the same way.
    tampered = started.model_copy(deep=True)
    tampered.request.spec.command = ["sh", "-c", "cat /inputs/source/.scanner.yml"]
    for workflow_input, settings in (
        (started, backend_settings(edited)),
        (tampered, None),
    ):
        commands = []
        with pytest.raises(Exception, match="launch|profile"):
            await launch(journey, workflow_input, settings=settings, commands=commands)
        assert not any(command[0] == "create" for command in commands)


async def test_unscoped_development_sessions_keep_generic_jobs(journey):
    from moonmind.security.container_job_capabilities import (
        mint_container_job_session_capability,
    )

    capability = journey.capability
    token = mint_container_job_session_capability(
        secret=SECRET,
        owner=capability.owner,
        agent_run_id=capability.agent_run_id,
        workflow_id=capability.workflow_id,
        step_id=capability.step_id,
        session_id=capability.session_id,
        runtime_id=capability.runtime_id,
        source_kind="omnigent",
        workspace_kind="sandbox",
        workspace_id=capability.workspace_id,
        lifetime_seconds=300,
    )
    body = container_job_submission(
        {
            "image": "alpine",
            "command": ["true"],
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
        env=journey.environment,
        request_id="development",
    )
    transport_app = FastAPI()
    transport_app.include_router(mcp_tools.router)

    async def session_dependency():
        async with journey.sessions() as session:
            yield session

    transport_app.dependency_overrides[mcp_tools.get_async_session] = session_dependency
    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        allowed = await client.post(
            "/mcp/container/tools/call",
            headers={"Authorization": f"Bearer {token}"},
            json={"tool": "container.submit", "arguments": body},
        )
        assert allowed.status_code == 200, allowed.text
        # A development session cannot claim an assessment it was not given.
        claimed = container_job_submission(
            profile_spec(assessmentRef="assessment-1"),
            env=journey.environment,
            request_id="claimed",
        )
        refused = await client.post(
            "/mcp/container/tools/call",
            headers={"Authorization": f"Bearer {token}"},
            json={"tool": "container.submit", "arguments": claimed},
        )
        assert refused.status_code == 403, refused.text
        # Without an assessment, the same session may still use a profile.
        profiled = container_job_submission(
            profile_spec(), env=journey.environment, request_id="profiled"
        )
        assert (
            await client.post(
                "/mcp/container/tools/call",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool": "container.submit", "arguments": profiled},
            )
        ).status_code == 200


async def test_scoped_step_cannot_fan_out_to_unscoped_children():
    """Alternate transport: execution fan-out would start children unscoped."""

    from types import SimpleNamespace
    from unittest.mock import patch

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.security.execution_fanout_capabilities import (
        verify_execution_fanout_capability,
    )
    from moonmind.workflows.temporal.workflows.run import (
        RUN_ASSESSMENT_SCOPE_AUTHORITY_PATCH,
        RUN_EXECUTION_FANOUT_AUTHORIZATION_PATCH,
        MoonMindRunWorkflow,
    )

    def step_policy(workflow_parameters):
        # The production run workflow builds the trusted step policy; the
        # selected Skill alone would authorize fan-out.
        with (
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=SimpleNamespace(
                    namespace="default",
                    workflow_id="workflow-1",
                    run_id="run-1",
                    parent=None,
                ),
            ),
            patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                side_effect=lambda patch_id: patch_id
                in {
                    RUN_ASSESSMENT_SCOPE_AUTHORITY_PATCH,
                    RUN_EXECUTION_FANOUT_AUTHORIZATION_PATCH,
                },
            ),
        ):
            run = MoonMindRunWorkflow()
            run._execution_fanout_authorization_by_step["assess"] = {
                "authorized": True,
                "selectedSkill": "security-review",
                "sourceKind": "built_in",
                "reasonCode": "trusted_resolved_skill_requirement",
            }
            request = run._build_agent_execution_request(
                node_inputs={"runtime": {"mode": "omnigent"}},
                node_id="assess",
                tool_name="omnigent",
                workflow_parameters=workflow_parameters,
            )
        return dict(request.step_execution.skill_source_policy)

    def build(policy):
        request = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "correlationId": "workflow-1",
                "idempotencyKey": STEP,
                "parameters": {"requiredCapabilities": ["execution.fanout"]},
                "stepExecution": {
                    "workflowId": "workflow-1",
                    "runId": "run-1",
                    "logicalStepId": "assess",
                    "executionOrdinal": 1,
                    "stepExecutionId": STEP,
                    "runtimeContextPolicy": "fresh_agent_run",
                    "skillSourcePolicy": policy,
                },
            }
        )
        return OmnigentRuntimeEnvironmentService(
            moonmind_url="http://api:8000", signing_secret=SECRET
        ).build(
            request=request,
            plan=_plan("test/model"),
            host_lease_ref="lease-1",
            launch_policy=get_launch_policy("omnigent-on-demand@1"),
            workspace_attachment={"accessMode": "read-write"},
        )

    # Control: the same Skill on an unscoped run receives a fan-out bearer.
    unscoped = build(step_policy({}))
    assert (
        verify_execution_fanout_capability(
            unscoped["MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN"], secret=SECRET
        ).parent_workflow_id
        == "workflow-1"
    )

    scoped_policy = step_policy({"assessmentScope": SCOPE})
    assert scoped_policy["executionFanout"]["authorized"] is False
    with pytest.raises(HarnessPlatformError) as denied:
        build(scoped_policy)
    assert denied.value.code == "authorization_denied"
    # Even a forged authorized decision beside the scope cannot mint fan-out.
    with pytest.raises(HarnessPlatformError, match="assessment-scoped") as forged:
        build(
            {
                **scoped_policy,
                "executionFanout": {"authorized": True, "sourceKind": "built_in"},
            }
        )
    assert forged.value.code == "authorization_denied"
