"""Unit regressions for issue #3946 (shared projection mutator).

Test-substitution label (REQ-07): the DB-backed tests in this module run
against SQLite (aiosqlite, one session per test via ``tmp_path``). SQLite
proves the mutator's ordering, field-authority, savepoint, and freshness
rules at unit level; it does NOT prove deployed-PostgreSQL isolation
(two independent transactions, insert-conflict/retry incl. source-less
executions) or Temporal-backed run-chain/outage wiring. Those deployed
qualifications remain explicitly unexecuted here and must be proven through
the hermetic integration suites under a ``moonmind-test`` compose project
(coordinate #3950).
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.core.sync import (
    _artifact_ref_from_memo,
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
    desc.workflow_type = "MoonMind.UserWorkflow"
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
    assert result["workflow_type"] == TemporalWorkflowType.USER_WORKFLOW
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

def test_map_temporal_completed_with_canceled_mm_state_projects_canceled():
    start_time = datetime.now(UTC)
    updated_at = datetime(2026, 6, 27, 17, 0, tzinfo=UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:canceled-via-update"
    desc.run_id = "run-canceled-via-update"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = updated_at
    desc.search_attributes = {
        "mm_state": "canceled",
        "mm_entry": "run",
        "mm_updated_at": updated_at.isoformat(),
    }

    async def _memo() -> dict[str, object]:
        return {
            "entry": "run",
            "owner_id": "owner-1",
            "owner_type": "user",
            "summary": "Execution canceled.",
        }

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["state"] == MoonMindWorkflowState.CANCELED
    assert result["close_status"] == TemporalExecutionCloseStatus.CANCELED
    assert result["memo"]["summary"] == "Execution canceled."
    assert result["updated_at"] == updated_at

def test_map_temporal_state_to_projection_extracts_finish_summary():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:finish-summary"
    desc.run_id = "run-finish-summary"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = start_time
    desc.search_attributes = {}
    finish_summary = {
        "schemaVersion": "v1",
        "finishOutcome": {
            "code": "PUBLISHED_BRANCH",
            "stage": "publish",
            "reason": "published branch",
        },
    }

    async def _memo() -> dict[str, object]:
        return {"entry": "run", "finishSummary": finish_summary}

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["finish_outcome_code"] == "PUBLISHED_BRANCH"
    assert result["finish_summary_json"] == finish_summary


def test_map_temporal_state_to_projection_uses_plan_artifact_ref_when_plan_ref_missing():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:plan-artifact-ref"
    desc.run_id = "run-plan-artifact-ref"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.FAILED
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = start_time
    desc.search_attributes = {}

    async def _memo() -> dict[str, object]:
        return {
            "entry": "run",
            "plan_artifact_ref": {"artifact_id": "artifact://plan/source"},
        }

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["plan_ref"] == "artifact://plan/source"


def test_map_temporal_state_to_projection_extracts_snake_case_finish_outcome():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:finish-summary-snake"
    desc.run_id = "run-finish-summary-snake"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.COMPLETED
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = start_time
    desc.search_attributes = {}
    finish_summary = {
        "schema_version": "v1",
        "finish_outcome": {
            "code": "PUBLISH_DISABLED",
            "stage": "publish",
            "reason": "publishing disabled",
        },
    }

    async def _memo() -> dict[str, object]:
        return {"entry": "run", "finish_summary": finish_summary}

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["finish_outcome_code"] == "PUBLISH_DISABLED"
    assert result["finish_summary_json"] == finish_summary


def test_map_temporal_state_to_projection_uses_search_attributes_for_owner_fields():
    start_time = datetime.now(UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:456"
    desc.run_id = "run-456"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
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
    desc.workflow_type = "MoonMind.UserWorkflow"
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
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
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
            desc.workflow_type = "MoonMind.UserWorkflow"
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
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
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
            desc.workflow_type = "MoonMind.UserWorkflow"
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
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                close_status=None,
                entry="run",
                search_attributes={"mm_state": "initializing", "mm_entry": "run"},
                memo={
                    "title": "Task",
                    "summary": "Execution initialized.",
                    "agentRunId": "8b376541-53ba-4d76-a18f-8366943550ec",
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
            desc.workflow_type = "MoonMind.UserWorkflow"
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
            assert canonical.memo["agentRunId"] == "8b376541-53ba-4d76-a18f-8366943550ec"
            assert canonical.create_idempotency_key == "create-key-1"
            assert canonical.last_update_idempotency_key == "update-key-1"
            assert canonical.last_update_response == {"status": "accepted"}
            assert _as_utc(canonical.started_at) == started_at
            assert _as_utc(canonical.updated_at) == updated_at
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_sync_execution_projection_repairs_completed_projection_for_graceful_cancel(
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
            created_at = datetime(2026, 6, 27, 16, 55, tzinfo=UTC)
            canceled_at = datetime(2026, 6, 27, 17, 0, tzinfo=UTC)
            canonical = TemporalExecutionCanonicalRecord(
                workflow_id="mm:graceful-cancel-refresh",
                run_id="run-1",
                namespace="moonmind",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.CANCELED,
                close_status=TemporalExecutionCloseStatus.CANCELED,
                entry="run",
                search_attributes={"mm_state": "canceled", "mm_entry": "run"},
                memo={"title": "Task", "summary": "Execution canceled."},
                artifact_refs=[],
                parameters={"targetRuntime": "codex_cli"},
                started_at=created_at,
                updated_at=canceled_at,
                closed_at=canceled_at,
            )
            projection = TemporalExecutionRecord(
                workflow_id=canonical.workflow_id,
                run_id="run-1",
                namespace="moonmind",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.COMPLETED,
                close_status=TemporalExecutionCloseStatus.COMPLETED,
                entry="run",
                search_attributes={"mm_state": "completed", "mm_entry": "run"},
                memo={"title": "Task", "summary": "Execution canceled."},
                artifact_refs=[],
                parameters={"targetRuntime": "codex_cli"},
                projection_version=1,
                last_synced_at=created_at,
                sync_state=TemporalExecutionProjectionSyncState.FRESH,
                sync_error=None,
                started_at=created_at,
                updated_at=canceled_at,
                closed_at=canceled_at,
            )
            session.add(canonical)
            session.add(projection)
            await session.commit()

            desc = Mock(spec=WorkflowExecutionDescription)
            desc.id = canonical.workflow_id
            desc.run_id = "run-1"
            desc.namespace = "moonmind"
            desc.workflow_type = "MoonMind.UserWorkflow"
            desc.status = WorkflowExecutionStatus.COMPLETED
            desc.start_time = created_at
            desc.execution_time = created_at
            desc.close_time = canceled_at
            desc.search_attributes = {
                "mm_state": "canceled",
                "mm_entry": "run",
                "mm_updated_at": canceled_at.isoformat(),
            }

            async def _memo() -> dict[str, object]:
                return {
                    "entry": "run",
                    "owner_id": "owner-1",
                    "owner_type": "user",
                    "title": "Task",
                    "summary": "Execution canceled.",
                }

            desc.memo = _memo

            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)
            await session.refresh(canonical)

            assert refreshed.state == MoonMindWorkflowState.CANCELED
            assert refreshed.close_status == TemporalExecutionCloseStatus.CANCELED
            assert refreshed.memo["summary"] == "Execution canceled."
            assert canonical.state == MoonMindWorkflowState.CANCELED
            assert canonical.close_status == TemporalExecutionCloseStatus.CANCELED
            assert canonical.memo["summary"] == "Execution canceled."
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
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
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
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                close_status=None,
                entry="run",
                search_attributes={"mm_state": "initializing", "mm_entry": "run"},
                memo={"title": "Task", "summary": "Existing summary"},
                artifact_refs=[],
                finish_outcome_code="FAILED",
                finish_summary_json={
                    "schemaVersion": "v1",
                    "finishOutcome": {"code": "FAILED"},
                },
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
            desc.workflow_type = "MoonMind.UserWorkflow"
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
            assert refreshed.finish_outcome_code == "FAILED"
            assert refreshed.finish_summary_json == {
                "schemaVersion": "v1",
                "finishOutcome": {"code": "FAILED"},
            }
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
    """DB-written keys like agentRunId survive projection sync even though
    Temporal's memo is immutable and will never contain them."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {
        "title": "My Task",
        "summary": "Running",
        "agentRunId": "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d",
    }
    # Temporal's memo is a subset — agentRunId is absent (it's immutable after start)
    temporal_payload = {"memo": {"title": "My Task", "summary": "Executing step 3"}}

    merged = merged_memo_for_projection(temporal_payload, canonical)

    # DB-only key survives
    assert merged["agentRunId"] == "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"
    # Temporal's value wins for keys present in both
    assert merged["summary"] == "Executing step 3"
    assert merged["title"] == "My Task"

def test_merged_memo_temporal_wins_for_overlapping_keys():
    """Temporal is authoritative for any key it supplies."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {"summary": "Old summary", "agentRunId": "abc-123"}
    temporal_payload = {"memo": {"summary": "New summary from Temporal"}}

    merged = merged_memo_for_projection(temporal_payload, canonical)

    assert merged["summary"] == "New summary from Temporal"
    assert merged["agentRunId"] == "abc-123"

def test_merged_memo_without_canonical_returns_temporal_memo_only():
    payload = {"memo": {"title": "task", "summary": "done"}}
    merged = merged_memo_for_projection(payload, None)
    assert merged == {"title": "task", "summary": "done"}

def test_merged_memo_with_empty_temporal_memo_returns_canonical_memo():
    """When Temporal provides no memo (rare), the canonical DB memo is used."""
    from types import SimpleNamespace

    canonical = SimpleNamespace()
    canonical.memo = {"agentRunId": "abc", "title": "task"}
    payload: dict = {"memo": {}}

    merged = merged_memo_for_projection(payload, canonical)
    assert merged["agentRunId"] == "abc"
    assert merged["title"] == "task"

# --- mm_started_at semantic timestamp tests ---

def _make_running_desc(
    *,
    workflow_id: str,
    start_time: datetime,
    search_attributes: dict[str, "object"],
    memo_data: dict[str, "object"] | None = None,
) -> Mock:
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = workflow_id
    desc.run_id = f"run-{workflow_id}"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.RUNNING
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = None
    desc.search_attributes = search_attributes

    payload = memo_data or {"entry": "run", "owner_id": "owner-1", "owner_type": "user"}

    async def _memo() -> dict[str, object]:
        return payload

    desc.memo = _memo
    return desc

def test_started_at_is_none_for_awaiting_slot_without_mm_started_at():
    """Running Temporal workflow with mm_state=awaiting_slot must not surface
    a started_at: Temporal's start_time fires when the workflow is created,
    even while it is still waiting for a provider profile slot."""
    start_time = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    desc = _make_running_desc(
        workflow_id="mm:awaiting-slot",
        start_time=start_time,
        search_attributes={
            "mm_state": "awaiting_slot",
            "mm_entry": "run",
        },
    )

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["state"] == MoonMindWorkflowState.AWAITING_SLOT
    assert result["started_at"] is None

def test_started_at_uses_mm_started_at_when_present():
    """When the workflow has stamped mm_started_at, the projection prefers
    it over Temporal's lifecycle timestamps."""
    start_time = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    semantic_started_at = datetime(2026, 5, 1, 12, 7, tzinfo=UTC)
    desc = _make_running_desc(
        workflow_id="mm:executing-with-mm-started",
        start_time=start_time,
        search_attributes={
            "mm_state": "executing",
            "mm_entry": "run",
            "mm_started_at": semantic_started_at.isoformat(),
        },
    )

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["state"] == MoonMindWorkflowState.EXECUTING
    assert _as_utc(result["started_at"]) == semantic_started_at

def test_started_at_legacy_fallback_for_executing_without_mm_started_at():
    """In-flight workflows that pre-date mm_started_at must keep working:
    when the workflow is in a real-work state but no semantic timestamp is
    available, fall back to the Temporal lifecycle timestamp."""
    start_time = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    desc = _make_running_desc(
        workflow_id="mm:executing-legacy",
        start_time=start_time,
        search_attributes={
            "mm_state": "executing",
            "mm_entry": "run",
        },
    )

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["state"] == MoonMindWorkflowState.EXECUTING
    assert _as_utc(result["started_at"]) == start_time

def test_started_at_none_for_planning_state():
    """PLANNING is a pre-work state; no started_at without mm_started_at."""
    start_time = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    desc = _make_running_desc(
        workflow_id="mm:planning",
        start_time=start_time,
        search_attributes={
            "mm_state": "planning",
            "mm_entry": "run",
        },
    )

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["state"] == MoonMindWorkflowState.PLANNING
    assert result["started_at"] is None

def test_started_at_persists_through_awaiting_slot_after_work_began():
    """Cooldown / requeue can return the workflow to awaiting_slot after work
    has already begun. Once mm_started_at is stamped, it must keep winning."""
    start_time = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    semantic_started_at = datetime(2026, 5, 1, 12, 5, tzinfo=UTC)
    desc = _make_running_desc(
        workflow_id="mm:cooldown-requeue",
        start_time=start_time,
        search_attributes={
            "mm_state": "awaiting_slot",
            "mm_entry": "run",
            "mm_started_at": semantic_started_at.isoformat(),
        },
    )

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["state"] == MoonMindWorkflowState.AWAITING_SLOT
    assert _as_utc(result["started_at"]) == semantic_started_at


def test_artifact_ref_from_memo_returns_string_ref():
    memo = {"plan_artifact_ref": {"artifact_id": "artifact://plan/source"}}

    assert _artifact_ref_from_memo(memo, "plan_artifact_ref") == "artifact://plan/source"


def test_artifact_ref_from_memo_ignores_non_string_ref_values():
    """Nested dicts/lists under a ref key must not be stringified into an
    invalid artifact reference; only genuine string refs are returned."""
    memo = {
        "plan_artifact_ref": {
            "artifact_id": {"nested": "value"},
            "ref": ["artifact://plan/list"],
        }
    }

    assert _artifact_ref_from_memo(memo, "plan_artifact_ref") is None


def test_artifact_ref_from_memo_skips_non_string_then_finds_valid_ref():
    memo = {
        "plan_artifact_ref": {
            "artifactId": {"nested": "value"},
            "id": "artifact://plan/id",
        }
    }

    assert _artifact_ref_from_memo(memo, "plan_artifact_ref") == "artifact://plan/id"


# --- Issue #3946 regression coverage: field authority, run-chain ordering,
# --- transaction isolation, and bounded truthful freshness.


def _sqlite_session_factory(tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base

    db_url = f"sqlite+aiosqlite:///{tmp_path}/test-3946.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


def _seed_execution(session, workflow_id, *, run_id="run-1", updated_at=None,
                    state=None, close_status=None, owner_id="owner-1",
                    namespace="moonmind", parameters=None, refs=None, memo=None,
                    version=1):
    canonical = TemporalExecutionCanonicalRecord(
        workflow_id=workflow_id,
        run_id=run_id,
        namespace=namespace,
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=owner_id,
        owner_type=TemporalExecutionOwnerType.USER,
        state=state or MoonMindWorkflowState.EXECUTING,
        close_status=close_status,
        entry="run",
        search_attributes={},
        memo=dict(memo or {"title": "Task"}),
        artifact_refs=list(refs or []),
        parameters=dict(parameters or {"targetRuntime": "codex_cli"}),
        started_at=updated_at,
        updated_at=updated_at,
        closed_at=None,
    )
    projection = TemporalExecutionRecord(
        workflow_id=workflow_id,
        run_id=run_id,
        namespace=namespace,
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=owner_id,
        owner_type=TemporalExecutionOwnerType.USER,
        state=state or MoonMindWorkflowState.EXECUTING,
        close_status=close_status,
        entry="run",
        search_attributes={},
        memo=dict(memo or {"title": "Task"}),
        artifact_refs=list(refs or []),
        parameters=dict(parameters or {"targetRuntime": "codex_cli"}),
        projection_version=version,
        last_synced_at=updated_at,
        sync_state=TemporalExecutionProjectionSyncState.FRESH,
        sync_error=None,
        started_at=updated_at,
        updated_at=updated_at,
        closed_at=None,
    )
    session.add(canonical)
    session.add(projection)
    return canonical, projection


def _temporal_desc(*, workflow_id, run_id, updated_at, memo, namespace="moonmind",
                   status=None, close_time=None):
    # Plain Mock (no spec): run-chain hints such as previous_run_id are
    # optional Temporal SDK attributes that a spec'd mock would reject.
    desc = Mock()
    desc.id = workflow_id
    # Optional run-chain linkage defaults to absent; individual tests set it
    # explicitly. (A plain Mock would otherwise auto-create truthy child mocks
    # for these optional SDK attributes.)
    desc.previous_run_id = None
    desc.first_execution_run_id = None
    desc.run_id = run_id
    desc.namespace = namespace
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = status or WorkflowExecutionStatus.RUNNING
    desc.start_time = updated_at
    desc.execution_time = updated_at
    desc.close_time = close_time
    search = {"mm_state": "executing", "mm_entry": "run"}
    if updated_at is not None:
        search["mm_updated_at"] = updated_at.isoformat()
    desc.search_attributes = search

    async def _memo() -> dict[str, object]:
        return dict(memo)

    desc.memo = _memo
    return desc


@pytest.mark.asyncio
async def test_temporal_observation_cannot_change_owner_or_admission_parameters(tmp_path):
    """REQ-02: a Temporal observation refreshes lifecycle without changing the
    authorized principal, namespace identity, or immutable admission params."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:field-authority", updated_at=stored_at)
            await session.commit()

            newer = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:field-authority",
                run_id="run-1",
                updated_at=newer,
                namespace="other-namespace",
                memo={
                    "entry": "run",
                    "owner_id": "owner-2",
                    "owner_type": "system",
                    "parameters": {"targetRuntime": "other_runtime"},
                },
            )
            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.owner_id == "owner-1"
            assert refreshed.owner_type == TemporalExecutionOwnerType.USER
            assert refreshed.namespace == "moonmind"
            assert refreshed.parameters == {"targetRuntime": "codex_cli"}
            assert refreshed.state == MoonMindWorkflowState.EXECUTING
            assert _as_utc(refreshed.updated_at) == newer
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_temporal_observation_may_introduce_new_parameter_keys(tmp_path):
    """REQ-02: Temporal memo parameters may add keys the canonical record does
    not own, but stored admission values still win on conflict."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:param-additive", updated_at=stored_at)
            await session.commit()

            newer = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:param-additive",
                run_id="run-1",
                updated_at=newer,
                memo={
                    "entry": "run",
                    "owner_id": "owner-1",
                    "owner_type": "user",
                    "parameters": {
                        "targetRuntime": "other_runtime",
                        "freshKey": "fresh-value",
                    },
                },
            )
            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.parameters["targetRuntime"] == "codex_cli"
            assert refreshed.parameters["freshKey"] == "fresh-value"
    finally:
        await engine.dispose()


def test_continued_as_new_does_not_map_to_logical_completion():
    """REQ-03: CONTINUED_AS_NEW closes one run but continues the logical
    workflow; it must not read as terminal COMPLETED."""
    start_time = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    desc = Mock(spec=WorkflowExecutionDescription)
    desc.id = "mm:continued"
    desc.run_id = "run-1"
    desc.namespace = "moonmind"
    desc.workflow_type = "MoonMind.UserWorkflow"
    desc.status = WorkflowExecutionStatus.CONTINUED_AS_NEW
    desc.start_time = start_time
    desc.execution_time = start_time
    desc.close_time = start_time
    desc.search_attributes = {"mm_state": "executing", "mm_entry": "run"}

    async def _memo() -> dict[str, object]:
        return {"entry": "run", "owner_id": "owner-1", "owner_type": "user"}

    desc.memo = _memo

    result = asyncio.run(map_temporal_state_to_projection(desc))

    assert result["state"] == MoonMindWorkflowState.EXECUTING
    assert result["close_status"] == TemporalExecutionCloseStatus.CONTINUED_AS_NEW
    assert result["continued_as_new"] is True


@pytest.mark.asyncio
async def test_equal_timestamp_across_runs_yields_stale_without_successor_evidence(tmp_path):
    """REQ-03: equal semantic timestamps across runs prove nothing; arrival
    time is not successor evidence, so the projection stays on the current run
    with reconciliation-needed status."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stamped = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _, projection = _seed_execution(
                session, "mm:equal-ts", run_id="run-1", updated_at=stamped, version=4,
            )
            await session.commit()

            desc = _temporal_desc(
                workflow_id="mm:equal-ts",
                run_id="run-2",
                updated_at=stamped,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )
            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-1"
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.STALE
            assert refreshed.sync_error == "successor_run_unverified_reconciliation_needed"
            assert refreshed.projection_version == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_successor_with_previous_run_link_replaces_on_equal_timestamps(tmp_path):
    """REQ-03: positive run-chain evidence (previous_run_id) lets the successor
    advance the projection even when semantic timestamps are equal."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stamped = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:successor-link", run_id="run-1", updated_at=stamped)
            await session.commit()

            desc = _temporal_desc(
                workflow_id="mm:successor-link",
                run_id="run-2",
                updated_at=stamped,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )
            desc.previous_run_id = "run-1"
            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-2"
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_timestamps_across_runs_yield_stale(tmp_path):
    """REQ-03: a different run without a reliable timestamp on either side
    needs positive run-chain evidence before replacing the projection."""
    from api_service.core.sync import mutate_execution_projection
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:missing-ts", run_id="run-1", updated_at=stored_at)
            await session.commit()

            refreshed = await mutate_execution_projection(
                session,
                workflow_id="mm:missing-ts",
                payload={
                    "workflow_id": "mm:missing-ts",
                    "run_id": "run-2",
                    "namespace": "moonmind",
                    "workflow_type": TemporalWorkflowType.USER_WORKFLOW,
                    "owner_id": "owner-1",
                    "owner_type": TemporalExecutionOwnerType.USER,
                    "state": MoonMindWorkflowState.EXECUTING,
                    "close_status": None,
                    "entry": "run",
                    "search_attributes": {},
                    "memo": {"title": "Task"},
                    "artifact_refs": [],
                    "parameters": {},
                    "updated_at": None,
                },
                owner="temporal",
            )
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-1"
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.STALE
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_batch_item_failure_does_not_poison_sibling(tmp_path, monkeypatch):
    """REQ-04: one item's database/fetch error must not poison unrelated
    repairs in the same shared-session batch."""
    from types import SimpleNamespace

    from api_service.core.sync import (
        sync_execution_projection,
        sync_temporal_executions_safely,
    )
    from api_service.db.models import Base
    import api_service.core.sync as sync_module

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:batch-good", updated_at=stored_at)
            await session.commit()

            newer = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            good_desc = _temporal_desc(
                workflow_id="mm:batch-good",
                run_id="run-1",
                updated_at=newer,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )

            async def _fake_fetch(sess, workflow_id, client):
                if workflow_id == "mm:batch-bad":
                    raise RuntimeError("temporal unavailable")
                return await sync_execution_projection(sess, good_desc)

            monkeypatch.setattr(
                sync_module, "fetch_and_sync_execution", _fake_fetch,
            )
            items = [
                SimpleNamespace(workflow_id="mm:batch-bad"),
                SimpleNamespace(workflow_id="mm:batch-good"),
            ]
            results = await sync_temporal_executions_safely(session, items, object())

            assert len(results) == 2
            assert results[0].workflow_id == "mm:batch-bad"
            refreshed = results[1]
            await session.refresh(refreshed)
            assert _as_utc(refreshed.updated_at) == newer
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_single_item_failure_rolls_back_and_keeps_session_usable(tmp_path):
    """REQ-04: the single-item wrapper must roll back its failed transaction
    so the session can process later work."""
    from api_service.core.sync import (
        sync_execution_projection,
        sync_single_temporal_execution_safely,
    )
    from api_service.db.models import Base
    import api_service.core.sync as sync_module

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:single-usable", updated_at=stored_at)
            await session.commit()

            async def _boom(sess, workflow_id, client):
                raise RuntimeError("temporal down")

            original = sync_module.fetch_and_sync_execution
            sync_module.fetch_and_sync_execution = _boom  # type: ignore[assignment]
            try:
                outcome = await sync_single_temporal_execution_safely(
                    session, "mm:single-usable", object()
                )
            finally:
                sync_module.fetch_and_sync_execution = original  # type: ignore[assignment]
            assert outcome is None

            newer = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:single-usable",
                run_id="run-1",
                updated_at=newer,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )
            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)
            assert _as_utc(refreshed.updated_at) == newer
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_observation_does_not_bump_version(tmp_path):
    """REQ-05: duplicate observations preserve freshness without producing
    another meaningful revision."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:duplicate", updated_at=stored_at, version=7)
            await session.commit()

            memo = {"entry": "run", "owner_id": "owner-1", "owner_type": "user", "title": "Task"}
            first = await sync_execution_projection(
                session,
                _temporal_desc(
                    workflow_id="mm:duplicate", run_id="run-1",
                    updated_at=stored_at, memo=memo,
                ),
            )
            await session.commit()
            await session.refresh(first)
            # First observation merges Temporal memo keys into the stored memo:
            # one meaningful revision.
            assert first.projection_version == 8

            second = await sync_execution_projection(
                session,
                _temporal_desc(
                    workflow_id="mm:duplicate", run_id="run-1",
                    updated_at=stored_at, memo=memo,
                ),
            )
            await session.commit()
            await session.refresh(second)
            assert second.projection_version == 8
            assert second.sync_state is TemporalExecutionProjectionSyncState.FRESH
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_observation_does_not_grow_refs_or_claim_fresh(tmp_path):
    """REQ-05: stale observations must not union their refs into history or
    overstate freshness; historical refs never become the current result."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            _seed_execution(
                session, "mm:stale-refs", updated_at=stored_at,
                refs=["art_current"], version=3,
            )
            await session.commit()

            older = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:stale-refs",
                run_id="run-9",
                updated_at=older,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )
            payload = await map_temporal_state_to_projection(desc)
            payload["artifact_refs"] = ["art_stale_historical"]
            loaded = bool(payload.pop("_temporal_memo_loaded", False)) and bool(payload.get("memo"))
            from api_service.core.sync import mutate_execution_projection

            refreshed = await mutate_execution_projection(
                session, workflow_id="mm:stale-refs", payload=payload,
                owner="temporal", metadata_loaded=loaded,
            )
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.artifact_refs == ["art_current"]
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.STALE
            assert refreshed.projection_version == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_partial_decode_preserves_fields_without_claiming_current(tmp_path):
    """REQ-05: a partially decoded observation preserves valid fields but is
    reported as repair-pending, never as fully current."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(
                session, "mm:partial", updated_at=stored_at,
                memo={"title": "Task", "summary": "Existing summary"}, version=2,
            )
            await session.commit()

            newer = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:partial",
                run_id="run-2",
                updated_at=newer,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )
            payload = await map_temporal_state_to_projection(desc)
            from api_service.core.sync import mutate_execution_projection

            refreshed = await mutate_execution_projection(
                session, workflow_id="mm:partial", payload=payload,
                owner="temporal", metadata_loaded=False,
            )
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-2"
            assert refreshed.memo.get("summary") == "Existing summary"
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.REPAIR_PENDING
            assert refreshed.sync_error == "temporal_memo_decode_incomplete"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_artifact_refs_are_bounded(tmp_path):
    """REQ-05: the inline current-summary ref list is capped; overflow stays
    behind artifact linkage/history instead of growing history metadata."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(
                session, "mm:bounded-refs", updated_at=stored_at,
                refs=[f"art_{i}" for i in range(120)],
            )
            await session.commit()

            newer = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:bounded-refs",
                run_id="run-1",
                updated_at=newer,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )
            payload = await map_temporal_state_to_projection(desc)
            payload["artifact_refs"] = [f"art_new_{i}" for i in range(30)]
            loaded = bool(payload.pop("_temporal_memo_loaded", False)) and bool(payload.get("memo"))
            from api_service.core.sync import (
                _MAX_PROJECTION_ARTIFACT_REFS,
                mutate_execution_projection,
            )

            refreshed = await mutate_execution_projection(
                session, workflow_id="mm:bounded-refs", payload=payload,
                owner="temporal", metadata_loaded=loaded,
            )
            await session.commit()
            await session.refresh(refreshed)

            assert len(refreshed.artifact_refs) == _MAX_PROJECTION_ARTIFACT_REFS
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_canonical_mutation_rejects_wrong_owner_namespace_type(tmp_path):
    """REQ-02: canonical payload merging must reject wrong-owner, wrong-
    namespace, and wrong-type changes under explicit field policy."""
    import pytest as _pytest

    from api_service.core.sync import mutate_execution_projection
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:canonical-authority", updated_at=stored_at)
            await session.commit()

            base_payload = {
                "workflow_id": "mm:canonical-authority",
                "run_id": "run-1",
                "namespace": "moonmind",
                "workflow_type": TemporalWorkflowType.USER_WORKFLOW,
                "owner_id": "owner-1",
                "owner_type": TemporalExecutionOwnerType.USER,
                "state": MoonMindWorkflowState.EXECUTING,
                "close_status": None,
                "entry": "run",
                "search_attributes": {},
                "memo": {"title": "Task"},
                "artifact_refs": [],
                "parameters": {"targetRuntime": "codex_cli"},
                "updated_at": stored_at,
            }
            for field, bad in (
                ("owner_id", "owner-2"),
                ("namespace", "other-namespace"),
            ):
                payload = dict(base_payload)
                payload[field] = bad
                with _pytest.raises(ValueError, match="protected field"):
                    await mutate_execution_projection(
                        session,
                        workflow_id="mm:canonical-authority",
                        payload=payload,
                        owner="canonical",
                    )
                await session.rollback()
            # A move to a non-product workflow type is rejected (either by
            # explicit canonical field policy or by product-projection scope).
            payload = dict(base_payload)
            payload["workflow_type"] = TemporalWorkflowType.MANIFEST_INGEST
            with _pytest.raises(ValueError):
                await mutate_execution_projection(
                    session,
                    workflow_id="mm:canonical-authority",
                    payload=payload,
                    owner="canonical",
                )
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_canonical_mutation_rejects_immutable_creation_key(tmp_path):
    """REQ-02: canonical writes may not rewrite immutable creation keys."""
    import pytest as _pytest

    from api_service.core.sync import mutate_execution_projection
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            canonical, _ = _seed_execution(
                session, "mm:canonical-immutable", updated_at=stored_at,
            )
            canonical.create_idempotency_key = "key-1"
            await session.commit()

            with _pytest.raises(ValueError, match="immutable field"):
                await mutate_execution_projection(
                    session,
                    workflow_id="mm:canonical-immutable",
                    payload={
                        "workflow_id": "mm:canonical-immutable",
                        "run_id": "run-1",
                        "namespace": "moonmind",
                        "workflow_type": TemporalWorkflowType.USER_WORKFLOW,
                        "owner_id": "owner-1",
                        "owner_type": TemporalExecutionOwnerType.USER,
                        "state": MoonMindWorkflowState.EXECUTING,
                        "close_status": None,
                        "entry": "run",
                        "search_attributes": {},
                        "memo": {"title": "Task"},
                        "artifact_refs": [],
                        "parameters": {"targetRuntime": "codex_cli"},
                        "create_idempotency_key": "key-2",
                        "updated_at": stored_at,
                    },
                    owner="canonical",
                )
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_late_predecessor_observation_stays_stale(tmp_path):
    """REQ-03: a late predecessor observation (incoming run equals the stored
    run-chain previous_run_id) stays on the current run, never replacing it."""
    from api_service.core.sync import mutate_execution_projection
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            _seed_execution(
                session, "mm:late-predecessor", run_id="run-2",
                updated_at=stored_at,
                memo={"title": "Task", "previous_run_id": "run-1"},
            )
            await session.commit()

            older = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            refreshed = await mutate_execution_projection(
                session,
                workflow_id="mm:late-predecessor",
                payload={
                    "workflow_id": "mm:late-predecessor",
                    "run_id": "run-1",
                    "namespace": "moonmind",
                    "workflow_type": TemporalWorkflowType.USER_WORKFLOW,
                    "owner_id": "owner-1",
                    "owner_type": TemporalExecutionOwnerType.USER,
                    "state": MoonMindWorkflowState.EXECUTING,
                    "close_status": None,
                    "entry": "run",
                    "search_attributes": {},
                    "memo": {"title": "Task"},
                    "artifact_refs": [],
                    "parameters": {},
                    "updated_at": older,
                },
                owner="temporal",
            )
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-2"
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.STALE
            assert refreshed.sync_error == "stale_temporal_observation_ignored"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_strictly_newer_timestamp_replaces_without_run_link(tmp_path):
    """REQ-03: a strictly newer semantic timestamp on a different run is
    positive current-run evidence (supported reset / fresh-run case), so the
    successor replaces the predecessor even without an explicit link."""
    from api_service.core.sync import mutate_execution_projection
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:reset-fresh", run_id="run-1", updated_at=stored_at)
            await session.commit()

            newer = datetime(2026, 9, 1, 12, 10, tzinfo=UTC)
            refreshed = await mutate_execution_projection(
                session,
                workflow_id="mm:reset-fresh",
                payload={
                    "workflow_id": "mm:reset-fresh",
                    "run_id": "run-2",
                    "namespace": "moonmind",
                    "workflow_type": TemporalWorkflowType.USER_WORKFLOW,
                    "owner_id": "owner-1",
                    "owner_type": TemporalExecutionOwnerType.USER,
                    "state": MoonMindWorkflowState.EXECUTING,
                    "close_status": None,
                    "entry": "run",
                    "search_attributes": {},
                    "memo": {"title": "Task"},
                    "artifact_refs": [],
                    "parameters": {},
                    "updated_at": newer,
                },
                owner="temporal",
            )
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-2"
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_observation_preserves_current_result_attribution(tmp_path):
    """REQ-05 (SQLite substitute for integration proof): a stale observation
    must not promote historical input/plan refs to the current result."""
    from api_service.db.models import Base

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)
            canonical, projection = _seed_execution(
                session, "mm:stale-attribution", run_id="run-2",
                updated_at=stored_at, refs=["art_current"], version=4,
            )
            canonical.input_ref = "input-current"
            projection.input_ref = "input-current"
            await session.commit()

            older = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:stale-attribution",
                run_id="run-1",
                updated_at=older,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user",
                      "input_ref": "input-stale-historical"},
            )
            payload = await map_temporal_state_to_projection(desc)
            payload["artifact_refs"] = ["art_stale_historical"]
            loaded = bool(payload.pop("_temporal_memo_loaded", False)) and bool(payload.get("memo"))
            from api_service.core.sync import mutate_execution_projection

            refreshed = await mutate_execution_projection(
                session, workflow_id="mm:stale-attribution", payload=payload,
                owner="temporal", metadata_loaded=loaded,
            )
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-2"
            assert refreshed.input_ref == "input-current"
            assert refreshed.artifact_refs == ["art_current"]
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.STALE
            assert refreshed.projection_version == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_continued_as_new_successor_advances_without_logical_completion(tmp_path):
    """REQ-03: a CONTINUED_AS_NEW predecessor close never reads as logical
    completion; the successor run advances the one logical-workflow projection."""
    from api_service.db.models import Base, TemporalExecutionCloseStatus

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(
                session, "mm:can-successor", run_id="run-1",
                updated_at=stored_at,
                state=MoonMindWorkflowState.EXECUTING,
                close_status=TemporalExecutionCloseStatus.CONTINUED_AS_NEW,
            )
            await session.commit()

            newer = datetime(2026, 9, 1, 12, 10, tzinfo=UTC)
            desc = _temporal_desc(
                workflow_id="mm:can-successor",
                run_id="run-2",
                updated_at=newer,
                memo={"entry": "run", "owner_id": "owner-1", "owner_type": "user"},
            )
            desc.previous_run_id = "run-1"
            refreshed = await sync_execution_projection(session, desc)
            await session.commit()
            await session.refresh(refreshed)

            assert refreshed.run_id == "run-2"
            assert refreshed.state == MoonMindWorkflowState.EXECUTING
            assert refreshed.close_status is None
            assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
    finally:
        await engine.dispose()


def _api_shaped_canonical_payload(canonical) -> dict:
    """Rebuild a canonical-owner payload from the stored canonical row.

    Mirrors the API reschedule/plan-binding patch shape: every column keeps
    its authorized stored value and only the admitted patch keys change.
    """
    from api_service.db.models import TemporalExecutionCanonicalRecord

    payload = {
        column.name: getattr(canonical, column.name)
        for column in TemporalExecutionCanonicalRecord.__table__.columns
    }
    payload["workflow_id"] = canonical.workflow_id
    return payload


@pytest.mark.asyncio
async def test_api_shaped_canonical_patch_accepts_scheduled_for_and_rejects_moves(tmp_path):
    """REQ-02: the API reschedule/plan-binding patch shape (payload rebuilt
    from the stored canonical row) applies admitted memo/scheduled_for hints
    to both rows, while wrong-owner/namespace/type moves and immutable
    creation-key rewrites are rejected instead of merging silently."""
    import pytest as _pytest

    from api_service.core.sync import mutate_execution_projection
    from api_service.db.models import Base, TemporalExecutionCanonicalRecord

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            canonical, _ = _seed_execution(
                session, "mm:api-canonical-patch", updated_at=stored_at,
            )
            canonical.create_idempotency_key = "key-1"
            await session.commit()

            # Happy path: admitted scheduled_for hint + plan memo keys.
            scheduled_at = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
            await session.refresh(canonical)
            payload = _api_shaped_canonical_payload(canonical)
            memo = dict(payload["memo"] or {})
            memo["omnigent_execution_plan_ref"] = "plan-1"
            payload["memo"] = memo
            payload["artifact_refs"] = list(payload["artifact_refs"] or []) + ["plan-1"]
            payload["scheduled_for"] = scheduled_at
            refreshed = await mutate_execution_projection(
                session, workflow_id="mm:api-canonical-patch",
                payload=payload, owner="canonical",
            )
            await session.commit()
            await session.refresh(refreshed)
            assert _as_utc(refreshed.scheduled_for) == scheduled_at
            assert refreshed.memo["omnigent_execution_plan_ref"] == "plan-1"
            assert "plan-1" in refreshed.artifact_refs
            stored_canonical = await session.get(
                TemporalExecutionCanonicalRecord, "mm:api-canonical-patch"
            )
            assert _as_utc(stored_canonical.scheduled_for) == scheduled_at
            assert stored_canonical.memo["omnigent_execution_plan_ref"] == "plan-1"

            # Rejection paths: each moves a protected field or rewrites an
            # immutable creation key under the same API payload shape.
            await session.refresh(canonical)
            bad_payloads = []
            for field, bad in (
                ("owner_id", "owner-2"),
                ("namespace", "other-namespace"),
                ("workflow_type", TemporalWorkflowType.MANIFEST_INGEST),
                ("create_idempotency_key", "key-2"),
            ):
                bad_payload = _api_shaped_canonical_payload(canonical)
                bad_payload[field] = bad
                bad_payloads.append((field, bad_payload))
            for field, bad_payload in bad_payloads:
                with _pytest.raises(ValueError):
                    await mutate_execution_projection(
                        session, workflow_id="mm:api-canonical-patch",
                        payload=bad_payload, owner="canonical",
                    )
                await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_owner_patch_applies_first_write_to_both_rows(tmp_path):
    """REQ-02: a first-time snapshot-owner patch carries snapshot memo keys
    and refs to both canonical and projection rows through the mutator."""
    from api_service.core.sync import mutate_execution_projection
    from api_service.db.models import Base, TemporalExecutionCanonicalRecord

    engine, session_factory = _sqlite_session_factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as session:
            stored_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
            _seed_execution(session, "mm:snapshot-first-write", updated_at=stored_at)
            await session.commit()

            refreshed = await mutate_execution_projection(
                session, workflow_id="mm:snapshot-first-write",
                payload={
                    "workflow_id": "mm:snapshot-first-write",
                    "memo": {
                        "task_input_snapshot_ref": "snap-1",
                        "task_input_snapshot_version": 1,
                        "task_input_snapshot_source_kind": "create",
                    },
                    "artifact_refs": ["snap-1"],
                },
                owner="snapshot",
            )
            await session.commit()
            await session.refresh(refreshed)
            assert refreshed.memo["task_input_snapshot_ref"] == "snap-1"
            assert "snap-1" in refreshed.artifact_refs
            stored_canonical = await session.get(
                TemporalExecutionCanonicalRecord, "mm:snapshot-first-write"
            )
            assert stored_canonical.memo["task_input_snapshot_ref"] == "snap-1"
            assert "snap-1" in stored_canonical.artifact_refs
    finally:
        await engine.dispose()


