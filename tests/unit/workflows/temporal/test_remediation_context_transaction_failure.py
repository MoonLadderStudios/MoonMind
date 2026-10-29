"""Optional context persistence cannot poison durable start/cancel intent."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from api_service.db.models import (
    MoonMindWorkflowState,
    OmnigentAgentProfile,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionStartUnconfirmedError,
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("cancel", [False, True])
async def test_context_failure_keeps_start_intent_recoverable(
    tmp_path, monkeypatch, cancel
):
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            record = TemporalExecutionCanonicalRecord(
                workflow_id="remediation",
                run_id="pending-run",
                namespace="default",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id="operator",
                state=MoonMindWorkflowState.INITIALIZING,
                entry="api",
                search_attributes={},
                parameters={
                    "workflow": {"remediation": {"target": {"workflowId": "target"}}}
                },
                memo={
                    "title": "Retained title",
                    "temporalStart": {"state": "pending", "taskQueue": "workflow"},
                },
            )
            session.add(record)
            session.add(
                OmnigentAgentProfile(profile_id="existing", display_name="Existing")
            )
            await session.commit()
            adapter = SimpleNamespace(start_workflow=AsyncMock())
            service = TemporalExecutionService(session, client_adapter=adapter)
            cancellation = asyncio.CancelledError("context request cancelled")

            async def build_context(**kwargs):
                record.memo = {**record.memo, "title": "Uncommitted context mutation"}
                session.add(
                    OmnigentAgentProfile(
                        profile_id="partial-context", display_name="Partial"
                    )
                )
                await session.flush()
                if cancel:
                    raise cancellation
                session.add(
                    OmnigentAgentProfile(
                        profile_id="existing", display_name="Duplicate"
                    )
                )
                await session.flush()

            builder = SimpleNamespace(
                build_context=AsyncMock(side_effect=build_context)
            )
            monkeypatch.setattr(
                service, "_remediation_context_builder", lambda: builder
            )
            expected = (
                asyncio.CancelledError
                if cancel
                else TemporalExecutionStartUnconfirmedError
            )
            with pytest.raises(expected) as caught:
                await service._ensure_create_start_confirmed(record)
            if cancel:
                assert caught.value is cancellation
            else:
                assert isinstance(caught.value.__cause__, IntegrityError)
            assert session.is_active
            assert await session.get(OmnigentAgentProfile, "partial-context") is None
            record = await session.get(
                TemporalExecutionCanonicalRecord, "remediation", populate_existing=True
            )
            assert record.memo["title"] == "Retained title"
            assert record.memo["temporalStart"]["state"] == "pending"
            assert not record.memo["temporalStart"].get("firstRequestedAt")
            adapter.start_workflow.assert_not_awaited()
            if not cancel:
                assert (
                    record.memo["temporalStart"]["reason"]
                    == "remediation_context_unavailable"
                )
                assert (
                    record.memo["temporalStart"]["firstFailure"]["errorType"]
                    == "IntegrityError"
                )
                async with sessions() as observer:
                    saved = await observer.get(
                        TemporalExecutionCanonicalRecord, "remediation"
                    )
                    assert (
                        saved.memo["temporalStart"]["firstFailure"]["errorType"]
                        == "IntegrityError"
                    )
            # A subsequent cancellation is still writable and prevents dispatch
            # even though the context retry remains outstanding.
            record.state = MoonMindWorkflowState.CANCELED
            record.close_status = TemporalExecutionCloseStatus.CANCELED
            await session.commit()
            await service._ensure_create_start_confirmed(record)
            builder.build_context.assert_awaited_once()
            adapter.start_workflow.assert_not_awaited()
        async with sessions() as observer:
            saved = await observer.get(TemporalExecutionCanonicalRecord, "remediation")
            assert saved.state is MoonMindWorkflowState.CANCELED
            assert await observer.get(OmnigentAgentProfile, "partial-context") is None
