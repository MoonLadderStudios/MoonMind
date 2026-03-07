import sys
import json
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.common import SearchAttributeKey

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

    @workflow.run
    async def run(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        workflow.logger.info("Starting MoonMind.Run workflow", extra={"input_payload": input_payload})

        info = workflow.info()

        mm_owner_id = None
        mm_owner_type = None

        # We don't try to access search_attributes.get if it's going to fail without definitions.
        # Fall back gracefully for tests if they just aren't populated.
        if info.search_attributes:
            try:
                if hasattr(info.search_attributes, "get") and not hasattr(info.search_attributes, "search_attributes"):
                    # Deprecated dict style
                    mm_owner_id = info.search_attributes.get("mm_owner_id")
                    mm_owner_type = info.search_attributes.get("mm_owner_type")
                else:
                    # TypedSearchAttributes
                    mm_owner_id = info.search_attributes.get(SearchAttributeKey.for_keyword("mm_owner_id"))
                    mm_owner_type = info.search_attributes.get(SearchAttributeKey.for_keyword("mm_owner_type"))
            except Exception:
                pass

        if not isinstance(input_payload, dict):
            raise ApplicationError("input_payload must be a dictionary", non_retryable=True)

        fallback_owner_id = input_payload.get("ownerId") or input_payload.get("owner_id")
        fallback_owner_type = input_payload.get("ownerType") or input_payload.get("owner_type")

        if not mm_owner_id and not fallback_owner_id:
            raise ApplicationError("Trusted owner metadata is required in Temporal search attributes", non_retryable=True)

        workflow_type = input_payload.get("workflowType") or input_payload.get("workflow_type")
        if not workflow_type:
             raise ApplicationError("workflowType is required", non_retryable=True)

        self._workflow_type = workflow_type
        self._entry = "run"
        self._title = input_payload.get("title")
        self._owner_id = str(mm_owner_id[0]) if isinstance(mm_owner_id, list) else (str(mm_owner_id) if mm_owner_id else fallback_owner_id)
        self._owner_type = str(mm_owner_type[0]) if isinstance(mm_owner_type, list) and mm_owner_type else (str(mm_owner_type) if mm_owner_type else fallback_owner_type)

        parameters = input_payload.get("initialParameters") or input_payload.get("initial_parameters") or {}
        self._repo = parameters.get("repo")
        self._integration = parameters.get("integration")

        input_ref = input_payload.get("inputArtifactRef") or input_payload.get("input_artifact_ref")
        plan_ref = input_payload.get("planArtifactRef") or input_payload.get("plan_artifact_ref")

        self._state = "initializing"
        self._update_search_attributes()

        self._state = "planning"
        self._update_search_attributes()

        await self._wait_if_paused()
        if self._cancel_requested:
            return {"status": "canceled"}

        # Real plan generation if no plan provided
        if not plan_ref:
            plan_result = await workflow.execute_activity(
                "plan.generate",
                {
                    "principal": self._owner_id,
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

        await self._wait_if_paused()
        if self._cancel_requested:
            return {"status": "canceled"}

        # Parse the plan to dynamically execute its tasks
        # artifact.read returns bytes, so we'll parse it as JSON
        plan_payload_bytes = await workflow.execute_activity(
            "artifact.read",
            {
                "artifact_ref": {"artifact_id": plan_ref.replace("artifact://", "")} if isinstance(plan_ref, str) and plan_ref.startswith("artifact://") else plan_ref,
                "principal": self._owner_id
            },
            start_to_close_timeout=timedelta(minutes=5),
            task_queue="mm-artifacts"
        )

        try:
            plan_data = json.loads(plan_payload_bytes.decode("utf-8"))
        except Exception as e:
            workflow.logger.warning(f"Failed to parse plan payload: {e}")
            plan_data = {"steps": []}

        steps = plan_data.get("steps", [])

        for step in steps:
            self._step_count += 1

            step_type = step.get("type")
            step_payload = step.get("payload", {})

            if step_type == "sandbox.run_command":
                await workflow.execute_activity(
                    "sandbox.run_command",
                    {
                        "principal": self._owner_id,
                        "command": step_payload.get("command"),
                        "timeout_seconds": step_payload.get("timeout_seconds", 300),
                    },
                    start_to_close_timeout=timedelta(minutes=10),
                    task_queue="mm-sandbox"
                )
            elif step_type == "mm.skill.execute":
                await workflow.execute_activity(
                    "mm.skill.execute",
                    {
                        "principal": self._owner_id,
                        "invocation_payload": step_payload.get("invocation_payload", {}),
                        "registry_snapshot_ref": input_ref or "fallback_ref"
                    },
                    start_to_close_timeout=timedelta(minutes=10),
                    task_queue="mm-skills"
                )
            else:
                workflow.logger.warning(f"Unknown step type: {step_type}")

            await self._wait_if_paused()
            if self._cancel_requested:
                return {"status": "canceled"}

        # Await External Integration if configured
        if self._integration:
            self._state = "awaiting_external"
            self._awaiting_external = True
            self._update_search_attributes()

            # Start integration via activity
            integration_start = await workflow.execute_activity(
                "integration.jules.start",
                {
                    "principal": self._owner_id,
                    "integration_name": self._integration,
                    "repo": self._repo,
                    "plan_ref": plan_ref,
                    "parameters": parameters,
                },
                start_to_close_timeout=timedelta(minutes=5),
                task_queue="mm-integrations"
            )

            # Wait cycle loop (e.g., waiting for external webhook via resume signal or polling logic)
            self._wait_cycle_count += 1
            await workflow.wait_condition(lambda: self._resume_requested or self._cancel_requested)
            self._resume_requested = False
            self._awaiting_external = False

        await self._wait_if_paused()
        if self._cancel_requested:
            return {"status": "canceled"}

        self._state = "finalizing"
        self._update_search_attributes()

        self._state = "succeeded"
        self._close_status = "completed"
        self._update_search_attributes()

        return {
            "status": "success",
            "message": "Workflow completed successfully",
            "plan_ref": plan_ref,
            "steps_executed": self._step_count
        }

    async def _wait_if_paused(self) -> None:
        if self._paused:
            await workflow.wait_condition(lambda: not self._paused or self._cancel_requested)

    def _update_search_attributes(self) -> None:
        updates = [
            SearchAttributeKey.for_keyword("mm_state").value_set(self._state),
            SearchAttributeKey.for_keyword("mm_entry").value_set(self._entry),
        ]
        if self._owner_type:
            updates.append(SearchAttributeKey.for_keyword("mm_owner_type").value_set(self._owner_type))
        if self._owner_id:
            updates.append(SearchAttributeKey.for_keyword("mm_owner_id").value_set(self._owner_id))
        if self._repo:
            updates.append(SearchAttributeKey.for_keyword("mm_repo").value_set(self._repo))
        if self._integration:
            updates.append(SearchAttributeKey.for_keyword("mm_integration").value_set(self._integration))

        try:
            workflow.upsert_search_attributes(updates)
        except Exception:
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
        self._resume_requested = True
        self._update_search_attributes()

    @workflow.signal
    def approve(self) -> None:
        self._approve_requested = True
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
