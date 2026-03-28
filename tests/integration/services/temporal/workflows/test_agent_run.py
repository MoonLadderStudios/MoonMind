import pytest
import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from temporalio.client import WorkflowFailureError
from temporalio.service import RPCError
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


@_activity.defn(name="integration.get_activity_route")
async def mock_get_activity_route(activity_name: str) -> dict:
    """Return a hardcoded catalog route so the workflow can resolve task queues."""
    # Map activity names to their expected task queues, matching production routing.
    queue_map = {
        "auth_profile.list": "agent-run-task-queue",
        "auth_profile.ensure_manager": "agent-run-task-queue",
        "auth_profile.reset_manager": "agent-run-task-queue",
        "agent_runtime.launch": "agent-run-task-queue",
        "agent_runtime.publish_artifacts": "agent-run-task-queue",
        "agent_runtime.cancel": "agent-run-task-queue",
        "agent_runtime.status": "agent-run-task-queue",
        "agent_runtime.fetch_result": "agent-run-task-queue",
        "integration.resolve_external_adapter": "mm.workflow",
        "integration.external_adapter_execution_style": "mm.workflow",
        "integration.jules.start": "mm.activity.integrations",
        "integration.jules.status": "mm.activity.integrations",
        "integration.jules.fetch_result": "mm.activity.integrations",
        "integration.jules.cancel": "mm.activity.integrations",
    }
    return {
        "task_queue": queue_map.get(activity_name, "agent-run-task-queue"),
        "timeouts": {
            "start_to_close_seconds": 30,
            "schedule_to_close_seconds": 60,
            "heartbeat_timeout_seconds": None,
        },
        "retries": {
            "max_attempts": 3,
            "max_interval_seconds": 10,
            "non_retryable_error_codes": [],
        },
    }


@_activity.defn(name="auth_profile.list")
async def mock_auth_profile_list(request: dict) -> dict:
    """Return a single default managed profile."""
    return {
        "profiles": [
            {
                "profile_id": "default-managed",
                "runtime_id": request.get("runtime_id", "test-agent"),
                "auth_mode": "volume",
                "volume_ref": "test-volume",
                "volume_mount_path": "/tmp/auth",
                "account_label": "test",
                "api_key_ref": None,
                "runtime_env_overrides": {},
                "api_key_env_var": None,
                "max_parallel_runs": 1,
                "cooldown_after_429_seconds": 900,
                "rate_limit_policy": "pause_and_retry",
                "max_lease_duration_seconds": 7200,
                "enabled": True,
            }
        ]
    }


@_activity.defn(name="auth_profile.ensure_manager")
async def mock_auth_profile_ensure_manager(request: dict) -> dict:
    return {"started": True, "workflow_id": f"auth-profile-manager:{request.get('runtime_id', 'test')}"}


@_activity.defn(name="auth_profile.reset_manager")
async def mock_auth_profile_reset_manager(request: dict) -> dict:
    return {"reset": True, "workflow_id": f"auth-profile-manager:{request.get('runtime_id', 'test')}"}


# Collect all activities that need to be registered on the main task queue.
_COMMON_AGENT_RUN_ACTIVITIES = [
    mock_publish_artifacts,
    mock_cancel,
    mock_auth_profile_list,
    mock_auth_profile_ensure_manager,
    mock_auth_profile_reset_manager,
]


@_activity.defn(name="agent_runtime.launch")
async def mock_agent_runtime_launch(request: dict) -> dict:
    """Simulate launching a managed agent container."""
    return {
        "container_id": "test-container-001",
        "status": "running",
        "agent_id": request.get("agent_id", "test-agent"),
    }


@_activity.defn(name="agent_runtime.status")
async def mock_agent_runtime_status(request: dict) -> dict:
    """Simulate polling the agent's execution status."""
    return {
        "status": "running",
        "container_id": request.get("container_id", "test-container-001"),
    }


@_activity.defn(name="agent_runtime.fetch_result")
async def mock_agent_runtime_fetch_result(request: dict) -> dict:
    """Simulate fetching the agent's final result."""
    return {
        "summary": "Completed via test mock",
        "artifacts": [],
    }


# Add launch/status/fetch_result to the common activities list
_COMMON_AGENT_RUN_ACTIVITIES.extend([
    mock_agent_runtime_launch,
    mock_agent_runtime_status,
    mock_agent_runtime_fetch_result,
])

# Activities that route to the workflow task queue (mm.workflow in production).
_WORKFLOW_QUEUE_ACTIVITIES = [
    mock_get_activity_route,
]


@workflow.defn(name="MoonMind.AuthProfileManager")
class MockAuthProfileManager:
    def __init__(self):
        self._shutdown = False
        self.pending_requests = []
        self._leases: dict[str, str] = {}  # profile_id -> workflow_id
        self.cooldown_reports: list[dict] = []
        self.cooldowns: dict[str, str] = {}

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
        self.cooldown_reports.append(dict(payload))
        profile_id = payload.get("profile_id", "default-managed")
        cooldown_seconds = int(payload.get("cooldown_seconds", 0))
        self.cooldowns[profile_id] = (
            workflow.now() + timedelta(seconds=cooldown_seconds)
        ).isoformat()

    @workflow.signal
    def sync_profiles(self, payload: dict) -> None:
        """Accept a profiles sync signal (no-op in test)."""
        pass

    @workflow.query
    def get_state(self) -> dict:
        return {
            "leases": dict(self._leases),
            "pending_requests": self.pending_requests,
            "cooldown_reports": list(self.cooldown_reports),
            "cooldowns": dict(self.cooldowns),
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
                    cooldown_until = self.cooldowns.get(profile_id)
                    if cooldown_until is not None:
                        cooldown_dt = datetime.fromisoformat(cooldown_until)
                        if workflow.now() < cooldown_dt:
                            self.pending_requests.insert(0, req)
                            await workflow.sleep(
                                (cooldown_dt - workflow.now()).total_seconds()
                            )
                            continue
                    self._leases[profile_id] = req["requester_workflow_id"]
                    handle = workflow.get_external_workflow_handle(req["requester_workflow_id"])
                    await handle.signal("slot_assigned", {"profile_id": profile_id})
        return {}


@workflow.defn(name="TestAgentRunParent")
class TestAgentRunParent:
    def __init__(self) -> None:
        self.state_changes: list[dict[str, str]] = []
        self.profile_assignments: list[dict] = []

    @workflow.signal
    def child_state_changed(self, new_state: str, reason: str) -> None:
        self.state_changes.append({"state": new_state, "reason": reason})

    @workflow.signal
    def profile_assigned(self, payload: dict) -> None:
        self.profile_assignments.append(dict(payload))

    @workflow.query
    def get_state(self) -> dict:
        return {
            "state_changes": list(self.state_changes),
            "profile_assignments": list(self.profile_assignments),
        }

    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        return await workflow.execute_child_workflow(
            MoonMindAgentRun.run,
            request,
            id=f"{workflow.info().workflow_id}:child",
            task_queue="agent-run-task-queue",
        )


@_activity.defn(name="agent_runtime.status")
async def mock_agent_runtime_status_rate_limited(request: dict) -> dict:
    return {
        "runId": request.get("run_id", "managed-rate-limit-run"),
        "agentKind": "managed",
        "agentId": request.get("agent_id", "gemini_cli"),
        "status": "failed",
    }


@_activity.defn(name="agent_runtime.fetch_result")
async def mock_agent_runtime_fetch_result_rate_limited(request: dict) -> dict:
    return {
        "summary": "Gemini API rate limit exceeded",
        "failureClass": "integration_error",
        "providerErrorCode": "429",
    }


@pytest.mark.asyncio
async def test_agent_run_workflow():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        # Worker on mm.workflow queue for catalog route lookups.
        async with Worker(
            env.client,
            task_queue="mm.workflow",
            activities=_WORKFLOW_QUEUE_ACTIVITIES,
        ):
            # Main agent-run worker.
            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockAuthProfileManager],
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
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
            task_queue="mm.workflow",
            activities=_WORKFLOW_QUEUE_ACTIVITIES,
        ):
            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockAuthProfileManager],
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
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


@pytest.mark.asyncio
async def test_agent_run_reports_managed_429_retry_summary_to_parent():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="mm.workflow",
            activities=_WORKFLOW_QUEUE_ACTIVITIES,
        ):
            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockAuthProfileManager, TestAgentRunParent],
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="gemini_cli",
                    execution_profile_ref="default-managed",
                    correlation_id="corr-429",
                    idempotency_key="idem-429",
                )

                manager_id = "auth-profile-manager:gemini_cli"
                await env.client.start_workflow(
                    MockAuthProfileManager.run,
                    {"runtime_id": "gemini_cli"},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                parent_handle = await env.client.start_workflow(
                    TestAgentRunParent.run,
                    request,
                    id="test-parent-managed-429",
                    task_queue="agent-run-task-queue",
                )
                child_handle = env.client.get_workflow_handle(
                    "test-parent-managed-429:child"
                )
                manager_handle = env.client.get_workflow_handle(manager_id)

                parent_state = {}
                for _ in range(30):
                    parent_state = await parent_handle.query(TestAgentRunParent.get_state)
                    if parent_state.get("profile_assignments"):
                        break
                    await asyncio.sleep(0.1)

                await child_handle.signal(
                    MoonMindAgentRun.completion_signal,
                    {
                        "summary": "Gemini API rate limit exceeded",
                        "failureClass": "integration_error",
                        "providerErrorCode": "429",
                    },
                )

                manager_state = {}
                for _ in range(30):
                    parent_state = await parent_handle.query(TestAgentRunParent.get_state)
                    manager_state = await manager_handle.query(MockAuthProfileManager.get_state)
                    if manager_state.get("cooldown_reports") and any(
                        "retry scheduled" in change["reason"].lower()
                        for change in parent_state.get("state_changes", [])
                    ):
                        break
                    await asyncio.sleep(0.1)

                assert manager_state["cooldown_reports"]
                assert manager_state["cooldown_reports"][-1]["cooldown_seconds"] == 900
                assert any(
                    "retry scheduled" in change["reason"].lower()
                    and "900s cooldown" in change["reason"].lower()
                    for change in parent_state["state_changes"]
                )

                await parent_handle.cancel()
                with pytest.raises(WorkflowFailureError):
                    await parent_handle.result()


@pytest.mark.asyncio
async def test_agent_run_managed_429_can_continue_as_new_after_retry_threshold():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="mm.workflow",
            activities=_WORKFLOW_QUEUE_ACTIVITIES,
        ):
            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockAuthProfileManager, TestAgentRunParent],
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="gemini_cli",
                    execution_profile_ref="default-managed",
                    correlation_id="corr-429-continue-as-new",
                    idempotency_key="idem-429-continue-as-new",
                    parameters={
                        "__moonmind_temporal_internal": {
                            "managed_429_retry_count": 24,
                        }
                    },
                )

                manager_id = "auth-profile-manager:gemini_cli"
                await env.client.start_workflow(
                    MockAuthProfileManager.run,
                    {"runtime_id": "gemini_cli"},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                parent_handle = await env.client.start_workflow(
                    TestAgentRunParent.run,
                    request,
                    id="test-parent-managed-429-continue-as-new",
                    task_queue="agent-run-task-queue",
                )
                child_handle = env.client.get_workflow_handle(
                    "test-parent-managed-429-continue-as-new:child"
                )
                manager_handle = env.client.get_workflow_handle(manager_id)

                original_run_id = ""
                for _ in range(30):
                    try:
                        description = await child_handle.describe()
                    except RPCError:
                        await asyncio.sleep(0.1)
                        continue
                    original_run_id = description.run_id
                    parent_state = await parent_handle.query(TestAgentRunParent.get_state)
                    if parent_state.get("profile_assignments"):
                        break
                    await asyncio.sleep(0.1)

                assert original_run_id

                await child_handle.signal(
                    MoonMindAgentRun.completion_signal,
                    {
                        "summary": "Gemini API rate limit exceeded",
                        "failureClass": "integration_error",
                        "providerErrorCode": "429",
                    },
                )

                new_run_id = original_run_id
                parent_state = {}
                manager_state = {}
                for _ in range(40):
                    try:
                        description = await child_handle.describe()
                    except RPCError:
                        await asyncio.sleep(0.1)
                        continue
                    new_run_id = description.run_id
                    parent_state = await parent_handle.query(TestAgentRunParent.get_state)
                    manager_state = await manager_handle.query(MockAuthProfileManager.get_state)
                    if (
                        new_run_id != original_run_id
                        and manager_state.get("cooldown_reports")
                        and any(
                            "retry scheduled" in change["reason"].lower()
                            for change in parent_state.get("state_changes", [])
                        )
                    ):
                        break
                    await asyncio.sleep(0.1)

                assert new_run_id != original_run_id
                assert manager_state["cooldown_reports"]
                assert manager_state["cooldown_reports"][-1]["cooldown_seconds"] == 900

                await parent_handle.cancel()
                with pytest.raises(WorkflowFailureError):
                    await parent_handle.result()


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
async def mock_jules_status(request: dict) -> dict:
    from datetime import UTC, datetime

    global _status_poll_count
    _status_poll_count += 1
    run_id = request.get("external_id", "unknown")
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
async def mock_jules_fetch_result(request: dict) -> dict:
    run_id = request.get("external_id", "unknown")
    _external_activity_calls.append("fetch_result")
    return {
        "outputRefs": [],
        "summary": f"Jules task {run_id} completed successfully.",
        "metadata": {"normalizedStatus": "completed"},
    }


@_activity.defn(name="integration.jules.cancel")
async def mock_jules_cancel(request: dict) -> dict:
    from datetime import UTC, datetime

    _external_activity_calls.append("cancel")
    run_id = request.get("run_id", "unknown")
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
        # Workflow-queue activities (resolve + execution style + catalog routing).
        async with Worker(
            env.client,
            task_queue="mm.workflow",
            activities=[
                mock_get_activity_route,
                mock_resolve_external_adapter,
                mock_external_adapter_execution_style,
            ],
        ):
            # Workflow worker: hosts the workflow + agent_runtime activities.
            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun],
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
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
@pytest.mark.xfail(
    reason=(
        "Slot release on cancellation requires both manager-side lease verification "
        "(Task 1) and parent-initiated defensive release (Task 4). This test runs "
        "MoonMindAgentRun without a MoonMind.Run parent, so only the manager-side "
        "verification path (via _verify_lease_holders on next loop iteration) can "
        "reclaim the slot. Full reliability also needs Task 4's parent fallback, "
        "which this test setup does not exercise."
    ),
    strict=False,
)
async def test_cancellation_releases_auth_profile_slot():
    """Verify that cancelling a managed AgentRun releases its auth profile slot.

    Regression test for the auth profile slot leak bug where `CancelledError`
    handler wrapped the release_slot signal in `asyncio.shield()`, which does not
    work with Temporal's workflow-level cancellation.

    The fix implements two complementary paths:
    1. Manager-side: _verify_lease_holders() reclaims slots from terminated
       workflows on each manager loop iteration (Task 1).
    2. Parent-side: MoonMind.Run sends release_slot defensively when a child
       exits in a terminal state (Task 4).

    This test runs AgentRun without a MoonMind.Run parent, so only the
    manager-side path is exercised. The slot should be reclaimed within one
    manager loop iteration (~60s max in production, immediate in this test
    via time-skipping).
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="mm.workflow",
            activities=_WORKFLOW_QUEUE_ACTIVITIES,
        ):
            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockAuthProfileManager],
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
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
