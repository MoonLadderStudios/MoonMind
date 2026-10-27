"""Admitted operator recovery through mounted workflow evidence consumers."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from api_service.api.routers import executions as ex
from api_service.db.base import get_async_session
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)
from api_service.services import single_user_conversion as conversion
from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository
from tests.artifact_access import access_for_test
from tests.unit.services.test_artifact_conversion import database, seed, service

pytestmark = pytest.mark.asyncio
OPERATOR = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize("action", ["download", "continue"])
@pytest.mark.parametrize("ref_form", ["bare", "durable"])
@pytest.mark.parametrize(
    "retained_owner,quarantine", [(False, False), (True, False), (True, True)]
)
async def test_converted_artifact_attached_to_new_workflow(
    tmp_path, monkeypatch, action, ref_form, retained_owner, quarantine
):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("DEFAULT_USER_ID", str(OPERATOR or ""))
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path, quarantine=quarantine)
            artifact_ref = (
                f"artifact:{artifact_id}" if ref_form == "durable" else artifact_id
            )
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
            # This workflow is created after conversion. Its captured evidence
            # may legitimately refer to retained bytes from the prior account.
            source = TemporalExecutionCanonicalRecord(
                workflow_id="new-workflow", run_id="new-run", namespace="default",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW, entry="user_workflow",
                owner_id=owner if retained_owner else OPERATOR, owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.COMPLETED,
                parameters={"workflow": {"instructions": "new work"}}, memo={},
            )
            session.add(source)
            session.add(TemporalArtifactLink(artifact_id=artifact_id, namespace="default",
                workflow_id=source.workflow_id, run_id=source.run_id, link_type="output"))
            await session.commit()
            artifacts = service(session, tmp_path)
            retained_quota = await TemporalArtifactRepository(session).quota_usage_for_scope(owner)
            monkeypatch.setattr(ex, "get_temporal_artifact_service", lambda _: artifacts)
            evidence = ex._SourceCapturedEvidence(
                available=True,
                named_refs={"finalSnapshotRef": artifact_ref},
                additional_refs=[],
                finish_summary_ref=None,
                final_snapshot_ref=artifact_ref,
                capture_manifest_ref=None,
                summary=None,
                logical_step_id=None,
                step_execution_id=None,
                incomplete_reason=None,
            )
            monkeypatch.setattr(ex, "_resolve_source_captured_evidence", AsyncMock(return_value=evidence))

            class WorkflowService:
                async def describe_execution(self, workflow_id, **kwargs):
                    assert workflow_id == source.workflow_id
                    return source

                @staticmethod
                def _full_rerun_parameters(parameters):
                    return dict(parameters)

                async def create_execution(self, **kwargs):
                    record = TemporalExecutionCanonicalRecord(
                        workflow_id=kwargs["_workflow_id"], run_id="destination-run",
                        namespace="default", workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                        entry="user_workflow", owner_id=OPERATOR,
                        owner_type=TemporalExecutionOwnerType.USER,
                        state=MoonMindWorkflowState.INITIALIZING,
                        parameters=kwargs["initial_parameters"], memo={},
                    )
                    session.add(record)
                    await session.flush()
                    return record

            app = FastAPI()
            app.include_router(ex.router)
            app.dependency_overrides[get_async_session] = lambda: session
            app.dependency_overrides[ex._get_service] = WorkflowService
            app.dependency_overrides[ex._ensure_submit_enabled] = lambda: None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                        base_url="http://localhost") as client:
                if quarantine:
                    if action == "download":
                        response = await client.get(
                            "/api/executions/new-workflow/captured-evidence/download",
                            params={"ref": artifact_ref},
                        )
                    else:
                        response = await client.post(
                            "/api/executions/new-workflow/continue",
                            json={
                                "idempotencyKey": "quarantined",
                                "selectedSourceArtifactRefs": [artifact_ref],
                            },
                        )
                    # Existing read() state-error mapping returns a generic 500;
                    # recovery must not bypass quarantine or return its bytes.
                    assert response.status_code == 500
                    assert b"retained bytes" not in response.content
                    assert await TemporalArtifactRepository(session).quota_usage_for_scope(owner) == retained_quota
                    return
                if action == "download":
                    response = await client.get(
                        "/api/executions/new-workflow/captured-evidence/download",
                        params={"ref": artifact_ref},
                    )
                    assert response.status_code == 200, response.text
                    assert response.content == b"retained bytes"
                    denied = await client.get("/api/executions/new-workflow/captured-evidence/download",
                                              params={"ref": "art_not_selected"})
                    assert denied.status_code == 404
                else:
                    response = await client.post(
                        "/api/executions/new-workflow/continue",
                        json={
                            "idempotencyKey": "recovery",
                            "selectedSourceArtifactRefs": [artifact_ref],
                        },
                    )
                    assert response.status_code == 201, response.text
                    destination = await session.get(TemporalExecutionCanonicalRecord,
                                                    response.json()["destinationWorkflowId"])
                    attachments = destination.parameters["workflow"]["inputAttachments"]
                    assert len(attachments) == 1
                    copied_id = attachments[0]["artifactId"]
                    _, body = await artifacts.read(
                        artifact_id=copied_id, access=access_for_test(OPERATOR)
                    )
                    assert body == b"retained bytes"
                    links = (await session.execute(select(TemporalArtifactLink).where(
                        TemporalArtifactLink.artifact_id == copied_id))).scalars().all()
                    assert any(link.workflow_id == destination.workflow_id
                               and link.link_type == "input.attachment" for link in links)
                    denied = await client.post("/api/executions/new-workflow/continue", json={
                        "idempotencyKey": "unauthorized", "selectedSourceArtifactRefs": ["art_not_selected"]})
                    assert denied.status_code == 403
                for headers in [
                    {"X-MoonMind-Execution-Fanout": "v1"},
                    {"X-MoonMind-Execution-Fanout": "v1", "X-MoonMind-Admitted-Principal": "operator"},
                ]:
                    denied = await client.get(
                        "/api/executions/new-workflow/captured-evidence/download",
                        params={"ref": artifact_ref},
                        headers=headers,
                    )
                    assert denied.status_code == 401
                    denied = await client.post("/api/executions/new-workflow/continue", headers=headers, json={
                        "idempotencyKey": "marker-denied", "selectedSourceArtifactRefs": [artifact_id]})
                    assert denied.status_code == 401
                retained = await session.get(TemporalArtifact, artifact_id)
                assert retained.created_by_principal == owner
                assert retained.operator_recovery_granted is True
                assert await TemporalArtifactRepository(session).quota_usage_for_scope(owner) == retained_quota
