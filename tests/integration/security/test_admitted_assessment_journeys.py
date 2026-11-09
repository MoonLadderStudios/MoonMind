"""Assessment journeys through the operator's admitted interface (#5034).

Each typed job starts as an admission file given to the ``moonmind container``
CLI inside a managed session. The CLI's exact MCP request is replayed against
the production ``/mcp/container/tools/call`` route under the session's signed
capability, so CLI validation, the submission envelope, capability scope
enforcement and Container Job admission all run. The worker launches the
persisted (capability-stamped) spec, a deterministic stand-in plays the scanner
at its binary boundary, and the workflow's publish -> remove -> cleanup order
releases job storage. Native report bytes must stay readable through the
artifact owner afterwards, and a pinned ``security.assess`` report over the
published summary keeps them past their own expiry until it is released.
Web and cloud jobs use an assessment-scoped
capability; repository and image jobs are not assessment-scope job kinds, so
they use the session's unscoped capability.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from api_service.api.routers import mcp_tools, presets
from api_service.services.container_jobs import (
    ContainerJobService,
    owner_artifact_principal,
)
from api_service.services.presets.catalog import PresetCatalogService
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from api_service.services.single_user_conversion import run_guarded_upgrade
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
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactTransactionOwnership,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.integration.security import test_cloud_assessment_jobs as cloud
from tests.integration.security import test_image_assessment_jobs as image
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security import test_repository_scanner_jobs as repo
from tests.integration.security import test_web_assessment_jobs as web
from tests.integration.security.assessment_admission import (
    seed_legacy_assessment_admission,
)

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

    def __init__(
        self,
        environment,
        monkeypatch,
        *,
        scope=None,
        workspace_id="assess",
        job_spec=None,
    ):
        self.environment, self.monkeypatch = environment, monkeypatch
        self.scope = scope
        self.job_spec = job_spec
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
            "MOONMIND_CONTAINER_JOBS_MCP_URL": "http://api:8000/mcp/container",
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

        def artifact_service(session, *, transaction_ownership):
            assert transaction_ownership is ArtifactTransactionOwnership.CALLER
            return TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=environment.service._store,
                transaction_ownership=transaction_ownership,
            )

        monkeypatch.setattr(
            mcp_tools, "get_temporal_artifact_service", artifact_service
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
        if self.scope is None or self.scope.engagement is None:
            await seed_legacy_assessment_admission(self.environment, self.scope)
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

        path = tmp_path / f"{command}.json"
        if self.job_spec is None:
            self.monkeypatch.setattr(ContainerJobMcpClient, "call", call)
            path.write_text(json.dumps(admission))
            result = CliRunner().invoke(
                cli_app, ["container", command, "--admission", str(path)]
            )
            assert result.exit_code == 0, result.output
        else:
            # Exercise the image-owned dependency-free entrypoint, not the
            # application Typer CLI. Only HTTP transport/scanner binaries are
            # controlled; its actual signed request goes through the API below.
            module_path = (
                Path(__file__).resolve().parents[3]
                / "services/omnigent/scripts/moonmind-container-cli.py"
            )
            module_spec = importlib.util.spec_from_file_location(
                "shipped_container_cli", module_path
            )
            cli = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(cli)
            native_key = {
                "repository-scan": "repositoryScan",
                "image-assessment": "imageAssessment",
                "cloud-assessment": "cloudAssessment",
                "web-assessment": "webAssessment",
            }[command]
            from moonmind.security.assessment_preset_inputs import (
                ASSESSMENT_INPUT_CONTRACTS,
            )

            model = ASSESSMENT_INPUT_CONTRACTS[command]
            assert model.model_validate(
                self.job_spec[native_key]
            ) == model.model_validate(admission)
            path.write_text(json.dumps(self.job_spec))

            def transport(request, timeout):
                payload = json.loads(request.data)
                if payload["tool"] == "container.submit":
                    sent.append(
                        (
                            request.full_url,
                            dict(request.header_items()),
                            payload["arguments"],
                        )
                    )
                    result = {"jobId": "pending-replay"}
                elif payload["tool"] == "container.status":
                    result = {"state": "succeeded"}
                else:
                    assert payload["tool"] == "container.logs"
                    result = {"entries": []}
                return io.BytesIO(json.dumps({"result": result}).encode())

            with self.monkeypatch.context() as patch:
                patch.setattr(cli.urllib.request, "urlopen", transport)
                patch.setattr(
                    sys,
                    "argv",
                    [
                        "moonmind",
                        "container",
                        "run",
                        "--spec",
                        str(path),
                        "--request-id",
                        command,
                    ],
                )
                assert cli.main() == 0
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


async def finish(backend, request, tmp_path, *, retained_outputs=None):
    """Remove, then clean up; explicitly verify any sole unpublished copies."""
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
    storage = storage_for(tmp_path, request.job_id)
    if retained_outputs:
        assert cleaned.cleanup_succeeded is False
        for relative_path, original in retained_outputs.items():
            assert (storage.outputs / relative_path).read_bytes() == original
        for directory in (
            storage.inputs,
            storage.scratch,
            storage.staging,
            storage.credentials,
            storage.workload_credentials,
        ):
            assert not directory.exists()
    else:
        assert cleaned.cleanup_succeeded is not False
        assert not storage.root.exists()
    return cleaned


async def assert_retained(environment, refs, published):
    """Native bytes outlive job storage and stay readable by their owner."""
    assert refs
    for ref in refs:
        _, body = await environment.service.read(
            artifact_id=ref,
            access=ArtifactAccessContext.for_execution("assessment"),
        )
        assert body == published[ref]
        assert json.loads(body)


async def assert_assessment_retains(environment, summary_ref, native_refs, assets):
    """A pinned report over the summary keeps its native reports past expiry."""
    service = environment.service
    outputs = await recurring.invoke(
        service,
        {
            "scope": {"assets": assets},
            "tools": ["retained-report"],
            "reportRefs": [summary_ref],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
        },
        key="retained-native-reports",
    )
    assert not outputs["diagnostics"]
    report = recurring.report_ref(outputs)
    retained = {summary_ref, *native_refs}
    assert retained <= await recurring.required(service, report)
    await service.pin(artifact_id=report, principal="operator", reason="audit")
    await recurring.age_everything(environment)
    for ref in sorted(retained):
        with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
            await service.soft_delete(artifact_id=ref, principal="operator")
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in retained:
        assert await recurring.status(environment, ref) == "complete"

    await service.unpin(artifact_id=report, principal="operator")
    await recurring.age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in retained | {report}:
        assert await recurring.status(environment, ref) == "deleted"


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


@pytest.mark.parametrize("entrypoint", ["cli", "preset-api"])
async def test_repository_scan_cli_job_keeps_native_reports_after_cleanup(
    environment, tmp_path, monkeypatch, entrypoint
):
    from tests.integration.security.quality import QualityRun, observation

    quality = QualityRun()
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
    preset_provenance = None
    if entrypoint == "preset-api":
        # The shared Create form event test emits this exact payload. Feeding
        # it here connects authoring to native expansion/admission/retention.
        create_payload = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "fixtures/security_review/create-code-preset.json"
            ).read_text()
        )
        admission = create_payload["assessment"]
        await run_guarded_upgrade(environment.session, operator_authorized=True)
        seed_dir = tmp_path / "assessment-presets"
        seed_dir.mkdir()
        seed = (
            Path(__file__).resolve().parents[3]
            / "api_service/data/presets/security-code-supply-chain.yaml"
        )
        (seed_dir / seed.name).write_text(seed.read_text())
        await PresetCatalogService(environment.session).import_seed_templates(
            seed_dir=seed_dir
        )
        monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
        monkeypatch.setattr(mcp_tools.settings.feature_flags, "preset_catalog", True)
        monkeypatch.setattr(
            mcp_tools.settings.feature_flags, "disable_preset_catalog", False
        )
        session.app.include_router(presets.router)
        async with AsyncClient(
            transport=ASGITransport(app=session.app, client=("127.0.0.1", 12345)),
            base_url="http://localhost",
        ) as client:
            response = await client.post(
                "/api/presets/security-code-supply-chain:expand",
                json={"inputs": create_payload},
            )
        assert response.status_code == 200, response.text
        expanded = response.json()
        assert len(expanded["steps"]) == 1
        step = expanded["steps"][0]
        assert step["skill"]["id"] == "security-review"
        frozen = step["annotations"]["securityAssessment"]
        assert frozen["kind"] == "repository-scan"
        assert frozen["admission"] == step["skill"]["args"]["assessment"]
        assert all(
            frozen["admission"][key] == value for key, value in admission.items()
        )
        admission = frozen["admission"]
        session.job_spec = frozen["jobSpec"]
        assert admission["requireFreshFeeds"] is False
        assert "assessmentScope" not in expanded
        preset_provenance = expanded["appliedTemplate"]
        # The test fixture performs the portable Skill's CLI dispatch, not its
        # model reasoning. Native admission, capture, runner, collector and
        # security.assess below are the real production owners.
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
    assert spec.repository_scan == RepositoryScan.model_validate(admission)

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
    await web.create_container_in_activity(backend, request, environment, monkeypatch)
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
    quality.useful()

    await finish(backend, request, tmp_path)
    await assert_retained(environment, summary["nativeRefs"].values(), published)
    assert all(repo.MARKER.encode() not in body for body in published.values())
    summary_ref = next(
        ref for ref, body in published.items() if b'"repository-scanner/v1"' in body
    )
    outputs = await recurring.invoke(
        environment.service,
        repo.retained_assessment(
            summary_ref, ["removed.txt", "tracked.txt", "untracked.txt"]
        ),
        key="quality-source-report",
    )
    observed = observation(
        (
            "vulnerable"
            if any(row["findings"] for row in summary["results"].values())
            else "clean"
        ),
        [
            f"{finding['ruleId']}@{finding['asset'] if finding['asset'] in {'removed.txt', 'tracked.txt', 'untracked.txt'} else '[redacted]'}"
            for row in summary["results"].values()
            for finding in row["findings"]
        ],
        summary["nativeRefs"],
        native_ref=summary_ref,
    )
    await quality.finish(
        environment.service,
        "admitted-source-vulnerable",
        observed,
        [
            *published,
            outputs["scanRef"],
            outputs["comparisonRef"],
            recurring.report_ref(outputs),
        ],
        provenance={
            "tool": "gitleaks controlled binary transport",
            "execution": summary["execution"],
            "configuration": spec.repository_scan.model_dump(
                mode="json", by_alias=True
            ),
            "model": None,
            "preset": preset_provenance,
            "dispatch": "controlled fixture substitutes for portable Skill CLI dispatch",
            "jobId": started.job_id,
            "topology": f"{entrypoint} -> CLI -> signed MCP -> admission -> Git capture -> scanner runner -> worker publication -> cleanup -> retained artifact -> security.assess -> report",
        },
    )
    await assert_assessment_retains(
        environment,
        summary_ref,
        summary["nativeRefs"].values(),
        sorted(
            {f["asset"] for row in summary["results"].values() for f in row["findings"]}
        ),
    )


@pytest.mark.parametrize("entrypoint", ["cli", "preset-api"])
async def test_image_assessment_cli_job_keeps_native_report_after_cleanup(
    environment, tmp_path, monkeypatch, entrypoint
):
    image.install_trivy(tmp_path, monkeypatch)
    db = tmp_path / "feeds"
    digest = image.database(db, {"pkg:apk/openssl@3.0.1": ["CVE-2024-0001"]}, "one")
    archive = await recurring.put(
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
    if entrypoint == "preset-api":
        step = await expand_preset(
            environment, tmp_path, monkeypatch, "security-image-sbom", assessment
        )
        session.job_spec = step["annotations"]["securityAssessment"]["jobSpec"]
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
    await web.create_container_in_activity(backend, request, environment, monkeypatch)
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
    await assert_assessment_retains(
        environment,
        next(
            ref for ref, body in published.items() if b'"image-assessment/v1"' in body
        ),
        [summary["nativeRef"]],
        [summary["subject"]["asset"]],
    )


async def expand_preset(environment, tmp_path, monkeypatch, slug, assessment):
    """Expand the maintained seed through its production HTTP boundary."""
    await run_guarded_upgrade(environment.session, operator_authorized=True)
    seed_dir = tmp_path / "scoped-presets"
    seed_dir.mkdir()
    seed = (
        Path(__file__).resolve().parents[3]
        / "api_service/data/presets"
        / f"{slug}.yaml"
    )
    (seed_dir / seed.name).write_text(seed.read_text())
    await PresetCatalogService(environment.session).import_seed_templates(
        seed_dir=seed_dir
    )
    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(mcp_tools.settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(
        mcp_tools.settings.feature_flags, "disable_preset_catalog", False
    )
    app = FastAPI()
    app.include_router(presets.router)

    async def session_dependency():
        yield environment.session

    app.dependency_overrides[mcp_tools.get_async_session] = session_dependency
    native = assessment.model_dump(mode="json", by_alias=True, exclude_none=True)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://localhost",
    ) as client:
        response = await client.post(
            f"/api/presets/{slug}:expand",
            json={
                "inputs": {
                    "goal": "Assess only the controlled scoped fixture",
                    "assessment": native,
                }
            },
        )
    assert response.status_code == 200, response.text
    expanded = response.json()
    step = expanded["steps"][0]
    frozen = step["annotations"]["securityAssessment"]["admission"]
    assert frozen == step["skill"]["args"]["assessment"]
    assert type(assessment).model_validate(frozen) == assessment
    assert "assessmentScope" not in expanded
    assert (
        step["skill"]["args"]["job_spec"]
        == step["annotations"]["securityAssessment"]["jobSpec"]
    )
    return step


async def scoped_preset_plan(
    environment, tmp_path, monkeypatch, slug, assessment, scope
):
    """Fixture substitutes for Skill dispatch, never for operator/job authority."""
    from api_service.db.models import SettingsOverride, TemporalExecutionCanonicalRecord
    from api_service.services.assessment_engagements import (
        admit_assessment_parameters,
        compile_assessment_proposal,
    )
    from moonmind.security.assessment_authorizations import (
        SETTINGS_KEY,
        prepare_authorizations_update,
    )
    from moonmind.security.operator_admission import OperatorAdmission
    from moonmind.security.workload_profiles import AdmittedAssessmentScope

    step = await expand_preset(environment, tmp_path, monkeypatch, slug, assessment)
    frozen = step["annotations"]["securityAssessment"]["admission"]

    # New saved authority has explicit limits, using the existing native job's
    # resource ceiling; target/actions/exclusions/grants remain exactly selected.
    saved_scope = scope.model_dump(mode="json", by_alias=True)
    saved_scope.update(maxCpuMillis=2000, maxMemoryMiB=4096)
    if "web-assessment" in scope.profile_refs:
        saved_scope["maxRequestsPerSecond"] = assessment.max_requests_per_second
    now = datetime.now(UTC)
    document = prepare_authorizations_update(
        {
            "authorizations": [
                {
                    "ref": "controlled-engagement",
                    "name": "Controlled fixture engagement",
                    "authorityKind": "self_hosted",
                    "authoritySource": "Local controlled test fixture",
                    "validFrom": (now - timedelta(minutes=1)).isoformat(),
                    "validUntil": (now + timedelta(hours=1)).isoformat(),
                    "maxTotalJobSeconds": scope.max_jobs * scope.max_job_seconds,
                    "scope": saved_scope,
                }
            ]
        },
        {"authorizations": []},
    )
    environment.session.add(SettingsOverride(key=SETTINGS_KEY, value_json=document))
    await environment.session.commit()
    parameters = {
        "workflow": {
            "title": "Controlled preset assessment",
            "steps": [step],
        },
        "assessmentAuthorizationRef": "controlled-engagement",
    }
    operator = OperatorAdmission(via="loopback")
    access = ArtifactAccessContext.for_execution("assessment")
    proposal = await compile_assessment_proposal(
        parameters,
        "controlled-engagement",
        session=environment.session,
        user=operator,
        access=access,
    )
    assert {section["label"] for section in proposal["sections"]} >= {
        "Targets",
        "Actions",
        "Identities",
        "Workflow plan",
    }
    parameters["assessmentReview"] = proposal
    admitted = await admit_assessment_parameters(
        parameters, session=environment.session, user=operator, access=access
    )
    assert (
        admitted["engagement"]["authorizationGeneration"]
        == document["authorizations"][0]["generation"]
    )
    environment.session.add(
        TemporalExecutionCanonicalRecord(
            workflow_id="assessment",
            run_id="run",
            workflow_type="MoonMind.UserWorkflow",
            entry="run",
            parameters=parameters,
        )
    )
    await environment.session.commit()
    return (
        type(assessment).model_validate(frozen),
        AdmittedAssessmentScope.model_validate(admitted),
        step["annotations"]["securityAssessment"]["jobSpec"],
    )


async def assert_review_captured(environment, accepted):
    from api_service.db.models import ContainerJobRecord

    record = await environment.session.get(ContainerJobRecord, accepted.job_id)
    review = record.assessment_review_json
    # Credential-shaped fixture refs are deliberately sanitized by the current
    # immutable review owner; raw grants remain in canonical execution authority.
    assert review["state"] == "redacted"
    assert review["limitations"] == [
        "Credential-shaped text was redacted from the accepted review."
    ]
    assert review["review"]["authorizationRef"] == "controlled-engagement"
    from api_service.db.models import TemporalExecutionCanonicalRecord

    canonical = await environment.session.get(
        TemporalExecutionCanonicalRecord, "assessment"
    )
    original = canonical.parameters["assessmentReview"]
    assert (
        review["review"]["authorizationGeneration"]
        == original["authorizationGeneration"]
    )
    assert review["review"]["proposalDigest"] == original["proposalDigest"]
    assert len(review["review"]["sections"]) == len(original["sections"])
    assert canonical.parameters["assessmentScope"]["grants"]


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


@pytest.mark.parametrize("entrypoint", ["cli", "preset-api"])
async def test_cloud_assessment_cli_job_keeps_native_report_after_cleanup(
    environment, tmp_path, monkeypatch, provider, entrypoint
):
    selected, admitted = cloud.assessment(), cloud.scope()
    job_spec = None
    if entrypoint == "preset-api":
        selected, admitted, job_spec = await scoped_preset_plan(
            environment,
            tmp_path,
            monkeypatch,
            "security-aws-posture",
            selected,
            admitted,
        )
    session = Session(environment, monkeypatch, scope=admitted, job_spec=job_spec)
    seen = spy_publication(monkeypatch)
    harness = CliCloud(
        session, environment, tmp_path, monkeypatch, provider, admitted=admitted
    )
    summary, _, _, accepted = await harness.run(
        selected, "findings", scenario="findings"
    )
    if entrypoint == "preset-api":
        await assert_review_captured(environment, accepted)
    assert (summary["outcome"], summary["status"]) == ("findings", "complete")
    harness.assert_no_seed()
    native = cloud.artifact_names(harness, accepted)["report"]["artifactRef"]
    assert native == summary["nativeRef"]
    retained = {native: harness.published[f"{accepted.job_id}-output-report.json"]}

    await finish(seen["backend"], seen["request"], tmp_path)
    await assert_retained(environment, [native], retained)
    await assert_assessment_retains(
        environment,
        cloud.artifact_names(harness, accepted)["summary"]["artifactRef"],
        [native],
        cloud.posture_review(None)["scope"]["assets"],
    )


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


@pytest.mark.parametrize("entrypoint", ["cli", "preset-api"])
async def test_web_assessment_cli_job_keeps_native_report_after_cleanup(
    environment, tmp_path, monkeypatch, target, entrypoint
):
    app, gateway = target
    selected, admitted = (
        web.assessment(app, identity=web.identity("alice")),
        web.scope(app),
    )
    job_spec = None
    if entrypoint == "preset-api":
        selected, admitted, job_spec = await scoped_preset_plan(
            environment, tmp_path, monkeypatch, "security-web-api", selected, admitted
        )
    session = Session(environment, monkeypatch, scope=admitted, job_spec=job_spec)
    harness = CliWeb(
        session, environment, tmp_path, monkeypatch, app, gateway, admitted=admitted
    )
    summary, _, _, accepted, resolved = await harness.run(selected, "alice")
    if entrypoint == "preset-api":
        await assert_review_captured(environment, accepted)
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
    await assert_assessment_retains(
        environment,
        web.artifact_names(harness, accepted)["summary"]["artifactRef"],
        [native],
        [web.origin(app)],
    )


@pytest.mark.parametrize("change", ["missing", "changed"])
async def test_scoped_cli_does_not_replace_missing_or_changed_canonical_admission(
    environment, tmp_path, monkeypatch, change
):
    from api_service.db.models import TemporalExecutionCanonicalRecord

    original = cloud.scope()
    session = Session(environment, monkeypatch, scope=original)
    temporal = AsyncMock()
    admission = cloud.assessment().model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    await session.submit(
        tmp_path,
        "cloud-assessment",
        admission,
        temporal=temporal,
        backend_settings=cloud.settings(),
        authorizer=NO_GRANTS,
    )
    canonical = await environment.session.get(
        TemporalExecutionCanonicalRecord, "assessment"
    )
    assert canonical.parameters["assessmentScope"] == original.model_dump(
        mode="json", by_alias=True
    )
    if change == "missing":
        await environment.session.delete(canonical)
    else:
        canonical.parameters = {
            "assessmentScope": original.model_copy(update={"max_jobs": 1}).model_dump(
                mode="json", by_alias=True
            )
        }
    await environment.session.commit()
    temporal.reset_mock()
    with pytest.raises(AssertionError, match="assessment_scope_denied"):
        await session.submit(
            tmp_path,
            "cloud-assessment",
            admission,
            temporal=temporal,
            backend_settings=cloud.settings(),
            authorizer=NO_GRANTS,
        )
    temporal.start_container_job.assert_not_awaited()


async def test_curated_issue_cli_mcp_retains_native_request_pairs_after_cleanup(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    scope = web.scope(app, action_classes=("web-known-issue",))
    session = Session(environment, monkeypatch, scope=scope)
    harness = CliWeb(
        session, environment, tmp_path, monkeypatch, app, gateway, admitted=scope
    )
    assessment = web.assessment(
        app,
        mode="known-issues",
        templateIds=["nextjs-middleware-bypass"],
        startPaths=["/middleware"],
        routes=[{"path": "/middleware", "methods": ["GET"]}],
        discovery=[],
    )
    summary, _, _, accepted, resolved = await harness.run(assessment, "curated")
    assert resolved == []
    assert summary["coverage"]["templates"][0]["outcome"] == "match"
    names = web.artifact_names(harness, accepted)
    refs = [names[name]["artifactRef"] for name in ("report", "messages")]
    retained = dict(
        zip(
            refs,
            [
                harness.published[f"{accepted.job_id}-output-{file}"]
                for file in ("report.json", "messages.har")
            ],
        )
    )
    await finish(harness.backend, harness.request, tmp_path)
    await assert_retained(environment, refs, retained)
    await assert_assessment_retains(
        environment, names["summary"]["artifactRef"], refs, [web.origin(app)]
    )
