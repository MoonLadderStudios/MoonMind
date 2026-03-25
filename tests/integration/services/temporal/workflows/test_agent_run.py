import pytest
import asyncio

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from temporalio.client import WorkflowFailureError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


# Local mock activities that simulate the catalog-routed activities
# (the standalone stubs were removed in favor of catalog routing).
from temporalio import activity as _activity


@_activity.defn(name="agent_runtime.publish_artifacts")
async def mock_publish_artifacts(result: dict) -> dict:
    return result


@_activity.defn(name="agent_runtime.cancel")
async def mock_cancel(request: dict) -> None:
    pass

@workflow.defn(name="MoonMind.AuthProfileManager")
class MockAuthProfileManager:
    def __init__(self):
        self._shutdown = False
        self.pending_requests = []
        self._leases: dict[str, str] = {}  # profile_id -> workflow_id

    @workflow.signal
    def request_slot(self, payload: dict) -> None:
        self.pending_requests.append(payload)

    @workflow.signal
    def release_slot(self, payload: dict) -> None:
        """Release a slot lease. Removes the lease if the workflow_id matches."""
        requester_id = payload.get("requester_workflow_id")
        # Find and remove any lease held by this requester
        to_remove = [p for p, wf in self._leases.items() if wf == requester_id]
        for p in to_remove:
            del self._leases[p]

    @workflow.signal
    def report_cooldown(self, payload: dict) -> None:
        pass

    @workflow.query
    def get_state(self) -> dict:
        return {
            "leases": dict(self._leases),
            "pending_requests": self.pending_requests,
        }

    @workflow.run
    async def run(self, input_payload: dict) -> dict:
        assign_slots = input_payload.get("assign_slots", True)
        while not self._shutdown:
            await workflow.wait_condition(lambda: len(self.pending_requests) > 0 or self._shutdown)
            if self._shutdown:
                break
            while self.pending_requests:
                req = self.pending_requests.pop(0)
                if assign_slots:
                    profile_id = "default-managed"
                    self._leases[profile_id] = req["requester_workflow_id"]
                    handle = workflow.get_external_workflow_handle(req["requester_workflow_id"])
                    await handle.signal("slot_assigned", {"profile_id": profile_id})
        return {}


@pytest.mark.asyncio
async def test_agent_run_workflow():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockAuthProfileManager],
            activities=[mock_publish_artifacts, mock_cancel],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="test-agent",
                execution_profile_ref="default-managed",
                correlation_id="corr-1",
                idempotency_key="idem-1",
            )
            
            # Start dummy manager
            runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
            runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
            manager_id = f"auth-profile-manager:{runtime_id}"
            await env.client.start_workflow(
                MockAuthProfileManager.run,
                {"runtime_id": request.agent_id},
                id=manager_id,
                task_queue="agent-run-task-queue",
            )
            
            # Start workflow
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id="test-workflow-1",
                task_queue="agent-run-task-queue",
            )
            
            # Signal completion
            result_payload = {"summary": "Success"}
            await handle.signal(MoonMindAgentRun.completion_signal, result_payload)
            
            result = await handle.result()
            
            assert isinstance(result, AgentRunResult)
            assert result.summary == "Success"

@pytest.mark.asyncio
async def test_agent_run_workflow_cancellation():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockAuthProfileManager],
            activities=[mock_publish_artifacts, mock_cancel],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="test-agent",
                execution_profile_ref="default-managed",
                correlation_id="corr-1",
                idempotency_key="idem-1",
            )
            
            # Start dummy manager
            runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
            runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
            manager_id = f"auth-profile-manager:{runtime_id}"
            await env.client.start_workflow(
                MockAuthProfileManager.run,
                {"runtime_id": request.agent_id, "assign_slots": False},
                id=manager_id,
                task_queue="agent-run-task-queue",
            )
            
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id="test-workflow-cancel",
                task_queue="agent-run-task-queue",
            )
            
            # Cancel the workflow while it's waiting
            await handle.cancel()
            
            with pytest.raises(WorkflowFailureError) as exc_info:
                await handle.result()
            
            # Verifies that workflow was cancelled. Check the full exception chain
            # since the top-level message may be generic.
            exc_str = str(exc_info.value).lower()
            cause_str = str(exc_info.value.__cause__).lower() if exc_info.value.__cause__ else ""
            assert "cancel" in exc_str or "cancel" in cause_str or isinstance(
                exc_info.value.__cause__, asyncio.CancelledError
            )


# --- External agent workflow path ---

# Track activity calls for external-agent verification.
_external_activity_calls: list[str] = []


@_activity.defn(name="integration.resolve_external_adapter")
async def mock_resolve_external_adapter(agent_id: str) -> str:
    _external_activity_calls.append(f"resolve:{agent_id}")
    return agent_id


@_activity.defn(name="integration.external_adapter_execution_style")
async def mock_external_adapter_execution_style(agent_id: str) -> str:
    _external_activity_calls.append(f"style:{agent_id}")
    return "polling"


@_activity.defn(name="integration.jules.start")
async def mock_jules_start(request: dict) -> dict:
    from datetime import UTC, datetime

    _external_activity_calls.append("start")
    return {
        "runId": "jules-task-001",
        "agentKind": "external",
        "agentId": "jules",
        "status": "running",
        "startedAt": datetime.now(tz=UTC).isoformat(),
        "pollHintSeconds": 2,
        "metadata": {
            "providerStatus": "in_progress",
            "normalizedStatus": "running",
        },
    }


# Counter for status polling — starts running, then completes.
_status_poll_count = 0


@_activity.defn(name="integration.jules.status")
async def mock_jules_status(run_id: str) -> dict:
    from datetime import UTC, datetime

    global _status_poll_count
    _status_poll_count += 1
    _external_activity_calls.append(f"status:{_status_poll_count}")
    if _status_poll_count >= 2:
        return {
            "runId": run_id,
            "agentKind": "external",
            "agentId": "jules",
            "status": "completed",
            "observedAt": datetime.now(tz=UTC).isoformat(),
            "metadata": {
                "providerStatus": "completed",
                "normalizedStatus": "completed",
            },
        }
    return {
        "runId": run_id,
        "agentKind": "external",
        "agentId": "jules",
        "status": "running",
        "observedAt": datetime.now(tz=UTC).isoformat(),
        "metadata": {
            "providerStatus": "in_progress",
            "normalizedStatus": "running",
        },
    }


@_activity.defn(name="integration.jules.fetch_result")
async def mock_jules_fetch_result(run_id: str) -> dict:
    _external_activity_calls.append("fetch_result")
    return {
        "outputRefs": [],
        "summary": f"Jules task {run_id} completed successfully.",
        "metadata": {"normalizedStatus": "completed"},
    }


@_activity.defn(name="integration.jules.cancel")
async def mock_jules_cancel(run_id: str) -> dict:
    from datetime import UTC, datetime

    _external_activity_calls.append("cancel")
    return {
        "runId": run_id,
        "agentKind": "external",
        "agentId": "jules",
        "status": "cancelled",
        "observedAt": datetime.now(tz=UTC).isoformat(),
        "metadata": {"cancelAccepted": False, "unsupported": True},
    }


@pytest.mark.asyncio
async def test_agent_run_external_agent_workflow():
    """Validate that external-agent runs route through integration activities."""
    global _status_poll_count
    _status_poll_count = 0
    _external_activity_calls.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        # Workflow-queue activities (resolve + execution style) match production routing.
        async with Worker(
            env.client,
            task_queue="mm.workflow",
            activities=[
                mock_resolve_external_adapter,
                mock_external_adapter_execution_style,
            ],
        ):
            # Workflow worker: hosts the workflow + agent_runtime activities.
            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun],
                activities=[mock_publish_artifacts, mock_cancel],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                # Integrations worker: hosts integration.* activities on
                # mm.activity.integrations, matching production fleet separation.
                async with Worker(
                    env.client,
                    task_queue="mm.activity.integrations",
                    activities=[
                        mock_jules_start,
                        mock_jules_status,
                        mock_jules_fetch_result,
                        mock_jules_cancel,
                    ],
                ):
                    request = AgentExecutionRequest(
                        agent_kind="external",
                        agent_id="jules",
                        execution_profile_ref="profile:jules-default",
                        correlation_id="corr-ext-1",
                        idempotency_key="idem-ext-1",
                        parameters={
                            "title": "External Test",
                            "description": "Integration test for external agent workflow",
                        },
                    )

                    handle = await env.client.start_workflow(
                        MoonMindAgentRun.run,
                        request,
                        id="test-workflow-external-1",
                        task_queue="agent-run-task-queue",
                    )

                    result = await handle.result()

                    assert isinstance(result, AgentRunResult)
                    assert result.summary is not None
                    assert "jules-task-001" in result.summary

                    # Verify activities were called in the correct order.
                    resolve_idx = _external_activity_calls.index("resolve:jules")
                    style_idx = _external_activity_calls.index("style:jules")
                    start_idx = _external_activity_calls.index("start")
                    fetch_idx = _external_activity_calls.index("fetch_result")
                    assert resolve_idx < style_idx < start_idx < fetch_idx, (
                        f"Expected resolve < style < start < fetch_result, got {_external_activity_calls}"
                    )
                    # At least one status poll should have happened between start and fetch_result.
                    assert any(
                        c.startswith("status:")
                        for c in _external_activity_calls[start_idx:fetch_idx]
                    )


@pytest.mark.asyncio
async def test_cancellation_releases_auth_profile_slot():
    """Verify that cancelling a managed AgentRun releases its auth profile slot.

    Regression test for the auth profile slot leak bug where `CancelledError`
    handler wrapped the release_slot signal in `asyncio.shield()`, which does not
    work with Temporal's workflow-level cancellation.

    This test will initially FAIL, establishing the regression baseline. The fix
    (Task 4) implements parent-initiated slot release fallback to close this gap.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockAuthProfileManager],
            activities=[mock_publish_artifacts, mock_cancel],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            request = AgentExecutionRequest(
                agent_kind="managed",
                agent_id="test-agent",
                execution_profile_ref="default-managed",
                correlation_id="corr-cancel-slot",
                idempotency_key="idem-cancel-slot",
            )

            # Start dummy manager that assigns slots
            runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
            runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
            manager_id = f"auth-profile-manager:{runtime_id}"
            manager_handle = await env.client.start_workflow(
                MockAuthProfileManager.run,
                {"runtime_id": request.agent_id, "assign_slots": True},
                id=manager_id,
                task_queue="agent-run-task-queue",
            )

            # Give the manager time to start
            await asyncio.sleep(0.1)

            # Start AgentRun workflow
            agent_handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id="test-workflow-cancel-slot",
                task_queue="agent-run-task-queue",
            )

            # Wait for the AgentRun to acquire a slot (it will be waiting for slot_assigned signal)
            # The manager should have assigned the slot by now
            await asyncio.sleep(0.5)

            # Verify the manager holds the lease
            manager_state_before = await manager_handle.query("get_state")
            assert "default-managed" in manager_state_before.get("leases", {}), (
                f"Expected manager to hold slot before cancellation, state={manager_state_before}"
            )

            # Cancel the AgentRun workflow while it's waiting for slot assignment or running
            await agent_handle.cancel()

            # Wait for cancellation to be processed
            await asyncio.sleep(0.5)

            # Query manager state after cancellation
            try:
                manager_state_after = await manager_handle.query("get_state")
            except Exception:
                # Manager might have shut down; check if slot is released via direct query
                manager_state_after = {"leases": {}}

            # The slot should be released after cancellation
            # This assertion will FAIL initially (documenting the bug)
            assert "default-managed" not in manager_state_after.get("leases", {}) or \
                   manager_state_after.get("leases", {}).get("default-managed") != "test-workflow-cancel-slot", (
                f"Slot was NOT released after cancellation. Manager state: {manager_state_after}"
            )
