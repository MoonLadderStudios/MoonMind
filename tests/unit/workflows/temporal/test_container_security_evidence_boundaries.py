"""Actual scoped ContainerJob publication retains only admitted raw rights."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from temporalio.testing import ActivityEnvironment

from api_service.api.routers.container_jobs import _build_service, container_job_owner
from api_service.db.models import (
    TemporalArtifactRedactionLevel,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.assessment_authorizations import (
    AssessmentAuthorizationDenied,
    bind_assessment_job,
)
from api_service.services.container_jobs import (
    ContainerJobEvidenceUnavailableError,
    ContainerJobNotFoundError,
    ContainerJobService,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSubmitRequest,
    OwnerIdentity,
    container_job_workflow_id,
)
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal import worker_runtime
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.unit.security import test_cloud_assessment as cloud
from tests.unit.services import test_container_jobs as job_fixtures

session_factory = job_fixtures.session_factory

pytestmark = pytest.mark.asyncio


async def setup_job(session, tmp_path, *, key="first", canonical=True):
    operator = OperatorAdmission(via="container-evidence-test")
    owner = container_job_owner(operator)
    submit = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": key,
            "source": {"source": "workflow", "workflowId": "assessment"},
            "spec": {
                **cloud.build_cloud_assessment_job(cloud.admission()),
                **cloud.WORKSPACE,
            },
        }
    )
    service = TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=LocalTemporalArtifactStore(tmp_path / "bytes"),
    )
    jobs = ContainerJobService(session, operator=operator, artifacts=service)
    record, _ = await jobs.repository.create_or_replay(owner=owner, request=submit)
    bind_assessment_job(record, cloud.scope(), submit)
    if canonical:
        session.add(
            TemporalExecutionCanonicalRecord(
                namespace="default",
                workflow_id="assessment",
                run_id="source-run",
                workflow_type="MoonMind.UserWorkflow",
                entry="run",
                owner_id=owner.principal_id,
                owner_type="user",
                state="completed",
            )
        )
    await session.commit()
    request = ContainerJobActivityRequest(
        jobId=record.job_id,
        ownershipToken=f"{record.job_id}:v1",
        owner=owner,
        request=submit,
        terminalState="succeeded",
        exitCode=0,
    )
    activity = ActivityEnvironment()
    activity.info = replace(
        activity.info,
        namespace="default",
        workflow_id=container_job_workflow_id(record.job_id),
        workflow_run_id="job-run",
    )
    return SimpleNamespace(
        service=service,
        jobs=jobs,
        record=record,
        request=request,
        activity=activity,
        owner=owner,
        operator=operator,
    )


async def test_scoped_publisher_preserves_exact_raw_consumers_and_safe_preview(
    session_factory,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    async with session_factory() as session:
        job = await setup_job(session, tmp_path)
        publish = worker_runtime._container_job_evidence_publisher(job.service)
        raw = b'{"notes":"apiKey=synthetic-publisher-canary","resource":"aws:fixture"}'
        ref = await job.activity.run(publish, job.request, "native.json", raw)
        artifact, _, _, policy = await job.service.get_metadata(
            artifact_id=ref,
            access=access_for_test("workflow:assessment"),
        )
        assert artifact.redaction_level is TemporalArtifactRedactionLevel.RESTRICTED
        assert artifact.created_by_principal == f"user:{job.owner.principal_id}"
        assert artifact.sha256 == hashlib.sha256(raw).hexdigest()
        assert policy.raw_access_allowed
        for reader in (
            access_for_test("operator"),
            access_for_test("workflow:assessment"),
            access_for_test("workflow:" + container_job_workflow_id(job.record.job_id)),
        ):
            assert (await job.service.read(artifact_id=ref, access=reader))[1] == raw
        await job.service.link_artifact(
            artifact_id=ref,
            principal=f"user:{job.owner.principal_id}",
            execution_ref={
                "namespace": "default",
                "workflow_id": "metadata-reader",
                "run_id": "run",
                "link_type": "input.evidence",
            },
        )
        other = access_for_test("workflow:metadata-reader")
        with pytest.raises(TemporalArtifactAuthorizationError):
            await job.service.read(artifact_id=ref, access=other)
        _, safe = await job.service.read(
            artifact_id=policy.preview_artifact_ref.artifact_id,
            access=other,
        )
        assert b"synthetic-publisher-canary" not in safe and b"aws:fixture" in safe


@pytest.mark.parametrize("mutation", ["owner", "source", "activity", "scope_removed"])
async def test_scoped_publisher_rejects_authored_authority_changes(
    session_factory,
    tmp_path,
    monkeypatch,
    mutation,
):
    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    async with session_factory() as session:
        job = await setup_job(session, tmp_path)
        if mutation == "owner":
            job.request.owner = OwnerIdentity(
                principalType="service", principalId="foreign"
            )
        elif mutation == "source":
            job.request.request.source.workflow_id = "foreign"
        elif mutation == "activity":
            job.activity.info = replace(job.activity.info, workflow_id="foreign")
        else:
            from tests.unit.workflows.temporal.test_container_job_evidence import (
                _request,
            )

            job.request.request = _request(tmp_path).request
        with pytest.raises(AssessmentAuthorizationDenied):
            await job.activity.run(
                worker_runtime._container_job_evidence_publisher(job.service),
                job.request,
                "native.json",
                b'{"secret":"synthetic-denied-canary"}',
            )


async def test_terminal_publication_and_owner_api_survive_lost_canonical_and_revocation(
    session_factory,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    async with session_factory() as session:
        job = await setup_job(session, tmp_path, canonical=False)
        job.record.cancel_idempotency_key = "already-canceled"
        job.record.assessment_deadline_at = datetime.now(UTC) - timedelta(hours=1)
        # Old jobs can lack a reconstructible scope without losing saved effects.
        job.record.assessment_scope_json = None
        await session.commit()
        publish = worker_runtime._container_job_evidence_publisher(job.service)
        logs = await job.activity.run(
            publish, job.request, "logs.txt", b"Retained terminal evidence"
        )
        body = json.dumps(
            {
                "jobId": job.record.job_id,
                "artifacts": [],
                "publication": {"state": "succeeded"},
            }
        ).encode()
        manifest = await job.activity.run(publish, job.request, "manifest.json", body)
        job.record.logs_ref, job.record.artifacts_ref = logs, manifest
        await session.commit()
        # Use the same service builder and actual operator admission as HTTP.
        monkeypatch.setattr(
            "api_service.api.routers.container_jobs.get_temporal_artifact_service",
            lambda *a, **kw: job.service,
        )
        jobs = _build_service(session, job.operator)
        assert (await jobs.logs(owner=job.owner, job_id=job.record.job_id)).entries[
            0
        ].text == "Retained terminal evidence"
        assert not (
            await jobs.artifacts(owner=job.owner, job_id=job.record.job_id)
        ).artifacts
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from api_service.api.routers import container_jobs as http_router

        app = FastAPI()
        app.include_router(http_router.router)
        app.dependency_overrides[http_router.get_instance_access] = lambda: job.operator

        async def current_session():
            yield session

        app.dependency_overrides[http_router.get_async_session] = current_session
        monkeypatch.setattr(http_router, "container_jobs_ready", lambda: True)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for suffix in ("logs", "artifacts"):
                response = await client.get(
                    f"/api/v1/container-jobs/{job.record.job_id}/{suffix}"
                )
                assert response.status_code == 200, response.text
            assert (
                "Retained terminal evidence"
                in (
                    await client.get(f"/api/v1/container-jobs/{job.record.job_id}/logs")
                ).text
            )
        with pytest.raises(ContainerJobNotFoundError):
            await jobs.logs(
                owner=OwnerIdentity(principalType="service", principalId="foreign"),
                job_id=job.record.job_id,
            )
        # A different job's restricted pointer cannot acquire this job's grant.
        foreign = await setup_job(session, tmp_path, key="foreign", canonical=False)
        foreign_ref = await foreign.activity.run(
            publish, foreign.request, "other.txt", b"foreign private evidence"
        )
        assert (
            await job.service.read(
                artifact_id=foreign_ref, access=access_for_test("operator")
            )
        )[1] == b"foreign private evidence"
        job.record.logs_ref = foreign_ref
        await session.commit()
        with pytest.raises(ContainerJobEvidenceUnavailableError):
            await jobs.logs(owner=job.owner, job_id=job.record.job_id)
        job.record.logs_ref = logs
        for ref in (logs, manifest):
            links = await job.service._repository.list_links(ref)
            await session.delete(
                next(
                    link
                    for link in links
                    if link.link_type == "output.artifact.operator"
                )
            )
        await session.commit()
        # Exact producer provenance cannot replace revoked operator read rights.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for suffix in ("logs", "artifacts"):
                response = await client.get(
                    f"/api/v1/container-jobs/{job.record.job_id}/{suffix}"
                )
                assert response.status_code != 200
                assert "Retained terminal evidence" not in response.text
        # The separately admitted source consumer retains its own exact raw grant.
        assert (
            await job.service.read(
                artifact_id=logs, access=access_for_test("workflow:assessment")
            )
        )[1] == b"Retained terminal evidence"


async def test_actual_cloud_collection_sanitizes_before_retention_and_retry(
    session_factory,
    tmp_path,
    monkeypatch,
):
    from moonmind.workflows.temporal import container_job_attachments as attachments
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    async with session_factory() as session:
        job = await setup_job(session, tmp_path)
        backend = DockerContainerJobBackend(
            workspace_root=tmp_path,
            evidence_publisher=worker_runtime._container_job_evidence_publisher(
                job.service
            ),
        )
        storage = backend._attachment_storage(job.request)
        attachments.claim(storage, job.request.ownership_token)
        storage.outputs.mkdir()
        native = json.loads(cloud.fixture("basic.ocsf.json"))
        native[0]["unmapped"]["notes"] = (
            "apiKey=synthetic-cloud-canary person=synthetic-person@example.test "
            "synthetic-delivered-value"
        )
        native[0]["unmapped"]["apiKey"] = "synthetic-plain-cloud-canary"
        native[0]["unmapped"]["configRef"] = (
            "src/check.py:42 token=synthetic-ref-cloud-canary"
        )
        original = json.dumps(native).encode()
        for output in job.request.request.spec.outputs:
            path = storage.outputs / output.relative_path
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(original if output.name == "report" else b"")
        job.request.resolved_image_ref = cloud.IMAGE.resolved_ref
        first = await job.activity.run(
            backend._collect_declared_outputs,
            job.request,
            ("synthetic-delivered-value",),
        )
        repeated = await job.activity.run(
            backend._collect_declared_outputs,
            job.request,
            ("synthetic-delivered-value",),
        )
        assert [entry.artifact_ref for entry in repeated] == [
            entry.artifact_ref for entry in first
        ]
        report = next(entry for entry in first if entry.name == "report")
        artifact, body = await job.service.read(
            artifact_id=report.artifact_ref,
            access=access_for_test("workflow:assessment"),
        )
        assert b"synthetic-cloud-canary" not in body
        assert b"synthetic-plain-cloud-canary" not in body
        assert b"synthetic-ref-cloud-canary" not in body
        assert b"src/check.py:42" in body
        assert b"synthetic-delivered-value" not in body
        assert (
            b"synthetic-person@example.test" in body
        )  # native identity, not anonymization
        assert artifact.redaction_level is TemporalArtifactRedactionLevel.RESTRICTED
        assert report.sha256 == artifact.sha256 == hashlib.sha256(body).hexdigest()
        retained = json.loads(body)
        for before, after in zip(native, retained, strict=True):
            assert before["finding_info"]["uid"] == after["finding_info"]["uid"]
            assert before["resources"] == after["resources"]
        summary = next(entry for entry in first if entry.name == "summary")
        _, summary_bytes = await job.service.read(
            artifact_id=summary.artifact_ref,
            access=access_for_test("workflow:assessment"),
        )
        assert (
            "credential_shaped_text_redacted"
            in json.loads(summary_bytes)["limitations"]
        )
        metadata_reader = access_for_test(
            "workflow:metadata-reader"
        )._with_verified_inputs([artifact.artifact_id])
        _, _, _, policy = await job.service.get_metadata(
            artifact_id=artifact.artifact_id, access=metadata_reader
        )
        assert not policy.raw_access_allowed
        with pytest.raises(TemporalArtifactAuthorizationError):
            await job.service.read(
                artifact_id=artifact.artifact_id, access=metadata_reader
            )
        _, preview = await job.service.read(
            artifact_id=policy.default_read_ref.artifact_id, access=metadata_reader
        )
        assert b"synthetic-person@example.test" not in preview


async def test_unrelated_container_publication_keeps_ordinary_classification(
    session_factory,
    tmp_path,
    monkeypatch,
):
    from tests.unit.workflows.temporal.test_container_job_evidence import _request

    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    async with session_factory() as session:
        request = _request(tmp_path)
        jobs = ContainerJobService(session)
        record, _ = await jobs.repository.create_or_replay(
            owner=request.owner,
            request=request.request,
        )
        await session.commit()
        request.job_id = record.job_id
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "bytes"),
        )
        ref = await worker_runtime._container_job_evidence_publisher(service)(
            request,
            "result.txt",
            b"ordinary pytest output",
        )
        artifact, payload = await service.read(
            artifact_id=ref,
            access=access_for_test("system:container_job"),
        )
        assert artifact.redaction_level is TemporalArtifactRedactionLevel.NONE
        assert payload == b"ordinary pytest output"
        assert not artifact.metadata_json.get("preview_artifact_id")


async def test_malformed_cloud_native_is_withheld_with_safe_diagnostic(
    session_factory,
    tmp_path,
    monkeypatch,
):
    from moonmind.workflows.temporal import container_job_attachments as attachments
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    async with session_factory() as session:
        job = await setup_job(session, tmp_path)
        backend = DockerContainerJobBackend(
            workspace_root=tmp_path,
            evidence_publisher=worker_runtime._container_job_evidence_publisher(
                job.service
            ),
        )
        storage = backend._attachment_storage(job.request)
        attachments.claim(storage, job.request.ownership_token)
        storage.outputs.mkdir()
        report = next(
            item for item in job.request.request.spec.outputs if item.name == "report"
        )
        path = storage.outputs / report.relative_path
        path.parent.mkdir(parents=True)
        native = b'{"password":"synthetic-malformed-canary"'
        path.write_bytes(native)
        manifest = await job.activity.run(
            backend._collect_declared_outputs, job.request
        )
        rejected = next(item for item in manifest if item.name == "report")
        assert (
            rejected.artifact_ref is None and rejected.collection_status == "rejected"
        )
        assert "synthetic-malformed-canary" not in str(manifest)
        assert path.read_bytes() == native  # preserve the sole source for recovery
        summary = next(item for item in manifest if item.name == "summary")
        _, body = await job.service.read(
            artifact_id=summary.artifact_ref,
            access=access_for_test("workflow:assessment"),
        )
        assert b"report_sanitization_failed" in body
        assert b"synthetic-malformed-canary" not in body


@pytest.mark.parametrize("parent", ["system", "missing"])
async def test_native_container_outputs_use_generic_operator_download_after_parent_loss(
    session_factory,
    tmp_path,
    monkeypatch,
    parent,
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api_service.api.routers import temporal_artifacts as router

    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    async with session_factory() as session:
        job = await setup_job(session, tmp_path, canonical=parent != "missing")
        if parent == "system":
            source = await session.get(TemporalExecutionCanonicalRecord, "assessment")
            source.owner_type, source.owner_id = "system", None
            await session.commit()
        native = b'{"findingUid":"native-fixture","resource":"aws:fixture"}'
        ref = await job.activity.run(
            worker_runtime._container_job_evidence_publisher(job.service),
            job.request,
            "native.json",
            native,
        )
        app = FastAPI()
        app.include_router(router.router)
        app.dependency_overrides[router._get_temporal_artifact_service] = lambda: (
            job.service
        )
        app.dependency_overrides[router.get_artifact_access] = lambda: access_for_test(
            "operator"
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.get(f"/api/artifacts/{ref}?include_download=true")
            ).json()["raw_access_allowed"]
            response = await client.get(f"/api/artifacts/{ref}/download")
            assert response.status_code == 200 and response.content == native
            assert (
                await client.post(f"/api/artifacts/{ref}/presign-download")
            ).status_code == 200
        app.dependency_overrides[router.get_artifact_access] = lambda: access_for_test(
            "workflow:foreign"
        )._with_verified_inputs([ref])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.get(f"/api/artifacts/{ref}/download")
            ).status_code == 403


async def test_legacy_scoped_none_output_keeps_recorded_operator_access(
    session_factory, tmp_path
):
    async with session_factory() as session:
        job = await setup_job(session, tmp_path, canonical=False)
        artifact, _ = await job.service.create(
            principal=f"user:{job.owner.principal_id}", content_type="text/plain"
        )
        await job.service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=f"user:{job.owner.principal_id}",
            payload=b"legacy retained logs",
        )
        job.record.logs_ref = artifact.artifact_id
        await session.commit()
        assert (await job.jobs.logs(owner=job.owner, job_id=job.record.job_id)).entries[
            0
        ].text == "legacy retained logs"
        assert artifact.redaction_level is TemporalArtifactRedactionLevel.NONE
        assert not await job.service._repository.list_links(artifact.artifact_id)
