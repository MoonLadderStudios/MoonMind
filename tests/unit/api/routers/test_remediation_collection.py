"""Route-to-service coverage for the Remediation page inventory.

``GET /api/executions/remediations`` describes both executions of every link.
``describe_execution`` commits and rolls back the request's single
``AsyncSession``, so the route must never run those calls concurrently, and one
link that cannot be loaded must be reported instead of failing the page.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.executions import _get_service, router
from api_service.auth_providers import get_instance_access
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalExecutionRemediationLink,
    TemporalWorkflowType,
)
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.service import TemporalExecutionService


class _OverlapDetectingService(TemporalExecutionService):
    """Real service whose describe calls record any overlap on the session."""

    def __init__(self, session: AsyncSession, *, poisoned: set[str]) -> None:
        adapter = MagicMock()
        adapter.describe_workflow = AsyncMock(return_value=None)
        super().__init__(session, client_adapter=adapter)
        self._poisoned = poisoned
        self.in_flight = 0
        self.max_in_flight = 0
        self.described: list[str] = []

    async def describe_execution(self, workflow_id: str, **kwargs):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Yield so a concurrent caller sharing this session is observed.
            await asyncio.sleep(0)
            self.described.append(workflow_id)
            if workflow_id in self._poisoned:
                # A database failure mid-item: the flush fails and leaves the
                # shared session needing a rollback before it can be reused.
                self._session.add(
                    TemporalExecutionRemediationLink(remediation_workflow_id="x")
                )
                await self._session.flush()
            return await super().describe_execution(workflow_id, **kwargs)
        finally:
            self.in_flight -= 1


def _canonical(workflow_id: str, title: str) -> TemporalExecutionCanonicalRecord:
    return TemporalExecutionCanonicalRecord(
        workflow_id=workflow_id,
        run_id=f"run-{workflow_id}",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        entry="user_workflow",
        owner_id=str(uuid4()),
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        parameters={"workflow": {"instructions": title}},
        memo={"title": title},
    )


def _link(remediation_id: str, target_id: str, *, age_minutes: int):
    stamp = datetime(2026, 10, 17, 12, 0, tzinfo=UTC) - timedelta(minutes=age_minutes)
    return TemporalExecutionRemediationLink(
        remediation_workflow_id=remediation_id,
        remediation_run_id=f"run-{remediation_id}",
        target_workflow_id=target_id,
        target_run_id=f"run-{target_id}",
        mode="snapshot",
        authority_mode="approval_gated",
        status="executing",
        created_at=stamp,
        updated_at=stamp,
    )


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/remediations.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db_session:
            db_session.add_all(
                [
                    _canonical("mm:target-a", "Checkout release"),
                    _canonical("mm:target-b", "Billing release"),
                    _canonical("mm:repair-1", "Repair checkout"),
                    _canonical("mm:repair-2", "Repair billing"),
                    _canonical("mm:repair-3", "Repair checkout again"),
                    _canonical("mm:repair-broken", "Repair that cannot load"),
                ]
            )
            await db_session.flush()
            db_session.add_all(
                [
                    # Newest first: the failing link is processed before the
                    # others, so its rollback must not break later items.
                    _link("mm:repair-broken", "mm:target-a", age_minutes=0),
                    _link("mm:repair-1", "mm:target-a", age_minutes=1),
                    _link("mm:repair-2", "mm:target-b", age_minutes=2),
                    _link("mm:repair-3", "mm:target-a", age_minutes=3),
                ]
            )
            await db_session.commit()
            yield db_session
    finally:
        await engine.dispose()


async def _get_collection(service: _OverlapDetectingService, db_session: AsyncSession):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_service] = lambda: service

    async def _session_override():
        yield db_session

    app.dependency_overrides[get_async_session] = _session_override
    app.dependency_overrides[get_instance_access] = lambda: OperatorAdmission(
        via="loopback"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get("/api/executions/remediations")


@pytest.mark.asyncio
async def test_collection_describes_links_sequentially_on_the_shared_session(
    session: AsyncSession,
) -> None:
    service = _OverlapDetectingService(session, poisoned=set())

    response = await _get_collection(service, session)

    assert response.status_code == 200, response.text
    assert service.max_in_flight == 1
    payload = response.json()
    assert [item["remediationWorkflowId"] for item in payload["items"]] == [
        "mm:repair-broken",
        "mm:repair-1",
        "mm:repair-2",
        "mm:repair-3",
    ]
    assert payload["items"][1]["title"] == "Repair checkout"
    assert payload["items"][1]["targetTitle"] == "Checkout release"
    assert payload["items"][2]["targetTitle"] == "Billing release"
    assert payload["unavailableCount"] == 0
    assert payload["unavailableItems"] == []


@pytest.mark.asyncio
async def test_collection_reports_a_failing_link_as_a_partial_result(
    session: AsyncSession,
) -> None:
    service = _OverlapDetectingService(session, poisoned={"mm:repair-broken"})

    response = await _get_collection(service, session)

    assert response.status_code == 200, response.text
    assert service.max_in_flight == 1
    payload = response.json()
    assert [item["remediationWorkflowId"] for item in payload["items"]] == [
        "mm:repair-1",
        "mm:repair-2",
        "mm:repair-3",
    ]
    assert [item["targetTitle"] for item in payload["items"]] == [
        "Checkout release",
        "Billing release",
        "Checkout release",
    ]
    assert payload["unavailableCount"] == 1
    [unavailable] = payload["unavailableItems"]
    assert unavailable["remediationWorkflowId"] == "mm:repair-broken"
    assert unavailable["targetWorkflowId"] == "mm:target-a"
    assert unavailable["code"] == "remediation_item_unavailable"
    # Compact diagnostics only: the error type, never the raw database text.
    assert "IntegrityError" in unavailable["message"]
    assert "INSERT" not in unavailable["message"]
    assert "NOT NULL" not in unavailable["message"]


@pytest.mark.asyncio
async def test_collection_still_omits_relationships_whose_executions_are_not_visible(
    session: AsyncSession,
) -> None:
    session.add(_link("mm:repair-orphan", "mm:target-missing", age_minutes=10))
    await session.commit()
    service = _OverlapDetectingService(session, poisoned=set())

    response = await _get_collection(service, session)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "mm:repair-orphan" not in {
        item["remediationWorkflowId"] for item in payload["items"]
    }
    assert payload["unavailableCount"] == 0
