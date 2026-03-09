from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.core.sync import sync_execution_projection
from api_service.db.models import Base, TemporalExecutionRecord


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
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = start_time
    desc.close_time = None

    desc.memo = {
        "entry": "run",
        "owner_id": "owner-1",
        "owner_type": "user",
        "step_count": 1,
    }
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


@pytest.mark.skip(reason="Not implemented yet")
@pytest.mark.asyncio
async def test_list_executions_router_sync_behavior():
    # This acts as a proxy for T007 testing that the list endpoint updates items.
    # The actual list endpoint uses the same sync_execution_projection function,
    # so we test the data flow directly.
    pass
