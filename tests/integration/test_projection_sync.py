from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.core.sync import sync_execution_projection
from api_service.db.models import Base, TemporalExecutionRecord

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

@pytest_asyncio.fixture
async def db_session(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        yield session

    await engine.dispose()

@pytest.mark.asyncio
async def test_sync_execution_projection_upsert_no_duplicates(db_session: AsyncSession):
    # Test DOC-REQ-002, DOC-REQ-003, DOC-REQ-004
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:upsert-test"
    desc.run_id = "run-upsert"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = start_time
    desc.execution_time = None
    desc.close_time = None

    class _CallableDict(dict):
        """Dict subclass that also works as an async callable (returns self)."""
        async def __call__(self):
            return dict(self)

    desc.memo = _CallableDict({
        "entry": "run",
        "owner_id": "owner-1",
        "owner_type": "user",
        "step_count": 1,
    })
    desc.search_attributes = {}

    # First insert
    record1 = await sync_execution_projection(db_session, desc)
    await db_session.commit()

    assert record1.workflow_id == "mm:upsert-test"
    assert record1.step_count == 1
    assert record1.projection_version == 1

    # Simulate update on temporal server
    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.close_time = start_time
    desc.memo["step_count"] = 5

    # Second sync (upsert)
    record2 = await sync_execution_projection(db_session, desc)
    await db_session.commit()

    assert record2.workflow_id == "mm:upsert-test"
    assert record2.step_count == 5
    assert record2.projection_version == 2
    assert record2.close_status is not None

    # Ensure no duplicates
    result = await db_session.execute(
        select(TemporalExecutionRecord).where(
            TemporalExecutionRecord.workflow_id == "mm:upsert-test"
        )
    )
    records = result.scalars().all()
    assert len(records) == 1

@pytest.mark.asyncio
async def test_list_executions_router_sync_behavior(db_session, monkeypatch):
    """MoonLadderStudios/MoonMind#3927: HTTP reads repair persisted drift."""
    from api_service.api.routers import executions
    from api_service.auth_providers import get_current_user
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings

    now = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:projection-router-3927"
    desc.run_id = "run-3927"
    desc.namespace = "default"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = now
    desc.execution_time = now
    desc.close_time = None
    desc.search_attributes = {}
    desc.memo = AsyncMock(return_value={
        "entry": "user_workflow",
        "owner_id": "owner-3927",
        "owner_type": "user",
    })
    record = await sync_execution_projection(db_session, desc)
    await db_session.commit()
    assert record.state.value == "executing"

    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.close_time = now
    handle = SimpleNamespace(describe=AsyncMock(return_value=desc))
    temporal = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    service = SimpleNamespace(list_executions=AsyncMock(return_value=SimpleNamespace(
        items=[record], next_page_token=None, count=1,
    )))
    user = SimpleNamespace(id="owner-3927", is_superuser=False)
    app = FastAPI()
    app.include_router(executions.router)

    async def session_dependency():
        yield db_session

    app.dependency_overrides[get_async_session] = session_dependency
    app.dependency_overrides[executions._get_service] = lambda: service
    app.dependency_overrides[executions.get_temporal_client] = lambda: temporal
    app.dependency_overrides[get_current_user()] = lambda: user
    # FastAPI may retain auth closures on nested router wrappers.
    routes = list(app.routes)
    while routes:
        route = routes.pop()
        wrapped = getattr(route, "original_router", route)
        routes.extend(getattr(wrapped, "routes", ()))
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", ()):
            if getattr(dependency.call, "__name__", "") == "_current_user_fallback":
                app.dependency_overrides[dependency.call] = lambda: user
    monkeypatch.setattr(settings.temporal, "temporal_authoritative_read_enabled", True)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/executions")

    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["state"] == "completed"
    temporal.get_workflow_handle.assert_called_once_with(desc.id)
    handle.describe.assert_awaited_once()
    await db_session.refresh(record)
    assert record.state.value == "completed"
    assert record.close_status.value == "completed"
    assert record.projection_version == 2
