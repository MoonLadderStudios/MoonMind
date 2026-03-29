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
    from moonmind.schemas.temporal_activity_models import (
        ArtifactReadInput,
        ArtifactWriteCompleteInput,
        PlanGenerateInput,
    )
    from moonmind.workflows.temporal.jules_bundle import (
        build_bundle_spec,
        eligible_for_bundle,
        is_jules_agent_runtime_node,
    )
    from moonmind.workflows.tasks.routing import _coerce_bool
    from moonmind.workflows.temporal.typed_execution import execute_typed_activity

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
    scheduled_for: Optional[str]


class _RunWorkflowOutputBase(TypedDict):
    status: str
    message: Optional[str]


class RunWorkflowOutput(_RunWorkflowOutputBase, total=False):
    proposals_generated: int
    proposals_submitted: int


WORKFLOW_NAME = "MoonMind.Run"
STATE_SCHEDULED = "scheduled"
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
# Replay-stable `workflow.patched` id for integration status polling terminal handling.
# Keep in sync with docs/Temporal/WorkerDeployment.md if renamed (only before first prod deploy).
INTEGRATION_POLL_LOOP_PATCH = "refactor-loop-1.2"
# Replay-stable patch id for parent-initiated defensive slot release on child terminal state.
RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH = "run-defensive-slot-release-1"
_MANAGED_AGENT_IDS = frozenset(
    {"gemini_cli", "gemini_cli", "claude", "claude_code", "codex", "codex_cli"}
)


def _normalize_agent_runtime_id(agent_id: str) -> str:
    """Normalize runtime identifiers for managed/external dispatch decisions."""

    return str(agent_id).strip().lower().replace("-", "_")


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

        self._scheduled_for: Optional[str] = None
        self._reschedule_requested = False

        # Proposal tracking
        self._proposals_generated = 0
        self._proposals_submitted = 0
        self._proposals_errors: list[str] = []

        # Auth profile slot tracking for managed agent runs.
        # Set when a child AgentRun acquires a slot so the parent can
        # defensively release it if the child exits in a terminal state.
        self._assigned_profile_id: Optional[str] = None
        self._assigned_child_workflow_id: Optional[str] = None
        self._assigned_runtime_id: Optional[str] = None
        self._active_agent_child_workflow_id: Optional[str] = None
        self._active_agent_id: Optional[str] = None

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

    async def _write_json_artifact(
        self,
        *,
        name: str,
        payload: dict[str, Any],
    ) -> str:
        artifact_create_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.create")
        artifact_write_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.write_complete")
        artifact_ref, _upload_desc = await workflow.execute_activity(
            "artifact.create",
            {
                "principal": self._principal(),
                "name": name,
                "content_type": "application/json",
            },
            **self._execute_kwargs_for_route(artifact_create_route),
        )
        artifact_id = (
            self._get_from_result(artifact_ref, "artifact_id")
            or self._get_from_result(artifact_ref, "artifactId")
            or ""
        )
        if not artifact_id:
            raise ValueError(f"artifact.create returned no artifact_id for {name}")
        await execute_typed_activity(
            "artifact.write_complete",
            ArtifactWriteCompleteInput(
                principal=self._principal(),
                artifact_id=artifact_id,
                payload=json.dumps(payload).encode("utf-8"),
                content_type="application/json",
            ),
            **self._execute_kwargs_for_route(artifact_write_route),
        )
        return str(artifact_id)

    async def _bundle_ordered_nodes_for_execution(
        self,
        ordered_nodes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        bundled_nodes: list[dict[str, Any]] = []
        bundle_index = 0
        idx = 0
        while idx < len(ordered_nodes):
            node = ordered_nodes[idx]
            if not is_jules_agent_runtime_node(node):
                bundled_nodes.append(node)
                idx += 1
                continue

            group = [node]
            cursor = idx + 1
            while cursor < len(ordered_nodes) and eligible_for_bundle(group[-1], ordered_nodes[cursor]):
                group.append(ordered_nodes[cursor])
                cursor += 1

            if len(group) > 1:
                bundle_index += 1
                bundle_spec = build_bundle_spec(
                    group,
                    workflow_id=workflow.info().workflow_id,
                    workflow_run_id=workflow.info().run_id,
                )
                manifest_ref = await self._write_json_artifact(
                    name=f"reports/jules_bundle_{bundle_index}.json",
                    payload=bundle_spec.manifest,
                )
                representative = dict(bundle_spec.representative_node)
                representative_inputs = dict(representative.get("inputs") or {})
                representative_inputs["bundleManifestRef"] = manifest_ref
                representative["inputs"] = representative_inputs
                bundled_nodes.append(representative)
            else:
                bundled_nodes.extend(group)
            idx = cursor

        return bundled_nodes

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
            workflow_type, parameters, input_ref, plan_ref, scheduled_for = (
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

        if self._scheduled_for:
            self._set_state(STATE_SCHEDULED, summary=f"Execution scheduled for {self._scheduled_for}.")
            while True:
                if not self._scheduled_for:
                    break
                
                try:
                    from datetime import datetime
                    target_dt = datetime.fromisoformat(self._scheduled_for.replace("Z", "+00:00"))
                except ValueError as exc:
                    self._get_logger().error(f"Invalid scheduled_for format: {self._scheduled_for}", exc_info=True)
                    raise exceptions.ApplicationError(f"Invalid scheduled_for format: {self._scheduled_for}", non_retryable=True) from exc
                
                now = workflow.now()
                delay = target_dt - now
                if delay.total_seconds() <= 0:
                    break
                
                self._reschedule_requested = False
                try:
                    await workflow.wait_condition(
                        lambda: self._reschedule_requested or self._cancel_requested,
                        timeout=delay,
                    )
                except asyncio.TimeoutError:
                    # Timeout means no reschedule/cancel happened before the scheduled time.
                    self._get_logger().debug("Scheduled delay elapsed without reschedule/cancel.")
                
                if self._cancel_requested:
                    break

        if self._cancel_requested:
            await self._run_finalizing_stage(parameters=parameters, status="canceled", error=None)
            return {"status": "canceled"}

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
        ws = self._mapping_value(parameters, "workspaceSpec", "workspace_spec") or {}
        self._repo = (
            self._string_from_mapping(parameters, "repo")
            or self._string_from_mapping(parameters, "repository")
            or self._string_from_mapping(ws, "repo")
            or self._string_from_mapping(ws, "repository")
        )
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
        scheduled_for = self._optional_string(
            input_payload,
            "scheduledFor",
            "scheduled_for",
        )

        if input_ref:
            self._input_ref = input_ref
        if plan_ref:
            self._plan_ref = plan_ref
        if scheduled_for:
            self._scheduled_for = scheduled_for

        return workflow_type, parameters, input_ref, plan_ref, scheduled_for

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
        plan_payload_args = PlanGenerateInput(
            principal=self._principal(),
            inputs_ref=input_ref,
            parameters=parameters,
            execution_ref={
                "namespace": workflow.info().namespace,
                "workflow_id": workflow.info().workflow_id,
                "run_id": workflow.info().run_id,
                "link_type": "plan",
            },
            idempotency_key=f"{workflow.info().workflow_id}_plan_generate" if workflow.patched("idempotency_key_phase3") else None,
        )

        plan_result = await execute_typed_activity(
            "plan.generate",
            plan_payload_args,
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
        plan_payload = await execute_typed_activity(
            "artifact.read",
            ArtifactReadInput(
                principal=self._principal(),
                artifact_ref=plan_ref,
            ),
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
        if workflow.patched("jules-bundling-v1"):
            ordered_nodes = await self._bundle_ordered_nodes_for_execution(
                ordered_nodes
            )

        registry_snapshot_ref = plan_definition.metadata.registry_snapshot.artifact_ref
        failure_mode = plan_definition.policy.failure_mode
        publish_mode = self._publish_mode(parameters)
        require_pull_request_url = publish_mode == "pr" and self._integration is None
        pull_request_url: str | None = None
        skill_definitions_by_key: dict[tuple[str, str], Any] = {}

        if registry_snapshot_ref:
            registry_payload = await execute_typed_activity(
                "artifact.read",
                ArtifactReadInput(
                    principal=self._principal(),
                    artifact_ref=registry_snapshot_ref,
                ),
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
                        child_workflow_id = f"{workflow.info().workflow_id}:agent:{node_id}"
                        if system_retries > 0:
                            child_workflow_id = f"{child_workflow_id}:retry{system_retries}"
                        self._active_agent_child_workflow_id = child_workflow_id
                        self._active_agent_id = request.agent_id
                        try:
                            child_result = await workflow.execute_child_workflow(
                                "MoonMind.AgentRun",
                                request,
                                id=child_workflow_id,
                                task_queue=WORKFLOW_TASK_QUEUE,
                            )
                        finally:
                            self._active_agent_child_workflow_id = None
                            self._active_agent_id = None
                        execution_result = self._map_agent_run_result(child_result)
                    except Exception:
                        if failure_mode == "FAIL_FAST":
                            raise
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
                        execute_payload = {
                            "invocation_payload": invocation_payload,
                            "principal": self._principal(),
                            "registry_snapshot_ref": registry_snapshot_ref,
                            "context": {
                                "workflow_id": workflow.info().workflow_id,
                                "run_id": workflow.info().run_id,
                                "node_id": node_id,
                            },
                        }
                        if workflow.patched("idempotency_key_phase3"):
                            execute_payload["idempotency_key"] = f"{workflow.info().workflow_id}_{node_id}_execute"

                        execution_result = await workflow.execute_activity(
                            route.activity_type,
                            execute_payload,
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

                    retryable = (
                        failure_message == "system_error"
                        or (
                            failure_message == "execution_error"
                            and tool_type == "agent_runtime"
                        )
                    )
                    if retryable and system_retries < 3:
                        system_retries += 1
                        self._get_logger().info(
                            f"Retrying plan node {node_id} after {failure_message} "
                            f"(attempt {system_retries} of 3)"
                        )
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
                    break

                break

            if result_status is None or result_status != "COMPLETED":
                continue

            if require_pull_request_url and pull_request_url is None:
                pull_request_url = self._extract_pull_request_url(execution_result)
                
                # If still not found, check the diagnostics artifact if present
                if pull_request_url is None and tool_type == "skill":
                    outputs = self._get_from_result(execution_result, "outputs") or {}
                    diag_ref = outputs.get("diagnostics_ref")
                    if diag_ref:
                        try:
                            diag_payload = await execute_typed_activity(
                                "artifact.read",
                                ArtifactReadInput(
                                    principal=self._principal(),
                                    artifact_ref=diag_ref,
                                ),
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
            last_tool = str(
                (ordered_nodes[-1].get("tool", {}) if ordered_nodes else {}).get("name") or ""
            ).strip().lower()
            # Derive the last node's effective agent_id using the same
            # resolution logic as _build_agent_execution_request so that
            # Jules-via-runtime-settings (e.g. tool.name="auto" with
            # inputs.runtime.mode="jules") is correctly detected.
            _last_inputs = ordered_nodes[-1].get("inputs", {}) if ordered_nodes else {}
            _last_rt = _last_inputs.get("runtime") or {}
            last_agent_id = (
                _last_rt.get("mode")
                or _last_rt.get("agent_id")
                or _last_inputs.get("targetRuntime")
                or last_tool
                or ""
            ).strip().lower()
            
            ws = self._mapping_value(parameters, "workspaceSpec", "workspace_spec") or {}
            
            if last_agent_id in ("jules", "jules_api", "github_pr_creator"):
                self._get_logger().info(
                    "Skipping native PR creation: agent '%s' handles its own PRs.",
                    last_agent_id,
                )
            else:
                agent_outputs = {}
                if "execution_result" in locals():
                    agent_outputs = self._get_from_result(execution_result, "outputs") or {}
                    if not isinstance(agent_outputs, dict):
                        agent_outputs = {}
                
                last_node_inputs = ordered_nodes[-1].get("inputs", {}) if ordered_nodes else {}
                head_branch = (
                    agent_outputs.get("branch")
                    or agent_outputs.get("targetBranch")
                    or ws.get("targetBranch")
                    or ws.get("branch")
                    or parameters.get("targetBranch")
                    or last_node_inputs.get("targetBranch")
                    or last_node_inputs.get("branch")
                    or ""
                )
                base_branch = (
                    ws.get("startingBranch")
                    or last_node_inputs.get("startingBranch")
                    or "main"
                )
                
                push_status = agent_outputs.get("push_status", "")
                if push_status == "no_commits":
                    self._get_logger().info(
                        "Skipping native PR creation: agent made no commits "
                        "on branch '%s'.",
                        agent_outputs.get("push_branch") or head_branch,
                    )
                elif not self._repo or not head_branch:
                    raise ValueError(
                        "publishMode 'pr' requested but no PR URL was returned, and missing repo/branch to create it natively"
                    )
                else:
                    self._get_logger().info(
                        f"Creating PR natively from {head_branch} into {base_branch} for repo {self._repo}"
                    )
                    create_payload = {
                        "repo": self._repo,
                        "head": head_branch,
                        "base": base_branch,
                        "title": self._title or "Automated changes by MoonMind",
                        "body": self._summary or "Automated changes by MoonMind.",
                    }
                    try:
                        create_result = await workflow.execute_activity(
                            "repo.create_pr",
                            create_payload,
                            start_to_close_timeout=timedelta(minutes=2),
                            task_queue=INTEGRATIONS_TASK_QUEUE,
                            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )
                        pr_url = self._get_from_result(create_result, "url")
                        created = self._get_from_result(create_result, "created")
                        summary = self._get_from_result(create_result, "summary") or ""
                        if pr_url:
                            pull_request_url = pr_url
                            self._get_logger().info(f"Natively created PR: {pull_request_url}")
                        else:
                            if "422" in summary:
                                self._get_logger().warning(
                                    f"Native PR creation failed with validation error (likely no commits): {summary}"
                                )
                            else:
                                raise ValueError(
                                    f"PR creation activity returned no URL"
                                    f" (created={created}): {summary}"
                                )
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
        for ws_key in ("repository", "repo", "startingBranch", "targetBranch", "branch"):
            ws_val = node_inputs.get(ws_key)
            if ws_val is not None:
                workspace_spec[ws_key] = ws_val

        parameters: dict[str, Any] = {}
        for param_key in ("model", "effort", "publishMode", "allowed_tools", "stepCount", "maxAttempts", "steps"):
            param_val = runtime_block.get(param_key) or node_inputs.get(param_key)
            if param_val is not None:
                parameters[param_key] = param_val
        profile_selector = self._build_profile_selector(
            agent_id=agent_id,
            runtime_block=runtime_block,
            node_inputs=node_inputs,
            parameters=parameters,
        )
        raw_metadata = runtime_block.get("metadata") or node_inputs.get("metadata")
        if isinstance(raw_metadata, Mapping):
            parameters["metadata"] = self._json_mapping(
                raw_metadata, path=f"node[{node_id}].metadata"
            )
        bundle_payload: dict[str, Any] = {}
        for bundle_key in (
            "bundleId",
            "bundledNodeIds",
            "bundleManifestRef",
            "bundleStrategy",
        ):
            if node_inputs.get(bundle_key) is not None:
                bundle_payload[bundle_key] = self._json_value(
                    node_inputs.get(bundle_key),
                    path=f"node[{node_id}].{bundle_key}",
                )
        if bundle_payload:
            metadata_payload = (
                parameters.get("metadata")
                if isinstance(parameters.get("metadata"), dict)
                else {}
            )
            moonmind_payload = (
                metadata_payload.get("moonmind")
                if isinstance(metadata_payload.get("moonmind"), dict)
                else {}
            )
            moonmind_payload.update(bundle_payload)
            metadata_payload["moonmind"] = moonmind_payload
            parameters["metadata"] = metadata_payload

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
            profile_selector=profile_selector,
        )

    def _build_profile_selector(
        self,
        *,
        agent_id: str,
        runtime_block: Mapping[str, Any],
        node_inputs: Mapping[str, Any],
        parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        selector_source = (
            runtime_block.get("profileSelector")
            or node_inputs.get("profileSelector")
            or {}
        )
        selector = dict(selector_source) if isinstance(selector_source, Mapping) else {}
        provider_id = str(selector.get("providerId") or "").strip().lower()
        if provider_id:
            return selector

        normalized_agent_id = _normalize_agent_runtime_id(agent_id)
        requested_model = str(parameters.get("model") or "").strip().lower()
        if normalized_agent_id in {"claude", "claude_code"} and requested_model.startswith(
            "minimax"
        ):
            selector["providerId"] = "minimax"

        return selector

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
    def _agent_kind_for_id(agent_id: str) -> str:
        """Derive ``agent_kind`` from ``agent_id``."""
        normalized_agent_id = _normalize_agent_runtime_id(agent_id)
        return "managed" if normalized_agent_id in _MANAGED_AGENT_IDS else "external"

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
                plan_payload = await execute_typed_activity(
                    "artifact.read",
                    ArtifactReadInput(
                        principal=self._principal(),
                        artifact_ref=plan_ref,
                    ),
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

        integration_parameters = dict(parameters)
        instructions_to_run = [None]

        integration_parameters.setdefault(
            "title", self._title or "MoonMind Integration"
        )
        if (
            self._integration == "jules"
            and len(step_instructions) > 1
            and workflow.patched("moonmind.run.jules_one_shot_bundle")
        ):
            workspace_spec = integration_parameters.get("workspaceSpec") or {}
            repo_value = (
                self._repo
                or workspace_spec.get("repository")
                or workspace_spec.get("repo")
                or ""
            )
            pseudo_nodes = []
            for index, instruction in enumerate(step_instructions, start=1):
                pseudo_nodes.append(
                    {
                        "id": f"integration-step-{index}",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "jules",
                            "version": "1.0",
                        },
                        "inputs": {
                            "instructions": instruction,
                            "repository": repo_value,
                            "repo": repo_value,
                            "startingBranch": workspace_spec.get("startingBranch")
                            or workspace_spec.get("branch")
                            or "main",
                            "targetBranch": workspace_spec.get("targetBranch"),
                            "publishMode": self._publish_mode(integration_parameters) or "none",
                        },
                    }
                )
            bundle_spec = build_bundle_spec(
                pseudo_nodes,
                workflow_id=workflow.info().workflow_id,
                workflow_run_id=workflow.info().run_id,
            )
            manifest_ref = await self._write_json_artifact(
                name="reports/jules_integration_bundle.json",
                payload=bundle_spec.manifest,
            )
            integration_parameters["description"] = bundle_spec.compiled_brief
            metadata = integration_parameters.setdefault("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("integration metadata must be an object when provided")

            moonmind_meta = metadata.setdefault("moonmind", {})
            if not isinstance(moonmind_meta, dict):
                moonmind_meta = {}
                metadata["moonmind"] = moonmind_meta

            moonmind_meta.update(
                {
                    "bundleId": bundle_spec.bundle_id,
                    "bundledNodeIds": list(bundle_spec.bundled_node_ids),
                    "bundleManifestRef": manifest_ref,
                    "bundleStrategy": "one_shot_jules",
                }
            )
            integration_parameters["metadata"] = metadata
        elif step_instructions:
            integration_parameters["description"] = step_instructions[0]
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
                        # Temporal records which branch was taken; replay without the patch marker
                        # must follow the pre-patch control flow (else branch + clear below).
                        if workflow.patched(INTEGRATION_POLL_LOOP_PATCH):
                            _poll_terminal = True
                        else:
                            self._resume_requested = True
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

            if not workflow.patched(INTEGRATION_POLL_LOOP_PATCH):
                self._resume_requested = False

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
                    # Jules creates the PR against startingBranch. If the
                    # caller wants a different merge destination we PATCH
                    # the PR base before merging.
                    ws = parameters.get("workspaceSpec") or {}
                    starting_branch = (
                        ws.get("startingBranch") or ws.get("branch") or "main"
                    )
                    base_override = (
                        parameters.get("publishBaseBranch")
                        or ws.get("publishBaseBranch")
                    )
                    # Only re-target when explicitly different from starting
                    effective_base = (
                        base_override
                        if base_override and base_override != starting_branch
                        else None
                    )

                    self._get_logger().info(
                        "Jules branch-publish: merging PR %s (base=%s)",
                        pr_url,
                        effective_base or starting_branch,
                    )
                    merge_payload = {"pr_url": pr_url}
                    if effective_base:
                        merge_payload["target_branch"] = effective_base

                    merge_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                        "repo.merge_pr"
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
                        raise ValueError(
                            f"Jules branch-publish: merge failed: {merge_summary}"
                        )
                else:
                    raise ValueError(
                        "Jules branch-publish: no PR URL found in result"
                    )
            except Exception as exc:
                raise ValueError(
                    f"Jules branch-publish auto-merge failed: {exc}"
                ) from exc

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
            generate_payload = {
                "principal": self._principal(),
                "workflow_id": workflow.info().workflow_id,
                "run_id": workflow.info().run_id,
                "repo": self._repo,
                "parameters": parameters,
            }
            if workflow.patched("idempotency_key_phase3"):
                generate_payload["idempotency_key"] = f"{workflow.info().workflow_id}_proposal_generate"

            candidates = await workflow.execute_activity(
                "proposal.generate",
                generate_payload,
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
            submit_payload = {
                "candidates": candidate_list,
                "policy": policy,
                "origin": origin,
                "principal": self._principal(),
            }
            if workflow.patched("idempotency_key_phase3"):
                submit_payload["idempotency_key"] = f"{workflow.info().workflow_id}_proposal_submit"

            submit_result = await workflow.execute_activity(
                "proposal.submit",
                submit_payload,
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
            await execute_typed_activity(
                "artifact.write_complete",
                ArtifactWriteCompleteInput(
                    principal=self._principal(),
                    artifact_id=self._get_from_result(artifact_ref, "artifact_id") or "",
                    payload=json.dumps(finish_summary).encode("utf-8"),
                    content_type="application/json",
                ),
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
        if self._scheduled_for:
            try:
                from datetime import datetime
                attributes["mm_scheduled_for"] = datetime.fromisoformat(self._scheduled_for.replace("Z", "+00:00"))
            except ValueError:
                self._get_logger().warning(
                    "Could not parse scheduled_for for search attribute: %s",
                    self._scheduled_for,
                )

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
        elif new_state in ("completed", "failed", "canceled", "timed_out"):
            # Child has reached a terminal state. If we have an assigned profile
            # slot, release it defensively. This is a fallback for cases where
            # the child fails to release the slot due to cancellation or other
            # issues.
            if workflow.patched(RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH):
                self._release_slot_defensive()

    @workflow.signal
    def profile_assigned(self, payload: dict) -> None:
        """Record that a child AgentRun has acquired an auth profile slot.

        The parent uses this to track which profile slot to release if the
        child exits in a terminal state without releasing it itself.
        """
        self._assigned_profile_id = payload.get("profile_id")
        self._assigned_child_workflow_id = payload.get("child_workflow_id")
        self._assigned_runtime_id = payload.get("runtime_id")
        self._get_logger().debug(
            "Child workflow %s assigned profile %s",
            self._assigned_child_workflow_id,
            self._assigned_profile_id,
        )

    def _release_slot_defensive(self) -> None:
        """Release the auth profile slot defensively when a child exits.

        This is called when a child AgentRun exits in a terminal state but
        may have failed to release its slot due to cancellation or other issues.
        """
        if not self._assigned_profile_id:
            return

        profile_id = self._assigned_profile_id
        child_wf_id = self._assigned_child_workflow_id or "unknown"

        self._get_logger().warning(
            "Defensively releasing auth profile slot %s for child %s",
            profile_id,
            child_wf_id,
        )

        # Use the runtime_id passed from the child via profile_assigned signal.
        # Fall back to inference from child_workflow_id if not available.
        runtime_id = self._assigned_runtime_id or self._infer_runtime_from_child(child_wf_id)
        if runtime_id:
            manager_id = f"auth-profile-manager:{runtime_id}"
            try:
                manager_handle = workflow.get_external_workflow_handle(manager_id)
                # Schedule the async signal without awaiting - best effort cleanup.
                # The manager's verify_lease_holders will reclaim the slot if this fails.
                asyncio.create_task(
                    self._signal_release_slot(
                        manager_handle, child_wf_id, profile_id
                    )
                )
            except Exception:
                self._get_logger().warning(
                    "Failed to schedule defensive release for profile %s", profile_id
                )

        # Clear the assignment
        self._assigned_profile_id = None
        self._assigned_child_workflow_id = None
        self._assigned_runtime_id = None

    async def _signal_release_slot(
        self, manager_handle: Any, child_workflow_id: str, profile_id: str
    ) -> None:
        """Send release_slot signal to the auth profile manager."""
        try:
            await manager_handle.signal(
                "release_slot",
                {
                    "requester_workflow_id": child_workflow_id,
                    "profile_id": profile_id,
                },
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to signal release_slot for profile %s: %s", profile_id, exc
            )

    def _infer_runtime_from_child(self, child_workflow_id: str) -> Optional[str]:
        """Infer runtime_id from child workflow ID pattern.

        Child workflow ID pattern: "<parent_workflow_id>:agent:<node_id>[:retry<N>]"
        The node_id typically indicates the agent kind (e.g., "jules", "gemini", "claude").
        """
        # Simple heuristic: extract from the workflow ID
        # Format: parent_id:agent:node_id[:retry<N>]
        parts = child_workflow_id.split(":")
        if len(parts) >= 3:
            node_id = parts[2]
            # Map common node IDs to runtime IDs
            mapping = {
                "jules": "jules",
                "gemini": "gemini_cli",
                "claude": "claude_code",
                "codex": "codex_cli",
            }
            return mapping.get(node_id)
        return None

    @workflow.update(name="Pause")
    def pause(self) -> None:
        self._paused = True
        self._waiting_reason = "Paused by user"
        self._update_search_attributes()

    @pause.validator
    def validate_pause(self) -> None:
        if self._paused:
            raise ValueError("Workflow is already paused.")
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot pause a completed workflow.")

    @workflow.update(name="Resume")
    async def resume(self, payload: dict[str, Any] | None = None) -> None:
        self._paused = False
        self._waiting_reason = None
        await self._forward_operator_message_to_active_child(payload)
        if self._awaiting_external:
            self._resume_requested = True
        self._update_search_attributes()

    @resume.validator
    def validate_resume(self, payload: dict[str, Any] | None = None) -> None:
        if not self._paused and not self._awaiting_external:
            raise ValueError("Workflow is not paused or awaiting external completion.")
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot resume a completed workflow.")

    @workflow.update(name="Approve")
    async def approve(self, payload: dict[str, Any] | None = None) -> None:
        self._approve_requested = True
        await self._forward_operator_message_to_active_child(payload)
        if self._awaiting_external:
            self._resume_requested = True

    @approve.validator
    def validate_approve(self, payload: dict[str, Any] | None = None) -> None:
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot approve a completed workflow.")

    @workflow.update(name="Cancel")
    def cancel(self, reason: Optional[str] = None) -> None:
        self._cancel_requested = True
        self._paused = False
        self._close_status = CLOSE_STATUS_CANCELED
        summary = f"Canceled: {reason}" if reason else "Canceled."
        self._set_state(STATE_CANCELED, summary=summary)

    @cancel.validator
    def validate_cancel(self, reason: Optional[str] = None) -> None:
        if self._cancel_requested:
            raise ValueError("Workflow is already canceled.")
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot cancel a completed workflow.")

    @workflow.signal(name="reschedule")
    def reschedule(self, new_scheduled_for: str) -> None:
        self._scheduled_for = new_scheduled_for
        self._reschedule_requested = True
        self._set_state(STATE_SCHEDULED, summary=f"Execution rescheduled for {self._scheduled_for}.")
        self._update_search_attributes()

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

    @staticmethod
    def _extract_clarification_message(payload: Mapping[str, Any] | None) -> str | None:
        if not isinstance(payload, Mapping):
            return None
        candidate = (
            payload.get("message")
            or payload.get("clarification_response")
            or payload.get("clarificationResponse")
        )
        if candidate is None:
            parameters_patch = payload.get("parameters_patch") or payload.get(
                "parametersPatch"
            )
            if isinstance(parameters_patch, Mapping):
                candidate = (
                    parameters_patch.get("message")
                    or parameters_patch.get("clarification_response")
                    or parameters_patch.get("clarificationResponse")
                )
        if candidate is None:
            return None
        normalized = str(candidate).strip()
        return normalized or None

    async def _forward_operator_message_to_active_child(
        self, payload: Mapping[str, Any] | None
    ) -> bool:
        message = self._extract_clarification_message(payload)
        if not message:
            return False
        if not self._active_agent_child_workflow_id:
            return False
        if str(self._active_agent_id or "").strip().lower() not in {"jules", "jules_api"}:
            return False
        handle = workflow.get_external_workflow_handle(
            self._active_agent_child_workflow_id
        )
        await handle.signal("operator_message", {"message": message})
        self._summary = "Operator clarification sent to Jules."
        self._update_memo()
        return True

    @workflow.update
    def update_title(self, new_title: str) -> None:
        self._title = new_title

    @workflow.update
    def update_parameters(self, new_parameters: dict[str, Any]) -> None:
        self._parameters_updated = True
        self._updated_parameters = new_parameters

    @workflow.update(name="UpdateInputs")
    async def update_inputs(
        self, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload = payload or {}
        parameters_patch = payload.get("parameters_patch") or payload.get(
            "parametersPatch"
        )
        if isinstance(parameters_patch, Mapping):
            self._parameters_updated = True
            self._updated_parameters = dict(parameters_patch)
        forwarded = await self._forward_operator_message_to_active_child(payload)
        return {"accepted": True, "forwardedOperatorMessage": forwarded}

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "paused": self._paused,
            "cancel_requested": self._cancel_requested,
            "canceling": self._cancel_requested and self._state != STATE_CANCELED,
            "step_count": self._step_count,
            "summary": self._summary,
            "awaiting_external": self._awaiting_external,
            "waiting_reason": self._waiting_reason,
        }
