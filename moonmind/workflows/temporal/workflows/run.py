import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Optional, TypedDict

from temporalio import workflow
from temporalio.common import RetryPolicy

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)

INTEGRATIONS_TASK_QUEUE = "mm.activity.integrations"
LLM_TASK_QUEUE = "mm.activity.llm"
SANDBOX_TASK_QUEUE = "mm.activity.sandbox"


class RunWorkflowInput(TypedDict, total=False):
    """Input payload for the MoonMind.Run workflow."""

    workflow_type: str
    title: Optional[str]
    initial_parameters: dict[str, Any]
    input_artifact_ref: Optional[str]
    plan_artifact_ref: Optional[str]


class RunWorkflowOutput(TypedDict):
    status: str
    message: Optional[str]


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
        self._summary: str = "Execution initialized."
        self._correlation_id: Optional[str] = None

        # Artifact refs
        self._input_ref: Optional[str] = None
        self._plan_ref: Optional[str] = None
        self._logs_ref: Optional[str] = None
        self._summary_ref: Optional[str] = None
        self._registry_snapshot_ref: Optional[str] = None

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
    async def run(self, input_payload: RunWorkflowInput) -> RunWorkflowOutput:
        workflow_type, parameters, input_ref, plan_ref, registry_snapshot_ref = self._initialize_from_payload(
            input_payload
        )
        workflow.logger.info(
            "Starting MoonMind.Run workflow",
            extra={"workflow_type": workflow_type},
        )

        self._set_state(STATE_INITIALIZING, summary="Execution initialized.")
        self._set_state(STATE_PLANNING, summary="Planning execution strategy.")

        # Pause until unpaused
        await workflow.wait_condition(lambda: not self._paused)
        if self._cancel_requested:
            return {"status": "canceled"}

        resolved_plan_ref, resolved_registry_snapshot_ref = await self._run_planning_stage(
            parameters=parameters,
            input_ref=input_ref,
            plan_ref=plan_ref,
            registry_snapshot_ref=registry_snapshot_ref,
        )
        await self._run_execution_stage(
            parameters=parameters,
            plan_ref=resolved_plan_ref,
            registry_snapshot_ref=resolved_registry_snapshot_ref,
        )

        if self._cancel_requested:
            return {"status": "canceled"}

        self._set_state(STATE_FINALIZING, summary="Finalizing execution.")

        self._close_status = CLOSE_STATUS_COMPLETED
        self._set_state(STATE_SUCCEEDED, summary="Workflow completed successfully.")

        return {"status": "success", "message": "Workflow completed successfully"}

    def _initialize_from_payload(
        self, input_payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any], Optional[str], Optional[str], Optional[str]]:
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
        self._summary = workflow.memo().get("summary") or "Execution initialized."
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
        registry_snapshot_ref = self._optional_string(
            input_payload,
            "registrySnapshotRef",
            "registry_snapshot_ref",
        )

        if input_ref:
            self._input_ref = input_ref
        if plan_ref:
            self._plan_ref = plan_ref
        if registry_snapshot_ref:
            self._registry_snapshot_ref = registry_snapshot_ref

        return workflow_type, parameters, input_ref, plan_ref, registry_snapshot_ref

    async def _run_planning_stage(
        self,
        *,
        parameters: dict[str, Any],
        input_ref: Optional[str],
        plan_ref: Optional[str],
        registry_snapshot_ref: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        if plan_ref:
            return plan_ref, registry_snapshot_ref

        plan_result = await workflow.execute_activity(
            "plan.generate",
            {
                "principal": self._principal(),
                "inputs_ref": input_ref,
                "parameters": parameters,
                "execution_ref": {
                    "namespace": workflow.info().namespace,
                    "workflow_id": workflow.info().workflow_id,
                    "run_id": workflow.info().run_id,
                    "link_type": "plan",
                },
            },
            start_to_close_timeout=timedelta(minutes=15),
            task_queue=LLM_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        resolved_plan_ref = (
            plan_result.get("plan_ref")
            if isinstance(plan_result, dict)
            else getattr(plan_result, "plan_ref", None)
        )
        resolved_registry_snapshot_ref = (
            plan_result.get("registry_snapshot_ref")
            if isinstance(plan_result, dict)
            else getattr(plan_result, "registry_snapshot_ref", None)
        )

        if resolved_plan_ref:
            self._plan_ref = resolved_plan_ref
        if resolved_registry_snapshot_ref:
            self._registry_snapshot_ref = resolved_registry_snapshot_ref

        if resolved_plan_ref or resolved_registry_snapshot_ref:
            self._update_memo()

        return resolved_plan_ref, resolved_registry_snapshot_ref

    async def _run_execution_stage(
        self, *, parameters: dict[str, Any], plan_ref: Optional[str], registry_snapshot_ref: Optional[str]
    ) -> None:
        self._set_state(STATE_EXECUTING, summary="Executing run steps.")
        self._step_count += 1

        sandbox_result = await workflow.execute_activity(
            "sandbox.run_command",
            {
                "principal": self._principal(),
                "cmd": "echo executing",
                "timeout_seconds": 300,
            },
            start_to_close_timeout=timedelta(minutes=10),
            task_queue=SANDBOX_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )

        logs_ref = (
            sandbox_result.get("diagnostics_ref")
            if isinstance(sandbox_result, dict)
            else getattr(sandbox_result, "diagnostics_ref", None)
        )
        if logs_ref:
            self._logs_ref = logs_ref
            self._update_memo()

        if self._integration:
            await self._run_integration_stage(parameters=parameters, plan_ref=plan_ref)

    def _get_from_result(self, result: Any, key: str) -> Any:
        if isinstance(result, dict):
            return result.get(key)
        return getattr(result, key, None)

    async def _run_integration_stage(
        self, *, parameters: dict[str, Any], plan_ref: Optional[str]
    ) -> None:
        self._awaiting_external = True
        self._set_state(
            STATE_AWAITING_EXTERNAL, summary="Waiting for external integration."
        )

        integration_parameters = dict(parameters)
        integration_parameters.setdefault(
            "title", self._title or "MoonMind Integration"
        )
        integration_parameters.setdefault(
            "description",
            f"Monitor MoonMind.Run workflow for {self._repo or 'the requested task'}.",
        )
        metadata = integration_parameters.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("integration metadata must be an object when provided")
        metadata.setdefault("repo", self._repo)
        metadata.setdefault("planRef", plan_ref)
        integration_parameters["metadata"] = metadata

        start_result = await workflow.execute_activity(
            self._integration_activity_type("start"),
            {
                "principal": self._principal(),
                "parameters": integration_parameters,
            },
            start_to_close_timeout=timedelta(minutes=5),
            task_queue=INTEGRATIONS_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
        )
        summary_ref = self._get_from_result(start_result, "tracking_ref")
        if summary_ref:
            self._summary_ref = summary_ref
            self._update_memo()

        correlation_id = self._get_from_result(start_result, "correlation_id")
        self._correlation_id = correlation_id
        poll_interval_seconds = 5
        max_poll_interval_seconds = 300

        self._waiting_reason = "external_completion"
        self._attention_required = False
        self._update_search_attributes()

        while not self._resume_requested and not self._cancel_requested:
            self._wait_cycle_count += 1
            try:
                await workflow.wait_condition(
                    lambda: self._resume_requested or self._cancel_requested,
                    timeout=timedelta(seconds=poll_interval_seconds),
                )
            except asyncio.TimeoutError:
                # No external signal arrived in this interval; proceed to status polling.
                pass

            if self._resume_requested or self._cancel_requested:
                break

            try:
                poll_result = await workflow.execute_activity(
                    self._integration_activity_type("status"),
                    {
                        "principal": self._principal(),
                        "correlation_id": correlation_id,
                        "parameters": integration_parameters,
                        "execution_ref": {
                            "namespace": workflow.info().namespace,
                            "workflow_id": workflow.info().workflow_id,
                            "run_id": workflow.info().run_id,
                            "link_type": "output.summary",
                        },
                    },
                    start_to_close_timeout=timedelta(minutes=5),
                    task_queue=INTEGRATIONS_TASK_QUEUE,
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
                summary_ref = self._get_from_result(poll_result, "tracking_ref")
                if summary_ref:
                    self._summary_ref = summary_ref
                    self._update_memo()

                status = self._get_from_result(poll_result, "normalized_status")
                if status in ("succeeded", "failed", "canceled"):
                    self._resume_requested = True
                    if status == "failed":
                        workflow.logger.warning(f"Integration failed: {poll_result}")
                    elif status == "canceled":
                        self._cancel_requested = True
            except Exception as exc:
                workflow.logger.warning(f"Integration polling failed: {exc}")
            finally:
                poll_interval_seconds = min(
                    poll_interval_seconds * 2, max_poll_interval_seconds
                )
        self._resume_requested = False
        self._awaiting_external = False
        self._waiting_reason = None
        self._attention_required = False
        self._update_search_attributes()

    def _set_state(self, state: str, summary: Optional[str] = None) -> None:
        self._state = state
        if summary:
            self._summary = summary
        self._update_search_attributes()
        self._update_memo()

    def _principal(self) -> str:
        if not self._owner_id:
            raise ValueError("Trusted owner metadata is required")
        return self._owner_id

    def _integration_activity_type(self, operation: str = "start") -> str:
        if not self._integration:
            raise ValueError("integration is required for integration activities")
        return f"integration.{self._integration}.{operation}"

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
            "mm_updated_at": workflow.now(),
        }

        memo: dict[str, Any] = {
            "waiting_reason": self._waiting_reason,
            "attention_required": self._attention_required,
        }
        try:
            workflow.upsert_memo(memo)
        except Exception as exc:
            workflow.logger.warning(
                "Failed to upsert memo",
                extra={"error": str(exc)},
            )

        if self._owner_type:
            attributes["mm_owner_type"] = self._owner_type
        if self._owner_id:
            attributes["mm_owner_id"] = self._owner_id
        if self._repo:
            attributes["mm_repo"] = self._repo
        if self._integration:
            attributes["mm_integration"] = self._integration

        formatted_attributes = {
            k: v if isinstance(v, list) else [v] for k, v in attributes.items()
        }

        try:
            workflow.upsert_search_attributes(formatted_attributes)
        except Exception as exc:
            # During basic tests search attributes might not be registered
            workflow.logger.warning(
                "Failed to upsert search attributes",
                extra={"error": str(exc)},
            )

    def _update_memo(self) -> None:
        memo_dict: dict[str, Any] = {
            "title": self._title or "Run",
            "summary": self._summary,
        }
        if self._input_ref:
            memo_dict["input_artifact_ref"] = self._input_ref
        if self._plan_ref:
            memo_dict["plan_artifact_ref"] = self._plan_ref
        if self._logs_ref:
            memo_dict["logs_artifact_ref"] = self._logs_ref
        if self._summary_ref:
            memo_dict["summary_artifact_ref"] = self._summary_ref

        try:
            workflow.upsert_memo(memo_dict)
        except Exception as exc:
            workflow.logger.warning(
                "Failed to upsert memo",
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
        summary = f"Canceled: {reason}" if reason else "Canceled."
        self._set_state(STATE_CANCELED, summary=summary)

    @workflow.signal(name="ExternalEvent")
    def external_event(self, payload: dict[str, Any]) -> None:
        if (
            self._correlation_id is None
            or payload.get("correlation_id") != self._correlation_id
        ):
            workflow.logger.warning(
                "ExternalEvent signal rejected: missing or mismatched correlation_id"
            )
            return

        event_type = payload.get("event_type")
        normalized_status = payload.get("normalized_status")

        if event_type == "completed" or normalized_status in (
            "succeeded",
            "failed",
            "canceled",
        ):
            if normalized_status == "failed":
                safe_payload = {
                    key: value
                    for key, value in payload.items()
                    if key
                    in (
                        "event_type",
                        "normalized_status",
                        "error_message",
                        "correlation_id",
                    )
                }
                workflow.logger.warning(f"Integration failed: {safe_payload}")
            elif normalized_status == "canceled":
                self._cancel_requested = True
            self._resume_requested = True

    @workflow.update
    def update_title(self, new_title: str) -> None:
        self._title = new_title

    @workflow.update
    def update_parameters(self, new_parameters: dict[str, Any]) -> None:
        self._parameters_updated = True
        self._updated_parameters = new_parameters
