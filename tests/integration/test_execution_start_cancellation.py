"""Public cancellation fences a saved create before any retry can start it."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import CancelledError, TerminatedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.api.routers import executions
from api_service.db.models import TemporalArtifactLink, TemporalExecutionCanonicalRecord
from moonmind.config.settings import settings
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.activity_catalog import publication_task_queue
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration import test_execution_start_admission as admission_tests
from tests.integration.test_execution_start_admission import (
    _api_context,
    _register_search_attributes,
    _submit,
)

conversion_postgres_url = admission_tests.conversion_postgres_url
sessions = admission_tests.sessions
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@workflow.defn(name="MoonMind.UserWorkflow")
class CancelAdmissionConsumer:
    @workflow.run
    async def run(self, payload: dict) -> None:
        await workflow.wait_condition(lambda: False)


async def _cancel(sessions, adapter, workflow_id, *, graceful, expect_pending=False):
    async with sessions() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        service._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        service._best_effort_terminate_workflow_scoped_managed_sessions = AsyncMock()
        service._sync_integration_correlation_record = AsyncMock()
        service._fan_out_dependency_resolution = AsyncMock()
        result = await executions.cancel_execution(
            workflow_id=workflow_id,
            response=Response(),
            payload=executions.CancelExecutionRequest(graceful=graceful),
            service=service,
            user=OperatorAdmission(via="loopback"),
            _actions_enabled=None,
            authorization=None,
            execution_fanout=None,
        )
        if expect_pending:
            service._fan_out_dependency_resolution.assert_not_awaited()
        return result


@pytest.mark.parametrize("delivery", ["absent", "in-flight", "ack-lost"])
@pytest.mark.parametrize("graceful", [True, False], ids=["graceful", "force"])
async def test_public_cancel_preserves_intent_across_pending_create(
    sessions, monkeypatch, delivery, graceful
):
    await _api_context(sessions, monkeypatch)
    payload = {
        "type": "workflow",
        "payload": {
            "title": "Cancelable original admission",
            "idempotencyKey": "cancel-pending-create",
            "workflow": {
                "instructions": "Retain the original request",
                "publish": {"mode": "none"},
            },
        },
    }
    async with await WorkflowEnvironment.start_local() as env:
        await _register_search_attributes(env)
        adapter = TemporalClientAdapter(client=env.client)
        actual_start = adapter.start_workflow
        actual_describe = adapter.describe_workflow
        describe_unavailable = delivery == "ack-lost"
        attempts = []
        at_delivery = asyncio.Event()
        release_delivery = asyncio.Event()

        async def start(**kwargs):
            attempts.append(deepcopy(kwargs))
            if delivery == "ack-lost":
                await actual_start(**kwargs)
                raise ConnectionError("injected lost acknowledgement after delivery")
            if len(attempts) <= 2:
                raise ConnectionError("injected loss before any Temporal start")
            at_delivery.set()
            await release_delivery.wait()
            return await actual_start(**kwargs)

        async def describe(*args, **kwargs):
            if describe_unavailable:
                raise ConnectionError("injected reconciliation outage")
            return await actual_describe(*args, **kwargs)

        adapter.start_workflow = AsyncMock(side_effect=start)
        adapter.describe_workflow = AsyncMock(side_effect=describe)
        async with Worker(
            env.client,
            task_queue=publication_task_queue(
                settings.temporal.user_workflow_v2_task_queue
            ),
            workflows=[CancelAdmissionConsumer],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with pytest.raises(HTTPException) as pending:
                await _submit(sessions, adapter, payload)
            assert pending.value.status_code == 503
            async with sessions() as session:
                record = (
                    await session.execute(select(TemporalExecutionCanonicalRecord))
                ).scalar_one()
                workflow_id = record.workflow_id
            describe_unavailable = False
            if delivery == "in-flight":
                retry = asyncio.create_task(_submit(sessions, adapter, payload))
                await asyncio.wait_for(at_delivery.wait(), 10)
                cancel = asyncio.create_task(
                    _cancel(
                        sessions,
                        adapter,
                        workflow_id,
                        graceful=graceful,
                        expect_pending=delivery == "absent",
                    )
                )
                # The pending owner holds the canonical row until delivery has
                # an outcome. The cancel must wait and then target that outcome.
                await asyncio.sleep(0.1)
                release_delivery.set()
                try:
                    await asyncio.wait_for(retry, 15)
                    await asyncio.wait_for(cancel, 15)
                finally:
                    release_delivery.set()
                    await asyncio.gather(retry, cancel, return_exceptions=True)
                with pytest.raises(WorkflowFailureError) as stopped:
                    await asyncio.wait_for(
                        env.client.get_workflow_handle(workflow_id).result(), 10
                    )
                assert isinstance(
                    stopped.value.cause, CancelledError if graceful else TerminatedError
                )
            elif delivery == "absent":
                await _cancel(
                    sessions,
                    adapter,
                    workflow_id,
                    graceful=graceful,
                    expect_pending=delivery == "absent",
                )
                reply = await _submit(sessions, adapter, payload)
                assert reply.workflow_id == workflow_id
                assert len(attempts) == 2
                with pytest.raises(RPCError) as absent:
                    await adapter.describe_workflow(workflow_id)
                assert absent.value.status == RPCStatusCode.NOT_FOUND
            else:
                await _cancel(sessions, adapter, workflow_id, graceful=graceful)
                with pytest.raises(WorkflowFailureError) as stopped:
                    await asyncio.wait_for(
                        env.client.get_workflow_handle(workflow_id).result(), 10
                    )
                assert isinstance(
                    stopped.value.cause, CancelledError if graceful else TerminatedError
                )
                await _submit(sessions, adapter, payload)
            async with sessions() as session:
                record = await session.get(
                    TemporalExecutionCanonicalRecord, workflow_id
                )
                if delivery == "absent":
                    assert record.close_status is None
                    assert record.closed_at is None
                    assert record.state.value not in {"canceled", "failed", "completed"}
                else:
                    actual = await actual_describe(workflow_id)
                    assert record.run_id == actual.run_id
                    links = (
                        (
                            await session.execute(
                                select(TemporalArtifactLink).where(
                                    TemporalArtifactLink.workflow_id == workflow_id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert links
                    assert {link.run_id for link in links} == {actual.run_id}
                assert any(
                    item["action"] == "cancel"
                    for item in record.memo["intervention_audit"]
                )
            assert (
                len(attempts) == {"in-flight": 3, "absent": 2, "ack-lost": 1}[delivery]
            )
