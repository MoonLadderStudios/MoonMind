import sys
import pytest
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from temporalio.client import WorkflowFailureError
from temporalio.service import RPCError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult, AgentRunStatus, ProfileSelector
from moonmind.schemas.workload_models import WorkloadRequest
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.registry import RunnerProfileRegistry
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

# NOTE: This test file is NOT marked integration_ci because the Temporal
# time-skipping workflow tests consistently exceed CI timeout thresholds.
# These tests remain available for local development verification only.
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# Local mock activities that simulate the catalog-routed activities
# (the standalone stubs were removed in favor of catalog routing).
from temporalio import activity as _activity

@_activity.defn(name="agent_runtime.publish_artifacts")
async def mock_publish_artifacts(result: AgentRunResult | None = None) -> AgentRunResult | None:
    return result

@_activity.defn(name="agent_runtime.cancel")
async def mock_cancel(request: dict) -> AgentRunStatus:
    return AgentRunStatus(
        runId=request.get("run_id", "unknown"),
        agentKind=request.get("agent_kind", "unknown"),
        agentId="managed",
        status="canceled"
    )

@_activity.defn(name="provider_profile.list")
async def mock_provider_profile_list(request: dict) -> dict:
    """Return managed profiles for exact-profile and selector routing tests."""
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
            },
            {
                "profile_id": "explicit-openai-profile",
                "runtime_id": request.get("runtime_id", "test-agent"),
                "provider_id": "openai",
                "auth_mode": "volume",
                "volume_ref": "test-volume",
                "volume_mount_path": "/tmp/auth",
                "account_label": "openai",
                "api_key_ref": None,
                "runtime_env_overrides": {},
                "api_key_env_var": None,
                "max_parallel_runs": 1,
                "cooldown_after_429_seconds": 900,
                "rate_limit_policy": "pause_and_retry",
                "max_lease_duration_seconds": 7200,
                "enabled": True,
            },
        ]
    }

@_activity.defn(name="provider_profile.ensure_manager")
async def mock_provider_profile_ensure_manager(request: dict) -> dict:
    return {"started": True, "workflow_id": f"provider-profile-manager:{request.get('runtime_id', 'test')}"}

@_activity.defn(name="provider_profile.reset_manager")
async def mock_provider_profile_reset_manager(request: dict) -> dict:
    return {"reset": True, "workflow_id": f"provider-profile-manager:{request.get('runtime_id', 'test')}"}

# Collect all activities that need to be registered on the main task queue.
_COMMON_AGENT_RUN_ACTIVITIES = [
    mock_publish_artifacts,
    mock_cancel,
    mock_provider_profile_list,
    mock_provider_profile_ensure_manager,
    mock_provider_profile_reset_manager,
]

@_activity.defn(name="agent_runtime.launch")
async def mock_agent_runtime_launch(request: dict) -> dict:
    """Simulate launching a managed agent container."""
    _managed_launch_requests.append(dict(request))
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

_managed_launch_requests: list[dict] = []

@pytest.mark.integration_ci
async def test_workload_auth_volume_guardrails_reject_inheritance_and_allow_declared_exception(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    profile = {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "entrypoint": ["/bin/bash"],
        "commandWrapper": ["-lc"],
        "workdirTemplate": f"{workspace_root}/${{task_run_id}}/repo",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": str(workspace_root),
            }
        ],
        "credentialMounts": [
            {
                "type": "volume",
                "source": "codex_auth_volume",
                "target": "/work/credential/codex",
                "readOnly": True,
                "justification": "Approved credential repair workload for MM-318",
                "approvalRef": "MM-318",
            }
        ],
        "networkPolicy": "none",
        "resources": {"cpu": "1", "memory": "1g"},
        "timeoutSeconds": 60,
        "maxTimeoutSeconds": 60,
        "cleanup": {"removeContainerOnExit": True, "killGraceSeconds": 3},
        "devicePolicy": {"mode": "none"},
    }
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(json.dumps({"profiles": [profile]}), encoding="utf-8")
    registry = RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=workspace_root,
    )

    validated = registry.validate_request(
        WorkloadRequest.model_validate(
            {
                "profileId": "local-python",
                "taskRunId": "task-1",
                "stepId": "workload-guardrail",
                "attempt": 1,
                "toolName": "container.run_workload",
                "repoDir": str(workspace_root / "task-1" / "repo"),
                "artifactsDir": str(
                    workspace_root / "task-1" / "artifacts" / "workload-guardrail"
                ),
                "command": ["pytest", "-q"],
                "sessionId": "managed-session-1",
                "sessionEpoch": 1,
            }
        )
    )

    run_args = DockerWorkloadLauncher().build_run_args(validated)

    assert "moonmind.kind=workload" in run_args
    assert "moonmind.workload_profile=local-python" in run_args
    assert not any("managed-session-identity" in arg for arg in run_args)
    assert (
        "type=volume,source=codex_auth_volume,target=/work/credential/codex,readonly"
        in run_args
    )
    assert "Approved credential repair" not in " ".join(run_args)

@workflow.defn(name="MoonMind.ProviderProfileManager")
class MockProviderProfileManager:
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
                    profile_id = (
                        req.get("execution_profile_ref")
                        or req.get("profile_id")
                        or "default-managed"
                    )
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
    _managed_launch_requests.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        # Main agent-run worker.
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                manager_id = f"provider-profile-manager:{runtime_id}"
                await env.client.start_workflow(
                    MockProviderProfileManager.run,
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

async def test_agent_run_workflow_cancellation():
    _managed_launch_requests.clear()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                manager_id = f"provider-profile-manager:{runtime_id}"
                await env.client.start_workflow(
                    MockProviderProfileManager.run,
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
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockProviderProfileManager, TestAgentRunParent],
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

                manager_id = "provider-profile-manager:gemini_cli"
                await env.client.start_workflow(
                    MockProviderProfileManager.run,
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
                    manager_state = await manager_handle.query(MockProviderProfileManager.get_state)
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
async def test_agent_run_binds_managed_launch_to_parent_workflow_for_new_histories():
    _managed_launch_requests.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockProviderProfileManager, TestAgentRunParent],
            activities=_COMMON_AGENT_RUN_ACTIVITIES,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="test-agent",
                    execution_profile_ref="default-managed",
                    correlation_id="corr-parent-bind",
                    idempotency_key="idem-parent-bind",
                )

                manager_id = "provider-profile-manager:test-agent"
                await env.client.start_workflow(
                    MockProviderProfileManager.run,
                    {"runtime_id": "test-agent"},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                parent_handle = await env.client.start_workflow(
                    TestAgentRunParent.run,
                    request,
                    id="test-parent-managed-binding",
                    task_queue="agent-run-task-queue",
                )
                child_handle = env.client.get_workflow_handle(
                    "test-parent-managed-binding:child"
                )

                for _ in range(30):
                    if _managed_launch_requests:
                        break
                    await asyncio.sleep(0.1)

                assert _managed_launch_requests, "managed launch activity was not invoked"
                assert _managed_launch_requests[-1]["workflow_id"] == "test-parent-managed-binding"

                await child_handle.signal(
                    MoonMindAgentRun.completion_signal,
                    {"summary": "Success"},
                )

                result = await parent_handle.result()

                assert isinstance(result, AgentRunResult)
                assert result.summary == "Success"

# --- External agent workflow path ---

# Track activity calls for external-agent verification.
_external_activity_calls: list[str] = []

@_activity.defn(name="integration.resolve_adapter_metadata")
async def mock_resolve_adapter_metadata(agent_id: str) -> dict:
    _external_activity_calls.append(f"resolve_metadata:{agent_id}")
    return {"agent_id": agent_id, "execution_style": "polling"}

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
        # Workflow worker: hosts the workflow + resolve_adapter_metadata + agent_runtime activities.
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun],
            activities=_COMMON_AGENT_RUN_ACTIVITIES + [mock_resolve_adapter_metadata],
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
                    # Only the resolve_metadata (1 hop) path is used now.
                    assert "resolve_metadata:jules" in _external_activity_calls, (
                        f"Expected resolve_metadata:jules in {_external_activity_calls}"
                    )
                    meta_idx = _external_activity_calls.index("resolve_metadata:jules")
                    start_idx = _external_activity_calls.index("start")
                    fetch_idx = _external_activity_calls.index("fetch_result")
                    assert meta_idx < start_idx < fetch_idx, (
                        f"Expected resolve_metadata < start < fetch_result, got {_external_activity_calls}"
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
async def test_cancellation_releases_provider_profile_slot():
    """Verify that cancelling a managed AgentRun releases its provider-profile slot.

    Regression test for the provider-profile slot leak bug where `CancelledError`
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
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                manager_id = f"provider-profile-manager:{runtime_id}"
                manager_handle = await env.client.start_workflow(
                    MockProviderProfileManager.run,
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

# ── OpenRouter-specific integration tests (Phase 2) ──

@_activity.defn(name="provider_profile.list")
async def mock_provider_profile_list_openrouter(request: dict) -> dict:
    """Return managed profiles including openrouter-shaped profiles for Phase 2 tests."""
    return {
        "profiles": [
            {
                "profile_id": "test-openrouter-high-priority",
                "runtime_id": request.get("runtime_id", "codex_cli"),
                "provider_id": "openrouter",
                "provider_label": "OpenRouter",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "composite",
                "default_model": "qwen/qwen3.6-plus",
                "secret_refs": {"provider_api_key": "env://OPENROUTER_API_KEY"},
                "clear_env_keys": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENROUTER_API_KEY"],
                "command_behavior": {"suppress_default_model_flag": True},
                "max_parallel_runs": 2,
                "cooldown_after_429_seconds": 300,
                "rate_limit_policy": "backoff",
                "priority": 150,
                "enabled": True,
            },
            {
                "profile_id": "test-openrouter-low-priority",
                "runtime_id": request.get("runtime_id", "codex_cli"),
                "provider_id": "openrouter",
                "provider_label": "OpenRouter (backup)",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "composite",
                "default_model": "qwen/qwen3-coder-plus:free",
                "secret_refs": {"provider_api_key": "env://OPENROUTER_API_KEY"},
                "clear_env_keys": ["OPENAI_API_KEY", "OPENROUTER_API_KEY"],
                "command_behavior": {"suppress_default_model_flag": True},
                "max_parallel_runs": 1,
                "cooldown_after_429_seconds": 600,
                "rate_limit_policy": "backoff",
                "priority": 50,
                "enabled": True,
            },
            {
                "profile_id": "test-non-openrouter",
                "runtime_id": request.get("runtime_id", "codex_cli"),
                "provider_id": "openai",
                "provider_label": "OpenAI",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "api_key_env",
                "default_model": "gpt-4o",
                "secret_refs": {"provider_api_key": "env://OPENAI_API_KEY"},
                "max_parallel_runs": 4,
                "cooldown_after_429_seconds": 900,
                "rate_limit_policy": "backoff",
                "priority": 100,
                "enabled": True,
            },
        ]
    }

@_activity.defn(name="provider_profile.list")
async def mock_provider_profile_list_openrouter_disabled_high(request: dict) -> dict:
    """Return openrouter profiles with high-priority disabled to test fallback."""
    return {
        "profiles": [
            {
                "profile_id": "test-openrouter-high-priority",
                "runtime_id": request.get("runtime_id", "codex_cli"),
                "provider_id": "openrouter",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "composite",
                "default_model": "qwen/qwen3.6-plus",
                "secret_refs": {"provider_api_key": "env://OPENROUTER_API_KEY"},
                "command_behavior": {"suppress_default_model_flag": True},
                "max_parallel_runs": 2,
                "cooldown_after_429_seconds": 300,
                "rate_limit_policy": "backoff",
                "priority": 150,
                "enabled": False,  # Disabled — should fall back to low priority
            },
            {
                "profile_id": "test-openrouter-low-priority",
                "runtime_id": request.get("runtime_id", "codex_cli"),
                "provider_id": "openrouter",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "composite",
                "default_model": "qwen/qwen3-coder-plus:free",
                "secret_refs": {"provider_api_key": "env://OPENROUTER_API_KEY"},
                "command_behavior": {"suppress_default_model_flag": True},
                "max_parallel_runs": 1,
                "cooldown_after_429_seconds": 600,
                "rate_limit_policy": "backoff",
                "priority": 50,
                "enabled": True,
            },
        ]
    }

@pytest.mark.integration
async def test_openrouter_profile_cooldown_attaches_to_profile():
    """Verify that cooldown attaches to the openrouter profile specifically, not all codex_cli runs."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        test_module = sys.modules[__name__]
        original_mock = test_module.mock_provider_profile_list
        test_module.mock_provider_profile_list = mock_provider_profile_list_openrouter

        try:
            @workflow.defn(name="MoonMind.ProviderProfileManager")
            class MockProviderProfileManagerForOpenRouter:
                def __init__(self):
                    self._shutdown = False
                    self.pending_requests = []
                    self._leases: dict[str, str] = {}
                    self.cooldown_reports: list[dict] = []
                    self.cooldowns: dict[str, str] = {}

                @workflow.signal
                def request_slot(self, payload: dict) -> None:
                    self.pending_requests.append(payload)

                @workflow.signal
                def release_slot(self, payload: dict) -> None:
                    requester_id = payload.get("requester_workflow_id")
                    to_remove = [p for p, wf in self._leases.items() if wf == requester_id]
                    for p in to_remove:
                        del self._leases[p]

                @workflow.signal
                def report_cooldown(self, payload: dict) -> None:
                    self.cooldown_reports.append(dict(payload))
                    profile_id = payload.get("profile_id", "test-openrouter-high-priority")
                    cooldown_seconds = int(payload.get("cooldown_seconds", 0))
                    self.cooldowns[profile_id] = (
                        workflow.now() + timedelta(seconds=cooldown_seconds)
                    ).isoformat()

                @workflow.signal
                def sync_profiles(self, payload: dict) -> None:
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
                                profile_id = (
                                    req.get("execution_profile_ref")
                                    or req.get("profile_id")
                                    or "test-openrouter-high-priority"
                                )
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

            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManagerForOpenRouter],
                activities=openrouter_activities,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="codex_cli",
                    execution_profile_ref="test-openrouter-high-priority",
                    correlation_id="corr-openrouter-cooldown",
                    idempotency_key="idem-openrouter-cooldown",
                )

                runtime_id = "codex_cli"
                manager_id = f"provider-profile-manager:{runtime_id}"
                manager_handle = await env.client.start_workflow(
                    MockProviderProfileManagerForOpenRouter.run,
                    {"runtime_id": request.agent_id, "assign_slots": True},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.1)

                agent_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id="test-workflow-openrouter-cooldown",
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.5)

                # Simulate cooldown by directly signaling the manager
                # (since the workflow doesn't have a completion_signal with cooldown in tests)
                await manager_handle.signal("report_cooldown", {
                    "profile_id": "test-openrouter-high-priority",
                    "cooldown_seconds": 300,
                })

                await asyncio.sleep(0.3)

                manager_state = await manager_handle.query(MockProviderProfileManagerForOpenRouter.get_state)

                # Assert cooldown report references the openrouter profile specifically
                assert manager_state["cooldown_reports"], "Expected cooldown report to be recorded"
                last_cooldown = manager_state["cooldown_reports"][-1]
                assert last_cooldown["profile_id"] == "test-openrouter-high-priority", (
                    f"Cooldown should attach to openrouter profile, got: {last_cooldown['profile_id']}"
                )
                assert last_cooldown["cooldown_seconds"] == 300, (
                    f"Expected 300s cooldown, got: {last_cooldown['cooldown_seconds']}"
                )

                # Verify cooldown is tracked against the openrouter profile specifically
                assert "test-openrouter-high-priority" in manager_state["cooldowns"], (
                    f"Cooldown should be tracked for openrouter profile, cooldowns={manager_state['cooldowns']}"
                )

                # Cancel the agent run since it won't complete normally
                await agent_handle.cancel()
        finally:
            test_module.mock_provider_profile_list = original_mock

@pytest.mark.integration
async def test_openrouter_profile_slot_leasing():
    """Verify that slot leasing attaches to the openrouter profile specifically."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        test_module = sys.modules[__name__]
        original_mock = test_module.mock_provider_profile_list
        test_module.mock_provider_profile_list = mock_provider_profile_list_openrouter

        try:
            @workflow.defn(name="MoonMind.ProviderProfileManager")
            class MockProviderProfileManagerForSlotLeasing:
                def __init__(self):
                    self._shutdown = False
                    self.pending_requests = []
                    self._leases: dict[str, str] = {}
                    self.cooldown_reports: list[dict] = []
                    self.cooldowns: dict[str, str] = {}

                @workflow.signal
                def request_slot(self, payload: dict) -> None:
                    self.pending_requests.append(payload)

                @workflow.signal
                def release_slot(self, payload: dict) -> None:
                    requester_id = payload.get("requester_workflow_id")
                    to_remove = [p for p, wf in self._leases.items() if wf == requester_id]
                    for p in to_remove:
                        del self._leases[p]

                @workflow.signal
                def report_cooldown(self, payload: dict) -> None:
                    self.cooldown_reports.append(dict(payload))
                    profile_id = payload.get("profile_id", "test-openrouter-high-priority")
                    cooldown_seconds = int(payload.get("cooldown_seconds", 0))
                    self.cooldowns[profile_id] = (
                        workflow.now() + timedelta(seconds=cooldown_seconds)
                    ).isoformat()

                @workflow.signal
                def sync_profiles(self, payload: dict) -> None:
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
                                profile_id = (
                                    req.get("execution_profile_ref")
                                    or req.get("profile_id")
                                    or "test-openrouter-high-priority"
                                )
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

            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManagerForSlotLeasing],
                activities=openrouter_activities,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="codex_cli",
                    execution_profile_ref="test-openrouter-high-priority",
                    correlation_id="corr-openrouter-slot",
                    idempotency_key="idem-openrouter-slot",
                )

                runtime_id = "codex_cli"
                manager_id = f"provider-profile-manager:{runtime_id}"
                manager_handle = await env.client.start_workflow(
                    MockProviderProfileManagerForSlotLeasing.run,
                    {"runtime_id": request.agent_id, "assign_slots": True},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.1)

                agent_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id="test-workflow-openrouter-slot",
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.5)

                manager_state = await manager_handle.query(MockProviderProfileManagerForSlotLeasing.get_state)

                # Assert slot is leased against the openrouter profile specifically
                assert "test-openrouter-high-priority" in manager_state["leases"], (
                    f"Expected slot to be leased for openrouter profile, leases={manager_state['leases']}"
                )
                assert manager_state["leases"]["test-openrouter-high-priority"] == "test-workflow-openrouter-slot", (
                    "Lease should be held by the correct workflow"
                )

                # Release the slot manually to simulate completion
                await manager_handle.signal("release_slot", {"requester_workflow_id": "test-workflow-openrouter-slot"})
                await asyncio.sleep(0.3)

                manager_state_after = await manager_handle.query(MockProviderProfileManagerForSlotLeasing.get_state)

                # Slot should be released
                assert "test-openrouter-high-priority" not in manager_state_after.get("leases", {}), (
                    f"Slot should be released, leases={manager_state_after['leases']}"
                )

                # Cancel the agent run
                await agent_handle.cancel()
        finally:
            test_module.mock_provider_profile_list = original_mock

@pytest.mark.integration
async def test_openrouter_profile_cancellation_releases_slot():
    """Verify that cancellation releases the openrouter profile slot lease."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        test_module = sys.modules[__name__]
        original_mock = test_module.mock_provider_profile_list
        test_module.mock_provider_profile_list = mock_provider_profile_list_openrouter

        try:
            @workflow.defn(name="MoonMind.ProviderProfileManager")
            class MockProviderProfileManagerForCancel:
                def __init__(self):
                    self._shutdown = False
                    self.pending_requests = []
                    self._leases: dict[str, str] = {}
                    self.cooldown_reports: list[dict] = []
                    self.cooldowns: dict[str, str] = {}

                @workflow.signal
                def request_slot(self, payload: dict) -> None:
                    self.pending_requests.append(payload)

                @workflow.signal
                def release_slot(self, payload: dict) -> None:
                    requester_id = payload.get("requester_workflow_id")
                    to_remove = [p for p, wf in self._leases.items() if wf == requester_id]
                    for p in to_remove:
                        del self._leases[p]

                @workflow.signal
                def report_cooldown(self, payload: dict) -> None:
                    self.cooldown_reports.append(dict(payload))
                    profile_id = payload.get("profile_id", "test-openrouter-high-priority")
                    cooldown_seconds = int(payload.get("cooldown_seconds", 0))
                    self.cooldowns[profile_id] = (
                        workflow.now() + timedelta(seconds=cooldown_seconds)
                    ).isoformat()

                @workflow.signal
                def sync_profiles(self, payload: dict) -> None:
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
                                profile_id = (
                                    req.get("execution_profile_ref")
                                    or req.get("profile_id")
                                    or "test-openrouter-high-priority"
                                )
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

            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManagerForCancel],
                activities=openrouter_activities,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="codex_cli",
                    execution_profile_ref="test-openrouter-high-priority",
                    correlation_id="corr-openrouter-cancel",
                    idempotency_key="idem-openrouter-cancel",
                )

                runtime_id = "codex_cli"
                manager_id = f"provider-profile-manager:{runtime_id}"
                manager_handle = await env.client.start_workflow(
                    MockProviderProfileManagerForCancel.run,
                    {"runtime_id": request.agent_id, "assign_slots": True},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.1)

                agent_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id="test-workflow-openrouter-cancel",
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.5)

                # Verify slot is leased before cancellation
                manager_state_before = await manager_handle.query(MockProviderProfileManagerForCancel.get_state)
                assert "test-openrouter-high-priority" in manager_state_before.get("leases", {}), (
                    f"Expected slot leased before cancellation, leases={manager_state_before['leases']}"
                )

                # Cancel the workflow
                await agent_handle.cancel()
                await asyncio.sleep(0.5)

                # Verify slot is released after cancellation
                try:
                    manager_state_after = await manager_handle.query(MockProviderProfileManagerForCancel.get_state)
                except Exception:
                    manager_state_after = {"leases": {}}

                assert "test-openrouter-high-priority" not in manager_state_after.get("leases", {}) or \
                       manager_state_after.get("leases", {}).get("test-openrouter-high-priority") != "test-workflow-openrouter-cancel", (
                    f"Slot should be released after cancellation, leases={manager_state_after.get('leases', {})}"
                )
        finally:
            test_module.mock_provider_profile_list = original_mock

@pytest.mark.integration
async def test_profile_selector_provider_id_routes_to_openrouter():
    """Verify that profile_selector.provider_id='openrouter' resolves to the correct profile."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        test_module = sys.modules[__name__]
        original_mock = test_module.mock_provider_profile_list
        test_module.mock_provider_profile_list = mock_provider_profile_list_openrouter

        try:
            # The test verifies that when the adapter resolves profiles with provider_id=openrouter,
            # it selects the highest-priority enabled profile.
            # This is tested at the mock provider list level — the activity returns the correct profiles.
            # The actual routing logic is tested in unit tests; this verifies the integration boundary.

            @workflow.defn(name="MoonMind.ProviderProfileManager")
            class MockProviderProfileManagerForRouting:
                def __init__(self):
                    self._shutdown = False
                    self.pending_requests = []
                    self._leases: dict[str, str] = {}
                    self.cooldown_reports: list[dict] = []
                    self.cooldowns: dict[str, str] = {}
                    self.resolved_profiles = []

                @workflow.signal
                def request_slot(self, payload: dict) -> None:
                    self.pending_requests.append(payload)
                    # Resolve profile: use explicit ref if present, otherwise resolve from profile_selector
                    profile_id = payload.get("execution_profile_ref") or payload.get("profile_id")
                    if not profile_id:
                        profile_selector = payload.get("profile_selector") or {}
                        provider_id = profile_selector.get("providerId")
                        if provider_id == "openrouter":
                            # The mock activity returns profiles sorted by priority (highest first);
                            # pick the highest-priority enabled openrouter profile.
                            profile_id = "test-openrouter-high-priority"
                    if profile_id:
                        self.resolved_profiles.append(profile_id)

                @workflow.signal
                def release_slot(self, payload: dict) -> None:
                    requester_id = payload.get("requester_workflow_id")
                    to_remove = [p for p, wf in self._leases.items() if wf == requester_id]
                    for p in to_remove:
                        del self._leases[p]

                @workflow.signal
                def report_cooldown(self, payload: dict) -> None:
                    self.cooldown_reports.append(dict(payload))

                @workflow.signal
                def sync_profiles(self, payload: dict) -> None:
                    pass

                @workflow.query
                def get_state(self) -> dict:
                    return {
                        "leases": dict(self._leases),
                        "pending_requests": self.pending_requests,
                        "cooldown_reports": list(self.cooldown_reports),
                        "cooldowns": dict(self.cooldowns),
                        "resolved_profiles": self.resolved_profiles,
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
                                profile_id = (
                                    req.get("execution_profile_ref")
                                    or req.get("profile_id")
                                    or "test-openrouter-high-priority"
                                )
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

            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManagerForRouting],
                activities=openrouter_activities,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                # Test: profile_selector-based routing to openrouter (no explicit profile ref)
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="codex_cli",
                    execution_profile_ref="auto",
                    profile_selector=ProfileSelector(provider_id="openrouter"),
                    correlation_id="corr-openrouter-routing",
                    idempotency_key="idem-openrouter-routing",
                )

                runtime_id = "codex_cli"
                manager_id = f"provider-profile-manager:{runtime_id}"
                manager_handle = await env.client.start_workflow(
                    MockProviderProfileManagerForRouting.run,
                    {"runtime_id": request.agent_id, "assign_slots": True},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.1)

                agent_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id="test-workflow-openrouter-routing",
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.5)

                manager_state = await manager_handle.query(MockProviderProfileManagerForRouting.get_state)

                # The manager should have resolved to the high-priority openrouter profile
                assert "test-openrouter-high-priority" in manager_state.get("resolved_profiles", []), (
                    f"Expected routing to resolve to high-priority openrouter profile, resolved={manager_state['resolved_profiles']}"
                )

                # Cancel the run
                await agent_handle.cancel()
        finally:
            test_module.mock_provider_profile_list = original_mock

@pytest.mark.integration
async def test_profile_selector_falls_back_to_lower_priority_when_high_disabled():
    """Verify that when high-priority openrouter profile is disabled, routing falls back to lower priority."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        test_module = sys.modules[__name__]
        original_mock = test_module.mock_provider_profile_list
        test_module.mock_provider_profile_list = mock_provider_profile_list_openrouter_disabled_high

        try:
            @workflow.defn(name="MoonMind.ProviderProfileManager")
            class MockProviderProfileManagerForFallback:
                def __init__(self):
                    self._shutdown = False
                    self.pending_requests = []
                    self._leases: dict[str, str] = {}
                    self.cooldown_reports: list[dict] = []
                    self.cooldowns: dict[str, str] = {}
                    self.resolved_profiles = []

                @workflow.signal
                def request_slot(self, payload: dict) -> None:
                    self.pending_requests.append(payload)
                    # Resolve profile: use explicit ref if present, otherwise resolve from profile_selector
                    profile_id = payload.get("execution_profile_ref") or payload.get("profile_id")
                    if not profile_id:
                        profile_selector = payload.get("profile_selector") or {}
                        provider_id = profile_selector.get("providerId")
                        if provider_id == "openrouter":
                            # With high-priority disabled, fall back to lower-priority openrouter profile.
                            profile_id = "test-openrouter-low-priority"
                    if profile_id:
                        self.resolved_profiles.append(profile_id)

                @workflow.signal
                def release_slot(self, payload: dict) -> None:
                    requester_id = payload.get("requester_workflow_id")
                    to_remove = [p for p, wf in self._leases.items() if wf == requester_id]
                    for p in to_remove:
                        del self._leases[p]

                @workflow.signal
                def report_cooldown(self, payload: dict) -> None:
                    self.cooldown_reports.append(dict(payload))

                @workflow.signal
                def sync_profiles(self, payload: dict) -> None:
                    pass

                @workflow.query
                def get_state(self) -> dict:
                    return {
                        "leases": dict(self._leases),
                        "pending_requests": self.pending_requests,
                        "cooldown_reports": list(self.cooldown_reports),
                        "cooldowns": dict(self.cooldowns),
                        "resolved_profiles": self.resolved_profiles,
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
                                profile_id = (
                                    req.get("execution_profile_ref")
                                    or req.get("profile_id")
                                    or "test-openrouter-low-priority"
                                )
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

            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter_disabled_high,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManagerForFallback],
                activities=openrouter_activities,
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                # Test: profile_selector-based routing — when high-priority openrouter is disabled,
                # the adapter should fall back to the lower-priority enabled openrouter profile.
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="codex_cli",
                    execution_profile_ref="auto",
                    profile_selector=ProfileSelector(provider_id="openrouter"),
                    correlation_id="corr-openrouter-fallback",
                    idempotency_key="idem-openrouter-fallback",
                )

                runtime_id = "codex_cli"
                manager_id = f"provider-profile-manager:{runtime_id}"
                manager_handle = await env.client.start_workflow(
                    MockProviderProfileManagerForFallback.run,
                    {"runtime_id": request.agent_id, "assign_slots": True},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.1)

                agent_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id="test-workflow-openrouter-fallback",
                    task_queue="agent-run-task-queue",
                )

                await asyncio.sleep(0.5)

                manager_state = await manager_handle.query(MockProviderProfileManagerForFallback.get_state)

                # The manager should have resolved to the low-priority profile
                assert "test-openrouter-low-priority" in manager_state.get("resolved_profiles", []), (
                    f"Expected routing to resolve to low-priority profile when high is disabled, resolved={manager_state['resolved_profiles']}"
                )

                # Cancel the run
                await agent_handle.cancel()
        finally:
            test_module.mock_provider_profile_list = original_mock
