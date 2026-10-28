"""Assessment journeys through the operator's admitted interface (#5034).

Each typed job starts as an admission file given to the ``moonmind container``
CLI inside a managed session. The CLI's exact MCP request is replayed against
the production ``/mcp/container/tools/call`` route under the session's signed
capability, so CLI validation, the submission envelope, capability scope
enforcement and Container Job admission all run. The worker launches the
persisted (capability-stamped) spec, a deterministic stand-in plays the scanner
at its binary boundary, and the workflow's publish -> remove -> cleanup order
releases job storage. Native report bytes must stay readable through the
artifact owner afterwards. Web and cloud jobs use an assessment-scoped
capability; repository and image jobs are not assessment-scope job kinds, so
they use the session's unscoped capability.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from api_service.api.routers import mcp_tools
from api_service.services.container_jobs import (
    ContainerJobService,
    owner_artifact_principal,
)
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from moonmind.cli import app as cli_app
from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.container_job_cli import ContainerJobCliError, ContainerJobMcpClient
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    OwnerIdentity,
)
from moonmind.schemas.repository_scanner_models import ImageAssessment
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from moonmind.security.image_assessment_runner import run_image_assessment
from moonmind.security.repository_scanner_runner import run_scanner
from moonmind.security.repository_scanners import RepositoryScan
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.integration.security import test_cloud_assessment_jobs as cloud
from tests.integration.security import test_image_assessment_jobs as image
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security import test_repository_scanner_jobs as repo
from tests.integration.security import test_web_assessment_jobs as web

environment = recurring.environment
target = web.target
provider = cloud.provider

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OWNER = OwnerIdentity(principalType="user", principalId="operator")
SECRET = "admitted-journey-secret"
IMAGE_REF = "sha256:" + "a" * 64
NO_GRANTS = PrivateImageAuthorizationService(
    PrivateImageAuthorizationPolicy.model_validate({"grants": []})
)


class Session:
    """A managed session's CLI environment and the production MCP route."""

    def __init__(self, environment, monkeypatch, *, scope=None, workspace_id="assess"):
        self.environment, self.monkeypatch = environment, monkeypatch
        token = mint_container_job_session_capability(
            secret=SECRET,
            owner=OWNER,
            agent_run_id="run",
            workflow_id="assessment",
            session_id="session",
            runtime_id="runtime",
            step_id="assess",
            source_kind="omnigent",
            workspace_kind="sandbox",
            workspace_id=workspace_id,
            assessment_scope=scope,
            lifetime_seconds=3600,
        )
        for name in (
            "MOONMIND_CONTAINER_JOBS_MCP_URL",
            "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE",
        ):
            monkeypatch.delenv(name, raising=False)
        for name, value in {
            "MOONMIND_URL": "http://api:8000",
            "MOONMIND_AGENT_RUN_ID": "run",
            "MOONMIND_TASK_WORKFLOW_ID": "assessment",
            "MOONMIND_STEP_ID": "assess",
            "MOONMIND_CONTAINER_JOBS_SESSION_ID": "session",
            "MOONMIND_CONTAINER_JOBS_SOURCE_KIND": "omnigent",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND": "sandbox",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID": workspace_id,
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH": "repo",
            "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": token,
        }.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(mcp_tools.settings.security, "JWT_SECRET_KEY", SECRET)
        monkeypatch.setattr(mcp_tools, "container_jobs_ready", lambda: True)
        monkeypatch.setattr(
            mcp_tools, "get_temporal_artifact_service", lambda _s: environment.service
        )
        # The route builds its own service; the deployment's backend
        # settings, image authorizer and Temporal client are injected here.
        self.service = {}
        monkeypatch.setattr(
            mcp_tools,
            "ContainerJobService",
            lambda session, **kw: ContainerJobService(
                session, **{**kw, **self.service}
            ),
        )
        self.app = FastAPI()
        self.app.include_router(mcp_tools.router)

        async def session_dependency():
            yield environment.session

        self.app.dependency_overrides[mcp_tools.get_async_session] = session_dependency

    async def submit(self, tmp_path, command, admission, *, temporal, **service):
        """Run the CLI command, then send its exact request to the API route."""
        self.service = {"temporal": temporal, **service}
        sent = []

        def call(client, tool, arguments):
            if tool == "container.submit":
                sent.append(
                    (client._endpoint, client._authorization_headers(), arguments)
                )
                return {"jobId": "pending-replay"}
            if tool == "container.status":
                return {"state": "succeeded"}
            raise ContainerJobCliError("auxiliary reads are not replayed")

        self.monkeypatch.setattr(ContainerJobMcpClient, "call", call)
        path = tmp_path / f"{command}.json"
        path.write_text(json.dumps(admission))
        result = CliRunner().invoke(
            cli_app, ["container", command, "--admission", str(path)]
        )
        assert result.exit_code == 0, result.output
        ((endpoint, headers, arguments),) = sent
        async with AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://api:8000"
        ) as client:
            response = await client.post(
                urlsplit(endpoint).path,
                headers=headers,
                json={"tool": "container.submit", "arguments": arguments},
            )
        assert response.status_code == 200, response.text
        job_id = response.json()["result"]["jobId"]
        started = temporal.start_container_job.call_args.args[0]
        assert started.job_id == job_id and started.owner == OWNER
        # The worker launches what admission persisted, not the CLI's copy.
        assert started.request.source.source == "omnigent"
        return started


def spy_publication(monkeypatch):
    """Keep the worker objects the harness used so cleanup can follow."""
    seen = {}
    original = DockerContainerJobBackend.publish_evidence

    async def publish_evidence(backend, request):
        seen["backend"], seen["request"] = backend, request
        return await original(backend, request)

    monkeypatch.setattr(DockerContainerJobBackend, "publish_evidence", publish_evidence)
    return seen


async def finish(backend, request, tmp_path):
    """The workflow's order after publication: remove, then clean up."""
    request.publication = AuxiliaryOutcome(state="succeeded")
    daemon = backend._runner

    async def removed(argv):
        # After removal the daemon knows neither the job's container nor its
        # egress networks; the harness daemons answer only launch commands.
        if tuple(argv[:2]) == ("network", "inspect") or argv[0] == "inspect":
            return 1, b"", b"No such object"
        return await daemon(argv)

    backend._runner = removed
    await backend.remove_container(request)
    cleaned = await backend.cleanup(request)
    assert cleaned.cleanup_succeeded is not False
    assert not storage_for(tmp_path, request.job_id).root.exists()


async def assert_retained(environment, refs, published):
    """Native bytes outlive job storage and stay readable by their owner."""
    assert refs
    for ref in refs:
        _, body = await environment.service.read(
            artifact_id=ref,
            principal=owner_artifact_principal(OWNER),
            allow_restricted_raw=True,
        )
        assert body == published[ref]
        assert json.loads(body)


def publisher(environment, published):
    async def publish(request, name, payload):
        principal = owner_artifact_principal(request.owner)
        artifact, _ = await environment.service.create(
            principal=principal,
            content_type="application/json",
            link={
                "namespace": "default",
                "workflow_id": "assessment",
                "run_id": "run",
                "link_type": "output.container-job",
            },
        )
        await environment.service.write_complete(
            artifact_id=artifact.artifact_id, principal=principal, payload=payload
        )
        published[artifact.artifact_id] = payload
        return artifact.artifact_id

    return publish


async def test_repository_scan_cli_job_keeps_native_reports_after_cleanup(
    environment, tmp_path, monkeypatch
):
    root = tmp_path / "repository"
    repo.repository(root)
    repo.install_fixture_process(tmp_path, "gitleaks", monkeypatch)
    session = Session(environment, monkeypatch)
    temporal = AsyncMock()
    settings = resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [{"sourceRef": "repository-security", "image": "ghcr.io/x/scan:1"}]
            )
        }
    )
    admission = {"scanner": "gitleaks", "outputDirectory": "artifacts/gitleaks-run"}
    started = await session.submit(
        tmp_path,
        "repository-scan",
        admission,
        temporal=temporal,
        backend_settings=settings,
        authorizer=NO_GRANTS,
    )
    spec = started.request.spec
    assert spec.repository_scan is not None and spec.workspace_read_only

    published = {}

    async def daemon(argv):
        if argv[0] == "inspect":
            return 1, b"", b"no such container"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=daemon,
        evidence_publisher=publisher(environment, published),
        log_spool_root=tmp_path / "live",
        settings=settings,
    )
    request = ContainerJobActivityRequest(
        jobId=started.job_id,
        owner=started.owner,
        ownershipToken=started.ownership_token,
        request=started.request,
        resolvedWorkspaceRef=str(root),
        resolvedImageRef=IMAGE_REF,
    )
    await backend.create_container(request)
    storage = storage_for(tmp_path, started.job_id)
    scan = RepositoryScan.model_validate(admission)
    code = run_scanner(
        scan,
        source=storage.inputs / "source",
        output=storage.outputs / scan.output_directory,
        database=tmp_path / "database",
        rules_root=tmp_path / "rules",
    )
    request.container_ref = "fixture-container"
    request.terminal_state = "succeeded" if code == 0 else "failed"
    request.exit_code = code
    await backend.publish_evidence(request)
    summary = next(
        json.loads(body)
        for body in published.values()
        if b'"repository-scanner/v1"' in body
    )
    assert set(summary["nativeRefs"]) == {"history", "worktree"}
    assert any(row["findings"] for row in summary["results"].values())

    await finish(backend, request, tmp_path)
    await assert_retained(environment, summary["nativeRefs"].values(), published)
    assert all(repo.MARKER.encode() not in body for body in published.values())


async def test_image_assessment_cli_job_keeps_native_report_after_cleanup(
    environment, tmp_path, monkeypatch
):
    image.install_trivy(tmp_path, monkeypatch)
    db = tmp_path / "feeds"
    digest = image.database(db, {"pkg:apk/openssl@3.0.1": ["CVE-2024-0001"]}, "one")
    archive = await image.owned_artifact(
        environment.service, image.image_archive(image.VULNERABLE)
    )
    assessment = ImageAssessment(
        operation="assess",
        target="archive",
        inputArtifactRef=archive,
        databaseCacheRef="scanner-db",
        databaseDigest=digest,
    )
    session = Session(environment, monkeypatch)
    temporal = AsyncMock()
    settings = image.settings()
    started = await session.submit(
        tmp_path,
        "image-assessment",
        assessment.model_dump(mode="json", by_alias=True, exclude_none=True),
        temporal=temporal,
        backend_settings=settings,
        authorizer=image.AUTHORIZER,
    )
    assert started.request.spec.image_assessment is not None

    published = {}

    async def daemon(argv):
        if argv[0] == "inspect":
            return 1, b"", b"no such container"
        return 0, b"", b""

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=daemon,
        evidence_publisher=publisher(environment, published),
        artifact_reader=_container_job_artifact_reader(environment.service),
        log_spool_root=tmp_path / "live",
        settings=settings,
    )
    workspace = tmp_path / "temporal_sandbox" / "assess" / "repo"
    workspace.mkdir(parents=True)
    request = ContainerJobActivityRequest(
        jobId=started.job_id,
        owner=started.owner,
        ownershipToken=started.ownership_token,
        request=started.request,
        registryAuthorization=started.registry_authorization,
        targetAuthorization=started.target_authorization,
        resolvedWorkspaceRef=str(workspace),
        resolvedImageRef=IMAGE_REF,
    )
    await backend.create_container(request)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    storage = storage_for(tmp_path, started.job_id)
    code = run_image_assessment(
        assessment,
        inputs=storage.inputs,
        output=storage.outputs / assessment.output_directory,
        database=db,
        auth=storage.credentials,
    )
    request.container_ref = "fixture-container"
    request.terminal_state = "succeeded" if code == 0 else "failed"
    request.exit_code = code
    await backend.publish_evidence(request)
    summary = next(
        json.loads(body)
        for body in published.values()
        if b'"image-assessment/v1"' in body
    )
    assert summary["coverage"] == "complete", summary["limitations"]
    assert [f["ruleId"] for f in summary["result"]["findings"]] == ["CVE-2024-0001"]

    await finish(backend, request, tmp_path)
    await assert_retained(environment, [summary["nativeRef"]], published)


class CliCloud(cloud.Harness):
    def __init__(self, session, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session

    async def submit(self, assessment, key, jobs=None):
        started = await self.session.submit(
            self.tmp_path,
            "cloud-assessment",
            assessment.model_dump(mode="json", by_alias=True, exclude_none=True),
            temporal=self.temporal,
            backend_settings=self.settings,
            authorizer=NO_GRANTS,
        )
        return started.request, SimpleNamespace(job_id=started.job_id)


async def test_cloud_assessment_cli_job_keeps_native_report_after_cleanup(
    environment, tmp_path, monkeypatch, provider
):
    session = Session(environment, monkeypatch, scope=cloud.scope())
    seen = spy_publication(monkeypatch)
    harness = CliCloud(session, environment, tmp_path, monkeypatch, provider)
    summary, _, _, accepted = await harness.run(
        cloud.assessment(), "findings", scenario="findings"
    )
    assert (summary["outcome"], summary["status"]) == ("findings", "complete")
    harness.assert_no_seed()
    native = cloud.artifact_names(harness, accepted)["report"]["artifactRef"]
    assert native == summary["nativeRef"]
    retained = {native: harness.published[f"{accepted.job_id}-output-report.json"]}

    await finish(seen["backend"], seen["request"], tmp_path)
    await assert_retained(environment, [native], retained)


class CliWeb(web.Harness):
    def __init__(self, session, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session

    async def submit(self, assessment, key):
        started = await self.session.submit(
            self.tmp_path,
            "web-assessment",
            assessment.model_dump(mode="json", by_alias=True, exclude_none=True),
            temporal=self.temporal,
            backend_settings=self.settings,
            authorizer=NO_GRANTS,
        )
        return started.request, SimpleNamespace(job_id=started.job_id)


async def test_web_assessment_cli_job_keeps_native_report_after_cleanup(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    session = Session(environment, monkeypatch, scope=web.scope(app))
    harness = CliWeb(session, environment, tmp_path, monkeypatch, app, gateway)
    summary, _, _, accepted, resolved = await harness.run(
        web.assessment(app, identity=web.identity("alice")), "alice"
    )
    assert resolved == ["secret://web/alice"]
    assert summary["coverage"]["authentication"]["status"] == "verified"
    assert summary["findings"]
    harness.assert_no_secret()
    native = web.artifact_names(harness, accepted)["report"]["artifactRef"]
    assert native == summary["nativeRef"]
    retained = {native: harness.published[f"{accepted.job_id}-output-report.json"]}

    await finish(harness.backend, harness.request, tmp_path)
    await assert_retained(environment, [native], retained)
    harness.assert_no_secret()
