"""Step retry restores retained input grants before Temporal resumes consumers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import WorkflowExecutionStatus

from api_service.api.routers import executions
from api_service.db.models import TemporalArtifactLink, TemporalExecutionCanonicalRecord
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal import retry_from_step
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.service import (
    TemporalExecutionRetryUnavailableError,
    TemporalExecutionService,
)
from tests.unit.omnigent import test_api_planning_input_authority as api_fixtures
from tests.unit.omnigent.test_admitted_input_authority import _protect, _skill_set
from tests.unit.workflows.temporal.test_retry_from_step import _History, _rows

pytestmark = pytest.mark.asyncio
artifacts = api_fixtures.artifacts
auth_mode = api_fixtures.auth_mode


async def _failed_admission(sessions, monkeypatch):
    saved = await api_fixtures._api_context(sessions, monkeypatch)
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="failed-run")),
        describe_workflow=AsyncMock(
            return_value=SimpleNamespace(
                run_id="failed-run",
                status=WorkflowExecutionStatus.FAILED,
                parent_id=None,
            )
        ),
        namespace_retention_seconds=AsyncMock(return_value=86400),
    )
    async with sessions() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        monkeypatch.setattr(service, "_enforce_worker_code_freshness", AsyncMock())
        response = await executions.create_execution(
            payload={
                "type": "workflow",
                "payload": {
                    "workflow": {
                        "instructions": "Resolve selected PRs",
                        "publish": {"mode": "none"},
                    }
                },
            },
            service=service,
            session=session,
            user=OperatorAdmission(via="loopback"),
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
        )
        # Reproduce pre-upgrade admission: inputs exist, but the parent has no
        # grants when it reads its Skill manifest before starting an AgentRun.
        await session.execute(
            delete(TemporalArtifactLink).where(
                TemporalArtifactLink.workflow_id == response.workflow_id
            )
        )
        await session.commit()
    history = _History()
    start = HistoryEvent(
        event_id=1, event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
    )
    start.workflow_execution_started_event_attributes.input.payloads.append(
        MOONMIND_TEMPORAL_DATA_CONVERTER.payload_converter.to_payload(
            adapter.start_workflow.call_args.kwargs["input_args"]
        )
    )
    history.events.append(start)
    for order in (1, 2, 3):
        history.memo(history.workflow_task(), mm_current_step_order=order)
    adapter.fetch_workflow_history = AsyncMock(return_value=history)
    adapter.reset_workflow_execution = AsyncMock(return_value="retry-run")
    monkeypatch.setattr(
        retry_from_step, "replay_failure_before_reset", AsyncMock(return_value=None)
    )
    return saved, response.workflow_id, adapter


@pytest.mark.parametrize("reset_failure", [False, True])
async def test_retry_restores_recorded_inputs_before_reset_and_deduplicates(
    artifacts, monkeypatch, reset_failure
):
    saved, workflow_id, adapter = await _failed_admission(artifacts, monkeypatch)
    unrelated = await _skill_set(
        artifacts, tag="unrelated", owner=saved.actor, workflow_id=None
    )
    async with artifacts() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        monkeypatch.setattr(service, "_sync_retried_run_projection", AsyncMock())
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        # Mutable projection parameters cannot replace the recorded admission.
        record.parameters = {"resolvedSkillsetRef": unrelated.manifest_id}
        await session.commit()
        reader = TemporalArtifactService(TemporalArtifactRepository(session))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await reader.read(
                artifact_id=saved.skills.manifest_id,
                principal=f"workflow:{workflow_id}",
            )

        async def reset(*args, **kwargs):
            # An independent session represents Temporal's immediate consumer.
            async with artifacts() as consumer_session:
                consumer = TemporalArtifactService(
                    TemporalArtifactRepository(consumer_session)
                )
                for ref in (
                    saved.skills.manifest_id,
                    saved.skills.body_id,
                    saved.compiled[0].binding.plan_artifact_ref.removeprefix(
                        "artifact:"
                    ),
                    saved.compiled[0].binding.task_input_snapshot_ref,
                ):
                    assert (
                        await consumer.read(
                            artifact_id=ref, principal=f"workflow:{workflow_id}"
                        )
                    )[1]
            if reset_failure and adapter.reset_workflow_execution.await_count == 1:
                raise ConnectionError("Reset was not delivered")
            return "retry-run"

        adapter.reset_workflow_execution.side_effect = reset
        kwargs = dict(
            ledger_rows=_rows("completed", "completed", "failed"),
            idempotency_key="retry-inputs",
            expected_run_id="failed-run",
        )
        if reset_failure:
            with pytest.raises(ConnectionError, match="not delivered"):
                await service.retry_execution_from_step(record, **kwargs)
        result = await service.retry_execution_from_step(record, **kwargs)
        assert await service.retry_execution_from_step(record, **kwargs) == result
        assert result["stepId"] == "step-3"
        assert result["workflowId"] == workflow_id
        assert result["runId"] == "retry-run"
        links = (
            await session.scalars(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == workflow_id
                )
            )
        ).all()
        assert len(links) == len({link.artifact_id for link in links})
        for artifact_id, principal in (
            (saved.skills.manifest_id, "workflow:unrelated"),
            (unrelated.manifest_id, f"workflow:{workflow_id}"),
        ):
            with pytest.raises(TemporalArtifactAuthorizationError):
                await reader.read(artifact_id=artifact_id, principal=principal)
    assert adapter.reset_workflow_execution.await_count == (2 if reset_failure else 1)


@pytest.mark.parametrize("protection", ["restricted", "quarantined"])
async def test_retry_refuses_protected_original_inputs_without_partial_grants(
    artifacts, monkeypatch, protection
):
    saved, workflow_id, adapter = await _failed_admission(artifacts, monkeypatch)
    await _protect(artifacts, saved.skills.manifest_id, protection)
    async with artifacts() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        with pytest.raises(TemporalExecutionRetryUnavailableError) as error:
            await service.retry_execution_from_step(
                record,
                ledger_rows=_rows("completed", "completed", "failed"),
                idempotency_key="protected-inputs",
                expected_run_id="failed-run",
            )
        assert error.value.reason == "input_artifacts_unavailable"
        adapter.reset_workflow_execution.assert_not_awaited()
        assert (
            await session.scalars(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == workflow_id
                )
            )
        ).all() == []


@pytest.mark.parametrize("fault", ["binding", "missing-plan", "link-outage", "replay"])
async def test_retry_preserves_the_failed_run_when_admission_cannot_complete(
    artifacts, monkeypatch, fault
):
    from api_service.db.models import OmnigentExecutionPlanRecord
    from moonmind.omnigent import bridge_artifacts

    saved, workflow_id, adapter = await _failed_admission(artifacts, monkeypatch)
    history = adapter.fetch_workflow_history.return_value
    if fault == "binding":
        converter = MOONMIND_TEMPORAL_DATA_CONVERTER.payload_converter
        payloads = history.events[
            0
        ].workflow_execution_started_event_attributes.input.payloads
        original = converter.from_payloads(payloads)[0]
        original["initial_parameters"]["omnigentExecutionPlan"]["planDigest"] = (
            "sha256:" + "f" * 64
        )
        payloads[0].CopyFrom(converter.to_payload(original))
    elif fault == "link-outage":
        link = bridge_artifacts._link_execution_input
        calls = 0

        async def failing_link(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise TemporalArtifactStateError("Injected link persistence failure")
            await link(*args, **kwargs)

        monkeypatch.setattr(bridge_artifacts, "_link_execution_input", failing_link)
    elif fault == "replay":
        monkeypatch.setattr(
            retry_from_step,
            "replay_failure_before_reset",
            AsyncMock(return_value="history changed"),
        )
    async with artifacts() as session:
        if fault == "missing-plan":
            await session.execute(
                delete(OmnigentExecutionPlanRecord).where(
                    OmnigentExecutionPlanRecord.plan_ref
                    == saved.compiled[0].binding.plan_ref
                )
            )
            await session.commit()
        service = TemporalExecutionService(session, client_adapter=adapter)
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        with pytest.raises(TemporalExecutionRetryUnavailableError) as error:
            await service.retry_execution_from_step(
                record,
                ledger_rows=_rows("completed", "completed", "failed"),
                idempotency_key="unavailable-inputs",
                expected_run_id="failed-run",
            )
        assert error.value.reason == (
            "history_not_replayable"
            if fault == "replay"
            else "input_artifacts_unavailable"
        )
        adapter.reset_workflow_execution.assert_not_awaited()
    async with artifacts() as session:
        assert (
            await session.scalars(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == workflow_id
                )
            )
        ).all() == []
