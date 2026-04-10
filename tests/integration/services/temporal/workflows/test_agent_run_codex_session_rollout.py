from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from temporalio import activity as _activity
from temporalio import workflow
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionHandle,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    LaunchCodexManagedSessionRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


# NOTE: This test uses the Temporal time-skipping test server and is not
# suitable for the required integration_ci suite.
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@workflow.defn(name="MoonMind.ProviderProfileManager")
class MockProviderProfileManager:
    def __init__(self) -> None:
        self._shutdown = False
        self.pending_requests: list[dict[str, Any]] = []
        self._leases: dict[str, str] = {}

    @workflow.signal
    def request_slot(self, payload: dict[str, Any]) -> None:
        self.pending_requests.append(dict(payload))

    @workflow.signal
    def release_slot(self, payload: dict[str, Any]) -> None:
        requester_id = payload.get("requester_workflow_id")
        if not isinstance(requester_id, str):
            return
        for profile_id, workflow_id in list(self._leases.items()):
            if workflow_id == requester_id:
                del self._leases[profile_id]

    @workflow.signal
    def report_cooldown(self, payload: dict[str, Any]) -> None:
        del payload

    @workflow.signal
    def sync_profiles(self, payload: dict[str, Any]) -> None:
        del payload

    @workflow.signal
    def shutdown(self) -> None:
        self._shutdown = True

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        del input_payload
        while not self._shutdown:
            await workflow.wait_condition(
                lambda: bool(self.pending_requests) or self._shutdown
            )
            while self.pending_requests:
                request = self.pending_requests.pop(0)
                profile_id = str(
                    request.get("execution_profile_ref")
                    or request.get("profile_id")
                    or "default-managed"
                ).strip()
                self._leases[profile_id] = str(request["requester_workflow_id"])
                handle = workflow.get_external_workflow_handle(
                    str(request["requester_workflow_id"])
                )
                await handle.signal("slot_assigned", {"profile_id": profile_id})
        return {}


@workflow.defn(name="DummyCodexSessionWorkflow")
class DummyCodexSessionWorkflow:
    def __init__(self) -> None:
        self._status: dict[str, Any] = {
            "status": "active",
            "binding": None,
            "containerId": None,
            "threadId": None,
            "activeTurnId": None,
            "terminationRequested": False,
        }
        self._shutdown = False

    @workflow.signal
    def attach_runtime_handles(self, payload: dict[str, Any]) -> None:
        self._status.update(dict(payload))

    @workflow.signal
    def shutdown(self) -> None:
        self._shutdown = True

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        return dict(self._status)

    @workflow.run
    async def run(self, binding_payload: dict[str, Any]) -> dict[str, Any]:
        self._status["binding"] = dict(binding_payload)
        await workflow.wait_condition(lambda: self._shutdown)
        return dict(self._status)


@_activity.defn(name="agent_runtime.publish_artifacts")
async def mock_publish_artifacts(
    result: AgentRunResult | None = None,
) -> AgentRunResult | None:
    return result


@_activity.defn(name="agent_runtime.cancel")
async def mock_cancel(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": request.get("run_id", "unknown"),
        "agentKind": request.get("agent_kind", "managed"),
        "agentId": request.get("agent_id", "codex"),
        "status": "canceled",
    }


@_activity.defn(name="provider_profile.list")
async def mock_provider_profile_list(request: dict[str, Any]) -> dict[str, Any]:
    runtime_id = str(request.get("runtime_id") or "codex_cli").strip() or "codex_cli"
    return {
        "profiles": [
            {
                "profile_id": "default-managed",
                "runtime_id": runtime_id,
                "provider_id": "openai",
                "auth_mode": "volume",
                "volume_ref": "test-volume",
                "volume_mount_path": "/tmp/auth",
                "account_label": "test-openai",
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


@_activity.defn(name="provider_profile.ensure_manager")
async def mock_provider_profile_ensure_manager(
    request: dict[str, Any],
) -> dict[str, Any]:
    return {
        "started": True,
        "workflow_id": f"provider-profile-manager:{request.get('runtime_id', 'codex_cli')}",
    }


@_activity.defn(name="provider_profile.reset_manager")
async def mock_provider_profile_reset_manager(
    request: dict[str, Any],
) -> dict[str, Any]:
    return {
        "reset": True,
        "workflow_id": f"provider-profile-manager:{request.get('runtime_id', 'codex_cli')}",
    }


class FakeCodexSessionController:
    async def launch_session(self, request: Any) -> CodexManagedSessionHandle:
        return CodexManagedSessionHandle(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": "ctr-test-codex",
                "threadId": "thread-test-codex",
            },
            status="ready",
            imageRef=str(request.image_ref),
        )

    async def send_turn(self, request: Any) -> CodexManagedSessionTurnResponse:
        return CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
            },
            turnId="turn-test-codex",
            status="completed",
            metadata={
                "assistantText": "Recovered final answer without vendor turn id",
            },
        )

    async def fetch_session_summary(self, request: Any) -> CodexManagedSessionSummary:
        return CodexManagedSessionSummary(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
            },
            latestSummaryRef=None,
            latestCheckpointRef=None,
            latestControlEventRef=None,
            metadata={
                "lastAssistantText": "Recovered final answer without vendor turn id",
                "lastTurnId": "turn-test-codex",
                "lastTurnStatus": "completed",
                "lastTurnError": None,
            },
        )

    async def publish_session_artifacts(
        self,
        request: Any,
    ) -> CodexManagedSessionArtifactsPublication:
        return CodexManagedSessionArtifactsPublication(
            sessionState={
                "sessionId": request.session_id,
                "sessionEpoch": request.session_epoch,
                "containerId": request.container_id,
                "threadId": request.thread_id,
            },
            publishedArtifactRefs=(),
            latestSummaryRef=None,
            latestCheckpointRef=None,
            latestControlEventRef=None,
            metadata=dict(request.metadata),
        )


async def test_agent_run_managed_codex_session_recovers_terminal_rollout_without_turn_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_runtime_root = tmp_path / "agent_jobs"
    managed_run_root = managed_runtime_root / "managed_runs"
    monkeypatch.setattr(
        agent_run_module,
        "_MANAGED_RUNTIME_STORE_ROOT",
        str(managed_runtime_root),
    )
    monkeypatch.setattr(
        agent_run_module,
        "_MANAGED_RUN_STORE_ROOT",
        str(managed_run_root),
    )
    controller = FakeCodexSessionController()

    @_activity.defn(name="agent_runtime.build_launch_context")
    async def build_launch_context(payload: dict[str, Any]) -> dict[str, Any]:
        profile = payload["profile"]
        return {
            "profile_id": profile["profile_id"],
            "credential_source": "volume",
            "delta_env_overrides": {},
            "passthrough_env_keys": [],
            "env_keys_count": 0,
        }

    @_activity.defn(name="agent_runtime.load_session_snapshot")
    async def load_session_snapshot(
        payload: dict[str, Any],
    ) -> CodexManagedSessionSnapshot:
        binding = CodexManagedSessionBinding.model_validate(payload)
        return CodexManagedSessionSnapshot(
            binding=binding,
            status="active",
            containerId=None,
            threadId=None,
            activeTurnId=None,
            terminationRequested=False,
        )

    @_activity.defn(name="agent_runtime.launch_session")
    async def launch_session(payload: dict[str, Any]) -> CodexManagedSessionHandle:
        request = LaunchCodexManagedSessionRequest.model_validate(payload["request"])
        return await controller.launch_session(
            type(
                "LaunchRequest",
                (),
                {
                    "session_id": request.session_id,
                    "session_epoch": request.session_epoch,
                    "image_ref": request.image_ref,
                },
            )()
        )

    @_activity.defn(name="agent_runtime.prepare_turn_instructions")
    async def prepare_turn_instructions(payload: dict[str, Any]) -> str:
        request = payload["request"]
        parameters = request.get("parameters") or {}
        return str(parameters.get("instructions") or "").strip() or "prepared instructions"

    @_activity.defn(name="agent_runtime.send_turn")
    async def send_turn(payload: dict[str, Any]) -> CodexManagedSessionTurnResponse:
        request = SendCodexManagedSessionTurnRequest.model_validate(payload)
        return await controller.send_turn(
            type(
                "SendTurnRequest",
                (),
                {
                    "session_id": request.session_id,
                    "session_epoch": request.session_epoch,
                    "container_id": request.container_id,
                    "thread_id": request.thread_id,
                },
            )()
        )

    @_activity.defn(name="agent_runtime.fetch_session_summary")
    async def fetch_session_summary(
        payload: dict[str, Any],
    ) -> CodexManagedSessionSummary:
        request = FetchCodexManagedSessionSummaryRequest.model_validate(payload)
        return await controller.fetch_session_summary(
            type(
                "SummaryRequest",
                (),
                {
                    "session_id": request.session_id,
                    "session_epoch": request.session_epoch,
                    "container_id": request.container_id,
                    "thread_id": request.thread_id,
                },
            )()
        )

    @_activity.defn(name="agent_runtime.publish_session_artifacts")
    async def publish_session_artifacts(
        payload: dict[str, Any],
    ) -> CodexManagedSessionArtifactsPublication:
        request = PublishCodexManagedSessionArtifactsRequest.model_validate(payload)
        return await controller.publish_session_artifacts(
            type(
                "PublishRequest",
                (),
                {
                    "session_id": request.session_id,
                    "session_epoch": request.session_epoch,
                    "container_id": request.container_id,
                    "thread_id": request.thread_id,
                    "metadata": dict(request.metadata),
                },
            )()
        )

    @_activity.defn(name="agent_runtime.fetch_result")
    async def fetch_result(payload: dict[str, Any]) -> AgentRunResult:
        del payload
        return AgentRunResult(
            summary="Recovered final answer without vendor turn id",
            metadata={
                "sessionSummary": {
                    "metadata": {
                        "lastAssistantText": "Recovered final answer without vendor turn id",
                    }
                }
            },
        )

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        executionProfileRef="default-managed",
        correlationId="corr-codex-rollout",
        idempotencyKey="idem-codex-rollout",
        parameters={
            "publishMode": "none",
            "instructions": "Shrink the Mission Control title text a bit",
        },
        managedSession={
            "workflowId": "test-codex-session-workflow",
            "taskRunId": "task-run-codex-rollout",
            "sessionId": "sess:test-codex-session-workflow",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "default-managed",
        },
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="agent-run-task-queue",
            workflows=[
                MoonMindAgentRun,
                MockProviderProfileManager,
                DummyCodexSessionWorkflow,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            async with Worker(
                env.client,
                task_queue=settings.temporal.activity_artifacts_task_queue,
                activities=[
                    mock_provider_profile_list,
                    mock_provider_profile_ensure_manager,
                    mock_provider_profile_reset_manager,
                ],
            ):
                async with Worker(
                    env.client,
                    task_queue=settings.temporal.activity_agent_runtime_task_queue,
                    activities=[
                        mock_publish_artifacts,
                        mock_cancel,
                        build_launch_context,
                        load_session_snapshot,
                        launch_session,
                        prepare_turn_instructions,
                        send_turn,
                        fetch_session_summary,
                        publish_session_artifacts,
                        fetch_result,
                    ],
                ):
                    manager_handle = await env.client.start_workflow(
                        MockProviderProfileManager.run,
                        {"runtime_id": "codex_cli"},
                        id="provider-profile-manager:codex_cli",
                        task_queue="agent-run-task-queue",
                    )
                    session_handle = await env.client.start_workflow(
                        DummyCodexSessionWorkflow.run,
                        request.managed_session.model_dump(mode="json", by_alias=True),
                        id=request.managed_session.workflow_id,
                        task_queue="agent-run-task-queue",
                    )

                    try:
                        result = await env.client.execute_workflow(
                            MoonMindAgentRun.run,
                            request,
                            id="test-agent-run-managed-codex-rollout-recovery",
                            task_queue="agent-run-task-queue",
                        )
                    finally:
                        await session_handle.signal(DummyCodexSessionWorkflow.shutdown)
                        await session_handle.result()
                        try:
                            await manager_handle.signal(MockProviderProfileManager.shutdown)
                            await manager_handle.result()
                        except RPCError:
                            # Best-effort teardown can race the worker shutdown path.
                            pass

                    assert isinstance(result, AgentRunResult)
                    assert result.failure_class is None
                    assert result.summary == "Recovered final answer without vendor turn id"
                    assert isinstance(result.metadata, dict)
                    assert result.metadata["sessionSummary"]["metadata"][
                        "lastAssistantText"
                    ] == "Recovered final answer without vendor turn id"
