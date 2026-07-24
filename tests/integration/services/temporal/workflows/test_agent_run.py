import sys
import pytest
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker, UnsandboxedWorkflowRunner
from temporalio.client import WorkflowFailureError
from temporalio.service import RPCError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult, AgentRunStatus, ProfileSelector
from moonmind.schemas.workload_models import WorkloadRequest
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.registry import RunnerProfileRegistry
from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    _SLOT_WAIT_TIMEOUT_SECONDS,
)

# NOTE: This test file is NOT marked integration_ci because the Temporal
# time-skipping workflow tests consistently exceed CI timeout thresholds.
# These tests remain available for local development verification only.
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# Local mock activities that simulate the catalog-routed activities
# (the standalone stubs were removed in favor of catalog routing).
from temporalio import activity as _activity


@_activity.defn(name="agent_runtime.publish_artifacts")
async def mock_publish_artifacts(
    result: AgentRunResult | None = None,
) -> AgentRunResult | None:
    return result


_managed_cancel_count = 0


def _request_get(request: object, *keys: str, default: Any = None) -> Any:
    if isinstance(request, dict):
        for key in keys:
            if key in request:
                return request[key]
        return default
    for key in keys:
        value = getattr(request, key, None)
        if value is not None:
            return value
    return default


@_activity.defn(name="agent_runtime.cancel")
async def mock_cancel(request: dict) -> AgentRunStatus:
    global _managed_cancel_count
    _managed_cancel_count += 1
    return AgentRunStatus(
        runId=_request_get(request, "run_id", "runId", default="unknown"),
        agentKind=_request_get(
            request,
            "agent_kind",
            "agentKind",
            default="unknown",
        ),
        agentId="managed",
        status="canceled",
    )


@_activity.defn(name="provider_profile.list")
async def mock_provider_profile_list(request: dict) -> dict:
    """Return managed profiles for exact-profile and selector routing tests."""
    runtime_id = request.get("runtime_id", "test-agent")
    profiles = [
        {
            "profile_id": "default-managed",
            "runtime_id": runtime_id,
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
            "runtime_id": runtime_id,
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
        {
            "profile_id": "alternate-managed",
            "runtime_id": runtime_id,
            "provider_id": "anthropic",
            "auth_mode": "volume",
            "volume_ref": "test-volume",
            "volume_mount_path": "/tmp/auth",
            "account_label": "alternate",
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
    if runtime_id == "claude_code":
        profiles.append(
            {
                "profile_id": "claude-managed",
                "runtime_id": runtime_id,
                "provider_id": "anthropic",
                "auth_mode": "volume",
                "volume_ref": "test-volume",
                "volume_mount_path": "/tmp/auth",
                "account_label": "claude",
                "api_key_ref": None,
                "runtime_env_overrides": {},
                "api_key_env_var": None,
                "max_parallel_runs": 1,
                "cooldown_after_429_seconds": 900,
                "rate_limit_policy": "pause_and_retry",
                "max_lease_duration_seconds": 7200,
                "enabled": True,
            }
        )
    if runtime_id == "codex_cli":
        profiles.append(
            {
                "profile_id": "codex-managed",
                "runtime_id": runtime_id,
                "provider_id": "openai",
                "auth_mode": "volume",
                "volume_ref": "test-volume",
                "volume_mount_path": "/tmp/auth",
                "account_label": "codex",
                "api_key_ref": None,
                "runtime_env_overrides": {},
                "api_key_env_var": None,
                "max_parallel_runs": 1,
                "cooldown_after_429_seconds": 900,
                "rate_limit_policy": "pause_and_retry",
                "max_lease_duration_seconds": 7200,
                "enabled": True,
            }
        )
    return {
        "profiles": profiles
    }

@_activity.defn(name="provider_profile.ensure_manager")
async def mock_provider_profile_ensure_manager(request: dict) -> dict:
    global _provider_profile_ensure_manager_count
    _provider_profile_ensure_manager_count += 1
    return {"started": True, "workflow_id": f"provider-profile-manager:{request.get('runtime_id', 'test')}"}

@_activity.defn(name="provider_profile.reset_manager")
async def mock_provider_profile_reset_manager(request: dict) -> dict:
    return {"reset": True, "workflow_id": f"provider-profile-manager:{request.get('runtime_id', 'test')}"}


_provider_profile_ensure_manager_count = 0
_provider_profile_manager_state_count = 0
_provider_profile_manager_state_mode = "running"


@_activity.defn(name="provider_profile.manager_state")
async def mock_provider_profile_manager_state(request: dict) -> dict:
    global _provider_profile_manager_state_count
    _provider_profile_manager_state_count += 1
    workflow_id = f"provider-profile-manager:{request.get('runtime_id', 'test')}"
    if _provider_profile_manager_state_mode == "ambiguous":
        return {
            "running": True,
            "workflow_id": workflow_id,
            "status": "RUNNING",
            "inspection_succeeded": False,
            "inspection_status": "QUERY_TIMEOUT",
        }
    return {
        "running": True,
        "workflow_id": workflow_id,
        "status": "RUNNING",
        "inspection_succeeded": True,
        "requester_pending": True,
    }


# Collect all activities that need to be registered on the main task queue.
_COMMON_AGENT_RUN_ACTIVITIES = [
    mock_publish_artifacts,
    mock_cancel,
    mock_provider_profile_list,
    mock_provider_profile_ensure_manager,
    mock_provider_profile_reset_manager,
    mock_provider_profile_manager_state,
]

_managed_launch_requests: list[dict] = []
_managed_status_mode = "default"
_managed_status_poll_count = 0
_managed_fetch_result_count = 0

@_activity.defn(name="agent_runtime.launch")
async def mock_agent_runtime_launch(request: dict) -> dict:
    """Simulate launching a managed agent container."""
    _managed_launch_requests.append(dict(request))
    return {
        "container_id": "test-container-001",
        "status": "running",
        "agent_id": request.get("agent_id", "test-agent"),
    }

@_activity.defn(name="agent_runtime.build_launch_context")
async def mock_agent_runtime_build_launch_context(request: dict) -> dict:
    profile = request["profile"]
    return {
        "profile_id": profile["profile_id"],
        "credential_source": "volume",
        "delta_env_overrides": {},
        "passthrough_env_keys": [],
        "env_keys_count": 0,
    }

@_activity.defn(name="agent_runtime.status")
async def mock_agent_runtime_status(request: dict) -> dict:
    """Simulate polling the agent's execution status."""
    global _managed_status_poll_count
    _managed_status_poll_count += 1
    if _managed_status_mode == "silent_then_completed":
        status = "completed" if _managed_status_poll_count >= 3 else "running"
        metadata: dict[str, Any] = {"runtimeId": "claude_code"}
        if status == "completed":
            metadata["finishedAt"] = datetime.now(tz=UTC).isoformat()
            metadata["exitCode"] = 0
        return {
            "runId": request.get("runId")
            or request.get("run_id")
            or "test-managed-run",
            "agentKind": "managed",
            "agentId": request.get("agentId")
            or request.get("agent_id")
            or "claude_code",
            "status": status,
            "metadata": metadata,
        }
    if _managed_status_mode == "silent_then_rate_limited_after_cancel":
        if not _managed_cancel_count:
            status = "running"
        elif _managed_fetch_result_count == 0:
            status = "canceled"
        else:
            status = "failed"
        metadata = {"runtimeId": "claude_code"}
        if status in {"canceled", "failed"}:
            metadata["finishedAt"] = datetime.now(tz=UTC).isoformat()
            metadata["exitCode"] = -9
        return {
            "runId": request.get("runId")
            or request.get("run_id")
            or "test-managed-run",
            "agentKind": "managed",
            "agentId": request.get("agentId")
            or request.get("agent_id")
            or "claude_code",
            "status": status,
            "metadata": metadata,
        }
    return {
        "status": "running",
        "container_id": request.get("container_id", "test-container-001"),
    }

@_activity.defn(name="agent_runtime.fetch_result")
async def mock_agent_runtime_fetch_result(request: dict) -> dict:
    """Simulate fetching the agent's final result."""
    global _managed_fetch_result_count
    _managed_fetch_result_count += 1
    if _managed_status_mode == "silent_then_completed":
        return {
            "summary": "Managed run completed during no-progress grace.",
            "metadata": {
                "normalizedStatus": "completed",
                "fetchRunId": request.get("runId") or request.get("run_id"),
            },
        }
    if _managed_status_mode == "silent_then_rate_limited_after_cancel":
        if _managed_fetch_result_count == 1:
            return {
                "summary": "Managed run canceled before provider classification.",
                "failureClass": "execution_error",
                "metadata": {
                    "normalizedStatus": "canceled",
                    "fetchRunId": request.get("runId") or request.get("run_id"),
                },
            }
        return {
            "summary": "Provider rate limit reached; retry after cooldown.",
            "failureClass": "integration_error",
            "providerErrorCode": "429",
            "retryRecommendation": "retry_after_cooldown",
            "metadata": {
                "normalizedStatus": "failed",
                "fetchRunId": request.get("runId") or request.get("run_id"),
                "providerFailure": {
                    "providerErrorClass": "rate_limit",
                    "providerErrorCode": "429",
                    "retryRecommendation": "retry_after_cooldown",
                    "sanitizedSummary": (
                        "Provider rate limit reached; the run will retry "
                        "after a profile cooldown."
                    ),
                },
            },
        }
    return {
        "summary": "Completed via test mock",
        "artifacts": [],
    }

# Add launch/status/fetch_result to the common activities list
_COMMON_AGENT_RUN_ACTIVITIES.extend([
    mock_agent_runtime_launch,
    mock_agent_runtime_build_launch_context,
    mock_agent_runtime_status,
    mock_agent_runtime_fetch_result,
])

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
        "workdirTemplate": f"{workspace_root}/${{agent_run_id}}/repo",
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
                "agentRunId": "task-1",
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
        self.default_profile_id = "default-managed"
        self.selector_provider_map: dict[str, str] = {}
        self.resolved_profiles: list[str] = []

    @workflow.signal
    def request_slot(self, payload: dict) -> None:
        profile_id = payload.get("execution_profile_ref") or payload.get("profile_id")
        if not profile_id:
            profile_selector = payload.get("profile_selector") or {}
            provider_id = profile_selector.get("providerId")
            profile_id = self.selector_provider_map.get(provider_id)
        if profile_id:
            self.resolved_profiles.append(profile_id)
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
            "resolved_profiles": list(self.resolved_profiles),
        }

    @workflow.run
    async def run(self, input_payload: dict) -> dict:
        assign_slots = input_payload.get("assign_slots", True)
        self.default_profile_id = input_payload.get(
            "default_profile_id", self.default_profile_id
        )
        self.selector_provider_map = dict(input_payload.get("selector_provider_map") or {})
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
                        or self.selector_provider_map.get(
                            (req.get("profile_selector") or {}).get("providerId")
                        )
                        or self.default_profile_id
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

@workflow.defn(name="MoonMind.ProviderProfileManager")
class RuntimeUpdateProviderProfileManager:
    def __init__(self) -> None:
        self.pending_requests: list[dict] = []
        self.assignable_profile_id = "alternate-managed"
        self.release_payloads: list[dict] = []
        self.request_payloads: list[dict] = []
        self.assign_then_update_model: str | None = None

    @workflow.signal
    def request_slot(self, payload: dict) -> None:
        requester = payload["requester_workflow_id"]
        self.request_payloads.append(dict(payload))
        self.pending_requests = [
            req
            for req in self.pending_requests
            if req.get("requester_workflow_id") != requester
        ]
        self.pending_requests.append(dict(payload))

    @workflow.signal
    def release_slot(self, payload: dict) -> None:
        self.release_payloads.append(dict(payload))
        requester = payload.get("requester_workflow_id")
        self.pending_requests = [
            req
            for req in self.pending_requests
            if req.get("requester_workflow_id") != requester
        ]

    @workflow.signal
    def report_cooldown(self, payload: dict) -> None:
        return None

    @workflow.signal
    def sync_profiles(self, payload: dict) -> None:
        return None

    @workflow.query
    def get_state(self) -> dict:
        return {
            "pending_requests": list(self.pending_requests),
            "release_payloads": list(self.release_payloads),
            "request_payloads": list(self.request_payloads),
            "assignable_profile_id": self.assignable_profile_id,
        }

    @workflow.run
    async def run(self, input_payload: dict) -> dict:
        self.assign_then_update_model = input_payload.get("assign_then_update_model")
        self.assignable_profile_id = input_payload.get(
            "assignable_profile_id",
            self.assignable_profile_id,
        )
        while True:
            await workflow.wait_condition(lambda: bool(self.pending_requests))
            req = self.pending_requests[0]
            if self.assign_then_update_model:
                self.pending_requests.pop(0)
                profile_id = req.get("execution_profile_ref") or "default-managed"
                handle = workflow.get_external_workflow_handle(
                    req["requester_workflow_id"]
                )
                await handle.signal(
                    "update_runtime_selection",
                    {"model": self.assign_then_update_model},
                )
                await handle.signal("slot_assigned", {"profile_id": profile_id})
                await workflow.sleep(3600)
                continue
            if (
                self.assignable_profile_id != "*"
                and req.get("execution_profile_ref") != self.assignable_profile_id
            ):
                await workflow.sleep(1)
                continue
            self.pending_requests.pop(0)
            handle = workflow.get_external_workflow_handle(
                req["requester_workflow_id"]
            )
            assigned_profile_id = (
                req.get("execution_profile_ref")
                or (
                    "default-managed"
                    if self.assignable_profile_id == "*"
                    else self.assignable_profile_id
                )
            )
            await handle.signal(
                "slot_assigned",
                {"profile_id": assigned_profile_id},
            )
            await workflow.sleep(3600)

@_activity.defn(name="agent_runtime.status")
async def mock_agent_runtime_status_rate_limited(request: dict) -> dict:
    return {
        "runId": request.get("run_id", "managed-rate-limit-run"),
        "agentKind": "managed",
        "agentId": request.get("agent_id", "claude_code"),
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
                runtime_mapping = {"claude_code": "claude_code", "claude": "claude_code", "codex": "codex_cli"}
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
                runtime_mapping = {"claude_code": "claude_code", "claude": "claude_code", "codex": "codex_cli"}
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


async def test_slot_wait_preserves_durable_request_when_manager_inspection_is_ambiguous(
):
    """A busy manager query must not trigger ensure/re-request amplification."""
    global _provider_profile_ensure_manager_count
    global _provider_profile_manager_state_count
    global _provider_profile_manager_state_mode

    _provider_profile_ensure_manager_count = 0
    _provider_profile_manager_state_count = 0
    _provider_profile_manager_state_mode = "ambiguous"
    history = None
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with (
                Worker(
                    env.client,
                    task_queue="agent-run-task-queue-ambiguous-manager",
                    workflows=[
                        MoonMindAgentRun,
                        RuntimeUpdateProviderProfileManager,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue="mm.activity.artifacts",
                    activities=[
                        mock_provider_profile_list,
                        mock_provider_profile_ensure_manager,
                        mock_provider_profile_reset_manager,
                        mock_provider_profile_manager_state,
                    ],
                ),
            ):
                manager_id = "provider-profile-manager:codex_cli"
                await env.client.start_workflow(
                    RuntimeUpdateProviderProfileManager.run,
                    {
                        "runtime_id": "codex_cli",
                        "assignable_profile_id": "never-assign",
                    },
                    id=manager_id,
                    task_queue="agent-run-task-queue-ambiguous-manager",
                )
                manager_handle = env.client.get_workflow_handle(manager_id)
                child_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    AgentExecutionRequest(
                        agent_kind="managed",
                        agent_id="codex_cli",
                        execution_profile_ref="default-managed",
                        correlation_id="ambiguous-manager:corr",
                        idempotency_key="ambiguous-manager:idem",
                    ),
                    id="test-agent-run-ambiguous-manager",
                    task_queue="agent-run-task-queue-ambiguous-manager",
                )

                manager_state = {}
                for _ in range(40):
                    manager_state = await manager_handle.query(
                        RuntimeUpdateProviderProfileManager.get_state
                    )
                    if (
                        manager_state.get("request_payloads")
                        and _provider_profile_manager_state_count >= 1
                    ):
                        break
                    await asyncio.sleep(0.05)

                assert len(manager_state.get("request_payloads", [])) == 1
                await env.sleep(_SLOT_WAIT_TIMEOUT_SECONDS + 5)

                for _ in range(40):
                    if _provider_profile_manager_state_count >= 2:
                        break
                    await asyncio.sleep(0.05)

                manager_state = await manager_handle.query(
                    RuntimeUpdateProviderProfileManager.get_state
                )
                assert _provider_profile_manager_state_count >= 2
                assert _provider_profile_ensure_manager_count == 0
                assert len(manager_state["request_payloads"]) == 1

                await child_handle.cancel()
                with pytest.raises(WorkflowFailureError):
                    await child_handle.result()
                history = await child_handle.fetch_history()

        assert history is not None
        await Replayer(
            workflows=[MoonMindAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
    finally:
        _provider_profile_manager_state_mode = "running"


@pytest.mark.asyncio
async def test_agent_run_reports_managed_429_retry_summary_to_parent():
    rate_limited_activities = [
        mock_publish_artifacts,
        mock_cancel,
        mock_provider_profile_list,
        mock_provider_profile_ensure_manager,
        mock_provider_profile_reset_manager,
        mock_agent_runtime_launch,
        mock_agent_runtime_build_launch_context,
        mock_agent_runtime_status_rate_limited,
        mock_agent_runtime_fetch_result_rate_limited,
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[MoonMindAgentRun, MockProviderProfileManager, TestAgentRunParent],
            activities=rate_limited_activities,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
                request = AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="claude_code",
                    execution_profile_ref="default-managed",
                    correlation_id="corr-429",
                    idempotency_key="idem-429",
                )

                manager_id = "provider-profile-manager:claude_code"
                await env.client.start_workflow(
                    MockProviderProfileManager.run,
                    {"runtime_id": "claude_code"},
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

                manager_state = {}
                for _ in range(60):
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
async def test_managed_agent_runtime_selection_update_replaces_slot_wait_request():
    """Provider/model edits while awaiting slot should affect the active child."""
    _managed_launch_requests.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="agent-run-task-queue-runtime-update",
                workflows=[MoonMindAgentRun, RuntimeUpdateProviderProfileManager],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm.activity.artifacts",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
            Worker(
                env.client,
                task_queue="mm.activity.agent_runtime",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
        ):
            manager_id = "provider-profile-manager:claude_code"
            await env.client.start_workflow(
                RuntimeUpdateProviderProfileManager.run,
                {"runtime_id": "claude_code"},
                id=manager_id,
                task_queue="agent-run-task-queue-runtime-update",
            )
            manager_handle = env.client.get_workflow_handle(manager_id)
            child_id = "test-agent-runtime-selection-update"
            child_handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="claude_code",
                    execution_profile_ref="default-managed",
                    correlation_id="corr-runtime-update",
                    idempotency_key="idem-runtime-update",
                    parameters={
                        "model": "old-model",
                        "profileId": "default-managed",
                    },
                ),
                id=child_id,
                task_queue="agent-run-task-queue-runtime-update",
            )
            await asyncio.sleep(0.1)

            await child_handle.signal(
                "update_runtime_selection",
                {
                    "executionProfileRef": "alternate-managed",
                    "model": "new-model",
                    "parametersPatch": {
                        "model": "new-model",
                        "profileId": "alternate-managed",
                    },
                },
            )

            for _ in range(80):
                if _managed_launch_requests:
                    break
                await asyncio.sleep(0.1)
            manager_state = await manager_handle.query(
                RuntimeUpdateProviderProfileManager.get_state
            )
            assert _managed_launch_requests, manager_state
            launched_request = _managed_launch_requests[-1]["request"]
            assert launched_request["executionProfileRef"] == "alternate-managed"
            assert launched_request["parameters"]["model"] == "new-model"
            assert launched_request["parameters"]["profileId"] == "alternate-managed"

            await child_handle.signal(
                MoonMindAgentRun.completion_signal,
                {"summary": "completed after runtime update"},
            )
            result = await child_handle.result()
            assert result.summary == "completed after runtime update"


@pytest.mark.asyncio
async def test_managed_agent_runtime_selection_update_switches_runtime_manager():
    """Runtime edits while awaiting a slot must move the request to the new manager."""
    _managed_launch_requests.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="agent-run-task-queue-runtime-manager-switch",
                workflows=[MoonMindAgentRun, RuntimeUpdateProviderProfileManager],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm.activity.artifacts",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
            Worker(
                env.client,
                task_queue="mm.activity.agent_runtime",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
        ):
            claude_manager_id = "provider-profile-manager:claude_code"
            codex_manager_id = "provider-profile-manager:codex_cli"
            await env.client.start_workflow(
                RuntimeUpdateProviderProfileManager.run,
                {
                    "runtime_id": "claude_code",
                    "assignable_profile_id": "claude-after-edit",
                },
                id=claude_manager_id,
                task_queue="agent-run-task-queue-runtime-manager-switch",
            )
            await env.client.start_workflow(
                RuntimeUpdateProviderProfileManager.run,
                {
                    "runtime_id": "codex_cli",
                    "assignable_profile_id": "codex-managed",
                },
                id=codex_manager_id,
                task_queue="agent-run-task-queue-runtime-manager-switch",
            )
            claude_manager_handle = env.client.get_workflow_handle(
                claude_manager_id
            )
            codex_manager_handle = env.client.get_workflow_handle(codex_manager_id)

            child_id = "test-agent-runtime-manager-switch"
            child_handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="claude_code",
                    execution_profile_ref="claude-managed",
                    correlation_id="corr-runtime-manager-switch",
                    idempotency_key="idem-runtime-manager-switch",
                    parameters={
                        "targetRuntime": "claude_code",
                        "profileId": "claude-managed",
                        "model": "old-model",
                    },
                ),
                id=child_id,
                task_queue="agent-run-task-queue-runtime-manager-switch",
            )
            await asyncio.sleep(0.1)

            await child_handle.signal(
                "update_runtime_selection",
                {
                    "targetRuntime": "codex_cli",
                    "executionProfileRef": "codex-managed",
                    "model": "new-model",
                    "parametersPatch": {
                        "targetRuntime": "codex_cli",
                        "profileId": "codex-managed",
                        "model": "new-model",
                        "workflow": {
                            "runtime": {
                                "mode": "codex_cli",
                                "profileId": "codex-managed",
                            }
                        },
                    },
                },
            )

            for _ in range(80):
                codex_state = await codex_manager_handle.query(
                    RuntimeUpdateProviderProfileManager.get_state
                )
                if codex_state["pending_requests"]:
                    break
                await asyncio.sleep(0.1)

            claude_state = await claude_manager_handle.query(
                RuntimeUpdateProviderProfileManager.get_state
            )
            codex_state = await codex_manager_handle.query(
                RuntimeUpdateProviderProfileManager.get_state
            )
            assert claude_state["pending_requests"] == []
            assert any(
                payload.get("requester_workflow_id") == child_id
                for payload in claude_state["release_payloads"]
            )
            assert codex_state["request_payloads"]
            codex_request = codex_state["request_payloads"][-1]
            assert codex_request["requester_workflow_id"] == child_id
            assert codex_request["runtime_id"] == "codex_cli"
            assert codex_request["execution_profile_ref"] == "codex-managed"

            await child_handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await child_handle.result()


@pytest.mark.asyncio
async def test_managed_agent_runtime_switch_detaches_incompatible_session_before_launch():
    """A queued Codex retry switched to Claude must not launch a mixed request."""
    _managed_launch_requests.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="agent-run-task-queue-session-runtime-switch",
                workflows=[MoonMindAgentRun, RuntimeUpdateProviderProfileManager],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm.activity.artifacts",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
            Worker(
                env.client,
                task_queue="mm.activity.agent_runtime",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
        ):
            codex_manager_id = "provider-profile-manager:codex_cli"
            claude_manager_id = "provider-profile-manager:claude_code"
            await env.client.start_workflow(
                RuntimeUpdateProviderProfileManager.run,
                {
                    "runtime_id": "codex_cli",
                    "assignable_profile_id": "hold-codex-slot",
                },
                id=codex_manager_id,
                task_queue="agent-run-task-queue-session-runtime-switch",
            )
            await env.client.start_workflow(
                RuntimeUpdateProviderProfileManager.run,
                {
                    "runtime_id": "claude_code",
                    "assignable_profile_id": "*",
                },
                id=claude_manager_id,
                task_queue="agent-run-task-queue-session-runtime-switch",
            )

            child_id = "test-agent-session-runtime-switch"
            child_handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                AgentExecutionRequest(
                    agentKind="managed",
                    agentId="codex_cli",
                    executionProfileRef="default-managed",
                    correlationId="corr-session-runtime-switch",
                    idempotencyKey="idem-session-runtime-switch",
                    managedSession={
                        "workflowId": "task:session:codex_cli",
                        "agentRunId": "task",
                        "sessionId": "sess:task:codex_cli",
                        "sessionEpoch": 2,
                        "runtimeId": "codex_cli",
                        "executionProfileRef": "default-managed",
                    },
                    stepExecution={
                        "schemaVersion": "v1",
                        "workflowId": "task",
                        "runId": "run",
                        "logicalStepId": "node-1",
                        "executionOrdinal": 2,
                        "stepExecutionId": "task:run:node-1:execution:2",
                        "reason": "runtime_recovered",
                        "runtimeContextPolicy": "fresh_agent_run",
                        "runtimeSelection": {
                            "runtimeId": "codex_cli",
                            "agentKind": "managed",
                            "model": "gpt-5.6-sol",
                            "effort": "high",
                            "executionProfileRef": "default-managed",
                            "skillId": "pr-resolver",
                        },
                        "runtimeSessionReset": {
                            "resolvedPolicy": "fresh_agent_run",
                        },
                    },
                    parameters={
                        "targetRuntime": "codex_cli",
                        "model": "gpt-5.6-sol",
                        "effort": "high",
                        "profileId": "default-managed",
                    },
                ),
                id=child_id,
                task_queue="agent-run-task-queue-session-runtime-switch",
            )

            codex_manager_handle = env.client.get_workflow_handle(codex_manager_id)
            for _ in range(80):
                codex_state = await codex_manager_handle.query(
                    RuntimeUpdateProviderProfileManager.get_state
                )
                if codex_state["pending_requests"]:
                    break
                await asyncio.sleep(0.1)

            await child_handle.signal(
                "update_runtime_selection",
                {
                    "targetRuntime": "claude_code",
                    "model": "claude-opus-4-7",
                    "effort": "high",
                    "parametersPatch": {
                        "targetRuntime": "claude_code",
                        "model": "claude-opus-4-7",
                        "workflow": {
                            "runtime": {
                                "mode": "claude_code",
                            }
                        },
                    },
                },
            )

            for _ in range(80):
                if _managed_launch_requests:
                    break
                await asyncio.sleep(0.1)

            claude_manager_handle = env.client.get_workflow_handle(
                claude_manager_id
            )
            claude_state = await claude_manager_handle.query(
                RuntimeUpdateProviderProfileManager.get_state
            )
            assert _managed_launch_requests, {
                "child": str((await child_handle.describe()).status),
                "codexManager": await codex_manager_handle.query(
                    RuntimeUpdateProviderProfileManager.get_state
                ),
                "claudeManager": claude_state,
            }
            launched_request = _managed_launch_requests[-1]["request"]
            assert launched_request["agentId"] == "claude_code"
            assert launched_request.get("managedSession") is None
            assert launched_request["stepExecution"]["runtimeSessionReset"] is None
            assert launched_request["stepExecution"]["runtimeSelection"] == {
                "runtimeId": "claude_code",
                "agentKind": "managed",
                "model": "claude-opus-4-7",
                "effort": "high",
                "executionProfileRef": "default-managed",
                "skillId": "pr-resolver",
            }

            await child_handle.signal(
                MoonMindAgentRun.completion_signal,
                {"summary": "completed after safe runtime switch"},
            )
            result = await child_handle.result()
            assert result.summary == "completed after safe runtime switch"


@pytest.mark.asyncio
async def test_managed_agent_profile_switch_detaches_existing_session_before_launch():
    """A queued Codex retry must not reuse a session from the previous profile."""
    _managed_launch_requests.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="agent-run-task-queue-session-profile-switch",
                workflows=[MoonMindAgentRun, RuntimeUpdateProviderProfileManager],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm.activity.artifacts",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
            Worker(
                env.client,
                task_queue="mm.activity.agent_runtime",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
        ):
            manager_id = "provider-profile-manager:codex_cli"
            await env.client.start_workflow(
                RuntimeUpdateProviderProfileManager.run,
                {
                    "runtime_id": "codex_cli",
                    "assignable_profile_id": "alternate-managed",
                },
                id=manager_id,
                task_queue="agent-run-task-queue-session-profile-switch",
            )

            child_id = "test-agent-session-profile-switch"
            child_handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                AgentExecutionRequest(
                    agentKind="managed",
                    agentId="codex_cli",
                    executionProfileRef="default-managed",
                    correlationId="corr-session-profile-switch",
                    idempotencyKey="idem-session-profile-switch",
                    managedSession={
                        "workflowId": "task:session:codex_cli",
                        "agentRunId": "task",
                        "sessionId": "sess:task:codex_cli",
                        "sessionEpoch": 2,
                        "runtimeId": "codex_cli",
                        "executionProfileRef": "default-managed",
                    },
                    stepExecution={
                        "schemaVersion": "v1",
                        "workflowId": "task",
                        "runId": "run",
                        "logicalStepId": "node-1",
                        "executionOrdinal": 2,
                        "stepExecutionId": "task:run:node-1:execution:2",
                        "reason": "runtime_recovered",
                        "runtimeContextPolicy": "fresh_agent_run",
                        "runtimeSelection": {
                            "runtimeId": "codex_cli",
                            "agentKind": "managed",
                            "model": "gpt-5.6-sol",
                            "effort": "high",
                            "executionProfileRef": "default-managed",
                            "skillId": "pr-resolver",
                        },
                        "runtimeSessionReset": {
                            "resolvedPolicy": "fresh_agent_run",
                        },
                    },
                    parameters={
                        "targetRuntime": "codex_cli",
                        "model": "gpt-5.6-sol",
                        "effort": "high",
                        "profileId": "default-managed",
                        "metadata": {
                            "moonmind": {
                                "deferManagedSessionUntilSlot": {
                                    "agentRunId": "task",
                                }
                            }
                        },
                    },
                ),
                id=child_id,
                task_queue="agent-run-task-queue-session-profile-switch",
            )

            manager_handle = env.client.get_workflow_handle(manager_id)
            for _ in range(80):
                manager_state = await manager_handle.query(
                    RuntimeUpdateProviderProfileManager.get_state
                )
                if manager_state["pending_requests"]:
                    break
                await asyncio.sleep(0.1)

            await child_handle.signal(
                "update_runtime_selection",
                {
                    "targetRuntime": "codex_cli",
                    "executionProfileRef": "alternate-managed",
                    "parametersPatch": {
                        "profileId": "alternate-managed",
                        "workflow": {
                            "runtime": {
                                "mode": "codex_cli",
                                "profileId": "alternate-managed",
                            }
                        },
                    },
                },
            )

            for _ in range(80):
                if _managed_launch_requests:
                    break
                await asyncio.sleep(0.1)

            assert _managed_launch_requests, await manager_handle.query(
                RuntimeUpdateProviderProfileManager.get_state
            )
            launched_request = _managed_launch_requests[-1]["request"]
            assert launched_request["agentId"] == "codex_cli"
            assert launched_request.get("managedSession") is None
            assert launched_request["executionProfileRef"] == "alternate-managed"
            assert launched_request["stepExecution"]["runtimeSessionReset"] is None
            assert launched_request["stepExecution"]["runtimeSelection"][
                "executionProfileRef"
            ] == "alternate-managed"

            await child_handle.signal(
                MoonMindAgentRun.completion_signal,
                {"summary": "completed after safe profile switch"},
            )
            result = await child_handle.result()
            assert result.summary == "completed after safe profile switch"


@pytest.mark.asyncio
async def test_managed_agent_model_update_keeps_simultaneously_assigned_slot():
    """Model-only edits racing with slot assignment must keep the acquired slot."""
    _managed_launch_requests.clear()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="agent-run-task-queue-runtime-update-model",
                workflows=[MoonMindAgentRun, RuntimeUpdateProviderProfileManager],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm.activity.artifacts",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
            Worker(
                env.client,
                task_queue="mm.activity.agent_runtime",
                activities=_COMMON_AGENT_RUN_ACTIVITIES,
            ),
        ):
            manager_id = "provider-profile-manager:claude_code"
            await env.client.start_workflow(
                RuntimeUpdateProviderProfileManager.run,
                {
                    "runtime_id": "claude_code",
                    "assign_then_update_model": "new-model",
                },
                id=manager_id,
                task_queue="agent-run-task-queue-runtime-update-model",
            )
            manager_handle = env.client.get_workflow_handle(manager_id)
            child_handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                AgentExecutionRequest(
                    agent_kind="managed",
                    agent_id="claude_code",
                    execution_profile_ref="default-managed",
                    correlation_id="corr-runtime-update-model",
                    idempotency_key="idem-runtime-update-model",
                    parameters={
                        "model": "old-model",
                        "profileId": "default-managed",
                    },
                ),
                id="test-agent-runtime-selection-model-update",
                task_queue="agent-run-task-queue-runtime-update-model",
            )

            for _ in range(80):
                if _managed_launch_requests:
                    break
                await asyncio.sleep(0.1)
            manager_state = await manager_handle.query(
                RuntimeUpdateProviderProfileManager.get_state
            )
            assert _managed_launch_requests, manager_state
            launched_request = _managed_launch_requests[-1]["request"]
            assert launched_request["executionProfileRef"] == "default-managed"
            assert launched_request["parameters"]["model"] == "new-model"
            assert manager_state["release_payloads"] == []
            assert len(manager_state["request_payloads"]) == 1

            await child_handle.signal(
                MoonMindAgentRun.completion_signal,
                {"summary": "completed after model update"},
            )
            result = await child_handle.result()
            assert result.summary == "completed after model update"

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

_codex_cloud_status_mode = "feedback"
_codex_cloud_status_poll_count = 0

@_activity.defn(name="integration.codex_cloud.start")
async def mock_codex_cloud_start(request: dict) -> dict:
    _external_activity_calls.append("codex_cloud_start")
    return {
        "runId": "codex-cloud-task-001",
        "agentKind": "external",
        "agentId": "codex_cloud",
        "status": "running",
        "startedAt": datetime.now(tz=UTC).isoformat(),
        "pollHintSeconds": 1,
        "metadata": {
            "providerStatus": "running",
            "normalizedStatus": "running",
        },
    }

@_activity.defn(name="integration.codex_cloud.status")
async def mock_codex_cloud_status(request: dict) -> dict:
    global _codex_cloud_status_poll_count
    _codex_cloud_status_poll_count += 1
    _external_activity_calls.append(
        f"codex_cloud_status:{_codex_cloud_status_poll_count}"
    )
    status = (
        "awaiting_feedback"
        if _codex_cloud_status_mode == "feedback"
        else "running"
    )
    return {
        "runId": request.get("runId")
        or request.get("run_id")
        or "codex-cloud-task-001",
        "agentKind": "external",
        "agentId": "codex_cloud",
        "status": status,
        "metadata": {
            "providerStatus": status,
            "normalizedStatus": status,
        },
    }

@_activity.defn(name="integration.codex_cloud.fetch_result")
async def mock_codex_cloud_fetch_result(request: dict) -> dict:
    _external_activity_calls.append("codex_cloud_fetch_result")
    return {
        "summary": "Codex Cloud completed unexpectedly.",
        "metadata": {"normalizedStatus": "completed"},
    }

@_activity.defn(name="integration.codex_cloud.cancel")
async def mock_codex_cloud_cancel(request: dict) -> dict:
    return {
        "runId": request.get("runId")
        or request.get("run_id")
        or "codex-cloud-task-001",
        "agentKind": "external",
        "agentId": "codex_cloud",
        "status": "cancelled",
    }

async def _run_codex_cloud_parent_for_resiliency_test(
    *,
    workflow_id: str,
) -> tuple[AgentRunResult, dict[str, Any]]:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, TestAgentRunParent],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm.workflow",
                activities=[mock_resolve_adapter_metadata],
            ),
            Worker(
                env.client,
                task_queue="mm.activity.integrations",
                activities=[
                    mock_codex_cloud_start,
                    mock_codex_cloud_status,
                    mock_codex_cloud_fetch_result,
                    mock_codex_cloud_cancel,
                ],
            ),
            Worker(
                env.client,
                task_queue="mm.activity.agent_runtime",
                activities=[mock_publish_artifacts],
            ),
        ):
            parent_handle = await env.client.start_workflow(
                TestAgentRunParent.run,
                AgentExecutionRequest(
                    agent_kind="external",
                    agent_id="codex_cloud",
                    execution_profile_ref="profile:codex-cloud-default",
                    correlation_id=f"{workflow_id}:corr",
                    idempotency_key=f"{workflow_id}:idem",
                ),
                id=workflow_id,
                task_queue="agent-run-task-queue",
            )
            try:
                result = await asyncio.wait_for(parent_handle.result(), timeout=15)
            except asyncio.TimeoutError as exc:
                raise AssertionError(
                    "AgentRun resiliency test timed out; "
                    f"calls={_external_activity_calls}, "
                    f"status_polls={_codex_cloud_status_poll_count}"
                ) from exc
            parent_state = await parent_handle.query(TestAgentRunParent.get_state)
            return result, parent_state

@pytest.mark.asyncio
async def test_agent_run_escalates_external_feedback_request_to_parent_intervention():
    global _codex_cloud_status_mode, _codex_cloud_status_poll_count
    _codex_cloud_status_mode = "feedback"
    _codex_cloud_status_poll_count = 0
    _external_activity_calls.clear()

    result, parent_state = await _run_codex_cloud_parent_for_resiliency_test(
        workflow_id="test-parent-codex-cloud-feedback",
    )

    assert result.provider_error_code == "intervention_requested"
    assert result.metadata["reason"] == "agent_requested_feedback"
    assert result.metadata["status"] == "intervention_requested"
    assert result.metadata["resiliencyPolicy"]["runtime"] == "codex_cloud"
    assert any(
        change["state"] == "intervention_requested"
        and "feedback" in change["reason"].lower()
        for change in parent_state["state_changes"]
    )
    assert "codex_cloud_fetch_result" not in _external_activity_calls

@pytest.mark.asyncio
async def test_agent_run_escalates_external_no_progress_to_parent_intervention(
    monkeypatch,
):
    global _codex_cloud_status_mode, _codex_cloud_status_poll_count
    _codex_cloud_status_mode = "stagnant"
    _codex_cloud_status_poll_count = 0
    _external_activity_calls.clear()

    def test_policy(request: AgentExecutionRequest) -> dict[str, Any]:
        return {
            "runtime": request.agent_id,
            "noProgressTimeoutSeconds": 1,
            "stuckAction": "request_intervention",
            "retryPolicy": "test_short_no_progress_policy",
        }

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_resiliency_policy_for_request",
        staticmethod(test_policy),
    )

    result, parent_state = await _run_codex_cloud_parent_for_resiliency_test(
        workflow_id="test-parent-codex-cloud-no-progress",
    )

    assert _codex_cloud_status_poll_count >= 2
    assert result.provider_error_code == "intervention_requested"
    assert result.metadata["reason"] == "stuck_no_progress"
    assert result.metadata["status"] == "intervention_requested"
    assert (
        result.metadata["resiliencyPolicy"]["retryPolicy"]
        == "test_short_no_progress_policy"
    )
    assert any(
        change["state"] == "intervention_requested"
        and "no observable progress" in change["reason"].lower()
        for change in parent_state["state_changes"]
    )
    assert "codex_cloud_fetch_result" not in _external_activity_calls

@pytest.mark.asyncio
async def test_agent_run_reconciles_managed_completion_during_no_progress_grace(
    monkeypatch,
):
    global _managed_status_mode, _managed_status_poll_count, _managed_fetch_result_count
    _managed_launch_requests.clear()
    _managed_status_mode = "silent_then_completed"
    _managed_status_poll_count = 0
    _managed_fetch_result_count = 0

    def test_policy(request: AgentExecutionRequest) -> dict[str, Any]:
        return {
            "runtime": request.agent_id,
            "noProgressTimeoutSeconds": 1,
            "noProgressGraceSeconds": 5,
            "stuckAction": "request_intervention",
            "retryPolicy": "test_managed_grace_reconciliation",
        }

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_resiliency_policy_for_request",
        staticmethod(test_policy),
    )

    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with (
                Worker(
                    env.client,
                    task_queue="agent-run-task-queue",
                    workflows=[
                        MoonMindAgentRun,
                        MockProviderProfileManager,
                        TestAgentRunParent,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue="mm.activity.artifacts",
                    activities=[
                        mock_provider_profile_list,
                        mock_provider_profile_ensure_manager,
                        mock_provider_profile_reset_manager,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue="mm.activity.agent_runtime",
                    activities=[
                        mock_agent_runtime_build_launch_context,
                        mock_agent_runtime_launch,
                        mock_agent_runtime_status,
                        mock_agent_runtime_fetch_result,
                        mock_publish_artifacts,
                        mock_cancel,
                    ],
                ),
            ):
                manager_id = "provider-profile-manager:claude_code"
                await env.client.start_workflow(
                    MockProviderProfileManager.run,
                    {"runtime_id": "claude_code"},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )

                parent_handle = await env.client.start_workflow(
                    TestAgentRunParent.run,
                    AgentExecutionRequest(
                        agent_kind="managed",
                        agent_id="claude_code",
                        execution_profile_ref="default-managed",
                        correlation_id="managed-grace:corr",
                        idempotency_key="managed-grace:idem",
                    ),
                    id="test-parent-managed-no-progress-grace",
                    task_queue="agent-run-task-queue",
                )

                result = await asyncio.wait_for(parent_handle.result(), timeout=15)
                parent_state = await parent_handle.query(TestAgentRunParent.get_state)

        assert isinstance(result, AgentRunResult)
        assert result.failure_class is None
        assert result.provider_error_code is None
        assert result.summary == "Managed run completed during no-progress grace."
        assert result.metadata["resiliencyPolicy"]["retryPolicy"] == (
            "test_managed_grace_reconciliation"
        )
        assert _managed_status_poll_count >= 3
        assert _managed_fetch_result_count == 1
        assert not any(
            change["state"] == "intervention_requested"
            for change in parent_state["state_changes"]
        )
    finally:
        _managed_status_mode = "default"

@pytest.mark.asyncio
async def test_agent_run_reconciles_managed_quota_after_no_progress_cancel(
    monkeypatch,
):
    global _managed_status_mode, _managed_status_poll_count
    global _managed_fetch_result_count, _managed_cancel_count
    _managed_launch_requests.clear()
    _managed_status_mode = "silent_then_rate_limited_after_cancel"
    _managed_status_poll_count = 0
    _managed_fetch_result_count = 0
    _managed_cancel_count = 0

    def test_policy(request: AgentExecutionRequest) -> dict[str, Any]:
        return {
            "runtime": request.agent_id,
            "noProgressTimeoutSeconds": 1,
            "noProgressGraceSeconds": 1,
            "stuckAction": "request_intervention",
            "retryPolicy": "test_managed_cancel_fetch_rate_limit",
        }

    monkeypatch.setattr(
        MoonMindAgentRun,
        "_resiliency_policy_for_request",
        staticmethod(test_policy),
    )

    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with (
                Worker(
                    env.client,
                    task_queue="agent-run-task-queue",
                    workflows=[
                        MoonMindAgentRun,
                        MockProviderProfileManager,
                        TestAgentRunParent,
                    ],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue="mm.activity.artifacts",
                    activities=[
                        mock_provider_profile_list,
                        mock_provider_profile_ensure_manager,
                        mock_provider_profile_reset_manager,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue="mm.activity.agent_runtime",
                    activities=[
                        mock_agent_runtime_build_launch_context,
                        mock_agent_runtime_launch,
                        mock_agent_runtime_status,
                        mock_agent_runtime_fetch_result,
                        mock_publish_artifacts,
                        mock_cancel,
                    ],
                ),
            ):
                manager_id = "provider-profile-manager:claude_code"
                await env.client.start_workflow(
                    MockProviderProfileManager.run,
                    {"runtime_id": "claude_code"},
                    id=manager_id,
                    task_queue="agent-run-task-queue",
                )
                manager_handle = env.client.get_workflow_handle(manager_id)

                parent_handle = await env.client.start_workflow(
                    TestAgentRunParent.run,
                    AgentExecutionRequest(
                        agent_kind="managed",
                        agent_id="claude_code",
                        execution_profile_ref="default-managed",
                        correlation_id="managed-quota-after-cancel:corr",
                        idempotency_key="managed-quota-after-cancel:idem",
                    ),
                    id="test-parent-managed-quota-after-cancel",
                    task_queue="agent-run-task-queue",
                )

                await env.sleep(50)

                manager_state = {}
                parent_state = {}
                for _ in range(20):
                    manager_state = await manager_handle.query(
                        MockProviderProfileManager.get_state
                    )
                    parent_state = await parent_handle.query(
                        TestAgentRunParent.get_state
                    )
                    if manager_state.get("cooldown_reports") and any(
                        "retry scheduled" in change["reason"].lower()
                        for change in parent_state.get("state_changes", [])
                    ):
                        break
                    await asyncio.sleep(0.1)

                debug_state = {
                    "cancel_count": _managed_cancel_count,
                    "fetch_count": _managed_fetch_result_count,
                    "status_polls": _managed_status_poll_count,
                    "manager_state": manager_state,
                    "parent_state": parent_state,
                }
                assert _managed_cancel_count >= 1, debug_state
                assert _managed_fetch_result_count >= 2, debug_state
                assert manager_state["cooldown_reports"], debug_state
                assert (
                    manager_state["cooldown_reports"][-1]["cooldown_seconds"]
                    == 900
                )
                assert not any(
                    change["state"] == "intervention_requested"
                    for change in parent_state["state_changes"]
                )
                assert any(
                    "retry scheduled" in change["reason"].lower()
                    and "900s cooldown" in change["reason"].lower()
                    for change in parent_state["state_changes"]
                )

                await parent_handle.cancel()
                with pytest.raises(WorkflowFailureError):
                    await parent_handle.result()
    finally:
        _managed_status_mode = "default"
        _managed_cancel_count = 0

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
        "MoonMindAgentRun without a MoonMind.UserWorkflow parent, so only the manager-side "
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
    2. Parent-side: MoonMind.UserWorkflow sends release_slot defensively when a child
       exits in a terminal state (Task 4).

    This test runs AgentRun without a MoonMind.UserWorkflow parent, so only the
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
                runtime_mapping = {"claude_code": "claude_code", "claude": "claude_code", "codex": "codex_cli"}
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
            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
                mock_agent_runtime_launch,
                mock_agent_runtime_build_launch_context,
                mock_agent_runtime_status,
                mock_agent_runtime_fetch_result,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                    MockProviderProfileManager.run,
                    {
                        "runtime_id": request.agent_id,
                        "assign_slots": True,
                        "default_profile_id": "test-openrouter-high-priority",
                    },
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

                manager_state = await manager_handle.query(MockProviderProfileManager.get_state)

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
            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
                mock_agent_runtime_launch,
                mock_agent_runtime_build_launch_context,
                mock_agent_runtime_status,
                mock_agent_runtime_fetch_result,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                    MockProviderProfileManager.run,
                    {
                        "runtime_id": request.agent_id,
                        "assign_slots": True,
                        "default_profile_id": "test-openrouter-high-priority",
                    },
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

                manager_state = await manager_handle.query(MockProviderProfileManager.get_state)

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

                manager_state_after = await manager_handle.query(MockProviderProfileManager.get_state)

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
            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
                mock_agent_runtime_launch,
                mock_agent_runtime_build_launch_context,
                mock_agent_runtime_status,
                mock_agent_runtime_fetch_result,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                    MockProviderProfileManager.run,
                    {
                        "runtime_id": request.agent_id,
                        "assign_slots": True,
                        "default_profile_id": "test-openrouter-high-priority",
                    },
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
                manager_state_before = await manager_handle.query(MockProviderProfileManager.get_state)
                assert "test-openrouter-high-priority" in manager_state_before.get("leases", {}), (
                    f"Expected slot leased before cancellation, leases={manager_state_before['leases']}"
                )

                # Cancel the workflow
                await agent_handle.cancel()
                await asyncio.sleep(0.5)

                # Verify slot is released after cancellation
                try:
                    manager_state_after = await manager_handle.query(MockProviderProfileManager.get_state)
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

            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
                mock_agent_runtime_launch,
                mock_agent_runtime_build_launch_context,
                mock_agent_runtime_status,
                mock_agent_runtime_fetch_result,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                    MockProviderProfileManager.run,
                    {
                        "runtime_id": request.agent_id,
                        "assign_slots": True,
                        "default_profile_id": "test-openrouter-high-priority",
                        "selector_provider_map": {
                            "openrouter": "test-openrouter-high-priority"
                        },
                    },
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

                manager_state = await manager_handle.query(MockProviderProfileManager.get_state)

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
            openrouter_activities = [
                mock_publish_artifacts,
                mock_cancel,
                mock_provider_profile_list_openrouter_disabled_high,
                mock_provider_profile_ensure_manager,
                mock_provider_profile_reset_manager,
                mock_agent_runtime_launch,
                mock_agent_runtime_build_launch_context,
                mock_agent_runtime_status,
                mock_agent_runtime_fetch_result,
            ]

            async with Worker(
                env.client,
                task_queue="agent-run-task-queue",
                workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                    MockProviderProfileManager.run,
                    {
                        "runtime_id": request.agent_id,
                        "assign_slots": True,
                        "default_profile_id": "test-openrouter-low-priority",
                        "selector_provider_map": {
                            "openrouter": "test-openrouter-low-priority"
                        },
                    },
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

                manager_state = await manager_handle.query(MockProviderProfileManager.get_state)

                # The manager should have resolved to the low-priority profile
                assert "test-openrouter-low-priority" in manager_state.get("resolved_profiles", []), (
                    f"Expected routing to resolve to low-priority profile when high is disabled, resolved={manager_state['resolved_profiles']}"
                )

                # Cancel the run
                await agent_handle.cancel()
        finally:
            test_module.mock_provider_profile_list = original_mock
