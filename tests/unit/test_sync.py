import asyncio
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.core.sync import (
    map_temporal_state_to_projection,
    merged_memo_for_projection,
    merged_parameters_for_projection,
    sync_execution_projection,
)
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def test_map_temporal_state_to_projection_success():
    start_time = datetime.now(UTC)
    updated_at = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:123"
    desc.run_id = "run-123"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = start_time

    memo_data = {
        "entry": "run",
        "owner_id": "owner-1",
        "owner_type": "user",
        "input_ref": "input-1",
        "paused": True,
        "step_count": 5,
    }
    desc.memo = memo_data

    class MockSearchAttribute:
        def __init__(self, data):
            self.data = data

    desc.search_attributes = {
        "mm_repo": MockSearchAttribute("repo-1"),
        "mm_custom": MockSearchAttribute(b'{"key": "value"}'),
        "mm_updated_at": MockSearchAttribute(updated_at.isoformat()),
    }

    async def _memo() -> dict[str, object]:
        return memo_data

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["workflow_id"] == "mm:123"
    assert result["run_id"] == "run-123"
    assert result["namespace"] == "moonmind"
    assert result["workflow_type"] == TemporalWorkflowType.RUN
    assert result["owner_id"] == "owner-1"
    assert result["owner_type"] == TemporalExecutionOwnerType.USER
    assert result["state"] == MoonMindWorkflowState.COMPLETED
    assert result["close_status"] == TemporalExecutionCloseStatus.COMPLETED
    assert result["entry"] == "run"
    assert result["input_ref"] == "input-1"
    assert result["paused"] is True
    assert result["step_count"] == 5
    assert result["search_attributes"]["mm_repo"] == "repo-1"
    assert result["search_attributes"]["mm_custom"] == {"key": "value"}
    assert result["updated_at"] == updated_at


def test_map_temporal_state_to_projection_uses_search_attributes_for_owner_fields():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:456"
    desc.run_id = "run-456"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = None

    memo_data: dict[str, object] = {
        "entry": "run",
    }

    class MockSearchAttribute:
        def __init__(self, data):
            self.data = data

    desc.search_attributes = {
        "mm_owner_id": MockSearchAttribute(["owner-from-search"]),
        "mm_owner_type": MockSearchAttribute(["user"]),
    }

    async def _memo() -> dict[str, object]:
        return memo_data

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["owner_id"] == "owner-from-search"
    assert result["owner_type"] == TemporalExecutionOwnerType.USER


def test_map_temporal_state_to_projection_memo_parameters_empty_by_default():
    """Temporal memo typically does not contain targetRuntime/model/effort.

    These fields are stored in the canonical record's parameters column at
    creation time and must be merged during projection sync (see
    sync_execution_projection) to be visible in the API response.
    """
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:789"
    desc.run_id = "run-789"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.Run"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = None
    desc.search_attributes = {}

    memo_data: dict[str, object] = {
        "entry": "run",
        "title": "Some task",
        "summary": "Running",
    }

    async def _memo() -> dict[str, object]:
        return memo_data

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    # Memo doesn't contain these execution parameters; they live in the
    # canonical record's parameters column set at create_execution time.
    params = result["parameters"]
    assert params.get("targetRuntime") is None
    assert params.get("model") is None
    assert params.get("effort") is None


@pytest.mark.asyncio
async def test_sync_execution_projection_preserves_updated_at_when_mm_updated_at_missing(
    tmp_path,
):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base

    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            existing_updated_at = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
            existing_synced_at = datetime(2026, 3, 6, 12, 1, tzinfo=UTC)
            projection = TemporalExecutionRecord(
                workflow_id="mm:preserve-updated-at",
                run_id="run-1",
                namespace="moonmind",
                workflow_type=TemporalWorkflowType.RUN,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.EXECUTING,
                close_status=None,
                entry="run",
                search_attributes={"mm_state": "executing", "mm_entry": "run"},
                memo={"title": "Task"},
                artifact_refs=[],
                parameters={},
                projection_version=3,
                last_synced_at=existing_synced_at,
                sync_state=TemporalExecutionProjectionSyncState.STALE,
                sync_error="stale projection",
                started_at=existing_updated_at,
                updated_at=existing_updated_at,
                closed_at=None,
            )
            session.add(projection)
            await session.commit()

            desc = Mock(spec=WorkflowExecutionDescription)
            desc.id = projection.workflow_id
            desc.run_id = "run-1"
            desc.namespace = "moonmind"
            desc.workflow_type = "MoonMind.Run"
            desc.status = WorkflowExecutionStatus.RUNNING
            desc.start_time = existing_updated_at
            desc.execution_time = existing_updated_at
            desc.close_time = None
            desc.search_attributes = {"mm_state": "executing", "mm_entry": "run"}

            async def _memo() -> dict[str, object]:
                return {"entry": "run", "owner_id": "owner-1", "owner_type": "user"}

            desc.memo = _memo

            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)

            assert _as_utc(refreshed.updated_at) == existing_updated_at
            assert _as_utc(refreshed.last_synced_at) > existing_synced_at
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_execution_projection_uses_mm_updated_at_from_temporal(
    tmp_path,
):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base

    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            created_at = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
            canonical_updated_at = datetime(2026, 3, 6, 12, 5, tzinfo=UTC)
            session.add(
                TemporalExecutionCanonicalRecord(
                    workflow_id="mm:canonical-updated-at",
                    run_id="run-1",
                    namespace="moonmind",
                    workflow_type=TemporalWorkflowType.RUN,
                    owner_id="owner-1",
                    owner_type=TemporalExecutionOwnerType.USER,
                    state=MoonMindWorkflowState.EXECUTING,
                    close_status=None,
                    entry="run",
                    search_attributes={},
                    memo={"title": "Task"},
                    artifact_refs=[],
                    parameters={},
                    started_at=created_at,
                    updated_at=created_at,
                    closed_at=None,
                )
            )
            await session.commit()

            desc = Mock(spec=WorkflowExecutionDescription)
            desc.id = "mm:canonical-updated-at"
            desc.run_id = "run-1"
            desc.namespace = "moonmind"
            desc.workflow_type = "MoonMind.Run"
            desc.status = WorkflowExecutionStatus.RUNNING
            desc.start_time = created_at
            desc.execution_time = created_at
            desc.close_time = None
            desc.search_attributes = {
                "mm_state": "executing",
                "mm_entry": "run",
                "mm_updated_at": canonical_updated_at.isoformat(),
            }

            async def _memo() -> dict[str, object]:
                return {"entry": "run", "owner_id": "owner-1", "owner_type": "user"}

            desc.memo = _memo

            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)

            assert _as_utc(refreshed.updated_at) == canonical_updated_at
            assert _as_utc(refreshed.last_synced_at) >= canonical_updated_at
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_execution_projection_refreshes_canonical_summary_and_started_at(
    tmp_path,
):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base

    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            created_at = datetime(2026, 4, 9, 20, 44, tzinfo=UTC)
            started_at = datetime(2026, 4, 9, 20, 45, tzinfo=UTC)
            updated_at = datetime(2026, 4, 9, 20, 46, tzinfo=UTC)
            canonical = TemporalExecutionCanonicalRecord(
                workflow_id="mm:canonical-refresh",
                run_id="run-old",
                namespace="moonmind",
                workflow_type=TemporalWorkflowType.RUN,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                close_status=None,
                entry="run",
                search_attributes={"mm_state": "initializing", "mm_entry": "run"},
                memo={
                    "title": "Task",
                    "summary": "Execution initialized.",
                    "taskRunId": "8b376541-53ba-4d76-a18f-8366943550ec",
                },
                artifact_refs=[],
                parameters={"targetRuntime": "codex_cli"},
                create_idempotency_key="create-key-1",
                last_update_idempotency_key="update-key-1",
                last_update_response={"status": "accepted"},
                started_at=None,
                updated_at=created_at,
                closed_at=None,
            )
            session.add(canonical)
            await session.commit()

            desc = Mock(spec=WorkflowExecutionDescription)
            desc.id = canonical.workflow_id
            desc.run_id = "run-new"
            desc.namespace = "moonmind"
            desc.workflow_type = "MoonMind.Run"
            desc.status = WorkflowExecutionStatus.RUNNING
            desc.start_time = started_at
            desc.execution_time = started_at
            desc.close_time = None
            desc.search_attributes = {
                "mm_state": "executing",
                "mm_entry": "run",
                "mm_updated_at": updated_at.isoformat(),
            }

            async def _memo() -> dict[str, object]:
                return {
                    "entry": "run",
                    "owner_id": "owner-1",
                    "owner_type": "user",
                    "title": "Task",
                    "summary": "Launching agent...",
                }

            desc.memo = _memo

            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)
            await session.refresh(canonical)

            assert refreshed.memo["summary"] == "Launching agent..."
            assert refreshed.create_idempotency_key == "create-key-1"
            assert refreshed.last_update_idempotency_key == "update-key-1"
            assert refreshed.last_update_response == {"status": "accepted"}
            assert _as_utc(refreshed.started_at) == started_at
            assert canonical.run_id == "run-new"
            assert canonical.state == MoonMindWorkflowState.EXECUTING
            assert canonical.memo["summary"] == "Launching agent..."
            assert canonical.memo["taskRunId"] == "8b376541-53ba-4d76-a18f-8366943550ec"
            assert canonical.create_idempotency_key == "create-key-1"
            assert canonical.last_update_idempotency_key == "update-key-1"
            assert canonical.last_update_response == {"status": "accepted"}
            assert _as_utc(canonical.started_at) == started_at
            assert _as_utc(canonical.updated_at) == updated_at
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sync_execution_projection_preserves_metadata_when_temporal_memo_decode_fails(
    tmp_path,
):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base

    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            created_at = datetime(2026, 4, 9, 20, 44, tzinfo=UTC)
            started_at = datetime(2026, 4, 9, 20, 45, tzinfo=UTC)
            updated_at = datetime(2026, 4, 9, 20, 46, tzinfo=UTC)
            canonical = TemporalExecutionCanonicalRecord(
                workflow_id="mm:memo-decode-failure",
                run_id="run-old",
                namespace="moonmind",
                workflow_type=TemporalWorkflowType.RUN,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                close_status=None,
                entry="run",
                search_attributes={"mm_state": "initializing", "mm_entry": "run"},
                memo={"title": "Task", "summary": "Existing summary"},
                artifact_refs=[],
                input_ref="input-1",
                parameters={"targetRuntime": "codex_cli"},
                create_idempotency_key="create-key-2",
                last_update_idempotency_key="update-key-2",
                last_update_response={"status": "cached"},
                started_at=None,
                updated_at=created_at,
                closed_at=None,
            )
            projection = TemporalExecutionRecord(
                workflow_id="mm:memo-decode-failure",
                run_id="run-old",
                namespace="moonmind",
                workflow_type=TemporalWorkflowType.RUN,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                close_status=None,
                entry="run",
                search_attributes={"mm_state": "initializing", "mm_entry": "run"},
                memo={"title": "Task", "summary": "Existing summary"},
                artifact_refs=[],
                input_ref="input-1",
                parameters={"targetRuntime": "codex_cli"},
                create_idempotency_key="create-key-2",
                last_update_idempotency_key="update-key-2",
                last_update_response={"status": "cached"},
                projection_version=2,
                last_synced_at=created_at,
                sync_state=TemporalExecutionProjectionSyncState.STALE,
                sync_error="memo decode failed",
                started_at=None,
                updated_at=created_at,
                closed_at=None,
            )
            session.add(canonical)
            session.add(projection)
            await session.commit()

            desc = Mock(spec=WorkflowExecutionDescription)
            desc.id = canonical.workflow_id
            desc.run_id = "run-new"
            desc.namespace = "moonmind"
            desc.workflow_type = "MoonMind.Run"
            desc.status = WorkflowExecutionStatus.RUNNING
            desc.start_time = started_at
            desc.execution_time = started_at
            desc.close_time = None
            desc.search_attributes = {
                "mm_state": "executing",
                "mm_entry": "run",
                "mm_updated_at": updated_at.isoformat(),
            }

            async def _memo() -> dict[str, object]:
                raise RuntimeError("boom")

            desc.memo = _memo

            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)
            await session.refresh(canonical)

            assert refreshed.run_id == "run-new"
            assert refreshed.state == MoonMindWorkflowState.EXECUTING
            assert refreshed.memo["summary"] == "Existing summary"
            assert refreshed.input_ref == "input-1"
            assert refreshed.create_idempotency_key == "create-key-2"
            assert refreshed.last_update_idempotency_key == "update-key-2"
            assert refreshed.last_update_response == {"status": "cached"}
            assert _as_utc(refreshed.started_at) == started_at
            assert _as_utc(refreshed.updated_at) == updated_at
            assert canonical.run_id == "run-new"
            assert canonical.state == MoonMindWorkflowState.EXECUTING
            assert canonical.memo["summary"] == "Existing summary"
            assert canonical.input_ref == "input-1"
            assert canonical.create_idempotency_key == "create-key-2"
            assert canonical.last_update_idempotency_key == "update-key-2"
            assert canonical.last_update_response == {"status": "cached"}
            assert _as_utc(canonical.started_at) == started_at
            assert _as_utc(canonical.updated_at) == updated_at
    finally:
        await engine.dispose()


def test_merged_parameters_for_projection_combines_canonical_with_memo_payload():
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.parameters = {
        "targetRuntime": "codex",
        "task": {"tool": {"name": "fix-ci"}},
    }
    payload = {"parameters": {"model": "gpt-5"}}
    merged = merged_parameters_for_projection(payload, canonical)
    assert merged["targetRuntime"] == "codex"
    assert merged["task"]["tool"]["name"] == "fix-ci"
    assert merged["model"] == "gpt-5"


def test_merged_parameters_for_projection_without_canonical_returns_memo_only():
    payload = {"parameters": {"targetRuntime": "jules"}}
    assert merged_parameters_for_projection(payload, None) == {"targetRuntime": "jules"}


# --- merged_memo_for_projection tests ---

def test_merged_memo_preserves_db_only_keys_absent_from_temporal_memo():
    """DB-written keys like taskRunId survive projection sync even though
    Temporal's memo is immutable and will never contain them."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {
        "title": "My Task",
        "summary": "Running",
        "taskRunId": "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d",
    }
    # Temporal's memo is a subset — taskRunId is absent (it's immutable after start)
    temporal_payload = {"memo": {"title": "My Task", "summary": "Executing step 3"}}

    merged = merged_memo_for_projection(temporal_payload, canonical)

    # DB-only key survives
    assert merged["taskRunId"] == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"
    # Temporal's value wins for keys present in both
    assert merged["summary"] == "Executing step 3"
    assert merged["title"] == "My Task"


def test_merged_memo_temporal_wins_for_overlapping_keys():
    """Temporal is authoritative for any key it supplies."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {"summary": "Old summary", "taskRunId": "abc-123"}
    temporal_payload = {"memo": {"summary": "New summary from Temporal"}}

    merged = merged_memo_for_projection(temporal_payload, canonical)

    assert merged["summary"] == "New summary from Temporal"
    assert merged["taskRunId"] == "abc-123"


def test_merged_memo_without_canonical_returns_temporal_memo_only():
    payload = {"memo": {"title": "task", "summary": "done"}}
    merged = merged_memo_for_projection(payload, None)
    assert merged == {"title": "task", "summary": "done"}


def test_merged_memo_with_empty_temporal_memo_returns_canonical_memo():
    """When Temporal provides no memo (rare), the canonical DB memo is used."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {"taskRunId": "abc", "title": "task"}
    payload: dict = {"memo": {}}

    merged = merged_memo_for_projection(payload, canonical)
    assert merged["taskRunId"] == "abc"
    assert merged["title"] == "task"
