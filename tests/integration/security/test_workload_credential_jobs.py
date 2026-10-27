"""Scoped workload credentials from admission to launch (#5037).

The operator's scope, with credential grants, enters at the trusted Omnigent
launch boundary and travels in the signed session capability. A submission
through the production MCP transport is admitted by the Container Job service;
the worker Activities then resolve the stored secret and exchange it, with the
production boto3 STS adapter, at a local STS fixture. The Docker daemon is a
deterministic command fixture. Seeded secret bytes are searched for in every
request, history payload, tool result and published artifact.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import mcp_tools
from api_service.db.models import Base, ContainerJobRecord
from api_service.services.container_jobs import ContainerJobService
from moonmind.auth.secret_refs import SecretMissingError
from moonmind.config.container_backend_settings import (
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
from moonmind.security.workload_profiles import WORKLOAD_PROFILES_ENV_KEY
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import container_job
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from tests.unit.omnigent.test_generic_platform_production_services import _plan

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SECRET = "workload-credential-signing-secret"
STEP = "workflow-1:run-1:assess:execution:1"
IMAGE = "sha256:" + "f" * 64
ROLE = "arn:aws:iam::123456789012:role/audit"
SOURCE = {
    "accessKeyId": "AKIASEEDSOURCE0001",
    "secretAccessKey": "seed-source-secret-bytes",
}
APP_VALUE = "seed-app-login-bytes"
ISSUED = (
    "ASIASEEDSESSION0001",
    "seed-session-secret-bytes",
    "seed-session-token-bytes",
)
SEEDS = (*SOURCE.values(), APP_VALUE, *ISSUED)

PROFILES = [
    {
        "profileRef": "cloud-audit",
        "imageSourceRef": "cloud-scanner",
        "modes": [
            {
                "name": "audit",
                "actionClass": "cloud-inventory",
                "argv": ["audit", "{target}", "--output=/outputs/report.json"],
                "arguments": [{"name": "target", "type": "input", "required": True}],
            }
        ],
        "credentials": [
            {
                "name": "cloud",
                "purpose": "cloud_inventory",
                "delivery": "file",
                "issuer": "aws-sts",
            },
            {
                "name": "app",
                "purpose": "application_auth",
                "delivery": "env",
                "environmentName": "APP_LOGIN",
            },
        ],
        "outputs": [{"name": "report", "relativePath": "report.json"}],
        "maxCpuMillis": 2000,
        "maxMemoryMiB": 2048,
        "maxTimeoutSeconds": 1800,
    }
]


def scope():
    expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    return {
        "assessmentRef": "assessment-1",
        "profileRefs": ["cloud-audit"],
        "actionClasses": ["cloud-inventory"],
        "targets": [{"workspacePath": "config"}],
        "maxJobs": 3,
        "maxJobSeconds": 1800,
        "evidenceIntent": "Read-only inventory of the admitted account",
        "grants": [
            {
                "ref": "db://aws-audit",
                "purpose": "cloud_inventory",
                "account": "123456789012",
                "profileRefs": ["cloud-audit"],
                "expiresAt": expires,
                "roleArn": ROLE,
                "revision": 3,
            },
            {
                "ref": "db://app-login",
                "purpose": "application_auth",
                "account": "https://app.test",
                "profileRefs": ["cloud-audit"],
                "expiresAt": expires,
            },
        ],
    }


CLOUD = {"slot": "cloud", "credentialRef": "db://aws-audit", "account": "123456789012"}
APP = {"slot": "app", "credentialRef": "db://app-login", "account": "https://app.test"}


def profile_spec(*credentials):
    return {
        "workloadProfile": {
            "profileRef": "cloud-audit",
            "mode": "audit",
            "arguments": {"target": "config"},
            "credentials": list(credentials),
        },
        "inputAttachments": [{"name": "config", "workspacePath": "config"}],
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 1200,
    }


class StsFixture(BaseHTTPRequestHandler):
    """Minimal AssumeRole endpoint: records the request, answers or denies."""

    calls: list[dict] = []
    deny = False

    def do_POST(self):  # noqa: N802 - http.server API
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        type(self).calls.append(
            {
                "form": parse_qs(body),
                "authorization": self.headers.get("Authorization", ""),
            }
        )
        if type(self).deny:
            status, reply = 403, (
                "<ErrorResponse><Error><Type>Sender</Type><Code>AccessDenied</Code>"
                f"<Message>not authorized {SOURCE['secretAccessKey']}</Message></Error>"
                "<RequestId>1</RequestId></ErrorResponse>"
            )
        else:
            expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            status, reply = 200, (
                '<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">'
                "<AssumeRoleResult><Credentials>"
                f"<AccessKeyId>{ISSUED[0]}</AccessKeyId>"
                f"<SecretAccessKey>{ISSUED[1]}</SecretAccessKey>"
                f"<SessionToken>{ISSUED[2]}</SessionToken>"
                f"<Expiration>{expires}</Expiration></Credentials>"
                "<AssumedRoleUser><AssumedRoleId>AROA:job</AssumedRoleId>"
                f"<Arn>{ROLE}/job</Arn></AssumedRoleUser></AssumeRoleResult>"
                "<ResponseMetadata><RequestId>1</RequestId></ResponseMetadata>"
                "</AssumeRoleResponse>"
            )
        self.send_response(status)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply.encode())

    def log_message(self, *_args):
        pass


@pytest.fixture
def sts(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), StsFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    StsFixture.calls = []
    StsFixture.deny = False
    # Configuration, not credentials: the adapter still signs only with the
    # stored source key, never with these ambient worker values.
    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAAMBIENTWORKER")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-worker-secret")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    yield StsFixture
    server.shutdown()


@pytest_asyncio.fixture
async def journey(tmp_path, monkeypatch, sts):
    workspace_root = tmp_path / "workspaces"
    workspace_id = hashlib.sha256(f"workflow-1:{STEP}".encode()).hexdigest()[:24]
    repository = workspace_root / "temporal_sandbox" / workspace_id / "repo"
    (repository / "config").mkdir(parents=True)
    (repository / "config" / "accounts.json").write_text('{"account":"123456789012"}')
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
                "skillSourcePolicy": {"assessmentScope": scope()},
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
    assert capability.assessment_scope.grants

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    temporal = AsyncMock()
    settings = resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [{"sourceRef": "cloud-scanner", "image": "ghcr.io/x/prowler:1"}]
            ),
            WORKLOAD_PROFILES_ENV_KEY: json.dumps(PROFILES),
        }
    )
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
    state.temporal = temporal
    state.sessions = sessions
    state.workspace_root = workspace_root
    state.settings = settings
    state.store = {
        "db://aws-audit": (3, json.dumps(SOURCE)),
        "db://app-login": (1, APP_VALUE),
    }
    state.resolved = []
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def call(tool, arguments):
            return await client.post(
                "/mcp/container/tools/call",
                headers={"Authorization": f"Bearer {token}"},
                json={"tool": tool, "arguments": arguments},
            )

        async def submit(spec):
            body = container_job_submission(
                spec,
                env=environment,
                request_id=hashlib.sha256(
                    json.dumps(spec, sort_keys=True).encode()
                ).hexdigest(),
            )
            return await call("container.submit", body)

        async def jobs():
            async with sessions() as session:
                return await session.scalar(
                    select(func.count()).select_from(ContainerJobRecord)
                )

        state.call = call
        state.submit = submit
        state.jobs = jobs
        yield state
    await engine.dispose()


async def run_worker(state, workflow_input, *, outputs=None, logs=b""):
    """Drive the production worker Activities through launch and evidence."""

    commands, published = [], {}

    async def daemon(argv):
        argv = tuple(argv)
        commands.append(argv)
        if argv[:3] == ("inspect", "--format", "{{json .State}}"):
            return 0, b'{"Running":false,"ExitCode":0}', b""
        if argv[0] == "inspect":
            return 1, b"", b"No such container"
        if argv[0] == "logs":
            return 0, logs, b""
        return 0, b"", b""

    async def publish(_request, name, payload):
        published[name] = payload
        return f"art_{hashlib.sha256(name.encode()).hexdigest()[:20]}"

    async def resolve(ref, revision):
        state.resolved.append((ref, revision))
        current = state.store.get(ref)
        if current is None or (revision is not None and current[0] != revision):
            raise SecretMissingError("not active at that revision")
        return current[1]

    backend = DockerContainerJobBackend(
        workspace_root=state.workspace_root,
        command_runner=daemon,
        evidence_publisher=publish,
        settings=state.settings,
        secret_resolver=resolve,
    )
    activities = TemporalAgentRuntimeActivities(container_job_backend=backend)
    workflow = MoonMindContainerJobWorkflow()

    async def invoke_activity(name, payload, **_kwargs):
        # Every Activity payload is the serialized history form.
        state.history.append(json.dumps(payload))
        return await getattr(activities, name.replace(".", "_"))(payload)

    state.history = [workflow_input.model_dump_json(by_alias=True)]
    original = container_job.workflow.execute_activity
    container_job.workflow.execute_activity = invoke_activity
    request = ContainerJobActivityRequest(
        jobId=workflow_input.job_id,
        owner=workflow_input.owner,
        ownershipToken=workflow_input.ownership_token,
        request=workflow_input.request,
        workloadCredentials=workflow_input.workload_credentials,
    )
    storage = storage_for(state.workspace_root, workflow_input.job_id)
    try:
        resolved = await workflow._activity("container_job.resolve_workspace", request)
        request.resolved_workspace_ref = resolved.resolved_workspace_ref
        request.resolved_image_ref = IMAGE
        created = await workflow._activity("container_job.create_container", request)
        request.container_ref = created.container_ref
        state.delivered = {
            path.name: path.read_text()
            for path in storage.workload_credentials.rglob("*")
            if path.is_file()
        }
        for name, body in (outputs or {}).items():
            (storage.outputs / name).write_text(body)
        observed = await workflow._activity("container_job.observe_container", request)
        request.terminal_state = observed.terminal_state
        request.exit_code = observed.exit_code
        await workflow._activity("container_job.publish_evidence", request)
    finally:
        container_job.workflow.execute_activity = original
    return request, commands, published, storage


def assert_no_seed(blob) -> None:
    text = blob if isinstance(blob, str) else blob.decode(errors="replace")
    for seed in SEEDS:
        assert seed not in text


async def test_authorized_references_reach_the_fixtures_and_values_stay_out(
    journey, sts
):
    response = await journey.submit(profile_spec(CLOUD, APP))
    assert response.status_code == 200, response.text
    assert_no_seed(response.text)
    workflow_input = journey.temporal.start_container_job.await_args.args[0]
    decisions = workflow_input.workload_credentials
    owner = workflow_input.owner
    assert [d.slot for d in decisions] == ["cloud", "app"]
    assert all(
        d.job_id == workflow_input.job_id
        and d.principal == f"{owner.principal_type}:{owner.principal_id}"
        and d.assessment_ref == "assessment-1"
        for d in decisions
    )
    async with journey.sessions() as session:
        record = await session.scalar(select(ContainerJobRecord))
        assert_no_seed(json.dumps(record.request_json))

    leaked = f"{APP_VALUE} {ISSUED[2]}"
    request, commands, published, storage = await run_worker(
        journey,
        workflow_input,
        outputs={"report.json": json.dumps({"accounts": 1, "echo": leaked})},
        logs=leaked.encode(),
    )

    # The STS fixture was reached with the stored source key, not the
    # worker's ambient credentials, and asked for a read-only session.
    [call] = sts.calls
    assert SOURCE["accessKeyId"] in call["authorization"]
    assert "AKIAAMBIENTWORKER" not in call["authorization"]
    assert call["form"]["RoleArn"] == [ROLE]
    assert call["form"]["PolicyArns.member.1.arn"] == [
        "arn:aws:iam::aws:policy/SecurityAudit"
    ]
    assert journey.resolved == [("db://aws-audit", 3), ("db://app-login", None)]

    # The container fixture receives the issued session and the login.
    created = next(c for c in commands if c[0] == "create")
    assert_no_seed(" ".join(created))
    assert f"aws_session_token = {ISSUED[2]}" in journey.delivered["cloud"]
    assert journey.delivered["env"] == f"APP_LOGIN={APP_VALUE}\n"
    assert SOURCE["secretAccessKey"] not in "".join(journey.delivered.values())

    for payload in (*journey.history, *published.values()):
        assert_no_seed(payload)
    report = json.loads(published[f"{workflow_input.job_id}-output-report.json"])
    assert report["accounts"] == 1

    status = await journey.call("container.status", {"jobId": workflow_input.job_id})
    assert_no_seed(status.text)
    assert not (storage.workload_credentials / "files").exists()


@pytest.mark.parametrize(
    "credential",
    [
        {**CLOUD, "credentialRef": "db://not-admitted"},
        {**CLOUD, "account": "210987654321"},
        {**APP, "credentialRef": "db://aws-audit"},
        {**CLOUD, "credentialRef": "db://app-login"},
    ],
)
async def test_denied_reference_never_reaches_a_job_or_the_fixture(
    journey, sts, credential
):
    response = await journey.submit(profile_spec(credential))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "assessment_scope_denied"
    assert await journey.jobs() == 0
    journey.temporal.start_container_job.assert_not_called()
    assert sts.calls == []


async def test_issuer_denial_keeps_its_code_without_provider_text(journey, sts):
    sts.deny = True
    assert (await journey.submit(profile_spec(CLOUD))).status_code == 200
    workflow_input = journey.temporal.start_container_job.await_args.args[0]
    with pytest.raises(
        Exception, match="credential_issuance_failed.*AccessDenied"
    ) as failed:
        await run_worker(journey, workflow_input)
    assert_no_seed(str(failed.value))
    assert len(sts.calls) == 1
    for payload in journey.history:
        assert_no_seed(payload)


async def test_rotation_after_admission_fails_closed(journey, sts):
    assert (await journey.submit(profile_spec(CLOUD))).status_code == 200
    workflow_input = journey.temporal.start_container_job.await_args.args[0]
    journey.store["db://aws-audit"] = (4, json.dumps(SOURCE))
    with pytest.raises(Exception, match="credential_unresolved"):
        await run_worker(journey, workflow_input)
    assert sts.calls == []


async def test_generic_secret_reference_has_no_slot_to_authorize_it(journey):
    generic = await journey.submit(
        {
            "image": "alpine",
            "environment": [{"name": "API_TOKEN", "secretRef": "db://app-login"}],
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        }
    )
    assert generic.status_code == 403
    assert await journey.jobs() == 0
