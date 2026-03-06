import sys
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import logging

@workflow.defn(name="MoonMind.Run")
class MoonMindRunWorkflow:
    def __init__(self) -> None:
        self._state = "initializing"
        self._owner_type: Optional[str] = None
        self._owner_id: Optional[str] = None
        self._workflow_type: Optional[str] = None
        self._entry: Optional[str] = None
        self._repo: Optional[str] = None
        self._integration: Optional[str] = None
        self._close_status: Optional[str] = None
        self._title: Optional[str] = None

        # State tracking
        self._paused: bool = False
        self._awaiting_external: bool = False
        self._waiting_reason: Optional[str] = None
        self._attention_required: bool = False

        # Action flags
        self._cancel_requested = False
        self._approve_requested = False
        self._resume_requested = False
        self._parameters_updated = False
        self._updated_parameters: Dict[str, Any] = {}

        # Internal state
        self._wait_cycle_count = 0
        self._step_count = 0
        self._max_wait_cycles = 100
        self._max_steps = 100

    @workflow.run
    async def run(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        workflow.logger.info("Starting MoonMind.Run workflow", extra={"input_payload": input_payload})

        # Basic input validation and initialization
        if not isinstance(input_payload, dict):
            raise ValueError("input_payload must be a dictionary")

        workflow_type = input_payload.get("workflowType") or input_payload.get("workflow_type")
        if not workflow_type:
             raise ValueError("workflowType is required")

        self._workflow_type = workflow_type
        self._entry = "run"
        self._title = input_payload.get("title")
        self._owner_id = input_payload.get("ownerId") or input_payload.get("owner_id")
        self._owner_type = input_payload.get("ownerType") or input_payload.get("owner_type")

        parameters = input_payload.get("initialParameters") or input_payload.get("initial_parameters") or {}
        self._repo = parameters.get("repo")
        self._integration = parameters.get("integration")

        input_ref = input_payload.get("inputArtifactRef") or input_payload.get("input_artifact_ref")
        plan_ref = input_payload.get("planArtifactRef") or input_payload.get("plan_artifact_ref")

        self._state = "initializing"
        self._update_search_attributes()

        self._state = "planning"
        self._update_search_attributes()

        # Pause until unpaused
        await workflow.wait_condition(lambda: not self._paused)
        if self._cancel_requested:
            return {"status": "canceled"}

        # Simulate executing the plan generation
        if not plan_ref:
            plan_result = await workflow.execute_activity(
                "plan.generate",
                {
                    "principal": self._owner_id or "system",
                    "inputs_ref": input_ref,
                    "parameters": parameters,
                    "execution_ref": {"workflow_id": workflow.info().workflow_id, "run_id": workflow.info().run_id}
                },
                start_to_close_timeout=timedelta(minutes=15),
                task_queue="mm-llm"
            )
            plan_ref = plan_result.get("plan_ref") if isinstance(plan_result, dict) else getattr(plan_result, "plan_ref", None)

        self._state = "executing"
        self._update_search_attributes()
        self._step_count += 1

        # Execute sandbox action
        sandbox_result = await workflow.execute_activity(
            "sandbox.command",
            {
                "principal": self._owner_id or "system",
                "command": "echo executing",
                "timeout_seconds": 300,
            },
            start_to_close_timeout=timedelta(minutes=10),
            task_queue="mm-sandbox"
        )

        if self._integration:
            self._state = "awaiting_external"
            self._awaiting_external = True
            self._update_search_attributes()

            integration_start = await workflow.execute_activity(
                "integration.start",
                {
                    "principal": self._owner_id or "system",
                    "integration_name": self._integration,
                    "repo": self._repo,
                    "plan_ref": plan_ref,
                    "parameters": parameters,
                },
                start_to_close_timeout=timedelta(minutes=5),
                task_queue="mm-integrations"
            )

            # Simulate a wait cycle loop
            self._wait_cycle_count += 1
            await workflow.wait_condition(lambda: self._resume_requested or self._cancel_requested)
            self._resume_requested = False
            self._awaiting_external = False

        if self._cancel_requested:
            return {"status": "canceled"}

        self._state = "finalizing"
        self._update_search_attributes()

        self._state = "succeeded"
        self._close_status = "completed"
        self._update_search_attributes()

        return {
            "status": "success",
            "message": "Workflow completed successfully"
        }

    def _update_search_attributes(self) -> None:
        attributes: dict[str, Any] = {
            "mm_state": self._state,
            "mm_entry": self._entry,
        }
        if self._owner_type:
            attributes["mm_owner_type"] = self._owner_type
        if self._owner_id:
            attributes["mm_owner_id"] = self._owner_id
        if self._repo:
            attributes["mm_repo"] = self._repo
        if self._integration:
            attributes["mm_integration"] = self._integration

        try:
            workflow.upsert_search_attributes(attributes)
        except Exception:
            # During basic tests search attributes might not be registered
            pass

    @workflow.signal
    def pause(self) -> None:
        self._paused = True
        self._waiting_reason = "Paused by user"
        self._update_search_attributes()

    @workflow.signal
    def resume(self) -> None:
        self._paused = False
        self._waiting_reason = None

        # Only set resume_requested if we are actually waiting for an external integration
        if self._awaiting_external:
            self._resume_requested = True

        self._update_search_attributes()

    @workflow.signal
    def approve(self) -> None:
        self._approve_requested = True
        if self._awaiting_external:
            self._resume_requested = True

    @workflow.signal
    def cancel(self, reason: Optional[str] = None) -> None:
        self._cancel_requested = True
        self._state = "canceled"
        self._close_status = "canceled"
        self._update_search_attributes()

    @workflow.update
    def update_title(self, new_title: str) -> None:
        self._title = new_title

    @workflow.update
    def update_parameters(self, new_parameters: Dict[str, Any]) -> None:
        self._parameters_updated = True
        self._updated_parameters = new_parameters
