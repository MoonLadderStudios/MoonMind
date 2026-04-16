from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.executions import get_temporal_client
from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalArtifact,
    TemporalArtifactEncryption,
    TemporalArtifactLink,
    TemporalArtifactRedactionLevel,
    TemporalArtifactRetentionClass,
    TemporalArtifactStatus,
    TemporalArtifactStorageBackend,
    TemporalArtifactUploadMode,
)
from api_service.main import app
from moonmind.config.settings import settings
from moonmind.workflows.temporal.service import TemporalExecutionService

CURRENT_USER_DEP = get_current_user()


class _QueryHandle:
    def __init__(self, state: dict[str, dict[str, object]], workflow_id: str) -> None:
        self._state = state
        self._workflow_id = workflow_id

    async def query(self, name: str):
        workflow_state = self._state.get(self._workflow_id, {})
        return workflow_state.get(name)


class _QueryClient:
    def __init__(self, state: dict[str, dict[str, object]]) -> None:
        self._state = state

    def get_workflow_handle(self, workflow_id: str) -> _QueryHandle:
        return _QueryHandle(self._state, workflow_id)


@pytest.fixture(autouse=True)
def _reset_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def query_state():
    state: dict[str, dict[str, object]] = {}
    app.dependency_overrides[get_temporal_client] = lambda: _QueryClient(state)
    return state


async def _create_uploaded_artifact(
    artifact_id: str,
    *,
    status: TemporalArtifactStatus = TemporalArtifactStatus.COMPLETE,
) -> str:
    async with db_base.async_session_maker() as session:
        session.add(
            TemporalArtifact(
                artifact_id=artifact_id,
                content_type="application/json; charset=utf-8",
                size_bytes=128,
                storage_key=f"tests/{artifact_id}.json",
                storage_backend=TemporalArtifactStorageBackend.S3,
                encryption=TemporalArtifactEncryption.NONE,
                status=status,
                retention_class=TemporalArtifactRetentionClass.STANDARD,
                redaction_level=TemporalArtifactRedactionLevel.NONE,
                upload_mode=TemporalArtifactUploadMode.SINGLE_PUT,
                metadata_json={"label": "Contract test input"},
            )
        )
        await session.commit()
    return artifact_id


@pytest.mark.asyncio
async def test_execution_lifecycle_endpoints_contract(tmp_path, query_state):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_contract.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            create_response = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.Run",
                    "title": "Contract run",
                    "inputArtifactRef": "artifact://input/123",
                    "idempotencyKey": "create-abc",
                },
            )
            assert create_response.status_code == 201
            execution = create_response.json()
            workflow_id = execution["workflowId"]
            assert execution["taskId"] == workflow_id
            assert execution["source"] == "temporal"
            assert execution["ownerType"] == "user"
            assert execution["entry"] == "run"
            assert execution["status"] == "queued"
            assert execution["rawState"] == "initializing"
            assert execution["runId"]
            assert execution["temporalRunId"] == execution["runId"]
            assert execution["createdAt"]
            assert execution["startedAt"] in (execution["createdAt"], None)
            assert execution["detailHref"] == f"/tasks/{workflow_id}"

            describe_response = await client.get(f"/api/executions/{workflow_id}")
            assert describe_response.status_code == 200
            describe_body = describe_response.json()
            assert describe_body["taskId"] == workflow_id
            assert describe_body["workflowId"] == workflow_id
            assert describe_body["entry"] == "run"
            assert describe_body["ownerType"] == "user"
            assert describe_body["ownerId"] == str(shared_user_id)
            assert describe_body["title"] == "Contract run"
            assert describe_body["summary"] == "Execution initialized."
            assert describe_body["dashboardStatus"] == "queued"
            assert describe_body["state"] == "initializing"
            assert describe_body["temporalStatus"] == "running"
            assert describe_body["artifactRefs"] == ["artifact://input/123"]
            query_state[workflow_id] = {
                "get_progress": {
                    "runId": "run-query-latest",
                    "total": 2,
                    "pending": 1,
                    "ready": 0,
                    "running": 1,
                    "awaitingExternal": 0,
                    "reviewing": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "skipped": 0,
                    "canceled": 0,
                    "currentStepTitle": "Run tests",
                    "updatedAt": "2026-04-08T12:00:00Z",
                },
                "get_step_ledger": {
                    "workflowId": workflow_id,
                    "runId": "run-query-latest",
                    "runScope": "latest",
                    "steps": [
                        {
                            "logicalStepId": "run-tests",
                            "order": 1,
                            "title": "Run tests",
                            "tool": {
                                "type": "skill",
                                "name": "repo.run_tests",
                                "version": "1",
                            },
                            "dependsOn": [],
                            "status": "running",
                            "waitingReason": None,
                            "attentionRequired": False,
                            "attempt": 1,
                            "startedAt": "2026-04-08T12:00:00Z",
                            "updatedAt": "2026-04-08T12:00:00Z",
                            "summary": "Executing tests",
                            "checks": [],
                            "refs": {
                                "childWorkflowId": None,
                                "childRunId": None,
                                "taskRunId": "task-run-123",
                            },
                            "artifacts": {
                                "outputSummary": None,
                                "outputPrimary": None,
                                "runtimeStdout": None,
                                "runtimeStderr": None,
                                "runtimeMergedLogs": None,
                                "runtimeDiagnostics": None,
                                "providerSnapshot": None,
                            },
                            "lastError": None,
                        }
                    ],
                },
            }
            describe_response = await client.get(f"/api/executions/{workflow_id}")
            assert describe_response.status_code == 200
            describe_body = describe_response.json()
            assert describe_body["runId"] == "run-query-latest"
            assert describe_body["temporalRunId"] == "run-query-latest"
            assert describe_body["progress"]["running"] == 1
            assert describe_body["progress"]["currentStepTitle"] == "Run tests"
            assert "runId" not in describe_body["progress"]
            assert describe_body["stepsHref"] == f"/api/executions/{workflow_id}/steps"
            original_temporal_run_id = describe_response.json()["temporalRunId"]

            steps_response = await client.get(f"/api/executions/{workflow_id}/steps")
            assert steps_response.status_code == 200
            steps_body = steps_response.json()
            assert steps_body["workflowId"] == workflow_id
            assert steps_body["runId"] == "run-query-latest"
            assert steps_body["runScope"] == "latest"
            assert steps_body["steps"][0]["refs"]["taskRunId"] == "task-run-123"

            configure_integration = await client.post(
                f"/api/executions/{workflow_id}/integration",
                json={
                    "integrationName": "jules",
                    "externalOperationId": "task-123",
                    "normalizedStatus": "running",
                    "providerStatus": "queued",
                    "callbackSupported": True,
                    "recommendedPollSeconds": 30,
                    "externalUrl": "https://jules.example.test/tasks/task-123",
                },
            )
            assert configure_integration.status_code == 202
            configured_body = configure_integration.json()
            assert configured_body["state"] == "awaiting_external"
            assert configured_body["searchAttributes"]["mm_integration"] == "jules"
            callback_key = configured_body["integration"]["callbackCorrelationKey"]
            assert callback_key

            update_response = await client.post(
                f"/api/executions/task:{workflow_id}/update",
                json={
                    "updateName": "SetTitle",
                    "title": "Renamed title",
                    "idempotencyKey": "set-title-1",
                },
            )
            assert update_response.status_code == 200
            update_body = update_response.json()
            assert update_response.headers.get("Deprecation") == "true"
            assert (
                update_response.headers.get("X-MoonMind-Canonical-WorkflowId")
                == workflow_id
            )
            assert update_body["accepted"] is True
            assert update_body["execution"]["taskId"] == workflow_id
            assert update_body["execution"]["workflowId"] == workflow_id
            assert update_body["execution"]["uiQueryModel"] == "compatibility_adapter"
            assert update_body["refresh"] == {
                "uiQueryModel": "compatibility_adapter",
                "patchedExecution": True,
                "listStale": True,
                "refetchSuggested": True,
                "refreshedAt": update_body["refresh"]["refreshedAt"],
            }

            rerun_response = await client.post(
                f"/api/executions/{workflow_id}/update",
                json={
                    "updateName": "RequestRerun",
                    "idempotencyKey": "rerun-1",
                },
            )
            assert rerun_response.status_code == 200
            assert rerun_response.json()["accepted"] is True
            assert rerun_response.json()["applied"] == "continue_as_new"
            latest_rerun_run_id = rerun_response.json()["execution"]["runId"]
            query_state[workflow_id]["get_progress"]["runId"] = latest_rerun_run_id
            query_state[workflow_id]["get_step_ledger"]["runId"] = latest_rerun_run_id

            rerun_detail = await client.get(f"/api/executions/{workflow_id}")
            assert rerun_detail.status_code == 200
            rerun_execution = rerun_detail.json()
            assert rerun_execution["taskId"] == workflow_id
            assert rerun_execution["workflowId"] == workflow_id
            assert rerun_execution["detailHref"] == f"/tasks/{workflow_id}"
            assert rerun_execution["temporalRunId"] != original_temporal_run_id
            assert rerun_execution["temporalRunId"] == latest_rerun_run_id

            pause_response = await client.post(
                f"/api/executions/{workflow_id}/signal",
                json={"signalName": "Pause"},
            )
            assert pause_response.status_code == 202
            pause_body = pause_response.json()
            assert pause_body["state"] == "awaiting_external"
            assert pause_body["waitingReason"] == "operator_paused"
            assert pause_body["attentionRequired"] is True
            assert pause_body["dashboardStatus"] == "awaiting_action"
            assert pause_body["status"] == "awaiting_action"

            resume_response = await client.post(
                f"/api/executions/{workflow_id}/signal",
                json={"signalName": "Resume"},
            )
            assert resume_response.status_code == 202
            resume_body = resume_response.json()
            assert resume_body["state"] == "executing"
            assert resume_body["waitingReason"] is None
            assert resume_body["attentionRequired"] is False
            assert resume_body["dashboardStatus"] == "running"
            assert resume_body["status"] == "running"

            poll_response = await client.post(
                f"/api/executions/{workflow_id}/integration/poll",
                json={
                    "normalizedStatus": "running",
                    "providerStatus": "running",
                    "completedWaitCycles": 1,
                },
            )
            assert poll_response.status_code == 202
            assert poll_response.json()["integration"]["monitorAttemptCount"] == 1

            callback_response = await client.post(
                f"/api/integrations/jules/callbacks/{callback_key}",
                json={
                    "eventType": "completed",
                    "providerEventId": "evt-1",
                    "normalizedStatus": "completed",
                    "providerStatus": "completed",
                },
            )
            assert callback_response.status_code == 202
            callback_body = callback_response.json()
            assert callback_body["state"] == "executing"
            assert callback_body["integration"]["normalizedStatus"] == "completed"

            cancel_response = await client.post(
                f"/api/executions/{workflow_id}/cancel",
                json={"reason": "stop"},
            )
            assert cancel_response.status_code == 202
            cancel_body = cancel_response.json()
            assert cancel_body["state"] == "canceled"
            assert cancel_body["temporalStatus"] == "canceled"
            assert cancel_body["closeStatus"] == "canceled"
            assert cancel_body["dashboardStatus"] == "canceled"
            assert cancel_body["status"] == "canceled"

            post_cancel_update = await client.post(
                f"/api/executions/{workflow_id}/update",
                json={
                    "updateName": "SetTitle",
                    "title": "Should reject",
                    "idempotencyKey": "post-cancel",
                },
            )
            assert post_cancel_update.status_code == 200
            assert post_cancel_update.json()["accepted"] is False
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_request_rerun_keeps_workflow_id_and_rotates_run_id(tmp_path, query_state):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_contract_rerun.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            create_response = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.Run",
                    "title": "Rerun contract",
                    "planArtifactRef": "artifact://plan/123",
                    "idempotencyKey": "rerun-create-1",
                },
            )
            assert create_response.status_code == 201
            created = create_response.json()

            rerun_response = await client.post(
                f"/api/executions/{created['workflowId']}/update",
                json={
                    "updateName": "RequestRerun",
                    "idempotencyKey": "rerun-update-1",
                },
            )
            assert rerun_response.status_code == 200
            rerun_body = rerun_response.json()
            assert rerun_body["accepted"] is True
            assert rerun_body["applied"] == "continue_as_new"
            assert (
                rerun_body["message"]
                == "Rerun requested. Execution continued as new run."
            )
            assert rerun_body["continueAsNewCause"] == "manual_rerun"
            assert rerun_body["execution"]["workflowId"] == created["workflowId"]
            assert rerun_body["execution"]["runId"] != created["runId"]
            assert (
                rerun_body["execution"]["temporalRunId"]
                == rerun_body["execution"]["runId"]
            )
            assert rerun_body["execution"]["continueAsNewCause"] == "manual_rerun"
            assert rerun_body["refresh"] == {
                "uiQueryModel": "compatibility_adapter",
                "patchedExecution": True,
                "listStale": True,
                "refetchSuggested": True,
                "refreshedAt": rerun_body["refresh"]["refreshedAt"],
            }

            describe_response = await client.get(
                f"/api/executions/{created['workflowId']}"
            )
            assert describe_response.status_code == 200
            described = describe_response.json()
            assert described["workflowId"] == created["workflowId"]
            assert described["taskId"] == created["workflowId"]
            assert described["runId"] != created["runId"]
            assert described["temporalRunId"] == described["runId"]
            assert described["latestRunView"] is True
            assert described["continueAsNewCause"] == "manual_rerun"
            if created["startedAt"] is not None:
                assert described["startedAt"] == created["startedAt"]
            assert described["state"] == "executing"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_execution_list_pagination_and_state_filter(tmp_path, query_state):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_contract_list.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            created_ids: list[str] = []
            for idx in range(3):
                response = await client.post(
                    "/api/executions",
                    json={
                        "workflowType": "MoonMind.Run",
                        "title": f"Run-{idx}",
                        "inputArtifactRef": f"artifact://input/{idx}",
                        "idempotencyKey": f"list-{idx}",
                    },
                )
                assert response.status_code == 201
                created_ids.append(response.json()["workflowId"])

            manifest_response = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.ManifestIngest",
                    "title": "Manifest-0",
                    "manifestArtifactRef": "artifact://manifest/0",
                    "idempotencyKey": "manifest-0",
                },
            )
            assert manifest_response.status_code == 201

            await client.post(f"/api/executions/{created_ids[0]}/cancel", json={})

            first_page = await client.get(
                "/api/executions",
                params={
                    "workflowType": "MoonMind.Run",
                    "ownerType": "user",
                    "entry": "run",
                    "pageSize": 2,
                },
            )
            assert first_page.status_code == 200
            first_body = first_page.json()
            assert len(first_body["items"]) == 2
            assert first_body["count"] == 3
            assert first_body["uiQueryModel"] == "compatibility_adapter"
            assert first_body["staleState"] is False
            assert first_body["degradedCount"] is False
            assert first_body["refreshedAt"]
            assert first_body["nextPageToken"]
            for item in first_body["items"]:
                assert item["workflowId"]
                assert item["taskId"] == item["workflowId"]
                assert item["temporalRunId"] == item["runId"]
                assert item["latestRunView"] is True
                assert item["ownerType"] == "user"
                assert item["entry"] == "run"
                assert item["artifactRefs"] == []

            second_page = await client.get(
                "/api/executions",
                params={
                    "workflowType": "MoonMind.Run",
                    "ownerType": "user",
                    "entry": "run",
                    "pageSize": 2,
                    "nextPageToken": first_body["nextPageToken"],
                },
            )
            assert second_page.status_code == 200
            second_body = second_page.json()
            assert len(second_body["items"]) == 1
            assert (
                second_body["items"][0]["taskId"]
                == second_body["items"][0]["workflowId"]
            )

            canceled_only = await client.get(
                "/api/executions",
                params={
                    "workflowType": "MoonMind.Run",
                    "ownerType": "user",
                    "entry": "run",
                    "state": "canceled",
                },
            )
            assert canceled_only.status_code == 200
            canceled_body = canceled_only.json()
            assert canceled_body["count"] == 1
            assert canceled_body["items"][0]["state"] == "canceled"
            assert canceled_body["items"][0]["dashboardStatus"] == "canceled"
            assert (
                canceled_body["items"][0]["taskId"]
                == canceled_body["items"][0]["workflowId"]
            )

            stale_token = await client.get(
                "/api/executions",
                params={
                    "workflowType": "MoonMind.Run",
                    "ownerType": "user",
                    "entry": "run",
                    "state": "canceled",
                    "pageSize": 2,
                    "nextPageToken": first_body["nextPageToken"],
                },
            )
            assert stale_token.status_code == 422
            assert stale_token.json()["detail"]["code"] == "invalid_execution_query"

            forbidden = await client.get(
                "/api/executions",
                params={"ownerType": "system"},
            )
            assert forbidden.status_code == 403
            run_only = await client.get(
                "/api/executions",
                params={"entry": "run", "ownerType": "user"},
            )
            assert run_only.status_code == 200
            run_only_body = run_only.json()
            assert run_only_body["count"] == 3
            assert all(item["entry"] == "run" for item in run_only_body["items"])

            manifest_only = await client.get(
                "/api/executions",
                params={"entry": "manifest", "ownerType": "user"},
            )
            assert manifest_only.status_code == 200
            manifest_body = manifest_only.json()
            assert manifest_body["count"] == 1
            assert manifest_body["items"][0]["entry"] == "manifest"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_projection_orphaned_rows_repair_from_canonical_public_routes(tmp_path, query_state):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_contract_orphaned.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            create_response = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.Run",
                    "title": "Ghost row candidate",
                    "idempotencyKey": "orphaned-create-1",
                },
            )
            assert create_response.status_code == 201
            workflow_id = create_response.json()["workflowId"]

            async with db_base.async_session_maker() as session:
                service = TemporalExecutionService(session)
                await service.mark_projection_orphaned(
                    workflow_id=workflow_id,
                    sync_error="temporal execution missing",
                )

            describe_response = await client.get(f"/api/executions/{workflow_id}")
            assert describe_response.status_code == 200
            assert describe_response.json()["workflowId"] == workflow_id

            update_response = await client.post(
                f"/api/executions/{workflow_id}/update",
                json={
                    "updateName": "SetTitle",
                    "title": "Should repair",
                    "idempotencyKey": "orphaned-update-1",
                },
            )
            assert update_response.status_code == 200
            assert update_response.json()["accepted"] is True

            list_response = await client.get("/api/executions")
            assert list_response.status_code == 200
            assert list_response.json()["count"] == 1
            assert list_response.json()["items"][0]["workflowId"] == workflow_id
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_task_shaped_create_returns_temporal_identity_and_redirect(
    tmp_path, monkeypatch
):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_contract_submit.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_root",
        str(tmp_path / "artifacts"),
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            input_artifact_ref = await _create_uploaded_artifact(
                "art_01TESTCONTRACTINPUT000000000",
            )
            create_response = await client.post(
                "/api/executions",
                json={
                    "type": "task",
                    "priority": 4,
                    "maxAttempts": 3,
                    "payload": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "targetRuntime": "codex",
                        "requiredCapabilities": ["git"],
                        "task": {
                            "instructions": "Implement Temporal submit redirect coverage.",
                            "runtime": {
                                "mode": "codex",
                                "model": "gpt-5.3-codex",
                                "effort": "high",
                            },
                            "inputArtifactRef": input_artifact_ref,
                            "publish": {"mode": "branch"},
                        },
                    },
                },
            )
            assert create_response.status_code == 201
            body = create_response.json()
            assert body["source"] == "temporal"
            assert body["taskId"] == body["workflowId"]
            assert body["temporalRunId"] == body["runId"]
            assert body["legacyRunId"] is None
            assert body["redirectPath"] == f"/tasks/{body['taskId']}?source=temporal"
            assert body["searchAttributes"]["mm_repo"] == "MoonLadderStudios/MoonMind"
            assert body["memo"]["input_ref"] == input_artifact_ref
            snapshot = body["taskInputSnapshot"]
            assert snapshot["available"] is True
            assert snapshot["snapshotVersion"] == 1
            assert snapshot["sourceKind"] == "create"
            assert snapshot["reconstructionMode"] == "authoritative"
            assert snapshot["artifactRef"]
            assert (
                body["memo"]["summary"]
                == "Implement Temporal submit redirect coverage."
            )

            async with db_base.async_session_maker() as session:
                artifact = await session.get(
                    TemporalArtifact,
                    snapshot["artifactRef"],
                )
                assert artifact is not None
                assert (
                    artifact.content_type
                    == "application/vnd.moonmind.task-input-snapshot+json;version=1"
                )
                assert artifact.retention_class is TemporalArtifactRetentionClass.LONG
                assert (
                    artifact.metadata_json["artifact_class"]
                    == "input.original_snapshot"
                )
                assert artifact.metadata_json["snapshot_version"] == 1
                assert artifact.metadata_json["source_kind"] == "create"
                links = (
                    await session.execute(
                        select(TemporalArtifactLink).where(
                            TemporalArtifactLink.artifact_id == snapshot["artifactRef"],
                            TemporalArtifactLink.workflow_id == body["workflowId"],
                            TemporalArtifactLink.link_type == "input.original_snapshot",
                        )
                    )
                ).scalars().all()
                assert len(links) == 1
                canonical = await session.get(
                    TemporalExecutionCanonicalRecord,
                    body["workflowId"],
                )
                assert canonical is not None
                assert (
                    canonical.memo["task_input_snapshot_ref"]
                    == snapshot["artifactRef"]
                )
                assert snapshot["artifactRef"] in canonical.artifact_refs
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_task_shaped_create_rejects_pending_upload_input_artifact(tmp_path):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_contract_pending_input.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            artifact_id = await _create_uploaded_artifact(
                "art_01TESTPENDINGCONTRACTINPUT0",
                status=TemporalArtifactStatus.PENDING_UPLOAD,
            )

            create_response = await client.post(
                "/api/executions",
                json={
                    "type": "task",
                    "priority": 4,
                    "maxAttempts": 3,
                    "payload": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "targetRuntime": "codex",
                        "task": {
                            "instructions": "Should fail fast on unreadable input artifact.",
                            "runtime": {
                                "mode": "codex",
                                "model": "gpt-5.3-codex",
                                "effort": "high",
                            },
                            "inputArtifactRef": artifact_id,
                            "publish": {"mode": "branch"},
                        },
                    },
                },
            )
            assert create_response.status_code == 422
            body = create_response.json()
            assert body["detail"]["code"] == "invalid_execution_request"
            assert "readable artifact" in body["detail"]["message"]
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_manifest_execution_status_and_node_page_contract(tmp_path):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_manifest_contract.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    shared_user_id = uuid4()
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id=shared_user_id, is_superuser=False
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            manifest_artifact_ref = await _create_uploaded_artifact(
                "art_manifest_123",
            )
            create_response = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.ManifestIngest",
                    "manifestArtifactRef": manifest_artifact_ref,
                    "failurePolicy": "best_effort",
                    "initialParameters": {
                        "requestedBy": {"type": "user", "id": str(shared_user_id)},
                        "executionPolicy": {"maxConcurrency": 6},
                        "manifestNodes": [
                            {"nodeId": "node-a", "state": "ready"},
                            {"nodeId": "node-b", "state": "running"},
                            {"nodeId": "node-c", "state": "failed"},
                        ],
                    },
                    "idempotencyKey": "manifest-contract-create-1",
                },
            )
            assert create_response.status_code == 201
            created = create_response.json()
            workflow_id = created["workflowId"]
            assert created["workflowType"] == "MoonMind.ManifestIngest"
            assert manifest_artifact_ref in created["artifactRefs"]
            assert created["executionPolicy"]["maxConcurrency"] == 6
            assert created["counts"]["ready"] == 1
            assert created["counts"]["running"] == 1
            assert created["counts"]["failed"] == 1

            update_response = await client.post(
                f"/api/executions/{workflow_id}/update",
                json={
                    "updateName": "SetConcurrency",
                    "maxConcurrency": 4,
                    "idempotencyKey": "manifest-set-concurrency-1",
                },
            )
            assert update_response.status_code == 200
            assert update_response.json()["accepted"] is True

            status_response = await client.get(
                f"/api/executions/{workflow_id}/manifest-status"
            )
            assert status_response.status_code == 200
            status_payload = status_response.json()
            assert status_payload["workflowId"] == workflow_id
            assert status_payload["maxConcurrency"] == 4
            assert status_payload["failurePolicy"] == "best_effort"
            assert status_payload["counts"]["running"] == 1

            nodes_response = await client.get(
                f"/api/executions/{workflow_id}/manifest-nodes",
                params={"state": "running", "limit": 10},
            )
            assert nodes_response.status_code == 200
            nodes_payload = nodes_response.json()
            assert nodes_payload["count"] == 1
            assert nodes_payload["items"][0]["nodeId"] == "node-b"
            assert nodes_payload["items"][0]["workflowType"] == "MoonMind.Run"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker
