import asyncio
import os
from datetime import timedelta
from temporalio import workflow, activity
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        AgentRunHandle,
        AgentRunResult,
        AgentRunStatus as AgentRunStatusModel,
    )
    from moonmind.workflows.adapters.agent_adapter import AgentAdapter
    from moonmind.workflows.adapters.managed_agent_adapter import ManagedAgentAdapter
    from moonmind.workflows.adapters.external_adapter_registry import (
        build_default_registry,
    )
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

# Map canonical AgentRunState literals to workflow-usable status constants.
# Named RunStatus (not AgentRunStatus) to avoid shadowing the Pydantic model
# imported in activity code.
class RunStatus:
    queued = "queued"
    launching = "launching"
    running = "running"
    awaiting_callback = "awaiting_callback"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"

PROVIDER_RATE_LIMIT_ERROR_CODE = "429"

# Activity catalog constants for agent_runtime fleet routing.
AGENT_RUNTIME_TASK_QUEUE = "mm.activity.agent_runtime"
AGENT_RUNTIME_ACTIVITY_TIMEOUT = timedelta(minutes=5)
AGENT_RUNTIME_CANCEL_TIMEOUT = timedelta(minutes=1)
INTEGRATIONS_TASK_QUEUE = "mm.activity.integrations"
INTEGRATIONS_ACTIVITY_TIMEOUT = timedelta(minutes=5)
INTEGRATIONS_STATUS_TIMEOUT = timedelta(seconds=60)
WORKFLOW_TASK_QUEUE = "mm.workflow"
STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT = timedelta(seconds=120)


@activity.defn(name="integration.resolve_external_adapter")
async def resolve_external_adapter(agent_id: str) -> str:
    """Activity: verify that *agent_id* has a registered adapter.

    All non-deterministic work (reading env vars, dynamic imports) runs
    here rather than in the workflow so that replays remain deterministic.

    Returns the validated agent_id on success; raises if no adapter exists.
    """

    registry = build_default_registry()
    # Validate the adapter is registered — this forces the gate check.
    registry.create(agent_id)
    return agent_id


@activity.defn(name="integration.external_adapter_execution_style")
async def external_adapter_execution_style(agent_id: str) -> str:
    """Return ``polling`` or ``streaming_gateway`` for *agent_id*."""

    from moonmind.workflows.adapters.base_external_agent_adapter import (
        BaseExternalAgentAdapter,
    )
    from moonmind.workflows.adapters.external_adapter_registry import (
        build_default_registry,
    )

    registry = build_default_registry()
    adapter = registry.create(agent_id)
    if isinstance(adapter, BaseExternalAgentAdapter):
        return adapter.provider_capability.execution_style
    return "polling"


@workflow.defn(name="MoonMind.AgentRun")
class MoonMindAgentRun:
    def __init__(self):
        self.completion_event = asyncio.Event()
        self.slot_assigned_event = asyncio.Event()
        self.run_status = RunStatus.queued
        self.final_result: AgentRunResult | None = None
        self.run_id: str | None = None
        self.agent_kind: str | None = None
        self._assigned_profile_id: str | None = None
        self._external_agent_id: str | None = None

    async def _ensure_manager_and_signal(
        self, manager_id: str, runtime_id: str
    ) -> workflow.ExternalWorkflowHandle:
        """Signal the auth-profile-manager; auto-start it on first failure.

        Tries the signal. If the manager workflow doesn't exist, starts it
        via the ``auth_profile.ensure_manager`` activity and retries once.
        """
        manager_handle = workflow.get_external_workflow_handle(manager_id)
        signal_payload = {
            "requester_workflow_id": workflow.info().workflow_id,
            "runtime_id": runtime_id,
        }
        try:
            await manager_handle.signal("request_slot", signal_payload)
        except ApplicationError as exc:
            if "ExternalWorkflowExecutionNotFound" not in (
                getattr(exc, "type", None) or str(exc)
            ):
                raise
            workflow.logger.warning(
                "AuthProfileManager %s not found, auto-starting via activity",
                manager_id,
            )
            await workflow.execute_activity(
                "auth_profile.ensure_manager",
                {"runtime_id": runtime_id},
                task_queue="mm.activity.artifacts",
                start_to_close_timeout=timedelta(seconds=30),
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            # Re-acquire handle and retry signal once.
            manager_handle = workflow.get_external_workflow_handle(manager_id)
            await manager_handle.signal("request_slot", signal_payload)
        return manager_handle

    @workflow.signal
    def completion_signal(self, result_dict: dict) -> None:
        self.final_result = AgentRunResult(**result_dict)
        self.run_status = RunStatus.completed
        self.completion_event.set()

    @workflow.signal
    def slot_assigned(self, payload: dict) -> None:
        self._assigned_profile_id = payload.get("profile_id")
        self.slot_assigned_event.set()

    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        self.agent_kind = request.agent_kind

        timeout_seconds = 3600
        if request.timeout_policy and "timeout_seconds" in request.timeout_policy:
            timeout_seconds = request.timeout_policy["timeout_seconds"]

        # Loop for handling 429 cooldown & profile swaps safely within the timeout boundary
        overall_start = workflow.now()

        try:
            while True:
                skip_poll_and_fetch = False
                elapsed = (workflow.now() - overall_start).total_seconds()
                if elapsed >= timeout_seconds:
                    self.run_status = RunStatus.timed_out
                    return AgentRunResult(failure_class="execution_error")

                manager_handle = None
                # Acquire auth slot if managed
                if request.agent_kind == "managed":
                    runtime_mapping = {
                        "gemini_cli": "gemini_cli",
                        "claude": "claude_code",
                        "codex": "codex_cli",
                    }
                    runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                    manager_id = f"auth-profile-manager:{runtime_id}"

                    self.slot_assigned_event.clear()
                    manager_handle = await self._ensure_manager_and_signal(
                        manager_id, runtime_id
                    )

                    # Wait for assigned slot
                    try:
                        await workflow.wait_condition(
                            lambda: self.slot_assigned_event.is_set(),
                            timeout=timedelta(minutes=5)
                        )
                    except asyncio.TimeoutError:
                        workflow.logger.error("Timed out waiting for auth profile slot.")
                        self.run_status = RunStatus.timed_out
                        return AgentRunResult(failure_class="execution_error")
                    request.execution_profile_ref = self._assigned_profile_id

                    # Wire ManagedAgentAdapter with real DI callables.
                    # The slot_requester / slot_releaser / cooldown_reporter
                    # are thin wrappers around AuthProfileManager signals.
                    # The profile_fetcher dispatches to the auth_profile.list
                    # activity on the artifacts fleet.
                    wf_id = workflow.info().workflow_id

                    async def _profile_fetcher(**kw):
                        return await workflow.execute_activity(
                            "auth_profile.list",
                            {"runtime_id": kw.get("runtime_id", runtime_id)},
                            task_queue="mm.activity.artifacts",
                            start_to_close_timeout=timedelta(seconds=30),
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                    async def _slot_requester(**kw):
                        await manager_handle.signal("request_slot", {
                            "requester_workflow_id": wf_id,
                            "runtime_id": kw.get("runtime_id", runtime_id),
                        })

                    async def _slot_releaser(**kw):
                        await manager_handle.signal("release_slot", {
                            "requester_workflow_id": wf_id,
                            "profile_id": kw.get("profile_id", request.execution_profile_ref),
                        })

                    async def _cooldown_reporter(**kw):
                        await manager_handle.signal("report_cooldown", {
                            "profile_id": kw.get("profile_id", request.execution_profile_ref),
                            "cooldown_seconds": kw.get("cooldown_seconds", 300),
                        })

                    async def _run_launcher(**kw):
                        return await workflow.execute_activity(
                            "agent_runtime.launch",
                            kw.get("payload", {}),
                            task_queue=AGENT_RUNTIME_TASK_QUEUE,
                            start_to_close_timeout=timedelta(seconds=30),
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                    store_root = os.path.join(
                        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
                        "managed_runs",
                    )
                    run_store = ManagedRunStore(store_root)

                    adapter: AgentAdapter = ManagedAgentAdapter(
                        profile_fetcher=_profile_fetcher,
                        slot_requester=_slot_requester,
                        slot_releaser=_slot_releaser,
                        cooldown_reporter=_cooldown_reporter,
                        workflow_id=wf_id,
                        runtime_id=runtime_id,
                        run_store=run_store,
                        run_launcher=_run_launcher,
                    )

                    # --- Managed agent: launch via adapter ---
                    handle = await adapter.start(request)
                    self.run_id = handle.run_id
                    self.run_status = handle.status
                    poll_interval = handle.poll_hint_seconds or 10

                elif request.agent_kind == "external":
                    # Validate adapter availability in an activity (deterministic-safe).
                    validated_id = await workflow.execute_activity(
                        resolve_external_adapter,
                        request.agent_id,
                        task_queue=WORKFLOW_TASK_QUEUE,
                        start_to_close_timeout=timedelta(seconds=30),
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    )
                    # Store the validated agent_id for activity routing.
                    self._external_agent_id = validated_id

                    execution_style = await workflow.execute_activity(
                        external_adapter_execution_style,
                        validated_id,
                        task_queue=WORKFLOW_TASK_QUEUE,
                        start_to_close_timeout=timedelta(seconds=30),
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    )

                    if execution_style == "streaming_gateway":
                        stc_seconds = min(
                            max(int(timeout_seconds), 60),
                            86400,
                        )
                        result_payload = await workflow.execute_activity(
                            "integration.openclaw.execute",
                            request,
                            task_queue=INTEGRATIONS_TASK_QUEUE,
                            start_to_close_timeout=timedelta(seconds=stc_seconds),
                            heartbeat_timeout=STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )
                        self.final_result = (
                            AgentRunResult(**result_payload)
                            if isinstance(result_payload, dict)
                            else result_payload
                        )
                        self.run_status = RunStatus.completed
                        adapter = None
                        skip_poll_and_fetch = True
                    else:
                        # Start via Temporal activity on the integrations fleet
                        # (determinism-safe: no adapter construction in-workflow).
                        handle_dict = await workflow.execute_activity(
                            f"integration.{validated_id}.start",
                            request,
                            task_queue=INTEGRATIONS_TASK_QUEUE,
                            start_to_close_timeout=INTEGRATIONS_ACTIVITY_TIMEOUT,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                        if isinstance(handle_dict, dict) and "external_id" in handle_dict:
                            status = handle_dict.get("normalized_status", "unknown")
                            if status not in {"queued", "launching", "running", "awaiting_callback", "awaiting_approval", "intervention_requested", "collecting_results", "completed", "failed", "cancelled", "timed_out"}:
                                status = "running"
                            handle = AgentRunHandle(
                                runId=handle_dict["external_id"],
                                agentKind="external",
                                agentId=validated_id,
                                status=status,
                                startedAt=workflow.now(),
                                metadata={
                                    "providerStatus": handle_dict.get("provider_status", "unknown"),
                                    "normalizedStatus": status,
                                    "externalUrl": handle_dict.get("url"),
                                    "callbackSupported": handle_dict.get("callback_supported", False),
                                }
                            )
                        else:
                            handle = AgentRunHandle(**handle_dict) if isinstance(handle_dict, dict) else handle_dict

                        self.run_id = handle.run_id
                        self.run_status = handle.status
                        poll_interval = handle.poll_hint_seconds or 10
                        adapter = None  # External ops route through activities, not adapter
                else:
                    raise ValueError(f"Unknown agent kind: {request.agent_kind}")

                # Wait for completion checking periodically
                if not skip_poll_and_fetch:
                    while True:
                        remaining_timeout = timeout_seconds - (workflow.now() - overall_start).total_seconds()
                        if remaining_timeout <= 0:
                            break

                        try:
                            # Add bounded wait
                            await workflow.wait_condition(
                                lambda: self.completion_event.is_set(),
                                timeout=timedelta(seconds=min(poll_interval, remaining_timeout))
                            )
                            break  # Callback received
                        except asyncio.TimeoutError:
                            if request.agent_kind == "external":
                                # Poll via Temporal activity (determinism-safe).
                                status_dict = await workflow.execute_activity(
                                    f"integration.{self._external_agent_id}.status",
                                    self.run_id,
                                    task_queue=INTEGRATIONS_TASK_QUEUE,
                                    start_to_close_timeout=INTEGRATIONS_STATUS_TIMEOUT,
                                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                )
                                status_obj = AgentRunStatusModel(**status_dict) if isinstance(status_dict, dict) else status_dict
                            else:
                                # Managed agent: poll via adapter directly.
                                status_obj = await adapter.status(self.run_id)
                                workflow.logger.info(f"STATUS_OBJ for {self.run_id}: {status_obj}")

                            self.run_status = status_obj.status
                            if status_obj.status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled):
                                break

                elapsed = (workflow.now() - overall_start).total_seconds()

                if elapsed >= timeout_seconds and not self.completion_event.is_set():
                    self.run_status = RunStatus.timed_out
                    if manager_handle and request.execution_profile_ref:
                        await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                    return AgentRunResult(failure_class="execution_error")

                if self.final_result is None:
                    if request.agent_kind == "external":
                        # Fetch result via Temporal activity.
                        result_dict = await workflow.execute_activity(
                            f"integration.{self._external_agent_id}.fetch_result",
                            self.run_id,
                            task_queue=INTEGRATIONS_TASK_QUEUE,
                            start_to_close_timeout=INTEGRATIONS_ACTIVITY_TIMEOUT,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )
                        self.final_result = AgentRunResult(**result_dict) if isinstance(result_dict, dict) else result_dict
                    else:
                        # Managed agent: fetch via adapter.
                        self.final_result = await adapter.fetch_result(self.run_id)

                # Check for 429
                if request.agent_kind == "managed" and manager_handle and self.final_result.provider_error_code == PROVIDER_RATE_LIMIT_ERROR_CODE:
                    await manager_handle.signal("report_cooldown", {"profile_id": request.execution_profile_ref, "cooldown_seconds": 300})
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                    self.completion_event.clear()
                    self.final_result = None
                    continue # Retries loop

                # Not a 429 or external agent
                if manager_handle and request.execution_profile_ref:
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})

                # Post-run artifact publishing via the agent_runtime activity fleet.
                enriched_result = await workflow.execute_activity(
                    "agent_runtime.publish_artifacts",
                    self.final_result.model_dump(mode="json", by_alias=True) if hasattr(self.final_result, "model_dump") else self.final_result,
                    task_queue=AGENT_RUNTIME_TASK_QUEUE,
                    start_to_close_timeout=AGENT_RUNTIME_ACTIVITY_TIMEOUT,
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )

                if isinstance(enriched_result, dict):
                    # Handle duplicate aliases from older history events
                    if "diagnosticsRef" in enriched_result and "diagnostics_ref" in enriched_result:
                        del enriched_result["diagnostics_ref"]
                    self.final_result = AgentRunResult(**enriched_result)
                return self.final_result

        except asyncio.TimeoutError:
            self.run_status = RunStatus.timed_out
            if request.agent_kind == "managed" and hasattr(request, "execution_profile_ref") and request.execution_profile_ref:
                try:
                    runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
                    runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                    manager_id = f"auth-profile-manager:{runtime_id}"
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                except Exception:
                    workflow.logger.warning("Failed to release slot on timeout, which may lead to a leak.", exc_info=True)
            return AgentRunResult(failure_class="execution_error")

        except CancelledError:
            if request.agent_kind == "managed" and getattr(request, "execution_profile_ref", None):
                runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
                runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                manager_id = f"auth-profile-manager:{runtime_id}"
                try:
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                except Exception:
                    # Errors are intentionally ignored to avoid masking the original cancellation
                    workflow.logger.warning("Failed to release slot on cancellation, which may lead to a leak.", exc_info=True)

            if self.run_id is not None and self.agent_kind is not None:
                try:
                    if self.agent_kind == "external" and self._external_agent_id is not None:
                        # Route external cancel through integration activity.
                        await workflow.execute_activity(
                            f"integration.{self._external_agent_id}.cancel",
                            self.run_id,
                            task_queue=INTEGRATIONS_TASK_QUEUE,
                            start_to_close_timeout=AGENT_RUNTIME_CANCEL_TIMEOUT,
                        )
                    else:
                        await workflow.execute_activity(
                            "agent_runtime.cancel",
                            {"agent_kind": self.agent_kind, "run_id": self.run_id},
                            task_queue=AGENT_RUNTIME_TASK_QUEUE,
                            start_to_close_timeout=AGENT_RUNTIME_CANCEL_TIMEOUT,
                        )
                except Exception:
                    workflow.logger.warning("Failed to cancel agent runtime on cancellation.", exc_info=True)
            raise
            
        except Exception:
            if request.agent_kind == "managed" and getattr(request, "execution_profile_ref", None):
                runtime_mapping = {"gemini_cli": "gemini_cli", "claude": "claude_code", "codex": "codex_cli"}
                runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                manager_id = f"auth-profile-manager:{runtime_id}"
                try:
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                except Exception:
                    workflow.logger.warning("Failed to release slot on unexpected exception, which may lead to a leak.", exc_info=True)
            raise
