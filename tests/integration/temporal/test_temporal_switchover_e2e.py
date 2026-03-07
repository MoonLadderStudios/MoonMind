"""Integration test for the full user-visible Temporal workflow switchover path."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.client import Client

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import Base, User
from api_service.main import app
from moonmind.config.settings import settings

# Ignore the env file so we can run isolated in pytest
os.environ["IGNORE_ENV_FILE"] = "1"

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _poll_for_status(
    client: AsyncClient,
    task_id: str,
    target_statuses: set[str],
    max_attempts: int = 120,
) -> dict[str, Any]:
    for _ in range(max_attempts):
        resp = await client.get(f"/api/executions/{task_id}")
        assert (
            resp.status_code == 200
        ), f"Detail fetch failed with {resp.status_code}: {resp.text}"
        detail = resp.json()
        if detail.get("status") in target_statuses:
            return detail
        await asyncio.sleep(1.0)
    pytest.fail(f"Task {task_id} did not reach one of {target_statuses} within timeout")
    raise AssertionError(
        f"Task {task_id} did not reach one of {target_statuses} within timeout"
    )


async def _poll_for_artifact_refs(
    client: AsyncClient,
    task_id: str,
    max_attempts: int = 120,
) -> list[str]:
    for _ in range(max_attempts):
        resp = await client.get(f"/api/executions/{task_id}")
        assert (
            resp.status_code == 200
        ), f"Detail fetch failed with {resp.status_code}: {resp.text}"
        detail = resp.json()
        artifact_refs = list(detail.get("artifactRefs") or [])
        if artifact_refs:
            return artifact_refs
        await asyncio.sleep(1.0)
    pytest.fail(f"Task {task_id} did not expose artifact refs within timeout")
    raise AssertionError(f"Task {task_id} did not expose artifact refs within timeout")


async def test_temporal_switchover_e2e() -> None:
    """Verify that a full end-to-end task flows through the system."""

    current_user_dependency: Callable[[], User] = get_current_user()

    # Only run this test if we can connect to a local Temporal instance.
    # The requirement is that the test passes on a healthy stack without manual intervention.
    try:
        temporal_client = await asyncio.wait_for(
            Client.connect(settings.temporal.address), timeout=5
        )
        await temporal_client.close()
    except Exception as e:
        pytest.skip(
            "Skipping e2e test because Temporal is not available "
            f"at {settings.temporal.address}: {e}"
        )

    test_user_id = uuid.uuid4()
    test_user = User(id=test_user_id, email="test@example.com", is_superuser=True)

    app.dependency_overrides[current_user_dependency] = lambda: test_user

    # Setup an isolated test DB to satisfy the API's database dependencies
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session_maker = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_async_session():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_get_async_session

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Create execution
            create_req = {
                "workflowType": "MoonMind.Run",
                "title": "E2E Test Task",
                "initialParameters": {
                    "testing": True,
                },
            }

            response = await client.post("/api/executions", json=create_req)
            if response.status_code != 201:
                pytest.fail(f"Failed to create execution: {response.text}")

            execution = response.json()
            assert "workflowId" in execution
            assert "taskId" in execution
            task_id = execution["taskId"]
            workflow_id = execution["workflowId"]

            # Verify it shows up in the list
            list_resp = await client.get("/api/executions")
            assert list_resp.status_code == 200
            items = list_resp.json().get("items", [])
            assert any(
                item.get("taskId") == task_id for item in items
            ), "Execution not found in list"

            # Wait until the execution leaves the 'initializing' status, proving workers are polling
            # and moving the state forward.
            detail = await _poll_for_status(
                client,
                task_id,
                {"planning", "executing", "success", "failed", "canceled"},
            )

            # Wait for artifact refs to appear rather than using a fixed sleep duration.
            artifact_refs = await _poll_for_artifact_refs(client, task_id)

            # Check artifact link works
            artifacts_resp = await client.get(
                f"/api/executions/moonmind/{workflow_id}/{detail.get('runId')}/artifacts"
            )
            assert (
                artifacts_resp.status_code == 200
            ), f"Failed to fetch artifacts: {artifacts_resp.text}"
            assert artifact_refs

            # Perform an operator action: cancel
            cancel_resp = await client.post(
                f"/api/executions/{task_id}/cancel", json={}
            )
            assert (
                cancel_resp.status_code == 202
            ), f"Cancel failed: {cancel_resp.status_code} {cancel_resp.text}"

            # Ensure the cancel action reflects on the workflow
            final_detail = await _poll_for_status(
                client, task_id, {"success", "failed", "canceled"}
            )
            assert final_detail.get("status") in {"success", "failed", "canceled"}
    finally:
        app.dependency_overrides.pop(current_user_dependency, None)
        app.dependency_overrides.pop(get_async_session, None)
        await engine.dispose()
