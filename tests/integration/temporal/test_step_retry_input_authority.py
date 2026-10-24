"""A retained run recovers its Skill inputs before a real Temporal reset."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import TemporalExecutionCanonicalRecord
from moonmind.workflows.temporal import retry_from_step
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.unit.omnigent import test_step_retry_input_authority as input_fixtures

artifacts = input_fixtures.artifacts
auth_mode = input_fixtures.auth_mode
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@workflow.defn(name="MoonMind.UserWorkflow")
class SkillInputWorkflow:
    @workflow.run
    async def run(self, inputs: dict) -> str:
        workflow.upsert_memo({"mm_current_step_order": 1})
        await workflow.execute_activity(
            "input_retry.completed_effect", start_to_close_timeout=timedelta(seconds=10)
        )
        workflow.upsert_memo({"mm_current_step_order": 2})
        return await workflow.execute_activity(
            "input_retry.read_skill",
            inputs["initial_parameters"]["resolvedSkillsetRef"],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


async def test_real_step_retry_restores_inputs_and_keeps_completed_effects(
    artifacts, monkeypatch
):
    replay = retry_from_step.replay_failure_before_reset
    _, workflow_id, admission_adapter = await input_fixtures._failed_admission(
        artifacts, monkeypatch
    )
    monkeypatch.setattr(retry_from_step, "replay_failure_before_reset", replay)
    monkeypatch.setattr(
        retry_from_step, "_replay_workflows", lambda: [SkillInputWorkflow]
    )
    calls = []

    @activity.defn(name="input_retry.completed_effect")
    async def completed_effect():
        calls.append("completed")

    @activity.defn(name="input_retry.read_skill")
    async def read_skill(artifact_id: str) -> str:
        calls.append("read")
        async with artifacts() as session:
            reader = TemporalArtifactService(TemporalArtifactRepository(session))
            principal = f"workflow:{activity.info().workflow_id}"
            _, manifest = await reader.read(
                artifact_id=artifact_id, principal=principal
            )
            body_id = json.loads(manifest)["skills"][0]["content_ref"]
            return (await reader.read(artifact_id=body_id, principal=principal))[
                1
            ].decode()

    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        async with Worker(
            env.client,
            task_queue="step-retry-input-test",
            workflows=[SkillInputWorkflow],
            activities=[completed_effect, read_skill],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                SkillInputWorkflow.run,
                admission_adapter.start_workflow.call_args.kwargs["input_args"],
                id=workflow_id,
                task_queue="step-retry-input-test",
            )
            with pytest.raises(WorkflowFailureError):
                await handle.result()
            assert calls == ["completed", "read"]
            async with artifacts() as session:
                record = await session.get(
                    TemporalExecutionCanonicalRecord, workflow_id
                )
                record.run_id = handle.first_execution_run_id
                await session.commit()
                service = TemporalExecutionService(
                    session, client_adapter=TemporalClientAdapter(env.client)
                )
                monkeypatch.setattr(
                    service, "_sync_retried_run_projection", AsyncMock()
                )
                kwargs = dict(
                    ledger_rows=[
                        {"logicalStepId": "step-1", "order": 1, "status": "completed"},
                        {"logicalStepId": "step-2", "order": 2, "status": "failed"},
                    ],
                    idempotency_key="restore-original-inputs",
                    expected_run_id=handle.first_execution_run_id,
                )
                response = await service.retry_execution_from_step(record, **kwargs)
                assert response["stepId"] == "step-2"
                assert response["workflowId"] == workflow_id
                assert response["runId"] != handle.first_execution_run_id
                result = await asyncio.wait_for(
                    env.client.get_workflow_handle(
                        workflow_id, run_id=response["runId"]
                    ).result(),
                    timeout=20,
                )
                assert "Use api-create." in result
                assert (
                    await service.retry_execution_from_step(record, **kwargs)
                    == response
                )
    assert calls == ["completed", "read", "read"]
