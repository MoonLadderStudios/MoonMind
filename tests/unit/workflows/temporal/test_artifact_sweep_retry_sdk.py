"""The real SDK retains the sweep's first schedule time across retries."""

from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.artifacts import (
    LifecycleSweepSummary,
    TemporalArtifactActivities,
)


@workflow.defn
class _SweepRetryWindowWorkflow:
    @workflow.run
    async def run(self) -> None:
        await workflow.execute_activity(
            "sweep-retry-window-probe",
            start_to_close_timeout=timedelta(minutes=45),
            schedule_to_close_timeout=timedelta(minutes=50),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(minutes=24), maximum_attempts=2
            ),
        )


@pytest.mark.asyncio
async def test_real_activity_retry_keeps_original_sweep_window() -> None:
    observed = []

    class Service:
        async def drain_lifecycle(self, *, time_budget, **kwargs):
            info = activity.info()
            observed.append((info, time_budget))
            if info.attempt == 1:
                raise ApplicationError("Simulated worker interruption")
            return LifecycleSweepSummary("retry", 0, 0, 0, drained=False)

    activities = TemporalArtifactActivities(Service())

    @activity.defn(name="sweep-retry-window-probe")
    async def sweep() -> None:
        await activities.artifact_lifecycle_sweep(principal="service:lifecycle")

    queue = f"artifact-sweep-retry-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[_SweepRetryWindowWorkflow],
            activities=[sweep],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            await environment.client.execute_workflow(
                _SweepRetryWindowWorkflow.run,
                id=str(uuid4()),
                task_queue=queue,
            )

    assert len(observed) == 2
    first, retry = observed
    assert retry[0].attempt == 2
    assert retry[0].scheduled_time == first[0].scheduled_time
    assert retry[0].started_time - retry[0].scheduled_time >= timedelta(minutes=24)
    assert timedelta(minutes=39) < first[1] <= timedelta(minutes=40)
    assert timedelta(minutes=15) < retry[1] <= timedelta(minutes=16)
