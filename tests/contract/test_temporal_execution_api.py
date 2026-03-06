from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.main import app
from moonmind.workflows.temporal.service import TemporalExecutionService

CURRENT_USER_DEP = get_current_user()


@pytest.fixture(autouse=True)
def _reset_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_execution_lifecycle_endpoints_contract(tmp_path):
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

            describe_response = await client.get(f"/api/executions/{workflow_id}")
            assert describe_response.status_code == 200
            assert describe_response.json()["state"] == "initializing"
            assert describe_response.json()["temporalStatus"] == "running"

            update_response = await client.post(
                f"/api/executions/{workflow_id}/update",
                json={
                    "updateName": "SetTitle",
                    "title": "Renamed title",
                    "idempotencyKey": "set-title-1",
                },
            )
            assert update_response.status_code == 200
            assert update_response.json()["accepted"] is True

            pause_response = await client.post(
                f"/api/executions/{workflow_id}/signal",
                json={"signalName": "Pause"},
            )
            assert pause_response.status_code == 202
            assert pause_response.json()["state"] == "awaiting_external"

            resume_response = await client.post(
                f"/api/executions/{workflow_id}/signal",
                json={"signalName": "Resume"},
            )
            assert resume_response.status_code == 202
            assert resume_response.json()["state"] == "executing"

            cancel_response = await client.post(
                f"/api/executions/{workflow_id}/cancel",
                json={"reason": "stop"},
            )
            assert cancel_response.status_code == 202
            assert cancel_response.json()["state"] == "canceled"
            assert cancel_response.json()["temporalStatus"] == "canceled"
            assert cancel_response.json()["closeStatus"] == "canceled"

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

            forced_create = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.Run",
                    "title": "Forced termination run",
                    "idempotencyKey": "forced-create-1",
                },
            )
            assert forced_create.status_code == 201
            forced_workflow_id = forced_create.json()["workflowId"]
            forced_cancel = await client.post(
                f"/api/executions/{forced_workflow_id}/cancel",
                json={"reason": "ops stop", "graceful": False},
            )
            assert forced_cancel.status_code == 202
            assert forced_cancel.json()["state"] == "failed"
            assert forced_cancel.json()["temporalStatus"] == "failed"
            assert forced_cancel.json()["closeStatus"] == "terminated"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_execution_list_pagination_and_state_filter(tmp_path):
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
                        "idempotencyKey": f"list-{idx}",
                    },
                )
                assert response.status_code == 201
                created_ids.append(response.json()["workflowId"])

            await client.post(f"/api/executions/{created_ids[0]}/cancel", json={})

            first_page = await client.get(
                "/api/executions",
                params={"workflowType": "MoonMind.Run", "pageSize": 2},
            )
            assert first_page.status_code == 200
            first_body = first_page.json()
            assert len(first_body["items"]) == 2
            assert first_body["count"] == 3
            assert first_body["nextPageToken"]

            second_page = await client.get(
                "/api/executions",
                params={
                    "workflowType": "MoonMind.Run",
                    "pageSize": 2,
                    "nextPageToken": first_body["nextPageToken"],
                },
            )
            assert second_page.status_code == 200
            second_body = second_page.json()
            assert len(second_body["items"]) == 1

            canceled_only = await client.get(
                "/api/executions",
                params={"workflowType": "MoonMind.Run", "state": "canceled"},
            )
            assert canceled_only.status_code == 200
            canceled_body = canceled_only.json()
            assert canceled_body["count"] == 1
            assert canceled_body["items"][0]["state"] == "canceled"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_projection_orphaned_rows_repair_from_canonical_public_routes(tmp_path):
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
