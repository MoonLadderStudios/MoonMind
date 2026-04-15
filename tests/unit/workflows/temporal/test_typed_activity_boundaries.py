from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentRunStatus
from moonmind.schemas.temporal_activity_models import (
    AgentRuntimeFetchResultInput,
    ExternalAgentRunInput,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.typed_execution import execute_typed_activity


def test_external_agent_run_input_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ExternalAgentRunInput.model_validate(
            {"runId": "run-1", "providerPayload": {"raw": True}}
        )


def test_external_agent_run_input_validates_legacy_alias_at_boundary() -> None:
    request = ExternalAgentRunInput.model_validate({"external_id": "ext-1"})

    assert request.run_id == "ext-1"
    assert request.model_dump(mode="json", by_alias=True) == {"runId": "ext-1"}


def test_agent_runtime_fetch_result_input_rejects_unsupported_publish_mode() -> None:
    with pytest.raises(ValidationError):
        AgentRuntimeFetchResultInput.model_validate(
            {"runId": "managed-1", "publishMode": "side-channel"}
        )


@activity.defn(name="typed.boundary.status")
async def _typed_status_activity(request: ExternalAgentRunInput) -> AgentRunStatus:
    assert isinstance(request, ExternalAgentRunInput)
    return AgentRunStatus(
        runId=request.run_id,
        agentKind="external",
        agentId="jules",
        status="completed",
        observedAt=datetime.now(tz=UTC),
    )


@workflow.defn(name="TypedActivityBoundaryWorkflow")
class _TypedActivityBoundaryWorkflow:
    @workflow.run
    async def run(self, run_id: str) -> AgentRunStatus:
        return await execute_typed_activity(
            "typed.boundary.status",
            ExternalAgentRunInput(runId=run_id),
            start_to_close_timeout=timedelta(seconds=30),
        )


@pytest.mark.asyncio
async def test_typed_activity_request_round_trips_through_temporal_worker() -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        async with Worker(
            env.client,
            task_queue="typed-boundary-test",
            workflows=[_TypedActivityBoundaryWorkflow],
            activities=[_typed_status_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                _TypedActivityBoundaryWorkflow.run,
                "round-trip-1",
                id="typed-boundary-round-trip",
                task_queue="typed-boundary-test",
            )

    assert isinstance(result, AgentRunStatus)
    assert result.run_id == "round-trip-1"
    assert result.status == "completed"
