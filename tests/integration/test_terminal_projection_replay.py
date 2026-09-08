import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.core.sync import sync_execution_projection
from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRecord,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

REPLAY = json.loads(
    (
        Path(__file__).parent
        / "reliability/replays/terminal-projection-metadata-skew/manifest.json"
    ).read_text()
)


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "agent-runs"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/projection.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            yield db
    finally:
        await engine.dispose()


@pytest.mark.parametrize("query", ["", "?source=temporal"])
@pytest.mark.parametrize(
    "terminal_status,expected",
    [
        (WorkflowExecutionStatus.FAILED, "failed"),
        (WorkflowExecutionStatus.COMPLETED, "completed"),
        (WorkflowExecutionStatus.CANCELED, "canceled"),
    ],
)
async def test_terminal_describe_repairs_metadata_skew(
    session, monkeypatch, query, terminal_status, expected
):
    from api_service.api.routers import executions
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.service import TemporalExecutionService

    started = datetime.fromisoformat(REPLAY["startedAt"])
    updated = datetime.fromisoformat(REPLAY["terminalUpdatedAt"])
    metadata_updated = datetime.fromisoformat(REPLAY["metadataUpdatedAt"])
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = REPLAY["incidentWorkflowId"]
    desc.run_id = "incident-run"
    desc.namespace = "default"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = terminal_status
    desc.start_time = desc.execution_time = started
    desc.close_time = datetime.fromisoformat(REPLAY["closedAt"])
    desc.search_attributes = {"mm_state": [expected], "mm_updated_at": [updated]}
    desc.memo = AsyncMock(
        return_value={
            "summary": "Terminal result",
            "owner_id": "owner",
            "owner_type": "user",
        }
    )
    source = TemporalExecutionCanonicalRecord(
        workflow_id=desc.id,
        run_id=desc.run_id,
        namespace="default",
        workflow_type=desc.workflow_type,
        owner_id="owner",
        owner_type="user",
        entry="user_workflow",
        state="executing",
        close_status=None,
        memo={"summary": "Launching agent..."},
        parameters={},
        artifact_refs=[],
        search_attributes={"mm_state": ["executing"]},
        started_at=started,
        updated_at=metadata_updated,
        finish_outcome_code=expected.upper(),
    )
    session.add(source)
    await session.commit()
    adapter = SimpleNamespace(describe_workflow=AsyncMock(return_value=desc))
    service = TemporalExecutionService(session, client_adapter=adapter)
    await service._sync_projection_best_effort(source)
    handle = SimpleNamespace(
        describe=AsyncMock(return_value=desc),
        query=AsyncMock(side_effect=RuntimeError("no live query")),
    )
    temporal = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    user = SimpleNamespace(id="owner", is_superuser=True)
    app = FastAPI()
    app.include_router(executions.router)

    async def session_dependency():
        yield session

    app.dependency_overrides[get_async_session] = session_dependency
    app.dependency_overrides[executions._get_service] = lambda: service
    app.dependency_overrides[executions.get_temporal_client] = lambda: temporal
    routes = list(app.routes)
    while routes:
        route = routes.pop()
        routes.extend(getattr(getattr(route, "original_router", route), "routes", ()))
        for dependency in getattr(
            getattr(route, "dependant", None), "dependencies", ()
        ):
            if getattr(dependency.call, "__name__", "") == "_current_user_fallback":
                app.dependency_overrides[dependency.call] = lambda: user
    monkeypatch.setattr(settings.temporal, "temporal_authoritative_read_enabled", True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/executions/{desc.id}{query}")
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["state"] == detail["rawState"] == expected
        assert detail["closeStatus"] == expected
        assert detail["closedAt"]
        # The older Temporal memo cannot replace newer database metadata.
        assert detail["summary"] == "Launching agent..."
        listing = await client.get("/api/executions")
        assert listing.status_code == 200, listing.text
        assert listing.json()["items"][0]["state"] == detail["state"]

    # A later metadata timestamp on a delayed RUNNING snapshot is no authority
    # to reopen this same run, even on a subsequent request/transaction.
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.close_time = None
    desc.search_attributes = {
        "mm_state": ["executing"],
        "mm_updated_at": [metadata_updated + timedelta(seconds=1)],
    }
    await sync_execution_projection(session, desc)
    await session.commit()
    for model in (TemporalExecutionCanonicalRecord, TemporalExecutionRecord):
        record = await session.get(model, desc.id, populate_existing=True)
        assert record.state.value == expected
        assert record.close_status.value == expected
        assert record.finish_outcome_code == expected.upper()


async def test_older_closure_preserves_newer_database_metadata(session):
    updated = datetime.fromisoformat(REPLAY["metadataUpdatedAt"])
    workflow_id = REPLAY["incidentWorkflowId"]
    source = TemporalExecutionCanonicalRecord(
        workflow_id=workflow_id, run_id="current-run", namespace="default",
        workflow_type="MoonMind.UserWorkflow", owner_id="owner", owner_type="user",
        entry="user_workflow", state="executing", close_status=None,
        memo={"summary": "Newer outcome", "parameters": {"version": "new"}},
        parameters={"version": "new"}, artifact_refs=[],
        search_attributes={"mm_state": ["executing"], "custom": ["preserve"]},
        updated_at=updated, integration_state={"pr": "published"},
        pending_parameters_patch={"version": "pending"}, attention_required=True,
        step_count=12, wait_cycle_count=3, rerun_count=2,
    )
    session.add(source)
    await session.commit()
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id, desc.run_id = workflow_id, "current-run"
    desc.namespace, desc.workflow_type = "default", "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.FAILED
    desc.start_time = desc.execution_time = datetime.fromisoformat(REPLAY["startedAt"])
    desc.close_time = datetime.fromisoformat(REPLAY["closedAt"])
    desc.search_attributes = {
        "mm_state": ["failed"],
        "mm_updated_at": [datetime.fromisoformat(REPLAY["terminalUpdatedAt"])],
    }
    desc.memo = AsyncMock(return_value={
        "summary": "Older outcome", "parameters": {"version": "old"},
        "integration_state": {"pr": "pending"}, "step_count": 1,
    })
    await sync_execution_projection(session, desc)
    await session.commit()
    for model in (TemporalExecutionCanonicalRecord, TemporalExecutionRecord):
        record = await session.get(model, workflow_id, populate_existing=True)
        assert record.state.value == record.close_status.value == "failed"
        assert record.closed_at is not None
        assert record.updated_at.replace(tzinfo=updated.tzinfo) == updated
        assert record.memo["summary"] == "Newer outcome"
        assert record.parameters == {"version": "new"}
        assert record.integration_state == {"pr": "published"}
        assert record.pending_parameters_patch == {"version": "pending"}
        assert record.attention_required is True
        assert (record.step_count, record.wait_cycle_count, record.rerun_count) == (12, 3, 2)
        assert record.search_attributes["custom"] == ["preserve"]
        assert record.search_attributes["mm_state"] == ["failed"]


@pytest.mark.parametrize(
    "incoming_run,status",
    [
        ("previous-run", WorkflowExecutionStatus.FAILED),
        ("current-run", WorkflowExecutionStatus.RUNNING),
        ("current-run", None),
        ("current-run", 999),
    ],
)
async def test_stale_or_unknown_evidence_cannot_close_current_run(
    session, incoming_run, status
):
    updated = datetime.fromisoformat(REPLAY["metadataUpdatedAt"])
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = REPLAY["incidentWorkflowId"]
    desc.run_id = "current-run"
    desc.namespace = "default"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = desc.execution_time = datetime.fromisoformat(REPLAY["startedAt"])
    desc.close_time = None
    desc.search_attributes = {"mm_state": ["executing"], "mm_updated_at": [updated]}
    desc.memo = AsyncMock(return_value={"summary": "Current run"})
    await sync_execution_projection(session, desc)
    await session.commit()

    desc.run_id = incoming_run
    desc.status = status
    desc.close_time = (
        datetime.fromisoformat(REPLAY["closedAt"])
        if status == WorkflowExecutionStatus.FAILED
        else None
    )
    desc.search_attributes = {
        "mm_state": ["unknown"],
        "mm_updated_at": [datetime.fromisoformat(REPLAY["terminalUpdatedAt"])],
    }
    desc.memo = AsyncMock(return_value={"summary": "Stale evidence"})
    record = await sync_execution_projection(session, desc)
    await session.commit()
    assert record.run_id == "current-run"
    assert record.state.value == "executing"
    assert record.close_status is None
    assert record.memo["summary"] == "Current run"
