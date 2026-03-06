"""Integration coverage for Temporal-side integrations monitoring flows."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, MoonMindWorkflowState
from moonmind.schemas.jules_models import normalize_jules_status
from moonmind.workflows.temporal.service import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@asynccontextmanager
async def _db(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path}/integrations_monitoring.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_callback_first_completion_uses_single_terminal_path(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(
                session,
                integration_poll_initial_seconds=5,
                integration_poll_max_seconds=30,
                integration_poll_jitter_ratio=0.0,
            )
            created = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="Callback path",
                input_artifact_ref=None,
                plan_artifact_ref="artifact://plan/1",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )
            configured = await service.configure_integration_monitoring(
                workflow_id=created.workflow_id,
                integration_name="jules",
                correlation_id="corr-callback",
                external_operation_id="task-callback",
                normalized_status="running",
                provider_status="in_progress",
                callback_supported=True,
                callback_correlation_key="cb-callback",
                recommended_poll_seconds=10,
                external_url="https://jules.example.test/tasks/task-callback",
                provider_summary={},
                result_refs=[],
            )

            completed = await service.ingest_integration_callback(
                integration_name="jules",
                callback_correlation_key="cb-callback",
                payload={
                    "event_type": "completed",
                    "provider_event_id": "evt-terminal",
                    "normalized_status": "succeeded",
                    "provider_status": "completed",
                },
                payload_artifact_ref="art_callback",
            )

            assert configured.state is MoonMindWorkflowState.AWAITING_EXTERNAL
            assert completed.state is MoonMindWorkflowState.EXECUTING
            assert completed.awaiting_external is False
            assert completed.integration_state["normalized_status"] == "succeeded"
            assert "art_callback" in completed.artifact_refs


async def test_polling_fallback_and_continue_as_new_preserve_monitoring_identity(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(
                session,
                integration_poll_initial_seconds=5,
                integration_poll_max_seconds=20,
                integration_poll_jitter_ratio=0.0,
                run_continue_as_new_wait_cycle_threshold=2,
            )
            created = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="Poll path",
                input_artifact_ref=None,
                plan_artifact_ref="artifact://plan/2",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )
            configured = await service.configure_integration_monitoring(
                workflow_id=created.workflow_id,
                integration_name="jules",
                correlation_id="corr-poll",
                external_operation_id="task-poll",
                normalized_status="queued",
                provider_status="pending",
                callback_supported=False,
                callback_correlation_key=None,
                recommended_poll_seconds=None,
                external_url=None,
                provider_summary={},
                result_refs=[],
            )
            original_run_id = configured.run_id

            waiting = await service.record_integration_poll(
                workflow_id=created.workflow_id,
                normalized_status="running",
                provider_status="in_progress",
                observed_at=None,
                recommended_poll_seconds=None,
                external_url=None,
                provider_summary={"stage": "provider"},
                result_refs=[],
                completed_wait_cycles=2,
            )
            finished = await service.record_integration_poll(
                workflow_id=created.workflow_id,
                normalized_status="succeeded",
                provider_status="completed",
                observed_at=None,
                recommended_poll_seconds=None,
                external_url=None,
                provider_summary={"stage": "provider"},
                result_refs=["art_result"],
                completed_wait_cycles=0,
            )

            assert waiting.run_id != original_run_id
            assert waiting.integration_state["correlation_id"] == "corr-poll"
            assert waiting.integration_state["external_operation_id"] == "task-poll"
            assert finished.integration_state["normalized_status"] == "succeeded"
            assert "art_result" in finished.artifact_refs


async def test_duplicate_reordered_and_invalid_callbacks_are_safe(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session)
            created = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="Callback safety",
                input_artifact_ref=None,
                plan_artifact_ref="artifact://plan/3",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )
            await service.configure_integration_monitoring(
                workflow_id=created.workflow_id,
                integration_name="jules",
                correlation_id="corr-safe",
                external_operation_id="task-safe",
                normalized_status="running",
                provider_status="running",
                callback_supported=True,
                callback_correlation_key="cb-safe",
                recommended_poll_seconds=5,
                external_url=None,
                provider_summary={},
                result_refs=[],
            )

            first = await service.ingest_integration_callback(
                integration_name="jules",
                callback_correlation_key="cb-safe",
                payload={
                    "event_type": "status_changed",
                    "provider_event_id": "evt-dup",
                    "normalized_status": "running",
                    "provider_status": "running",
                },
                payload_artifact_ref=None,
            )
            duplicate = await service.ingest_integration_callback(
                integration_name="jules",
                callback_correlation_key="cb-safe",
                payload={
                    "event_type": "status_changed",
                    "provider_event_id": "evt-dup",
                    "normalized_status": "running",
                    "provider_status": "running",
                },
                payload_artifact_ref=None,
            )
            terminal = await service.ingest_integration_callback(
                integration_name="jules",
                callback_correlation_key="cb-safe",
                payload={
                    "event_type": "completed",
                    "provider_event_id": "evt-terminal",
                    "normalized_status": "succeeded",
                    "provider_status": "completed",
                },
                payload_artifact_ref=None,
            )
            reordered = await service.ingest_integration_callback(
                integration_name="jules",
                callback_correlation_key="cb-safe",
                payload={
                    "event_type": "late-progress",
                    "provider_event_id": "evt-late",
                    "normalized_status": "running",
                    "provider_status": "running",
                },
                payload_artifact_ref=None,
            )

            assert first.integration_state["provider_event_ids_seen"] == ["evt-dup"]
            assert "Ignored duplicate external event" in duplicate.memo["summary"]
            assert terminal.integration_state["normalized_status"] == "succeeded"
            assert reordered.integration_state["normalized_status"] == "succeeded"
            with pytest.raises(TemporalExecutionNotFoundError):
                await service.ingest_integration_callback(
                    integration_name="jules",
                    callback_correlation_key="missing-key",
                    payload={"event_type": "completed"},
                    payload_artifact_ref=None,
                )


async def test_failure_and_cancel_paths_keep_jules_normalization_compact(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session)
            created = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="Failure path",
                input_artifact_ref=None,
                plan_artifact_ref="artifact://plan/4",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )
            await service.configure_integration_monitoring(
                workflow_id=created.workflow_id,
                integration_name="jules",
                correlation_id="corr-failure",
                external_operation_id="task-failure",
                normalized_status=normalize_jules_status("in_progress"),
                provider_status="in_progress",
                callback_supported=False,
                callback_correlation_key=None,
                recommended_poll_seconds=5,
                external_url=None,
                provider_summary={},
                result_refs=[],
            )
            failed = await service.record_integration_poll(
                workflow_id=created.workflow_id,
                normalized_status=normalize_jules_status("failed"),
                provider_status="failed",
                observed_at=None,
                recommended_poll_seconds=None,
                external_url=None,
                provider_summary={"reason": "provider_500"},
                result_refs=[],
                completed_wait_cycles=0,
            )
            canceled = await service.cancel_execution(
                workflow_id=created.workflow_id,
                reason="operator stop",
                graceful=True,
            )

            assert failed.memo["error_category"] == "integration_error"
            assert failed.integration_state["normalized_status"] == "failed"
            assert canceled.state is MoonMindWorkflowState.CANCELED
