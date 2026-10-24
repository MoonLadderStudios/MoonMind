"""One context producer retains admission authority across artifact commits."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.service import RPCError, RPCStatusCode

from api_service.db.models import TemporalExecutionRemediationLink
from moonmind.workflows.temporal.artifacts import TemporalArtifactService
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionStartUnconfirmedError,
)
from tests.integration import test_execution_input_admission_postgres as fixtures

sessions = fixtures.sessions
conversion_postgres_url = fixtures.conversion_postgres_url
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def test_concurrent_remediation_retry_does_not_overlap_context_producers(
    sessions, monkeypatch
):
    starts = []
    started = {}

    async def start(**kwargs):
        starts.append(kwargs)
        result = SimpleNamespace(run_id=f"actual-run-{len(starts)}")
        started[kwargs["workflow_id"]] = result
        return result

    async def describe(workflow_id):
        if workflow_id in started:
            return started[workflow_id]
        raise RPCError("Not delivered", RPCStatusCode.NOT_FOUND, b"")

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(side_effect=start),
        describe_workflow=AsyncMock(side_effect=describe),
        namespace_retention_seconds=AsyncMock(return_value=86400),
    )

    def service(session):
        owner = TemporalExecutionService(session, client_adapter=adapter)
        owner.check_system_paused = AsyncMock(return_value=False)
        owner._enforce_worker_code_freshness = AsyncMock()
        owner._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        owner._query_step_ledger_rows = AsyncMock(return_value=[])
        return owner

    create = {
        "workflow_type": "MoonMind.UserWorkflow",
        "owner_id": None,
        "title": "Target",
        "input_artifact_ref": None,
        "plan_artifact_ref": None,
        "manifest_artifact_ref": None,
        "failure_policy": None,
        "initial_parameters": {"workflow": {"instructions": "Retain target"}},
        "idempotency_key": None,
    }
    async with sessions() as session:
        target = await service(session).create_execution(**create)
    context_created = asyncio.Event()
    release_context = asyncio.Event()
    retry_found = asyncio.Event()
    contexts = []
    actual_create = TemporalArtifactService.create

    async def artifact_create(self, **kwargs):
        result = await actual_create(self, **kwargs)
        if (
            kwargs.get("metadata_json", {}).get("artifact_type")
            == "remediation.context"
        ):
            contexts.append(result[0].artifact_id)
            if len(contexts) == 1:
                context_created.set()
                await release_context.wait()
        return result

    monkeypatch.setattr(TemporalArtifactService, "create", artifact_create)
    request = {
        **create,
        "title": "Remediation",
        "initial_parameters": {
            "workflow": {
                "instructions": "Inspect target",
                "remediation": {"target": {"workflowId": target.workflow_id}},
            }
        },
        "idempotency_key": "one-remediation",
    }

    async def writer(*, retry=False):
        async with sessions() as session:
            owner = service(session)
            if retry:
                actual_find = owner.find_create_execution

                async def find(**kwargs):
                    result = await actual_find(**kwargs)
                    assert result is not None
                    retry_found.set()
                    return result

                owner.find_create_execution = find
            return await owner.create_execution(**request)

    first = asyncio.create_task(writer())
    await asyncio.wait_for(context_created.wait(), 15)
    second = asyncio.create_task(writer(retry=True))
    await asyncio.wait_for(retry_found.wait(), 10)
    # The second caller has observed the committed pending admission while its
    # first context producer is suspended after the artifact owner's commit.
    await asyncio.sleep(0.2)
    release_context.set()
    try:
        results = await asyncio.wait_for(asyncio.gather(first, second), 30)
    finally:
        release_context.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert len(contexts) == 1
    assert len(starts) == 2  # target and exactly one remediation
    assert len({r.workflow_id for r in results}) == 1
    async with sessions() as session:
        link = await session.get(
            TemporalExecutionRemediationLink, results[0].workflow_id
        )
        assert link.context_artifact_ref == contexts[0]


async def test_same_session_remediation_retry_reuses_saved_context(
    sessions, monkeypatch
):
    starts = []
    contexts = []
    actual_create = TemporalArtifactService.create

    async def artifact_create(self, **kwargs):
        result = await actual_create(self, **kwargs)
        if (
            kwargs.get("metadata_json", {}).get("artifact_type")
            == "remediation.context"
        ):
            contexts.append(result[0].artifact_id)
        return result

    monkeypatch.setattr(TemporalArtifactService, "create", artifact_create)

    async def start(**kwargs):
        starts.append(kwargs)
        if len(starts) in {2, 3}:
            raise ConnectionError("Injected nondelivery before remediation start")
        return SimpleNamespace(run_id=f"actual-run-{len(starts)}")

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(side_effect=start),
        describe_workflow=AsyncMock(
            side_effect=RPCError("Not delivered", RPCStatusCode.NOT_FOUND, b"")
        ),
        namespace_retention_seconds=AsyncMock(return_value=86400),
    )
    async with sessions() as session:
        owner = TemporalExecutionService(session, client_adapter=adapter)
        owner.check_system_paused = AsyncMock(return_value=False)
        owner._enforce_worker_code_freshness = AsyncMock()
        owner._sync_projection_best_effort = AsyncMock(side_effect=lambda r: r)
        owner._query_step_ledger_rows = AsyncMock(return_value=[])
        create = {
            "workflow_type": "MoonMind.UserWorkflow",
            "owner_id": None,
            "title": "Target",
            "input_artifact_ref": None,
            "plan_artifact_ref": None,
            "manifest_artifact_ref": None,
            "failure_policy": None,
            "initial_parameters": {"workflow": {"instructions": "Retain target"}},
            "idempotency_key": None,
        }
        target = await owner.create_execution(**create)
        request = {
            **create,
            "title": "Remediation",
            "initial_parameters": {
                "workflow": {
                    "instructions": "Inspect target",
                    "remediation": {"target": {"workflowId": target.workflow_id}},
                }
            },
            "idempotency_key": "same-session-retry",
        }
        with pytest.raises(TemporalExecutionStartUnconfirmedError) as pending:
            await owner.create_execution(**request)
        assert pending.value.detail["reason"] == "start_not_confirmed"
        assert len(contexts) == 1
        remediation = await owner.create_execution(**request)
        assert len(contexts) == 1
        assert len(starts) == 4  # target, two failed attempts, one successful retry
        assert {call["workflow_id"] for call in starts[1:]} == {remediation.workflow_id}
        assert remediation.run_id == "actual-run-4"
        link = await session.get(
            TemporalExecutionRemediationLink,
            remediation.workflow_id,
            populate_existing=True,
        )
        assert link.context_artifact_ref == contexts[0]
        assert link.remediation_run_id == remediation.run_id
