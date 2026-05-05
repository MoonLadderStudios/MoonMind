import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Optional, TypedDict

from temporalio import exceptions, workflow
from temporalio.common import RetryPolicy, SearchAttributeKey, SearchAttributePair
from temporalio.exceptions import CancelledError
from temporalio.workflow import ActivityCancellationType, ChildWorkflowCancellationType

with workflow.unsafe.imports_passed_through():
    from collections.abc import Mapping as WorkflowMapping

    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
    )
    from moonmind.schemas.agent_skill_models import SkillSelector
    from moonmind.schemas.managed_session_models import (
        CodexManagedSessionBinding,
        CodexManagedSessionSnapshot,
        CodexManagedSessionWorkflowInput,
        TerminateCodexManagedSessionRequest,
        canonical_codex_managed_runtime_id,
    )
    from moonmind.schemas.temporal_activity_models import (
        ArtifactReadInput,
        ArtifactWriteCompleteInput,
        DependencyStatusSnapshotInput,
        ExecutionTerminalStateInput,
        PlanGenerateInput,
    )
    from moonmind.workflows.temporal.jules_bundle import (
        build_bundle_spec,
        eligible_for_bundle,
        is_jules_agent_runtime_node,
    )
    from moonmind.workflows.tasks.routing import _coerce_bool
    from moonmind.workflows.agent_skills.selection import selected_agent_skill
    from moonmind.config.settings import settings
    from moonmind.utils.logging import scrub_github_tokens
    from moonmind.workflows.temporal.jira_agent_skills import (
        JIRA_AGENT_SKILLS,
        JIRA_BACKED_AGENT_SKILLS,
    )
    from moonmind.workflows.temporal.typed_execution import execute_typed_activity
    from moonmind.workflows.tasks.task_contract import (
        build_effective_task_skill_selectors,
    )
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        workflow_id_for_runtime,
    )
    from moonmind.schemas.temporal_models import (
        DependencyResolvedSignalPayload,
        ExecutionProgressModel,
        StepLedgerSnapshotModel,
        normalize_dependency_ids,
    )

from moonmind.workflows.skills.skill_plan_contracts import parse_plan_definition
from moonmind.workflows.skills.approval_policy import (
    ReviewRequest,
    build_feedback_input,
    build_feedback_instruction,
    parse_review_verdict,
)
from moonmind.workflows.skills.skill_registry import parse_skill_registry
from moonmind.workflows.temporal.step_ledger import (
    build_initial_step_rows,
    build_progress_summary,
    build_step_ledger_snapshot,
    refresh_ready_steps,
    upsert_step_check,
    update_step_row,
)
from moonmind.workflows.temporal.completion_summary import (
    is_generic_completion_summary,
)
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
    TemporalActivityRoute,
    build_default_activity_catalog,
)

class DependencyFailureError(ValueError):
    """Structured dependency gate failure."""

    def __init__(self, message: str, *, detail: dict[str, Any]) -> None:
        super().__init__(message)
        self.detail = detail

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)
DEPENDENCY_RECONCILE_INTERVAL = timedelta(seconds=30)
_TERMINAL_LAST_ERROR_UNSET = object()

DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()
_PR_OPTIONAL_AGENT_SKILLS = JIRA_AGENT_SKILLS
_JIRA_ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_JIRA_BACKED_AGENT_SKILLS = JIRA_BACKED_AGENT_SKILLS

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
    mergeAutomationDisposition: str
    headSha: str

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
DEPENDENCY_RESOLUTION_NOT_APPLICABLE = "not_applicable"
DEPENDENCY_RESOLUTION_SATISFIED = "satisfied"
DEPENDENCY_RESOLUTION_FAILED = "dependency_failed"
DEPENDENCY_RESOLUTION_BYPASSED = "bypassed"
DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE = "manual_override"
MERGE_AUTOMATION_SUCCESS_STATUSES = frozenset({"merged", "already_merged"})
MERGE_AUTOMATION_FAILURE_STATUSES = frozenset({"blocked", "failed", "expired"})
MERGE_AUTOMATION_CANCELED_STATUS = "canceled"
MERGE_AUTOMATION_TERMINAL_STATUSES = (
    MERGE_AUTOMATION_SUCCESS_STATUSES
    | MERGE_AUTOMATION_FAILURE_STATUSES
    | frozenset({MERGE_AUTOMATION_CANCELED_STATUS})
)
OWNER_ID_SEARCH_ATTRIBUTE = "mm_owner_id"
OWNER_TYPE_SEARCH_ATTRIBUTE = "mm_owner_type"
_GITHUB_PR_URL_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+",
    re.IGNORECASE,
)
_JSON_OBJECT_CODE_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE | re.DOTALL,
)
# Replay-stable `workflow.patched` id for integration status polling terminal handling.
INTEGRATION_POLL_LOOP_PATCH = "refactor-loop-1.2"
# Replay-stable patch id for parent-initiated defensive slot release on child terminal state.
RUN_DEFENSIVE_SLOT_RELEASE_ON_CHILD_TERMINAL_PATCH = "run-defensive-slot-release-1"
# Replay-stable patch id for task-scoped Codex terminate activity+signal finalization.
RUN_TASK_SCOPED_SESSION_TERMINATION_PATCH = "run-task-scoped-session-termination-v1"
RUN_BLOCKED_OUTCOME_SHORT_CIRCUIT_PATCH = "run-blocked-outcome-short-circuit-v1"
# Replay-stable patch id for the v2 task-scoped Codex termination path. The
# identifier says "update" for in-flight history continuity, but current
# Temporal external workflow handles expose the session control surface by signal.
RUN_TASK_SCOPED_SESSION_TERMINATION_UPDATE_PATCH = "run-task-scoped-session-termination-v2"
# Replay-stable patch id for task-scoped Codex termination through the
# AgentSession update handler. This path executes the remote terminate activity.
RUN_TASK_SCOPED_SESSION_TERMINATION_UPDATE_EXECUTE_PATCH = (
    "run-task-scoped-session-termination-v3"
)
# Replay-stable patch id for skipping registry reads on agent-runtime-only plans.
RUN_CONDITIONAL_REGISTRY_READ_PATCH = "run-conditional-registry-read-v1"
RUN_PROVIDER_PROFILE_MANAGER_ID_PATCH = "provider-profile-manager-id-v1"
DEPENDENCY_GATE_PATCH = "dependency-gate-v1"
NATIVE_PR_CREATE_PAYLOAD_PATCH = "native-pr-create-payload-v1"
NATIVE_PR_BRANCH_DEFAULTS_PATCH = "native-pr-branch-defaults-v1"
NATIVE_PR_PUSH_STATUS_GATE_PATCH = "native-pr-push-status-gate-v1"
RUN_WORKFLOW_PUBLISH_OUTCOME_PATCH = "run-workflow-publish-outcome-v1"
RUN_FETCH_PROFILE_SNAPSHOTS_PATCH = "fetch-profile-snapshots-v1"
RUN_SLOT_CONTINUITY_PATCH = "run-slot-continuity-v1"
RUN_TERMINAL_STATE_ACTIVITY_PATCH = "run-terminal-state-activity-v1"
RUN_PAUSE_SAFE_BOUNDARIES_PATCH = "run-pause-safe-boundaries-v1"
_PROFILE_SYNC_RUNTIME_IDS = ("codex_cli", "claude_code", "gemini_cli")
_MANAGED_AGENT_IDS = frozenset(
    {"gemini_cli", "gemini_cli", "claude", "claude_code", "codex", "codex_cli"}
)

def _normalize_agent_runtime_id(agent_id: str) -> str:
    """Normalize runtime identifiers for managed/external dispatch decisions."""

    return str(agent_id).strip().lower().replace("-", "_")

def _legacy_manager_workflow_id(runtime_id: str) -> str:
    # Preserve legacy workflow IDs for in-flight histories. New executions use
    # provider-profile-manager IDs once the replay patch is active.
    return f"auth-profile-manager:{runtime_id}"

@workflow.defn(name="MoonMind.Run")
class MoonMindRunWorkflow:
    def _manager_workflow_id(self, runtime_id: str) -> str:
        if workflow.patched(RUN_PROVIDER_PROFILE_MANAGER_ID_PATCH):
            return workflow_id_for_runtime(runtime_id)
        return _legacy_manager_workflow_id(runtime_id)

    def _get_logger(self) -> logging.LoggerAdapter | logging.Logger:
        try:
            info = workflow.info()
        except Exception:
            logging.getLogger(__name__).exception(
                "Error getting workflow info in _get_logger"
            )
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
            logging.getLogger(__name__).exception(
                "Error checking logger capabilities in _get_logger"
            )
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    def __init__(self) -> None:
        self._state = STATE_INITIALIZING
        self._owner_type: Optional[str] = None
        self._owner_id: Optional[str] = None
        self._workflow_type: Optional[str] = None
        self._entry: Optional[str] = None
        self._repo: Optional[str] = None
        self._integration: Optional[str] = None
        self._target_runtime: Optional[str] = None
        self._target_skill: Optional[str] = None
        self._close_status: Optional[str] = None
        self._title: Optional[str] = None
        self._summary: str = "Execution initialized."
        self._correlation_id: Optional[str] = None
        self._pull_request_url: Optional[str] = None
        self._publish_status: Optional[str] = None
        self._publish_reason: Optional[str] = None
        self._publish_context: dict[str, Any] = {}
        self._operator_summary: Optional[str] = None
        self._last_step_id: Optional[str] = None
        self._last_step_summary: Optional[str] = None
        self._plan_blocked_message: Optional[str] = None
        self._last_diagnostics_ref: Optional[str] = None
        self._merge_automation_disposition: Optional[str] = None
        self._merge_automation_head_sha: Optional[str] = None
        self._report_created: bool = False
        self._report_ref: Optional[str] = None
        self._declared_dependencies: list[str] = []
        self._dependency_wait_occurred: bool = False
        self._dependency_wait_duration_ms: int = 0
        self._dependency_resolution: str = DEPENDENCY_RESOLUTION_NOT_APPLICABLE
        self._failed_dependency_id: str | None = None
        self._dependency_outcomes_by_id: dict[str, dict[str, Any]] = {}
        self._unresolved_dependency_ids: set[str] = set()
        self._dependency_wait_started_at: datetime | None = None
        self._dependency_failure: dict[str, Any] | None = None
        self._dependency_manual_override_unresolved_count: int = 0

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
        self._codex_session_handle: Any | None = None
        self._codex_session_binding: CodexManagedSessionBinding | None = None
        self._step_ledger_rows: list[dict[str, Any]] = []
        self._progress_snapshot: dict[str, Any] = {
            "total": 0,
            "pending": 0,
            "ready": 0,
            "running": 0,
            "awaitingExternal": 0,
            "reviewing": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "canceled": 0,
            "currentStepTitle": None,
            "updatedAt": "1970-01-01T00:00:00+00:00",
        }

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
        artifact_create_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "artifact.create"
        )
        artifact_write_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "artifact.write_complete"
        )
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
            while cursor < len(ordered_nodes) and eligible_for_bundle(
                group[-1], ordered_nodes[cursor]
            ):
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

    def _plan_dependency_map(
        self,
        *,
        ordered_nodes: list[dict[str, Any]],
        edges: tuple[Any, ...],
    ) -> dict[str, list[str]]:
        dependency_map = {
            str(node.get("id") or "").strip(): set()
            for node in ordered_nodes
            if str(node.get("id") or "").strip()
        }
        bundle_id_by_member_id: dict[str, str] = {}
        for node in ordered_nodes:
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue
            node_inputs = node.get("inputs")
            candidates: list[Any] = [
                node.get("bundledNodeIds"),
                node.get("bundled_node_ids"),
            ]
            if isinstance(node_inputs, Mapping):
                candidates.extend(
                    [
                        node_inputs.get("bundledNodeIds"),
                        node_inputs.get("bundled_node_ids"),
                        node_inputs.get("bundleNodeIds"),
                        node_inputs.get("bundle_node_ids"),
                    ]
                )
            for candidate in candidates:
                if not isinstance(candidate, (list, tuple, set)):
                    continue
                for member_id in candidate:
                    normalized_member_id = str(member_id or "").strip()
                    if normalized_member_id and normalized_member_id != node_id:
                        bundle_id_by_member_id[normalized_member_id] = node_id
        for edge in edges:
            from_node = str(getattr(edge, "from_node", "") or "").strip()
            to_node = str(getattr(edge, "to_node", "") or "").strip()
            if not from_node or not to_node:
                continue
            mapped_from_node = bundle_id_by_member_id.get(from_node, from_node)
            mapped_to_node = bundle_id_by_member_id.get(to_node, to_node)
            if mapped_from_node == mapped_to_node:
                continue
            if (
                mapped_to_node not in dependency_map
                or mapped_from_node not in dependency_map
            ):
                continue
            dependency_map[mapped_to_node].add(mapped_from_node)
        return {
            node_id: sorted(dependencies)
            for node_id, dependencies in dependency_map.items()
        }

    def _try_update_step_row(
        self,
        logical_step_id: str,
        **update_kwargs: Any,
    ) -> bool:
        try:
            update_step_row(
                self._step_ledger_rows,
                logical_step_id,
                **update_kwargs,
            )
        except KeyError:
            self._get_logger().warning(
                "Skipping step-ledger update for unknown logical step id %s",
                logical_step_id,
                extra={
                    "event": "step_ledger_unknown_step",
                    "logical_step_id": logical_step_id,
                },
            )
            return False
        return True

    def _sync_progress_snapshot(self, *, updated_at: datetime) -> None:
        self._progress_snapshot = build_progress_summary(
            self._step_ledger_rows,
            updated_at=updated_at,
        )

    def _initialize_step_ledger(
        self,
        *,
        ordered_nodes: list[dict[str, Any]],
        dependency_map: dict[str, list[str]],
        updated_at: datetime,
    ) -> None:
        self._step_ledger_rows = build_initial_step_rows(
            ordered_nodes=ordered_nodes,
            dependency_map=dependency_map,
            updated_at=updated_at,
        )
        self._sync_progress_snapshot(updated_at=updated_at)

    def _mark_step_running(
        self,
        logical_step_id: str,
        *,
        updated_at: datetime,
        summary: str | None = None,
    ) -> None:
        if not self._try_update_step_row(
            logical_step_id,
            updated_at=updated_at,
            status="running",
            summary=summary,
            waiting_reason=None,
            attention_required=False,
            increment_attempt=True,
            set_started_at=True,
        ):
            return
        self._sync_progress_snapshot(updated_at=updated_at)

    def _mark_step_waiting(
        self,
        logical_step_id: str,
        *,
        status: str,
        updated_at: datetime,
        waiting_reason: str | None,
        summary: str | None = None,
        attention_required: bool = False,
        refs: Mapping[str, Any] | None = None,
        artifacts: Mapping[str, Any] | None = None,
    ) -> None:
        update_kwargs: dict[str, Any] = {
            "updated_at": updated_at,
            "status": status,
            "summary": summary,
            "waiting_reason": waiting_reason,
            "attention_required": attention_required,
        }
        if refs is not None:
            update_kwargs["refs"] = refs
        if artifacts is not None:
            update_kwargs["artifacts"] = artifacts
        if not self._try_update_step_row(logical_step_id, **update_kwargs):
            return
        self._sync_progress_snapshot(updated_at=updated_at)

    def _mark_step_terminal(
        self,
        logical_step_id: str,
        *,
        status: str,
        updated_at: datetime,
        summary: str | None = None,
        last_error: str | None | object = _TERMINAL_LAST_ERROR_UNSET,
    ) -> None:
        update_kwargs: dict[str, Any] = {
            "updated_at": updated_at,
            "status": status,
            "summary": summary,
        }
        if last_error is not _TERMINAL_LAST_ERROR_UNSET:
            update_kwargs["last_error"] = last_error
        if not self._try_update_step_row(
            logical_step_id,
            **update_kwargs,
        ):
            return
        self._sync_progress_snapshot(updated_at=updated_at)

    def _upsert_step_check(
        self,
        logical_step_id: str,
        *,
        kind: str,
        status: str,
        summary: str | None = None,
        retry_count: int = 0,
        artifact_ref: str | None = None,
    ) -> bool:
        try:
            upsert_step_check(
                self._step_ledger_rows,
                logical_step_id,
                kind=kind,
                status=status,
                summary=summary,
                retry_count=retry_count,
                artifact_ref=artifact_ref,
            )
        except KeyError:
            self._get_logger().warning(
                "Skipping step-check update for unknown logical step id %s",
                logical_step_id,
                extra={
                    "event": "step_check_unknown_step",
                    "logical_step_id": logical_step_id,
                },
            )
            return False
        return True

    def _step_attempt_for(self, logical_step_id: str) -> int | None:
        for row in self._step_ledger_rows:
            if row.get("logicalStepId") == logical_step_id:
                attempt = row.get("attempt")
                if isinstance(attempt, bool):
                    return None
                if isinstance(attempt, (int, float)):
                    return int(attempt)
                return None
        return None

    def _record_step_result_evidence(
        self,
        logical_step_id: str,
        *,
        execution_result: Any,
        updated_at: datetime,
    ) -> None:
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return

        def _output_ref(*keys: str) -> str | None:
            for key in keys:
                value = outputs.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        def _output_refs_map() -> dict[str, str]:
            value = outputs.get("outputRefs") or outputs.get("output_refs")
            if not isinstance(value, Mapping):
                return {}
            refs: dict[str, str] = {}
            for raw_key, raw_value in value.items():
                if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                    continue
                key = raw_key.strip()
                ref = raw_value.strip()
                if key and ref:
                    refs[key] = ref
            return refs

        def _output_ref_list(*keys: str) -> list[str]:
            for key in keys:
                value = outputs.get(key)
                if isinstance(value, (list, tuple)):
                    return [
                        item.strip()
                        for item in value
                        if isinstance(item, str) and item.strip()
                    ]
                if isinstance(value, Mapping):
                    return [
                        item.strip()
                        for item in value.values()
                        if isinstance(item, str) and item.strip()
                    ]
            return []

        output_refs_by_class = _output_refs_map()

        def _artifact_class_ref(*classes: str) -> str | None:
            for artifact_class in classes:
                value = output_refs_by_class.get(artifact_class)
                if value:
                    return value
            return None

        output_refs = _output_ref_list("outputRefs", "output_refs")
        agent_result_ref = _output_ref(
            "outputAgentResultRef",
            "output_agent_result_ref",
        )
        workload_metadata = outputs.get("workloadMetadata") or outputs.get(
            "workload_metadata"
        )
        workload_result = outputs.get("workloadResult") or outputs.get(
            "workload_result"
        )
        if not isinstance(workload_metadata, Mapping) and isinstance(
            workload_result,
            Mapping,
        ):
            result_metadata = workload_result.get("metadata")
            if isinstance(result_metadata, Mapping):
                workload_metadata = result_metadata.get("workload")
        if not isinstance(workload_metadata, Mapping):
            workload_metadata = None

        refs = {
            "childWorkflowId": _output_ref("childWorkflowId", "child_workflow_id"),
            "childRunId": _output_ref("childRunId", "child_run_id"),
            "taskRunId": _output_ref("taskRunId", "task_run_id")
            or (
                str(workload_metadata.get("taskRunId")).strip()
                if isinstance(workload_metadata, Mapping)
                and workload_metadata.get("taskRunId") is not None
                else None
            ),
        }
        artifacts = {
            "outputSummary": _output_ref("outputSummaryRef", "output_summary_ref")
            or _artifact_class_ref("output.summary"),
            "outputPrimary": _output_ref(
                "outputPrimaryRef",
                "output_primary_ref",
            )
            or _artifact_class_ref("output.primary"),
            "runtimeStdout": _output_ref(
                "stdoutArtifactRef",
                "stdout_artifact_ref",
                "stdoutRef",
                "stdout_ref",
            )
            or _artifact_class_ref("runtime.stdout"),
            "runtimeStderr": _output_ref(
                "stderrArtifactRef",
                "stderr_artifact_ref",
                "stderrRef",
                "stderr_ref",
            )
            or _artifact_class_ref("runtime.stderr"),
            "runtimeMergedLogs": _output_ref(
                "mergedLogArtifactRef",
                "merged_log_artifact_ref",
                "logArtifactRef",
                "log_artifact_ref",
            ),
            "runtimeDiagnostics": _output_ref("diagnosticsRef", "diagnostics_ref")
            or _artifact_class_ref("runtime.diagnostics"),
            "providerSnapshot": _output_ref(
                "providerSnapshotRef",
                "provider_snapshot_ref",
            ),
        }

        if artifacts["outputPrimary"] is None:
            reserved_refs = {
                value
                for value in artifacts.values()
                if isinstance(value, str) and value.strip()
            }
            fallback_primary = next(
                (ref for ref in output_refs if ref not in reserved_refs),
                None,
            )
            if fallback_primary is not None:
                artifacts["outputPrimary"] = fallback_primary
            elif agent_result_ref is not None:
                artifacts["outputPrimary"] = agent_result_ref

        if not self._try_update_step_row(
            logical_step_id,
            updated_at=updated_at,
            refs=refs,
            artifacts=artifacts,
            workload=workload_metadata,
        ):
            return
        self._sync_progress_snapshot(updated_at=updated_at)

    def _refresh_step_readiness(self, *, updated_at: datetime) -> None:
        refresh_ready_steps(self._step_ledger_rows, updated_at=updated_at)
        self._sync_progress_snapshot(updated_at=updated_at)

    def _bounded_review_summary(self, value: Any, *, fallback: str) -> str:
        summary = self._coerce_text(value, max_chars=240)
        if summary:
            return summary
        return fallback

    def _check_status_for_review_verdict(self, verdict: str) -> str:
        normalized = str(verdict or "").strip().upper()
        if normalized == "PASS":
            return "passed"
        if normalized == "FAIL":
            return "failed"
        return "inconclusive"

    def _accepted_review_summary(self, verdict: str, *, retry_count: int) -> str:
        normalized = str(verdict or "").strip().upper()
        if normalized == "INCONCLUSIVE":
            return "Review inconclusive; accepted execution"
        if retry_count > 0:
            retry_label = "retry" if retry_count == 1 else "retries"
            return f"Approved after {retry_count} {retry_label}"
        return "Approved by structured review"

    def _review_gate_active(
        self,
        *,
        approval_policy: Any,
        tool_type: str,
        tool_name: str,
    ) -> bool:
        if approval_policy is None or not getattr(approval_policy, "enabled", False):
            return False
        skip_tool_types = {
            str(value).strip().lower()
            for value in getattr(approval_policy, "skip_tool_types", ())
            if str(value).strip()
        }
        normalized_tool_type = str(tool_type or "").strip().lower()
        normalized_tool_name = str(tool_name or "").strip().lower()
        return (
            normalized_tool_type not in skip_tool_types
            and normalized_tool_name not in skip_tool_types
        )

    def _inject_review_feedback_into_inputs(
        self,
        *,
        tool_type: str,
        original_inputs: Mapping[str, Any],
        attempt: int,
        feedback: str,
        issues: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        merged_inputs = build_feedback_input(
            original_inputs,
            attempt,
            feedback,
            issues,
        )
        if tool_type == "agent_runtime":
            for key in (
                "instructions",
                "instructionRef",
                "instruction",
                "instructionsText",
                "instructions_text",
            ):
                instruction = merged_inputs.get(key)
                if isinstance(instruction, str) and instruction.strip():
                    merged_inputs[key] = build_feedback_instruction(
                        instruction,
                        attempt,
                        feedback,
                    )
                    break
        return merged_inputs

    def _dependency_ids_from_parameters(self, parameters: Mapping[str, Any]) -> list[str]:
        task_payload = parameters.get("task")
        if task_payload is None:
            return []
        if not isinstance(task_payload, Mapping):
            raise ValueError("initialParameters.task must be an object when provided")

        depends_on = task_payload.get("dependsOn")
        if depends_on is None:
            return []
        if not isinstance(depends_on, list):
            raise ValueError(
                "initialParameters.task.dependsOn must be a list when provided"
            )

        normalized: list[str] = []
        for dep_id in depends_on:
            if not isinstance(dep_id, str):
                raise ValueError(
                    "initialParameters.task.dependsOn entries must be strings"
                )
            candidate = dep_id.strip()
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return normalized

    def _dependency_failure_category(
        self,
        *,
        terminal_state: str | None,
        close_status: str | None,
    ) -> str:
        normalized_close_status = str(close_status or "").strip().lower()
        if normalized_close_status == "canceled":
            return "dependency_canceled"
        if normalized_close_status == "terminated":
            return "dependency_terminated"
        if normalized_close_status == "timed_out":
            return "dependency_timed_out"
        if normalized_close_status == "failed":
            return "dependency_failed"
        normalized_state = str(terminal_state or "").strip().lower()
        if normalized_state == STATE_CANCELED:
            return "dependency_canceled"
        if normalized_state == "terminated":
            return "dependency_terminated"
        if normalized_state == "timed_out":
            return "dependency_timed_out"
        return "dependency_failed"

    def _update_dependency_wait_duration(self) -> None:
        if self._dependency_wait_started_at is None:
            return
        elapsed = workflow.now() - self._dependency_wait_started_at
        self._dependency_wait_duration_ms = max(
            0, int(elapsed.total_seconds() * 1000)
        )

    def _dependency_outcomes(self) -> list[dict[str, Any]]:
        return [
            dict(self._dependency_outcomes_by_id[workflow_id])
            for workflow_id in self._declared_dependencies
            if workflow_id in self._dependency_outcomes_by_id
        ]

    def _record_dependency_outcome(
        self,
        *,
        prerequisite_workflow_id: str,
        terminal_state: str,
        close_status: str | None,
        resolved_at: str,
        failure_category: str | None,
        message: str | None,
    ) -> None:
        if prerequisite_workflow_id not in self._declared_dependencies:
            return
        if prerequisite_workflow_id in self._dependency_outcomes_by_id:
            return

        outcome = {
            "workflowId": prerequisite_workflow_id,
            "terminalState": terminal_state,
            "closeStatus": close_status,
            "resolvedAt": resolved_at,
            "failureCategory": failure_category,
            "message": message,
        }
        self._dependency_outcomes_by_id[prerequisite_workflow_id] = outcome

        if terminal_state == STATE_COMPLETED:
            self._unresolved_dependency_ids.discard(prerequisite_workflow_id)
            if not self._unresolved_dependency_ids and self._dependency_failure is None:
                self._dependency_resolution = DEPENDENCY_RESOLUTION_SATISFIED
            return

        self._failed_dependency_id = prerequisite_workflow_id
        self._dependency_resolution = DEPENDENCY_RESOLUTION_FAILED
        self._dependency_failure = {
            "failedDependencyId": prerequisite_workflow_id,
            "terminalState": terminal_state,
            "closeStatus": close_status,
            "failureCategory": failure_category,
            "message": message,
        }
        self._unresolved_dependency_ids.discard(prerequisite_workflow_id)

    def _record_missing_dependency(self, prerequisite_workflow_id: str) -> None:
        self._record_dependency_outcome(
            prerequisite_workflow_id=prerequisite_workflow_id,
            terminal_state="unknown",
            close_status=None,
            resolved_at=workflow.now().isoformat(),
            failure_category="dependency_unresolved",
            message=(
                f"Prerequisite execution '{prerequisite_workflow_id}' could not be "
                "reconciled from durable execution state."
            ),
        )

    def _record_dependency_signal(self, payload: dict[str, Any]) -> None:
        try:
            signal = DependencyResolvedSignalPayload.model_validate(payload)
        except Exception:
            self._get_logger().warning(
                "Ignoring invalid DependencyResolved payload: %r",
                payload,
            )
            return

        prerequisite_workflow_id = signal.prerequisite_workflow_id
        if prerequisite_workflow_id not in self._declared_dependencies:
            self._get_logger().warning(
                "Ignoring DependencyResolved signal for undeclared prerequisite %s",
                prerequisite_workflow_id,
                extra={
                    "event": "dependency_signal_ignored_undeclared",
                },
            )
            return

        # Normalize "succeeded" to "completed" so the outcome recorder
        # (which only treats "completed" as success) can satisfy the gate.
        terminal_state = signal.terminal_state
        if terminal_state == "succeeded":
            terminal_state = "completed"

        is_terminal_failure = terminal_state != "completed"
        if is_terminal_failure:
            self._get_logger().warning(
                "DependencyResolved signal indicates non-success terminal state %s for %s",
                terminal_state,
                prerequisite_workflow_id,
                extra={
                    "event": "dependency_signal_failure",
                    "prerequisite_workflow_id": prerequisite_workflow_id,
                    "terminal_state": terminal_state,
                    "close_status": signal.close_status,
                    "failure_category": signal.failure_category,
                },
            )
        else:
            self._get_logger().info(
                "DependencyResolved signal received for %s",
                prerequisite_workflow_id,
                extra={
                    "event": "dependency_signal_received",
                    "prerequisite_workflow_id": prerequisite_workflow_id,
                    "terminal_state": terminal_state,
                    "close_status": signal.close_status,
                },
            )

        self._record_dependency_outcome(
            prerequisite_workflow_id=prerequisite_workflow_id,
            terminal_state=terminal_state,
            close_status=signal.close_status,
            resolved_at=signal.resolved_at.isoformat(),
            failure_category=signal.failure_category,
            message=signal.message,
        )
        self._update_search_attributes()
        self._update_memo()

    def _bypass_dependencies(self, payload: dict[str, Any] | None = None) -> None:
        if self._state != STATE_WAITING_ON_DEPENDENCIES:
            self._get_logger().warning(
                "Ignoring BypassDependencies signal outside dependency wait",
                extra={
                    "event": "dependency_bypass_ignored",
                    "state": self._state,
                },
            )
            return

        envelope = payload if isinstance(payload, dict) else {}
        signal_payload = envelope.get("payload")
        details = signal_payload if isinstance(signal_payload, dict) else envelope
        reason = str(details.get("reason") or "").strip()
        if not reason:
            reason = "Dependency wait bypassed by operator."

        self._update_dependency_wait_duration()
        bypassed_at = workflow.now().isoformat().replace("+00:00", "Z")
        for prerequisite_workflow_id in list(self._unresolved_dependency_ids):
            self._dependency_outcomes_by_id[prerequisite_workflow_id] = {
                "workflowId": prerequisite_workflow_id,
                "terminalState": "bypassed",
                "closeStatus": None,
                "resolvedAt": bypassed_at,
                "failureCategory": None,
                "message": reason,
            }

        self._unresolved_dependency_ids.clear()
        self._dependency_failure = None
        self._failed_dependency_id = None
        self._dependency_resolution = DEPENDENCY_RESOLUTION_BYPASSED
        self._summary = reason
        self._get_logger().warning(
            "Dependency gate bypassed by operator",
            extra={
                "event": "dependency_gate_bypassed",
                "dependency_count": len(self._declared_dependencies),
                "wait_duration_ms": self._dependency_wait_duration_ms,
            },
        )
        self._update_search_attributes()
        self._update_memo()

    async def _reconcile_dependencies(self, dependency_ids: list[str]) -> None:
        if not dependency_ids:
            return

        dependency_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "execution.dependency_status_snapshot"
        )
        snapshot = await execute_typed_activity(
            "execution.dependency_status_snapshot",
            DependencyStatusSnapshotInput(workflowIds=dependency_ids),
            **self._execute_kwargs_for_route(dependency_route),
        )
        if not isinstance(snapshot, Mapping):
            raise ValueError(
                "execution.dependency_status_snapshot must return a JSON object"
            )

        for dependency_id in dependency_ids:
            raw_entry = snapshot.get(dependency_id)
            if not isinstance(raw_entry, Mapping):
                self._get_logger().warning(
                    "Dependency reconciliation: prerequisite %s not found in snapshot",
                    dependency_id,
                    extra={
                        "event": "dependency_reconciliation_mismatch",
                        "prerequisite_workflow_id": dependency_id,
                        "mismatch_type": "missing_from_snapshot",
                    },
                )
                self._record_missing_dependency(dependency_id)
                continue

            state = str(raw_entry.get("state") or "").strip()
            close_status = str(raw_entry.get("closeStatus") or "").strip() or None
            workflow_type = str(raw_entry.get("workflowType") or "").strip() or None
            message = str(raw_entry.get("summary") or "").strip() or None

            if workflow_type and workflow_type != WORKFLOW_NAME:
                self._record_dependency_outcome(
                    prerequisite_workflow_id=dependency_id,
                    terminal_state="failed",
                    close_status=close_status,
                    resolved_at=workflow.now().isoformat(),
                    failure_category="dependency_unsupported_workflow_type",
                    message=(
                        f"Prerequisite execution '{dependency_id}' resolved to "
                        f"unsupported workflow type '{workflow_type}'."
                    ),
                )
                continue

            if state == STATE_COMPLETED:
                self._record_dependency_outcome(
                    prerequisite_workflow_id=dependency_id,
                    terminal_state=state,
                    close_status=close_status or CLOSE_STATUS_COMPLETED,
                    resolved_at=workflow.now().isoformat(),
                    failure_category=None,
                    message=message,
                )
                continue

            if state in {STATE_FAILED, STATE_CANCELED, "terminated", "timed_out"}:
                self._record_dependency_outcome(
                    prerequisite_workflow_id=dependency_id,
                    terminal_state=state,
                    close_status=close_status,
                    resolved_at=workflow.now().isoformat(),
                    failure_category=self._dependency_failure_category(
                        terminal_state=state,
                        close_status=close_status,
                    ),
                    message=message,
                )

    def _raise_for_dependency_failure(self) -> None:
        if self._dependency_failure is None:
            return
        detail = dict(self._dependency_failure)
        message = str(detail.get("message") or "").strip() or (
            f"Prerequisite execution '{detail.get('failedDependencyId')}' "
            "did not complete successfully."
        )
        self._get_logger().error(
            "Dependency gate failed",
            extra={
                "event": "dependency_gate_failed",
                "failed_dependency_id": detail.get("failedDependencyId"),
                "terminal_state": detail.get("terminalState"),
                "close_status": detail.get("closeStatus"),
                "failure_category": detail.get("failureCategory"),
                "wait_duration_ms": self._dependency_wait_duration_ms,
            },
        )
        raise DependencyFailureError(message, detail=detail)

    async def _wait_for_dependencies(self, dependency_ids: list[str]) -> None:
        if not dependency_ids:
            return

        self._declared_dependencies = list(dependency_ids)
        self._dependency_wait_occurred = True
        self._dependency_wait_started_at = workflow.now()
        self._dependency_wait_duration_ms = 0
        self._dependency_resolution = DEPENDENCY_RESOLUTION_NOT_APPLICABLE
        self._failed_dependency_id = None
        self._dependency_failure = None
        self._dependency_outcomes_by_id = {}
        self._unresolved_dependency_ids = set(dependency_ids)
        self._waiting_reason = "dependency_wait"
        self._set_state(
            STATE_WAITING_ON_DEPENDENCIES,
            summary=f"Waiting on {len(dependency_ids)} prerequisite execution(s).",
        )
        self._get_logger().info(
            "Dependency gate entered",
            extra={
                "event": "dependency_gate_entered",
                "dependency_count": len(dependency_ids),
                "dependency_ids": dependency_ids,
            },
        )

        try:
            await self._reconcile_dependencies(dependency_ids)
            while (
                not self._cancel_requested
                and self._dependency_failure is None
                and (self._unresolved_dependency_ids or self._paused)
            ):
                try:
                    await workflow.wait_condition(
                        lambda: self._cancel_requested
                        or self._dependency_failure is not None
                        or (
                            not self._paused
                            and not self._unresolved_dependency_ids
                        ),
                        timeout=DEPENDENCY_RECONCILE_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    await self._reconcile_dependencies(dependency_ids)
                    self._update_dependency_wait_duration()
                    self._update_search_attributes()
                    self._update_memo()
            self._update_dependency_wait_duration()

            if (
                self._dependency_failure is None
                and self._close_status != CLOSE_STATUS_CANCELED
            ):
                event = (
                    "dependency_gate_manual_override"
                    if self._dependency_resolution == DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE
                    else "dependency_gate_satisfied"
                )
                self._get_logger().info(
                    "Dependency gate manually overridden"
                    if event == "dependency_gate_manual_override"
                    else "Dependency gate satisfied",
                    extra={
                        "event": event,
                        "dependency_count": len(dependency_ids),
                        "unresolved_dependency_count": (
                            self._dependency_manual_override_unresolved_count
                            if event == "dependency_gate_manual_override"
                            else len(self._unresolved_dependency_ids)
                        ),
                        "wait_duration_ms": self._dependency_wait_duration_ms,
                    },
                )

            self._raise_for_dependency_failure()
        finally:
            self._waiting_reason = None
            self._update_search_attributes()
            self._update_memo()

    async def _wait_if_paused_at_safe_boundary(self) -> None:
        if not workflow.patched(RUN_PAUSE_SAFE_BOUNDARIES_PATCH):
            await workflow.wait_condition(lambda: not self._paused)
            return
        if not self._paused:
            return

        self._waiting_reason = "operator_paused"
        self._attention_required = True
        self._update_search_attributes()
        self._update_memo()
        await workflow.wait_condition(
            lambda: not self._paused or self._cancel_requested
        )
        if self._cancel_requested:
            return
        if self._waiting_reason == "operator_paused":
            self._waiting_reason = None
        self._attention_required = False
        self._update_search_attributes()
        self._update_memo()

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
            self._set_state(
                STATE_SCHEDULED,
                summary=f"Execution scheduled for {self._scheduled_for}.",
            )
            while True:
                if not self._scheduled_for:
                    break

                try:
                    from datetime import datetime

                    target_dt = datetime.fromisoformat(
                        self._scheduled_for.replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    self._get_logger().error(
                        f"Invalid scheduled_for format: {self._scheduled_for}",
                        exc_info=True,
                    )
                    raise exceptions.ApplicationError(
                        f"Invalid scheduled_for format: {self._scheduled_for}",
                        non_retryable=True,
                    ) from exc

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
                    self._get_logger().debug(
                        "Scheduled delay elapsed without reschedule/cancel."
                    )

                if self._cancel_requested:
                    break

        if self._cancel_requested:
            await self._run_finalizing_stage(
                parameters=parameters, status="canceled", error=None
            )
            self._close_status = CLOSE_STATUS_CANCELED
            self._set_state(STATE_CANCELED, summary="Execution canceled.")
            await self._record_terminal_state(
                state=STATE_CANCELED,
                close_status=CLOSE_STATUS_CANCELED,
                summary="Execution canceled.",
            )
            return {"status": "canceled"}

        self._set_state(STATE_INITIALIZING, summary="Execution initialized.")
        try:
            dependency_ids = self._dependency_ids_from_parameters(parameters)
            if dependency_ids and workflow.patched(DEPENDENCY_GATE_PATCH):
                await self._wait_for_dependencies(dependency_ids)
                if self._cancel_requested:
                    await self._run_finalizing_stage(
                        parameters=parameters, status="canceled", error=None
                    )
                    self._close_status = CLOSE_STATUS_CANCELED
                    self._set_state(STATE_CANCELED, summary="Execution canceled.")
                    await self._record_terminal_state(
                        state=STATE_CANCELED,
                        close_status=CLOSE_STATUS_CANCELED,
                        summary="Execution canceled.",
                    )
                    return {"status": "canceled"}
        except ValueError as exc:
            await self._run_finalizing_stage(
                parameters=parameters, status="failed", error=str(exc)
            )
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=str(exc))
            await self._record_terminal_state(
                state=STATE_FAILED,
                close_status=CLOSE_STATUS_FAILED,
                summary=str(exc),
                error_category="execution_error",
            )
            raise exceptions.ApplicationError(
                str(exc),
                non_retryable=True,
            ) from exc
        self._set_state(STATE_PLANNING, summary="Planning execution strategy.")

        await self._wait_if_paused_at_safe_boundary()
        if self._cancel_requested:
            await self._run_finalizing_stage(
                parameters=parameters, status="canceled", error=None
            )
            self._close_status = CLOSE_STATUS_CANCELED
            self._set_state(STATE_CANCELED, summary="Execution canceled.")
            await self._record_terminal_state(
                state=STATE_CANCELED,
                close_status=CLOSE_STATUS_CANCELED,
                summary="Execution canceled.",
            )
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
            await self._run_finalizing_stage(
                parameters=parameters, status="failed", error=str(exc)
            )
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=str(exc))
            await self._record_terminal_state(
                state=STATE_FAILED,
                close_status=CLOSE_STATUS_FAILED,
                summary=str(exc),
                error_category="execution_error",
            )
            raise exceptions.ApplicationError(
                str(exc),
                non_retryable=True,
            ) from exc
        except Exception as exc:
            await self._run_finalizing_stage(
                parameters=parameters, status="failed", error=str(exc)
            )
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=str(exc))
            await self._record_terminal_state(
                state=STATE_FAILED,
                close_status=CLOSE_STATUS_FAILED,
                summary=str(exc),
                error_category="execution_error",
            )
            raise

        if self._cancel_requested:
            await self._run_finalizing_stage(
                parameters=parameters, status="canceled", error=None
            )
            self._close_status = CLOSE_STATUS_CANCELED
            self._set_state(STATE_CANCELED, summary="Execution canceled.")
            await self._record_terminal_state(
                state=STATE_CANCELED,
                close_status=CLOSE_STATUS_CANCELED,
                summary="Execution canceled.",
            )
            return {"status": "canceled"}

        await self._run_proposals_stage(parameters=parameters)

        self._set_state(STATE_FINALIZING, summary="Finalizing execution.")

        output_status = "success"
        output_message = "Workflow completed successfully"
        finalizing_status = "success"
        finalizing_error: str | None = None
        publish_failure = False

        if workflow.patched(RUN_WORKFLOW_PUBLISH_OUTCOME_PATCH):
            output_status, output_message, publish_failure = (
                self._determine_publish_completion(parameters=parameters)
            )
            if publish_failure:
                finalizing_status = "failed"
                finalizing_error = output_message
            elif output_status == "no_changes":
                finalizing_error = self._publish_reason or output_message

        await self._run_finalizing_stage(
            parameters=parameters, status=finalizing_status, error=finalizing_error
        )

        if publish_failure:
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=output_message)
            await self._record_terminal_state(
                state=STATE_FAILED,
                close_status=CLOSE_STATUS_FAILED,
                summary=output_message,
                error_category=(
                    "user_error" if self._plan_blocked_message else "execution_error"
                ),
            )
            raise exceptions.ApplicationError(
                output_message,
                non_retryable=True,
            )

        self._close_status = CLOSE_STATUS_COMPLETED
        self._set_state(STATE_COMPLETED, summary=output_message)
        await self._record_terminal_state(
            state=STATE_COMPLETED,
            close_status=CLOSE_STATUS_COMPLETED,
            summary=output_message,
        )

        output: RunWorkflowOutput = {
            "status": output_status,
            "message": output_message,
        }
        if self._proposals_generated > 0 or self._proposals_submitted > 0:
            output["proposals_generated"] = self._proposals_generated
            output["proposals_submitted"] = self._proposals_submitted
        if self._merge_automation_disposition:
            output["mergeAutomationDisposition"] = self._merge_automation_disposition
        if self._merge_automation_head_sha:
            output["headSha"] = self._merge_automation_head_sha
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
        self._target_runtime = self._runtime_visibility_from_parameters(parameters)
        self._target_skill = self._skill_visibility_from_parameters(parameters)
        task_parameters = self._mapping_value(parameters, "task")
        self._declared_dependencies = normalize_dependency_ids(
            task_parameters.get("dependsOn")
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

    def _runtime_visibility_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> str | None:
        task_payload = self._mapping_value(parameters, "task") or {}
        task_runtime_payload = (
            self._mapping_value(task_payload, "runtime")
            if isinstance(task_payload, Mapping)
            else {}
        ) or {}
        runtime_payload = self._mapping_value(parameters, "runtime") or {}
        return (
            self._coerce_text(parameters.get("targetRuntime"), max_chars=80)
            or self._coerce_text(task_runtime_payload.get("mode"), max_chars=80)
            or self._coerce_text(task_runtime_payload.get("targetRuntime"), max_chars=80)
            or self._coerce_text(runtime_payload.get("mode"), max_chars=80)
            or self._coerce_text(runtime_payload.get("targetRuntime"), max_chars=80)
        )

    def _skill_visibility_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> str | None:
        direct = (
            self._coerce_text(parameters.get("targetSkill"), max_chars=160)
            or self._coerce_text(parameters.get("target_skill"), max_chars=160)
            or self._coerce_text(parameters.get("skillId"), max_chars=160)
            or self._coerce_text(parameters.get("skill"), max_chars=160)
        )
        if direct:
            return direct
        task_payload = self._mapping_value(parameters, "task") or {}
        if not isinstance(task_payload, Mapping):
            return None
        tool_payload = self._mapping_value(task_payload, "tool") or {}
        skill_payload = self._mapping_value(task_payload, "skill") or {}
        tool_type = str(
            tool_payload.get("type") or tool_payload.get("kind") or ""
        ).strip()
        if not tool_type or tool_type == "skill":
            tool_name = (
                self._coerce_text(tool_payload.get("name"), max_chars=160)
                or self._coerce_text(tool_payload.get("id"), max_chars=160)
            )
            if tool_name:
                return tool_name
        return (
            self._coerce_text(skill_payload.get("id"), max_chars=160)
            or self._coerce_text(skill_payload.get("name"), max_chars=160)
        )

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
            idempotency_key=(
                f"{workflow.info().workflow_id}_plan_generate"
                if workflow.patched("idempotency_key_phase3")
                else None
            ),
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

    async def _fetch_profile_snapshots(self) -> None:
        """Best-effort fetch of provider profile snapshots for all managed runtimes.

        Populates ``self._profile_snapshots`` so that
        ``_build_agent_execution_request`` can validate plan node profile refs
        against known profiles before spawning child workflows.
        """
        snapshots: dict[str, dict[str, Any]] = {}
        has_data = False
        profile_list_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("provider_profile.list")
        for runtime_id in _PROFILE_SYNC_RUNTIME_IDS:
            try:
                kwargs = self._execute_kwargs_for_route(profile_list_route)
                kwargs["start_to_close_timeout"] = timedelta(seconds=30)
                kwargs["retry_policy"] = RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=3,
                )
                result = await workflow.execute_activity(
                    "provider_profile.list",
                    {"runtime_id": runtime_id},
                    **kwargs,
                )
                if isinstance(result, dict):
                    for profile in result.get("profiles", []):
                        if isinstance(profile, dict):
                            pid = str(profile.get("profile_id", "")).strip()
                            if pid:
                                snapshots[pid] = profile
                                has_data = True
            except Exception:
                self._get_logger().warning(
                    "Failed to fetch provider profiles for runtime_id=%s; "
                    "profile validation will be skipped for this runtime.",
                    runtime_id,
                    exc_info=True,
                )
        if has_data:
            self._profile_snapshots = snapshots

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

        # Fetch provider profile snapshots so that _build_agent_execution_request
        # can validate plan node profile refs against known profiles.
        if workflow.patched(RUN_FETCH_PROFILE_SNAPSHOTS_PATCH):
            await self._fetch_profile_snapshots()

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
        dependency_map = self._plan_dependency_map(
            ordered_nodes=ordered_nodes,
            edges=plan_definition.edges,
        )
        self._initialize_step_ledger(
            ordered_nodes=ordered_nodes,
            dependency_map=dependency_map,
            updated_at=workflow.now(),
        )

        registry_snapshot_ref = plan_definition.metadata.registry_snapshot.artifact_ref
        task_payload = parameters.get("task")
        task_skills = (
            task_payload.get("skills")
            if isinstance(task_payload, Mapping)
            else parameters.get("skills")
        )
        failure_mode = plan_definition.policy.failure_mode
        publish_mode = self._publish_mode(parameters)
        pr_publish_optional = self._pr_publish_optional_for_plan(ordered_nodes)
        require_pull_request_url = (
            publish_mode == "pr"
            and self._integration is None
            and not pr_publish_optional
        )
        pull_request_url: str | None = None
        skill_definitions_by_key: dict[tuple[str, str], Any] = {}
        requires_registry_lookup = any(
            node.tool_type == "skill" for node in plan_definition.nodes
        )
        if workflow.patched(RUN_CONDITIONAL_REGISTRY_READ_PATCH):
            should_read_registry = bool(
                registry_snapshot_ref and requires_registry_lookup
            )
        else:
            should_read_registry = bool(registry_snapshot_ref)

        if should_read_registry:
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

        previous_step_outputs: Mapping[str, Any] = {}
        for index, node in enumerate(ordered_nodes, start=1):
            await self._wait_if_paused_at_safe_boundary()
            if self._cancel_requested:
                return

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

            tool_type = (
                str(selected_node.get("type") or selected_node.get("kind") or "skill")
                .strip()
                .lower()
            )
            tool_name = str(
                selected_node.get("name") or selected_node.get("id") or ""
            ).strip()
            tool_version = str(selected_node.get("version") or "").strip()
            node_id = str(node.get("id") or "unknown")
            original_node_inputs = dict(node.get("inputs", {}))
            approval_policy = plan_definition.policy.approval_policy
            review_gate_active = self._review_gate_active(
                approval_policy=approval_policy,
                tool_type=tool_type,
                tool_name=tool_name,
            )
            max_review_attempts = (
                int(getattr(approval_policy, "max_review_attempts", 0))
                if review_gate_active
                else 0
            )
            review_retry_count = 0
            previous_review_feedback: str | None = None
            previous_review_issues: tuple[Mapping[str, Any], ...] = ()
            result_status: str | None = None
            execution_result: Any = None
            accepted_execution = False
            current_review_attempt = 1

            while current_review_attempt <= (max_review_attempts + 1):
                await self._wait_if_paused_at_safe_boundary()
                if self._cancel_requested:
                    return

                node_inputs = dict(original_node_inputs)
                if previous_review_feedback:
                    node_inputs = self._inject_review_feedback_into_inputs(
                        tool_type=tool_type,
                        original_inputs=node_inputs,
                        attempt=review_retry_count,
                        feedback=previous_review_feedback,
                        issues=previous_review_issues,
                    )

                self._step_count = index
                self._summary = (
                    f"Executing plan step {index}/{len(ordered_nodes)}: {tool_name}"
                )
                self._update_memo()
                self._refresh_step_readiness(updated_at=workflow.now())
                self._mark_step_running(
                    node_id,
                    updated_at=workflow.now(),
                    summary=self._summary,
                )

                system_retries = 0
                while system_retries <= 3:
                    await self._wait_if_paused_at_safe_boundary()
                    if self._cancel_requested:
                        return

                    if tool_type == "agent_runtime":
                        # --- Agent dispatch: child workflow ---
                        try:
                            resolved_skillset_ref = (
                                await self._resolve_agent_node_skillset_ref(
                                    task_skills=task_skills,
                                    node_skills=node.get("skills"),
                                    node_inputs=node_inputs,
                                    node_id=node_id,
                                    existing_skillset_ref=(
                                        self._existing_agent_skillset_ref(
                                            parameters=parameters,
                                            node=node,
                                            node_inputs=node_inputs,
                                        )
                                    ),
                                )
                            )
                            request = self._build_agent_execution_request(
                                node_inputs=node_inputs,
                                node_id=node_id,
                                tool_name=tool_name,
                                resolved_skillset_ref=resolved_skillset_ref,
                                workflow_parameters=parameters,
                            )
                            if workflow.patched(RUN_SLOT_CONTINUITY_PATCH):
                                self._mark_slot_continuity_for_next_step(
                                    request=request,
                                    ordered_nodes=ordered_nodes,
                                    current_index=index,
                                )
                            request = await self._maybe_bind_task_scoped_session(request)
                            child_workflow_id = (
                                f"{workflow.info().workflow_id}:agent:{node_id}"
                            )
                            if system_retries > 0:
                                child_workflow_id = (
                                    f"{child_workflow_id}:retry{system_retries}"
                                )
                            self._active_agent_child_workflow_id = child_workflow_id
                            self._active_agent_id = request.agent_id
                            self._mark_step_waiting(
                                node_id,
                                status="awaiting_external",
                                updated_at=workflow.now(),
                                waiting_reason="Awaiting child workflow progress",
                                summary=f"Awaiting child workflow for {tool_name}",
                                refs=self._pending_agent_step_refs(
                                    child_workflow_id=child_workflow_id,
                                    request=request,
                                ),
                            )
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
                            self._mark_step_terminal(
                                node_id,
                                status="failed",
                                updated_at=workflow.now(),
                                summary=f"{tool_name} failed",
                                last_error="execution_error",
                            )
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
                            "tool": {
                                "type": "skill",
                                "name": tool_name,
                                "version": tool_version,
                            },
                            "skill": {"name": tool_name, "version": tool_version},
                            "inputs": node_inputs,
                            "options": node.get("options", {}),
                        }
                        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                            "mm.skill.execute"
                        )
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
                        if route.activity_type not in {
                            "mm.skill.execute",
                            "mm.tool.execute",
                        }:
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
                                    "ownerId": self._owner_id,
                                    "ownerType": self._owner_type,
                                    "previousOutputs": previous_step_outputs,
                                },
                            }
                            if workflow.patched("idempotency_key_phase3"):
                                execute_payload["idempotency_key"] = (
                                    f"{workflow.info().workflow_id}_{node_id}_execute"
                                )

                            execution_result = await workflow.execute_activity(
                                route.activity_type,
                                execute_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                **self._execute_kwargs_for_route(route),
                            )
                        except Exception:
                            self._mark_step_terminal(
                                node_id,
                                status="failed",
                                updated_at=workflow.now(),
                                summary=f"{tool_name} failed",
                                last_error="execution_error",
                            )
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
                    self._record_step_result_evidence(
                        node_id,
                        execution_result=execution_result,
                        updated_at=workflow.now(),
                    )
                    if result_status != "COMPLETED":
                        failure_message = self._activity_result_failure_message(
                            execution_result
                        )

                        retryable = failure_message == "system_error" or (
                            failure_message == "execution_error"
                            and tool_type == "agent_runtime"
                        )
                        if retryable and system_retries < 3:
                            self._mark_step_terminal(
                                node_id,
                                status="failed",
                                updated_at=workflow.now(),
                                summary=f"{tool_name} failed",
                                last_error=failure_message,
                            )
                            system_retries += 1
                            self._get_logger().info(
                                f"Retrying plan node {node_id} after {failure_message} "
                                f"(attempt {system_retries} of 3)"
                            )
                            await self._wait_if_paused_at_safe_boundary()
                            if self._cancel_requested:
                                return
                            self._mark_step_running(
                                node_id,
                                updated_at=workflow.now(),
                                summary=self._summary,
                            )
                            continue

                        self._mark_step_terminal(
                            node_id,
                            status="failed",
                            updated_at=workflow.now(),
                            summary=f"{tool_name} failed",
                            last_error=failure_message,
                        )
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
                    break

                await self._wait_if_paused_at_safe_boundary()
                if self._cancel_requested:
                    return

                if not review_gate_active:
                    accepted_execution = True
                    break

                self._mark_step_waiting(
                    node_id,
                    status="reviewing",
                    updated_at=workflow.now(),
                    waiting_reason="Awaiting structured review result",
                    summary="Structured review in progress",
                )
                self._upsert_step_check(
                    node_id,
                    kind="approval_policy",
                    status="pending",
                    summary="Structured review in progress",
                    retry_count=review_retry_count,
                    artifact_ref=None,
                )

                review_request = ReviewRequest(
                    node_id=node_id,
                    step_index=index,
                    total_steps=len(ordered_nodes),
                    review_attempt=current_review_attempt,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    tool_type=tool_type,
                    inputs=node_inputs,
                    execution_result=(
                        execution_result
                        if isinstance(execution_result, Mapping)
                        else {}
                    ),
                    workflow_context={
                        "workflow_id": workflow.info().workflow_id,
                        "run_id": workflow.info().run_id,
                        "plan_title": plan_definition.metadata.title,
                    },
                    reviewer_model=str(
                        getattr(approval_policy, "reviewer_model", "default")
                    ),
                    review_timeout_seconds=int(
                        getattr(approval_policy, "review_timeout_seconds", 120)
                    ),
                    previous_feedback=previous_review_feedback,
                )
                review_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("step.review")
                review_verdict = parse_review_verdict(
                    await workflow.execute_activity(
                        "step.review",
                        review_request.to_payload(),
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        **self._execute_kwargs_for_route(review_route),
                    )
                )
                step_attempt = self._step_attempt_for(node_id) or 0
                review_artifact_ref = await self._write_json_artifact(
                    name=(
                        "reports/review_"
                        f"{node_id}_attempt_{step_attempt}.json"
                    ),
                    payload={
                        "logicalStepId": node_id,
                        "attempt": step_attempt,
                        "reviewAttempt": current_review_attempt,
                        "request": review_request.to_payload(),
                        "verdict": review_verdict.to_payload(),
                    },
                )
                review_check_status = self._check_status_for_review_verdict(
                    review_verdict.verdict
                )

                if review_verdict.verdict == "FAIL":
                    review_retry_count += 1
                    failed_review_summary = self._bounded_review_summary(
                        review_verdict.feedback,
                        fallback="Review failed; retrying step",
                    )
                    self._upsert_step_check(
                        node_id,
                        kind="approval_policy",
                        status=review_check_status,
                        summary=failed_review_summary,
                        retry_count=review_retry_count,
                        artifact_ref=review_artifact_ref,
                    )
                    if review_retry_count <= max_review_attempts:
                        previous_review_feedback = (
                            review_verdict.feedback
                            or "Structured review requested another retry."
                        )
                        previous_review_issues = tuple(review_verdict.issues)
                        current_review_attempt += 1
                        continue
                    self._mark_step_terminal(
                        node_id,
                        status="failed",
                        updated_at=workflow.now(),
                        summary=failed_review_summary,
                        last_error="review_failed",
                    )
                    if failure_mode == "FAIL_FAST":
                        raise ValueError(
                            f"plan node review failed after {review_retry_count} retry"
                        )
                    break

                self._upsert_step_check(
                    node_id,
                    kind="approval_policy",
                    status=review_check_status,
                    summary=self._accepted_review_summary(
                        review_verdict.verdict,
                        retry_count=review_retry_count,
                    ),
                    retry_count=review_retry_count,
                    artifact_ref=review_artifact_ref,
                )
                accepted_execution = True
                break

            if not accepted_execution:
                continue

            self._mark_step_terminal(
                node_id,
                status="succeeded",
                updated_at=workflow.now(),
                summary=self._get_from_result(execution_result, "summary")
                or self._summary,
                last_error=None,
            )
            self._refresh_step_readiness(updated_at=workflow.now())
            self._record_execution_context(
                node_id=node_id,
                execution_result=execution_result,
            )
            blocked_message = self._blocked_outcome_message(execution_result)
            if (
                blocked_message
                and workflow.patched(RUN_BLOCKED_OUTCOME_SHORT_CIRCUIT_PATCH)
            ):
                self._plan_blocked_message = blocked_message
                self._publish_status = "not_required"
                self._publish_reason = blocked_message
                require_pull_request_url = False
                pull_request_url = None
                self._summary = blocked_message
                self._mark_remaining_plan_steps_skipped(
                    ordered_nodes=ordered_nodes,
                    completed_index=index - 1,
                    summary=blocked_message,
                )
                self._refresh_step_readiness(updated_at=workflow.now())
                self._update_memo()
                break
            self._record_publish_result(
                parameters=parameters,
                execution_result=execution_result,
            )
            if (
                pr_publish_optional
                and publish_mode == "pr"
                and self._execution_result_has_publishable_changes(execution_result)
            ):
                require_pull_request_url = True
            outputs_for_story_output = self._get_from_result(
                execution_result, "outputs"
            )
            if isinstance(outputs_for_story_output, Mapping):
                previous_step_outputs = outputs_for_story_output
                story_output_result = outputs_for_story_output.get("storyOutput")
                if isinstance(story_output_result, Mapping):
                    story_output_status = str(
                        story_output_result.get("status") or ""
                    ).strip()
                    story_output_mode = str(
                        story_output_result.get("mode") or ""
                    ).strip()
                    if self._is_successful_jira_story_output(
                        mode=story_output_mode,
                        status=story_output_status,
                    ):
                        require_pull_request_url = False
                        self._publish_status = "published"
                        self._publish_reason = (
                            "Jira issue output succeeded; no PR output required"
                        )
                        self._publish_context["storyOutputMode"] = "jira"
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
                                diag_text = diag_payload.decode(
                                    "utf-8", errors="replace"
                                )
                            elif isinstance(diag_payload, str):
                                diag_text = diag_payload
                            else:
                                diag_text = str(diag_payload)

                            pr_match = _GITHUB_PR_URL_PATTERN.search(diag_text)
                            if pr_match:
                                pull_request_url = pr_match.group(0)
                        except Exception as e:
                            self._get_logger().warning(
                                f"Failed to extract PR URL from diagnostics_ref {diag_ref}: {e}"
                            )

        await self._wait_if_paused_at_safe_boundary()
        if self._cancel_requested:
            return

        if require_pull_request_url and pull_request_url is None:
            last_tool = (
                str(
                    (ordered_nodes[-1].get("tool", {}) if ordered_nodes else {}).get(
                        "name"
                    )
                    or ""
                )
                .strip()
                .lower()
            )
            # Derive the last node's effective agent_id using the same
            # resolution logic as _build_agent_execution_request so that
            # Jules-via-runtime-settings (e.g. tool.name="auto" with
            # inputs.runtime.mode="jules") is correctly detected.
            _last_inputs = ordered_nodes[-1].get("inputs", {}) if ordered_nodes else {}
            _last_rt = _last_inputs.get("runtime") or {}
            last_agent_id = (
                (
                    _last_rt.get("mode")
                    or _last_rt.get("agent_id")
                    or _last_inputs.get("targetRuntime")
                    or last_tool
                    or ""
                )
                .strip()
                .lower()
            )

            ws = (
                self._mapping_value(parameters, "workspaceSpec", "workspace_spec") or {}
            )

            if last_agent_id in ("jules", "jules_api", "github_pr_creator"):
                self._get_logger().info(
                    "Skipping native PR creation: agent '%s' handles its own PRs.",
                    last_agent_id,
                )
            else:
                agent_outputs = {}
                if "execution_result" in locals():
                    agent_outputs = (
                        self._get_from_result(execution_result, "outputs") or {}
                    )
                    if not isinstance(agent_outputs, dict):
                        agent_outputs = {}

                last_node_inputs = (
                    ordered_nodes[-1].get("inputs", {}) if ordered_nodes else {}
                )
                publish_payload = self._resolve_publish_payload(parameters)
                head_branch, base_branch = self._resolve_native_pr_branches(
                    parameters=parameters,
                    agent_outputs=agent_outputs,
                    workspace_spec=ws,
                    last_node_inputs=last_node_inputs,
                    publish_payload=publish_payload,
                )
                pr_title = self._title or "Automated changes by MoonMind"
                pr_body = self._summary or "Automated changes by MoonMind."
                if workflow.patched(NATIVE_PR_CREATE_PAYLOAD_PATCH):
                    task_payload = self._mapping_value(parameters, "task")
                    pr_title = self._resolve_native_pr_title(
                        publish_payload=publish_payload,
                        task_payload=task_payload,
                    )
                    pr_body = self._resolve_native_pr_body(
                        publish_payload=publish_payload,
                        task_payload=task_payload,
                    )

                push_status = agent_outputs.get("push_status", "")
                if push_status == "no_commits":
                    self._get_logger().info(
                        "Skipping native PR creation: agent made no commits "
                        "on branch '%s'.",
                        agent_outputs.get("push_branch") or head_branch,
                    )
                elif self._native_pr_push_status_blocks_creation(push_status):
                    self._get_logger().info(
                        "Skipping native PR creation: publish push already "
                        "failed with status '%s'.",
                        push_status,
                    )
                elif not self._repo or not head_branch:
                    raise ValueError(
                        "publishMode 'pr' requested but no PR URL was returned, and missing repo/branch to create it natively"
                    )
                else:
                    self._get_logger().info(
                        f"Creating PR natively from {head_branch} into {base_branch} for repo {self._repo}"
                    )
                    self._publish_context["branch"] = head_branch
                    self._publish_context["baseRef"] = base_branch
                    create_payload = {
                        "repo": self._repo,
                        "head": head_branch,
                        "base": base_branch,
                        "title": pr_title,
                        "body": pr_body,
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
                        created_head_sha = self._coerce_text(
                            self._get_from_result(create_result, "headSha"),
                            max_chars=80,
                        )
                        if created_head_sha:
                            self._publish_context["headSha"] = created_head_sha
                        if pr_url:
                            pull_request_url = pr_url
                            self._get_logger().info(
                                f"Natively created PR: {pull_request_url}"
                            )
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
        if (
            pr_publish_optional
            and publish_mode == "pr"
            and self._publish_status is None
            and pull_request_url is None
        ):
            self._publish_status = "not_required"
            self._publish_reason = (
                "Jira issue agent completed; no PR output required"
            )
        # Persist the PR URL so the workflow output can determine if a PR was created.
        self._pull_request_url = pull_request_url
        publish_mode = self._publish_mode(parameters)
        if publish_mode == "pr" and pull_request_url:
            self._publish_status = "published"
            self._publish_reason = "published pull request"
            self._publish_context["pullRequestUrl"] = pull_request_url
        if self._plan_blocked_message:
            self._summary = self._plan_blocked_message
        else:
            self._summary = f"Executed {len(ordered_nodes)} plan step(s)."
        self._update_memo()

        await self._maybe_start_merge_gate(
            parameters=parameters,
            pull_request_url=pull_request_url,
        )

        if self._cancel_requested:
            return

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

    @staticmethod
    def _is_blocked_outcome_value(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return value.strip().lower() == "blocked"

    def _blocked_outcome_message_from_mapping(
        self,
        payload: Mapping[str, Any],
    ) -> str | None:
        status_value = (
            payload.get("decision")
            or payload.get("status")
            or payload.get("outcome")
            or payload.get("result")
        )
        if not self._is_blocked_outcome_value(status_value):
            return None

        blockers = payload.get("blockingIssues")
        if blockers is None:
            blockers = payload.get("blocking_issues")
        if blockers is None:
            blockers = payload.get("blockers")

        summary = self._coerce_text(
            payload.get("summary")
            or payload.get("message")
            or payload.get("reason")
            or payload.get("blockedReason")
            or payload.get("blocked_reason"),
            max_chars=700,
        )
        if (
            not summary
            and isinstance(blockers, Sequence)
            and not isinstance(blockers, (str, bytes))
            and blockers
        ):
            summary = "A plan step reported unresolved blockers."
        if not summary:
            return None
        return f"Workflow blocked by plan step: {summary}"

    @staticmethod
    def _json_mapping_candidates_from_text(text: str) -> tuple[Mapping[str, Any], ...]:
        raw_text = str(text or "").strip()
        if not raw_text:
            return ()

        candidates: list[str] = []
        if raw_text.startswith("{") and raw_text.endswith("}"):
            candidates.append(raw_text)
        for match in _JSON_OBJECT_CODE_FENCE_PATTERN.finditer(raw_text):
            content = match.group(1).strip()
            if content.startswith("{") and content.endswith("}"):
                candidates.append(content)

        mappings: list[Mapping[str, Any]] = []
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, Mapping):
                mappings.append(parsed)
        return tuple(mappings)

    def _blocked_outcome_message(self, execution_result: Any) -> str | None:
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return None

        mapping_candidates: list[Mapping[str, Any]] = [outputs]
        for field in (
            "workflowOutcome",
            "workflow_outcome",
            "terminalOutcome",
            "terminal_outcome",
            "outcome",
            "result",
        ):
            value = outputs.get(field)
            if isinstance(value, Mapping):
                mapping_candidates.append(value)

        for candidate in mapping_candidates:
            message = self._blocked_outcome_message_from_mapping(candidate)
            if message:
                return message

        for field in (
            "operator_summary",
            "operatorSummary",
            "lastAssistantText",
            "assistantText",
            "summary",
            "message",
        ):
            value = outputs.get(field)
            if not isinstance(value, str):
                continue
            for candidate in self._json_mapping_candidates_from_text(value):
                message = self._blocked_outcome_message_from_mapping(candidate)
                if message:
                    return message

        return None

    def _mark_remaining_plan_steps_skipped(
        self,
        *,
        ordered_nodes: Sequence[Mapping[str, Any]],
        completed_index: int,
        summary: str,
    ) -> None:
        for node in ordered_nodes[completed_index + 1:]:
            node_id = str(node.get("id") or "").strip()
            if not node_id:
                continue
            try:
                self._mark_step_terminal(
                    node_id,
                    status="skipped",
                    updated_at=workflow.now(),
                    summary=summary,
                    last_error=None,
                )
            except KeyError:
                continue

    def _publish_mode(self, parameters: Mapping[str, Any]) -> str:
        value = parameters.get("publishMode")
        if not isinstance(value, str):
            return ""
        normalized = value.strip().lower()
        return normalized if normalized in {"none", "branch", "pr"} else ""

    def _managed_session_runtime_id(
        self, request: AgentExecutionRequest
    ) -> str | None:
        if request.agent_kind != "managed":
            return None
        return canonical_codex_managed_runtime_id(request.agent_id)

    def _task_scoped_session_workflow_id(self, runtime_id: str) -> str:
        return f"{workflow.info().workflow_id}:session:{runtime_id}"

    def _task_scoped_session_visibility(
        self,
        *,
        binding: CodexManagedSessionBinding,
    ) -> dict[str, Any]:
        return {
            "TaskRunId": [binding.task_run_id],
            "RuntimeId": [binding.runtime_id],
            "SessionId": [binding.session_id],
            "SessionEpoch": [binding.session_epoch],
            "SessionStatus": ["active"],
            "IsDegraded": [False],
        }

    def _task_scoped_session_static_details(
        self,
        *,
        binding: CodexManagedSessionBinding,
    ) -> str:
        return (
            "Task-scoped Codex managed session | "
            f"taskRunId={binding.task_run_id} | "
            f"runtime={binding.runtime_id} | "
            f"session={binding.session_id} | "
            f"epoch={binding.session_epoch}"
        )

    def _pending_agent_step_refs(
        self,
        *,
        child_workflow_id: str,
        request: AgentExecutionRequest,
    ) -> dict[str, str]:
        refs = {"childWorkflowId": child_workflow_id}
        if request.managed_session is not None:
            task_run_id = str(request.managed_session.task_run_id or "").strip()
            if task_run_id:
                refs["taskRunId"] = task_run_id
        return refs

    async def _ensure_task_scoped_codex_session(
        self, request: AgentExecutionRequest
    ) -> CodexManagedSessionBinding | None:
        runtime_id = self._managed_session_runtime_id(request)
        if runtime_id is None:
            return None
        if self._codex_session_binding is not None:
            return self._codex_session_binding

        session_input = CodexManagedSessionWorkflowInput(
            taskRunId=workflow.info().workflow_id,
            runtimeId=runtime_id,
            executionProfileRef=request.execution_profile_ref,
        )
        session_workflow_id = self._task_scoped_session_workflow_id(runtime_id)
        initial_binding = CodexManagedSessionBinding.from_input(
            workflow_id=session_workflow_id,
            session_input=session_input,
        )
        self._codex_session_handle = await workflow.start_child_workflow(
            "MoonMind.AgentSession",
            session_input,
            id=session_workflow_id,
            task_queue=WORKFLOW_TASK_QUEUE,
            search_attributes=self._task_scoped_session_visibility(
                binding=initial_binding
            ),
            static_summary="Task-scoped Codex managed session",
            static_details=self._task_scoped_session_static_details(
                binding=initial_binding
            ),
        )
        self._codex_session_binding = initial_binding
        return self._codex_session_binding

    async def _maybe_bind_task_scoped_session(
        self, request: AgentExecutionRequest
    ) -> AgentExecutionRequest:
        binding = await self._ensure_task_scoped_codex_session(request)
        if binding is None:
            return request
        return request.model_copy(update={"managed_session": binding})

    async def _terminate_task_scoped_sessions(self, *, reason: str) -> None:
        binding = self._codex_session_binding
        try:
            if binding is not None:
                session_handle = workflow.get_external_workflow_handle(
                    binding.workflow_id
                )
                if workflow.patched(
                    RUN_TASK_SCOPED_SESSION_TERMINATION_UPDATE_EXECUTE_PATCH
                ):
                    try:
                        await session_handle.execute_update(
                            "TerminateSession",
                            {"reason": reason},
                        )
                    except Exception as exc:
                        self._get_logger().warning(
                            "Task-scoped Codex terminate update failed for %s: %s",
                            binding.session_id,
                            exc,
                        )
                        try:
                            await self._terminate_task_scoped_session_via_activity(
                                binding=binding,
                                reason=reason,
                            )
                        except Exception as activity_exc:
                            self._get_logger().warning(
                                "Task-scoped Codex terminate activity failed for %s; "
                                "falling back to session signal: %s",
                                binding.session_id,
                                activity_exc,
                            )
                        await session_handle.signal(
                            "control_action",
                            {
                                "action": "terminate_session",
                                "reason": reason,
                            },
                        )
                elif workflow.patched(RUN_TASK_SCOPED_SESSION_TERMINATION_UPDATE_PATCH):
                    await session_handle.signal(
                        "control_action",
                        {
                            "action": "terminate_session",
                            "reason": reason,
                        },
                    )
                elif workflow.patched(RUN_TASK_SCOPED_SESSION_TERMINATION_PATCH):
                    try:
                        await self._terminate_task_scoped_session_via_activity(
                            binding=binding,
                            reason=reason,
                        )
                    except Exception as exc:
                        self._get_logger().warning(
                            "Task-scoped Codex terminate activity failed for %s; "
                            "falling back to session signal: %s",
                            binding.session_id,
                            exc,
                        )
                    await session_handle.signal(
                        "control_action",
                        {
                            "action": "terminate_session",
                            "reason": reason,
                        },
                    )
                else:
                    await session_handle.signal(
                        "control_action",
                        {
                            "action": "terminate_session",
                            "reason": reason,
                        },
                    )
        finally:
            self._codex_session_handle = None
            self._codex_session_binding = None

    async def _terminate_task_scoped_session_via_activity(
        self,
        *,
        binding: CodexManagedSessionBinding,
        reason: str,
    ) -> None:
        snapshot_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "agent_runtime.load_session_snapshot"
        )
        snapshot_payload = await workflow.execute_activity(
            snapshot_route.activity_type,
            binding.model_dump(mode="json", by_alias=True),
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
            **self._execute_kwargs_for_route(snapshot_route),
        )
        snapshot = CodexManagedSessionSnapshot.model_validate(snapshot_payload)
        if snapshot.container_id and snapshot.thread_id:
            terminate_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "agent_runtime.terminate_session"
            )
            await workflow.execute_activity(
                terminate_route.activity_type,
                TerminateCodexManagedSessionRequest(
                    sessionId=snapshot.binding.session_id,
                    sessionEpoch=snapshot.binding.session_epoch,
                    containerId=snapshot.container_id,
                    threadId=snapshot.thread_id,
                    reason=reason,
                ).model_dump(mode="json", by_alias=True),
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                **self._execute_kwargs_for_route(terminate_route),
            )

    def _coerce_text(
        self, value: Any, max_chars: int | None = None, *, flatten: bool = True
    ) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()) if flatten else value.strip()
        if not normalized:
            return None
        if max_chars:
            return normalized[:max_chars].rstrip()
        return normalized

    @staticmethod
    def _is_transient_summary(value: str) -> bool:
        normalized = " ".join(value.strip().lower().split())
        return (
            normalized
            in {
                "launching agent...",
                "launching agent",
                "agent is running.",
                "agent is running",
                "executing run steps.",
                "executing run steps",
                "execution initialized.",
                "execution initialized",
                "planning execution strategy.",
                "planning execution strategy",
                "generating task proposals.",
                "generating task proposals",
                "finalizing execution.",
                "finalizing execution",
            }
            or normalized.startswith("executing plan step")
            or (normalized.startswith("executed") and "plan step" in normalized)
        )

    def _resolve_publish_payload(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        task_payload = self._mapping_value(parameters, "task")
        publish_payload = self._mapping_value(parameters, "publish")
        if publish_payload:
            return publish_payload
        nested_publish = task_payload.get("publish") if isinstance(task_payload, dict) else None
        if isinstance(nested_publish, Mapping):
            return self._json_mapping(nested_publish, path="parameters.task.publish")
        return {}

    def _proposal_generation_requested(self, parameters: Mapping[str, Any]) -> bool:
        if workflow.patched("run-workflow-nested-propose-tasks"):
            task_node = parameters.get("task")
            task_payload = task_node if isinstance(task_node, Mapping) else {}
            task_flag = task_payload.get("proposeTasks", parameters.get("proposeTasks"))
            return _coerce_bool(task_flag, default=False)
        else:
            return _coerce_bool(parameters.get("proposeTasks"), default=False)

    def _resolve_task_body_instructions(self, task_payload: Mapping[str, Any]) -> str | None:
        instructions = self._coerce_text(task_payload.get("instructions"), flatten=False)
        if instructions:
            return instructions
        steps = task_payload.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                step_instructions = self._coerce_text(step.get("instructions"), flatten=False)
                if step_instructions:
                    return step_instructions
        return None

    def _resolve_native_pr_title(
        self,
        *,
        publish_payload: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> str:
        publish_title = self._coerce_text(
            publish_payload.get("prTitle"), max_chars=150
        )
        if publish_title:
            return publish_title
        explicit_title = self._coerce_text(task_payload.get("title"), max_chars=150)
        if explicit_title:
            return explicit_title
        steps = task_payload.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                step_title = self._coerce_text(step.get("title"), max_chars=150)
                if step_title:
                    return step_title
        task_instructions = self._coerce_text(
            task_payload.get("instructions"), max_chars=150
        )
        if task_instructions:
            return task_instructions
        if self._title:
            return self._title
        return "Automated changes by MoonMind"

    def _resolve_native_pr_body(
        self,
        *,
        publish_payload: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> str:
        publish_body = self._coerce_text(
            publish_payload.get("prBody"), flatten=False
        )
        if publish_body:
            return publish_body
        default_summary = self._resolve_task_body_instructions(task_payload)
        if default_summary:
            return default_summary
        if self._summary and not self._is_transient_summary(self._summary):
            return self._summary
        return "Automated changes by MoonMind."

    def _resolve_publish_base_branch(self, publish_payload: Mapping[str, Any]) -> str | None:
        raw_base = publish_payload.get("prBaseBranch")
        if raw_base is None:
            raw_base = publish_payload.get("baseBranch")
        return self._coerce_text(raw_base)

    def _resolve_native_pr_branches(
        self,
        *,
        parameters: Mapping[str, Any],
        agent_outputs: Mapping[str, Any],
        workspace_spec: Mapping[str, Any],
        last_node_inputs: Mapping[str, Any],
        publish_payload: Mapping[str, Any],
    ) -> tuple[str, str]:
        if workflow.patched(NATIVE_PR_BRANCH_DEFAULTS_PATCH):
            head_candidates = (
                agent_outputs.get("push_branch"),
                agent_outputs.get("branch"),
                agent_outputs.get("targetBranch"),
                workspace_spec.get("targetBranch"),
                parameters.get("targetBranch"),
                last_node_inputs.get("targetBranch"),
            )
            base_candidates = (
                self._resolve_publish_base_branch(publish_payload),
                agent_outputs.get("push_base_branch"),
                agent_outputs.get("baseBranch"),
                agent_outputs.get("base_branch"),
                workspace_spec.get("startingBranch"),
                last_node_inputs.get("startingBranch"),
                "main",
            )
        else:
            head_candidates = (
                agent_outputs.get("push_branch"),
                agent_outputs.get("branch"),
                agent_outputs.get("targetBranch"),
                workspace_spec.get("targetBranch"),
                parameters.get("targetBranch"),
                last_node_inputs.get("targetBranch"),
                workspace_spec.get("branch"),
                last_node_inputs.get("branch"),
            )
            base_candidates = (
                agent_outputs.get("baseBranch"),
                agent_outputs.get("base_branch"),
                workspace_spec.get("startingBranch"),
                workspace_spec.get("branch"),
                last_node_inputs.get("startingBranch"),
                last_node_inputs.get("branch"),
                "main",
            )
        head_branch = next(
            (
                candidate
                for candidate in (
                    self._coerce_text(value) for value in head_candidates
                )
                if candidate
            ),
            "",
        )
        base_branch = next(
            (
                candidate
                for candidate in (
                    self._coerce_text(value) for value in base_candidates
                )
                if candidate
            ),
            "main",
        )
        return head_branch, base_branch

    def _native_pr_push_status_blocks_creation(self, push_status: Any) -> bool:
        status = self._coerce_text(push_status)
        if status in {"failed", "skipped"}:
            return True
        if status == "protected_branch":
            return workflow.patched(NATIVE_PR_PUSH_STATUS_GATE_PATCH)
        return False

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

    def _record_publish_result(
        self,
        *,
        parameters: Mapping[str, Any],
        execution_result: Any,
    ) -> None:
        publish_mode = self._publish_mode(parameters)
        if publish_mode not in {"pr", "branch"}:
            return

        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return

        push_status = self._coerce_text(outputs.get("push_status"))
        if push_status is None:
            return

        if push_status == "no_commits":
            self._publish_status = "skipped"
            self._publish_reason = self._compose_no_change_publish_reason(
                publish_mode=publish_mode,
            )
            return

        if push_status == "failed":
            push_error = self._coerce_text(outputs.get("push_error"), max_chars=200)
            self._publish_status = "failed"
            self._publish_reason = push_error or "publish failed"
            return

        if push_status == "skipped":
            push_error = self._coerce_text(outputs.get("push_error"), max_chars=200)
            self._publish_status = "failed"
            self._publish_reason = push_error or "publish skipped"
            return

        if push_status == "protected_branch":
            if not workflow.patched(NATIVE_PR_PUSH_STATUS_GATE_PATCH):
                return
            push_branch = self._coerce_text(outputs.get("push_branch"), max_chars=120)
            self._publish_status = "failed"
            if push_branch:
                self._publish_reason = (
                    f"publish failed: working branch '{push_branch}' is protected"
                )
            else:
                self._publish_reason = "publish failed: working branch is protected"
            return

        if push_status == "pushed" and publish_mode == "branch":
            self._publish_status = "published"
            self._publish_reason = "published branch"

    @staticmethod
    def _is_successful_jira_story_output(*, mode: str, status: str) -> bool:
        return mode == "jira" and status in {"jira_created", "jira_partial"}

    def _record_execution_context(self, *, node_id: str, execution_result: Any) -> None:
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return

        self._last_step_id = node_id
        operator_summary = self._sanitize_operator_summary(
            self._coerce_text(
                outputs.get("operator_summary") or outputs.get("operatorSummary"),
                max_chars=1600,
            )
        )
        meaningful_operator_summary = (
            operator_summary
            if operator_summary and not is_generic_completion_summary(operator_summary)
            else None
        )
        if meaningful_operator_summary:
            self._operator_summary = operator_summary

        step_summary = meaningful_operator_summary or self._coerce_text(
            outputs.get("summary") or outputs.get("message"),
            max_chars=1600,
        )
        self._last_step_summary = self._sanitize_operator_summary(
            step_summary
        )

        self._last_diagnostics_ref = self._coerce_text(
            outputs.get("diagnostics_ref") or outputs.get("diagnosticsRef"),
            max_chars=200,
        )
        merge_automation_disposition = self._coerce_text(
            outputs.get("mergeAutomationDisposition")
            or outputs.get("merge_automation_disposition"),
            max_chars=80,
        )
        if merge_automation_disposition:
            self._merge_automation_disposition = merge_automation_disposition
        merge_automation_head_sha = self._coerce_text(
            outputs.get("headSha")
            or outputs.get("head_sha")
            or outputs.get("latestHeadSha")
            or outputs.get("latest_head_sha"),
            max_chars=80,
        )
        if merge_automation_head_sha:
            self._merge_automation_head_sha = merge_automation_head_sha

        publish_branch = self._coerce_text(
            outputs.get("push_branch") or outputs.get("branch"),
            max_chars=120,
        )
        if publish_branch:
            self._publish_context["branch"] = publish_branch

        publish_base_ref = self._coerce_text(
            outputs.get("push_base_ref") or outputs.get("pushBaseRef"),
            max_chars=120,
        )
        if publish_base_ref:
            self._publish_context["baseRef"] = publish_base_ref

        push_commit_count = outputs.get("push_commit_count")
        if push_commit_count is None:
            push_commit_count = outputs.get("pushCommitCount")
        normalized_commit_count: int | None = None
        if isinstance(push_commit_count, bool):
            push_commit_count = None
        if isinstance(push_commit_count, (int, float)):
            normalized_commit_count = int(push_commit_count)
        elif isinstance(push_commit_count, str) and push_commit_count.strip().isdigit():
            normalized_commit_count = int(push_commit_count.strip())

        if normalized_commit_count is not None:
            if normalized_commit_count >= 0:
                self._publish_context["commitCount"] = normalized_commit_count
            else:
                self._publish_context.pop("commitCount", None)

        pull_request_url = self._extract_pull_request_url(execution_result)
        if pull_request_url:
            self._publish_context["pullRequestUrl"] = pull_request_url
        head_sha = self._coerce_text(
            outputs.get("head_sha")
            or outputs.get("headSha")
            or outputs.get("push_head_sha")
            or outputs.get("pushHeadSha"),
            max_chars=80,
        )
        if head_sha:
            self._publish_context["headSha"] = head_sha

        self._record_report_result(execution_result)

    def _record_report_result(self, execution_result: Any) -> None:
        metadata = self._get_from_result(execution_result, "metadata")
        if not isinstance(metadata, Mapping):
            return

        report_ref = self._coerce_text(
            metadata.get("primaryReportRef")
            or metadata.get("primary_report_ref"),
            max_chars=200,
        )
        report_bundle = metadata.get("reportBundle") or metadata.get("report_bundle")
        if not report_ref and isinstance(report_bundle, Mapping):
            primary_ref = (
                report_bundle.get("primary_report_ref")
                or report_bundle.get("primaryReportRef")
            )
            if isinstance(primary_ref, Mapping):
                report_ref = self._coerce_text(
                    primary_ref.get("artifact_id")
                    or primary_ref.get("artifactId"),
                    max_chars=200,
                )
            else:
                report_ref = self._coerce_text(primary_ref, max_chars=200)
        if report_ref:
            self._report_created = True
            self._report_ref = report_ref
            self._publish_context["reportRef"] = report_ref

    @staticmethod
    def _node_selected_skill(node: Mapping[str, Any]) -> str:
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            return ""
        selected_skill = str(inputs.get("selectedSkill") or "").strip().lower()
        if selected_skill:
            return selected_skill
        metadata = inputs.get("metadata")
        if isinstance(metadata, Mapping):
            moonmind = metadata.get("moonmind")
            if isinstance(moonmind, Mapping):
                return str(moonmind.get("selectedSkill") or "").strip().lower()
        return ""

    @classmethod
    def _pr_publish_optional_for_plan(cls, nodes: list[Mapping[str, Any]]) -> bool:
        if not nodes:
            return False
        for node in nodes:
            tool = node.get("tool")
            if not isinstance(tool, Mapping):
                return False
            tool_type = str(tool.get("type") or tool.get("kind") or "").strip().lower()
            if tool_type != "agent_runtime":
                return False
            if cls._node_selected_skill(node) not in _PR_OPTIONAL_AGENT_SKILLS:
                return False
        return True

    def _execution_result_has_publishable_changes(self, execution_result: Any) -> bool:
        if self._extract_pull_request_url(execution_result):
            return True
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return False
        push_status = str(outputs.get("push_status") or "").strip().lower()
        if push_status in {"pushed", "published"}:
            return True
        commit_count = outputs.get("push_commit_count")
        if commit_count is None:
            commit_count = outputs.get("pushCommitCount")
        if isinstance(commit_count, bool):
            return False
        if isinstance(commit_count, (int, float)):
            return int(commit_count) > 0
        if isinstance(commit_count, str) and commit_count.strip().isdigit():
            return int(commit_count.strip()) > 0
        return False

    @staticmethod
    def _sanitize_operator_summary(summary: str | None) -> str | None:
        if not summary:
            return None
        scrubbed = scrub_github_tokens(summary).strip()
        return scrubbed or None

    def _compose_no_change_publish_reason(
        self,
        *,
        publish_mode: str,
    ) -> str:
        branch = self._coerce_text(self._publish_context.get("branch"), max_chars=120)
        base_ref = self._coerce_text(self._publish_context.get("baseRef"), max_chars=120)
        operator_summary = self._coerce_text(
            self._operator_summary,
            max_chars=700,
        )
        commit_count: int | None = None
        commit_count_value = self._publish_context.get("commitCount")
        if isinstance(commit_count_value, bool):
            commit_count_value = None
        if isinstance(commit_count_value, (int, float)) and int(commit_count_value) >= 0:
            commit_count = int(commit_count_value)
        elif isinstance(commit_count_value, str) and commit_count_value.strip().isdigit():
            commit_count = int(commit_count_value.strip())

        parts: list[str] = []
        if publish_mode == "pr":
            parts.append(
                "publishMode 'pr' requested, but no publishable diff was produced"
            )
        else:
            parts.append("publish skipped because no local changes were produced")

        if branch and base_ref and commit_count and commit_count > 0:
            parts.append(
                f"branch '{branch}' has {commit_count} commits ahead of {base_ref}"
            )
        elif branch and base_ref:
            parts.append(f"branch '{branch}' has no commits ahead of {base_ref}")
        elif branch:
            parts.append(f"branch '{branch}' has no commits to publish")

        if operator_summary:
            parts.append(f"final agent report: {operator_summary}")

        reason = ". ".join(part.rstrip(".") for part in parts if part)
        return f"{reason}." if reason else "publish skipped: no local changes"

    def _compose_success_completion_message(
        self,
        *,
        publish_detail: str | None = None,
        publish_mode: str = "",
    ) -> str:
        parts = ["Workflow completed successfully"]
        detail = self._coerce_text(publish_detail, max_chars=180)
        if detail and detail.lower() not in {
            "completed",
            "workflow completed successfully",
        }:
            parts.append(detail)

        operator_summary = self._coerce_text(self._operator_summary, max_chars=700)
        last_step_summary = self._coerce_text(self._last_step_summary, max_chars=700)
        final_summary = None
        for candidate in (last_step_summary, operator_summary):
            if (
                candidate
                and not self._is_transient_summary(candidate)
                and not is_generic_completion_summary(candidate)
            ):
                final_summary = candidate
                break
        if final_summary:
            parts.append(f"Final result: {final_summary}")

        if publish_mode == "pr":
            pull_request_url = self._coerce_text(
                self._publish_context.get("pullRequestUrl"),
                max_chars=200,
            )
            if pull_request_url:
                parts.append(f"Pull request: {pull_request_url}")

        if len(parts) == 1:
            return parts[0]
        message = ". ".join(part.rstrip(".") for part in parts if part)
        return f"{message}." if message else "Workflow completed successfully"

    def _determine_publish_completion(
        self,
        *,
        parameters: Mapping[str, Any],
    ) -> tuple[str, str, bool]:
        if self._plan_blocked_message:
            return ("failed", self._plan_blocked_message, True)

        publish_mode = self._publish_mode(parameters)
        if self._publish_status == "skipped":
            if publish_mode == "pr":
                self._publish_status = "failed"
                return (
                    "failed",
                    self._publish_reason
                    or "publishMode 'pr' requested but no local changes were produced",
                    True,
                )
            return ("no_changes", "Workflow completed with no local changes", False)

        if self._publish_status == "failed":
            return (
                "failed",
                self._publish_reason or "Publish failed",
                True,
            )

        missing_outcome = self._missing_required_outcome_reason(
            parameters=parameters,
            publish_mode=publish_mode,
        )
        if missing_outcome:
            self._publish_status = "failed"
            self._publish_reason = missing_outcome
            return ("failed", missing_outcome, True)

        if publish_mode == "none":
            return (
                "success",
                self._compose_success_completion_message(publish_mode=publish_mode),
                False,
            )

        if self._publish_status == "not_required":
            return (
                "success",
                self._compose_success_completion_message(
                    publish_detail=self._publish_reason,
                    publish_mode=publish_mode,
                ),
                False,
            )

        if publish_mode == "branch" and self._publish_status is None:
            self._publish_status = "failed"
            self._publish_reason = "branch publish outcome unknown"
            return (
                "failed",
                self._publish_reason,
                True,
            )

        if self._publish_status == "published":
            publish_detail = self._publish_reason
            if (
                publish_mode == "pr"
                and publish_detail
                and publish_detail.startswith("Jira issue output succeeded")
            ):
                publish_detail = None
            return (
                "success",
                self._compose_success_completion_message(
                    publish_detail=publish_detail,
                    publish_mode=publish_mode,
                ),
                False,
            )

        if (
            publish_mode == "pr"
            and self._integration is None
            and self._pull_request_url is None
        ):
            self._publish_status = "failed"
            self._publish_reason = "publishMode 'pr' requested but no PR was created"
            return (
                "failed",
                self._publish_reason,
                True,
            )

        return (
            "success",
            self._compose_success_completion_message(publish_mode=publish_mode),
            False,
        )

    def _missing_required_outcome_reason(
        self,
        *,
        parameters: Mapping[str, Any],
        publish_mode: str,
    ) -> str | None:
        if publish_mode == "pr" and not self._pull_request_created():
            return "publishMode 'pr' requested but no PR was created"
        if (
            publish_mode == "branch"
            and self._publish_status is None
            and not self._branch_published()
        ):
            return "branch publish outcome unknown"
        if self._merge_automation_requested(parameters) and not self._merge_happened():
            return "merge automation requested but PR was not merged"
        if self._report_requested(parameters) and not self._report_created:
            return "reportOutput requested but no final report was created"
        return None

    def _pull_request_created(self) -> bool:
        if self._pull_request_url:
            return True
        pull_request_url = self._coerce_text(
            self._publish_context.get("pullRequestUrl"),
            max_chars=500,
        )
        if pull_request_url:
            return True
        pr_number = self._publish_context.get("pullRequestNumber")
        return isinstance(pr_number, int) and pr_number > 0

    def _branch_published(self) -> bool:
        return self._publish_status == "published"

    def _merge_happened(self) -> bool:
        status = self._coerce_text(
            self._publish_context.get("mergeAutomationStatus"),
            max_chars=40,
        )
        if status in MERGE_AUTOMATION_SUCCESS_STATUSES:
            return True
        result = self._publish_context.get("mergeAutomationResult")
        if isinstance(result, Mapping):
            result_status = self._coerce_text(result.get("status"), max_chars=40)
            return result_status in MERGE_AUTOMATION_SUCCESS_STATUSES
        return False

    def _merge_automation_requested(self, parameters: Mapping[str, Any]) -> bool:
        return self._merge_automation_request(parameters) is not None

    def _report_requested(self, parameters: Mapping[str, Any]) -> bool:
        candidates = []
        task_payload = self._mapping_value(parameters, "task")
        if isinstance(task_payload, Mapping):
            candidates.append(
                task_payload.get("reportOutput") or task_payload.get("report_output")
            )
        candidates.append(
            parameters.get("reportOutput") or parameters.get("report_output")
        )
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            try:
                enabled = _coerce_bool(candidate.get("enabled"), default=False)
                required = _coerce_bool(candidate.get("required"), default=True)
            except ValueError:
                continue
            if enabled and required:
                return True
        return False

    def _merge_automation_request(
        self,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        candidates: list[Any] = []
        publish_payload = self._resolve_publish_payload(parameters)
        task_payload = self._mapping_value(parameters, "task")
        if isinstance(publish_payload, Mapping):
            candidates.append(
                publish_payload.get("mergeAutomation")
                or publish_payload.get("merge_automation")
            )
        if isinstance(task_payload, Mapping):
            candidates.append(
                task_payload.get("mergeAutomation")
                or task_payload.get("merge_automation")
            )
            task_publish = task_payload.get("publish")
            if isinstance(task_publish, Mapping):
                candidates.append(
                    task_publish.get("mergeAutomation")
                    or task_publish.get("merge_automation")
                )
        candidates.append(
            parameters.get("mergeAutomation") or parameters.get("merge_automation")
        )

        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            if not _coerce_bool(candidate.get("enabled"), default=False):
                continue
            timeout_config = candidate.get("timeouts")
            if not isinstance(timeout_config, Mapping):
                timeout_config = {}
            post_merge_jira_config = candidate.get("postMergeJira") or candidate.get(
                "post_merge_jira"
            )
            if not isinstance(post_merge_jira_config, Mapping):
                post_merge_jira_config = {}
            jira_issue_key = self._coerce_text(
                candidate.get("jiraIssueKey") or candidate.get("jira_issue_key"),
                max_chars=40,
            )
            post_merge_issue_key = self._coerce_text(
                post_merge_jira_config.get("issueKey")
                or post_merge_jira_config.get("issue_key"),
                max_chars=40,
            )
            effective_jira_issue_key = (
                jira_issue_key
                or post_merge_issue_key
                or self._canonical_jira_issue_key_from_parameters(parameters)
            )
            post_merge_jira: dict[str, Any] = dict(post_merge_jira_config)
            if effective_jira_issue_key and "enabled" not in post_merge_jira:
                post_merge_jira["enabled"] = True
            if effective_jira_issue_key and "required" not in post_merge_jira:
                post_merge_jira["required"] = True
            post_merge_jira.setdefault("strategy", "done_category")
            return {
                "enabled": True,
                "checks": self._coerce_text(candidate.get("checks"), max_chars=20)
                or "required",
                "automatedReview": self._coerce_text(
                    candidate.get("automatedReview")
                    or candidate.get("automated_review"),
                    max_chars=20,
                )
                or "required",
                "jiraStatus": self._coerce_text(
                    candidate.get("jiraStatus") or candidate.get("jira_status"),
                    max_chars=20,
                )
                or "optional",
                "mergeMethod": self._coerce_text(
                    candidate.get("mergeMethod") or candidate.get("merge_method"),
                    max_chars=20,
                )
                or "squash",
                "jiraIssueKey": effective_jira_issue_key,
                "postMergeJira": post_merge_jira,
                "fallbackPollSeconds": (
                    candidate.get("fallbackPollSeconds")
                    or candidate.get("fallback_poll_seconds")
                    or timeout_config.get("fallbackPollSeconds")
                    or timeout_config.get("fallback_poll_seconds")
                    or 120
                ),
                "expireAfterSeconds": (
                    candidate.get("expireAfterSeconds")
                    or candidate.get("expire_after_seconds")
                    or timeout_config.get("expireAfterSeconds")
                    or timeout_config.get("expire_after_seconds")
                ),
            }
        return None

    def _canonical_jira_issue_key_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> str | None:
        task_payload = self._mapping_value(parameters, "task")
        explicit_key = self._first_explicit_jira_issue_key(parameters, task_payload)
        if explicit_key:
            return explicit_key
        if not self._is_jira_backed_task(parameters, task_payload):
            return None
        keys: set[str] = set()
        for text in self._jira_issue_key_text_sources(parameters, task_payload):
            keys.update(
                match.group(0).upper()
                for match in _JIRA_ISSUE_KEY_PATTERN.finditer(text)
            )
            if len(keys) > 1:
                return None
        if len(keys) == 1:
            return next(iter(keys))
        return None

    def _first_explicit_jira_issue_key(
        self,
        parameters: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> str | None:
        mappings: list[Mapping[str, Any]] = [parameters, task_payload]
        for key in ("metadata", "origin", "inputs", "input", "traceability"):
            nested = task_payload.get(key)
            if isinstance(nested, Mapping):
                mappings.append(nested)
        for mapping in mappings:
            candidate = self._coerce_text(
                mapping.get("jiraIssueKey")
                or mapping.get("jira_issue_key")
                or mapping.get("issueKey")
                or mapping.get("issue_key"),
                max_chars=40,
            )
            if candidate and _JIRA_ISSUE_KEY_PATTERN.fullmatch(candidate.upper()):
                return candidate.upper()
        return None

    def _is_jira_backed_task(
        self,
        parameters: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> bool:
        skill_names: set[str] = set()
        for payload in (parameters, task_payload):
            for key in ("tool", "skill"):
                nested = payload.get(key)
                if isinstance(nested, Mapping):
                    name = self._coerce_text(
                        nested.get("name") or nested.get("id"),
                        max_chars=120,
                    )
                    if name:
                        skill_names.add(name.lower())
        skills_payload = task_payload.get("skills")
        if isinstance(skills_payload, Mapping):
            include = skills_payload.get("include")
            if isinstance(include, Sequence) and not isinstance(include, (str, bytes)):
                for item in include:
                    if isinstance(item, Mapping):
                        name = self._coerce_text(
                            item.get("name") or item.get("id"),
                            max_chars=120,
                        )
                        if name:
                            skill_names.add(name.lower())
                    else:
                        name = self._coerce_text(item, max_chars=120)
                        if name:
                            skill_names.add(name.lower())
        return bool(skill_names & _JIRA_BACKED_AGENT_SKILLS)

    def _jira_issue_key_text_sources(
        self,
        parameters: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> Iterable[str]:
        for payload in (parameters, task_payload):
            for key in ("title", "instructions", "summary", "description"):
                value = payload.get(key)
                if isinstance(value, str):
                    yield value
        steps = task_payload.get("steps")
        if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes)):
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                for key in ("title", "instructions", "summary", "description"):
                    value = step.get(key)
                    if isinstance(value, str):
                        yield value

    @staticmethod
    def _normalize_positive_int(
        value: Any,
        *,
        default: int | None,
        maximum: int,
    ) -> int | None:
        if value is None or value == "":
            return default
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return default
        if candidate <= 0:
            return default
        return min(candidate, maximum)

    @staticmethod
    def _parse_github_pull_request_url(url: str) -> dict[str, Any] | None:
        match = re.match(
            r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)",
            str(url or "").strip(),
            re.IGNORECASE,
        )
        if not match:
            return None
        return {
            "repo": f"{match.group('owner')}/{match.group('repo')}",
            "number": int(match.group("number")),
        }

    def _build_merge_gate_start_payload(
        self,
        *,
        parameters: Mapping[str, Any],
        pull_request_url: str,
        head_sha: str | None,
        parent_workflow_id: str,
        parent_run_id: str | None,
    ) -> dict[str, Any] | None:
        request = self._merge_automation_request(parameters)
        if request is None:
            return None
        parsed = self._parse_github_pull_request_url(pull_request_url)
        if parsed is None:
            return None
        repo = self._repo or parsed["repo"]
        normalized_head_sha = (
            self._coerce_text(head_sha, max_chars=80)
            or self._coerce_text(self._publish_context.get("headSha"), max_chars=80)
        )
        if not normalized_head_sha:
            return None
        pr_number = int(parsed["number"])
        fallback_poll_seconds = self._normalize_positive_int(
            request.get("fallbackPollSeconds"),
            default=120,
            maximum=3600,
        )
        expire_after_seconds = self._normalize_positive_int(
            request.get("expireAfterSeconds"),
            default=None,
            maximum=2_592_000,
        )
        timeouts: dict[str, Any] = {"fallbackPollSeconds": fallback_poll_seconds or 120}
        if expire_after_seconds is not None:
            timeouts["expireAfterSeconds"] = expire_after_seconds
        task_payload = self._mapping_value(parameters, "task") or {}
        task_runtime_payload = (
            self._mapping_value(task_payload, "runtime")
            if isinstance(task_payload, Mapping)
            else {}
        ) or {}
        resolver_template: dict[str, Any] = {
            "repository": repo,
            "targetRuntime": (
                self._coerce_text(
                    parameters.get("targetRuntime")
                    or task_runtime_payload.get("mode")
                    or task_runtime_payload.get("targetRuntime"),
                    max_chars=80,
                )
                or "codex"
            ),
            "requiredCapabilities": ["git", "gh"],
        }
        profile_id = self._inherited_execution_profile_ref(parameters)
        if profile_id:
            resolver_template["executionProfileRef"] = profile_id
        runtime_model = self._coerce_text(
            parameters.get("model"),
            max_chars=160,
        )
        if runtime_model:
            resolver_template["model"] = runtime_model
        runtime_effort = self._coerce_text(
            parameters.get("effort"),
            max_chars=80,
        )
        if runtime_effort:
            resolver_template["effort"] = runtime_effort
        return {
            "workflowType": "MoonMind.MergeAutomation",
            "parentWorkflowId": parent_workflow_id,
            "parentRunId": parent_run_id,
            "principal": self._owner_id or parent_workflow_id,
            "publishContextRef": (
                self._coerce_text(
                    self._publish_context.get("publishContextRef")
                    or self._publish_context.get("artifactRef"),
                    max_chars=500,
                )
                or f"artifact://workflow/{parent_workflow_id}/publish-context"
            ),
            "pullRequest": {
                "repo": repo,
                "number": pr_number,
                "url": pull_request_url,
                "headSha": normalized_head_sha,
                "headBranch": self._coerce_text(
                    self._publish_context.get("branch"),
                    max_chars=120,
                ),
                "baseBranch": self._coerce_text(
                    self._publish_context.get("baseRef"),
                    max_chars=120,
                ),
            },
            "jiraIssueKey": request.get("jiraIssueKey"),
            "mergeAutomationConfig": {
                "gate": {
                    "github": {
                        "checks": request.get("checks") or "required",
                        "automatedReview": request.get("automatedReview") or "required",
                    },
                    "jira": {
                        "status": request.get("jiraStatus") or "optional",
                        "issueKey": request.get("jiraIssueKey"),
                    },
                },
                "resolver": {
                    "skill": "pr-resolver",
                    "mergeMethod": request.get("mergeMethod") or "squash",
                },
                "timeouts": timeouts,
                "postMergeJira": request.get("postMergeJira") or {},
            },
            "resolverTemplate": resolver_template,
            "idempotencyKey": (
                f"merge-automation:{parent_workflow_id}:{repo}:{pr_number}:{normalized_head_sha}"
            ),
        }

    def _merge_automation_summary_from_context(self) -> dict[str, Any] | None:
        context = self._publish_context
        if not context:
            return None
        result = context.get("mergeAutomationResult")
        result_map = result if isinstance(result, Mapping) else {}
        status = self._coerce_text(
            context.get("mergeAutomationStatus") or result_map.get("status"),
            max_chars=40,
        )
        workflow_id = self._coerce_text(
            context.get("mergeAutomationWorkflowId"),
            max_chars=500,
        )
        if not status and not workflow_id and not result_map:
            return None
        pr_url = self._coerce_text(
            result_map.get("prUrl") or context.get("pullRequestUrl"),
            max_chars=500,
        )
        pr_number = result_map.get("prNumber")
        if pr_number is None and pr_url:
            parsed = self._parse_github_pull_request_url(pr_url)
            if parsed is not None:
                pr_number = parsed.get("number")
        resolver_ids = result_map.get("resolverChildWorkflowIds")
        if not isinstance(resolver_ids, list):
            resolver_ids = []
        blockers = result_map.get("blockers")
        if not isinstance(blockers, list):
            blockers = []
        artifact_refs = result_map.get("artifactRefs")
        if not isinstance(artifact_refs, Mapping):
            artifact_refs = {}
        summary: dict[str, Any] = {
            "enabled": True,
            "status": status or "unknown",
        }
        if pr_number is not None:
            summary["prNumber"] = pr_number
        if pr_url:
            summary["prUrl"] = pr_url
        latest_head_sha = self._coerce_text(
            result_map.get("latestHeadSha")
            or result_map.get("lastHeadSha")
            or context.get("headSha"),
            max_chars=80,
        )
        if latest_head_sha:
            summary["latestHeadSha"] = latest_head_sha
        if workflow_id:
            summary["childWorkflowId"] = workflow_id
        normalized_resolver_ids: list[str] = []
        for item in resolver_ids:
            resolver_id = self._coerce_text(item, max_chars=500)
            if resolver_id:
                normalized_resolver_ids.append(resolver_id)
        summary["resolverChildWorkflowIds"] = normalized_resolver_ids
        cycles = result_map.get("cycles")
        if cycles is not None:
            summary["cycles"] = cycles
        summary["blockers"] = [
            dict(blocker) for blocker in blockers if isinstance(blocker, Mapping)
        ]
        if artifact_refs:
            summary["artifactRefs"] = dict(artifact_refs)
        post_merge_jira = result_map.get("postMergeJira")
        if isinstance(post_merge_jira, Mapping):
            summary["postMergeJira"] = dict(post_merge_jira)
        return summary

    def _merge_automation_child_succeeded(self, result: Any) -> bool:
        status = self._coerce_text(self._get_from_result(result, "status"), max_chars=40)
        return status in MERGE_AUTOMATION_SUCCESS_STATUSES

    def _merge_automation_child_canceled(self, result: Any) -> bool:
        status = self._coerce_text(self._get_from_result(result, "status"), max_chars=40)
        return status == MERGE_AUTOMATION_CANCELED_STATUS

    def _merge_automation_child_status_valid(self, result: Any) -> bool:
        status = self._coerce_text(self._get_from_result(result, "status"), max_chars=40)
        return bool(status) and status in MERGE_AUTOMATION_TERMINAL_STATUSES

    def _merge_automation_failure_reason(self, result: Any) -> str:
        status = self._coerce_text(self._get_from_result(result, "status"), max_chars=40)
        if not status:
            return "merge automation failed: missing terminal status"
        if status not in MERGE_AUTOMATION_TERMINAL_STATUSES:
            return f"merge automation failed: unsupported terminal status {status}"
        blockers = self._get_from_result(result, "blockers")
        blocker_summary = ""
        if isinstance(blockers, list):
            for blocker in blockers:
                if not isinstance(blocker, Mapping):
                    continue
                summary = self._coerce_text(blocker.get("summary"), max_chars=500)
                if summary:
                    blocker_summary = summary
                    break
        if blocker_summary:
            return f"merge automation {status or 'failed'}: {blocker_summary}"
        summary = self._coerce_text(self._get_from_result(result, "summary"), max_chars=500)
        if summary:
            return f"merge automation {status or 'failed'}: {summary}"
        return f"merge automation {status or 'failed'}"

    async def _maybe_start_merge_gate(
        self,
        *,
        parameters: Mapping[str, Any],
        pull_request_url: str | None,
    ) -> None:
        if not pull_request_url:
            return
        info = workflow.info()
        payload = self._build_merge_gate_start_payload(
            parameters=parameters,
            pull_request_url=pull_request_url,
            head_sha=self._coerce_text(self._publish_context.get("headSha"), max_chars=80),
            parent_workflow_id=info.workflow_id,
            parent_run_id=info.run_id,
        )
        if payload is None:
            return
        workflow_id = str(payload["idempotencyKey"])
        if (
            self._publish_context.get("mergeAutomationWorkflowId") == workflow_id
            and self._publish_context.get("mergeAutomationResult") is not None
        ):
            return
        self._awaiting_external = True
        self._publish_context["mergeAutomationWorkflowId"] = workflow_id
        self._publish_context["mergeAutomationStatus"] = "awaiting_child"
        self._waiting_reason = "Waiting for PR merge automation."
        self._attention_required = False
        self._set_state(
            STATE_AWAITING_EXTERNAL,
            summary="Waiting for PR merge automation.",
        )
        try:
            child_result = await workflow.execute_child_workflow(
                "MoonMind.MergeAutomation",
                payload,
                id=workflow_id,
                task_queue=WORKFLOW_TASK_QUEUE,
                cancellation_type=ChildWorkflowCancellationType.TRY_CANCEL,
                static_summary="Waiting for pull request merge readiness",
                static_details=f"Merge automation for {pull_request_url}",
            )
        except CancelledError:
            self._awaiting_external = False
            self._waiting_reason = None
            self._publish_context["mergeAutomationStatus"] = MERGE_AUTOMATION_CANCELED_STATUS
            self._publish_context["mergeAutomationSummary"] = (
                "Merge automation canceled while parent was awaiting child workflow."
            )
            self._update_memo()
            self._update_search_attributes()
            raise
        except Exception:
            self._awaiting_external = False
            self._waiting_reason = None
            self._publish_context["mergeAutomationStatus"] = "failed"
            self._update_memo()
            self._update_search_attributes()
            raise
        self._awaiting_external = False
        self._waiting_reason = None
        self._publish_context["mergeAutomationResult"] = (
            dict(child_result) if isinstance(child_result, Mapping) else child_result
        )
        child_status = self._coerce_text(
            self._get_from_result(child_result, "status"),
            max_chars=40,
        ) or "unknown"
        child_status_valid = self._merge_automation_child_status_valid(child_result)
        reason = (
            self._merge_automation_failure_reason(child_result)
            if not self._merge_automation_child_succeeded(child_result)
            else None
        )
        if child_status_valid:
            self._publish_context["mergeAutomationStatus"] = child_status
        else:
            self._publish_context["mergeAutomationStatus"] = "failed"
        if reason:
            self._publish_context["mergeAutomationSummary"] = reason
        self._update_memo()
        self._update_search_attributes()
        if self._merge_automation_child_canceled(child_result):
            self._cancel_requested = True
            self._close_status = CLOSE_STATUS_CANCELED
            self._set_state(
                STATE_CANCELED,
                summary=self._merge_automation_failure_reason(child_result),
            )
            return
        if not self._merge_automation_child_succeeded(child_result):
            raise ValueError(reason or "merge automation failed")

    async def _resolve_agent_node_skillset_ref(
        self,
        *,
        task_skills: Mapping[str, Any] | Any | None,
        node_skills: Mapping[str, Any] | Any | None = None,
        node_inputs: Mapping[str, Any],
        node_id: str,
        existing_skillset_ref: str | None,
    ) -> str | None:
        """Resolve effective task/step skill intent before AgentRun launch."""

        if existing_skillset_ref:
            return existing_skillset_ref

        effective = build_effective_task_skill_selectors(
            task_skills,
            node_skills if node_skills is not None else node_inputs.get("skills"),
        )
        selected_skill = selected_agent_skill(node_inputs)
        if selected_skill == "auto":
            selected_skill = ""
        if effective is None and not selected_skill:
            return None

        effective_payload = (
            effective.model_dump(mode="json", exclude_none=True)
            if effective is not None
            else {}
        )
        if selected_skill:
            excluded_names = {
                str(item.get("name") or "").strip().lower()
                if isinstance(item, WorkflowMapping)
                else str(item or "").strip().lower()
                for item in (effective_payload.get("exclude") or [])
            }
            excluded_names.discard("")
            if selected_skill in excluded_names:
                raise ValueError(
                    f"selected skill '{selected_skill}' cannot also be excluded"
                )
            include = list(effective_payload.get("include") or [])
            included_names = {
                str(item.get("name") or "").strip().lower()
                for item in include
                if isinstance(item, WorkflowMapping)
            }
            if selected_skill not in included_names:
                include.append({"name": selected_skill})
                effective_payload["include"] = include

        selector = SkillSelector.model_validate(effective_payload)
        if not selector.sets and not selector.include and not selector.exclude:
            return None

        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("agent_skill.resolve")
        resolved = await workflow.execute_activity(
            "agent_skill.resolve",
            args=[
                selector,
                self._principal(),
                node_inputs.get("workspaceRoot"),
                False,
                False,
            ],
            **self._execute_kwargs_for_route(route),
        )
        if selected_skill:
            normalized_skill = str(selected_skill).strip().lower()
            resolved_skill_names = self._resolved_skillset_skill_names(resolved)
            if normalized_skill not in resolved_skill_names:
                raise ValueError(
                    f"selected skill '{selected_skill}' was not resolved into the "
                    "agent skill snapshot"
                )
        manifest_ref = self._resolved_skillset_field(
            resolved,
            "manifest_ref",
            "manifestRef",
        )
        if manifest_ref:
            return str(manifest_ref)
        snapshot_id = self._resolved_skillset_field(
            resolved,
            "snapshot_id",
            "snapshotId",
        )
        return str(snapshot_id) if snapshot_id else None

    @staticmethod
    def _resolved_skillset_skill_names(resolved: Any) -> set[str]:
        if resolved is None:
            return set()
        skills = (
            resolved.get("skills")
            if isinstance(resolved, WorkflowMapping)
            else getattr(resolved, "skills", None)
        )
        if not isinstance(skills, Iterable) or isinstance(skills, (str, bytes)):
            return set()
        names: set[str] = set()
        for skill in skills:
            if isinstance(skill, WorkflowMapping):
                raw_name = (
                    skill.get("skill_name")
                    or skill.get("skillName")
                    or skill.get("name")
                )
            else:
                raw_name = (
                    getattr(skill, "skill_name", None)
                    or getattr(skill, "skillName", None)
                    or getattr(skill, "name", None)
                )
            name = str(raw_name or "").strip().lower()
            if name:
                names.add(name)
        return names

    @staticmethod
    def _resolved_skillset_field(resolved: Any, *keys: str) -> Any:
        if resolved is None:
            return None
        is_mapping = isinstance(resolved, WorkflowMapping)
        for key in keys:
            value = resolved.get(key) if is_mapping else getattr(resolved, key, None)
            if value:
                return value
        return None

    @staticmethod
    def _existing_agent_skillset_ref(
        *,
        parameters: Mapping[str, Any],
        node: Mapping[str, Any],
        node_inputs: Mapping[str, Any],
    ) -> str | None:
        for source in (node_inputs, node, parameters):
            for key in ("resolvedSkillsetRef", "resolved_skillset_ref"):
                value = source.get(key)
                if value is not None:
                    candidate = str(value).strip()
                    if candidate:
                        return candidate
        return None

    def _build_agent_execution_request(
        self,
        *,
        node_inputs: dict[str, Any],
        node_id: str,
        tool_name: str,
        resolved_skillset_ref: str | None = None,
        workflow_parameters: Mapping[str, Any] | None = None,
    ) -> "AgentExecutionRequest":
        """Build an ``AgentExecutionRequest`` from plan-node inputs and workflow context."""
        runtime_block_raw = node_inputs.get("runtime")
        runtime_block = (
            runtime_block_raw if isinstance(runtime_block_raw, Mapping) else {}
        )
        agent_id = self._agent_id_from_runtime_inputs(
            node_inputs=node_inputs,
            fallback_name=tool_name,
        )
        if not agent_id:
            raise ValueError(
                "agent_runtime plan node must specify an agent_id "
                "(via inputs.runtime.mode, inputs.targetRuntime, or tool.name)"
            )

        agent_kind = self._agent_kind_for_id(agent_id)
        # Prefer runtime_block profile values (set by the runtime planner)
        # over top-level node_inputs keys, which may have been corrupted
        # by AI-generated plan modifications or template injection.
        raw_execution_profile_ref = (
            runtime_block.get("executionProfileRef")
            or runtime_block.get("profileId")
            or runtime_block.get("providerProfile")
            or node_inputs.get("executionProfileRef")
            or node_inputs.get("profileId")
            or node_inputs.get("providerProfile")
        )
        execution_profile_ref = None
        if raw_execution_profile_ref is not None:
            candidate = str(raw_execution_profile_ref).strip() or None
            candidate = self._validated_execution_profile_ref(
                candidate,
                agent_id=agent_id,
                source_label="Plan node",
            )
            execution_profile_ref = candidate
        if execution_profile_ref is None and workflow_parameters is not None:
            execution_profile_ref = self._inherited_execution_profile_ref(
                workflow_parameters,
                agent_id=agent_id,
            )
        wf_info = workflow.info()
        correlation_id = wf_info.workflow_id
        idempotency_key = f"{wf_info.workflow_id}:{node_id}:{wf_info.run_id}"

        workspace_spec: dict[str, Any] = {}
        for ws_key in (
            "repository",
            "repo",
            "startingBranch",
            "targetBranch",
            "branch",
        ):
            ws_val = node_inputs.get(ws_key)
            if ws_val is not None:
                workspace_spec[ws_key] = ws_val

        parameters: dict[str, Any] = {}
        for param_key in (
            "model",
            "effort",
            "publishMode",
            "commitMessage",
            "allowed_tools",
            "stepCount",
            "maxAttempts",
            "steps",
        ):
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
        raw_report_output = (
            node_inputs.get("reportOutput")
            or node_inputs.get("report_output")
            or (
                workflow_parameters.get("reportOutput")
                if isinstance(workflow_parameters, Mapping)
                else None
            )
            or (
                workflow_parameters.get("report_output")
                if isinstance(workflow_parameters, Mapping)
                else None
            )
        )
        if isinstance(raw_report_output, Mapping):
            report_output = self._json_mapping(
                raw_report_output,
                path=f"node[{node_id}].reportOutput",
            )
            if _coerce_bool(report_output.get("enabled"), default=False):
                report_output["executionRef"] = {
                    "namespace": wf_info.namespace,
                    "workflow_id": wf_info.workflow_id,
                    "run_id": wf_info.run_id,
                }
                parameters["reportOutput"] = report_output
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
                moonmind_payload["reportOutput"] = report_output
                metadata_payload["moonmind"] = moonmind_payload
                parameters["metadata"] = metadata_payload
        selected_skill = str(node_inputs.get("selectedSkill") or "").strip()
        if selected_skill:
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
            moonmind_payload["selectedSkill"] = selected_skill
            metadata_payload["moonmind"] = moonmind_payload
            parameters["metadata"] = metadata_payload
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

        step_attempt = self._step_attempt_for(node_id)
        if step_attempt is not None:
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
            moonmind_payload["stepLedger"] = {
                "logicalStepId": node_id,
                "attempt": step_attempt,
                "scope": "step",
            }
            metadata_payload["moonmind"] = moonmind_payload
            parameters["metadata"] = metadata_payload

        return AgentExecutionRequest(
            agent_kind=agent_kind,
            agent_id=agent_id,
            execution_profile_ref=execution_profile_ref,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            instruction_ref=node_inputs.get("instructions")
            or node_inputs.get("instructionRef"),
            resolved_skillset_ref=resolved_skillset_ref,
            input_refs=node_inputs.get("inputRefs") or [],
            workspace_spec=workspace_spec,
            parameters=parameters,
            timeout_policy=node_inputs.get("timeoutPolicy") or {},
            retry_policy=node_inputs.get("retryPolicy") or {},
            approval_policy=node_inputs.get("approvalPolicy") or {},
            callback_policy=node_inputs.get("callbackPolicy") or {},
            profile_selector=profile_selector,
        )

    def _inherited_execution_profile_ref(
        self,
        parameters: Mapping[str, Any],
        *,
        agent_id: str | None = None,
    ) -> str | None:
        """Resolve the parent-request provider profile for child workflow launches."""
        task_payload = self._mapping_value(parameters, "task") or {}
        task_runtime_payload = (
            self._mapping_value(task_payload, "runtime")
            if isinstance(task_payload, Mapping)
            else {}
        ) or {}
        for source in (task_runtime_payload, parameters):
            if not isinstance(source, Mapping):
                continue
            profile_id = self._coerce_text(
                source.get("executionProfileRef")
                or source.get("execution_profile_ref")
                or source.get("profileId")
                or source.get("profile_id")
                or source.get("providerProfile")
                or source.get("provider_profile"),
                max_chars=160,
            )
            if profile_id:
                source_agent_id = self._agent_id_from_runtime_inputs(
                    node_inputs={"runtime": source},
                )
                if agent_id and source_agent_id:
                    if self._managed_runtime_id(agent_id) != self._managed_runtime_id(
                        source_agent_id
                    ):
                        self._get_logger().warning(
                            "Inherited execution_profile_ref '%s' targets runtime "
                            "'%s' but child runtime is '%s'; falling back to "
                            "auto-selection.",
                            profile_id,
                            self._managed_runtime_id(source_agent_id),
                            self._managed_runtime_id(agent_id),
                        )
                        return None
                return self._validated_execution_profile_ref(
                    profile_id,
                    agent_id=agent_id,
                    source_label="Inherited",
                )
        return None

    def _validated_execution_profile_ref(
        self,
        profile_id: str | None,
        *,
        agent_id: str | None,
        source_label: str,
    ) -> str | None:
        """Validate a profile ref against known snapshots when they are available."""
        if profile_id is None:
            return None
        profile_snapshots = getattr(self, "_profile_snapshots", None)
        if profile_snapshots is None:
            return profile_id
        if profile_id not in profile_snapshots:
            self._get_logger().warning(
                "%s execution_profile_ref '%s' is not a known profile for this "
                "runtime; falling back to auto-selection.",
                source_label,
                profile_id,
            )
            return None
        if not isinstance(profile_snapshots, Mapping):
            return profile_id
        snapshot = profile_snapshots.get(profile_id)
        if not isinstance(snapshot, Mapping):
            return profile_id
        runtime_id = self._coerce_text(snapshot.get("runtime_id"), max_chars=160)
        if not runtime_id or not agent_id:
            return profile_id
        child_runtime_id = self._managed_runtime_id(agent_id)
        if runtime_id != child_runtime_id:
            self._get_logger().warning(
                "%s execution_profile_ref '%s' belongs to runtime '%s' but child "
                "runtime is '%s'; falling back to auto-selection.",
                source_label,
                profile_id,
                runtime_id,
                child_runtime_id,
            )
            return None
        return profile_id

    @staticmethod
    def _managed_runtime_id(agent_id: str) -> str:
        runtime_mapping = {
            "gemini_cli": "gemini_cli",
            "claude": "claude_code",
            "claude_code": "claude_code",
            "codex": "codex_cli",
            "codex_cli": "codex_cli",
        }
        normalized_agent_id = _normalize_agent_runtime_id(agent_id)
        return runtime_mapping.get(normalized_agent_id, normalized_agent_id)

    @staticmethod
    def _plan_node_tool_mapping(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
        tool = node.get("tool")
        skill = node.get("skill")
        if isinstance(tool, Mapping):
            return tool
        if isinstance(skill, Mapping):
            return skill
        return None

    @staticmethod
    def _agent_id_from_runtime_inputs(
        *,
        node_inputs: Mapping[str, Any],
        fallback_name: str | None = None,
    ) -> str:
        runtime_block_raw = node_inputs.get("runtime")
        runtime_block = (
            runtime_block_raw if isinstance(runtime_block_raw, Mapping) else {}
        )
        return str(
            runtime_block.get("mode")
            or runtime_block.get("agent_id")
            or node_inputs.get("targetRuntime")
            or fallback_name
            or ""
        ).strip()

    def _agent_runtime_id_for_plan_node(
        self,
        node: Mapping[str, Any],
    ) -> str | None:
        selected_node = self._plan_node_tool_mapping(node)
        if selected_node is None:
            return None
        tool_type = (
            str(selected_node.get("type") or selected_node.get("kind") or "skill")
            .strip()
            .lower()
        )
        if tool_type != "agent_runtime":
            return None
        node_inputs_raw = node.get("inputs")
        node_inputs = node_inputs_raw if isinstance(node_inputs_raw, Mapping) else {}
        agent_id = self._agent_id_from_runtime_inputs(
            node_inputs=node_inputs,
            fallback_name=selected_node.get("name") or selected_node.get("id"),
        )
        return agent_id or None

    def _mark_slot_continuity_for_next_step(
        self,
        *,
        request: "AgentExecutionRequest",
        ordered_nodes: Sequence[Mapping[str, Any]],
        current_index: int,
    ) -> None:
        if request.agent_kind != "managed" or current_index >= len(ordered_nodes):
            return
        next_agent_id = self._agent_runtime_id_for_plan_node(
            ordered_nodes[current_index]
        )
        if not next_agent_id or self._agent_kind_for_id(next_agent_id) != "managed":
            return
        if self._managed_runtime_id(next_agent_id) != self._managed_runtime_id(
            request.agent_id
        ):
            return

        parameters = dict(request.parameters or {})
        metadata = (
            dict(parameters.get("metadata"))
            if isinstance(parameters.get("metadata"), Mapping)
            else {}
        )
        moonmind_payload = (
            dict(metadata.get("moonmind"))
            if isinstance(metadata.get("moonmind"), Mapping)
            else {}
        )
        moonmind_payload["slotContinuity"] = {
            "reserveForImmediateFollowup": True,
        }
        metadata["moonmind"] = moonmind_payload
        parameters["metadata"] = metadata
        request.parameters = parameters

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
        if normalized_agent_id in {
            "claude",
            "claude_code",
        } and requested_model.startswith("minimax"):
            selector["providerId"] = "minimax"

        return selector

    def _map_agent_run_result(self, result: Any) -> dict[str, Any]:
        """Convert ``AgentRunResult`` to the dict format the execution loop expects."""
        if isinstance(result, dict):
            failure = result.get("failure_class") or result.get("failureClass")
            summary = result.get("summary") or ""
            output_refs = result.get("output_refs") or result.get("outputRefs") or []
            diagnostics_ref = result.get("diagnostics_ref") or result.get(
                "diagnosticsRef"
            )
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
                artifact_read_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                    "artifact.read"
                )
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
                            "publishMode": self._publish_mode(integration_parameters)
                            or "none",
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

            while (
                not self._resume_requested
                and not self._cancel_requested
                and not _poll_terminal
            ):
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
                    if status in (
                        "completed",
                        "failed",
                        "canceled",
                        "awaiting_feedback",
                    ):
                        # Temporal records which branch was taken; replay without the patch marker
                        # must follow the pre-patch control flow (else branch + clear below).
                        if workflow.patched(INTEGRATION_POLL_LOOP_PATCH):
                            _poll_terminal = True
                        else:
                            self._resume_requested = True
                        self._external_status = (
                            "completed" if status == "awaiting_feedback" else status
                        )
                        if status == "failed":
                            self._get_logger().warning(
                                f"Integration failed: {poll_result}"
                            )
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
            self._get_logger().info(
                "Jules branch-publish: fetching result for PR merge"
            )
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
                pr_url = self._get_from_result(
                    fetch_result, "external_url"
                ) or self._get_from_result(fetch_result, "url")
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
                    base_override = parameters.get("publishBaseBranch") or ws.get(
                        "publishBaseBranch"
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
                    merge_summary = self._get_from_result(merge_result, "summary") or ""
                    if merged:
                        self._pull_request_url = pr_url
                        self._publish_status = "published"
                        self._publish_reason = "published branch"
                        self._get_logger().info(
                            "Jules branch-publish: PR merged: %s", merge_summary
                        )
                    else:
                        raise ValueError(
                            f"Jules branch-publish: merge failed: {merge_summary}"
                        )
                else:
                    raise ValueError("Jules branch-publish: no PR URL found in result")
            except Exception as exc:
                raise ValueError(
                    f"Jules branch-publish auto-merge failed: {exc}"
                ) from exc

    async def _run_proposals_stage(self, *, parameters: dict[str, Any]) -> None:
        """Best-effort proposal generation phase.

        Runs only when proposal generation is requested by the run payload.
        Failures are logged but do not fail the workflow.
        """
        if not self._proposal_generation_requested(parameters):
            return

        if workflow.patched("enable_task_proposals_gate"):
            if not settings.workflow.enable_task_proposals:
                self._get_logger().info("Task proposal generation is globally disabled")
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
                generate_payload["idempotency_key"] = (
                    f"{workflow.info().workflow_id}_proposal_generate"
                )

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
            submit_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("proposal.submit")
            task_node = parameters.get("task")
            task = task_node if isinstance(task_node, dict) else {}
            policy = task.get("proposalPolicy")
            policy_payload: dict[str, Any] = {}
            if isinstance(policy, dict):
                from moonmind.workflows.tasks.task_contract import TaskProposalPolicy

                try:
                    parsed_policy = TaskProposalPolicy.model_validate(policy)
                    policy_payload = parsed_policy.model_dump(
                        by_alias=True,
                        exclude_none=True,
                    )
                except Exception as exc:
                    self._get_logger().warning(
                        "Failed to validate task.proposalPolicy: %s", exc
                    )
            origin = {
                "source": "workflow",
                "workflow_id": workflow.info().workflow_id,
                "temporal_run_id": workflow.info().run_id,
                "trigger_repo": self._repo or "",
            }
            submit_payload = {
                "candidates": candidate_list,
                "policy": policy_payload,
                "origin": origin,
                "principal": self._principal(),
            }
            if workflow.patched("idempotency_key_phase3"):
                submit_payload["idempotency_key"] = (
                    f"{workflow.info().workflow_id}_proposal_submit"
                )

            submit_result = await workflow.execute_activity(
                "proposal.submit",
                submit_payload,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                **self._execute_kwargs_for_route(submit_route),
            )
            if isinstance(submit_result, dict):
                self._proposals_submitted = submit_result.get("submitted_count", 0)
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
            await self._terminate_task_scoped_sessions(reason=status)
        except Exception as exc:
            self._get_logger().warning(
                "Failed to terminate task-scoped agent sessions: %s", exc
            )
        try:
            self._get_logger().info("Generating finish summary.")

            publish_mode = self._publish_mode(parameters)
            publish_status = "skipped" if status != "success" else "published"
            publish_reason = (
                "run did not complete successfully"
                if status in ("failed", "canceled")
                else (
                    "publishing disabled"
                    if publish_mode == "none"
                    else "published successfully"
                )
            )

            # Map Temporal status back to FinishOutcome code.
            code = "FAILED" if status == "failed" else "NO_CHANGES"
            if status == "canceled":
                code = "CANCELLED"

            if workflow.patched(RUN_WORKFLOW_PUBLISH_OUTCOME_PATCH):
                if status == "success":
                    if publish_mode == "none":
                        code = "PUBLISH_DISABLED"
                        publish_status = "skipped"
                        publish_reason = "publishing disabled"
                    elif self._publish_status == "skipped":
                        code = "NO_CHANGES"
                        publish_status = "skipped"
                        publish_reason = (
                            self._publish_reason or "publish skipped: no local changes"
                        )
                    elif self._publish_status == "not_required":
                        code = "PUBLISH_DISABLED"
                        publish_status = "skipped"
                        publish_reason = (
                            self._publish_reason or "publish output not required"
                        )
                    elif publish_mode == "pr":
                        code = "PUBLISHED_PR"
                        publish_status = "published"
                        publish_reason = self._publish_reason or "published pull request"
                    elif publish_mode == "branch":
                        code = "PUBLISHED_BRANCH"
                        publish_status = "published"
                        publish_reason = self._publish_reason or "published branch"
                elif self._publish_status == "failed":
                    publish_status = "failed"
                    publish_reason = self._publish_reason or error or "publish failed"
                elif status == "failed" and self._publish_status == "not_required":
                    publish_status = "skipped"
                    publish_reason = (
                        self._publish_reason
                        or error
                        or "publish output not required"
                    )
            else:
                if status == "success":
                    if publish_mode == "pr":
                        # Guard with workflow.patched for replay safety.
                        if workflow.patched("run-workflow-pr-status-v1"):
                            # Use explicit PR URL tracking from execution stage
                            code = (
                                "PUBLISHED_PR"
                                if self._pull_request_url
                                else "NO_PR_CREATED"
                            )
                        else:
                            code = "PUBLISHED_PR"
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
                    "durationMs": int(
                        (workflow.now() - workflow.info().start_time).total_seconds()
                        * 1000
                    ),
                },
                "finishOutcome": {
                    "code": code,
                    "stage": self._state,
                    "reason": error or "completed",
                },
                "publish": {
                    "mode": publish_mode,
                    "status": publish_status,
                    "reason": publish_reason,
                },
                "proposals": {
                    "requested": self._proposal_generation_requested(parameters),
                    "generatedCount": self._proposals_generated,
                    "submittedCount": self._proposals_submitted,
                    "errors": self._proposals_errors,
                },
                "dependencies": {
                    "declaredIds": list(self._declared_dependencies),
                    "waited": self._dependency_wait_occurred,
                    "waitDurationMs": int(self._dependency_wait_duration_ms or 0),
                    "resolution": self._dependency_resolution
                    or DEPENDENCY_RESOLUTION_NOT_APPLICABLE,
                    "failedDependencyId": self._failed_dependency_id,
                    "outcomes": self._dependency_outcomes(),
                },
            }
            if self._operator_summary:
                finish_summary["operatorSummary"] = self._operator_summary
            if self._publish_context:
                finish_summary["publishContext"] = dict(self._publish_context)
                merge_automation_summary = self._merge_automation_summary_from_context()
                if merge_automation_summary:
                    finish_summary["mergeAutomation"] = merge_automation_summary
            if self._last_step_id or self._last_step_summary or self._last_diagnostics_ref:
                finish_summary["lastStep"] = {
                    "id": self._last_step_id,
                    "summary": self._last_step_summary,
                    "diagnosticsRef": self._last_diagnostics_ref,
                }

            artifact_create_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "artifact.create"
            )
            artifact_ref, upload_desc = await workflow.execute_activity(
                "artifact.create",
                {
                    "principal": self._principal(),
                    "name": "reports/run_summary.json",
                    "content_type": "application/json",
                },
                **self._execute_kwargs_for_route(artifact_create_route),
            )

            artifact_write_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "artifact.write_complete"
            )
            resolved_artifact_id = (
                self._get_from_result(artifact_ref, "artifact_id")
                or self._get_from_result(artifact_ref, "artifactId")
                or ""
            )
            await execute_typed_activity(
                "artifact.write_complete",
                ArtifactWriteCompleteInput(
                    principal=self._principal(),
                    artifact_id=resolved_artifact_id,
                    payload=json.dumps(finish_summary).encode("utf-8"),
                    content_type="application/json",
                ),
                **self._execute_kwargs_for_route(artifact_write_route),
            )
            self._summary_ref = resolved_artifact_id
        except Exception as exc:
            self._get_logger().warning(f"Failed to generate finish summary: {exc}")

    async def _record_terminal_state(
        self,
        *,
        state: str,
        close_status: str,
        summary: str | None,
        error_category: str | None = None,
    ) -> None:
        if not workflow.patched(RUN_TERMINAL_STATE_ACTIVITY_PATCH):
            return

        activity_task: asyncio.Task[Any] | None = None
        try:
            terminal_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "execution.record_terminal_state"
            )
            activity_task = asyncio.create_task(
                execute_typed_activity(
                    "execution.record_terminal_state",
                    ExecutionTerminalStateInput(
                        workflowId=workflow.info().workflow_id,
                        state=state,
                        closeStatus=close_status,
                        summary=summary,
                        errorCategory=error_category,
                    ),
                    cancellation_type=ActivityCancellationType.ABANDON,
                    **self._execute_kwargs_for_route(terminal_route),
                )
            )
            await asyncio.shield(activity_task)
        except (CancelledError, asyncio.CancelledError):
            self._get_logger().warning(
                "Cancellation received while recording terminal execution state; "
                "waiting for shielded activity to finish."
            )
            try:
                if activity_task is not None:
                    await activity_task
            except Exception as exc:
                self._get_logger().warning(
                    "Failed to record terminal execution state after cancellation: %s",
                    exc,
                )
            raise
        except Exception as exc:
            self._get_logger().warning(
                "Failed to record terminal execution state: %s", exc
            )

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

    def _dependency_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "dependencies": {
                "declaredIds": list(self._declared_dependencies),
                "waited": self._dependency_wait_occurred,
                "waitDurationMs": int(self._dependency_wait_duration_ms or 0),
                "resolution": self._dependency_resolution
                or DEPENDENCY_RESOLUTION_NOT_APPLICABLE,
                "failedDependencyId": self._failed_dependency_id,
                "outcomes": self._dependency_outcomes(),
            },
        }
        if self._dependency_failure is not None:
            metadata["dependency_failure"] = dict(self._dependency_failure)
        return metadata

    def _update_search_attributes(self) -> None:
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

        pairs = [
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_state"),
                self._state,
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_entry"),
                self._entry or "run",
            ),
            SearchAttributePair(
                SearchAttributeKey.for_datetime("mm_updated_at"),
                workflow.now(),
            ),
            SearchAttributePair(
                SearchAttributeKey.for_bool("mm_has_dependencies"),
                bool(self._declared_dependencies),
            ),
            SearchAttributePair(
                SearchAttributeKey.for_int("mm_dependency_count"),
                len(self._declared_dependencies),
            ),
        ]
        if self._owner_type:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_owner_type"),
                    self._owner_type,
                )
            )
        if self._owner_id:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_owner_id"),
                    self._owner_id,
                )
            )
        if self._repo:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_repo"),
                    self._repo,
                )
            )
        if self._integration:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_integration"),
                    self._integration,
                )
            )
        if self._scheduled_for:
            try:
                pairs.append(
                    SearchAttributePair(
                        SearchAttributeKey.for_datetime("mm_scheduled_for"),
                        datetime.fromisoformat(
                            self._scheduled_for.replace("Z", "+00:00")
                        ),
                    )
                )
            except ValueError:
                self._get_logger().warning(
                    "Could not parse scheduled_for for search attribute: %s",
                    self._scheduled_for,
                )
        try:
            workflow.upsert_search_attributes(pairs)
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
        if workflow.patched("run-memo-runtime-skill-visibility"):
            if self._target_runtime:
                memo_dict["targetRuntime"] = self._target_runtime
            if self._target_skill:
                memo_dict["targetSkill"] = self._target_skill
        if self._input_ref:
            memo_dict["input_artifact_ref"] = self._input_ref
        if self._plan_ref:
            memo_dict["plan_artifact_ref"] = self._plan_ref
        if self._logs_ref:
            memo_dict["logs_artifact_ref"] = self._logs_ref
        if self._summary_ref:
            memo_dict["summary_artifact_ref"] = self._summary_ref
        if self._pull_request_url:
            memo_dict["pull_request_url"] = self._pull_request_url
        merge_automation_summary = self._merge_automation_summary_from_context()
        if merge_automation_summary:
            memo_dict["merge_automation"] = merge_automation_summary
        memo_dict.update(self._dependency_metadata())

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
            self._waiting_reason = "provider_profile_slot"
            self._attention_required = False
            self._set_state(STATE_AWAITING_SLOT, summary=reason)
        elif new_state == "launching":
            self._waiting_reason = None
            self._attention_required = False
            self._set_state(STATE_EXECUTING, summary="Launching agent...")
        elif new_state == "running":
            self._waiting_reason = None
            self._attention_required = False
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
        """Record that a child AgentRun has acquired a provider-profile slot.

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

    @workflow.signal(name="DependencyResolved")
    def dependency_resolved(self, payload: dict[str, Any]) -> None:
        self._record_dependency_signal(payload)

    @workflow.signal(name="BypassDependencies")
    def bypass_dependencies(self, payload: dict[str, Any] | None = None) -> None:
        self._bypass_dependencies(payload)

    def _release_slot_defensive(self) -> None:
        """Release the provider-profile slot defensively when a child exits.

        This is called when a child AgentRun exits in a terminal state but
        may have failed to release its slot due to cancellation or other issues.
        """
        if not self._assigned_profile_id:
            return

        profile_id = self._assigned_profile_id
        child_wf_id = self._assigned_child_workflow_id or "unknown"

        self._get_logger().warning(
            "Defensively releasing provider-profile slot %s for child %s",
            profile_id,
            child_wf_id,
        )

        # Use the runtime_id passed from the child via profile_assigned signal.
        # Fall back to inference from child_workflow_id if not available.
        runtime_id = self._assigned_runtime_id or self._infer_runtime_from_child(
            child_wf_id
        )
        if runtime_id:
            manager_id = self._manager_workflow_id(runtime_id)
            try:
                manager_handle = workflow.get_external_workflow_handle(manager_id)
                # Schedule the async signal without awaiting - best effort cleanup.
                # The manager's verify_lease_holders will reclaim the slot if this fails.
                asyncio.create_task(
                    self._signal_release_slot(manager_handle, child_wf_id, profile_id)
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
        """Send release_slot signal to the ProviderProfileManager."""
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

    @workflow.update(name="SkipDependencyWait")
    def skip_dependency_wait(self) -> None:
        self._update_dependency_wait_duration()
        self._dependency_manual_override_unresolved_count = len(
            self._unresolved_dependency_ids
        )
        self._unresolved_dependency_ids.clear()
        self._paused = False
        self._dependency_failure = None
        self._failed_dependency_id = None
        self._dependency_resolution = DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE
        self._summary = "Dependency wait skipped by operator."
        self._update_search_attributes()
        self._update_memo()

    @skip_dependency_wait.validator
    def validate_skip_dependency_wait(self) -> None:
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot skip dependency wait for a completed workflow.")
        if self._state != STATE_WAITING_ON_DEPENDENCIES:
            raise ValueError("Workflow is not waiting on dependencies.")
        if self._dependency_failure is not None:
            raise ValueError("Cannot skip dependency wait after dependency failure.")
        if not self._unresolved_dependency_ids:
            raise ValueError("Workflow has no unresolved dependencies.")

    @workflow.update(name="SendMessage")
    async def send_message(self, payload: dict[str, Any] | None = None) -> None:
        message = self._extract_operator_message(payload)
        if message is not None:
            await self._forward_operator_message_to_active_child({"message": message})
        self._update_search_attributes()

    @send_message.validator
    def validate_send_message(self, payload: dict[str, Any] | None = None) -> None:
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot send a message to a completed workflow.")
        if self._extract_operator_message(payload) is None:
            raise ValueError("message is required when sending an operator message.")

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
        self._set_state(
            STATE_SCHEDULED, summary=f"Execution rescheduled for {self._scheduled_for}."
        )
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
                self._external_status = (
                    "completed"
                    if normalized_status == "awaiting_feedback"
                    else normalized_status
                )
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
    def _extract_operator_message(payload: Mapping[str, Any] | None) -> str | None:
        if not isinstance(payload, Mapping):
            return None
        candidate = payload.get("message")
        if candidate is None:
            return None
        normalized = str(candidate).strip()
        return normalized or None

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
        if str(self._active_agent_id or "").strip().lower() not in {
            "jules",
            "jules_api",
        }:
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

    @workflow.query
    def get_progress(self) -> dict[str, Any]:
        payload = ExecutionProgressModel.model_validate(self._progress_snapshot).model_dump(
            by_alias=True,
            mode="json",
        )
        payload["runId"] = workflow.info().run_id
        return payload

    @workflow.query
    def get_step_ledger(self) -> dict[str, Any]:
        snapshot = build_step_ledger_snapshot(
            workflow_id=workflow.info().workflow_id,
            run_id=workflow.info().run_id,
            rows=self._step_ledger_rows,
        )
        return StepLedgerSnapshotModel.model_validate(snapshot).model_dump(
            by_alias=True,
            mode="json",
        )
