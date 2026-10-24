"""Integration checks for artifact authorization boundaries.

Phase 6: verify that the artifact service enforces ownership-based access control
at the workflow boundary, including the restricted-artifact raw-content boundary.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.auth_providers import instance_operator_principal
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalArtifactRedactionLevel,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    S3TemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize(("fault", "status_code", "code"), [
    (TimeoutError("secret-signed-url-must-not-leak"), 503, "artifact_storage_unavailable"),
    (ClientError({"Error": {"Code": "AccessDenied", "Message": "private-fixture"}}, "GetObject"), 502, "artifact_storage_forbidden"),
    (ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"), 409, "artifact_bytes_missing"),
    (OperationalError("SELECT links", {}, RuntimeError("private-fixture")), 503, "artifact_authority_unavailable"),
])
async def test_real_download_boundary_maps_lazy_s3_failures(tmp_path, monkeypatch, fault, status_code, code):
    from api_service.api.routers import temporal_artifacts as routes
    from moonmind.workflows.temporal import artifacts as module

    class Client:
        def head_bucket(self, **kwargs):
            pass
        def get_object(self, **kwargs):
            raise fault

    monkeypatch.setattr(module.boto3, "client", lambda **kwargs: Client())
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session), store=LocalTemporalArtifactStore(tmp_path / "artifacts"))
            artifact, _ = await service.create(principal="owner", content_type="text/plain", link=ExecutionRef(namespace="default", workflow_id="reader", run_id="run", link_type="input.test"))
            await service.write_complete(artifact_id=artifact.artifact_id, principal="owner", payload=b"retained")
            if isinstance(fault, OperationalError):
                async def unavailable(*args, **kwargs):
                    raise fault
                monkeypatch.setattr(service._repository, "list_links", unavailable)
            else:
                service._store = S3TemporalArtifactStore(endpoint_url="http://synthetic.invalid", bucket="fixture", access_key_id="fixture", secret_access_key="fixture", region_name="us-east-1", use_ssl=False)
            app = FastAPI()
            app.include_router(routes.router)
            app.dependency_overrides[routes._get_temporal_artifact_service] = lambda: service
            app.dependency_overrides[routes._resolve_principal] = lambda: "workflow:reader"
            app.dependency_overrides[routes._resolve_recovery_principal] = lambda: None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                response = await client.get(f"/api/artifacts/{artifact.artifact_id}/download")
            assert response.status_code == status_code
            assert response.json()["detail"]["code"] == code
            assert "secret-signed-url" not in response.text
            assert "private-fixture" not in response.text
            assert artifact.status.value == "complete"


@workflow.defn
class ArtifactFailureJourney:
    @workflow.run
    async def run(self, request: dict) -> bytes:
        from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
        from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
        route = build_default_activity_catalog().resolve_activity("artifact.read")
        kwargs = MoonMindRunWorkflow()._execute_kwargs_for_route(route)
        kwargs["task_queue"] = workflow.info().task_queue
        return await workflow.execute_activity("artifact.read", request, **kwargs)


@pytest.mark.parametrize(("fault_kind", "expected_attempts"), [("transient", 2), ("persistent", 3), ("denied", 1), ("missing", 1)])
async def test_temporal_read_retries_only_transient_failures(tmp_path, monkeypatch, fault_kind, expected_attempts):
    from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
    from moonmind.workflows.temporal.activity_catalog import ARTIFACTS_FLEET, build_default_activity_catalog
    from moonmind.workflows.temporal.activity_runtime import build_activity_bindings

    async with _db(tmp_path) as maker:
        async with maker() as session:
            store = LocalTemporalArtifactStore(tmp_path / "artifacts")
            service = TemporalArtifactService(TemporalArtifactRepository(session), store=store)
            artifact, _ = await service.create(principal="owner", content_type="text/plain")
            await service.write_complete(artifact_id=artifact.artifact_id, principal="owner", payload=b"retained")
            read = store.read_bytes
            attempts = []

            def injected(key):
                attempts.append(key)
                if (fault_kind == "transient" and len(attempts) == 1) or fault_kind == "persistent":
                    raise TimeoutError("synthetic storage outage")
                if fault_kind == "denied":
                    raise PermissionError("synthetic rejected storage credentials")
                if fault_kind == "missing":
                    raise FileNotFoundError("synthetic missing object")
                return read(key)

            monkeypatch.setattr(store, "read_bytes", injected)
            handler = next(binding.handler for binding in build_activity_bindings(
                build_default_activity_catalog(), artifact_activities=TemporalArtifactActivities(service),
                agent_skills_activities=AgentSkillsActivities(), fleets=(ARTIFACTS_FLEET,),
            ) if binding.activity_type == "artifact.read")
            queue = f"artifact-failure-{uuid4().hex}"
            async with await WorkflowEnvironment.start_time_skipping() as env:
                async with Worker(env.client, task_queue=queue, workflows=[ArtifactFailureJourney], activities=[handler], workflow_runner=UnsandboxedWorkflowRunner()):
                    result = env.client.execute_workflow(ArtifactFailureJourney.run,
                        {"artifact_ref": artifact.artifact_id, "principal": "owner"}, id=queue, task_queue=queue,
                        execution_timeout=timedelta(minutes=1))
                    if fault_kind == "transient":
                        assert await result == b"retained"
                    else:
                        with pytest.raises(WorkflowFailureError) as raised:
                            await result
                        error = raised.value.cause.cause
                        assert isinstance(error, ApplicationError)
                        assert error.type == {"persistent": "artifact_storage_unavailable", "denied": "artifact_storage_forbidden", "missing": "artifact_bytes_missing"}[fault_kind]
            assert len(attempts) == expected_attempts
            assert set(attempts) == {artifact.storage_key}
            assert artifact.status.value == "complete"

@asynccontextmanager
async def _db(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path}/temporal_artifact_authz.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()

class TestArtifactAuthorizationBoundaries:
    """Phase 6: authorization boundary tests for the artifact service."""

    async def test_non_owner_cannot_read_restricted_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When OIDC is enabled, a non-owner principal must be denied read access."""
        monkeypatch.setenv("AUTH_PROVIDER", "oidc")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="owner@example.com",
                    content_type="text/plain",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                    payload=b"secret-content",
                    content_type="text/plain",
                )

                # Non-owner read must fail
                with pytest.raises(TemporalArtifactAuthorizationError, match="cannot read"):
                    await service.read(
                        artifact_id=artifact.artifact_id,
                        principal="attacker@example.com",
                    )

                # Owner read must succeed
                _artifact, payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                )
                assert payload == b"secret-content"

    async def test_service_principal_can_read_any_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Post-#4017: bare service principals cannot bypass ownership.

        A bare ``service:`` principal without an admitted scope is denied
        even for reads; carrying the owner's admitted scope succeeds.
        """
        monkeypatch.setenv("AUTH_PROVIDER", "oidc")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="owner@example.com",
                    content_type="text/plain",
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                    payload=b"owner-data",
                    content_type="text/plain",
                )

                with pytest.raises(
                    TemporalArtifactAuthorizationError, match="cannot read"
                ):
                    await service.read(
                        artifact_id=artifact.artifact_id,
                        principal="service:lifecycle",
                    )

                _artifact, payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="service:lifecycle",
                    admitted_principal="owner@example.com",
                )
                assert payload == b"owner-data"

    async def test_non_owner_cannot_mutate_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When OIDC is enabled, a non-owner principal must be denied mutation access."""
        monkeypatch.setenv("AUTH_PROVIDER", "oidc")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="owner@example.com",
                    content_type="text/plain",
                )

                # Non-owner write_complete must fail
                with pytest.raises(
                    TemporalArtifactAuthorizationError, match="cannot mutate"
                ):
                    await service.write_complete(
                        artifact_id=artifact.artifact_id,
                        principal="attacker@example.com",
                        payload=b"tampered",
                        content_type="text/plain",
                    )

                # Owner write must succeed
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                    payload=b"owner-content",
                    content_type="text/plain",
                )
                _artifact, payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal="owner@example.com",
                )
                assert payload == b"owner-content"

    @pytest.mark.parametrize("retired_auth_provider", ["disabled", "oidc"])
    async def test_retired_auth_selector_preserves_artifact_scope(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        retired_auth_provider: str,
    ) -> None:
        """Retired selectors grant no access; the admitted operator retains control."""
        monkeypatch.setenv("AUTH_PROVIDER", retired_auth_provider)
        operator = instance_operator_principal()
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                artifact, _upload = await service.create(
                    principal="workflow:owner",
                    content_type="text/plain",
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="workflow:owner",
                    payload=b"content",
                    content_type="text/plain",
                )

                with pytest.raises(
                    TemporalArtifactAuthorizationError, match="cannot read"
                ):
                    await service.read(
                        artifact_id=artifact.artifact_id,
                        principal="anyone",
                    )

                _artifact, payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal=operator,
                )
                assert payload == b"content"

                # Use an incomplete artifact so mutation authorization is exercised.
                artifact2, _upload2 = await service.create(
                    principal="workflow:owner",
                    content_type="text/plain",
                )
                with pytest.raises(
                    TemporalArtifactAuthorizationError, match="cannot mutate"
                ):
                    await service.write_complete(
                        artifact_id=artifact2.artifact_id,
                        principal="anyone",
                        payload=b"tampered",
                        content_type="text/plain",
                    )
                await service.write_complete(
                    artifact_id=artifact2.artifact_id,
                    principal=operator,
                    payload=b"updated",
                    content_type="text/plain",
                )
                _artifact2, payload2 = await service.read(
                    artifact_id=artifact2.artifact_id,
                    principal=operator,
                )
                assert payload2 == b"updated"

    async def test_execution_owner_can_preview_and_download_linked_input_attachment(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MM-628: linked input attachment reads are authorized by execution owner."""

        monkeypatch.setenv("AUTH_PROVIDER", "oidc")
        async with _db(tmp_path) as maker:
            async with maker() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session),
                    store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
                )
                execution_owner = "user-execution-owner"
                artifact, _upload = await service.create(
                    principal="uploader@example.com",
                    content_type="application/octet-stream",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="uploader@example.com",
                    payload=b"content",
                    content_type="application/octet-stream",
                )
                session.add(
                    TemporalExecutionCanonicalRecord(
                        namespace="moonmind",
                        workflow_id="wf-mm-628-auth",
                        run_id="run-mm-628-auth",
                        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                        owner_id=execution_owner,
                        owner_type=TemporalExecutionOwnerType.USER,
                        state=MoonMindWorkflowState.EXECUTING,
                        entry="run",
                        search_attributes={},
                        memo={},
                        artifact_refs=[artifact.artifact_id],
                        parameters={},
                    )
                )
                await session.flush()
                await service.link_artifact(
                    artifact_id=artifact.artifact_id,
                    principal="service:execution-linker",
                    execution_ref=ExecutionRef(
                        namespace="moonmind",
                        workflow_id="wf-mm-628-auth",
                        run_id="run-mm-628-auth",
                        link_type="input.attachment",
                    ),
                )

                _metadata_artifact, _links, _pinned, policy = await service.get_metadata(
                    artifact_id=artifact.artifact_id,
                    principal=execution_owner,
                )
                _read_artifact, payload = await service.read(
                    artifact_id=artifact.artifact_id,
                    principal=execution_owner,
                )

                assert policy.raw_access_allowed is True
                assert payload == b"content"
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(
                        artifact_id=artifact.artifact_id,
                        principal="user-without-view",
                    )
