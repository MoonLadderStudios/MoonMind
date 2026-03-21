import pytest

from temporalio import activity, workflow
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from temporalio.testing import WorkflowEnvironment

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


@activity.defn(name="auth_profile.list")
async def _mock_auth_profile_list(_payload: dict) -> dict:
    return {
        "profiles": [
            {
                "profile_id": "default-managed",
                "runtime_id": "gemini_cli",
                "auth_mode": "oauth",
                "enabled": True,
                "max_parallel_runs": 1,
            }
        ]
    }


@workflow.defn(name="MoonMind.AuthProfileManager")
class _NoAssignAuthProfileManager:
    def __init__(self) -> None:
        self.pending_requests: list[dict] = []

    @workflow.signal
    def request_slot(self, payload: dict) -> None:
        self.pending_requests.append(payload)

    @workflow.signal
    def sync_profiles(self, payload: dict) -> None:
        # No-op for workflow tests.
        return None

    @workflow.run
    async def run(self, _input_payload: dict) -> dict:
        # Never signal slot assignment. AgentRun must timeout on its own.
        while True:
            await workflow.wait_condition(lambda: len(self.pending_requests) > 0)
            self.pending_requests.clear()


@pytest.mark.asyncio
async def test_managed_slot_wait_timeout_respects_request_timeout_budget() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="mm.activity.artifacts",
                activities=[_mock_auth_profile_list],
            ),
            Worker(
                env.client,
                task_queue="agent-run-slot-timeout-tests",
                workflows=[MoonMindAgentRun, _NoAssignAuthProfileManager],
                # This path exits before publish/cancel activities are used.
                activities=[],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="gemini_cli",
                execution_profile_ref="default-managed",
                correlation_id="corr-slot-timeout-1",
                idempotency_key="idem-slot-timeout-1",
                timeout_policy={"timeout_seconds": 30},
            )

            manager_id = "auth-profile-manager:gemini_cli"
            await env.client.start_workflow(
                _NoAssignAuthProfileManager.run,
                {"runtime_id": "gemini_cli"},
                id=manager_id,
                task_queue="agent-run-slot-timeout-tests",
            )

            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id="test-slot-timeout-budget",
                task_queue="agent-run-slot-timeout-tests",
            )

            result = await handle.result()
            assert isinstance(result, AgentRunResult)
            assert result.failure_class == "execution_error"

            desc = await handle.describe()
            assert desc.close_time is not None
            elapsed_seconds = (desc.close_time - desc.start_time).total_seconds()
            assert elapsed_seconds <= 45
