"""Admission retries retain an authored dispatch time as RPC time passes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.service import RPCError, RPCStatusCode

from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.unit.workflows.temporal import test_temporal_service as fixtures

temporal_db = fixtures.temporal_db
mock_client_adapter = fixtures.mock_client_adapter


@pytest.mark.asyncio
async def test_admitted_create_retry_retains_original_dispatch_time(
    tmp_path, mock_client_adapter, monkeypatch
):
    now = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)
    due = now + timedelta(minutes=1)
    monkeypatch.setattr("moonmind.workflows.temporal.service._utc_now", lambda: now)
    starts = []

    async def start(**kwargs):
        nonlocal now
        starts.append(kwargs)
        if len(starts) == 1:
            now += timedelta(seconds=20)
            raise ConnectionError("Transient loss consumed twenty seconds")
        return SimpleNamespace(run_id="confirmed-delayed-run")

    mock_client_adapter.start_workflow = AsyncMock(side_effect=start)
    mock_client_adapter.describe_workflow.side_effect = RPCError(
        "Not delivered", RPCStatusCode.NOT_FOUND, b""
    )
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        service._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        execution = await service.create_execution(
            workflow_type="MoonMind.ProviderProfileManager",
            owner_id=None,
            title="Original dispatch timing",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="original-dispatch-timing",
            start_delay=timedelta(minutes=1),
        )
    assert execution.run_id == "confirmed-delayed-run"
    assert len(starts) == 2
    assert starts[0]["start_delay"] == timedelta(minutes=1)
    assert starts[1]["start_delay"] == timedelta(seconds=40)
    assert execution.memo["temporalStart"]["scheduledFor"] == due.isoformat()
