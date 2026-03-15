import asyncio
from datetime import timedelta
from temporalio import workflow, activity
from temporalio.exceptions import CancelledError

with workflow.unsafe.imports_passed_through():
    from .shared import AgentExecutionRequest, AgentRunResult, AgentRunStatus
    from ..adapters.base import AgentAdapter
    from ..adapters.managed import ManagedAgentAdapter
    from ..adapters.external import ExternalAgentAdapter

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
        self.run_status = AgentRunStatus.queued
        self.final_result: AgentRunResult | None = None
        self.run_id: str | None = None
        self.agent_kind: str | None = None

    @workflow.signal
    def completion_signal(self, result_dict: dict) -> None:
        self.final_result = AgentRunResult(**result_dict)
        self.run_status = AgentRunStatus.completed
        self.completion_event.set()

    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        self.agent_kind = request.agent_kind

        # T009: Adapter routing logic
        if request.agent_kind == "managed":
            adapter: AgentAdapter = ManagedAgentAdapter()
        elif request.agent_kind == "external":
            adapter: AgentAdapter = ExternalAgentAdapter()
        else:
            raise ValueError(f"Unknown agent kind: {request.agent_kind}")

        handle = await adapter.start(request)
        self.run_id = handle.run_id
        self.run_status = handle.status

        # Extract timeout
        timeout_seconds = 3600
        if request.timeout_policy and "timeout_seconds" in request.timeout_policy:
            timeout_seconds = request.timeout_policy["timeout_seconds"]

        # T015: Wrap wait phase in try...except CancelledError
        try:
            # T010: Wait phase loop with timeout
            # T011: Timeout handling
            poll_interval = handle.poll_hint_seconds or 10
            elapsed = 0

            while elapsed < timeout_seconds:
                try:
                    await asyncio.wait_for(self.completion_event.wait(), timeout=poll_interval)
                    break  # Callback received
                except asyncio.TimeoutError:
                    # Bounded status polling fallback
                    elapsed += poll_interval
                    current_status = adapter.status(self.run_id)
                    self.run_status = current_status
                    if current_status in (AgentRunStatus.completed, AgentRunStatus.failed, AgentRunStatus.cancelled):
                        break

            if elapsed >= timeout_seconds and not self.completion_event.is_set():
                self.run_status = AgentRunStatus.timed_out
                return AgentRunResult(failure_class="Timeout")

            if self.final_result is None:
                # Fallback to fetching
                self.final_result = adapter.fetch_result(self.run_id)

            # T012: Post-run artifact publishing logic
            await workflow.execute_activity(
                publish_artifacts_activity,
                self.final_result,
                start_to_close_timeout=timedelta(minutes=5)
            )

            # T013: Return normalized AgentRunResult
            return self.final_result

        except asyncio.TimeoutError:
            self.run_status = AgentRunStatus.timed_out
            return AgentRunResult(failure_class="Timeout")

        except CancelledError:
            # T016: Non-cancellable scope to call adapter's cancel
            with workflow.execute_in_background_with_shield():
                await workflow.execute_activity(
                    invoke_adapter_cancel,
                    args=[self.agent_kind, self.run_id],
                    start_to_close_timeout=timedelta(minutes=1)
                )
            raise
