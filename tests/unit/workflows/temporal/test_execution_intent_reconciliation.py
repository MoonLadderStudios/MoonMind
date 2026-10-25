"""The operational reconcile tick recovers durable execution delivery intent."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from sqlalchemy import select
from temporalio.client import WorkflowExecutionStatus

from api_service.api.routers import executions
from api_service.db.models import TemporalArtifactLink, TemporalExecutionCanonicalRecord
from moonmind.config.settings import settings
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    ManagedSessionReapResult,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
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


async def admit_then_disappear(sessions, adapter, *, disappear=True):
    """Commit the public route's actual admission, then interrupt before RPC."""
    payload = {
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
    async with sessions() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        service._enforce_worker_code_freshness = AsyncMock()
        service._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        if disappear:
            service._ensure_create_start_confirmed = AsyncMock(
                side_effect=RequestDisappeared()
            )
        request = executions.create_execution(
            payload=deepcopy(payload),
            service=service,
            session=session,
            user=OperatorAdmission(via="loopback"),
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
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
            body = await reader.artifact_read(
                {
                    "principal": f"workflow:{kwargs['workflow_id']}",
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
