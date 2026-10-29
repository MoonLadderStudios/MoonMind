"""Fresh recurring roots wait before implementation, including after replay."""

from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


class _Admission(MoonMindRunWorkflow):
    def _execute_kwargs_for_route(self, route):
        return {
            "task_queue": workflow.info().task_queue,
            "start_to_close_timeout": timedelta(seconds=5),
        }

    def _set_state(self, state, **_kwargs):
        self._state = state


@workflow.defn(name="RecurringAdmissionFixture")
class _Fixture:
    @workflow.run
    async def run(self, case: str) -> dict:
        owner = _Admission()
        parameters = {
            "system": {"recurrence": {"definitionId": "definition"}},
            "task": {
                "steps": [
                    {
                        "tool": {
                            "id": "github.load_issue_preset_brief",
                            "inputs": {"issueSearch": "repo:org/repo"},
                        }
                    }
                ]
            },
        }
        if case == "explicit":
            parameters["task"]["steps"][0]["tool"]["inputs"] = {"issueNumber": 1}
        await owner._await_recurring_implementation_admission(parameters)
        return {
            "awaiting": owner._awaiting_external,
            "reason": owner._waiting_reason,
            "state": owner._state,
            "canceled": owner._cancel_requested,
        }


class _Activities:
    def __init__(self):
        self.calls = 0

    @activity.defn(name="recurring.admit_implementation")
    async def admit(self, _payload: dict) -> dict:
        self.calls += 1
        return {"status": "waiting" if self.calls == 1 else "admitted"}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["search", "explicit"])
async def test_production_admission_wait_resumes_and_replays(case):
    activities = _Activities()
    queue = "test-issue-implementation-admission-" + case
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_Fixture],
            activities=[activities.admit],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _Fixture.run, case, id=queue, task_queue=queue
            )
            result = await handle.result()
            assert not result["awaiting"]
            assert result["reason"] is None
            assert not result["canceled"]
            if case == "search":
                assert activities.calls == 2
                assert result["state"] == "planning"
            else:
                assert activities.calls == 0
            history = await handle.fetch_history()
    await Replayer(
        workflows=[_Fixture], workflow_runner=UnsandboxedWorkflowRunner()
    ).replay_workflow(history)
