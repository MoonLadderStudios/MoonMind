from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Optional

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import (
        INTEGRATIONS_TASK_QUEUE,
        LLM_TASK_QUEUE,
        SANDBOX_TASK_QUEUE,
    )


WORKFLOW_NAME = "MoonMind.Run"
STATE_INITIALIZING = "initializing"
STATE_PLANNING = "planning"
STATE_EXECUTING = "executing"
STATE_AWAITING_EXTERNAL = "awaiting_external"
STATE_FINALIZING = "finalizing"
STATE_SUCCEEDED = "succeeded"
STATE_CANCELED = "canceled"
CLOSE_STATUS_COMPLETED = "completed"
CLOSE_STATUS_CANCELED = "canceled"
OWNER_ID_SEARCH_ATTRIBUTE = "mm_owner_id"
OWNER_TYPE_SEARCH_ATTRIBUTE = "mm_owner_type"


@workflow.defn(name="MoonMind.Run")
class MoonMindRunWorkflow:
    def __init__(self) -> None:
        self._state = STATE_INITIALIZING
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
        self._updated_parameters: dict[str, Any] = {}

        # Internal state
        self._wait_cycle_count = 0
        self._step_count = 0
        self._max_wait_cycles = 100
        self._max_steps = 100

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        workflow_type, parameters, input_ref, plan_ref = self._initialize_from_payload(
            input_payload
        )
        workflow.logger.info(
            "Starting MoonMind.Run workflow",
            extra={"workflow_type": workflow_type},
        )

        self._set_state(STATE_INITIALIZING)
        self._set_state(STATE_PLANNING)

        # Pause until unpaused
        await workflow.wait_condition(lambda: not self._paused)
        if self._cancel_requested:
            return {"status": "canceled"}

        resolved_plan_ref = await self._run_planning_stage(
            parameters=parameters,
            input_ref=input_ref,
            plan_ref=plan_ref,
        )
        await self._run_execution_stage(
            parameters=parameters,
            plan_ref=resolved_plan_ref,
        )

        if self._cancel_requested:
            return {"status": "canceled"}

        self._set_state(STATE_FINALIZING)

        self._close_status = CLOSE_STATUS_COMPLETED
        self._set_state(STATE_SUCCEEDED)

        return {"status": "success", "message": "Workflow completed successfully"}

    def _initialize_from_payload(
        self, input_payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any], Optional[str], Optional[str]]:
        if not isinstance(input_payload, dict):
            raise ValueError("input_payload must be a dictionary")

        workflow_type = self._required_string(
            input_payload,
            "workflowType",
            "workflow_type",
            error_message="workflowType is required",
        )
        if workflow_type != WORKFLOW_NAME:
            raise ValueError(f"workflowType must be {WORKFLOW_NAME}")

        self._workflow_type = workflow_type
        self._entry = "run"
        self._title = workflow.memo().get("title") or self._optional_string(
            input_payload,
            "title",
        )
        self._owner_type, self._owner_id = self._trusted_owner_metadata()

        parameters = self._mapping_value(
            input_payload,
            "initialParameters",
            "initial_parameters",
        )
        self._repo = self._string_from_mapping(parameters, "repo")
        self._integration = self._string_from_mapping(parameters, "integration")

        input_ref = self._optional_string(
            input_payload,
            "inputArtifactRef",
            "input_artifact_ref",
        )
        plan_ref = self._optional_string(
            input_payload,
            "planArtifactRef",
            "plan_artifact_ref",
        )
        return workflow_type, parameters, input_ref, plan_ref

    async def _run_planning_stage(
        self,
        *,
        parameters: dict[str, Any],
        input_ref: Optional[str],
        plan_ref: Optional[str],
    ) -> Optional[str]:
        if plan_ref:
            return plan_ref

        plan_result = await workflow.execute_activity(
            "plan.generate",
            {
                "principal": self._principal(),
                "inputs_ref": input_ref,
                "parameters": parameters,
                "execution_ref": {
                    "workflow_id": workflow.info().workflow_id,
                    "run_id": workflow.info().run_id,
                },
            },
            start_to_close_timeout=timedelta(minutes=15),
            task_queue=LLM_TASK_QUEUE,
        )
        return (
            plan_result.get("plan_ref")
            if isinstance(plan_result, dict)
            else getattr(plan_result, "plan_ref", None)
        )

    async def _run_execution_stage(
        self, *, parameters: dict[str, Any], plan_ref: Optional[str]
    ) -> None:
        self._set_state(STATE_EXECUTING)
        self._step_count += 1

        await workflow.execute_activity(
            "sandbox.command",
            {
                "principal": self._principal(),
                "command": "echo executing",
                "timeout_seconds": 300,
            },
            start_to_close_timeout=timedelta(minutes=10),
            task_queue=SANDBOX_TASK_QUEUE,
        )

        if self._integration:
            await self._run_integration_stage(parameters=parameters, plan_ref=plan_ref)

    async def _run_integration_stage(
        self, *, parameters: dict[str, Any], plan_ref: Optional[str]
    ) -> None:
        self._awaiting_external = True
        self._set_state(STATE_AWAITING_EXTERNAL)

        await workflow.execute_activity(
            "integration.start",
            {
                "principal": self._principal(),
                "integration_name": self._integration,
                "repo": self._repo,
                "plan_ref": plan_ref,
                "parameters": parameters,
            },
            start_to_close_timeout=timedelta(minutes=5),
            task_queue=INTEGRATIONS_TASK_QUEUE,
        )

        self._wait_cycle_count += 1
        await workflow.wait_condition(
            lambda: self._resume_requested or self._cancel_requested
        )
        self._resume_requested = False
        self._awaiting_external = False

    def _set_state(self, state: str) -> None:
        self._state = state
        self._update_search_attributes()

    def _principal(self) -> str:
        if not self._owner_id:
            raise ValueError("Trusted owner metadata is required")
        return self._owner_id

    def _trusted_owner_metadata(self) -> tuple[str, str]:
        search_attributes = workflow.info().search_attributes
        owner_type = self._search_attribute_value(
            search_attributes, OWNER_TYPE_SEARCH_ATTRIBUTE
        )
        owner_id = self._search_attribute_value(
            search_attributes, OWNER_ID_SEARCH_ATTRIBUTE
        )
        if not owner_type or not owner_id:
            raise ValueError(
                "Trusted owner metadata is required in Temporal search attributes"
            )
        return owner_type, owner_id

    def _search_attribute_value(
        self, search_attributes: Mapping[str, list[Any]], key: str
    ) -> Optional[str]:
        values = search_attributes.get(key) or []
        if not values:
            return None
        value = values[0]
        return value if isinstance(value, str) and value else None

    def _required_string(
        self,
        payload: Mapping[str, Any],
        *keys: str,
        error_message: str,
    ) -> str:
        value = self._optional_string(payload, *keys)
        if value is None:
            raise ValueError(error_message)
        return value

    def _optional_string(self, payload: Mapping[str, Any], *keys: str) -> Optional[str]:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string when provided")
            normalized = value.strip()
            if normalized:
                return normalized
        return None

    def _mapping_value(self, payload: Mapping[str, Any], *keys: str) -> dict[str, Any]:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            if not isinstance(value, Mapping):
                raise ValueError(f"{key} must be an object when provided")
            return self._json_mapping(value, path=key)
        return {}

    def _json_mapping(self, value: Mapping[str, Any], *, path: str) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            normalized[key] = self._json_value(item, path=f"{path}.{key}")
        return normalized

    def _json_value(self, value: Any, *, path: str) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Mapping):
            return self._json_mapping(value, path=path)
        if isinstance(value, list):
            return [self._json_value(item, path=f"{path}[]") for item in value]
        raise ValueError(f"{path} must contain only JSON-compatible values")

    def _string_from_mapping(
        self, payload: Mapping[str, Any], key: str
    ) -> Optional[str]:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string when provided")
        normalized = value.strip()
        return normalized or None

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
        except Exception as exc:
            # During basic tests search attributes might not be registered
            workflow.logger.warning(
                "Failed to upsert search attributes",
                extra={"error": str(exc)},
            )

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
        self._close_status = CLOSE_STATUS_CANCELED
        self._state = STATE_CANCELED
        self._update_search_attributes()

    @workflow.update
    def update_title(self, new_title: str) -> None:
        self._title = new_title

    @workflow.update
    def update_parameters(self, new_parameters: dict[str, Any]) -> None:
        self._parameters_updated = True
        self._updated_parameters = new_parameters
