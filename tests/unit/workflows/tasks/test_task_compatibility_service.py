from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.db import models as db_models
from moonmind.workflows.tasks.compatibility import (
    TaskCompatibilityService,
    _ListCursor,
)


def _service() -> TaskCompatibilityService:
    return TaskCompatibilityService(SimpleNamespace())


def _temporal_record(**overrides):
    now = datetime(2026, 3, 6, 0, 0, 0, tzinfo=UTC)
    payload = {
        "workflow_id": "mm:test-workflow",
        "entry": "run",
        "workflow_type": SimpleNamespace(value="MoonMind.Run"),
        "state": db_models.MoonMindWorkflowState.INITIALIZING,
        "close_status": None,
        "started_at": now,
        "updated_at": now,
        "closed_at": None,
        "owner_id": "owner-1",
        "search_attributes": {
            "mm_owner_type": "user",
            "mm_owner_id": "owner-1",
            "mm_entry": "run",
        },
        "memo": {
            "title": "Task Compatibility",
            "summary": "Execution initialized.",
        },
        "artifact_refs": ["artifact://input/1"],
        "namespace": "moonmind",
        "run_id": "temporal-run-1",
        "input_ref": "artifact://input/1",
        "plan_ref": None,
        "manifest_ref": None,
        "parameters": {},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_build_temporal_row_normalizes_identity_and_status() -> None:
    service = _service()
    record = _temporal_record(
        workflow_id="mm:manifest-1",
        entry="manifest",
        workflow_type=SimpleNamespace(value="MoonMind.ManifestIngest"),
        state=db_models.MoonMindWorkflowState.AWAITING_EXTERNAL,
        search_attributes={
            "mm_owner_type": "user",
            "mm_owner_id": "owner-1",
            "mm_entry": "manifest",
        },
        memo={
            "title": "Manifest Compatibility",
            "summary": "Waiting for approval",
        },
    )

    row = service._build_temporal_row(record)

    assert row.task_id == "mm:manifest-1"
    assert row.workflow_id == "mm:manifest-1"
    assert row.source == "temporal"
    assert row.entry == "manifest"
    assert row.status == "awaiting_action"
    assert row.temporal_status == "running"
    assert row.owner_type == "user"
    assert row.owner_id == "owner-1"
    assert row.detail_href == "/tasks/mm:manifest-1"


def test_build_temporal_detail_bounds_metadata_and_parameters() -> None:
    service = _service()
    long_title = "T" * 600
    record = _temporal_record(
        state=db_models.MoonMindWorkflowState.AWAITING_EXTERNAL,
        search_attributes={
            "mm_owner_type": "user",
            "mm_owner_id": "owner-1",
            "mm_entry": "run",
            "api_key": "should-not-leak",
        },
        memo={
            "title": long_title,
            "summary": "Waiting for operator input",
            "waiting_reason": "operator_paused",
            "attention_required": True,
            "secret": "hidden",
        },
        parameters={f"key-{idx}": f"value-{idx}" for idx in range(12)},
    )

    detail = service._build_temporal_detail(
        record,
        SimpleNamespace(is_superuser=True),
    )

    assert "api_key" not in detail.search_attributes
    assert detail.memo["title"].endswith("...")
    assert len(detail.memo["title"]) == 512
    assert len(detail.parameter_preview) == 10
    assert detail.debug.waiting_reason == "operator_paused"
    assert detail.debug.attention_required is True
    assert detail.actions.force_terminate is True
    assert detail.actions.resume is True
    assert detail.actions.pause is False


def test_cursor_round_trip_rejects_filter_mismatch() -> None:
    service = _service()
    cursor = service._encode_cursor(
        _ListCursor(
            offset=25,
            page_size=10,
            source="temporal",
            entry="run",
            workflow_type="MoonMind.Run",
            status_filter="running",
            owner_type="user",
            owner_id="owner-1",
        )
    )

    decoded = service._decode_cursor(
        cursor=cursor,
        page_size=10,
        source="temporal",
        entry="run",
        workflow_type="MoonMind.Run",
        status_filter="running",
        owner_type="user",
        owner_id="owner-1",
    )
    assert decoded.offset == 25

    with pytest.raises(ValueError, match="Compatibility cursor no longer matches"):
        service._decode_cursor(
            cursor=cursor,
            page_size=10,
            source="queue",
            entry="run",
            workflow_type="MoonMind.Run",
            status_filter="running",
            owner_type="user",
            owner_id="owner-1",
        )


@pytest.mark.asyncio
async def test_list_tasks_limits_source_fetches_to_cursor_window() -> None:
    service = _service()
    newer = service._build_temporal_row(_temporal_record(workflow_id="mm:newer"))
    older = service._build_temporal_row(
        _temporal_record(
            workflow_id="mm:older",
            updated_at=datetime(2026, 3, 5, 0, 0, 0, tzinfo=UTC),
        )
    )
    service._load_queue_rows = AsyncMock(return_value=([older], 7))
    service._load_orchestrator_rows = AsyncMock(return_value=([], 3))
    service._load_temporal_rows = AsyncMock(return_value=([newer], 5))
    cursor = service._encode_cursor(
        _ListCursor(
            offset=1,
            page_size=1,
            source="all",
            entry=None,
            workflow_type=None,
            status_filter=None,
            owner_type=None,
            owner_id=None,
        )
    )

    response = await service.list_tasks(
        user=SimpleNamespace(id="owner-1", is_superuser=True),
        source="all",
        entry=None,
        workflow_type=None,
        status_filter=None,
        owner_type=None,
        owner_id=None,
        page_size=1,
        cursor=cursor,
    )

    assert [item.task_id for item in response.items] == ["mm:older"]
    assert response.count == 15
    assert response.next_cursor
    service._load_queue_rows.assert_awaited_once_with(
        entry=None,
        status_filter=None,
        owner_id=None,
        limit=2,
    )
    service._load_orchestrator_rows.assert_awaited_once_with(
        entry=None,
        status_filter=None,
        limit=2,
    )
    service._load_temporal_rows.assert_awaited_once_with(
        user=SimpleNamespace(id="owner-1", is_superuser=True),
        entry=None,
        workflow_type=None,
        status_filter=None,
        owner_type=None,
        owner_id=None,
        limit=2,
    )
