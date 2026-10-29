"""The operational reconcile tick recovers durable execution delivery intent."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from sqlalchemy import select
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment

from api_service.api.routers import executions
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalArtifactLink,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
)
from moonmind.config.settings import settings
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    ManagedSessionReapResult,
)
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionStartUnconfirmedError,
)
from tests.unit.omnigent import test_admitted_input_authority as authority_fixtures
from tests.unit.omnigent.test_api_planning_input_authority import _api_context

pytestmark = pytest.mark.asyncio
artifacts = authority_fixtures.artifacts
auth_mode = authority_fixtures.auth_mode


class RequestDisappeared(BaseException):
    """A fixture interruption, without executing any process control."""


class IdleController:
    async def reconcile(self):
        return []

    async def reap_orphan_session_containers(self):
        return ManagedSessionReapResult()


def operational_tick(adapter):
    return TemporalAgentRuntimeActivities(
        artifact_service=SimpleNamespace(),
        session_controller=IdleController(),
        client_adapter=adapter,
        omnigent_stuck_state_service=SimpleNamespace(
            sweep=AsyncMock(
                return_value=SimpleNamespace(to_dict=lambda: {"scanned": 0})
            )
        ),
    )


def admission_request():
    return {
        "type": "workflow",
        "payload": {
            "title": "Durable original request",
            "idempotencyKey": "recover-without-the-client",
            "workflow": {
                "instructions": "Read the original admitted inputs",
                "publish": {"mode": "none"},
            },
        },
    }


async def admit_then_disappear(sessions, adapter, *, disappear=True):
    """Commit the public route's actual admission, then interrupt before RPC."""
    payload = admission_request()
    async with sessions() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        service._enforce_worker_code_freshness = AsyncMock()
        service._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        if disappear:
            service._ensure_create_start_confirmed = AsyncMock(
                side_effect=RequestDisappeared()
            )
        admission = OperatorAdmission(via="loopback")
        request = executions.create_execution(
            payload=deepcopy(payload),
            service=service,
            session=session,
            user=admission,
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
            access=ArtifactAccessContext.for_operator(admission),
        )
        if disappear:
            with pytest.raises(RequestDisappeared):
                await request
        else:
            await request
    async with sessions() as session:
        record = (
            await session.execute(select(TemporalExecutionCanonicalRecord))
        ).scalar_one()
        assert record.memo["temporalStart"]["state"] == (
            "pending" if disappear else "confirmed"
        )
        if disappear:
            assert not record.memo["temporalStart"].get("firstRequestedAt")
        links = (
            await session.execute(select(TemporalArtifactLink))
        ).scalars().all()
        assert links
        return record.workflow_id, record.run_id, deepcopy(record.parameters)


async def test_operational_tick_recovers_committed_admission_without_client_retry(
    artifacts, monkeypatch
):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    observed = []

    async def start(**kwargs):
        async with artifacts() as session:
            reader = TemporalArtifactActivities(
                TemporalArtifactService(TemporalArtifactRepository(session))
            )
            environment = ActivityEnvironment()
            environment.info = replace(
                environment.info, workflow_id=kwargs["workflow_id"]
            )
            body = await environment.run(
                reader.artifact_read,
                {
                    "artifact_ref": kwargs["input_args"]["initial_parameters"][
                        "resolvedSkillsetRef"
                    ],
                }
            )
            assert body
        observed.append(deepcopy(kwargs))
        return SimpleNamespace(run_id="background-confirmed-run")

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(side_effect=start),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        describe_workflow=AsyncMock(),
    )
    workflow_id, provisional_run, parameters = await admit_then_disappear(
        artifacts, adapter
    )
    adapter.start_workflow.assert_not_awaited()

    await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})

    adapter.start_workflow.assert_awaited_once()
    assert observed[0]["workflow_id"] == workflow_id
    assert observed[0]["input_args"]["initial_parameters"] == parameters
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.run_id == "background-confirmed-run" != provisional_run
        assert record.memo["temporalStart"]["state"] == "confirmed"
        links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == workflow_id
                )
            )
        ).scalars().all()
        assert links and all(link.run_id == record.run_id for link in links)


async def test_operational_tick_recovers_cancel_after_confirmed_start(
    artifacts, monkeypatch
):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    canceled = False

    async def cancel(*_args, **_kwargs):
        nonlocal canceled
        if not canceled:
            canceled = True
            raise RequestDisappeared()

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(
            return_value=SimpleNamespace(run_id="original-confirmed-run")
        ),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        describe_workflow=AsyncMock(
            return_value=SimpleNamespace(
                run_id="original-confirmed-run",
                status=WorkflowExecutionStatus.CANCELED,
            )
        ),
        cancel_workflow=AsyncMock(side_effect=cancel),
    )
    workflow_id, original_run, _parameters = await admit_then_disappear(
        artifacts, adapter, disappear=False
    )
    async with artifacts() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        service._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        service._best_effort_terminate_workflow_scoped_managed_sessions = AsyncMock()
        with pytest.raises(RequestDisappeared):
            await executions.cancel_execution(
                workflow_id=workflow_id,
                response=Response(),
                payload=executions.CancelExecutionRequest(graceful=True),
                service=service,
                user=OperatorAdmission(via="loopback"),
                _actions_enabled=None,
                authorization=None,
                execution_fanout=None,
            )
    adapter.cancel_workflow.assert_awaited_once()

    await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})

    assert adapter.cancel_workflow.await_count == 2
    adapter.start_workflow.assert_awaited_once()
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.run_id == original_run
        assert record.close_status.value == "canceled"


async def test_background_cancel_cannot_follow_a_changed_run(artifacts, monkeypatch):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(),
        cancel_workflow=AsyncMock(),
        describe_workflow=AsyncMock(),
    )
    workflow_id, provisional, _ = await admit_then_disappear(artifacts, adapter)
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        record.memo = {
            **record.memo,
            "temporalCancel": {
                "state": "pending", "targetRunId": provisional,
                "action": "cancel", "graceful": True, "reason": "original stop",
            },
        }
        record.run_id = "a-different-run"
        await session.commit()

    result = await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})

    adapter.cancel_workflow.assert_not_awaited()
    adapter.start_workflow.assert_not_awaited()
    assert result["executionIntents"]["items"][0]["reason"] == "target_run_changed"
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.run_id == "a-different-run"
        assert record.memo["temporalCancel"]["state"] == "pending"
        assert record.memo["temporalCancel"]["reasonCode"] == "target_run_changed"


async def test_successful_background_recovery_keeps_original_failure(artifacts, monkeypatch):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="recovered")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        describe_workflow=AsyncMock(),
    )
    workflow_id, _, _ = await admit_then_disappear(artifacts, adapter)
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        service = TemporalExecutionService(session, client_adapter=adapter)
        with pytest.raises(TemporalExecutionStartUnconfirmedError):
            await service._create_start_unconfirmed(
                record, dict(record.memo["temporalStart"]),
                "start_not_confirmed", ConnectionError("fixture original failure"),
            )

    await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})

    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        intent = record.memo["temporalStart"]
        assert intent["state"] == "confirmed"
        assert intent["firstFailure"]["reason"] == "start_not_confirmed"
        assert intent["firstFailure"]["errorType"] == "ConnectionError"
        assert intent["firstFailure"]["observedAt"]


@pytest.mark.parametrize("pending_start", [False, True])
async def test_older_cancel_completion_preserves_a_newer_force_cancel(artifacts, monkeypatch, pending_start):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    monkeypatch.setattr(
        TemporalExecutionService,
        "_best_effort_terminate_workflow_scoped_managed_sessions", AsyncMock(),
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def cancel(*_args, **_kwargs):
        entered.set()
        await release.wait()

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="original")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        cancel_workflow=AsyncMock(side_effect=cancel),
        terminate_workflow=AsyncMock(),
        describe_workflow=AsyncMock(return_value=SimpleNamespace(
            run_id="original", status=WorkflowExecutionStatus.CANCELED,
        )),
    )
    workflow_id, original_run, _ = await admit_then_disappear(
        artifacts, adapter, disappear=pending_start,
    )

    async def request(graceful, reason):
        async with artifacts() as session:
            service = TemporalExecutionService(session, client_adapter=adapter)
            return await service.cancel_execution(
                workflow_id=workflow_id, reason=reason, graceful=graceful,
                expected_run_id=original_run,
            )

    earlier = asyncio.create_task(request(True, "first graceful stop"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await request(False, "later force stop")
    finally:
        release.set()
    await asyncio.wait_for(earlier, 5)
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.memo["temporalCancel"]["reason"] == "later force stop"
        assert record.memo["temporalCancel"]["state"] == "confirmed"
        assert record.close_status.value == "terminated"


@pytest.mark.parametrize("condition", ["outage", "expired", "retention-unknown", "terminal", "legacy"])
async def test_background_preserves_uncertain_and_ineligible_history(
    artifacts, monkeypatch, condition,
):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="recovered")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        describe_workflow=AsyncMock(side_effect=RPCError("fixture absent", RPCStatusCode.NOT_FOUND, b"")),
    )
    workflow_id, _, parameters = await admit_then_disappear(artifacts, adapter)
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        memo = deepcopy(record.memo)
        if condition in {"expired", "retention-unknown"}:
            memo["temporalStart"].update(
                firstRequestedAt=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
                retentionSeconds=86400 if condition == "expired" else None,
            )
        elif condition == "terminal":
            record.state = MoonMindWorkflowState.COMPLETED
            record.close_status = TemporalExecutionCloseStatus.COMPLETED
        elif condition == "legacy":
            del memo["temporalStart"]
        record.memo = memo
        await session.commit()
    if condition == "outage":
        adapter.start_workflow.side_effect = ConnectionError("fixture dispatch outage")
        adapter.describe_workflow.side_effect = ConnectionError("fixture observation outage")
    result = await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})
    if condition in {"terminal", "legacy"}:
        assert result["executionIntents"]["selected"] == 0
    else:
        expected = {"expired": "history_absence_ambiguous",
                    "retention-unknown": "history_retention_unavailable",
                    "outage": "start_not_confirmed"}[condition]
        assert result["executionIntents"]["items"][0]["reason"] == expected
        async with artifacts() as session:
            record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            assert record.memo["temporalStart"]["state"] == "pending"
            assert record.memo["temporalStart"]["firstFailure"]["reason"] == expected
    if condition == "outage":
        adapter.start_workflow.side_effect = None
        adapter.describe_workflow.side_effect = RPCError("fixture absent", RPCStatusCode.NOT_FOUND, b"")
        await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})
        assert adapter.start_workflow.await_count == 2
        async with artifacts() as session:
            record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            assert record.memo["temporalStart"]["state"] == "confirmed"
            assert record.memo["temporalStart"]["firstFailure"]["errorType"] == "ConnectionError"
    else:
        adapter.start_workflow.assert_not_awaited()
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.parameters == parameters


async def test_delayed_observation_cannot_downgrade_confirmed_cancel(artifacts, monkeypatch):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(
        TemporalExecutionService,
        "_best_effort_terminate_workflow_scoped_managed_sessions", AsyncMock(),
    )
    entered, release = asyncio.Event(), asyncio.Event()
    observations = 0

    async def describe(*_args, **_kwargs):
        nonlocal observations
        observations += 1
        if observations == 1:
            entered.set()
            await release.wait()
            return SimpleNamespace(run_id="original", status=WorkflowExecutionStatus.RUNNING)
        return SimpleNamespace(run_id="original", status=WorkflowExecutionStatus.CANCELED)

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="original")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        cancel_workflow=AsyncMock(), describe_workflow=AsyncMock(side_effect=describe),
    )
    workflow_id, _, _ = await admit_then_disappear(artifacts, adapter, disappear=False)

    async def request():
        async with artifacts() as session:
            return await TemporalExecutionService(session, client_adapter=adapter).cancel_execution(
                workflow_id=workflow_id, reason="same stop", graceful=True,
                expected_run_id="original",
            )

    earlier = asyncio.create_task(request())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await request()
    finally:
        release.set()
    await asyncio.wait_for(earlier, 5)
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.memo["temporalCancel"]["state"] == "confirmed"
        assert record.memo["summary"] == "same stop"


@pytest.mark.parametrize("intent_format", ["current", "retained-request", "retained-audit"])
async def test_background_replay_cannot_replace_a_newer_undelivered_force_request(
    artifacts, monkeypatch, intent_format,
):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    monkeypatch.setattr(
        TemporalExecutionService,
        "_best_effort_terminate_workflow_scoped_managed_sessions", AsyncMock(),
    )
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="original")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        cancel_workflow=AsyncMock(side_effect=RequestDisappeared()),
        terminate_workflow=AsyncMock(side_effect=RequestDisappeared()),
        describe_workflow=AsyncMock(return_value=SimpleNamespace(
            run_id="original", status=WorkflowExecutionStatus.CANCELED,
        )),
    )
    workflow_id, _, _ = await admit_then_disappear(
        artifacts, adapter, disappear=intent_format == "retained-audit",
    )
    original_cancel = TemporalExecutionService.cancel_execution
    async with artifacts() as session:
        with pytest.raises(RequestDisappeared):
            await original_cancel(
                TemporalExecutionService(session, client_adapter=adapter),
                workflow_id=workflow_id, reason="old graceful stop", graceful=True,
            )
    if intent_format != "current":
        async with artifacts() as session:
            record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            memo = deepcopy(record.memo)
            if intent_format == "retained-request":
                del memo["temporalCancel"]["requestId"]
            else:
                del memo["temporalCancel"]
            record.memo = memo
            await session.commit()
    adapter.cancel_workflow = AsyncMock()
    inserted = False

    async def replay(service, **kwargs):
        nonlocal inserted
        if not inserted:
            inserted = True
            async with artifacts() as session:
                with pytest.raises(RequestDisappeared):
                    await original_cancel(
                        TemporalExecutionService(session, client_adapter=adapter),
                        workflow_id=workflow_id, reason="new force stop", graceful=False,
                    )
        return await original_cancel(service, **kwargs)

    monkeypatch.setattr(TemporalExecutionService, "cancel_execution", replay)
    await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})
    adapter.cancel_workflow.assert_not_awaited()
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.memo["temporalCancel"]["reason"] == "new force stop"
        assert record.memo["temporalCancel"]["graceful"] is False
        assert record.memo["temporalCancel"]["state"] == "pending"
    adapter.terminate_workflow.side_effect = None
    await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})
    adapter.cancel_workflow.assert_not_awaited()
    assert adapter.terminate_workflow.await_count == 2
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.memo["temporalCancel"]["state"] == "confirmed"
        assert record.close_status.value == "terminated"


@pytest.mark.parametrize("prefix", ["execution:", "workflow:", "task:"])
@pytest.mark.parametrize("closed", [False, True])
async def test_public_cancel_preserves_supported_identifier_aliases(
    artifacts, monkeypatch, prefix, closed,
):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    monkeypatch.setattr(
        TemporalExecutionService,
        "_best_effort_terminate_workflow_scoped_managed_sessions", AsyncMock(),
    )
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="original")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        cancel_workflow=AsyncMock(),
        describe_workflow=AsyncMock(return_value=SimpleNamespace(
            run_id="original", status=WorkflowExecutionStatus.CANCELED,
        )),
    )
    workflow_id, _, _ = await admit_then_disappear(artifacts, adapter, disappear=False)
    if closed:
        async with artifacts() as session:
            record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            record.state = MoonMindWorkflowState.CANCELED
            record.close_status = TemporalExecutionCloseStatus.CANCELED
            record.memo = {**record.memo, "temporalCancel": {
                "state": "pending", "targetRunId": "original",
                "requestId": "saved-request", "action": "cancel", "graceful": True,
            }}
            await session.commit()
    async with artifacts() as session:
        reply = await executions.cancel_execution(
            workflow_id=prefix + workflow_id, response=Response(),
            payload=executions.CancelExecutionRequest(graceful=True),
            service=TemporalExecutionService(session, client_adapter=adapter),
            user=OperatorAdmission(via="loopback"), _actions_enabled=None,
            authorization=None, execution_fanout=None,
        )
        assert reply.workflow_id == workflow_id
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.memo["temporalCancel"]["state"] == "confirmed"


@pytest.mark.parametrize("observation_fails", [False, True])
async def test_retained_start_failure_survives_background_recovery(
    artifacts, monkeypatch, observation_fails,
):
    await _api_context(artifacts, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(),
        describe_workflow=AsyncMock(return_value=SimpleNamespace(run_id="accepted")),
    )
    workflow_id, _, _ = await admit_then_disappear(artifacts, adapter)
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        # This is the pre-change on-disk shape, not a failure created by the
        # new writer that already populates firstFailure.
        record.memo = {**record.memo, "temporalStart": {
            **record.memo["temporalStart"],
            "firstRequestedAt": datetime.now(UTC).isoformat(),
            "retentionSeconds": 86400,
            "reason": "start_not_confirmed",
            "lastErrorType": "ConnectionError",
        }}
        await session.commit()
    if observation_fails:
        adapter.describe_workflow.side_effect = TimeoutError("fixture later observation")
        await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})
        async with artifacts() as session:
            record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            assert record.memo["temporalStart"]["reason"] == "reconciliation_unavailable"
            assert record.memo["temporalStart"]["firstFailure"] == {
                "reason": "start_not_confirmed", "errorType": "ConnectionError",
                "observedAt": None,
            }
        adapter.describe_workflow.side_effect = None
    await operational_tick(adapter).agent_runtime_reconcile_managed_sessions({})
    adapter.start_workflow.assert_not_awaited()
    async with artifacts() as session:
        record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert record.memo["temporalStart"]["state"] == "confirmed"
        assert record.memo["temporalStart"]["firstFailure"] == {
            "reason": "start_not_confirmed", "errorType": "ConnectionError",
            "observedAt": None,
        }
