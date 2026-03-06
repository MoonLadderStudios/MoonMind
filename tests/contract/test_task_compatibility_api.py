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

CURRENT_USER_DEP = get_current_user()


@pytest.fixture(autouse=True)
def _reset_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_task_compatibility_temporal_list_and_detail_contract(tmp_path):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/task_compatibility_contract.db"
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
            run_response = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.Run",
                    "title": "Compatibility Run",
                    "inputArtifactRef": "artifact://input/compat-run",
                    "idempotencyKey": "compat-run",
                },
            )
            assert run_response.status_code == 201
            run_execution = run_response.json()

            manifest_response = await client.post(
                "/api/executions",
                json={
                    "workflowType": "MoonMind.ManifestIngest",
                    "title": "Compatibility Manifest",
                    "manifestArtifactRef": "artifact://manifest/compat",
                    "idempotencyKey": "compat-manifest",
                },
            )
            assert manifest_response.status_code == 201
            manifest_execution = manifest_response.json()

            list_response = await client.get(
                "/api/tasks/list",
                params={"source": "temporal", "pageSize": 10},
            )
            assert list_response.status_code == 200
            list_body = list_response.json()
            assert list_body["count"] == 2
            assert list_body["countMode"] == "exact"
            assert list_body["nextCursor"] is None

            rows_by_id = {item["taskId"]: item for item in list_body["items"]}
            assert set(rows_by_id) == {
                run_execution["workflowId"],
                manifest_execution["workflowId"],
            }

            run_row = rows_by_id[run_execution["workflowId"]]
            assert run_row["source"] == "temporal"
            assert run_row["entry"] == "run"
            assert run_row["taskId"] == run_row["workflowId"]
            assert run_row["title"] == "Compatibility Run"
            assert run_row["status"] == "queued"
            assert run_row["detailHref"] == f"/tasks/{run_execution['workflowId']}"

            manifest_row = rows_by_id[manifest_execution["workflowId"]]
            assert manifest_row["source"] == "temporal"
            assert manifest_row["entry"] == "manifest"
            assert manifest_row["taskId"] == manifest_row["workflowId"]
            assert manifest_row["title"] == "Compatibility Manifest"

            detail_response = await client.get(f"/api/tasks/{run_execution['workflowId']}")
            assert detail_response.status_code == 200
            detail = detail_response.json()
            assert detail["taskId"] == run_execution["workflowId"]
            assert detail["workflowId"] == run_execution["workflowId"]
            assert detail["temporalRunId"] == run_execution["temporalRunId"]
            assert detail["source"] == "temporal"
            assert detail["entry"] == "run"
            assert detail["rawState"] == "initializing"
            assert detail["temporalStatus"] == "running"
            assert detail["closeStatus"] is None
            assert detail["artifactRefs"] == ["artifact://input/compat-run"]
            assert detail["inputArtifactRef"] == "artifact://input/compat-run"
            assert detail["searchAttributes"]["mm_owner_type"] == "user"
            assert detail["searchAttributes"]["mm_owner_id"] == str(shared_user_id)
            assert detail["searchAttributes"]["mm_entry"] == "run"
            assert detail["memo"]["title"] == "Compatibility Run"
            assert detail["actions"]["rename"] is True
            assert detail["actions"]["pause"] is True
            assert detail["actions"]["forceTerminate"] is False

            manifest_detail = await client.get(
                f"/api/tasks/{manifest_execution['workflowId']}"
            )
            assert manifest_detail.status_code == 200
            assert manifest_detail.json()["entry"] == "manifest"
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker


@pytest.mark.asyncio
async def test_task_compatibility_cursor_is_not_raw_temporal_page_token(tmp_path):
    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker

    db_url = f"sqlite+aiosqlite:///{tmp_path}/task_compatibility_cursor.db"
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
            for idx in range(3):
                response = await client.post(
                    "/api/executions",
                    json={
                        "workflowType": "MoonMind.Run",
                        "title": f"Cursor Run {idx}",
                        "idempotencyKey": f"compat-cursor-{idx}",
                    },
                )
                assert response.status_code == 201

            temporal_response = await client.get(
                "/api/executions",
                params={"pageSize": 2},
            )
            assert temporal_response.status_code == 200
            temporal_body = temporal_response.json()
            assert temporal_body["nextPageToken"]

            compatibility_response = await client.get(
                "/api/tasks/list",
                params={"source": "temporal", "pageSize": 2},
            )
            assert compatibility_response.status_code == 200
            compatibility_body = compatibility_response.json()
            assert compatibility_body["count"] == 3
            assert compatibility_body["countMode"] == "exact"
            assert compatibility_body["nextCursor"]
            assert compatibility_body["nextCursor"] != temporal_body["nextPageToken"]

            next_page = await client.get(
                "/api/tasks/list",
                params={
                    "source": "temporal",
                    "pageSize": 2,
                    "cursor": compatibility_body["nextCursor"],
                },
            )
            assert next_page.status_code == 200
            next_body = next_page.json()
            assert len(next_body["items"]) == 1
            assert next_body["nextCursor"] is None
    finally:
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker
