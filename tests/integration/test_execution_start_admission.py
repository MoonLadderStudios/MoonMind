"""Real API, PostgreSQL and Temporal preserve one admitted create across RPC loss."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from temporalio import workflow
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.api.routers import executions
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalArtifactLink,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
)
from moonmind.config.settings import settings
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.activity_catalog import publication_task_queue
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration import (
    test_execution_input_admission_postgres as admission_fixtures,
)
from tests.integration.workflows.temporal.workflows.test_merge_automation_full_topology import (
    _register_search_attributes,
)
from tests.unit.omnigent.test_api_planning_input_authority import _api_context

sessions = admission_fixtures.sessions
conversion_postgres_url = admission_fixtures.conversion_postgres_url
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
_OBSERVED = []


@workflow.defn(name="MoonMind.UserWorkflow")
class AdmissionConsumer:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        _OBSERVED.append((workflow.info().run_id, payload))
        return {"status": "success"}


async def _submit(sessions, adapter, payload, *, fail_confirmation=False):
    async with sessions() as session:
        if fail_confirmation:
            actual_commit = session.commit

            async def commit():
                nonlocal fail_confirmation
                if fail_confirmation and any(
                    isinstance(row, TemporalExecutionCanonicalRecord)
                    and (row.memo or {}).get("temporalStart", {}).get("state")
                    == "confirmed"
                    for row in session.identity_map.values()
                ):
                    fail_confirmation = False
                    raise RuntimeError("injected confirmation commit failure")
                await actual_commit()

            session.commit = commit
        service = TemporalExecutionService(session, client_adapter=adapter)
        service._enforce_worker_code_freshness = AsyncMock()
        service._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        return await executions.create_execution(
            payload=deepcopy(payload),
            service=service,
            session=session,
            user=OperatorAdmission(via="loopback"),
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
        )


@pytest.mark.parametrize(
    "loss",
    [
        "before",
        "after",
        "completed",
        "concurrent",
        "legacy",
        "expired",
        "retention-unavailable",
        "old-unattempted",
        "canceled",
        "cancel-requested",
        "race-completed",
        "auto-before",
        "permanent",
        "confirmation-write-failure",
    ],
)
async def test_admitted_create_recovers_one_original_temporal_start(
    sessions, monkeypatch, loss
):
    _OBSERVED.clear()
    saved = await _api_context(sessions, monkeypatch)
    original_time = datetime.now(UTC) + timedelta(hours=2)
    payload = {
        "type": "workflow",
        "payload": {
            "title": "Original admission",
            "idempotencyKey": "start-loss",
            "schedule": {"mode": "once", "scheduledFor": original_time.isoformat()},
            "workflow": {
                "title": "Original admission",
                "instructions": "Keep original inputs",
                "publish": {"mode": "none"},
            },
        },
    }
    async with await WorkflowEnvironment.start_local() as env:
        await _register_search_attributes(env)
        adapter = TemporalClientAdapter(client=env.client)
        actual_start = adapter.start_workflow
        actual_describe = adapter.describe_workflow
        attempts = []
        reconciliation_unavailable = loss in {"completed", "expired"}

        async def start(**kwargs):
            attempts.append(kwargs)
            async with sessions() as session:
                admitted = await session.get(
                    TemporalExecutionCanonicalRecord, kwargs["workflow_id"]
                )
                assert admitted.memo["temporalStart"]["firstRequestedAt"]
                assert admitted.memo["temporalStart"]["state"] == "pending"
            if loss == "permanent" and len(attempts) == 1:
                raise RPCError(
                    "injected permanent start rejection",
                    RPCStatusCode.INVALID_ARGUMENT,
                    b"",
                )
            if loss == "auto-before" and len(attempts) == 1:
                raise ConnectionError("injected single transient loss before delivery")
            if len(attempts) <= 2 and loss in {
                "before",
                "concurrent",
                "legacy",
                "retention-unavailable",
                "old-unattempted",
                "canceled",
                "cancel-requested",
                "race-completed",
            }:
                raise ConnectionError("injected loss before delivery")
            if loss == "race-completed" and len(attempts) == 3:
                # Another sender wins after our actual NOT_FOUND but before the
                # Start RPC. Its completed ID must still reject a second effect.
                raced = await actual_start(**kwargs)
                await env.client.get_workflow_handle(raced.workflow_id).result()
            result = await actual_start(**kwargs)
            if len(attempts) == 1 and loss in {"after", "completed", "expired"}:
                raise ConnectionError("injected loss after delivery")
            return result

        async def describe(*args, **kwargs):
            if reconciliation_unavailable:
                raise ConnectionError("injected reconciliation outage")
            return await actual_describe(*args, **kwargs)

        adapter.start_workflow = AsyncMock(side_effect=start)
        adapter.describe_workflow = AsyncMock(side_effect=describe)
        async with Worker(
            env.client,
            task_queue=publication_task_queue(
                settings.temporal.user_workflow_v2_task_queue
            ),
            workflows=[AdmissionConsumer],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            if loss in {"after", "auto-before"}:
                first = await _submit(sessions, adapter, payload)
                workflow_id = first.workflow_id
            else:
                with pytest.raises(HTTPException) as pending:
                    await _submit(
                        sessions,
                        adapter,
                        payload,
                        fail_confirmation=loss == "confirmation-write-failure",
                    )
                assert pending.value.status_code == 503
                assert pending.value.detail["code"] == "temporal_start_unconfirmed"
                async with sessions() as session:
                    record = (
                        await session.execute(select(TemporalExecutionCanonicalRecord))
                    ).scalar_one()
                    workflow_id = record.workflow_id
                    assert record.memo["temporalStart"]["state"] == "pending"
                    assert record.memo["temporalStart"]["firstRequestedAt"]
            if loss in {"completed", "expired"}:
                assert (await env.client.get_workflow_handle(workflow_id).result())[
                    "status"
                ] == "success"
                reconciliation_unavailable = False
            if loss in {
                "legacy",
                "expired",
                "old-unattempted",
                "canceled",
                "cancel-requested",
            }:
                async with sessions() as session:
                    record = await session.get(
                        TemporalExecutionCanonicalRecord, workflow_id
                    )
                    memo = dict(record.memo)
                    if loss == "legacy":
                        memo.pop("temporalStart")
                    elif loss == "expired":
                        memo["temporalStart"] = {
                            **memo["temporalStart"],
                            "firstRequestedAt": (
                                datetime.now(UTC) - timedelta(days=2)
                            ).isoformat(),
                        }
                    elif loss == "old-unattempted":
                        memo["temporalStart"] = {
                            **memo["temporalStart"],
                            "firstRequestedAt": None,
                        }
                        record.created_at = datetime.now(UTC) - timedelta(days=1000)
                    elif loss == "canceled":
                        record.state = MoonMindWorkflowState.CANCELED
                        record.close_status = TemporalExecutionCloseStatus.CANCELED
                    else:
                        memo["intervention_audit"] = [
                            {"action": "cancel", "transport": "temporal_cancel"}
                        ]
                    record.memo = memo
                    await session.commit()
            if loss == "expired":
                # Model the server's expired-history response after a real
                # completed effect. Purge scheduling itself is not this boundary.
                adapter.describe_workflow = AsyncMock(
                    side_effect=RPCError(
                        "retained history expired", RPCStatusCode.NOT_FOUND, b""
                    )
                )
            if loss == "retention-unavailable":
                adapter.namespace_retention_seconds = AsyncMock(
                    side_effect=ConnectionError("retention service unavailable")
                )
            replacement = deepcopy(payload)
            replacement["payload"]["title"] = "Changed retry title"
            replacement["payload"]["workflow"]["instructions"] = "Must never run"
            replacement["payload"]["workflow"]["title"] = "Changed retry title"
            replacement["payload"]["schedule"]["scheduledFor"] = (
                original_time + timedelta(days=1)
            ).isoformat()
            if loss == "permanent":
                assert len(attempts) == 1
                assert _OBSERVED == []
                return
            if loss in {"expired", "retention-unavailable"}:
                with pytest.raises(HTTPException) as denied:
                    await _submit(sessions, adapter, replacement)
                assert denied.value.status_code == 503
                assert denied.value.detail["reason"] == (
                    "history_absence_ambiguous"
                    if loss == "expired"
                    else "history_retention_unavailable"
                )
                assert len(attempts) == (1 if loss == "expired" else 2)
                assert len(_OBSERVED) == (1 if loss == "expired" else 0)
                return
            if loss in {"legacy", "canceled", "cancel-requested"}:
                reply = await _submit(sessions, adapter, replacement)
                assert reply.workflow_id == workflow_id
                assert len(attempts) == 2
                assert _OBSERVED == []
                assert len(saved.compiled) == 1
                return
            replies = await asyncio.gather(
                *[
                    _submit(sessions, adapter, replacement)
                    for _ in range(2 if loss == "concurrent" else 1)
                ]
            )
            handle = env.client.get_workflow_handle(workflow_id)
            assert (await asyncio.wait_for(handle.result(), 10))["status"] == "success"
            assert len(_OBSERVED) == 1
            actual_run_id, actual_input = _OBSERVED[0]
            assert {r.run_id for r in replies} == {actual_run_id}
            assert actual_input["title"] == "Original admission"
            assert (
                actual_input["initial_parameters"]["workflow"]["instructions"]
                == "Keep original inputs"
            )
            assert actual_input["scheduled_for"] == original_time.isoformat()
            assert len(saved.compiled) == 1
            assert len(attempts) == (
                3
                if loss in {"before", "concurrent", "old-unattempted", "race-completed"}
                else 2 if loss == "auto-before" else 1
            )
            async with sessions() as session:
                record = await session.get(
                    TemporalExecutionCanonicalRecord, workflow_id
                )
                assert record.memo["temporalStart"]["state"] == "confirmed"
                links = (
                    (
                        await session.execute(
                            select(TemporalArtifactLink).where(
                                TemporalArtifactLink.workflow_id == workflow_id,
                                TemporalArtifactLink.link_type
                                == "input.execution_plan",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert links and {link.run_id for link in links} == {actual_run_id}
