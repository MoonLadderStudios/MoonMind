import asyncio
import logging
from datetime import timedelta
from temporalio import workflow, activity
from temporalio.exceptions import CancelledError

with workflow.unsafe.imports_passed_through():
    from .shared import AgentExecutionRequest, AgentRunResult, AgentRunStatus
    from ..adapters.base import AgentAdapter
    from ..adapters.managed import ManagedAgentAdapter
    from ..adapters.external import ExternalAgentAdapter

PROVIDER_RATE_LIMIT_ERROR_CODE = "429"

# Note: In a real temporal app, adapters might be activities or standard classes
# accessed via DI. We simulate them here based on the request.

@activity.defn
async def publish_artifacts_activity(result: AgentRunResult) -> AgentRunResult:
    # Stub for publishing outputs back to artifact storage
    return result

@activity.defn
async def invoke_adapter_cancel(agent_kind: str, run_id: str) -> None:
    if agent_kind == "managed":
        adapter = ManagedAgentAdapter()
    elif agent_kind == "external":
        adapter = ExternalAgentAdapter()
    else:
        return
    await adapter.cancel(run_id)

@workflow.defn
class MoonMindAgentRun:
    def __init__(self):
        self.completion_event = asyncio.Event()
        self.slot_assigned_event = asyncio.Event()
        self.run_status = AgentRunStatus.queued
        self.final_result: AgentRunResult | None = None
        self.run_id: str | None = None
        self.agent_kind: str | None = None
        self._assigned_profile_id: str | None = None

    @workflow.signal
    def completion_signal(self, result_dict: dict) -> None:
        self.final_result = AgentRunResult(**result_dict)
        self.run_status = AgentRunStatus.completed
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
                elapsed = (workflow.now() - overall_start).total_seconds()
                if elapsed >= timeout_seconds:
                    self.run_status = AgentRunStatus.timed_out
                    return AgentRunResult(failure_class="Timeout")

                manager_handle = None
                # Acquire auth slot if managed
                if request.agent_kind == "managed":
                    manager_id = f"auth-profile-manager:{request.agent_id}"
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    
                    self.slot_assigned_event.clear()
                    await manager_handle.signal(
                        "request_slot", 
                        {"requester_workflow_id": workflow.info().workflow_id, "runtime_id": request.agent_id}
                    )
                    
                    # Wait for assigned slot
                    await workflow.wait_condition(lambda: self.slot_assigned_event.is_set())
                    request.execution_profile_ref = self._assigned_profile_id

                    adapter: AgentAdapter = ManagedAgentAdapter()
                elif request.agent_kind == "external":
                    adapter: AgentAdapter = ExternalAgentAdapter()
                else:
                    raise ValueError(f"Unknown agent kind: {request.agent_kind}")

                # Launch adapter
                handle = await adapter.start(request)
                self.run_id = handle.run_id
                self.run_status = handle.status

                poll_interval = handle.poll_hint_seconds or 10

                # Wait for completion checking periodically
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
                        current_status = await adapter.status(self.run_id)
                        self.run_status = current_status
                        if current_status in (AgentRunStatus.completed, AgentRunStatus.failed, AgentRunStatus.cancelled):
                            break

                elapsed = (workflow.now() - overall_start).total_seconds()

                if elapsed >= timeout_seconds and not self.completion_event.is_set():
                    self.run_status = AgentRunStatus.timed_out
                    if manager_handle and request.execution_profile_ref:
                        await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                    return AgentRunResult(failure_class="Timeout")

                if self.final_result is None:
                    # Fallback to fetching
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

                # Post-run artifact publishing logic
                await workflow.execute_activity(
                    publish_artifacts_activity,
                    self.final_result,
                    start_to_close_timeout=timedelta(minutes=5)
                )

                return self.final_result

        except asyncio.TimeoutError:
            self.run_status = AgentRunStatus.timed_out
            if request.agent_kind == "managed" and hasattr(request, "execution_profile_ref") and request.execution_profile_ref:
                try:
                    manager_id = f"auth-profile-manager:{request.agent_id}"
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                except Exception:
                    workflow.logger.warning("Failed to release slot on timeout, which may lead to a leak.", exc_info=True)
            return AgentRunResult(failure_class="Timeout")

        except CancelledError:
            if request.agent_kind == "managed" and getattr(request, "execution_profile_ref", None):
                manager_id = f"auth-profile-manager:{request.agent_id}"
                try:
                    with workflow.execute_in_background_with_shield():
                        manager_handle = workflow.get_external_workflow_handle(manager_id)
                        await manager_handle.signal("release_slot", {"requester_workflow_id": workflow.info().workflow_id, "profile_id": request.execution_profile_ref})
                except Exception:
                    # Errors are intentionally ignored to avoid masking the original cancellation
                    logging.exception("Failed to release slot on cancellation, which may lead to a leak.")

            if self.run_id is not None and self.agent_kind is not None:
                with workflow.execute_in_background_with_shield():
                    await workflow.execute_activity(
                        invoke_adapter_cancel,
                        args=[self.agent_kind, self.run_id],
                        start_to_close_timeout=timedelta(minutes=1)
                    )
            raise
