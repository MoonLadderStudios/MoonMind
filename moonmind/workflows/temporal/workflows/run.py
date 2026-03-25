import asyncio
import json
import logging
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Optional, TypedDict

from temporalio import exceptions, workflow
from temporalio.workflow import ActivityCancellationType
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
    )
    from moonmind.workflows.tasks.routing import _coerce_bool

from moonmind.workflows.skills.skill_plan_contracts import parse_plan_definition
from moonmind.workflows.skills.skill_registry import parse_skill_registry
from moonmind.workflows.temporal.activity_catalog import (
    INTEGRATIONS_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
    TemporalActivityRoute,
    build_default_activity_catalog,
)

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)

DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()


class RunWorkflowInput(TypedDict, total=False):
    """Input payload for the MoonMind.Run workflow."""

    workflow_type: str
    title: Optional[str]
    initial_parameters: dict[str, Any]
    input_artifact_ref: Optional[str]
    plan_artifact_ref: Optional[str]


class _RunWorkflowOutputBase(TypedDict):
    status: str
    message: Optional[str]


class RunWorkflowOutput(_RunWorkflowOutputBase, total=False):
    proposals_generated: int
    proposals_submitted: int


WORKFLOW_NAME = "MoonMind.Run"
STATE_INITIALIZING = "initializing"
STATE_WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"
STATE_PLANNING = "planning"
STATE_AWAITING_SLOT = "awaiting_slot"
STATE_EXECUTING = "executing"
STATE_PROPOSALS = "proposals"
STATE_AWAITING_EXTERNAL = "awaiting_external"
STATE_FINALIZING = "finalizing"
STATE_COMPLETED = "completed"
STATE_CANCELED = "canceled"
STATE_FAILED = "failed"
CLOSE_STATUS_COMPLETED = "completed"
CLOSE_STATUS_CANCELED = "canceled"
CLOSE_STATUS_FAILED = "failed"
OWNER_ID_SEARCH_ATTRIBUTE = "mm_owner_id"
OWNER_TYPE_SEARCH_ATTRIBUTE = "mm_owner_type"
_GITHUB_PR_URL_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+",
    re.IGNORECASE,
)
_MANAGED_AGENT_IDS = frozenset(
    {"gemini_cli", "gemini_cli", "claude", "claude_code", "codex", "codex_cli"}
)


@workflow.defn(name="MoonMind.Run")
class MoonMindRunWorkflow:
    def _get_logger(self) -> logging.LoggerAdapter | logging.Logger:
        try:
            info = workflow.info()
        except Exception:
            logging.getLogger(__name__).exception("Error getting workflow info in _get_logger")
            return logging.getLogger(__name__)

        extra = {
            "workflow_id": getattr(info, "workflow_id", "unknown"),
            "run_id": getattr(info, "run_id", "unknown"),
            "task_queue": getattr(info, "task_queue", "unknown"),
        }
        owner_id = getattr(self, "_owner_id", None)
        if owner_id:
            extra["owner_id"] = owner_id

        # In tests, workflow.logger might be mocked or we might be outside the event loop
        logger_to_use = workflow.logger
        if not hasattr(logger_to_use, "isEnabledFor"):
            logger_to_use = logging.getLogger(__name__)

        try:
            logger_to_use.isEnabledFor(logging.INFO)
            return logging.LoggerAdapter(logger_to_use, extra=extra)
        except Exception:
            logging.getLogger(__name__).exception("Error checking logger capabilities in _get_logger")
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

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
        self._external_status: Optional[str] = None

        # Internal state
        self._wait_cycle_count = 0
        self._step_count = 0
        self._max_wait_cycles = 100
        self._max_steps = 100

        # Proposal tracking
        self._proposals_generated = 0
        self._proposals_submitted = 0
        self._proposals_errors: list[str] = []

    def _retry_policy_for_route(self, route: TemporalActivityRoute) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        )

    def _execute_kwargs_for_route(self, route: TemporalActivityRoute) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": self._retry_policy_for_route(route),
        }
        if route.timeouts.heartbeat_timeout_seconds is not None:
            kwargs["heartbeat_timeout"] = timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            )
        return kwargs

    def _decode_json_payload(
        self,
        payload: Any,
        *,
        error_message: str,
    ) -> dict[str, Any]:
        decoded: Any
        if isinstance(payload, bytes):
            decoded = json.loads(payload.decode("utf-8"))
        elif isinstance(payload, str):
            decoded = json.loads(payload)
        else:
            decoded = payload
        if not isinstance(decoded, Mapping):
            raise ValueError(error_message)
        return self._json_mapping(decoded, path="activity_payload")

    def _ordered_plan_node_payloads(
        self,
        *,
        nodes: tuple[Any, ...],
        edges: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        payload_by_id = {node.id: node.to_payload() for node in nodes}
        node_order = {node.id: index for index, node in enumerate(nodes)}
        dependencies = {node_id: set() for node_id in payload_by_id}
        dependents = {node_id: set() for node_id in payload_by_id}

        for edge in edges:
            from_node = str(getattr(edge, "from_node", "") or "").strip()
            to_node = str(getattr(edge, "to_node", "") or "").strip()
            if from_node not in payload_by_id or to_node not in payload_by_id:
                raise ValueError(
                    "plan edges must reference nodes defined in plan.nodes"
                )
            if from_node == to_node:
                raise ValueError("plan edges cannot include self-dependencies")
            dependencies[to_node].add(from_node)
            dependents[from_node].add(to_node)

        ready = sorted(
            [node_id for node_id, deps in dependencies.items() if not deps],
            key=lambda node_id: node_order[node_id],
        )
        ordered_node_ids: list[str] = []

        while ready:
            node_id = ready.pop(0)
            ordered_node_ids.append(node_id)
            for dependent in sorted(
                dependents[node_id], key=lambda candidate: node_order[candidate]
            ):
                dependencies[dependent].discard(node_id)
                if not dependencies[dependent]:
                    ready.append(dependent)
            ready.sort(key=lambda candidate: node_order[candidate])

        if len(ordered_node_ids) != len(payload_by_id):
            raise ValueError(
                "plan edges contain at least one cycle; execution order is undefined"
            )
        return [payload_by_id[node_id] for node_id in ordered_node_ids]

    @workflow.run
    async def run(self, input_payload: RunWorkflowInput) -> RunWorkflowOutput:
        try:
            workflow_type, parameters, input_ref, plan_ref = (
                self._initialize_from_payload(input_payload)
            )
        except ValueError as exc:
            raise exceptions.ApplicationError(
                str(exc),
                non_retryable=True,
            ) from exc
        self._get_logger().info(
            "Starting MoonMind.Run workflow",
            extra={"workflow_type": workflow_type},
        )

        self._set_state(STATE_INITIALIZING, summary="Execution initialized.")
        self._set_state(STATE_PLANNING, summary="Planning execution strategy.")

        # Pause until unpaused
        await workflow.wait_condition(lambda: not self._paused)
        if self._cancel_requested:
            await self._run_finalizing_stage(parameters=parameters, status="canceled", error=None)
            return {"status": "canceled"}

        try:
            resolved_plan_ref = await self._run_planning_stage(
                parameters=parameters,
                input_ref=input_ref,
                plan_ref=plan_ref,
            )
            await self._run_execution_stage(
                parameters=parameters,
                plan_ref=resolved_plan_ref,
            )
        except ValueError as exc:
            await self._run_finalizing_stage(parameters=parameters, status="failed", error=str(exc))
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=str(exc))
            raise exceptions.ApplicationError(
                str(exc),
                non_retryable=True,
            ) from exc
        except Exception as exc:
            await self._run_finalizing_stage(parameters=parameters, status="failed", error=str(exc))
            raise

        if self._cancel_requested:
            await self._run_finalizing_stage(parameters=parameters, status="canceled", error=None)
            return {"status": "canceled"}

        await self._run_proposals_stage(parameters=parameters)

        self._set_state(STATE_FINALIZING, summary="Finalizing execution.")

        await self._run_finalizing_stage(parameters=parameters, status="success", error=None)

        self._close_status = CLOSE_STATUS_COMPLETED
        self._set_state(STATE_COMPLETED, summary="Workflow completed successfully.")

        output: RunWorkflowOutput = {
            "status": "success",
            "message": "Workflow completed successfully",
        }
        if self._proposals_generated > 0 or self._proposals_submitted > 0:
            output["proposals_generated"] = self._proposals_generated
            output["proposals_submitted"] = self._proposals_submitted
        return output

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

        if input_ref:
            self._input_ref = input_ref
        if plan_ref:
            self._plan_ref = plan_ref

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

        plan_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("plan.generate")
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
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
            **self._execute_kwargs_for_route(plan_route),
        )
        resolved_plan_ref = (
            plan_result.get("plan_ref")
            if isinstance(plan_result, dict)
            else getattr(plan_result, "plan_ref", None)
        )
        if resolved_plan_ref:
            self._plan_ref = resolved_plan_ref
            self._update_memo()
        return resolved_plan_ref

    async def _run_execution_stage(
        self, *, parameters: dict[str, Any], plan_ref: Optional[str]
    ) -> None:
        if plan_ref is None:
            raise ValueError(
                "plan_ref is required for execution stage: the planning stage must "
                "produce a plan artifact reference before execution can proceed. "
                "Ensure the planning activity returns a non-None 'plan_ref'."
            )
        self._set_state(STATE_EXECUTING, summary="Executing run steps.")

        artifact_read_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.read")
        plan_payload = await workflow.execute_activity(
            "artifact.read",
            {
                "principal": self._principal(),
                "artifact_ref": plan_ref,
            },
            **self._execute_kwargs_for_route(artifact_read_route),
        )
        plan_dict = self._decode_json_payload(
            plan_payload,
            error_message="plan_ref must resolve to a JSON object",
        )
        plan_definition = parse_plan_definition(plan_dict)
        ordered_nodes = self._ordered_plan_node_payloads(
            nodes=plan_definition.nodes,
            edges=plan_definition.edges,
        )

        registry_snapshot_ref = plan_definition.metadata.registry_snapshot.artifact_ref
        failure_mode = plan_definition.policy.failure_mode
        publish_mode = self._publish_mode(parameters)
        require_pull_request_url = publish_mode == "pr" and self._integration is None
        pull_request_url: str | None = None
        skill_definitions_by_key: dict[tuple[str, str], Any] = {}

        # Track the Jules session ID across plan nodes for multi-step
        # session reuse.  After the first Jules step completes, its
        # session_id is extracted from the result metadata.  Subsequent
        # Jules steps receive this ID so MoonMind.AgentRun calls
        # sendMessage instead of creating a new session.
        jules_session_id: str | None = None

        if registry_snapshot_ref:
            registry_payload = await workflow.execute_activity(
                "artifact.read",
                {
                    "principal": self._principal(),
                    "artifact_ref": registry_snapshot_ref,
                },
                **self._execute_kwargs_for_route(artifact_read_route),
            )
            registry_document = self._decode_json_payload(
                registry_payload,
                error_message=(
                    "registry_snapshot_ref must resolve to a JSON object payload"
                ),
            )
            skill_definitions = parse_skill_registry(registry_document)
            skill_definitions_by_key = {
                definition.key: definition for definition in skill_definitions
            }

        for index, node in enumerate(ordered_nodes, start=1):
            tool = node.get("tool")
            skill = node.get("skill")

            selected_node: Mapping[str, Any] | None = None
            if isinstance(tool, Mapping):
                selected_node = tool
            elif isinstance(skill, Mapping):
                selected_node = skill
            if selected_node is None:
                raise ValueError(
                    "plan node tool definition is required (node.skill is legacy alias)"
                )

            tool_type = str(
                selected_node.get("type") or selected_node.get("kind") or "skill"
            ).strip().lower()
            tool_name = str(
                selected_node.get("name") or selected_node.get("id") or ""
            ).strip()
            tool_version = str(selected_node.get("version") or "").strip()
            node_id = str(node.get("id") or "unknown")
            node_inputs = dict(node.get("inputs", {}))

            self._step_count = index
            self._summary = (
                f"Executing plan step {index}/{len(ordered_nodes)}: {tool_name}"
            )
            self._update_memo()

            system_retries = 0
            while system_retries <= 3:
                if tool_type == "agent_runtime":
                    # --- Agent dispatch: child workflow ---
                    try:
                        request = self._build_agent_execution_request(
                            node_inputs=node_inputs,
                            node_id=node_id,
                            tool_name=tool_name,
                        )
                        # --- Multi-step Jules: adjust parameters for proper PR creation ---
                        if request.agent_id == "jules":
                            new_params = dict(request.parameters or {})
                            if publish_mode == "pr":
                                new_params["publishMode"] = "branch"
                                new_params.pop("automationMode", None)
                            if jules_session_id:
                                new_params["jules_session_id"] = jules_session_id
                            
                            if new_params != (request.parameters or {}):
                                request = request.model_copy(update={"parameters": new_params})
                        child_workflow_id = f"{workflow.info().workflow_id}:agent:{node_id}"
                        if system_retries > 0:
                            child_workflow_id = f"{child_workflow_id}:retry{system_retries}"
                        child_result = await workflow.execute_child_workflow(
                            "MoonMind.AgentRun",
                            request,
                            id=child_workflow_id,
                            task_queue=WORKFLOW_TASK_QUEUE,
                        )
                        execution_result = self._map_agent_run_result(child_result)
                    except Exception:
                        if failure_mode == "FAIL_FAST":
                            raise
                        # Clear session on failure so later Jules steps start fresh.
                        if "request" in dir() and getattr(request, "agent_id", None) == "jules":
                            jules_session_id = None
                        result_status = "FAILED"
                        break

                elif tool_type == "skill":
                    # --- Activity dispatch: existing skill path ---
                    if not tool_name or not tool_version:
                        raise ValueError("plan node tool name/version is required")
                    invocation_payload = {
                        "id": node_id,
                        "tool": {"type": "skill", "name": tool_name, "version": tool_version},
                        "skill": {"name": tool_name, "version": tool_version},
                        "inputs": node_inputs,
                        "options": node.get("options", {}),
                    }
                    route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("mm.skill.execute")
                    if skill_definitions_by_key:
                        skill_key = (tool_name, tool_version)
                        if skill_key not in skill_definitions_by_key:
                            raise ValueError(
                                "Tool "
                                f"'{tool_name}:{tool_version}' was not found in pinned "
                                "registry snapshot"
                            )
                        route = DEFAULT_ACTIVITY_CATALOG.resolve_skill(
                            skill_definitions_by_key[skill_key]
                        )
                    if route.activity_type not in {"mm.skill.execute", "mm.tool.execute"}:
                        raise ValueError(
                            "plan node tool executor "
                            f"'{route.activity_type}' is unsupported by MoonMind.Run; "
                            "expected mm.tool.execute or mm.skill.execute"
                        )

                    try:
                        execution_result = await workflow.execute_activity(
                            route.activity_type,
                            {
                                "invocation_payload": invocation_payload,
                                "principal": self._principal(),
                                "registry_snapshot_ref": registry_snapshot_ref,
                                "context": {
                                    "workflow_id": workflow.info().workflow_id,
                                    "run_id": workflow.info().run_id,
                                    "node_id": node_id,
                                },
                            },
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            **self._execute_kwargs_for_route(route),
                        )
                    except Exception:
                        if failure_mode == "FAIL_FAST":
                            raise
                        result_status = "FAILED"
                        break

                else:
                    raise ValueError(
                        f"unsupported plan node tool.type: '{tool_type}'; "
                        "expected 'skill' or 'agent_runtime'"
                    )

                result_status = self._activity_result_status(execution_result)
                if result_status is None:
                    if failure_mode == "FAIL_FAST":
                        raise ValueError(
                            "plan node execution result is missing required status field"
                        )
                    break
                if result_status != "COMPLETED":
                    failure_message = self._activity_result_failure_message(
                        execution_result
                    )
                    
                    if failure_message == "system_error" and system_retries < 3:
                        system_retries += 1
                        self._get_logger().info(
                            f"Retrying plan node {node_id} after system_error "
                            f"(attempt {system_retries} of 3)"
                        )
                        if tool_type == "agent_runtime" and node_inputs.get("runtime", {}).get("mode") == "jules":
                            jules_session_id = None
                        continue
                        
                    if failure_mode == "FAIL_FAST":
                        detail = (
                            f" with error '{failure_message}'"
                            if failure_message
                            else ""
                        )
                        raise ValueError(
                            f"plan node execution returned status {result_status}{detail}"
                        )
                    # Clear session on failure so later Jules steps start fresh.
                    if tool_type == "agent_runtime" and node_inputs.get("runtime", {}).get("mode") == "jules":
                        jules_session_id = None
                    break

                break

            if result_status is None or result_status != "COMPLETED":
                continue

            # --- Multi-step Jules: extract session_id only on success ---
            if tool_type == "agent_runtime":
                extracted_id = self._extract_jules_session_id(child_result)
                if extracted_id:
                    jules_session_id = extracted_id
            if require_pull_request_url and pull_request_url is None:
                pull_request_url = self._extract_pull_request_url(execution_result)
                
                # If still not found, check the diagnostics artifact if present
                if pull_request_url is None and tool_type == "agent_runtime":
                    outputs = self._get_from_result(execution_result, "outputs") or {}
                    diag_ref = outputs.get("diagnostics_ref")
                    if diag_ref:
                        try:
                            diag_payload = await workflow.execute_activity(
                                "artifact.read",
                                {
                                    "principal": self._principal(),
                                    "artifact_ref": diag_ref,
                                },
                                **self._execute_kwargs_for_route(artifact_read_route),
                            )
                            if isinstance(diag_payload, bytes):
                                diag_text = diag_payload.decode("utf-8", errors="replace")
                            elif isinstance(diag_payload, str):
                                diag_text = diag_payload
                            else:
                                diag_text = str(diag_payload)
                                
                            pr_match = _GITHUB_PR_URL_PATTERN.search(diag_text)
                            if pr_match:
                                pull_request_url = pr_match.group(0)
                        except Exception as e:
                            self._get_logger().warning(f"Failed to extract PR URL from diagnostics_ref {diag_ref}: {e}")
                            
        if require_pull_request_url and pull_request_url is None:
            # Create PR natively since Jules publish_mode was overridden to "branch"
            ws = parameters.get("workspaceSpec") or {}
            head_branch = ws.get("branch") or ""
            target_branch = parameters.get("targetBranch") or ws.get("targetBranch") or ws.get("startingBranch") or "main"
            
            if not self._repo or not head_branch:
                raise ValueError(
                    "publishMode 'pr' requested but no PR URL was returned, and missing repo/branch to create it natively"
                )
            
            self._get_logger().info(
                f"Creating PR natively from {head_branch} into {target_branch} for repo {self._repo}"
            )
            create_payload = {
                "repo": self._repo,
                "head": head_branch,
                "base": target_branch,
                "title": self._title or "Automated changes by MoonMind",
                "body": self._summary or "Automated changes by MoonMind.",
            }
            try:
                create_result = await workflow.execute_activity(
                    "integration.jules.create_pr",
                    create_payload,
                    start_to_close_timeout=timedelta(minutes=2),
                    task_queue=INTEGRATIONS_TASK_QUEUE,
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                )
                pr_url = self._get_from_result(create_result, "url")
                if pr_url:
                    pull_request_url = pr_url
                    self._get_logger().info(f"Natively created PR: {pull_request_url}")
                else:
                    raise ValueError("PR creation activity succeeded but returned no URL")
            except Exception as e:
                raise ValueError(
                    f"publishMode 'pr' requested; native PR creation failed: {e}"
                ) from e
        self._summary = f"Executed {len(ordered_nodes)} plan step(s)."
        self._update_memo()

        if self._integration:
            await self._run_integration_stage(parameters=parameters, plan_ref=plan_ref)

    def _get_from_result(self, result: Any, key: str) -> Any:
        if isinstance(result, dict):
            return result.get(key)
        return getattr(result, key, None)

    def _activity_result_status(self, result: Any) -> str | None:
        raw_status = self._get_from_result(result, "status")
        if raw_status is None:
            return None
        normalized = str(raw_status).strip().upper()
        return normalized or None

    def _activity_result_failure_message(self, result: Any) -> str:
        outputs = self._get_from_result(result, "outputs")
        if isinstance(outputs, Mapping):
            error = outputs.get("error")
            if isinstance(error, str) and error.strip():
                return error.strip()
            stderr_tail = outputs.get("stderr_tail")
            if isinstance(stderr_tail, str) and stderr_tail.strip():
                return stderr_tail.strip()
            exit_code = outputs.get("exit_code")
            if exit_code is not None:
                return f"activity exited with code {exit_code}"
        progress = self._get_from_result(result, "progress")
        if isinstance(progress, Mapping):
            details = progress.get("details")
            if isinstance(details, str) and details.strip():
                return details.strip()
        return ""

    def _publish_mode(self, parameters: Mapping[str, Any]) -> str:
        value = parameters.get("publishMode")
        if not isinstance(value, str):
            return ""
        normalized = value.strip().lower()
        return normalized if normalized in {"none", "branch", "pr"} else ""

    def _extract_pull_request_url(self, result: Any) -> str | None:
        outputs = self._get_from_result(result, "outputs")
        if not isinstance(outputs, Mapping):
            return None

        candidate_fields = (
            "pull_request_url",
            "pullRequestUrl",
            "pr_url",
            "prUrl",
            "url",
            "external_url",
            "externalUrl",
            "stdout_tail",
            "stderr_tail",
            "summary",
            "message",
        )
        for field in candidate_fields:
            value = outputs.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            match = _GITHUB_PR_URL_PATTERN.search(value)
            if match is not None:
                return match.group(0)
        return None

    def _build_agent_execution_request(
        self,
        *,
        node_inputs: dict[str, Any],
        node_id: str,
        tool_name: str,
    ) -> "AgentExecutionRequest":
        """Build an ``AgentExecutionRequest`` from plan-node inputs and workflow context."""
        runtime_block = node_inputs.get("runtime") or {}
        agent_id = str(
            runtime_block.get("mode")
            or runtime_block.get("agent_id")
            or node_inputs.get("targetRuntime")
            or tool_name
        ).strip()
        if not agent_id:
            raise ValueError(
                "agent_runtime plan node must specify an agent_id "
                "(via inputs.runtime.mode, inputs.targetRuntime, or tool.name)"
            )

        agent_kind = self._agent_kind_for_id(agent_id)
        execution_profile_ref = str(
            node_inputs.get("executionProfileRef")
            or runtime_block.get("executionProfileRef")
            or f"default:{agent_id}"
        )
        wf_info = workflow.info()
        correlation_id = wf_info.workflow_id
        idempotency_key = f"{wf_info.workflow_id}:{node_id}:{wf_info.run_id}"

        workspace_spec: dict[str, Any] = {}
        for ws_key in ("repository", "repo", "startingBranch", "newBranch", "branch"):
            ws_val = node_inputs.get(ws_key)
            if ws_val is not None:
                workspace_spec[ws_key] = ws_val

        parameters: dict[str, Any] = {}
        for param_key in ("model", "effort", "publishMode", "allowed_tools", "stepCount", "maxAttempts", "steps"):
            param_val = runtime_block.get(param_key) or node_inputs.get(param_key)
            if param_val is not None:
                parameters[param_key] = param_val

        return AgentExecutionRequest(
            agent_kind=agent_kind,
            agent_id=agent_id,
            execution_profile_ref=execution_profile_ref,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            instruction_ref=node_inputs.get("instructions") or node_inputs.get("instructionRef"),
            input_refs=node_inputs.get("inputRefs") or [],
            workspace_spec=workspace_spec,
            parameters=parameters,
            timeout_policy=node_inputs.get("timeoutPolicy") or {},
            retry_policy=node_inputs.get("retryPolicy") or {},
            approval_policy=node_inputs.get("approvalPolicy") or {},
            callback_policy=node_inputs.get("callbackPolicy") or {},
        )

    def _map_agent_run_result(self, result: Any) -> dict[str, Any]:
        """Convert ``AgentRunResult`` to the dict format the execution loop expects."""
        if isinstance(result, dict):
            failure = result.get("failure_class") or result.get("failureClass")
            summary = result.get("summary") or ""
            output_refs = result.get("output_refs") or result.get("outputRefs") or []
            diagnostics_ref = result.get("diagnostics_ref") or result.get("diagnosticsRef")
            metadata = result.get("metadata") or {}
        else:
            failure = getattr(result, "failure_class", None)
            summary = getattr(result, "summary", "") or ""
            output_refs = getattr(result, "output_refs", []) or []
            diagnostics_ref = getattr(result, "diagnostics_ref", None)
            metadata = getattr(result, "metadata", {}) or {}

        status = "FAILED" if failure else "COMPLETED"
        outputs = {
            "summary": summary,
            "output_refs": output_refs,
            "error": failure or "",
        }
        if diagnostics_ref:
            outputs["diagnostics_ref"] = diagnostics_ref
        outputs.update(metadata)

        return {
            "status": status,
            "outputs": outputs,
        }

    @staticmethod
    def _extract_jules_session_id(result: Any) -> str | None:
        """Extract the Jules session ID from an ``AgentRunResult`` for multi-step reuse."""
        if isinstance(result, dict):
            metadata = result.get("metadata") or {}
        else:
            metadata = getattr(result, "metadata", {}) or {}
        return metadata.get("jules_session_id")

    @staticmethod
    def _agent_kind_for_id(agent_id: str) -> str:
        """Derive ``agent_kind`` from ``agent_id``."""
        return "managed" if agent_id.lower() in _MANAGED_AGENT_IDS else "external"

    async def _run_integration_stage(
        self, *, parameters: dict[str, Any], plan_ref: Optional[str]
    ) -> None:
        self._awaiting_external = True
        self._set_state(
            STATE_AWAITING_EXTERNAL, summary="Waiting for external integration."
        )

        # Extract per-step instructions if a plan is available
        step_instructions: list[str] = []
        if plan_ref:
            try:
                artifact_read_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.read")
                plan_payload = await workflow.execute_activity(
                    "artifact.read",
                    {
                        "principal": self._principal(),
                        "artifact_ref": plan_ref,
                    },
                    **self._execute_kwargs_for_route(artifact_read_route),
                )
                plan_dict = self._decode_json_payload(
                    plan_payload,
                    error_message="plan_ref must resolve to a JSON object",
                )
                plan_definition = parse_plan_definition(plan_dict)
                plan_nodes_payloads = self._ordered_plan_node_payloads(
                    nodes=plan_definition.nodes,
                    edges=plan_definition.edges,
                )
                for node in plan_nodes_payloads:
                    inputs = node.get("inputs", {})
                    instr = inputs.get("instructions")
                    if instr and isinstance(instr, str) and instr.strip():
                        step_instructions.append(instr.strip())
            except Exception as e:
                self._get_logger().warning(
                    f"Failed to extract step instructions for integration multi-step: {e}"
                )

        instructions_to_run = step_instructions if (step_instructions and self._integration == "jules") else [None]

        integration_parameters = dict(parameters)
        
        # --- Multi-step Jules: adjust parameters for proper PR creation ---
        if self._integration == "jules":
            publish_mode = self._publish_mode(integration_parameters)
            if publish_mode == "pr":
                integration_parameters["publishMode"] = "branch"
                integration_parameters.pop("automationMode", None)
                
        integration_parameters.setdefault(
            "title", self._title or "MoonMind Integration"
        )
        if instructions_to_run[0]:
            integration_parameters["description"] = instructions_to_run[0]
        else:
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

        start_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            self._integration_activity_type("start")
        )
        start_result = await workflow.execute_activity(
            start_route.activity_type,
            {
                "principal": self._principal(),
                "parameters": integration_parameters,
            },
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
            **self._execute_kwargs_for_route(start_route),
        )
        summary_ref = self._get_from_result(start_result, "tracking_ref")
        if summary_ref:
            self._summary_ref = summary_ref
            self._update_memo()

        external_id = self._get_from_result(start_result, "external_id")
        self._correlation_id = external_id
        
        self._waiting_reason = "external_completion"
        self._attention_required = False
        self._update_search_attributes()

        for step_index, instruction in enumerate(instructions_to_run):
            if self._cancel_requested:
                break

            if step_index > 0:
                self._resume_requested = False
                self._external_status = "running"
                self._update_memo()
                self._get_logger().info(f"Jules multi-step integration: sending step {step_index + 1}/{len(instructions_to_run)}")
                try:
                    await workflow.execute_activity(
                        self._integration_activity_type("send_message"),
                        {
                            "session_id": external_id,
                            "prompt": instruction,
                        },
                        start_to_close_timeout=timedelta(minutes=5),
                        task_queue=INTEGRATIONS_TASK_QUEUE,
                        retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                    )
                except Exception as exc:
                    self._get_logger().warning(f"Failed to send follow-up step {step_index + 1} to Jules: {exc}")
                    self._external_status = "failed"
                    break

            poll_interval_seconds = 5
            max_poll_interval_seconds = 300
            _poll_terminal = False

            while not self._resume_requested and not self._cancel_requested and not _poll_terminal:
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
                    poll_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                        self._integration_activity_type("status")
                    )
                    poll_result = await workflow.execute_activity(
                        poll_route.activity_type,
                        {
                            "principal": self._principal(),
                            "external_id": external_id,
                            "parameters": integration_parameters,
                            "execution_ref": {
                                "namespace": workflow.info().namespace,
                                "workflow_id": workflow.info().workflow_id,
                                "run_id": workflow.info().run_id,
                                "link_type": "output.summary",
                            },
                        },
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        **self._execute_kwargs_for_route(poll_route),
                    )
                    summary_ref = self._get_from_result(poll_result, "tracking_ref")
                    if summary_ref:
                        self._summary_ref = summary_ref
                        self._update_memo()

                    status = self._get_from_result(poll_result, "normalized_status")
                    if status in ("completed", "failed", "canceled", "awaiting_feedback"):
                        _poll_terminal = True
                        self._external_status = "completed" if status == "awaiting_feedback" else status
                        if status == "failed":
                            self._get_logger().warning(f"Integration failed: {poll_result}")
                        elif status == "canceled":
                            self._cancel_requested = True
                except Exception as exc:
                    self._get_logger().warning(f"Integration polling failed: {exc}")
                finally:
                    poll_interval_seconds = min(
                        poll_interval_seconds * 2, max_poll_interval_seconds
                    )

            if self._external_status != "completed":
                # If a step failed or was canceled, do not dispatch remaining steps
                break

        self._awaiting_external = False
        self._waiting_reason = None
        self._attention_required = False
        self._update_search_attributes()

        if self._external_status == "failed":
            raise ValueError("Integration failed during plan execution.")

        if self._external_status == "failed":
            raise ValueError("Integration failed during plan execution.")

        # --- Jules branch-publish auto-merge ---
        # When publishMode is "branch" and the integration is Jules, the
        # workflow uses AUTO_CREATE_PR so Jules produces a PR targeting the
        # starting branch.  After Jules succeeds, we auto-merge that PR so
        # the changes land directly on the target branch.
        publish_mode = self._publish_mode(parameters)
        if (
            publish_mode == "branch"
            and self._integration == "jules"
            and not self._cancel_requested
            and self._external_status == "completed"
        ):
            self._get_logger().info("Jules branch-publish: fetching result for PR merge")
            try:
                fetch_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                    self._integration_activity_type("fetch_result")
                )
                fetch_result = await workflow.execute_activity(
                    fetch_route.activity_type,
                    {
                        "principal": self._principal(),
                        "external_id": external_id,
                        "parameters": integration_parameters,
                    },
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    **self._execute_kwargs_for_route(fetch_route),
                )
                pr_url = (
                    self._get_from_result(fetch_result, "external_url")
                    or self._get_from_result(fetch_result, "url")
                )
                # If external_url is the session URL, try to extract PR URL
                # from the summary or output_refs text.
                if pr_url and not _GITHUB_PR_URL_PATTERN.search(pr_url):
                    summary_text = self._get_from_result(fetch_result, "summary") or ""
                    pr_match = _GITHUB_PR_URL_PATTERN.search(summary_text)
                    if pr_match:
                        pr_url = pr_match.group(0)
                    else:
                        pr_url = None

                if pr_url:
                    # Determine if the PR's base branch needs updating.
                    # If targetBranch is specified and differs from
                    # startingBranch (which Jules used as the PR base),
                    # the activity will PATCH the PR before merging.
                    ws = parameters.get("workspaceSpec") or {}
                    starting_branch = (
                        ws.get("startingBranch") or ws.get("branch") or "main"
                    )
                    target_branch = (
                        parameters.get("targetBranch")
                        or ws.get("targetBranch")
                    )
                    # Only pass target_branch when it differs from starting
                    effective_target = (
                        target_branch
                        if target_branch and target_branch != starting_branch
                        else None
                    )

                    self._get_logger().info(
                        "Jules branch-publish: merging PR %s (target=%s)",
                        pr_url,
                        effective_target or starting_branch,
                    )
                    merge_payload = {"pr_url": pr_url}
                    if effective_target:
                        merge_payload["target_branch"] = effective_target

                    merge_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                        "integration.jules.merge_pr"
                    )
                    merge_result = await workflow.execute_activity(
                        merge_route.activity_type,
                        merge_payload,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        **self._execute_kwargs_for_route(merge_route),
                    )
                    merged = self._get_from_result(merge_result, "merged")
                    merge_summary = (
                        self._get_from_result(merge_result, "summary") or ""
                    )
                    if merged:
                        self._get_logger().info(
                            "Jules branch-publish: PR merged: %s", merge_summary
                        )
                    else:
                        self._get_logger().warning(
                            "Jules branch-publish: merge failed: %s", merge_summary
                        )
                else:
                    self._get_logger().warning(
                        "Jules branch-publish: no PR URL found in result; "
                        "skipping auto-merge"
                    )
            except Exception as exc:
                self._get_logger().warning(
                    "Jules branch-publish auto-merge failed (best-effort): %s",
                    exc,
                    exc_info=True,
                )

    async def _run_proposals_stage(
        self, *, parameters: dict[str, Any]
    ) -> None:
        """Best-effort proposal generation phase.

        Runs only when ``proposeTasks`` is set in ``initialParameters``.
        Failures are logged but do not fail the workflow.
        """
        propose_tasks = parameters.get("proposeTasks")
        if not _coerce_bool(propose_tasks, default=False):
            return

        self._set_state(STATE_PROPOSALS, summary="Generating task proposals.")

        try:
            proposal_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "proposal.generate"
            )
            candidates = await workflow.execute_activity(
                "proposal.generate",
                {
                    "principal": self._principal(),
                    "workflow_id": workflow.info().workflow_id,
                    "run_id": workflow.info().run_id,
                    "repo": self._repo,
                    "parameters": parameters,
                },
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                **self._execute_kwargs_for_route(proposal_route),
            )
        except Exception as exc:
            self._get_logger().warning(
                "Proposal generation failed (best-effort): %s", exc
            )
            self._proposals_errors.append(f"generation failed: {str(exc)[:200]}")
            return

        candidate_list = candidates if isinstance(candidates, list) else []
        self._proposals_generated = len(candidate_list)

        if not candidate_list:
            return

        try:
            submit_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "proposal.submit"
            )
            policy = {
                "max_items": parameters.get("proposalMaxItems", 10),
                "targets": parameters.get("proposalTargets", "project"),
                "default_runtime": parameters.get("proposalDefaultRuntime"),
            }
            origin = {
                "workflow_id": workflow.info().workflow_id,
                "temporal_run_id": workflow.info().run_id,
                "trigger_repo": self._repo or "",
            }
            submit_result = await workflow.execute_activity(
                "proposal.submit",
                {
                    "candidates": candidate_list,
                    "policy": policy,
                    "origin": origin,
                    "principal": self._principal(),
                },
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                **self._execute_kwargs_for_route(submit_route),
            )
            if isinstance(submit_result, dict):
                self._proposals_submitted = submit_result.get(
                    "submitted_count", 0
                )
                errors = submit_result.get("errors") or []
                self._proposals_errors.extend(errors)
        except Exception as exc:
            self._get_logger().warning(
                "Proposal submission failed (best-effort): %s", exc
            )
            self._proposals_errors.append(f"submission failed: {str(exc)[:200]}")


    async def _run_finalizing_stage(
        self, *, parameters: dict[str, Any], status: str, error: Optional[str] = None
    ) -> None:
        try:
            self._get_logger().info("Generating finish summary.")

            # Map Temporal status back to FinishOutcome code
            code = "FAILED" if status == "failed" else "NO_CHANGES"
            if status == "canceled":
                code = "CANCELLED"
            # Try to refine it based on publish if it was successful
            if status == "success":
                publish_mode = self._publish_mode(parameters)
                if publish_mode == "pr":
                    code = "PUBLISHED_PR" # this is a simplification
                elif publish_mode == "branch":
                    code = "PUBLISHED_BRANCH"
                elif publish_mode == "none":
                    code = "PUBLISH_DISABLED"

            finish_summary = {
                "schemaVersion": "v1",
                "jobId": workflow.info().workflow_id,
                "targetRuntime": parameters.get("runtime", {}).get("mode", "auto"),
                "timestamps": {
                    "startedAt": workflow.info().start_time.isoformat(),
                    "finishedAt": workflow.now().isoformat(),
                    "durationMs": int((workflow.now() - workflow.info().start_time).total_seconds() * 1000),
                },
                "finishOutcome": {
                    "code": code,
                    "stage": self._state,
                    "reason": error or "completed",
                },
                "publish": {
                    "mode": self._publish_mode(parameters),
                    "status": "skipped" if status != "success" else "published",
                    "reason": (
                        "run did not complete successfully" if status in ("failed", "canceled")
                        else "publishing disabled" if self._publish_mode(parameters) == "none"
                        else "published successfully" if status == "success"
                        else "no local changes"
                    ),
                },
                "proposals": {
                    "generatedCount": self._proposals_generated,
                    "submittedCount": self._proposals_submitted,
                    "errors": self._proposals_errors,
                }
            }

            artifact_create_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.create")
            artifact_ref, upload_desc = await workflow.execute_activity(
                "artifact.create",
                {
                    "principal": self._principal(),
                    "name": "reports/run_summary.json",
                    "content_type": "application/json",
                },
                **self._execute_kwargs_for_route(artifact_create_route),
            )

            artifact_write_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.write_complete")
            await workflow.execute_activity(
                "artifact.write_complete",
                {
                    "principal": self._principal(),
                    "artifact_id": self._get_from_result(artifact_ref, "artifact_id") or "",
                    "payload": json.dumps(finish_summary).encode("utf-8"),
                    "content_type": "application/json",
                },
                **self._execute_kwargs_for_route(artifact_write_route),
            )
            self._summary_ref = self._get_from_result(artifact_ref, "artifact_id") or ""
        except Exception as exc:
            self._get_logger().warning(f"Failed to generate finish summary: {exc}")

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
            raise exceptions.ApplicationError(
                "Trusted owner metadata is required in Temporal search attributes",
                non_retryable=True,
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
            self._get_logger().warning(
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
            self._get_logger().warning(
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
            self._get_logger().warning(
                "Failed to upsert memo",
                extra={"error": str(exc)},
            )

    @workflow.signal
    def child_state_changed(self, new_state: str, reason: str) -> None:
        if new_state == "awaiting_slot":
            self._set_state(STATE_AWAITING_SLOT, summary=reason)
        elif new_state == "launching":
            self._set_state(STATE_EXECUTING, summary="Launching agent...")
        elif new_state == "running":
            self._set_state(STATE_EXECUTING, summary="Agent is running.")

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
            self._get_logger().warning(
                "ExternalEvent signal rejected: missing or mismatched correlation_id"
            )
            return

        event_type = payload.get("event_type")
        normalized_status = payload.get("normalized_status")

        if event_type == "completed" or normalized_status in (
            "completed",
            "failed",
            "canceled",
            "awaiting_feedback",
        ):
            if normalized_status:
                self._external_status = "completed" if normalized_status == "awaiting_feedback" else normalized_status
            elif event_type == "completed":
                self._external_status = "completed"

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
                self._get_logger().warning(f"Integration failed: {safe_payload}")
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

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "paused": self._paused,
            "cancel_requested": self._cancel_requested,
            "step_count": self._step_count,
            "summary": self._summary,
            "awaiting_external": self._awaiting_external,
            "waiting_reason": self._waiting_reason,
        }
