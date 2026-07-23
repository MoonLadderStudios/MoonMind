import asyncio
import logging
import os
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from temporalio import workflow, activity
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.workflow import ActivityCancellationType
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import ValidationError

    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        AgentRunHandle,
        AgentRunResult,
        AgentRunStatus as AgentRunStatusModel,
        ProfileSelector,
        _MAX_SUMMARY_CHARS,
    )
    from moonmind.schemas.managed_session_models import (
        CodexManagedSessionBinding,
        CodexManagedSessionWorkflowInput,
        SendCodexManagedSessionTurnRequest,
        build_codex_managed_session_turn_environment,
        canonical_managed_session_runtime_id,
    )
    from moonmind.schemas.temporal_activity_models import (
        AgentRuntimeCancelInput,
        AgentRuntimeFetchResultInput,
        AgentRuntimeStatusInput,
        ExternalAgentRunInput,
    )
    from moonmind.schemas.temporal_payload_policy import compact_temporal_ref_metadata
    from moonmind.workflows.adapters.agent_adapter import AgentAdapter
    from moonmind.workflows.adapters.managed_agent_adapter import (
        ManagedAgentAdapter,
        ProfileResolutionError,
    )
    from moonmind.workflows.adapters.codex_session_adapter import (
        CodexSessionRunFailedError,
        CodexSessionAdapter,
    )
    from moonmind.workflows.adapters.external_adapter_registry import (
        build_default_registry,
    )
    from moonmind.workflows.adapters.base_external_agent_adapter import (
        BaseExternalAgentAdapter,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityRoute,
        WORKFLOW_TASK_QUEUE,
        build_default_activity_catalog,
    )
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        workflow_id_for_runtime,
    )
    from moonmind.workflows.temporal.typed_execution import execute_typed_activity
    from moonmind.workflows.provider_failures import (
        build_provider_failure_event,
        classify_provider_failure,
        provider_error_requires_cooldown,
        provider_failure_event_from_metadata,
        provider_failure_event_requires_cooldown,
        resolve_provider_cooldown_seconds,
    )
    from moonmind.workflows.executions.runtime_capabilities import (
        resolve_runtime_execution_capabilities,
    )

TERMINAL_CONTRACT_CONTINUATION_PATCH_ID = "agent-run-terminal-contract-continuation-v1"
PR_RESOLVER_OWNED_CONTINUATION_PATCH_ID = "agent-run-pr-resolver-owned-continuation-v1"
PR_RESOLVER_CONTINUATION_OBSERVABILITY_PATCH_ID = (
    "agent-run-pr-resolver-continuation-observability-v1"
)
_MAX_TERMINAL_CONTRACT_CONTINUATIONS = 2


def _terminal_contract_continuation_instruction(missing: list[str]) -> str:
    evidence = ", ".join(f"`{path}`" for path in missing)
    return (
        "The selected skill has not satisfied its terminal contract. "
        f"Missing required evidence: {evidence or 'valid terminal evidence'}. "
        "Resume the still-running work from durable state and do not declare "
        "completion until the terminal result contract is satisfied."
    )

# Map canonical AgentRunState literals to workflow-usable status constants.
# Named RunStatus (not AgentRunStatus) to avoid shadowing the Pydantic model
# imported in activity code.
class RunStatus:
    queued = "queued"
    awaiting_slot = "awaiting_slot"
    launching = "launching"
    running = "running"
    awaiting_callback = "awaiting_callback"
    awaiting_feedback = "awaiting_feedback"
    completed = "completed"
    failed = "failed"
    cancelled = "canceled"
    timed_out = "timed_out"

_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.completed, RunStatus.failed, RunStatus.cancelled, RunStatus.timed_out, "intervention_requested"}
)

_EXTERNAL_STATUS_TO_RUN_STATUS: dict[str, str] = {
    "queued": RunStatus.queued,
    "launching": RunStatus.launching,
    "running": RunStatus.running,
    "in_progress": RunStatus.running,
    "in-progress": RunStatus.running,
    "processing": RunStatus.running,
    "awaiting_callback": RunStatus.awaiting_callback,
    "awaiting_feedback": RunStatus.awaiting_feedback,
    "awaiting_approval": "awaiting_approval",
    "intervention_requested": "intervention_requested",
    "collecting_results": "collecting_results",
    "completed": RunStatus.completed,
    "success": RunStatus.completed,
    "done": RunStatus.completed,
    "resolved": RunStatus.completed,
    "finished": RunStatus.completed,
    "failed": RunStatus.failed,
    "error": RunStatus.failed,
    "errored": RunStatus.failed,
    "cancelled": RunStatus.cancelled,
    "canceled": RunStatus.cancelled,
    "timed_out": RunStatus.timed_out,
    "timeout": RunStatus.timed_out,
    "timed-out": RunStatus.timed_out,
    "unknown": RunStatus.awaiting_callback,
}

def _setdefault_compact_ref_metadata(
    metadata: dict[str, Any],
    field_name: str,
    value: Any,
) -> None:
    for key, compact_value in compact_temporal_ref_metadata(field_name, value).items():
        metadata.setdefault(key, compact_value)


def _compact_workflow_text(value: Any, *, max_chars: int = 700) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _compact_workflow_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_workflow_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def _compact_workflow_text_list(
    value: Any,
    *,
    max_items: int = 20,
    max_chars: int = 400,
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    compact: list[str] = []
    for item in value:
        text = _compact_workflow_text(item, max_chars=max_chars)
        if text:
            compact.append(text)
        if len(compact) >= max_items:
            break
    return compact


def _compact_workflow_text_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    compact: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _compact_workflow_text(str(raw_key), max_chars=120)
        text = _compact_workflow_text(raw_value, max_chars=400)
        if key and text:
            compact[key] = text
        if len(compact) >= 20:
            break
    return compact


def _compact_moonspec_verify_for_workflow_history(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    scalar_keys = (
        "schemaVersion",
        "verdict",
        "gateVerdict",
        "gate_verdict",
        "moonSpecVerdict",
        "moonspecVerdict",
        "verificationVerdict",
        "verification_verdict",
        "confidence",
        "recommendedNextAction",
        "recommended_next_action",
        "targetLogicalStepId",
        "target_logical_step_id",
        "workspacePolicyRecommendation",
        "workspace_policy_recommendation",
        "recoverableInCurrentRuntime",
        "recoverable_in_current_runtime",
        "invalid",
        "degraded",
        "remainingWorkRef",
        "remaining_work_ref",
        "diagnosticsRef",
        "diagnostics_ref",
        "verificationReportRef",
        "verification_report_ref",
        "reportRef",
        "report_ref",
        "gateResultRef",
        "gate_result_ref",
        "artifactRef",
        "artifact_ref",
    )
    for key in scalar_keys:
        field_value = _compact_workflow_scalar(value.get(key))
        if field_value is not None:
            compact[key] = field_value

    for key in ("feedback", "summary", "message", "downgradeReason"):
        text = _compact_workflow_text(value.get(key), max_chars=900)
        if text:
            compact[key] = text

    for key in ("invalidatedRefs", "invalidated_refs"):
        refs = _compact_workflow_text_list(value.get(key))
        if refs:
            compact[key] = refs
            break
    for key in ("blockingEvidenceRefs", "blocking_evidence_refs"):
        refs = _compact_workflow_text_list(value.get(key))
        if refs:
            compact[key] = refs
            break

    validated_refs = _compact_workflow_text_mapping(
        value.get("validatedRefs") or value.get("validated_refs")
    )
    if validated_refs:
        compact["validatedRefs"] = validated_refs

    contract_violations = _compact_workflow_text_list(
        value.get("contractViolations") or value.get("contract_violations"),
        max_items=10,
        max_chars=700,
    )
    if contract_violations:
        compact["contractViolations"] = contract_violations

    return compact


def _compact_agent_run_result_payload_for_workflow_history(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    compact_payload = dict(payload)
    metadata = compact_payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return compact_payload

    compact_metadata = dict(metadata)
    for key in (
        "moonSpecVerify",
        "moonspecVerify",
        "moonspec_verify",
        "verificationResult",
        "verification_result",
    ):
        value = compact_metadata.get(key)
        if isinstance(value, Mapping):
            compact_metadata[key] = _compact_moonspec_verify_for_workflow_history(
                value
            )
    compact_payload["metadata"] = compact_metadata
    return compact_payload


# Default workflow-level execution timeouts
DEFAULT_MANAGED_TIMEOUT_SECONDS = 3600      # 1 hour
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 21600    # 6 hours
_CALLBACK_KEY_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")

STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT = timedelta(seconds=120)
OMNIGENT_PROFILE_BOUND_EXECUTION_PATCH_ID = (
    "agent-run-omnigent-profile-bound-execution-v1"
)
MANAGED_STATUS_ACTIVITY_PATCH_ID = "agent-run-managed-status-activity-v1"
PROVIDER_PROFILE_MANAGER_ID_PATCH = "provider-profile-manager-id-v1"
MANAGED_TASK_WORKFLOW_BINDING_PATCH_ID = "agent-run-managed-task-workflow-binding-v1"
MANAGED_SESSION_FETCH_RESULT_ACTIVITY_PATCH_ID = (
    "agent-run-managed-session-fetch-result-activity-v1"
)
TERMINAL_CHECKPOINT_PUBLICATION_PATCH_ID = (
    "agent-run-terminal-checkpoint-publication-v1"
)
STORY_BREAKDOWN_ARTIFACT_HANDOFF_PATCH_ID = (
    "agent-run-story-breakdown-artifact-handoff-v1"
)
MANAGED_SESSION_PREPARE_TURN_INSTRUCTIONS_ACTIVITY_PATCH_ID = (
    "agent-run-managed-session-prepare-turn-instructions-activity-v1"
)
MANAGED_SESSION_DEFER_TURN_INSTRUCTIONS_UNTIL_LAUNCH_PATCH_ID = (
    "agent-run-managed-session-defer-turn-instructions-until-launch-v1"
)
PR_RESOLVER_PAYLOAD_SKILL_DETECTION_PATCH_ID = (
    "agent-run-pr-resolver-payload-skill-detection-v1"
)
PR_RESOLVER_MERGE_GATE_OWNERSHIP_PATCH_ID = (
    "agent-run-pr-resolver-merge-gate-ownership-v1"
)
MANAGER_SLOT_WAIT_INSPECTION_PATCH_ID = "agent-run-slot-wait-manager-inspection-v1"
NON_DESTRUCTIVE_SLOT_WAIT_RECOVERY_PATCH_ID = (
    "agent-run-non-destructive-slot-wait-recovery-v1"
)
ACCURATE_SLOT_WAIT_REASON_PATCH_ID = "agent-run-accurate-slot-wait-reason-v1"
SLOT_HANDOFF_PATCH_ID = "agent-run-slot-handoff-v1"
SYNC_PROFILES_BEFORE_SLOT_REQUEST_PATCH_ID = (
    "agent-run-sync-profiles-before-slot-request-v1"
)
PIN_PROVIDER_PROFILE_BEFORE_SLOT_REQUEST_PATCH_ID = (
    "agent-run-pin-provider-profile-before-slot-request-v1"
)
STICKY_PINNED_PROFILE_COOLDOWN_RETRY_PATCH_ID = (
    "agent-run-sticky-pinned-profile-cooldown-retry-v1"
)
RUNTIME_SELECTION_PROFILE_CLEAR_PATCH_ID = (
    "agent-run-runtime-selection-profile-clear-v1"
)
RUNTIME_SELECTION_SESSION_REBIND_PATCH_ID = (
    "agent-run-runtime-selection-session-rebind-v1"
)
AWAITING_SLOT_RUNTIME_PROFILE_EDIT_PATCH_ID = (
    "agent-run-awaiting-slot-runtime-profile-edit-v1"
)
AGENT_RUN_RESILIENCY_POLICY_PATCH_ID = "agent-run-resiliency-policy-v1"
AGENT_RUN_CLAUDE_NO_PROGRESS_POLICY_PATCH_ID = (
    "agent-run-claude-code-no-progress-policy-v2"
)
# Prefer the canonical structured provider failure event (retry_after_seconds /
# reset_at / quota_scope / provider_error_class) when deciding profile cooldowns,
# falling back to text-marker classification only when the structured fields are
# absent. Gated so in-flight runs keep their recorded marker-based decision.
AGENT_RUN_STRUCTURED_PROVIDER_FAILURE_PATCH_ID = (
    "agent-run-structured-provider-failure-cooldown-v1"
)
AGENT_RUN_PROVIDER_COOLDOWN_EXPONENTIAL_BACKOFF_PATCH_ID = (
    "agent-run-provider-cooldown-exponential-backoff-v1"
)
AGENT_RUN_MANAGED_NO_PROGRESS_RECONCILIATION_PATCH_ID = (
    "agent-run-managed-no-progress-reconciliation-v1"
)
AGENT_RUN_MANAGED_NO_PROGRESS_CANCEL_FETCH_PATCH_ID = (
    "agent-run-managed-no-progress-cancel-fetch-v1"
)
MANAGED_SESSION_START_AFTER_SLOT_PATCH_ID = (
    "agent-run-managed-session-start-after-slot-v1"
)
AGENT_RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH = (
    "agent-run-workflow-child-task-queue-v2"
)
# Enforce the agent-run execution budget at the workflow boundary for the
# managed-session (codex) turn. The codex turn runs as a single long-blocking
# ``agent_runtime.send_turn`` activity, so while the workflow is parked on that
# await it cannot run its own elapsed/no-progress checks; it relies solely on
# the activity's server-side ScheduleToClose timeout. When that timer is not
# enforced promptly a stuck turn can run far past its budget before failing.
# This patch races the turn against a deterministic workflow timer so a stuck
# or parked turn is aborted at its budget and reported with a real summary.
MANAGED_SESSION_TURN_DEADLINE_PATCH_ID = (
    "agent-run-managed-session-turn-deadline-v1"
)
MANAGED_SESSION_PR_PUBLISH_BASE_BRANCH_PATCH_ID = (
    "agent-run-managed-session-pr-publish-base-branch-v1"
)
MANAGED_SESSION_BRIDGE_EVENTS_ACTIVITY_PATCH_ID = (
    "agent-run-managed-session-bridge-events-activity-v1"
)

# Module-level activity catalog — deterministic, safe for Temporal replay.
# Mirrors the pattern used by MoonMind.UserWorkflow (run.py:50).
DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()

# How long to wait for a slot_assigned signal before inspecting manager health
# and performing bounded, non-destructive recovery.
_SLOT_WAIT_TIMEOUT_SECONDS = 120
_SLOT_WAIT_MAX_RECOVERY_ATTEMPTS = 3
_DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS = 1800
_CLAUDE_CODE_NO_PROGRESS_TIMEOUT_SECONDS = 2400
_CLAUDE_CODE_NO_PROGRESS_GRACE_SECONDS = 900
_DEFAULT_MANAGED_429_RETRY_DELAY_SECONDS = 900
_MAX_PROVIDER_COOLDOWN_BACKOFF_SECONDS = 3600
_MANAGED_RUNTIME_STORE_ROOT = os.environ.get(
    "MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"
)
_MANAGED_RUN_STORE_ROOT = os.path.join(_MANAGED_RUNTIME_STORE_ROOT, "managed_runs")
_DEFAULT_SESSION_IMAGE_REF = os.environ.get(
    "WORKFLOW_JOB_IMAGE",
    "ghcr.io/moonladderstudios/moonmind:latest",
)
_SLOT_HANDOFF_TTL_SECONDS = 10
# Floor for the workflow-side managed-session turn deadline so a nearly-exhausted
# budget still gives the turn a usable, non-zero window before being aborted.
_MIN_MANAGED_TURN_DEADLINE_SECONDS = 60

def _request_selected_skill(
    request: AgentExecutionRequest,
    *,
    include_payload_contract: bool = True,
) -> str | None:
    """Return the selected agent skill recorded in request metadata, if present."""

    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    metadata = parameters.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    moonmind = metadata_map.get("moonmind")
    moonmind_map = moonmind if isinstance(moonmind, Mapping) else {}
    selected_skill = str(
        moonmind_map.get("selectedSkill")
        or metadata_map.get("selectedSkill")
        or ""
    ).strip()
    if selected_skill:
        return selected_skill
    if not include_payload_contract:
        return None

    direct_skill = str(
        parameters.get("targetSkill")
        or parameters.get("target_skill")
        or parameters.get("skillId")
        or parameters.get("skill")
        or ""
    ).strip()
    if direct_skill:
        return direct_skill

    task_payload = parameters.get("task")
    task = task_payload if isinstance(task_payload, Mapping) else {}
    skill_payload = task.get("skill")
    skill = skill_payload if isinstance(skill_payload, Mapping) else {}
    skill_id = str(skill.get("id") or skill.get("name") or "").strip()
    if skill_id:
        return skill_id

    tool_payload = task.get("tool")
    tool = tool_payload if isinstance(tool_payload, Mapping) else {}
    tool_type = str(tool.get("type") or tool.get("kind") or "").strip()
    if tool_type and tool_type != "skill":
        return None
    tool_name = str(tool.get("name") or tool.get("id") or "").strip()
    return tool_name or None

def _request_pr_resolver_merge_gate_owned(
    request: AgentExecutionRequest,
) -> bool:
    """Return whether a pr-resolver request carries a merge-automation gate owner."""

    parameters = request.parameters if isinstance(request.parameters, Mapping) else {}
    merge_gate = parameters.get("mergeGate")
    if not isinstance(merge_gate, Mapping):
        merge_gate = parameters.get("merge_gate")
    if not isinstance(merge_gate, Mapping):
        return False
    parent_workflow_id = str(
        merge_gate.get("parentWorkflowId")
        or merge_gate.get("parent_workflow_id")
        or ""
    ).strip()
    return bool(parent_workflow_id)

def _request_step_ledger_context(
    request: AgentExecutionRequest,
) -> dict[str, Any] | None:
    """Return compact step-ledger context carried from the parent workflow."""

    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    metadata = parameters.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    moonmind = metadata_map.get("moonmind")
    moonmind_map = moonmind if isinstance(moonmind, Mapping) else {}
    raw_context = moonmind_map.get("stepLedger")
    if not isinstance(raw_context, Mapping):
        return None

    logical_step_id = str(raw_context.get("logicalStepId") or "").strip()
    if not logical_step_id:
        return None

    context: dict[str, Any] = {"logicalStepId": logical_step_id}
    attempt = raw_context.get("attempt")
    if isinstance(attempt, (int, float)) and not isinstance(attempt, bool):
        context["attempt"] = int(attempt)
    scope = str(raw_context.get("scope") or "").strip()
    if scope:
        context["scope"] = scope
    return context

def _request_reserves_slot_for_immediate_followup(
    request: AgentExecutionRequest,
) -> bool:
    """Return whether a successful run should reserve its slot for next step."""

    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    metadata = parameters.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    moonmind = metadata_map.get("moonmind")
    moonmind_map = moonmind if isinstance(moonmind, Mapping) else {}
    continuity = moonmind_map.get("slotContinuity")
    continuity_map = continuity if isinstance(continuity, Mapping) else {}
    return bool(continuity_map.get("reserveForImmediateFollowup"))

def _normalize_agent_runtime_id(agent_id: str) -> str:
    """Normalize runtime identifiers for managed runtime routing."""

    return str(agent_id).strip().lower().replace("-", "_")

def _legacy_manager_workflow_id(runtime_id: str) -> str:
    # Preserve legacy workflow IDs for in-flight histories. New executions use
    # provider-profile-manager IDs once the replay patch is active.
    return f"auth-profile-manager:{runtime_id}"

@activity.defn(name="integration.resolve_adapter_metadata")
async def resolve_adapter_metadata(agent_id: str) -> dict:
    """Validate adapter and return execution metadata in one hop.

    All non-deterministic work (reading env vars, dynamic imports) runs
    here rather than in the workflow so that replays remain deterministic.

    Returns ``{"agent_id": ..., "execution_style": ...}`` on success;
    raises if no adapter is registered for *agent_id*.
    """
    registry = build_default_registry()
    resolved_agent_id = str(agent_id).strip().lower()
    adapter = registry.create(resolved_agent_id)
    execution_style = "polling"
    supports_callbacks = False
    if isinstance(adapter, BaseExternalAgentAdapter):
        capability = adapter.provider_capability
        execution_style = capability.execution_style
        supports_callbacks = capability.supports_callbacks

    from moonmind.config.settings import settings

    return {
        "agent_id": resolved_agent_id,
        "execution_style": execution_style,
        "supports_callbacks": supports_callbacks,
        "callback_base_url": settings.integration_callbacks.base_url,
    }

# --- In-flight compatibility shims (spec #285) ---
# These activities were superseded by resolve_adapter_metadata but must remain
# registered so that in-flight workflow histories do not fail with NotFoundError.

@activity.defn(name="integration.get_activity_route")
async def get_activity_route(activity_name: str) -> dict:
    import dataclasses
    from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
    catalog = build_default_activity_catalog()
    route = catalog.resolve_activity(activity_name)
    return dataclasses.asdict(route)

@activity.defn(name="integration.resolve_external_adapter")
async def resolve_external_adapter(agent_id: str) -> str:
    registry = build_default_registry()
    registry.create(agent_id)
    return agent_id

@activity.defn(name="integration.external_adapter_execution_style")
async def external_adapter_execution_style(agent_id: str) -> str:
    registry = build_default_registry()
    adapter = registry.create(agent_id)
    if isinstance(adapter, BaseExternalAgentAdapter):
        return adapter.provider_capability.execution_style
    return "polling"

@workflow.defn(name="MoonMind.AgentRun")
class MoonMindAgentRun:
    @staticmethod
    def _workflow_child_task_queue() -> str:
        if workflow.patched(AGENT_RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH):
            return settings.temporal.user_workflow_v2_task_queue
        return WORKFLOW_TASK_QUEUE

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

        logger_to_use = workflow.logger
        if not hasattr(logger_to_use, "isEnabledFor"):
            logger_to_use = logging.getLogger(__name__)

        try:
            logger_to_use.isEnabledFor(logging.INFO)
            return logging.LoggerAdapter(logger_to_use, extra=extra)
        except Exception:
            logging.getLogger(__name__).exception("Error checking logger capabilities in _get_logger")
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    def __init__(self):
        self.completion_event = asyncio.Event()
        self.slot_assigned_event = asyncio.Event()
        self.run_status = RunStatus.queued
        self.final_result: AgentRunResult | None = None
        self.run_id: str | None = None
        self.agent_kind: str | None = None
        self._assigned_profile_id: str | None = None
        self._external_agent_id: str | None = None
        # Auto-answer state (Jules question auto-answer, spec 094)
        self._answered_activity_ids: set[str] = set()
        self._auto_answer_count: int = 0
        self._pending_operator_messages: list[str] = []
        self._profile_snapshots: dict[str, dict[str, Any]] = {}
        self._awaiting_slot_reason_override: str | None = None
        self._slot_wait_timeout_override_seconds: int | None = None
        self._skip_default_profile_pin_once: bool = False
        self._provider_cooldown_retry_counts: dict[str, int] = {}
        self._terminal_result_payload_compacted_for_history: bool = False
        self.runtime_selection_updated_event = asyncio.Event()
        self._pending_runtime_selection_update: dict[str, Any] | None = None
        self._managed_session_detached_for_runtime_selection: bool = False
        self._paused: bool = False

    @staticmethod
    def _safe_callback_key(*parts: str) -> str:
        raw = "-".join(
            str(part or "").strip() for part in parts if str(part or "").strip()
        )
        safe = _CALLBACK_KEY_SAFE_PATTERN.sub("-", raw).strip("-")
        return safe or "callback"

    @staticmethod
    def _external_callback_url(
        *,
        base_url: str,
        integration_name: str,
        callback_correlation_key: str,
    ) -> str:
        base = str(base_url or "").strip().rstrip("/")
        integration = str(integration_name or "").strip().lower()
        key = str(callback_correlation_key or "").strip()
        return f"{base}/api/integrations/{integration}/callbacks/{key}"

    def _with_external_callback_ingress(
        self,
        request: AgentExecutionRequest,
        *,
        integration_name: str,
        supports_callbacks: bool,
        callback_base_url: str | None,
    ) -> AgentExecutionRequest:
        if not supports_callbacks:
            return request

        policy = (
            request.callback_policy if isinstance(request.callback_policy, dict) else {}
        )
        if policy.get("enabled") is False:
            return request

        explicit_url = (
            str(policy.get("callbackUrl") or "").strip()
            or str(policy.get("url") or "").strip()
            or str(request.callback_url or "").strip()
        )
        explicit_key = (
            str(policy.get("callbackCorrelationKey") or "").strip()
            or str(request.callback_correlation_key or "").strip()
        )
        key = explicit_key or self._safe_callback_key(
            workflow.info().workflow_id,
            integration_name,
            request.correlation_id,
            request.idempotency_key,
        )

        if explicit_url:
            callback_url = explicit_url
        else:
            base_url = str(
                policy.get("callbackBaseUrl")
                or policy.get("baseUrl")
                or callback_base_url
                or ""
            ).strip()
            if not base_url:
                return request
            callback_url = self._external_callback_url(
                base_url=base_url,
                integration_name=integration_name,
                callback_correlation_key=key,
            )

        next_policy = dict(policy)
        next_policy.setdefault("enabled", True)
        next_policy["callbackUrl"] = callback_url
        next_policy["callbackCorrelationKey"] = key
        return request.model_copy(
            update={
                "callback_policy": next_policy,
                "callback_url": callback_url,
                "callback_correlation_key": key,
            }
        )

    # --- Deterministic catalog-based routing (new path) ---

    @staticmethod
    def _retry_policy_for_route(route: TemporalActivityRoute) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        )

    @staticmethod
    def _execute_kwargs_for_route(route: TemporalActivityRoute) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": MoonMindAgentRun._retry_policy_for_route(route),
        }
        if route.timeouts.heartbeat_timeout_seconds is not None:
            kwargs["heartbeat_timeout"] = timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            )
        return kwargs

    async def _execute_routed_activity(
        self,
        activity_name: str,
        args: object,
        **overrides: Any,
    ) -> object:
        """Execute an activity using the module-level catalog for routing."""
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(activity_name)
        kwargs = self._execute_kwargs_for_route(route)
        kwargs.update(overrides)
        kwargs.setdefault(
            "summary",
            {
                "agent_runtime.launch_session": "Launch managed runtime session",
                "agent_runtime.session_status": "Fetch managed runtime session status",
            }.get(activity_name, activity_name),
        )
        return await execute_typed_activity(
            activity_name,
            args,
            **kwargs,
        )

    async def _signal_parent_child_state_changed(
        self,
        parent_info: Any,
        new_state: str,
        reason: str,
    ) -> None:
        if not parent_info:
            return
        parent_handle = workflow.get_external_workflow_handle(
            parent_info.workflow_id,
            run_id=parent_info.run_id,
        )
        try:
            await parent_handle.signal(
                "child_state_changed",
                args=[new_state, reason],
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to signal parent workflow %s: %s",
                parent_info.workflow_id,
                exc,
            )

    @staticmethod
    def _managed_runtime_id(agent_id: str) -> str:
        runtime_mapping = {
            "claude": "claude_code",
            "claude_code": "claude_code",
            "codex": "codex_cli",
            "codex_cli": "codex_cli",
        }
        normalized_agent_id = _normalize_agent_runtime_id(agent_id)
        return runtime_mapping.get(normalized_agent_id, normalized_agent_id)

    def _cooldown_seconds_for_profile(self, profile_id: str | None) -> int:
        normalized_profile_id = str(profile_id or "").strip()
        if normalized_profile_id:
            profile = self._profile_snapshots.get(normalized_profile_id) or {}
            raw_seconds = profile.get("cooldown_after_429_seconds")
            try:
                seconds = int(raw_seconds)
            except (TypeError, ValueError):
                seconds = 0
            if seconds >= 0:
                return seconds
        return _DEFAULT_MANAGED_429_RETRY_DELAY_SECONDS

    @staticmethod
    def _provider_failure_supplies_retry_timing(provider_failure_event: Any) -> bool:
        if provider_failure_event is None:
            return False
        retry_after_seconds = getattr(
            provider_failure_event, "retry_after_seconds", None
        )
        if retry_after_seconds is not None and retry_after_seconds > 0:
            return True
        reset_at = getattr(provider_failure_event, "reset_at", None)
        if reset_at is None:
            return False
        reset_raw = str(reset_at).strip()
        if not reset_raw:
            return False
        candidate = reset_raw[:-1] + "+00:00" if reset_raw.endswith("Z") else reset_raw
        try:
            reset_dt = datetime.fromisoformat(candidate)
        except ValueError:
            return False
        if reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)
        try:
            now = workflow.now()
        except Exception:
            now = datetime.now(timezone.utc)
        now_aware = (
            now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        )
        return (reset_dt - now_aware).total_seconds() > 0

    def _next_provider_cooldown_seconds(
        self,
        *,
        runtime_id: str,
        profile_id: str | None,
        base_seconds: int,
    ) -> int:
        seconds = max(0, int(base_seconds))
        if seconds <= 0:
            return seconds
        key = str(profile_id or runtime_id or "managed").strip() or "managed"
        prior_failures = self._provider_cooldown_retry_counts.get(key, 0)
        self._provider_cooldown_retry_counts[key] = prior_failures + 1
        multiplier = 2 ** min(prior_failures, 10)
        cap = max(seconds, _MAX_PROVIDER_COOLDOWN_BACKOFF_SECONDS)
        return min(seconds * multiplier, cap)

    @staticmethod
    def _profile_selector_has_constraints(selector: Any) -> bool:
        if selector is None:
            return False
        selector_payload = (
            selector.model_dump(by_alias=True, exclude_none=True)
            if hasattr(selector, "model_dump")
            else selector
        )
        if not isinstance(selector_payload, Mapping):
            return False
        for value in selector_payload.values():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and not value:
                continue
            return True
        return False

    def _default_execution_profile_ref(self, request: AgentExecutionRequest) -> str | None:
        if request.execution_profile_ref:
            return str(request.execution_profile_ref).strip() or None
        if self._profile_selector_has_constraints(request.profile_selector):
            return None

        default_profile_ids: list[str] = []
        enabled_profile_ids: list[str] = []
        for profile_id, profile in self._profile_snapshots.items():
            normalized_profile_id = str(profile_id or "").strip()
            if not normalized_profile_id:
                continue
            if profile.get("enabled") is False:
                continue
            enabled_profile_ids.append(normalized_profile_id)
            if profile.get("is_default") is True:
                default_profile_ids.append(normalized_profile_id)

        if len(default_profile_ids) == 1:
            return default_profile_ids[0]
        if not default_profile_ids and len(enabled_profile_ids) == 1:
            return enabled_profile_ids[0]
        return None

    @staticmethod
    def _format_retry_timestamp(value: datetime) -> str:
        rendered = value.isoformat()
        if rendered.endswith("+00:00"):
            return rendered[:-6] + "Z"
        return rendered

    def _build_managed_rate_limit_waiting_reason(
        self,
        *,
        runtime_id: str,
        profile_id: str | None,
        cooldown_seconds: int,
    ) -> str:
        retry_at = workflow.now() + timedelta(seconds=max(cooldown_seconds, 0))
        profile_fragment = f" on profile {profile_id}" if profile_id else ""
        return (
            "Managed provider capacity exhausted for "
            f"{runtime_id}{profile_fragment}; retry scheduled for "
            f"{self._format_retry_timestamp(retry_at)} after {cooldown_seconds}s cooldown."
        )

    @staticmethod
    def _provider_slot_intent_summary(
        request: AgentExecutionRequest,
    ) -> str:
        parts: list[str] = []
        exact_profile = str(request.execution_profile_ref or "").strip()
        if exact_profile:
            parts.append(f"exact_profile={exact_profile}")
        selector_source = request.profile_selector
        selector = (
            selector_source.model_dump(by_alias=True, exclude_none=True)
            if hasattr(selector_source, "model_dump")
            else selector_source
        )
        if not isinstance(selector, Mapping):
            selector = {}
        provider_id = str(selector.get("providerId") or "").strip()
        if provider_id:
            parts.append(f"provider={provider_id}")
        materialization = str(
            selector.get("runtimeMaterializationMode") or ""
        ).strip()
        if materialization:
            parts.append(f"materialization={materialization}")
        tags_any = selector.get("tagsAny") or []
        tags_all = selector.get("tagsAll") or []
        if tags_any:
            parts.append(f"tags_any={','.join(str(tag) for tag in tags_any)}")
        if tags_all:
            parts.append(f"tags_all={','.join(str(tag) for tag in tags_all)}")
        return ", ".join(parts) if parts else "selector=auto"

    def _build_provider_slot_waiting_reason(
        self,
        *,
        runtime_id: str,
        request: AgentExecutionRequest,
    ) -> str:
        intent = self._provider_slot_intent_summary(request)
        return (
            "Waiting for provider profile slot; "
            f"runtime={runtime_id}; {intent}; missing_condition=capacity_or_cooldown."
        )

    def _build_manager_slot_waiting_reason(
        self,
        *,
        runtime_id: str,
        request: AgentExecutionRequest,
        manager_state: Mapping[str, Any],
    ) -> str:
        """Describe the manager-owned condition that is blocking slot assignment."""

        intent = self._provider_slot_intent_summary(request)
        queue_position = manager_state.get("requester_queue_position")
        queue_suffix = (
            f"; queue_position={queue_position}"
            if isinstance(queue_position, int) and queue_position > 0
            else ""
        )
        profile = manager_state.get("requested_profile")
        if not isinstance(profile, Mapping):
            return (
                "Waiting in MoonMind provider profile queue; "
                f"runtime={runtime_id}; {intent}; missing_condition=manager_queue"
                f"{queue_suffix}."
            )

        profile_id = str(profile.get("profile_id") or "").strip()
        profile_suffix = f"; profile={profile_id}" if profile_id else ""
        slots_in_use = profile.get("current_leases_count")
        max_parallel_runs = profile.get("max_parallel_runs")
        if profile.get("enabled") is False or profile.get("launch_ready") is False:
            return (
                "Waiting for provider profile readiness; "
                f"runtime={runtime_id}; {intent}{profile_suffix}; "
                f"missing_condition=profile_not_launch_ready{queue_suffix}."
            )

        cooldown_until = str(profile.get("cooldown_until") or "").strip()
        if cooldown_until:
            return (
                "Waiting for provider cooldown to expire; "
                f"runtime={runtime_id}; {intent}{profile_suffix}; "
                f"missing_condition=provider_cooldown; cooldown_until={cooldown_until}"
                f"{queue_suffix}."
            )

        if (
            isinstance(slots_in_use, int)
            and isinstance(max_parallel_runs, int)
            and slots_in_use >= max_parallel_runs
        ):
            return (
                "Waiting for MoonMind provider profile slot; "
                f"runtime={runtime_id}; {intent}{profile_suffix}; "
                "missing_condition=moonmind_slot_capacity; "
                f"slots_in_use={slots_in_use}; max_parallel_runs={max_parallel_runs}"
                f"{queue_suffix}."
            )

        return (
            "Waiting in MoonMind provider profile queue; "
            f"runtime={runtime_id}; {intent}{profile_suffix}; "
            f"missing_condition=manager_queue{queue_suffix}."
        )

    @staticmethod
    def _resiliency_policy_for_request(
        request: AgentExecutionRequest,
        *,
        use_extended_claude_no_progress_window: bool = True,
    ) -> dict[str, Any]:
        """Return runtime-specific retry and stuck-detection policy metadata."""

        agent_id = _normalize_agent_runtime_id(request.agent_id)
        if request.agent_kind == "external":
            if agent_id in {"jules", "jules_api"}:
                return {
                    "runtime": agent_id,
                    "noProgressTimeoutSeconds": 2400,
                    "stuckAction": "request_intervention",
                    "retryPolicy": "provider_polling_with_human_feedback_escalation",
                }
            if agent_id == "codex_cloud":
                return {
                    "runtime": agent_id,
                    "noProgressTimeoutSeconds": 1800,
                    "stuckAction": "request_intervention",
                    "retryPolicy": "provider_polling_with_terminal_fetch",
                }
            return {
                "runtime": agent_id or request.agent_id,
                "noProgressTimeoutSeconds": _DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS,
                "stuckAction": "request_intervention",
                "retryPolicy": "generic_external_polling",
            }

        runtime_id = MoonMindAgentRun._managed_runtime_id(request.agent_id)
        if runtime_id == "codex_cli":
            return {
                "runtime": runtime_id,
                "noProgressTimeoutSeconds": 1800,
                "stuckAction": "request_intervention",
                "retryPolicy": "session_turn_self_heal_then_cooldown_retry",
            }
        if runtime_id == "claude_code":
            no_progress_timeout_seconds = 1500
            no_progress_grace_seconds = 600
            if use_extended_claude_no_progress_window:
                no_progress_timeout_seconds = _CLAUDE_CODE_NO_PROGRESS_TIMEOUT_SECONDS
                no_progress_grace_seconds = _CLAUDE_CODE_NO_PROGRESS_GRACE_SECONDS
            return {
                "runtime": runtime_id,
                "noProgressTimeoutSeconds": no_progress_timeout_seconds,
                "noProgressGraceSeconds": no_progress_grace_seconds,
                "stuckAction": "request_intervention",
                "retryPolicy": "managed_runtime_polling_with_profile_cooldown",
            }
        return {
            "runtime": runtime_id,
            "noProgressTimeoutSeconds": _DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS,
            "noProgressGraceSeconds": 300,
            "stuckAction": "request_intervention",
            "retryPolicy": "managed_runtime_polling_with_profile_cooldown",
        }

    def _refresh_selection_after_slot_assignment(
        self,
        request: AgentExecutionRequest,
        *,
        use_extended_claude_no_progress_window: bool,
    ) -> dict[str, Any]:
        """Refresh launch metadata and policy from the assigned selection."""

        if request.step_execution is not None:
            runtime_selection = dict(request.step_execution.runtime_selection or {})
            runtime_selection["runtimeId"] = request.agent_id
            if request.execution_profile_ref:
                runtime_selection["executionProfileRef"] = (
                    request.execution_profile_ref
                )
            else:
                runtime_selection.pop("executionProfileRef", None)
            request.step_execution.runtime_selection = runtime_selection
        return self._resiliency_policy_for_request(
            request,
            use_extended_claude_no_progress_window=(
                use_extended_claude_no_progress_window
            ),
        )

    @staticmethod
    def _status_progress_signature(status_obj: AgentRunStatusModel) -> tuple[Any, ...]:
        metadata = status_obj.metadata if isinstance(status_obj.metadata, Mapping) else {}
        progress_keys = (
            "providerStatus",
            "normalizedStatus",
            "progress",
            "progressPercent",
            "lastEventId",
            "latestActivityId",
            "latestAgentQuestion",
            "trackingRef",
            "externalUrl",
            "updatedAt",
            "lastUpdatedAt",
            "lastOutputAt",
            "lastLogAt",
            "lastLogOffset",
            "finishedAt",
            "exitCode",
            "stdoutArtifactRef",
            "stderrArtifactRef",
            "diagnosticsRef",
            "observabilityEventsRef",
            "activeTurnId",
        )
        return (
            status_obj.status,
            tuple((key, str(metadata.get(key))) for key in progress_keys if key in metadata),
        )

    @staticmethod
    def _max_no_progress_polls(
        *,
        policy: Mapping[str, Any],
        poll_interval: int,
    ) -> int:
        timeout_seconds = int(
            policy.get("noProgressTimeoutSeconds")
            or _DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS
        )
        return max(1, timeout_seconds // max(1, poll_interval))

    @staticmethod
    def _no_progress_grace_seconds(policy: Mapping[str, Any]) -> int:
        return max(0, int(policy.get("noProgressGraceSeconds") or 0))

    @staticmethod
    def _result_requires_provider_cooldown(result: AgentRunResult | None) -> bool:
        if result is None:
            return False
        metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
        provider_failure_event = provider_failure_event_from_metadata(
            metadata.get("providerFailure")
        )
        if provider_failure_event is not None:
            return provider_failure_event_requires_cooldown(provider_failure_event)
        return provider_error_requires_cooldown(
            provider_error_code=result.provider_error_code,
            retry_recommendation=result.retry_recommendation,
        )

    def _intervention_result(
        self,
        *,
        summary: str,
        request: AgentExecutionRequest,
        metadata: Mapping[str, Any] | None = None,
    ) -> AgentRunResult:
        result_metadata = {
            "status": "intervention_requested",
            "agentId": request.agent_id,
            "agentKind": request.agent_kind,
        }
        if self.run_id:
            result_metadata["runId"] = self.run_id
        if metadata:
            result_metadata.update(dict(metadata))
        return AgentRunResult(
            summary=summary,
            failureClass="user_error",
            providerErrorCode="intervention_requested",
            metadata=result_metadata,
        )

    def _timed_out_result(
        self,
        *,
        request: AgentExecutionRequest,
        timeout_seconds: float,
        detail: str,
        elapsed_seconds: float | None = None,
    ) -> AgentRunResult:
        """Build a failed result for an agent-run timeout with an actionable summary.

        ``failure_class`` stays ``execution_error`` so retry/category/billing
        semantics are unchanged; this only guarantees a non-empty, descriptive
        ``summary`` so downstream operator surfaces never collapse to the bare
        ``execution_error`` token (the runtime emits no summary of its own when
        the turn is aborted by a timeout).
        """
        elapsed_text = (
            f" after {int(elapsed_seconds)}s"
            if elapsed_seconds is not None
            else ""
        )
        summary = (
            f"Managed agent run {detail}{elapsed_text} "
            f"(execution budget {int(timeout_seconds)}s). No completed turn or "
            "result was produced; retry or human intervention is required."
        )
        metadata: dict[str, Any] = {
            "reason": "timed_out",
            "timeoutSeconds": int(timeout_seconds),
            "agentId": request.agent_id,
            "agentKind": request.agent_kind,
        }
        if elapsed_seconds is not None:
            metadata["elapsedSeconds"] = int(elapsed_seconds)
        return AgentRunResult(
            summary=summary,
            failure_class="execution_error",
            metadata=metadata,
        )

    async def _send_turn_within_budget(
        self,
        request_payload: Any,
        *,
        timeout_seconds: float,
        overall_start: datetime,
    ) -> Any:
        """Dispatch the managed-session turn activity under a workflow-side budget.

        The codex turn runs as a single long-blocking ``agent_runtime.send_turn``
        activity, so while the workflow is parked on that await it cannot run its
        own elapsed/no-progress checks; it relies solely on the activity's
        server-side timeout. When that timer is not enforced promptly a stuck or
        parked turn can run far past its budget before failing. Race the turn
        against a deterministic workflow timer so it is aborted at its budget.

        On deadline a typed ``ApplicationError`` is raised; it flows through the
        adapter's failure path so the operator gets a real summary rather than a
        bare ``execution_error``. Real activity errors/completions are returned or
        re-raised unchanged so existing retry/empty-turn handling is preserved.
        """
        turn_coro = self._execute_routed_activity(
            "agent_runtime.send_turn",
            request_payload,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        if not workflow.patched(MANAGED_SESSION_TURN_DEADLINE_PATCH_ID):
            return await turn_coro
        remaining = timeout_seconds - (workflow.now() - overall_start).total_seconds()
        deadline = max(remaining, _MIN_MANAGED_TURN_DEADLINE_SECONDS)
        turn_task = asyncio.create_task(turn_coro)
        try:
            await workflow.wait_condition(
                lambda: turn_task.done(),
                timeout=timedelta(seconds=deadline),
            )
        except asyncio.TimeoutError:
            turn_task.cancel()
            raise ApplicationError(
                f"Managed-session turn exceeded its {int(deadline)}s budget "
                "without completing; the turn was aborted and requires retry or "
                "human intervention.",
                type="ManagedSessionTurnDeadlineExceeded",
                non_retryable=True,
            )
        return turn_task.result()

    def _enrich_result_metadata(
        self,
        *,
        request: AgentExecutionRequest,
        result: AgentRunResult | None,
    ) -> AgentRunResult | None:
        if result is None:
            return result

        metadata = dict(result.metadata or {})
        metadata.setdefault("childWorkflowId", workflow.info().workflow_id)
        metadata.setdefault("childRunId", workflow.info().run_id)
        self._record_provider_native_pr_metadata(
            request=request,
            metadata=metadata,
        )
        if request.workspace_spec:
            workspace_spec = dict(request.workspace_spec)
            metadata.setdefault("workspaceSpec", workspace_spec)
            for key in (
                "workspaceLocator",
                "workspacePath",
                "workspaceRootRef",
                "workspaceRoot",
                "baseCommit",
                "workspaceCheckpointKind",
                "checkpointKind",
            ):
                value = workspace_spec.get(key)
                if value is not None:
                    metadata.setdefault(key, value)

        agent_run_id = ""
        if request.managed_session is not None:
            agent_run_id = str(request.managed_session.agent_run_id or "").strip()
            metadata["managedSession"] = request.managed_session.model_dump(
                mode="json",
                by_alias=True,
            )
            if request.instruction_ref:
                _setdefault_compact_ref_metadata(
                    metadata,
                    "instructionRef",
                    request.instruction_ref,
                )
            if request.resolved_skillset_ref:
                _setdefault_compact_ref_metadata(
                    metadata,
                    "resolvedSkillsetRef",
                    request.resolved_skillset_ref,
                )
        elif request.agent_kind == "managed":
            agent_run_id = str(self.run_id or "").strip()

        if agent_run_id:
            metadata.setdefault("agentRunId", agent_run_id)

        if request.agent_kind == "managed":
            runtime_id = (
                request.managed_session.runtime_id
                if request.managed_session is not None
                else request.agent_id
            )
            capabilities = resolve_runtime_execution_capabilities(runtime_id)
            metadata["agentKind"] = request.agent_kind
            metadata["agentId"] = capabilities.runtime_id
            metadata["runtimeCapabilities"] = capabilities.model_dump(
                by_alias=True,
                mode="json",
            )
            if (
                capabilities.workspace_authority == "managed_runtime"
                and agent_run_id
            ):
                metadata["workspaceLocator"] = {
                    "kind": "managed_runtime",
                    "runtimeId": capabilities.runtime_id,
                    "agentRunId": agent_run_id,
                    "relativePath": "repo",
                }
                for legacy_path_key in (
                    "workspacePath",
                    "workspace_path",
                    "workspaceRoot",
                    "workspace_root",
                ):
                    metadata.pop(legacy_path_key, None)
                nested_workspace_spec = metadata.get("workspaceSpec")
                if isinstance(nested_workspace_spec, Mapping):
                    sanitized_workspace_spec = dict(nested_workspace_spec)
                    for legacy_path_key in (
                        "workspacePath",
                        "workspace_path",
                        "workspaceRoot",
                        "workspace_root",
                    ):
                        sanitized_workspace_spec.pop(legacy_path_key, None)
                    metadata["workspaceSpec"] = sanitized_workspace_spec

        request_params = (
            request.parameters if isinstance(request.parameters, Mapping) else {}
        )
        selected_skill = _request_selected_skill(request)
        if selected_skill:
            moonmind_payload = (
                metadata.get("moonmind")
                if isinstance(metadata.get("moonmind"), dict)
                else {}
            )
            moonmind_payload.setdefault("selectedSkill", selected_skill)
            metadata["moonmind"] = moonmind_payload
        if str(selected_skill or "").strip().lower() == "moonspec-verify":
            for key in (
                "verify_artifact_path",
                "verifyArtifactPath",
                "verification_artifact_path",
                "verificationArtifactPath",
            ):
                value = request_params.get(key)
                if isinstance(value, str) and value.strip():
                    metadata["verify_artifact_path"] = value.strip()
                    break
        # Surface the assessment handoff path so publish_artifacts can publish the
        # verdict JSON as a durable artifact ref. Only the initial-assessment step
        # propagates this parameter (see _ensure_assessment_parameters), so no
        # skill gate is needed here.
        for key in ("assessment_artifact_path", "assessmentArtifactPath"):
            value = request_params.get(key)
            if isinstance(value, str) and value.strip():
                metadata["assessment_artifact_path"] = value.strip()
                break
        request_metadata = request_params.get("metadata")
        request_moonmind = (
            request_metadata.get("moonmind")
            if isinstance(request_metadata, Mapping)
            else None
        )
        parent_prepared_context = (
            request_moonmind.get("preparedContext")
            if isinstance(request_moonmind, Mapping)
            and isinstance(request_moonmind.get("preparedContext"), Mapping)
            else None
        )
        if parent_prepared_context is not None:
            moonmind_payload = (
                metadata.get("moonmind")
                if isinstance(metadata.get("moonmind"), dict)
                else {}
            )
            moonmind_payload["preparedContext"] = dict(parent_prepared_context)
            metadata["moonmind"] = moonmind_payload

        step_ledger_context = _request_step_ledger_context(request)
        report_output_context = (
            request.parameters.get("reportOutput")
            if isinstance(request.parameters, Mapping)
            else None
        )
        if step_ledger_context is not None:
            moonmind_payload = (
                metadata.get("moonmind")
                if isinstance(metadata.get("moonmind"), dict)
                else {}
            )
            moonmind_payload["stepLedger"] = step_ledger_context
            metadata["moonmind"] = moonmind_payload
        if isinstance(report_output_context, Mapping):
            moonmind_payload = (
                metadata.get("moonmind")
                if isinstance(metadata.get("moonmind"), dict)
                else {}
            )
            moonmind_payload["reportOutput"] = dict(report_output_context)
            metadata["moonmind"] = moonmind_payload

        if workflow.patched(STORY_BREAKDOWN_ARTIFACT_HANDOFF_PATCH_ID):
            params = (
                request.parameters if isinstance(request.parameters, Mapping) else {}
            )
            story_output_context = (
                params.get("storyOutput")
                if isinstance(params.get("storyOutput"), Mapping)
                else params.get("story_output")
                if isinstance(params.get("story_output"), Mapping)
                else None
            )

            def _story_path_param(camel_key: str, snake_key: str) -> str:
                return str(
                    params.get(camel_key)
                    or params.get(snake_key)
                    or (
                        story_output_context.get(camel_key)
                        if isinstance(story_output_context, Mapping)
                        else ""
                    )
                    or (
                        story_output_context.get(snake_key)
                        if isinstance(story_output_context, Mapping)
                        else ""
                    )
                    or ""
                ).strip()

            story_breakdown_path = _story_path_param(
                "storyBreakdownPath",
                "story_breakdown_path",
            )
            story_breakdown_markdown_path = _story_path_param(
                "storyBreakdownMarkdownPath",
                "story_breakdown_markdown_path",
            )
            if story_breakdown_path:
                metadata.setdefault("storyBreakdownPath", story_breakdown_path)
            if story_breakdown_markdown_path:
                metadata.setdefault(
                    "storyBreakdownMarkdownPath",
                    story_breakdown_markdown_path,
                )
            if story_output_context is not None and (
                story_breakdown_path or story_breakdown_markdown_path
            ):
                story_output_metadata = (
                    dict(metadata.get("storyOutput"))
                    if isinstance(metadata.get("storyOutput"), Mapping)
                    else dict(story_output_context)
                )
                if story_breakdown_path:
                    story_output_metadata.setdefault(
                        "storyBreakdownPath",
                        story_breakdown_path,
                    )
                if story_breakdown_markdown_path:
                    story_output_metadata.setdefault(
                        "storyBreakdownMarkdownPath",
                        story_breakdown_markdown_path,
                    )
                metadata["storyOutput"] = story_output_metadata

        return result.model_copy(update={"metadata": metadata})

    async def _publish_terminal_result_with_compacted_replay_cleanup(
        self,
        *,
        request: AgentExecutionRequest,
        result: AgentRunResult,
        manager_handle: Any | None = None,
    ) -> AgentRunResult:
        terminal_result = await self._publish_terminal_result(
            request=request,
            result=result,
        )
        if (
            self._terminal_result_payload_compacted_for_history
            and request.agent_kind == "managed"
            and request.execution_profile_ref
        ):
            handle = manager_handle
            if handle is None:
                runtime_id = self._managed_runtime_id(request.agent_id)
                manager_id = self._manager_workflow_id(runtime_id)
                handle = workflow.get_external_workflow_handle(manager_id)
            # Preserve replay for histories where oversized terminal metadata
            # raised after artifact publication and the old exception cleanup
            # emitted this idempotent slot release before failing the task.
            await handle.signal(
                "release_slot",
                self._release_slot_payload(
                    profile_id=request.execution_profile_ref,
                    request=request,
                ),
            )
            self._terminal_result_payload_compacted_for_history = False
        return terminal_result

    async def _publish_terminal_result(
        self,
        *,
        request: AgentExecutionRequest,
        result: AgentRunResult,
    ) -> AgentRunResult:
        self._terminal_result_payload_compacted_for_history = False
        self.final_result = self._enrich_result_metadata(
            request=request,
            result=result,
        )
        enriched_result = await self._execute_routed_activity(
            "agent_runtime.publish_artifacts",
            self.final_result,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        if isinstance(enriched_result, AgentRunResult):
            self.final_result = enriched_result
        elif isinstance(enriched_result, dict):
            if (
                "diagnosticsRef" in enriched_result
                and "diagnostics_ref" in enriched_result
            ):
                del enriched_result["diagnostics_ref"]
            try:
                self.final_result = AgentRunResult(**enriched_result)
            except ValueError:
                compacted_result = (
                    _compact_agent_run_result_payload_for_workflow_history(
                        enriched_result
                    )
                )
                if compacted_result == enriched_result:
                    raise
                self._terminal_result_payload_compacted_for_history = True
                self.final_result = AgentRunResult(**compacted_result)
        return self.final_result

    def _record_provider_native_pr_metadata(
        self,
        *,
        request: AgentExecutionRequest,
        metadata: dict[str, Any],
    ) -> None:
        if request.agent_kind != "external":
            return

        existing = metadata.get("providerNativePullRequest")
        existing_map = existing if isinstance(existing, Mapping) else {}
        raw_url = (
            existing_map.get("url")
            or existing_map.get("pullRequestUrl")
            or metadata.get("pullRequestUrl")
            or metadata.get("prUrl")
        )
        pull_request_url = str(raw_url or "").strip()
        if not pull_request_url:
            return

        workspace_spec = request.workspace_spec or {}
        parameters = (
            request.parameters if isinstance(request.parameters, Mapping) else {}
        )

        def _text(*values: Any) -> str | None:
            for value in values:
                candidate = str(value or "").strip()
                if candidate:
                    return candidate
            return None

        head_branch = _text(
            existing_map.get("headBranch"),
            existing_map.get("branch"),
            metadata.get("headBranch"),
            metadata.get("branch"),
            workspace_spec.get("headBranch"),
            workspace_spec.get("branch"),
            workspace_spec.get("startingBranch"),
        )
        base_branch = _text(
            existing_map.get("baseBranch"),
            existing_map.get("baseRef"),
            metadata.get("baseBranch"),
            metadata.get("baseRef"),
            parameters.get("targetBranch"),
            parameters.get("publishBaseBranch"),
            workspace_spec.get("targetBranch"),
            workspace_spec.get("publishBaseBranch"),
        )
        readiness_state = _text(
            existing_map.get("readinessState"),
            metadata.get("readinessState"),
        )
        if readiness_state is None:
            readiness_state = (
                "merged"
                if str(metadata.get("publishOutcome") or "").strip() == "branch_merged"
                else "pending"
            )

        provider_metadata = existing_map.get("metadata")
        if not isinstance(provider_metadata, Mapping):
            provider_metadata = metadata.get("prMetadata")
        if not isinstance(provider_metadata, Mapping):
            provider_metadata = {}

        envelope: dict[str, Any] = {
            "url": pull_request_url,
            "readinessState": readiness_state,
            "source": _text(existing_map.get("source"), request.agent_id) or "external",
        }
        if head_branch:
            envelope["headBranch"] = head_branch
        if base_branch:
            envelope["baseBranch"] = base_branch
        if provider_metadata:
            envelope["metadata"] = dict(provider_metadata)

        metadata["providerNativePullRequest"] = envelope
        metadata.setdefault("pullRequestUrl", pull_request_url)
        metadata.setdefault("readinessState", readiness_state)
        if head_branch:
            metadata.setdefault("headBranch", head_branch)
        if base_branch:
            metadata.setdefault("baseBranch", base_branch)

    def _managed_start_failure_result(
        self,
        *,
        request: AgentExecutionRequest,
        error: Exception,
    ) -> AgentRunResult:
        summary = str(error).strip()
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[: _MAX_SUMMARY_CHARS - 3] + "..."
        if not summary:
            summary = "Managed agent failed before execution started."
        metadata: dict[str, Any] = {"phase": "start"}
        if request.managed_session is not None:
            metadata["managedSession"] = request.managed_session.model_dump(
                mode="json",
                by_alias=True,
            )
        classification = classify_provider_failure(summary)
        provider_failure_event = build_provider_failure_event(
            classification=classification,
        )
        if provider_failure_event is not None:
            metadata["providerFailure"] = provider_failure_event.to_metadata()
        return AgentRunResult(
            summary=summary,
            failureClass=(
                classification.failure_class
                if classification is not None
                else "execution_error"
            ),
            providerErrorCode=(
                classification.provider_error_code
                if classification is not None
                else None
            ),
            retryRecommendation=(
                classification.retry_recommendation
                if classification is not None
                else None
            ),
            metadata=metadata,
        )

    @staticmethod
    def _uses_codex_session_adapter(request: AgentExecutionRequest) -> bool:
        return request.agent_kind == "managed" and request.managed_session is not None

    @staticmethod
    def _deferred_managed_session_intent(
        request: AgentExecutionRequest,
    ) -> dict[str, Any] | None:
        parameters = (
            request.parameters if isinstance(request.parameters, Mapping) else {}
        )
        metadata = parameters.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        moonmind = metadata_map.get("moonmind")
        moonmind_map = moonmind if isinstance(moonmind, Mapping) else {}
        raw_intent = moonmind_map.get("deferManagedSessionUntilSlot")
        if raw_intent is True:
            return {}
        if isinstance(raw_intent, Mapping):
            return dict(raw_intent)
        return None

    @staticmethod
    def _managed_session_runtime_id(
        request: AgentExecutionRequest,
    ) -> str | None:
        if request.agent_kind != "managed":
            return None
        return canonical_managed_session_runtime_id(request.agent_id)

    @staticmethod
    def _workflow_scoped_session_workflow_id(
        *,
        task_workflow_id: str,
        runtime_id: str,
    ) -> str:
        return f"{task_workflow_id}:session:{runtime_id}"

    @staticmethod
    def _workflow_scoped_session_visibility(
        *,
        binding: CodexManagedSessionBinding,
    ) -> dict[str, Any]:
        return {
            "AgentRunId": [binding.agent_run_id],
            "RuntimeId": [binding.runtime_id],
            "SessionId": [binding.session_id],
            "SessionEpoch": [binding.session_epoch],
            "SessionStatus": ["active"],
            "IsDegraded": [False],
        }

    @staticmethod
    def _workflow_scoped_session_static_details(
        *,
        binding: CodexManagedSessionBinding,
    ) -> str:
        return (
            "Workflow-scoped managed runtime session | "
            f"agentRunId={binding.agent_run_id} | "
            f"runtime={binding.runtime_id} | "
            f"session={binding.session_id} | "
            f"epoch={binding.session_epoch}"
        )

    async def _bind_deferred_workflow_scoped_session_after_slot(
        self,
        *,
        request: AgentExecutionRequest,
        runtime_id: str,
        parent_info: Any,
    ) -> AgentExecutionRequest:
        if request.managed_session is not None:
            return request
        if self._managed_session_detached_for_runtime_selection:
            return request
        if not workflow.patched(MANAGED_SESSION_START_AFTER_SLOT_PATCH_ID):
            return request
        intent = self._deferred_managed_session_intent(request)
        if intent is None:
            return request
        session_runtime_id = self._managed_session_runtime_id(request)
        if session_runtime_id is None:
            return request
        if session_runtime_id != runtime_id:
            raise ApplicationError(
                "Deferred managed session runtime does not match assigned runtime",
                type="ManagedSessionRuntimeMismatch",
                non_retryable=True,
            )

        task_workflow_id = str(intent.get("agentRunId") or "").strip()
        if not task_workflow_id and parent_info is not None:
            task_workflow_id = str(parent_info.workflow_id or "").strip()
        if not task_workflow_id:
            task_workflow_id = workflow.info().workflow_id

        session_input = CodexManagedSessionWorkflowInput(
            agentRunId=task_workflow_id,
            runtimeId=session_runtime_id,
            executionProfileRef=request.execution_profile_ref,
        )
        session_workflow_id = self._workflow_scoped_session_workflow_id(
            task_workflow_id=task_workflow_id,
            runtime_id=session_runtime_id,
        )
        binding = CodexManagedSessionBinding.from_input(
            workflow_id=session_workflow_id,
            session_input=session_input,
        )
        await workflow.start_child_workflow(
            "MoonMind.AgentSession",
            session_input,
            id=session_workflow_id,
            task_queue=self._workflow_child_task_queue(),
            parent_close_policy=workflow.ParentClosePolicy.ABANDON,
            search_attributes=self._workflow_scoped_session_visibility(binding=binding),
            static_summary="Workflow-scoped managed runtime session",
            static_details=self._workflow_scoped_session_static_details(binding=binding),
        )

        if parent_info is not None:
            parent_handle = workflow.get_external_workflow_handle(
                parent_info.workflow_id,
                run_id=parent_info.run_id,
            )
            await parent_handle.signal(
                "managed_session_bound",
                {
                    "binding": binding.model_dump(mode="json", by_alias=True),
                    "child_workflow_id": workflow.info().workflow_id,
                    "runtime_id": runtime_id,
                },
            )
        return request.model_copy(update={"managed_session": binding})

    @staticmethod
    def _request_workspace_starting_branch(
        request: AgentExecutionRequest,
    ) -> str | None:
        workspace_spec = (
            request.workspace_spec
            if isinstance(request.workspace_spec, Mapping)
            else {}
        )
        branch = str(
            workspace_spec.get("startingBranch")
            or workspace_spec.get("branch")
            or ""
        ).strip()
        return branch or None

    @staticmethod
    def _request_workspace_target_branch(
        request: AgentExecutionRequest,
    ) -> str | None:
        workspace_spec = (
            request.workspace_spec
            if isinstance(request.workspace_spec, Mapping)
            else {}
        )
        branch = str(
            workspace_spec.get("targetBranch")
            or ""
        ).strip()
        return branch or None

    def _build_managed_fetch_result_activity_input(
        self,
        request: AgentExecutionRequest,
    ) -> AgentRuntimeFetchResultInput:
        params = request.parameters if isinstance(request.parameters, Mapping) else {}
        raw_publish_mode = params.get("publishMode")
        publish_mode = (
            str(raw_publish_mode).strip().lower()
            if isinstance(raw_publish_mode, str) and raw_publish_mode.strip()
            else "none"
        )

        run_id = str(self.run_id or "").strip()
        if request.managed_session is not None:
            run_id = str(request.managed_session.agent_run_id).strip()

        activity_input: dict[str, Any] = {
            "runId": run_id,
            "agentId": request.agent_id,
        }
        if publish_mode != "none":
            activity_input["publishMode"] = publish_mode

        raw_commit_message = params.get("commitMessage")
        if isinstance(raw_commit_message, str) and raw_commit_message.strip():
            activity_input["commitMessage"] = raw_commit_message.strip()

        if workflow.patched(MANAGED_SESSION_PR_PUBLISH_BASE_BRANCH_PATCH_ID):
            publish_payload = (
                params.get("publish")
                if isinstance(params.get("publish"), Mapping)
                else {}
            )
            publish_base_branch = str(
                params.get("publishBaseBranch")
                or params.get("prBaseBranch")
                or params.get("baseBranch")
                or publish_payload.get("prBaseBranch")
                or publish_payload.get("baseBranch")
                or ""
            ).strip()
            target_branch = str(
                publish_base_branch
                or self._request_workspace_starting_branch(request)
                or ""
            ).strip()
        else:
            target_branch = str(
                params.get("publishBaseBranch")
                or self._request_workspace_starting_branch(request)
                or ""
            ).strip()
        if target_branch:
            activity_input["targetBranch"] = target_branch

        head_branch = str(
            params.get("targetBranch")
            or self._request_workspace_target_branch(request)
            or ""
        ).strip()
        if head_branch:
            activity_input["headBranch"] = head_branch

        selected_skill = _request_selected_skill(
            request,
            include_payload_contract=workflow.patched(
                PR_RESOLVER_PAYLOAD_SKILL_DETECTION_PATCH_ID
            ),
        )
        if selected_skill == "pr-resolver":
            activity_input["prResolverExpected"] = True
            if workflow.patched(PR_RESOLVER_MERGE_GATE_OWNERSHIP_PATCH_ID):
                activity_input["prResolverMergeGateOwned"] = (
                    _request_pr_resolver_merge_gate_owned(request)
                )
        terminal_checkpoint_patch_active = workflow.patched(
            TERMINAL_CHECKPOINT_PUBLICATION_PATCH_ID
        )
        workspace_spec = (
            request.workspace_spec if isinstance(request.workspace_spec, Mapping) else {}
        )
        checkpoint_policy = (
            params.get("checkpointPolicy")
            if isinstance(params.get("checkpointPolicy"), Mapping)
            else {}
        )
        if terminal_checkpoint_patch_active:
            activity_input["terminalCheckpointPublicationEnabled"] = bool(
                checkpoint_policy.get("publishOnGracefulFailure", True)
            )
            activity_input["noRemoteWrites"] = bool(
                params.get("noRemoteWrites") or workspace_spec.get("noRemoteWrites")
            )
            activity_input["readOnly"] = bool(
                params.get("readOnly") or workspace_spec.get("readOnly")
            )
            activity_input["dryRun"] = bool(params.get("dryRun"))
            activity_input["workspaceAuthoritative"] = not bool(
                workspace_spec.get("authorityLost")
            )
            activity_input["terminalCheckpointCapabilitySupported"] = not bool(
                workspace_spec.get("terminalCheckpointPublicationUnsupported")
            )
        return AgentRuntimeFetchResultInput.model_validate(activity_input)

    async def _fetch_managed_result(
        self,
        *,
        request: AgentExecutionRequest,
        adapter: AgentAdapter,
        uses_codex_session_adapter: bool,
        use_managed_status_activity: bool,
    ) -> AgentRunResult:
        def _pr_resolver_fetch_flags() -> tuple[bool, bool]:
            expected = (
                _request_selected_skill(
                    request,
                    include_payload_contract=workflow.patched(
                        PR_RESOLVER_PAYLOAD_SKILL_DETECTION_PATCH_ID
                    ),
                )
                == "pr-resolver"
            )
            if not expected:
                return False, False
            gate_owned = (
                _request_pr_resolver_merge_gate_owned(request)
                if workflow.patched(PR_RESOLVER_MERGE_GATE_OWNERSHIP_PATCH_ID)
                else False
            )
            return True, gate_owned

        if uses_codex_session_adapter:
            if workflow.patched(MANAGED_SESSION_FETCH_RESULT_ACTIVITY_PATCH_ID):
                result_payload = await self._execute_routed_activity(
                    "agent_runtime.fetch_result",
                    self._build_managed_fetch_result_activity_input(request),
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )
                return (
                    AgentRunResult(**result_payload)
                    if isinstance(result_payload, dict)
                    else result_payload
                )

            pr_resolver_expected, pr_resolver_merge_gate_owned = (
                _pr_resolver_fetch_flags()
            )
            return await adapter.fetch_result(
                self.run_id,
                pr_resolver_expected=pr_resolver_expected,
                pr_resolver_merge_gate_owned=pr_resolver_merge_gate_owned,
            )

        if use_managed_status_activity:
            result_payload = await self._execute_routed_activity(
                "agent_runtime.fetch_result",
                self._build_managed_fetch_result_activity_input(request),
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            return (
                AgentRunResult(**result_payload)
                if isinstance(result_payload, dict)
                else result_payload
            )

        pr_resolver_expected, pr_resolver_merge_gate_owned = _pr_resolver_fetch_flags()
        return await adapter.fetch_result(
            self.run_id,
            pr_resolver_expected=pr_resolver_expected,
            pr_resolver_merge_gate_owned=pr_resolver_merge_gate_owned,
        )

    async def _evaluate_terminal_contract(
        self,
        *,
        request: AgentExecutionRequest,
        result: AgentRunResult,
    ) -> AgentRunResult:
        """Enforce terminal evidence at the runtime-neutral AgentRun boundary."""
        if request.terminal_contract is None:
            return result
        workspace_path = str(
            (result.metadata or {}).get("workspacePath")
            or (request.workspace_spec or {}).get("workspacePath")
            or ""
        ).strip()
        async def _evaluate(candidate: AgentRunResult) -> AgentRunResult:
            payload = await self._execute_routed_activity(
                "agent_runtime.evaluate_terminal_evidence",
                {
                    "runId": (
                        request.managed_session.agent_run_id
                        if request.managed_session is not None
                        else str(self.run_id or "")
                    ),
                    "workspacePath": workspace_path,
                    "artifactSpoolPath": (
                        os.path.join(
                            _MANAGED_RUNTIME_STORE_ROOT,
                            request.managed_session.agent_run_id,
                            "artifacts",
                        )
                        if request.managed_session is not None
                        else ""
                    ),
                    "terminalContract": request.terminal_contract.model_dump(
                        mode="json", by_alias=True
                    ),
                    "result": candidate.model_dump(mode="json", by_alias=True),
                },
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            return (
                AgentRunResult.model_validate(payload)
                if isinstance(payload, Mapping)
                else payload
            )

        evaluated = await _evaluate(result)
        try:
            continuation_enabled = workflow.patched(
                TERMINAL_CONTRACT_CONTINUATION_PATCH_ID
            )
        except Exception as exc:
            # Direct activity-boundary tests execute this helper outside a
            # Temporal workflow event loop.
            if type(exc).__name__ != "_NotInWorkflowEventLoopError":
                raise
            continuation_enabled = True
        if not continuation_enabled:
            return evaluated
        try:
            owned_continuation_enabled = workflow.patched(
                PR_RESOLVER_OWNED_CONTINUATION_PATCH_ID
            )
        except Exception as exc:
            if type(exc).__name__ != "_NotInWorkflowEventLoopError":
                raise
            owned_continuation_enabled = True
        continuation_authority = request.terminal_continuation_authority
        synthetic_reenter_gate_failure = (
            evaluated.failure_class == "execution_error"
            and evaluated.provider_error_code == "PR_RESOLVER_REENTER_GATE"
        )
        if (
            owned_continuation_enabled
            and (evaluated.metadata or {}).get("terminalContractOutcome")
            == "continuation_requested"
            and continuation_authority is not None
            and continuation_authority.allows(
                gate_type="merge_automation", action="reenter_gate"
            )
            and synthetic_reenter_gate_failure
        ):
            metadata = dict(evaluated.metadata or {})
            metrics = dict(evaluated.metrics or {})
            continuation = metadata.get("gatedContinuation")
            continuation = continuation if isinstance(continuation, Mapping) else {}
            observability_enabled = workflow.patched(
                PR_RESOLVER_CONTINUATION_OBSERVABILITY_PATCH_ID
            )
            if observability_enabled:
                metrics.update(
                    {
                        "continuation_requested": 1,
                        "continuation_accepted": 1,
                    }
                )
            metadata.update(
                {
                    "terminalContractRecoveryOutcome": "durable_parent_handoff",
                    "terminalContractContinuationCount": 0,
                    "gateType": continuation_authority.gate_type,
                    "gateAction": "reenter_gate",
                    "gateOwnerWorkflowId": continuation_authority.owner_workflow_id,
                    "gateOwnerRunId": continuation_authority.owner_run_id,
                    "gateOwnerWorkflowType": continuation_authority.owner_workflow_type,
                }
            )
            if observability_enabled:
                metadata.update(
                    {
                        "continuationReason": continuation.get("reason"),
                        "continuationNotBefore": continuation.get("notBefore"),
                        "continuationRetryAfterSeconds": continuation.get(
                            "retryAfterSeconds"
                        ),
                        "continuationTimingSource": (
                            "skill_not_before"
                            if continuation.get("notBefore")
                            else (
                                "skill_retry_after"
                                if continuation.get("retryAfterSeconds") is not None
                                else "legacy_fallback"
                            )
                        ),
                    }
                )
            return evaluated.model_copy(
                update={
                    "failure_class": None,
                    "provider_error_code": None,
                    "retry_recommendation": None,
                    "metadata": metadata,
                    "metrics": metrics,
                }
            )
        if (
            (evaluated.metadata or {}).get("terminalContractOutcome")
            == "continuation_requested"
        ):
            metadata = dict(evaluated.metadata or {})
            metrics = dict(evaluated.metrics or {})
            rejection = (
                "continuation_rejected_failure_provenance"
                if continuation_authority is not None
                else "continuation_rejected_unowned"
            )
            if workflow.patched(PR_RESOLVER_CONTINUATION_OBSERVABILITY_PATCH_ID):
                metrics["continuation_requested"] = 1
                metrics[
                    "continuation_rejected_schema"
                    if continuation_authority is not None
                    else "continuation_rejected_ownership"
                ] = 1
            metadata.update(
                {
                    "terminalContractRecoveryOutcome": rejection,
                    "terminalContractContinuationCount": 0,
                }
            )
            return evaluated.model_copy(
                update={"metadata": metadata, "metrics": metrics}
            )
        if evaluated.failure_class is None:
            return evaluated

        runtime_id = (
            request.managed_session.runtime_id
            if request.managed_session is not None
            else self._managed_runtime_id(request.agent_id)
        )
        capabilities = resolve_runtime_execution_capabilities(runtime_id)
        history: list[dict[str, Any]] = []
        if not capabilities.supports_same_session_continuation:
            metadata = dict(evaluated.metadata or {})
            metadata.update(
                {
                    "terminalContractRecoveryOutcome": "continuation_unsupported",
                    "terminalContractContinuationCount": 0,
                    "runtimeCapabilityDigest": capabilities.capability_digest,
                }
            )
            return evaluated.model_copy(update={"metadata": metadata})

        # Same-session continuation is currently exposed by the managed-session
        # activity boundary. Capability policy and retry ownership remain here in
        # AgentRun; adapters only translate an individual runtime turn.
        if request.managed_session is None:
            metadata = dict(evaluated.metadata or {})
            metadata.update(
                {
                    "terminalContractRecoveryOutcome": "continuation_boundary_unavailable",
                    "terminalContractContinuationCount": 0,
                    "runtimeCapabilityDigest": capabilities.capability_digest,
                }
            )
            return evaluated.model_copy(update={"metadata": metadata})

        for continuation in range(1, _MAX_TERMINAL_CONTRACT_CONTINUATIONS + 1):
            missing = [
                str(item)
                for item in (evaluated.metadata or {}).get(
                    "terminalContractMissingEvidence", []
                )
            ]
            snapshot_request = request.managed_session.model_dump(
                mode="json", by_alias=True
            )
            snapshot = await self._execute_routed_activity(
                "agent_runtime.load_session_snapshot",
                snapshot_request,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            epoch_value = snapshot.get("sessionEpoch")
            turn_request = SendCodexManagedSessionTurnRequest(
                sessionId=request.managed_session.session_id,
                sessionEpoch=int(
                    epoch_value
                    if epoch_value is not None
                    else request.managed_session.session_epoch
                ),
                containerId=snapshot.get("containerId"),
                threadId=snapshot.get("threadId"),
                instructions=_terminal_contract_continuation_instruction(missing),
                reason="incomplete_terminal_contract",
                requestId=(
                    f"{request.idempotency_key}:terminal-contract:{continuation}"
                ),
                environment=build_codex_managed_session_turn_environment(
                    active_skills_dir=str(
                        request.parameters.get("_moonmindActiveSkillsDir") or ""
                    ),
                    step_execution_id=(
                        request.step_execution.step_execution_id
                        if request.step_execution is not None
                        else None
                    ),
                ),
            )
            history.append(
                {"continuation": continuation, "reason": "missing_terminal_evidence", "outcome": "requested"}
            )
            try:
                await self._execute_routed_activity(
                    "agent_runtime.send_turn",
                    turn_request,
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )
            except Exception:
                history[-1]["outcome"] = "provider_failure"
                break
            refreshed = await self._fetch_managed_result(
                request=request,
                adapter=None,  # activity-backed managed-session fetch ignores the adapter
                uses_codex_session_adapter=True,
                use_managed_status_activity=True,
            )
            evaluated = await _evaluate(refreshed)
            history[-1]["outcome"] = (
                "recovered" if evaluated.failure_class is None else "incomplete"
            )
            if evaluated.failure_class is None:
                break

        metadata = dict(evaluated.metadata or {})
        metadata.update(
            {
                "terminalContractContinuationCount": len(history),
                "terminalContractContinuationHistory": history,
                "terminalContractRecoveryOutcome": (
                    "recovered"
                    if evaluated.failure_class is None
                    else history[-1]["outcome"] if history else "exhausted"
                ),
                "runtimeCapabilityDigest": capabilities.capability_digest,
            }
        )
        return evaluated.model_copy(update={"metadata": metadata})

    async def _poll_managed_status(
        self,
        *,
        request: AgentExecutionRequest,
        adapter: AgentAdapter,
        uses_codex_session_adapter: bool,
        use_managed_status_activity: bool,
    ) -> AgentRunStatusModel:
        if uses_codex_session_adapter:
            return await adapter.status(self.run_id)
        if use_managed_status_activity:
            status_payload = await self._execute_routed_activity(
                "agent_runtime.status",
                AgentRuntimeStatusInput(
                    runId=str(self.run_id or ""),
                    agentId=request.agent_id,
                ),
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            return self._coerce_managed_status_payload(
                status_payload=status_payload,
                run_id=str(self.run_id or ""),
                fallback_agent_id=request.agent_id,
            )

        # Legacy replay-compatibility path: older histories recorded timer-only
        # polling for managed runs. Avoid mutable run-store reads here.
        return AgentRunStatusModel(
            runId=str(self.run_id or ""),
            agentKind="managed",
            agentId=request.agent_id,
            status=RunStatus.running,
        )

    async def _reconcile_managed_no_progress(
        self,
        *,
        request: AgentExecutionRequest,
        adapter: AgentAdapter,
        uses_codex_session_adapter: bool,
        use_managed_status_activity: bool,
        resiliency_policy: Mapping[str, Any],
        poll_interval: int,
        initial_status: AgentRunStatusModel,
        overall_start: datetime,
        timeout_seconds: int,
    ) -> AgentRunResult | None:
        grace_seconds = self._no_progress_grace_seconds(resiliency_policy)
        if grace_seconds <= 0:
            return None

        deadline = workflow.now() + timedelta(seconds=grace_seconds)
        status_obj = initial_status
        while True:
            self.run_status = status_obj.status
            if status_obj.status in _TERMINAL_RUN_STATUSES:
                return await self._fetch_managed_result(
                    request=request,
                    adapter=adapter,
                    uses_codex_session_adapter=uses_codex_session_adapter,
                    use_managed_status_activity=use_managed_status_activity,
                )

            remaining_grace = (deadline - workflow.now()).total_seconds()
            remaining_overall = timeout_seconds - (
                workflow.now() - overall_start
            ).total_seconds()
            remaining = min(remaining_grace, remaining_overall)
            if remaining <= 0:
                if workflow.patched(
                    AGENT_RUN_MANAGED_NO_PROGRESS_CANCEL_FETCH_PATCH_ID
                ):
                    return await self._cancel_managed_no_progress_and_fetch_cooldown_result(
                        request=request,
                        adapter=adapter,
                        uses_codex_session_adapter=uses_codex_session_adapter,
                        use_managed_status_activity=use_managed_status_activity,
                        poll_interval=poll_interval,
                        remaining_overall=remaining_overall,
                    )
                return None

            try:
                completed = await workflow.wait_condition(
                    lambda: self.completion_event.is_set(),
                    timeout=timedelta(seconds=min(poll_interval, remaining)),
                )
            except (asyncio.TimeoutError, TimeoutError):
                completed = False

            if completed or self.completion_event.is_set():
                return self.final_result

            status_obj = await self._poll_managed_status(
                request=request,
                adapter=adapter,
                uses_codex_session_adapter=uses_codex_session_adapter,
                use_managed_status_activity=use_managed_status_activity,
            )

    async def _cancel_managed_no_progress_and_fetch_cooldown_result(
        self,
        *,
        request: AgentExecutionRequest,
        adapter: AgentAdapter,
        uses_codex_session_adapter: bool,
        use_managed_status_activity: bool,
        poll_interval: int,
        remaining_overall: float,
    ) -> AgentRunResult | None:
        """Force silent managed runtimes to surface quota/capacity failures.

        Claude Code can remain silent while blocked on provider/session quota and
        emit the 429/session-limit line only after the process is interrupted.
        Before converting no-progress into human intervention, cancel the owned
        process and briefly reconcile the canonical runtime result so provider
        cooldown handling can take over when the supervisor classifies a 429.
        """

        if not self.run_id or remaining_overall <= 0:
            return None

        try:
            await self._execute_routed_activity(
                "agent_runtime.cancel",
                AgentRuntimeCancelInput(
                    agentKind=request.agent_kind,
                    runId=str(self.run_id or ""),
                ),
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
        except Exception:
            self._get_logger().warning(
                "Failed to cancel managed runtime during no-progress reconciliation.",
                exc_info=True,
            )
            return None

        reconcile_seconds = min(
            max(float(poll_interval) * 2.0, 10.0),
            max(1.0, float(remaining_overall)),
        )
        deadline = workflow.now() + timedelta(seconds=reconcile_seconds)

        while True:
            if self._result_requires_provider_cooldown(self.final_result):
                return self.final_result

            remaining = (deadline - workflow.now()).total_seconds()
            if remaining <= 0:
                return None

            try:
                completed = await workflow.wait_condition(
                    lambda: self.completion_event.is_set(),
                    timeout=timedelta(seconds=min(float(poll_interval), remaining)),
                )
            except (asyncio.TimeoutError, TimeoutError):
                completed = False

            if completed or self.completion_event.is_set():
                if self._result_requires_provider_cooldown(self.final_result):
                    return self.final_result
                return None

            status_obj = await self._poll_managed_status(
                request=request,
                adapter=adapter,
                uses_codex_session_adapter=uses_codex_session_adapter,
                use_managed_status_activity=use_managed_status_activity,
            )
            self.run_status = status_obj.status
            if status_obj.status in _TERMINAL_RUN_STATUSES:
                result = await self._fetch_managed_result(
                    request=request,
                    adapter=adapter,
                    uses_codex_session_adapter=uses_codex_session_adapter,
                    use_managed_status_activity=use_managed_status_activity,
                )
                if self._result_requires_provider_cooldown(result):
                    return result
                if status_obj.status != RunStatus.cancelled:
                    return None

    async def _ensure_manager_and_signal(
        self,
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool = True,
        execution_profile_ref: str | None = None,
        profile_selector: dict | None = None,
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> workflow.ExternalWorkflowHandle:
        """Signal the ProviderProfileManager for slot requests; auto-start it on first failure.

        Tries the signal. If the manager workflow doesn't exist, starts it
        via the ``provider_profile.ensure_manager`` activity and retries once.
        When ``request_slot`` is false this only returns the external handle;
        pre-slot profile sync owns its own signal-with-start retry so an
        already-running singleton does not pay an unconditional activity hop.
        
        Note: The Temporal Python SDK does not provide a `signal_with_start`
        method on `workflow.ExternalWorkflowHandle` objects for use inside a
        workflow. Therefore, we use this try/catch/activity/retry pattern to
        emulate the behavior.
        """
        manager_handle = workflow.get_external_workflow_handle(manager_id)
        signal_payload = {
            "requester_workflow_id": workflow.info().workflow_id,
            "runtime_id": runtime_id,
            "purpose": "execution_direct",
            "metadata": {
                "workflowId": workflow.info().workflow_id,
                "ownerIsWorkflow": True,
            },
        }
        if request_priority is not None:
            signal_payload["priority"] = request_priority
        if request_queue_metadata:
            signal_payload.update(request_queue_metadata)
        if workflow.patched(SLOT_HANDOFF_PATCH_ID):
            lease_group_id = self._lease_group_id()
            if lease_group_id:
                signal_payload["lease_group_id"] = lease_group_id
        if execution_profile_ref:
            signal_payload["execution_profile_ref"] = execution_profile_ref
        if profile_selector:
            signal_payload["profile_selector"] = profile_selector
        if not request_slot:
            return manager_handle

        for attempt in range(2):
            try:
                await manager_handle.signal("request_slot", signal_payload)
                return manager_handle
            except ApplicationError as exc:
                if "ExternalWorkflowExecutionNotFound" not in (
                    getattr(exc, "type", None) or str(exc)
                ):
                    raise
                if attempt > 0:
                    raise
            self._get_logger().warning(
                "ProviderProfileManager %s not found, auto-starting via activity",
                manager_id,
            )
            await self._execute_routed_activity(
                "provider_profile.ensure_manager",
                {"runtime_id": runtime_id},
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            # Re-acquire handle and retry the signal.
            manager_handle = workflow.get_external_workflow_handle(manager_id)
        return manager_handle

    @staticmethod
    def _request_priority(request: AgentExecutionRequest) -> int:
        parameters = request.parameters
        if not isinstance(parameters, Mapping):
            return 0
        try:
            return int(parameters.get("priority", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _request_queue_metadata(request: AgentExecutionRequest) -> dict[str, Any]:
        parameters = request.parameters
        if not isinstance(parameters, Mapping):
            return {}
        metadata = parameters.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        moonmind = metadata_map.get("moonmind")
        moonmind_map = moonmind if isinstance(moonmind, Mapping) else {}
        payload: dict[str, Any] = {}
        raw_queue_order = moonmind_map.get(
            "queueOrder",
            moonmind_map.get("queue_order"),
        )
        if raw_queue_order is not None and not isinstance(raw_queue_order, bool):
            try:
                payload["queue_order"] = int(raw_queue_order)
            except (TypeError, ValueError):
                # Queue metadata is best-effort; invalid values should not block
                # otherwise valid slot requests.
                pass
        raw_queued_at = moonmind_map.get("queuedAt", moonmind_map.get("queued_at"))
        queued_at = str(raw_queued_at or "").strip()
        if queued_at:
            payload["queued_at"] = queued_at
        return payload

    async def _manager_state_for_slot_wait(
        self,
        *,
        runtime_id: str,
        requester_workflow_id: str,
        execution_profile_ref: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a compact manager-health snapshot before resetting it."""

        payload = {
            "runtime_id": runtime_id,
            "requester_workflow_id": requester_workflow_id,
        }
        if execution_profile_ref is not None:
            payload["execution_profile_ref"] = execution_profile_ref
        result = await self._execute_routed_activity(
            "provider_profile.manager_state",
            payload,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        if isinstance(result, dict):
            return result
        return {"running": False, "error": "invalid_manager_state_payload"}

    async def _inspected_provider_slot_waiting_reason(
        self,
        *,
        manager_id: str,
        runtime_id: str,
        request: AgentExecutionRequest,
    ) -> str:
        waiting_reason = self._build_provider_slot_waiting_reason(
            runtime_id=runtime_id,
            request=request,
        )
        try:
            manager_state = await self._manager_state_for_slot_wait(
                runtime_id=runtime_id,
                requester_workflow_id=workflow.info().workflow_id,
                execution_profile_ref=request.execution_profile_ref,
            )
            if manager_state.get("running") is True:
                waiting_reason = self._build_manager_slot_waiting_reason(
                    runtime_id=runtime_id,
                    request=request,
                    manager_state=manager_state,
                )
        except CancelledError:
            raise
        except Exception as exc:
            self._get_logger().warning(
                "Auth profile manager %s state inspection failed while building the slot wait reason; using generic reason: %s",
                manager_id,
                exc,
            )
        return waiting_reason

    async def _reset_and_request_slot(
        self,
        manager_id: str,
        runtime_id: str,
        *,
        execution_profile_ref: str | None = None,
        profile_selector: dict | None = None,
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> workflow.ExternalWorkflowHandle:
        """Run the replay-only legacy recovery command and re-request a slot.

        The activity type is retained for histories that already scheduled it,
        but its implementation is non-destructive.
        """
        self._get_logger().warning(
            "Slot wait timed out — invoking legacy non-destructive ProviderProfileManager recovery for %s",
            manager_id,
        )
        await self._execute_routed_activity(
            "provider_profile.reset_manager",
            {"runtime_id": runtime_id},
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        return await self._ensure_manager_and_signal(
            manager_id,
            runtime_id,
            request_slot=True,
            execution_profile_ref=execution_profile_ref,
            profile_selector=profile_selector,
            request_priority=request_priority,
            request_queue_metadata=request_queue_metadata,
        )

    async def _recover_and_request_slot(
        self,
        manager_id: str,
        runtime_id: str,
        *,
        execution_profile_ref: str | None = None,
        profile_selector: dict | None = None,
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> workflow.ExternalWorkflowHandle:
        """Ensure the singleton exists and re-request without revoking leases."""
        self._get_logger().warning(
            "Slot wait timed out — ensuring ProviderProfileManager %s without resetting lease authority",
            manager_id,
        )
        await self._execute_routed_activity(
            "provider_profile.ensure_manager",
            {"runtime_id": runtime_id},
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        return await self._ensure_manager_and_signal(
            manager_id,
            runtime_id,
            request_slot=True,
            execution_profile_ref=execution_profile_ref,
            profile_selector=profile_selector,
            request_priority=request_priority,
            request_queue_metadata=request_queue_metadata,
        )

    async def _ensure_manager_started(
        self,
        manager_id: str,
        runtime_id: str,
    ) -> workflow.ExternalWorkflowHandle:
        """Return a provider-profile manager handle without requesting a slot."""
        return await self._ensure_manager_and_signal(
            manager_id,
            runtime_id,
            request_slot=False,
            execution_profile_ref=None,
            profile_selector=None,
            request_priority=None,
            request_queue_metadata=None,
        )

    async def _sync_manager_profiles(
        self,
        *,
        manager_id: str,
        manager_handle: workflow.ExternalWorkflowHandle,
        runtime_id: str,
    ) -> int:
        """Best-effort manager refresh from DB-backed provider_profile.list snapshot."""
        try:
            profile_snapshot = await self._execute_routed_activity(
                "provider_profile.list",
                {"runtime_id": runtime_id},
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            profiles = []
            if isinstance(profile_snapshot, dict):
                raw_profiles = profile_snapshot.get("profiles", [])
                if isinstance(raw_profiles, list):
                    profiles = raw_profiles
            self._profile_snapshots = {
                str(profile.get("profile_id")).strip(): profile
                for profile in profiles
                if isinstance(profile, dict) and str(profile.get("profile_id", "")).strip()
            }
            signal_payload = {"profiles": profiles}
            for attempt in range(2):
                try:
                    await manager_handle.signal("sync_profiles", signal_payload)
                    break
                except ApplicationError as exc:
                    if "ExternalWorkflowExecutionNotFound" not in (
                        getattr(exc, "type", None) or str(exc)
                    ):
                        raise
                    if attempt > 0:
                        raise
                self._get_logger().warning(
                    "ProviderProfileManager %s not found, auto-starting before profile sync",
                    manager_id,
                )
                await self._execute_routed_activity(
                    "provider_profile.ensure_manager",
                    {"runtime_id": runtime_id},
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )
                manager_handle = workflow.get_external_workflow_handle(manager_id)
            return len(profiles)
        except Exception:
            self._get_logger().warning(
                "Failed to sync provider profiles for runtime_id=%s; continuing with manager state",
                runtime_id,
                exc_info=True,
            )
            return -1

    def _manager_workflow_id(self, runtime_id: str) -> str:
        """Return the manager workflow ID for this execution's compatibility line."""

        if workflow.patched(PROVIDER_PROFILE_MANAGER_ID_PATCH):
            return workflow_id_for_runtime(runtime_id)
        return _legacy_manager_workflow_id(runtime_id)

    def _lease_group_id(self) -> str | None:
        parent_info = workflow.info().parent
        if parent_info is None:
            return None
        return str(parent_info.workflow_id or "").strip() or None

    def _release_slot_payload(
        self,
        *,
        profile_id: str | None,
        request: AgentExecutionRequest,
        reserve_for_followup: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "requester_workflow_id": workflow.info().workflow_id,
            "profile_id": profile_id,
        }
        if workflow.patched(SLOT_HANDOFF_PATCH_ID):
            lease_group_id = self._lease_group_id()
            if lease_group_id:
                payload["lease_group_id"] = lease_group_id
            if reserve_for_followup and _request_reserves_slot_for_immediate_followup(
                request
            ):
                payload["handoff_ttl_seconds"] = _SLOT_HANDOFF_TTL_SECONDS
        return payload

    @workflow.signal
    def completion_signal(self, result_dict: dict) -> None:
        self.final_result = AgentRunResult(**result_dict)
        self.run_status = RunStatus.completed
        self.completion_event.set()

    @workflow.signal
    def slot_assigned(self, payload: dict) -> None:
        self._assigned_profile_id = payload.get("profile_id")
        self.slot_assigned_event.set()

    @workflow.update(name="Pause")
    def pause(self) -> None:
        self._paused = True

    @pause.validator
    def validate_pause(self) -> None:
        if self._paused:
            raise ValueError("Agent run is already paused.")
        if self.run_status in _TERMINAL_RUN_STATUSES:
            raise ValueError("Cannot pause a terminal agent run.")

    @workflow.update(name="Resume")
    def resume(self) -> None:
        self._paused = False

    @resume.validator
    def validate_resume(self) -> None:
        if not self._paused:
            raise ValueError("Agent run is not paused.")
        if self.run_status in _TERMINAL_RUN_STATUSES:
            raise ValueError("Cannot resume a terminal agent run.")

    @workflow.signal
    def update_runtime_selection(self, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        update_payload: dict[str, Any] = {}
        profile_ref_source_keys = {
            "executionProfileRef",
            "execution_profile_ref",
            "profileId",
            "profile_id",
            "providerProfile",
        }
        for source_key, target_key in (
            ("executionProfileRef", "executionProfileRef"),
            ("execution_profile_ref", "executionProfileRef"),
            ("profileId", "executionProfileRef"),
            ("profile_id", "executionProfileRef"),
            ("providerProfile", "executionProfileRef"),
            ("model", "model"),
            ("requestedModel", "model"),
            ("requested_model", "model"),
            ("effort", "effort"),
            ("targetRuntime", "targetRuntime"),
            ("target_runtime", "targetRuntime"),
        ):
            value = payload.get(source_key)
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    if (
                        target_key == "executionProfileRef"
                        and source_key in profile_ref_source_keys
                        and workflow.patched(RUNTIME_SELECTION_PROFILE_CLEAR_PATCH_ID)
                    ):
                        update_payload[target_key] = ""
                    continue
            update_payload[target_key] = value
        parameters_patch = payload.get("parametersPatch") or payload.get(
            "parameters_patch"
        )
        if isinstance(parameters_patch, Mapping):
            update_payload["parametersPatch"] = dict(parameters_patch)
        profile_selector = payload.get("profileSelector") or payload.get(
            "profile_selector"
        )
        if isinstance(profile_selector, Mapping):
            update_payload["profileSelector"] = dict(profile_selector)
        if update_payload:
            self._pending_runtime_selection_update = update_payload
            self.runtime_selection_updated_event.set()

    @workflow.signal
    def operator_message(self, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        message = str(
            payload.get("message")
            or payload.get("clarification_response")
            or payload.get("clarificationResponse")
            or ""
        ).strip()
        if message:
            self._pending_operator_messages.append(message)

    @staticmethod
    def _mapping_copy(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _drop_runtime_profile_fields(runtime_payload: dict[str, Any]) -> None:
        for key in (
            "executionProfileRef",
            "execution_profile_ref",
            "profileId",
            "profile_id",
            "providerProfile",
            "provider_profile",
        ):
            runtime_payload.pop(key, None)

    @staticmethod
    def _drop_runtime_profile_selector_fields(runtime_payload: dict[str, Any]) -> None:
        for key in ("profileSelector", "profile_selector"):
            runtime_payload.pop(key, None)

    @staticmethod
    def _drop_runtime_model_fields(runtime_payload: dict[str, Any]) -> None:
        for key in (
            "model",
            "requestedModel",
            "requested_model",
            "resolvedModel",
            "resolved_model",
            "modelTier",
            "model_tier",
            "tierFallback",
            "tier_fallback",
            "tierPreview",
            "modelTierResolution",
            "modelSource",
        ):
            runtime_payload.pop(key, None)

    @staticmethod
    def _drop_runtime_effort_fields(runtime_payload: dict[str, Any]) -> None:
        for key in ("effort", "requestedEffort", "requested_effort"):
            runtime_payload.pop(key, None)

    @staticmethod
    def _has_runtime_profile_field(runtime_payload: Mapping[str, Any]) -> bool:
        return any(
            key in runtime_payload
            for key in (
                "executionProfileRef",
                "execution_profile_ref",
                "profileId",
                "profile_id",
                "providerProfile",
                "provider_profile",
            )
        )

    def _has_profile_selection_update(
        self,
        *,
        payload: Mapping[str, Any],
        parameters_patch: Any,
    ) -> bool:
        if self._has_runtime_profile_field(payload):
            return True
        if not isinstance(parameters_patch, Mapping):
            return False
        if self._has_runtime_profile_field(parameters_patch):
            return True
        for key in ("task", "authoredTaskInput", "workflow"):
            nested_payload = parameters_patch.get(key)
            if not isinstance(nested_payload, Mapping):
                continue
            runtime_payload = nested_payload.get("runtime")
            if isinstance(runtime_payload, Mapping) and self._has_runtime_profile_field(
                runtime_payload
            ):
                return True
        return False

    def _profile_ref_targets_different_runtime(
        self,
        *,
        profile_id: str,
        runtime_id: str,
    ) -> bool:
        snapshots = self._profile_snapshots
        if not isinstance(snapshots, Mapping):
            return False
        snapshot = snapshots.get(profile_id)
        if not isinstance(snapshot, Mapping):
            return False
        snapshot_runtime_id = str(snapshot.get("runtime_id") or "").strip()
        if not snapshot_runtime_id:
            return False
        return self._managed_runtime_id(snapshot_runtime_id) != self._managed_runtime_id(
            runtime_id
        )

    def _clear_runtime_profile_selection(
        self,
        request: AgentExecutionRequest,
    ) -> None:
        request.execution_profile_ref = None
        params = dict(request.parameters or {})
        self._drop_runtime_profile_fields(params)
        for key in ("task", "authoredTaskInput", "workflow"):
            payload = self._mapping_copy(params.get(key))
            runtime_payload = self._mapping_copy(payload.get("runtime"))
            if runtime_payload:
                self._drop_runtime_profile_fields(runtime_payload)
                payload["runtime"] = runtime_payload
                params[key] = payload
        request.parameters = params

    def _apply_runtime_selection_update(
        self,
        request: AgentExecutionRequest,
        payload: Mapping[str, Any],
        *,
        refresh_derived_selection: bool = False,
    ) -> None:
        previous_runtime_id = self._managed_runtime_id(request.agent_id)
        params = dict(request.parameters or {})
        parameters_patch = payload.get("parametersPatch")
        editable_runtime_payload_keys = {"task", "authoredTaskInput"}
        if refresh_derived_selection:
            editable_runtime_payload_keys.add("authoredWorkflowInput")
        if isinstance(parameters_patch, Mapping):
            for key, value in parameters_patch.items():
                if key in editable_runtime_payload_keys and isinstance(
                    value, Mapping
                ):
                    existing_payload = self._mapping_copy(params.get(key))
                    runtime_patch = value.get("runtime")
                    if isinstance(runtime_patch, Mapping):
                        existing_runtime = self._mapping_copy(
                            existing_payload.get("runtime")
                        )
                        existing_runtime.update(dict(runtime_patch))
                        existing_payload.update(dict(value))
                        existing_payload["runtime"] = existing_runtime
                    else:
                        existing_payload.update(dict(value))
                    params[key] = existing_payload
                else:
                    params[key] = value

        task_payload = self._mapping_copy(params.get("task"))
        task_runtime = self._mapping_copy(task_payload.get("runtime"))
        authored_payload = self._mapping_copy(params.get("authoredTaskInput"))
        authored_runtime = self._mapping_copy(authored_payload.get("runtime"))
        authored_workflow_payload = (
            self._mapping_copy(params.get("authoredWorkflowInput"))
            if refresh_derived_selection
            else {}
        )
        authored_workflow_runtime = self._mapping_copy(
            authored_workflow_payload.get("runtime")
        )
        workflow_payload = self._mapping_copy(params.get("workflow"))
        workflow_runtime = self._mapping_copy(workflow_payload.get("runtime"))

        previous_profile_ref = request.execution_profile_ref
        previous_selector_payload = request.profile_selector.model_dump(
            by_alias=True,
            exclude_none=True,
        )

        profile_id = str(payload.get("executionProfileRef") or "").strip()
        explicit_profile_selection_update = self._has_profile_selection_update(
            payload=payload,
            parameters_patch=parameters_patch,
        )
        if profile_id.lower() == "auto":
            profile_id = ""

        model = str(payload.get("model") or "").strip()
        if model:
            params["model"] = model
            params["requestedModel"] = model
            task_runtime["model"] = model
            authored_runtime["model"] = model
            authored_workflow_runtime["model"] = model
            workflow_runtime["model"] = model

        effort = str(payload.get("effort") or "").strip()
        if effort:
            params["effort"] = effort
            task_runtime["effort"] = effort
            authored_runtime["effort"] = effort
            authored_workflow_runtime["effort"] = effort
            workflow_runtime["effort"] = effort

        target_runtime = str(payload.get("targetRuntime") or "").strip()
        if target_runtime:
            request.agent_id = target_runtime
            params["targetRuntime"] = target_runtime
            task_runtime["mode"] = target_runtime
            authored_runtime["mode"] = target_runtime
            authored_workflow_runtime["mode"] = target_runtime
            workflow_runtime["mode"] = target_runtime
        current_runtime_id = self._managed_runtime_id(request.agent_id)
        runtime_changed = current_runtime_id != previous_runtime_id

        if (
            profile_id
            and runtime_changed
            and self._profile_ref_targets_different_runtime(
                profile_id=profile_id,
                runtime_id=current_runtime_id,
            )
        ):
            profile_id = ""

        if profile_id:
            request.execution_profile_ref = profile_id
            params["profileId"] = profile_id
            task_runtime["profileId"] = profile_id
            authored_runtime["profileId"] = profile_id
            authored_workflow_runtime["profileId"] = profile_id
            workflow_runtime["profileId"] = profile_id
        elif runtime_changed:
            request.execution_profile_ref = None
            self._drop_runtime_profile_fields(params)
            self._drop_runtime_profile_fields(task_runtime)
            self._drop_runtime_profile_fields(authored_runtime)
            self._drop_runtime_profile_fields(authored_workflow_runtime)
            self._drop_runtime_profile_fields(workflow_runtime)
        elif explicit_profile_selection_update:
            request.execution_profile_ref = None
            self._drop_runtime_profile_fields(params)
            self._drop_runtime_profile_fields(task_runtime)
            self._drop_runtime_profile_fields(authored_runtime)
            self._drop_runtime_profile_fields(authored_workflow_runtime)
            self._drop_runtime_profile_fields(workflow_runtime)

        profile_selector_payload = payload.get("profileSelector")
        if not isinstance(profile_selector_payload, Mapping) and not runtime_changed:
            if isinstance(parameters_patch, Mapping):
                candidate_selector = parameters_patch.get("profileSelector")
                if not isinstance(candidate_selector, Mapping):
                    candidate_selector = parameters_patch.get("profile_selector")
                if isinstance(candidate_selector, Mapping):
                    profile_selector_payload = candidate_selector
        if not isinstance(profile_selector_payload, Mapping) and not runtime_changed:
            task_patch = (
                parameters_patch.get("task")
                if isinstance(parameters_patch, Mapping)
                else None
            )
            task_runtime_patch = (
                task_patch.get("runtime") if isinstance(task_patch, Mapping) else None
            )
            if isinstance(task_runtime_patch, Mapping):
                task_selector = task_runtime_patch.get("profileSelector")
                if not isinstance(task_selector, Mapping):
                    task_selector = task_runtime_patch.get("profile_selector")
                if isinstance(task_selector, Mapping):
                    profile_selector_payload = task_selector
        if not isinstance(profile_selector_payload, Mapping) and not runtime_changed:
            authored_patch = (
                parameters_patch.get("authoredTaskInput")
                if isinstance(parameters_patch, Mapping)
                else None
            )
            authored_runtime_patch = (
                authored_patch.get("runtime")
                if isinstance(authored_patch, Mapping)
                else None
            )
            if isinstance(authored_runtime_patch, Mapping):
                authored_selector = authored_runtime_patch.get("profileSelector")
                if not isinstance(authored_selector, Mapping):
                    authored_selector = authored_runtime_patch.get("profile_selector")
                if isinstance(authored_selector, Mapping):
                    profile_selector_payload = authored_selector

        if isinstance(profile_selector_payload, Mapping):
            request.profile_selector = ProfileSelector.model_validate(
                dict(profile_selector_payload)
            )
            selector_params = request.profile_selector.model_dump(
                by_alias=True,
                exclude_none=True,
            )
            params["profileSelector"] = selector_params
            task_runtime["profileSelector"] = selector_params
            authored_runtime["profileSelector"] = selector_params
            authored_workflow_runtime["profileSelector"] = selector_params
            workflow_runtime["profileSelector"] = selector_params
        elif profile_id:
            request.profile_selector = ProfileSelector()
            params.pop("profileSelector", None)
            params.pop("profile_selector", None)
            task_runtime.pop("profileSelector", None)
            task_runtime.pop("profile_selector", None)
            authored_runtime.pop("profileSelector", None)
            authored_runtime.pop("profile_selector", None)
            authored_workflow_runtime.pop("profileSelector", None)
            authored_workflow_runtime.pop("profile_selector", None)
            workflow_runtime.pop("profileSelector", None)
            workflow_runtime.pop("profile_selector", None)
        elif runtime_changed or explicit_profile_selection_update:
            request.profile_selector = ProfileSelector()
            self._drop_runtime_profile_selector_fields(params)
            self._drop_runtime_profile_selector_fields(task_runtime)
            self._drop_runtime_profile_selector_fields(authored_runtime)
            self._drop_runtime_profile_selector_fields(authored_workflow_runtime)
            self._drop_runtime_profile_selector_fields(workflow_runtime)

        runtime_or_profile_changed = False
        if refresh_derived_selection:
            current_selector_payload = request.profile_selector.model_dump(
                by_alias=True,
                exclude_none=True,
            )
            profile_selection_changed = (
                request.execution_profile_ref != previous_profile_ref
                or current_selector_payload != previous_selector_payload
            )
            runtime_or_profile_changed = runtime_changed or profile_selection_changed
            requested_model_tier = None
            if isinstance(parameters_patch, Mapping):
                tier_sources: list[Mapping[str, Any]] = [parameters_patch]
                runtime_patch = parameters_patch.get("runtime")
                if isinstance(runtime_patch, Mapping):
                    tier_sources.append(runtime_patch)
                for container_key in (
                    "task",
                    "authoredTaskInput",
                    "authoredWorkflowInput",
                    "workflow",
                ):
                    container_patch = parameters_patch.get(container_key)
                    nested_runtime_patch = (
                        container_patch.get("runtime")
                        if isinstance(container_patch, Mapping)
                        else None
                    )
                    if isinstance(nested_runtime_patch, Mapping):
                        tier_sources.append(nested_runtime_patch)
                for tier_source in tier_sources:
                    for tier_key in ("modelTier", "model_tier"):
                        tier_value = tier_source.get(tier_key)
                        if tier_value not in (None, ""):
                            requested_model_tier = tier_value
            if (
                runtime_or_profile_changed
                and not model
                and requested_model_tier is None
            ):
                for runtime_payload in (
                    params,
                    task_runtime,
                    authored_runtime,
                    authored_workflow_runtime,
                    workflow_runtime,
                ):
                    self._drop_runtime_model_fields(runtime_payload)
            if runtime_or_profile_changed and not effort:
                for runtime_payload in (
                    params,
                    task_runtime,
                    authored_runtime,
                    authored_workflow_runtime,
                    workflow_runtime,
                ):
                    self._drop_runtime_effort_fields(runtime_payload)

        if task_runtime or "runtime" in task_payload:
            task_payload["runtime"] = task_runtime
            params["task"] = task_payload
        if authored_runtime or "runtime" in authored_payload:
            authored_payload["runtime"] = authored_runtime
            params["authoredTaskInput"] = authored_payload
        if refresh_derived_selection and (
            authored_workflow_runtime or "runtime" in authored_workflow_payload
        ):
            authored_workflow_payload["runtime"] = authored_workflow_runtime
            params["authoredWorkflowInput"] = authored_workflow_payload
        if workflow_runtime or "runtime" in workflow_payload:
            workflow_payload["runtime"] = workflow_runtime
            params["workflow"] = workflow_payload
        request.parameters = params

        if refresh_derived_selection and request.step_execution is not None:
            runtime_selection = dict(request.step_execution.runtime_selection or {})
            runtime_selection["runtimeId"] = request.agent_id
            if request.execution_profile_ref:
                runtime_selection["executionProfileRef"] = (
                    request.execution_profile_ref
                )
            else:
                runtime_selection.pop("executionProfileRef", None)
            if model:
                runtime_selection["model"] = model
            elif runtime_or_profile_changed:
                runtime_selection.pop("model", None)
            if effort:
                runtime_selection["effort"] = effort
            elif runtime_or_profile_changed:
                runtime_selection.pop("effort", None)
            request.step_execution.runtime_selection = runtime_selection

    def _synchronize_runtime_selection_authority(
        self,
        request: AgentExecutionRequest,
    ) -> None:
        """Keep session and step runtime evidence aligned with the launch request.

        Runtime-selection edits are accepted while an AgentRun waits for a
        provider slot. A retried step can already carry the workflow-scoped
        managed-session binding from its previous execution, so changing the
        runtime or credential profile must detach an incompatible binding before
        any activity re-validates the request. The adapter-visible Step Execution
        projection is updated at the same boundary so it never claims the old
        runtime, profile, model, or effort after the executable request has
        changed.
        """

        runtime_id = self._managed_runtime_id(request.agent_id)
        managed_session_runtime_id = canonical_managed_session_runtime_id(
            request.agent_id
        )
        selected_profile_id = str(request.execution_profile_ref or "").strip()
        bound_profile_id = str(
            (
                request.managed_session.execution_profile_ref
                if request.managed_session is not None
                else ""
            )
            or ""
        ).strip()
        detached_managed_session = False
        if (
            request.managed_session is not None
            and (
                request.managed_session.runtime_id != managed_session_runtime_id
                or (
                    selected_profile_id
                    and bound_profile_id
                    and bound_profile_id != selected_profile_id
                )
            )
        ):
            request.managed_session = None
            detached_managed_session = True
            self._managed_session_detached_for_runtime_selection = True

        step_execution = request.step_execution
        if step_execution is not None:
            runtime_selection = dict(step_execution.runtime_selection or {})
            runtime_selection["runtimeId"] = runtime_id
            runtime_selection["agentKind"] = request.agent_kind

            parameters = (
                request.parameters
                if isinstance(request.parameters, Mapping)
                else {}
            )
            for key, parameter_keys in (
                ("model", ("model", "requestedModel")),
                ("effort", ("effort",)),
            ):
                value = next(
                    (
                        parameters.get(parameter_key)
                        for parameter_key in parameter_keys
                        if parameters.get(parameter_key) is not None
                    ),
                    None,
                )
                if value is None and any(
                    parameter_key in parameters
                    for parameter_key in parameter_keys
                ):
                    runtime_selection.pop(key, None)
                elif value is not None:
                    runtime_selection[key] = value
            if request.execution_profile_ref:
                runtime_selection["executionProfileRef"] = (
                    request.execution_profile_ref
                )
            else:
                runtime_selection.pop("executionProfileRef", None)

            step_update: dict[str, Any] = {
                "runtime_selection": runtime_selection,
            }
            if detached_managed_session:
                step_update["runtime_session_reset"] = None
            request.step_execution = step_execution.model_copy(update=step_update)

        # Assignment validation is intentionally not enabled on the canonical
        # Pydantic model. Re-validate the complete payload here, immediately
        # before it can cross an Activity boundary, to catch any future partial
        # runtime-selection mutation at its orchestration source.
        try:
            AgentExecutionRequest.model_validate(
                request.model_dump(mode="json", by_alias=True)
            )
        except ValidationError as exc:
            issue_summary = ", ".join(
                f"{'.'.join(str(part) for part in issue['loc'])}:{issue['type']}"
                for issue in exc.errors(include_input=False)
            )
            raise ApplicationError(
                "Runtime selection produced an invalid AgentExecutionRequest "
                f"({exc.error_count()} validation error(s): {issue_summary}).",
                type="InvalidRuntimeSelection",
                non_retryable=True,
            ) from exc

    def _validate_synced_profile_selection(
        self,
        *,
        profile_count: int,
        runtime_id: str,
        request: AgentExecutionRequest,
        clear_invalid_profile_for_runtime_change: bool = False,
    ) -> None:
        if profile_count == 0:
            intent = self._provider_slot_intent_summary(request)
            raise ApplicationError(
                "No launch-ready provider profiles found; "
                f"runtime={runtime_id}; {intent}; "
                "missing_condition=setup_required_or_policy.",
                type="ProfileResolutionError",
                non_retryable=True,
            )
        if request.execution_profile_ref:
            requested_profile_id = str(request.execution_profile_ref).strip()
            if (
                requested_profile_id
                and self._profile_snapshots
                and requested_profile_id not in self._profile_snapshots
            ):
                if clear_invalid_profile_for_runtime_change:
                    self._clear_runtime_profile_selection(request)
                    return
                intent = self._provider_slot_intent_summary(request)
                raise ApplicationError(
                    "Requested provider profile is not launch-ready; "
                    f"runtime={runtime_id}; {intent}; "
                    "missing_condition=setup_required_or_policy.",
                    type="ProfileResolutionError",
                    non_retryable=True,
                )

    async def _consume_runtime_selection_update_for_slot_wait(
        self,
        *,
        request: AgentExecutionRequest,
        manager_id: str,
        runtime_id: str,
    ) -> tuple[workflow.ExternalWorkflowHandle, str, str]:
        payload = self._pending_runtime_selection_update
        self._pending_runtime_selection_update = None
        self.runtime_selection_updated_event.clear()
        previous_manager_id = manager_id
        previous_profile_ref = request.execution_profile_ref
        previous_selector_payload = request.profile_selector.model_dump(
            by_alias=True,
            exclude_none=True,
        )
        assigned_profile_id = self._assigned_profile_id
        if payload:
            self._apply_runtime_selection_update(
                request,
                payload,
                refresh_derived_selection=workflow.patched(
                    AWAITING_SLOT_RUNTIME_PROFILE_EDIT_PATCH_ID
                ),
            )
            if workflow.patched(RUNTIME_SELECTION_SESSION_REBIND_PATCH_ID):
                self._synchronize_runtime_selection_authority(request)
        runtime_id = self._managed_runtime_id(request.agent_id)
        manager_id = self._manager_workflow_id(runtime_id)
        selector_payload = request.profile_selector.model_dump(
            by_alias=True,
            exclude_none=True,
        )
        slot_selection_changed = (
            request.execution_profile_ref != previous_profile_ref
            or selector_payload != previous_selector_payload
        )
        if manager_id != previous_manager_id or slot_selection_changed:
            previous_manager_handle = workflow.get_external_workflow_handle(
                previous_manager_id
            )
            try:
                await previous_manager_handle.signal(
                    "release_slot",
                    self._release_slot_payload(
                        profile_id=assigned_profile_id,
                        request=request,
                    ),
                )
            except ApplicationError as exc:
                if "ExternalWorkflowExecutionNotFound" not in (
                    getattr(exc, "type", None) or str(exc)
                ):
                    raise
            self.slot_assigned_event.clear()
            self._assigned_profile_id = None
        else:
            manager_handle = workflow.get_external_workflow_handle(manager_id)
            profile_count = await self._sync_manager_profiles(
                manager_id=manager_id,
                manager_handle=manager_handle,
                runtime_id=runtime_id,
            )
            self._validate_synced_profile_selection(
                profile_count=profile_count,
                runtime_id=runtime_id,
                request=request,
            )
            return manager_handle, manager_id, runtime_id
        manager_handle = await self._ensure_manager_started(manager_id, runtime_id)
        profile_count = await self._sync_manager_profiles(
            manager_id=manager_id,
            manager_handle=manager_handle,
            runtime_id=runtime_id,
        )
        self._validate_synced_profile_selection(
            profile_count=profile_count,
            runtime_id=runtime_id,
            request=request,
            clear_invalid_profile_for_runtime_change=(
                manager_id != previous_manager_id
            ),
        )
        manager_handle = await self._ensure_manager_and_signal(
            manager_id,
            runtime_id,
            request_slot=True,
            execution_profile_ref=request.execution_profile_ref,
            profile_selector=selector_payload,
            request_priority=self._request_priority(request),
            request_queue_metadata=self._request_queue_metadata(request),
        )
        self._awaiting_slot_reason_override = None
        self._slot_wait_timeout_override_seconds = None
        return manager_handle, manager_id, runtime_id

    @staticmethod
    def _normalize_external_status(
        *,
        normalized_status: str | None,
        raw_status: object,
        provider_status: str | None,
    ) -> str:
        for candidate in (normalized_status, raw_status, provider_status):
            token = str(candidate or "").strip().lower()
            if not token:
                continue
            mapped = _EXTERNAL_STATUS_TO_RUN_STATUS.get(token)
            if mapped is not None:
                return mapped
        return RunStatus.awaiting_callback

    def _coerce_external_status_payload(
        self,
        *,
        status_payload: object,
        fallback_agent_id: str,
    ) -> AgentRunStatusModel:
        if isinstance(status_payload, AgentRunStatusModel):
            return status_payload

        payload: dict
        if isinstance(status_payload, dict):
            payload = status_payload
        else:
            payload = {
                "external_id": getattr(status_payload, "external_id", None),
                "externalId": getattr(status_payload, "externalId", None),
                "run_id": getattr(status_payload, "run_id", None),
                "runId": getattr(status_payload, "runId", None),
                "status": getattr(status_payload, "status", None),
                "normalized_status": getattr(status_payload, "normalized_status", None),
                "normalizedStatus": getattr(status_payload, "normalizedStatus", None),
                "provider_status": getattr(status_payload, "provider_status", None),
                "providerStatus": getattr(status_payload, "providerStatus", None),
                "url": getattr(status_payload, "url", None),
                "external_url": getattr(status_payload, "external_url", None),
                "externalUrl": getattr(status_payload, "externalUrl", None),
                "tracking_ref": getattr(status_payload, "tracking_ref", None),
                "trackingRef": getattr(status_payload, "trackingRef", None),
                "terminal": getattr(status_payload, "terminal", None),
                "agent_id": getattr(status_payload, "agent_id", None),
                "agentId": getattr(status_payload, "agentId", None),
                "metadata": getattr(status_payload, "metadata", None),
            }

        # Preferred path: canonical AgentRunStatus contract.
        try:
            return AgentRunStatusModel(**payload)
        except Exception:
            pass

        metadata = payload.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}

        external_id = str(
            payload.get("external_id")
            or payload.get("externalId")
            or payload.get("run_id")
            or payload.get("runId")
            or self.run_id
            or ""
        ).strip()
        if not external_id:
            raise ValueError("External status payload is missing external_id/run_id")

        normalized_status = str(
            payload.get("normalized_status")
            or payload.get("normalizedStatus")
            or metadata_dict.get("normalizedStatus")
            or metadata_dict.get("normalized_status")
            or ""
        ).strip().lower() or None
        provider_status = str(
            payload.get("provider_status")
            or payload.get("providerStatus")
            or metadata_dict.get("providerStatus")
            or metadata_dict.get("provider_status")
            or payload.get("status")
            or "unknown"
        ).strip()

        run_status = self._normalize_external_status(
            normalized_status=normalized_status,
            raw_status=payload.get("status"),
            provider_status=provider_status,
        )

        external_url = (
            payload.get("url")
            or payload.get("external_url")
            or payload.get("externalUrl")
            or metadata_dict.get("externalUrl")
            or metadata_dict.get("external_url")
        )
        tracking_ref = (
            payload.get("tracking_ref")
            or payload.get("trackingRef")
            or metadata_dict.get("trackingRef")
            or metadata_dict.get("tracking_ref")
        )
        terminal = bool(payload.get("terminal", run_status in _TERMINAL_RUN_STATUSES))

        agent_id = str(
            payload.get("agent_id")
            or payload.get("agentId")
            or self._external_agent_id
            or fallback_agent_id
        ).strip() or fallback_agent_id

        normalized_for_metadata = normalized_status or "unknown"
        result_metadata = {
            "providerStatus": provider_status,
            "normalizedStatus": normalized_for_metadata,
            "terminal": terminal,
        }
        if external_url is not None:
            result_metadata["externalUrl"] = external_url
        if tracking_ref is not None:
            result_metadata["trackingRef"] = tracking_ref

        return AgentRunStatusModel(
            runId=external_id,
            agentKind="external",
            agentId=agent_id,
            status=run_status,
            metadata=result_metadata,
        )

    @staticmethod
    def _coerce_external_start_status(
        handle_payload: dict[str, object],
    ) -> tuple[str, str]:
        """Map integration-start payload status fields into canonical run states."""
        normalized_token = str(
            handle_payload.get("normalized_status")
            or handle_payload.get("normalizedStatus")
            or ""
        ).strip().lower() or None
        run_status = MoonMindAgentRun._normalize_external_status(
            normalized_status=normalized_token,
            raw_status=handle_payload.get("status"),
            provider_status=str(
                handle_payload.get("provider_status")
                or handle_payload.get("providerStatus")
                or ""
            ).strip() or None,
        )
        valid_statuses = {
            "queued",
            "launching",
            "running",
            "awaiting_callback",
            "awaiting_feedback",
            "awaiting_approval",
            "intervention_requested",
            "collecting_results",
            "completed",
            "failed",
            "canceled",
            "timed_out",
        }
        if run_status not in valid_statuses:
            raise ValueError(f"Unsupported run status from integration start: {run_status!r}")
        return run_status, (normalized_token or run_status)

    @staticmethod
    def _coerce_managed_status_payload(
        *,
        status_payload: object,
        run_id: str,
        fallback_agent_id: str,
    ) -> AgentRunStatusModel:
        if isinstance(status_payload, AgentRunStatusModel):
            return status_payload
        if isinstance(status_payload, dict):
            return AgentRunStatusModel(**status_payload)

        payload = {
            "runId": getattr(status_payload, "run_id", None)
            or getattr(status_payload, "runId", None)
            or run_id,
            "agentKind": "managed",
            "agentId": getattr(status_payload, "agent_id", None)
            or getattr(status_payload, "agentId", None)
            or fallback_agent_id,
            "status": getattr(status_payload, "status", None) or "running",
            "metadata": getattr(status_payload, "metadata", None) or {},
        }
        return AgentRunStatusModel(**payload)

    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        self.agent_kind = request.agent_kind

        timeout_seconds = (
            DEFAULT_EXTERNAL_TIMEOUT_SECONDS
            if request.agent_kind == "external"
            else DEFAULT_MANAGED_TIMEOUT_SECONDS
        )
        if request.timeout_policy and "timeout_seconds" in request.timeout_policy:
            timeout_seconds = request.timeout_policy["timeout_seconds"]

        # Loop for handling 429 cooldown & profile swaps safely within the timeout boundary
        overall_start = workflow.now()
        use_managed_status_activity = workflow.patched(
            MANAGED_STATUS_ACTIVITY_PATCH_ID
        )
        requested_execution_profile_ref = request.execution_profile_ref
        resiliency_policy: Mapping[str, Any] = {}
        if workflow.patched(AGENT_RUN_RESILIENCY_POLICY_PATCH_ID):
            use_extended_claude_no_progress_window = True
            if (
                request.agent_kind == "managed"
                and self._managed_runtime_id(request.agent_id) == "claude_code"
            ):
                use_extended_claude_no_progress_window = workflow.patched(
                    AGENT_RUN_CLAUDE_NO_PROGRESS_POLICY_PATCH_ID
                )
            resiliency_policy = self._resiliency_policy_for_request(
                request,
                use_extended_claude_no_progress_window=(
                    use_extended_claude_no_progress_window
                ),
            )

        try:
            while True:
                skip_poll_and_fetch = False
                sticky_pinned_profile_on_cooldown_retry = workflow.patched(
                    STICKY_PINNED_PROFILE_COOLDOWN_RETRY_PATCH_ID
                )
                uses_codex_session_adapter = self._uses_codex_session_adapter(request)
                parent_info = workflow.info().parent
                elapsed = (workflow.now() - overall_start).total_seconds()
                if elapsed >= timeout_seconds:
                    self.run_status = RunStatus.timed_out
                    return await self._publish_terminal_result_with_compacted_replay_cleanup(
                        request=request,
                        result=self._timed_out_result(
                            request=request,
                            timeout_seconds=timeout_seconds,
                            elapsed_seconds=elapsed,
                            detail=(
                                "exceeded its execution budget before dispatching "
                                "a turn"
                            ),
                        ),
                    )

                manager_handle = None
                if self._paused:
                    await workflow.wait_condition(lambda: not self._paused)
                    overall_start = workflow.now()

                # Acquire provider-profile slot if managed
                if request.agent_kind == "managed":
                    runtime_id = self._managed_runtime_id(request.agent_id)
                    manager_id = self._manager_workflow_id(runtime_id)

                    self.slot_assigned_event.clear()
                    selector_payload = request.profile_selector.model_dump(
                        by_alias=True,
                        exclude_none=True,
                    )
                    if (
                        not sticky_pinned_profile_on_cooldown_retry
                        and self._skip_default_profile_pin_once
                        and not request.execution_profile_ref
                        and not self._profile_selector_has_constraints(
                            request.profile_selector
                        )
                    ):
                        selector_payload["allowDefaultFallback"] = True
                    if workflow.patched(SYNC_PROFILES_BEFORE_SLOT_REQUEST_PATCH_ID):
                        manager_handle = await self._ensure_manager_started(
                            manager_id,
                            runtime_id,
                        )
                        profile_count = await self._sync_manager_profiles(
                            manager_id=manager_id,
                            manager_handle=manager_handle,
                            runtime_id=runtime_id,
                        )
                        if workflow.patched(
                            PIN_PROVIDER_PROFILE_BEFORE_SLOT_REQUEST_PATCH_ID
                        ) and (
                            sticky_pinned_profile_on_cooldown_retry
                            or not self._skip_default_profile_pin_once
                        ):
                            pinned_profile_id = self._default_execution_profile_ref(
                                request
                            )
                            if pinned_profile_id:
                                request.execution_profile_ref = pinned_profile_id
                                if (
                                    sticky_pinned_profile_on_cooldown_retry
                                    and requested_execution_profile_ref is None
                                ):
                                    requested_execution_profile_ref = pinned_profile_id
                        self._skip_default_profile_pin_once = False
                        manager_handle = await self._ensure_manager_and_signal(
                            manager_id,
                            runtime_id,
                            request_slot=True,
                            execution_profile_ref=request.execution_profile_ref,
                            profile_selector=selector_payload,
                            request_priority=self._request_priority(request),
                            request_queue_metadata=self._request_queue_metadata(request),
                        )
                    else:
                        manager_handle = await self._ensure_manager_and_signal(
                            manager_id,
                            runtime_id,
                            request_slot=True,
                            execution_profile_ref=request.execution_profile_ref,
                            profile_selector=selector_payload,
                            request_priority=self._request_priority(request),
                            request_queue_metadata=self._request_queue_metadata(request),
                        )
                        profile_count = await self._sync_manager_profiles(
                            manager_id=manager_id,
                            manager_handle=manager_handle,
                            runtime_id=runtime_id,
                        )
                    self._validate_synced_profile_selection(
                        profile_count=profile_count,
                        runtime_id=runtime_id,
                        request=request,
                    )

                    # Wait for a provider-profile slot.
                    # Awaiting time does not count against the execution timeout;
                    # overall_start is reset once the slot is acquired.
                    # If the manager appears unavailable, bounded recovery
                    # ensures/re-requests it without terminating lease authority.
                    #
                    # NOTE: The slot_assigned signal may have arrived during
                    # _sync_manager_profiles (the activity gives the manager
                    # time to process request_slot).  Only notify the parent
                    # of awaiting_slot when we actually need to wait — otherwise
                    # the parent sees awaiting_slot→launching in the same
                    # workflow task and the "queued" state is never visible.
                    if not self.slot_assigned_event.is_set():
                        waiting_reason = (
                            self._awaiting_slot_reason_override
                            or self._build_provider_slot_waiting_reason(
                                runtime_id=runtime_id,
                                request=request,
                            )
                        )
                        if (
                            self._awaiting_slot_reason_override is None
                            and workflow.patched(ACCURATE_SLOT_WAIT_REASON_PATCH_ID)
                        ):
                            waiting_reason = (
                                await self._inspected_provider_slot_waiting_reason(
                                    manager_id=manager_id,
                                    runtime_id=runtime_id,
                                    request=request,
                                )
                            )
                        self._awaiting_slot_reason_override = None
                        # The inspection activity creates a workflow-task boundary;
                        # the manager can assign the slot while it runs.
                        if (
                            not self.slot_assigned_event.is_set()
                            and not self.runtime_selection_updated_event.is_set()
                        ):
                            self.run_status = RunStatus.awaiting_slot
                            await self._signal_parent_child_state_changed(
                                parent_info,
                                "awaiting_slot",
                                waiting_reason,
                            )

                    if workflow.patched("agent_run_slot_wait_retry_v1"):
                        slot_recovery_attempts = 0
                        refresh_waiting_reason = False
                        while (
                            not self.slot_assigned_event.is_set()
                            or self.runtime_selection_updated_event.is_set()
                        ):
                            try:
                                if (
                                    not self.slot_assigned_event.is_set()
                                    and refresh_waiting_reason
                                    and not self.runtime_selection_updated_event.is_set()
                                    and workflow.patched(
                                        ACCURATE_SLOT_WAIT_REASON_PATCH_ID
                                    )
                                ):
                                    waiting_reason = await self._inspected_provider_slot_waiting_reason(
                                        manager_id=manager_id,
                                        runtime_id=runtime_id,
                                        request=request,
                                    )
                                    if (
                                        not self.slot_assigned_event.is_set()
                                        and not self.runtime_selection_updated_event.is_set()
                                    ):
                                        self.run_status = RunStatus.awaiting_slot
                                        await self._signal_parent_child_state_changed(
                                            parent_info,
                                            "awaiting_slot",
                                            waiting_reason,
                                        )
                                        refresh_waiting_reason = False
                                if self.runtime_selection_updated_event.is_set():
                                    (
                                        manager_handle,
                                        manager_id,
                                        runtime_id,
                                    ) = await self._consume_runtime_selection_update_for_slot_wait(
                                        request=request,
                                        manager_id=manager_id,
                                        runtime_id=runtime_id,
                                    )
                                    requested_execution_profile_ref = (
                                        request.execution_profile_ref
                                    )
                                    refresh_waiting_reason = True
                                    continue
                                slot_wait_timeout_seconds = (
                                    self._slot_wait_timeout_override_seconds
                                    or _SLOT_WAIT_TIMEOUT_SECONDS
                                )
                                await workflow.wait_condition(
                                    lambda: self.slot_assigned_event.is_set()
                                    or self.runtime_selection_updated_event.is_set(),
                                    timeout=timedelta(seconds=slot_wait_timeout_seconds),
                                )
                                if self.runtime_selection_updated_event.is_set():
                                    (
                                        manager_handle,
                                        manager_id,
                                        runtime_id,
                                    ) = await self._consume_runtime_selection_update_for_slot_wait(
                                        request=request,
                                        manager_id=manager_id,
                                        runtime_id=runtime_id,
                                    )
                                    requested_execution_profile_ref = (
                                        request.execution_profile_ref
                                    )
                                    refresh_waiting_reason = True
                                    continue
                            except TimeoutError:
                                if (
                                    slot_recovery_attempts
                                    >= _SLOT_WAIT_MAX_RECOVERY_ATTEMPTS
                                ):
                                    raise ApplicationError(
                                        f"Auth profile slot not assigned after {_SLOT_WAIT_MAX_RECOVERY_ATTEMPTS} manager recovery attempts for runtime_id='{runtime_id}'",
                                        type="SlotAcquisitionTimeout",
                                        non_retryable=True,
                                    )
                                selector_payload = request.profile_selector.model_dump(
                                    by_alias=True,
                                    exclude_none=True,
                                )
                                if workflow.patched(MANAGER_SLOT_WAIT_INSPECTION_PATCH_ID):
                                    try:
                                        manager_state = await self._manager_state_for_slot_wait(
                                            runtime_id=runtime_id,
                                            requester_workflow_id=workflow.info().workflow_id,
                                        )
                                    except CancelledError:
                                        raise
                                    except Exception as exc:
                                        self._get_logger().warning(
                                            "Auth profile manager %s state inspection failed while %s waits for a slot; using non-destructive recovery: %s",
                                            manager_id,
                                            workflow.info().workflow_id,
                                            exc,
                                        )
                                        manager_state = {"running": False}
                                    if manager_state.get("running") is True:
                                        if manager_state.get("requester_pending") is True:
                                            self._get_logger().warning(
                                                "Auth profile manager %s is responsive while %s already waits in the pending queue; continuing without reset or duplicate request",
                                                manager_id,
                                                workflow.info().workflow_id,
                                            )
                                            manager_handle = workflow.get_external_workflow_handle(
                                                manager_id
                                            )
                                            await self._sync_manager_profiles(
                                                manager_id=manager_id,
                                                manager_handle=manager_handle,
                                                runtime_id=runtime_id,
                                            )
                                            continue
                                        self._get_logger().warning(
                                            "Auth profile manager %s is responsive while %s waits for a slot; re-requesting without reset",
                                            manager_id,
                                            workflow.info().workflow_id,
                                        )
                                        manager_handle = await self._ensure_manager_and_signal(
                                            manager_id,
                                            runtime_id,
                                            request_slot=True,
                                            execution_profile_ref=request.execution_profile_ref,
                                            profile_selector=selector_payload,
                                            request_priority=self._request_priority(request),
                                            request_queue_metadata=self._request_queue_metadata(request),
                                        )
                                        await self._sync_manager_profiles(
                                            manager_id=manager_id,
                                            manager_handle=manager_handle,
                                            runtime_id=runtime_id,
                                        )
                                        continue
                                self.slot_assigned_event.clear()
                                if workflow.patched(
                                    NON_DESTRUCTIVE_SLOT_WAIT_RECOVERY_PATCH_ID
                                ):
                                    manager_handle = (
                                        await self._recover_and_request_slot(
                                            manager_id,
                                            runtime_id,
                                            execution_profile_ref=request.execution_profile_ref,
                                            profile_selector=selector_payload,
                                            request_priority=self._request_priority(request),
                                            request_queue_metadata=self._request_queue_metadata(request),
                                        )
                                    )
                                else:
                                    # Replay compatibility for histories that
                                    # already scheduled the legacy activity.
                                    manager_handle = await self._reset_and_request_slot(
                                        manager_id,
                                        runtime_id,
                                        execution_profile_ref=request.execution_profile_ref,
                                        profile_selector=selector_payload,
                                        request_priority=self._request_priority(request),
                                        request_queue_metadata=self._request_queue_metadata(request),
                                    )
                                await self._sync_manager_profiles(
                                    manager_id=manager_id,
                                    manager_handle=manager_handle,
                                    runtime_id=runtime_id,
                                )
                                slot_recovery_attempts += 1
                    else:
                        while not self.slot_assigned_event.is_set():
                            await workflow.wait_condition(
                                lambda: self.slot_assigned_event.is_set(),
                            )

                    self._awaiting_slot_reason_override = None
                    self._slot_wait_timeout_override_seconds = None

                    if self._paused:
                        await workflow.wait_condition(lambda: not self._paused)

                    # Reset the execution clock so the timeout budget starts
                    # from launch readiness, excluding slot and pause waits.
                    overall_start = workflow.now()

                    self.run_status = RunStatus.launching
                    if parent_info:
                        parent_handle = workflow.get_external_workflow_handle(
                            parent_info.workflow_id, run_id=parent_info.run_id
                        )
                        await parent_handle.signal(
                            "child_state_changed",
                            args=["launching", f"Slot acquired for {runtime_id}"]
                        )
                    request.execution_profile_ref = self._assigned_profile_id
                    if workflow.patched(
                        AWAITING_SLOT_RUNTIME_PROFILE_EDIT_PATCH_ID
                    ):
                        if workflow.patched(AGENT_RUN_RESILIENCY_POLICY_PATCH_ID):
                            use_extended_claude_no_progress_window = True
                            if (
                                self._managed_runtime_id(request.agent_id)
                                == "claude_code"
                            ):
                                use_extended_claude_no_progress_window = (
                                    workflow.patched(
                                        AGENT_RUN_CLAUDE_NO_PROGRESS_POLICY_PATCH_ID
                                    )
                                )
                            resiliency_policy = (
                                self._refresh_selection_after_slot_assignment(
                                    request,
                                    use_extended_claude_no_progress_window=(
                                        use_extended_claude_no_progress_window
                                    ),
                                )
                            )
                    if workflow.patched(
                        RUNTIME_SELECTION_SESSION_REBIND_PATCH_ID
                    ):
                        self._synchronize_runtime_selection_authority(request)

                    # Notify parent of the assigned profile so it can release the slot
                    # if this child exits in a terminal state (fallback for cancelled
                    # workflows that fail to release their own slot).
                    if parent_info and self._assigned_profile_id:
                        if workflow.patched("agent_run_parent_profile_assigned_signal"):
                            parent_handle = workflow.get_external_workflow_handle(
                                parent_info.workflow_id, run_id=parent_info.run_id
                            )
                            await parent_handle.signal(
                                "profile_assigned",
                                {
                                    "profile_id": self._assigned_profile_id,
                                    "child_workflow_id": workflow.info().workflow_id,
                                    "runtime_id": runtime_id,
                                },
                            )

                    request = await self._bind_deferred_workflow_scoped_session_after_slot(
                        request=request,
                        runtime_id=runtime_id,
                        parent_info=parent_info,
                    )
                    uses_codex_session_adapter = self._uses_codex_session_adapter(
                        request
                    )

                    # Wire ManagedAgentAdapter with real DI callables.
                    # The slot_requester / slot_releaser / cooldown_reporter
                    # are thin wrappers around ProviderProfileManager signals.
                    # The profile_fetcher dispatches to the provider_profile.list
                    # activity on the artifacts fleet.
                    wf_id = workflow.info().workflow_id
                    if parent_info and workflow.patched(MANAGED_TASK_WORKFLOW_BINDING_PATCH_ID):
                        task_workflow_id = parent_info.workflow_id
                    else:
                        task_workflow_id = wf_id

                    async def _profile_fetcher(**kw):
                        return await self._execute_routed_activity(
                            "provider_profile.list",
                            {"runtime_id": kw.get("runtime_id", runtime_id)},
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                    async def _slot_requester(**kw):
                        payload = {
                            "requester_workflow_id": wf_id,
                            "runtime_id": kw.get("runtime_id", runtime_id),
                            "priority": self._request_priority(request),
                            "purpose": "execution_direct",
                            "metadata": {
                                "workflowId": wf_id,
                                "ownerIsWorkflow": True,
                            },
                        }
                        payload.update(self._request_queue_metadata(request))
                        if workflow.patched(SLOT_HANDOFF_PATCH_ID):
                            lease_group_id = self._lease_group_id()
                            if lease_group_id:
                                payload["lease_group_id"] = lease_group_id
                        exact_profile_id = kw.get(
                            "execution_profile_ref", request.execution_profile_ref
                        )
                        if exact_profile_id:
                            payload["execution_profile_ref"] = exact_profile_id
                        if request.profile_selector:
                            payload["profile_selector"] = (
                                request.profile_selector.model_dump(
                                    by_alias=True,
                                    exclude_none=True,
                                )
                            )
                        await manager_handle.signal("request_slot", payload)

                    async def _slot_releaser(**kw):
                        await manager_handle.signal(
                            "release_slot",
                            self._release_slot_payload(
                                profile_id=kw.get(
                                    "profile_id",
                                    request.execution_profile_ref,
                                ),
                                request=request,
                            ),
                        )

                    async def _cooldown_reporter(**kw):
                        await manager_handle.signal("report_cooldown", {
                            "profile_id": kw.get("profile_id", request.execution_profile_ref),
                            "cooldown_seconds": kw.get("cooldown_seconds", 300),
                        })

                    async def _run_launcher(**kw):
                        return await self._execute_routed_activity(
                            "agent_runtime.launch",
                            {
                                **kw.get("payload", {}),
                                "workflow_id": task_workflow_id,
                            },
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                    async def _launch_context_builder(**kw):
                        return await self._execute_routed_activity(
                            "agent_runtime.build_launch_context",
                            kw,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                    run_store = ManagedRunStore(_MANAGED_RUN_STORE_ROOT)

                    if uses_codex_session_adapter:
                        use_prepare_turn_instructions_activity = workflow.patched(
                            MANAGED_SESSION_PREPARE_TURN_INSTRUCTIONS_ACTIVITY_PATCH_ID
                        )
                        defer_turn_instructions_until_session_launch = workflow.patched(
                            MANAGED_SESSION_DEFER_TURN_INSTRUCTIONS_UNTIL_LAUNCH_PATCH_ID
                        )
                        use_publish_bridge_events_activity = workflow.patched(
                            MANAGED_SESSION_BRIDGE_EVENTS_ACTIVITY_PATCH_ID
                        )
                        if request.managed_session is None:
                            raise ApplicationError(
                                "managedSession is required for Codex session-backed runs",
                                type="MissingManagedSession",
                                non_retryable=True,
                            )
                        session_workflow_id = request.managed_session.workflow_id
                        session_handle = workflow.get_external_workflow_handle(
                            session_workflow_id
                        )

                        async def _load_session_snapshot(workflow_id: str) -> dict[str, Any]:
                            session_snapshot_request = request.managed_session.model_dump(
                                mode="json",
                                by_alias=True,
                            )
                            session_snapshot_request["workflowId"] = workflow_id
                            return await self._execute_routed_activity(
                                "agent_runtime.load_session_snapshot",
                                session_snapshot_request,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _attach_runtime_handles(payload: dict[str, Any]) -> None:
                            await session_handle.signal("attach_runtime_handles", payload)

                        async def _apply_session_control_action(payload: dict[str, Any]) -> None:
                            signal_payload = {
                                key: value
                                for key, value in payload.items()
                                if key in {"containerId", "threadId", "activeTurnId", "sessionEpoch"}
                            }
                            action = payload.get("action")
                            if action is not None:
                                signal_payload["lastControlAction"] = action
                            reason = payload.get("reason")
                            if reason is not None:
                                signal_payload["lastControlReason"] = reason
                            await session_handle.signal(
                                "attach_runtime_handles",
                                signal_payload,
                            )

                        async def _launch_session(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.launch_session",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _session_status(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.session_status",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _prepare_turn_instructions(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.prepare_turn_instructions",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _send_turn(request_payload: Any) -> Any:
                            return await self._send_turn_within_budget(
                                request_payload,
                                timeout_seconds=timeout_seconds,
                                overall_start=overall_start,
                            )

                        async def _interrupt_turn(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.interrupt_turn",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _clear_session(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.clear_session",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _terminate_session(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.terminate_session",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _fetch_session_summary(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.fetch_session_summary",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _publish_session_artifacts(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.publish_session_artifacts",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        async def _publish_bridge_events(request_payload: Any) -> Any:
                            return await self._execute_routed_activity(
                                "agent_runtime.publish_bridge_events",
                                request_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                        adapter = CodexSessionAdapter(
                            profile_fetcher=_profile_fetcher,
                            slot_requester=_slot_requester,
                            slot_releaser=_slot_releaser,
                            cooldown_reporter=_cooldown_reporter,
                            workflow_id=wf_id,
                            runtime_id=runtime_id,
                            run_store=run_store,
                            load_session_snapshot=_load_session_snapshot,
                            launch_session=_launch_session,
                            session_status=_session_status,
                            prepare_turn_instructions=(
                                _prepare_turn_instructions
                                if use_prepare_turn_instructions_activity
                                else None
                            ),
                            send_turn=_send_turn,
                            interrupt_turn=_interrupt_turn,
                            clear_remote_session=_clear_session,
                            terminate_remote_session=_terminate_session,
                            fetch_remote_summary=_fetch_session_summary,
                            publish_remote_artifacts=_publish_session_artifacts,
                            publish_bridge_events=(
                                _publish_bridge_events
                                if use_publish_bridge_events_activity
                                else None
                            ),
                            attach_runtime_handles=_attach_runtime_handles,
                            apply_session_control_action=_apply_session_control_action,
                            workspace_root=_MANAGED_RUNTIME_STORE_ROOT,
                            session_image_ref=_DEFAULT_SESSION_IMAGE_REF,
                            task_workflow_id=task_workflow_id,
                            launch_context_builder=_launch_context_builder,
                            defer_turn_instructions_until_session_launch=(
                                defer_turn_instructions_until_session_launch
                            ),
                        )
                    else:
                        adapter = ManagedAgentAdapter(
                            profile_fetcher=_profile_fetcher,
                            slot_requester=_slot_requester,
                            slot_releaser=_slot_releaser,
                            cooldown_reporter=_cooldown_reporter,
                            workflow_id=wf_id,
                            runtime_id=runtime_id,
                            run_store=run_store,
                            run_launcher=_run_launcher,
                            launch_context_builder=_launch_context_builder,
                        )

                    # --- Managed agent: launch via adapter ---
                    try:
                        handle = await adapter.start(request)
                    except ProfileResolutionError as exc:
                        raise ApplicationError(
                            str(exc),
                            type="ProfileResolutionError",
                            non_retryable=True,
                        ) from exc
                    except RuntimeError as exc:
                        self._get_logger().warning(
                            "Managed agent start failed",
                            extra={
                                "agent_id": request.agent_id,
                                "workflow_id": workflow.info().workflow_id,
                                "error": str(exc),
                            },
                        )
                        self.run_status = RunStatus.failed
                        if (
                            isinstance(exc, CodexSessionRunFailedError)
                            and isinstance(exc.agent_run_result, AgentRunResult)
                        ):
                            self.final_result = exc.agent_run_result
                        else:
                            self.final_result = self._managed_start_failure_result(
                                request=request,
                                error=exc,
                            )
                        skip_poll_and_fetch = True
                        handle = None
                    if handle is not None:
                        self.run_id = handle.run_id
                        self.run_status = handle.status
                        poll_interval = handle.poll_hint_seconds or 10
                        if (
                            uses_codex_session_adapter
                            and handle.status in _TERMINAL_RUN_STATUSES
                        ):
                            self.final_result = await self._fetch_managed_result(
                                request=request,
                                adapter=adapter,
                                uses_codex_session_adapter=uses_codex_session_adapter,
                                use_managed_status_activity=use_managed_status_activity,
                            )
                            skip_poll_and_fetch = True

                elif request.agent_kind == "external":
                    # Validate adapter availability and resolve execution style.
                    adapter_meta = await self._execute_routed_activity(
                        "integration.resolve_adapter_metadata",
                        request.agent_id,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                    )
                    validated_id = adapter_meta["agent_id"]
                    execution_style = adapter_meta["execution_style"]
                    request = self._with_external_callback_ingress(
                        request,
                        integration_name=validated_id,
                        supports_callbacks=bool(
                            adapter_meta.get("supports_callbacks", False)
                        ),
                        callback_base_url=adapter_meta.get("callback_base_url"),
                    )
                    # Store the validated agent_id for activity routing.
                    self._external_agent_id = validated_id

                    if execution_style == "streaming_gateway":
                        stc_seconds = min(
                            max(int(timeout_seconds), 60),
                            86400,
                        )
                        act_name = f"integration.{validated_id}.execute"
                        if (
                            validated_id == "omnigent"
                            and request.execution_profile_ref
                            and workflow.patched(
                                OMNIGENT_PROFILE_BOUND_EXECUTION_PATCH_ID
                            )
                        ):
                            act_name = "integration.omnigent.profile_bound_execute"
                        result_payload = await self._execute_routed_activity(
                            act_name,
                            request,
                            start_to_close_timeout=timedelta(seconds=stc_seconds),
                            schedule_to_close_timeout=timedelta(seconds=stc_seconds),
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
                        act_name = f"integration.{validated_id}.start"
                        handle_dict = await self._execute_routed_activity(
                            act_name,
                            request,
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        )

                        if isinstance(handle_dict, dict) and "external_id" in handle_dict:
                            try:
                                status, normalized_for_metadata = self._coerce_external_start_status(
                                    handle_dict
                                )
                            except ValueError as exc:
                                self._get_logger().warning(str(exc))
                                raise ApplicationError(
                                    str(exc),
                                    type="UnsupportedStatus",
                                    non_retryable=True,
                                ) from exc
                            handle = AgentRunHandle(
                                runId=handle_dict["external_id"],
                                agentKind="external",
                                agentId=validated_id,
                                status=status,
                                startedAt=workflow.now(),
                                metadata={
                                    "providerStatus": handle_dict.get("provider_status", handle_dict.get("status", "unknown")),
                                    "normalizedStatus": normalized_for_metadata,
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
                    last_progress_signature: tuple[Any, ...] | None = None
                    stagnant_poll_count = 0
                    while True:
                        if (
                            request.agent_kind == "external"
                            and self._external_agent_id == "jules"
                            and self.run_id
                            and self._pending_operator_messages
                        ):
                            while self._pending_operator_messages:
                                operator_message = self._pending_operator_messages.pop(0)
                                await self._execute_routed_activity(
                                    "integration.jules.send_message",
                                    {
                                        "session_id": self.run_id,
                                        "prompt": operator_message,
                                    },
                                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                )
                            self.run_status = RunStatus.running

                        remaining_timeout = timeout_seconds - (workflow.now() - overall_start).total_seconds()
                        if remaining_timeout <= 0:
                            break

                        try:
                            # Add bounded wait
                            completed = await workflow.wait_condition(
                                lambda: self.completion_event.is_set(),
                                timeout=timedelta(seconds=min(poll_interval, remaining_timeout))
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            completed = False

                        if completed or self.completion_event.is_set():
                            break  # Callback received

                        if request.agent_kind == "external":
                            # Poll via Temporal activity (determinism-safe).
                            act_name = f"integration.{self._external_agent_id}.status"
                            status_dict = await self._execute_routed_activity(
                                act_name,
                                ExternalAgentRunInput(runId=str(self.run_id or "")),
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,

                            )
                            status_obj = self._coerce_external_status_payload(
                                status_payload=status_dict,
                                fallback_agent_id=request.agent_id,
                            )
                        else:
                            status_obj = await self._poll_managed_status(
                                request=request,
                                adapter=adapter,
                                uses_codex_session_adapter=uses_codex_session_adapter,
                                use_managed_status_activity=use_managed_status_activity,
                            )

                        self.run_status = status_obj.status
                        if workflow.patched(AGENT_RUN_RESILIENCY_POLICY_PATCH_ID):
                            progress_signature = self._status_progress_signature(
                                status_obj
                            )
                            if progress_signature == last_progress_signature:
                                stagnant_poll_count += 1
                            else:
                                last_progress_signature = progress_signature
                                stagnant_poll_count = 0
                            max_stagnant_polls = self._max_no_progress_polls(
                                policy=resiliency_policy,
                                poll_interval=poll_interval,
                            )
                            if stagnant_poll_count >= max_stagnant_polls:
                                if (
                                    request.agent_kind == "managed"
                                    and workflow.patched(
                                        AGENT_RUN_MANAGED_NO_PROGRESS_RECONCILIATION_PATCH_ID
                                    )
                                ):
                                    reconciled_result = (
                                        await self._reconcile_managed_no_progress(
                                            request=request,
                                            adapter=adapter,
                                            uses_codex_session_adapter=uses_codex_session_adapter,
                                            use_managed_status_activity=(
                                                use_managed_status_activity
                                            ),
                                            resiliency_policy=resiliency_policy,
                                            poll_interval=poll_interval,
                                            initial_status=status_obj,
                                            overall_start=overall_start,
                                            timeout_seconds=timeout_seconds,
                                        )
                                    )
                                    if reconciled_result is not None:
                                        self.final_result = reconciled_result
                                        break

                                self.run_status = "intervention_requested"
                                self.final_result = self._intervention_result(
                                    summary=(
                                        "Agent run made no observable progress "
                                        f"for {resiliency_policy.get('noProgressTimeoutSeconds', _DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS)}s; "
                                        "human intervention is required before retrying."
                                    ),
                                    request=request,
                                    metadata={
                                        "reason": "stuck_no_progress",
                                        "resiliencyPolicy": dict(resiliency_policy),
                                        "lastStatus": status_obj.model_dump(
                                            mode="json",
                                            by_alias=True,
                                        ),
                                    },
                                )
                                await self._signal_parent_child_state_changed(
                                    parent_info,
                                    "intervention_requested",
                                    (
                                        "Agent run made no observable progress "
                                        "and needs human review."
                                    ),
                                )
                                break

                        if (
                            workflow.patched(AGENT_RUN_RESILIENCY_POLICY_PATCH_ID)
                            and status_obj.status == RunStatus.awaiting_feedback
                            and not (
                                request.agent_kind == "external"
                                and self._external_agent_id == "jules"
                            )
                        ):
                            self.run_status = "intervention_requested"
                            self.final_result = self._intervention_result(
                                summary=(
                                    "Agent requested human feedback; "
                                    "operator intervention is required."
                                ),
                                request=request,
                                metadata={
                                    "reason": "agent_requested_feedback",
                                    "resiliencyPolicy": dict(resiliency_policy),
                                    "lastStatus": status_obj.model_dump(
                                        mode="json",
                                        by_alias=True,
                                    ),
                                },
                            )
                            await self._signal_parent_child_state_changed(
                                parent_info,
                                "intervention_requested",
                                "Agent requested human feedback.",
                            )
                            break

                        # --- Jules auto-answer sub-flow (spec 094) ---
                        # Only react when Jules explicitly signals
                        # awaiting_user_feedback (normalized to
                        # awaiting_feedback).  Probing during "running"
                        # caused every progress message to be treated as
                        # a question and spammed with auto-answers.
                        if (
                            getattr(status_obj, "status", None)
                            == RunStatus.awaiting_feedback
                            and request.agent_kind == "external"
                            and self._external_agent_id == "jules"
                        ):
                            # Probe for an unanswered question first (cheap GET).
                            activities_result = await self._execute_routed_activity(
                                "integration.jules.list_activities",
                                {"session_id": self.run_id},
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )

                            act_id = activities_result.get("activityId") if isinstance(activities_result, dict) else None
                            question = activities_result.get("latestAgentQuestion") if isinstance(activities_result, dict) else None

                            if question and act_id and act_id not in self._answered_activity_ids:
                                # New question detected — read config via activity (determinism-safe)
                                auto_answer_config = await self._execute_routed_activity(
                                    "integration.jules.get_auto_answer_config",
                                    [],
                                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                )
                                aa_enabled = auto_answer_config.get("enabled", True) if isinstance(auto_answer_config, dict) else True
                                aa_max = auto_answer_config.get("max_answers", 3) if isinstance(auto_answer_config, dict) else 3

                                if not aa_enabled or self._auto_answer_count >= aa_max:
                                    # Opt-out or max cycles exhausted → escalate
                                    self.run_status = "intervention_requested"
                                    auto_answer_reason = (
                                        "jules_auto_answer_disabled"
                                        if not aa_enabled
                                        else "jules_auto_answer_exhausted"
                                    )
                                    self.final_result = self._intervention_result(
                                        summary=(
                                            "Jules requested human feedback; "
                                            "operator intervention is required."
                                        ),
                                        request=request,
                                        metadata={
                                            "reason": "agent_requested_feedback",
                                            "julesAutoAnswerReason": auto_answer_reason,
                                            "julesAutoAnswerCount": self._auto_answer_count,
                                            "julesAutoAnswerMax": aa_max,
                                            "resiliencyPolicy": dict(resiliency_policy),
                                            "lastStatus": status_obj.model_dump(
                                                mode="json",
                                                by_alias=True,
                                            ),
                                        },
                                    )
                                    self._get_logger().warning(
                                        "Jules auto-answer %s for session %s (count=%d, max=%d)",
                                        "disabled" if not aa_enabled else "exhausted",
                                        self.run_id,
                                        self._auto_answer_count,
                                        aa_max,
                                    )
                                    await self._signal_parent_child_state_changed(
                                        parent_info,
                                        "intervention_requested",
                                        "Jules requested human feedback.",
                                    )
                                    break

                                # Dispatch question-answer cycle
                                task_context = request.agent_id or ""
                                answer_result = await self._execute_routed_activity(
                                    "integration.jules.answer_question",
                                    {
                                        "session_id": self.run_id,
                                        "question": question,
                                        "task_context": task_context,
                                    },
                                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                )
                                if isinstance(answer_result, dict) and answer_result.get("answered"):
                                    self._answered_activity_ids.add(act_id)
                                    self._auto_answer_count += 1
                                    self._get_logger().info(
                                        "Jules auto-answer #%d sent for session %s (activity %s)",
                                        self._auto_answer_count,
                                        self.run_id,
                                        act_id,
                                    )
                                else:
                                    self._get_logger().warning(
                                        "Jules auto-answer failed for session %s: %s",
                                        self.run_id,
                                        answer_result.get("error") if isinstance(answer_result, dict) else "unknown",
                                    )

                        # --- end auto-answer sub-flow ---

                        if status_obj.status in _TERMINAL_RUN_STATUSES:
                            break

                elapsed = (workflow.now() - overall_start).total_seconds()

                if elapsed >= timeout_seconds and not self.completion_event.is_set():
                    self.run_status = RunStatus.timed_out
                    if manager_handle and request.execution_profile_ref:
                        await manager_handle.signal(
                            "release_slot",
                            self._release_slot_payload(
                                profile_id=request.execution_profile_ref,
                                request=request,
                            ),
                        )
                    return await self._publish_terminal_result_with_compacted_replay_cleanup(
                        request=request,
                        result=self._timed_out_result(
                            request=request,
                            timeout_seconds=timeout_seconds,
                            elapsed_seconds=elapsed,
                            detail=(
                                "made no observable progress and exceeded its "
                                "execution budget"
                            ),
                        ),
                    )

                if self.final_result is None:
                    if request.agent_kind == "external":
                        # Fetch result via Temporal activity.
                        act_name = f"integration.{self._external_agent_id}.fetch_result"
                        result_dict = await self._execute_routed_activity(
                            act_name,
                            ExternalAgentRunInput(runId=str(self.run_id or "")),
                            cancellation_type=ActivityCancellationType.TRY_CANCEL,

                        )
                        self.final_result = AgentRunResult(**result_dict) if isinstance(result_dict, dict) else result_dict
                    else:
                        self.final_result = await self._fetch_managed_result(
                            request=request,
                            adapter=adapter,
                            uses_codex_session_adapter=uses_codex_session_adapter,
                            use_managed_status_activity=use_managed_status_activity,
                        )

                if (
                    request.agent_kind == "external"
                    and self._external_agent_id in {"jules", "jules_api"}
                    and self.final_result is not None
                    and self.final_result.failure_class is None
                ):
                    publish_mode = str(
                        (request.parameters or {}).get("publishMode") or "none"
                    ).strip().lower()
                    if publish_mode == "branch":
                        result_meta = dict(self.final_result.metadata or {})
                        pr_url = str(
                            result_meta.get("pullRequestUrl")
                            or result_meta.get("externalUrl")
                            or ""
                        ).strip()
                        if not pr_url:
                            self.final_result = self.final_result.model_copy(
                                update={
                                    "summary": "Jules branch publication failed: no pull request URL was available for merge.",
                                    "failure_class": "execution_error",
                                    "provider_error_code": "branch_publish_failed",
                                    "metadata": {
                                        **result_meta,
                                        "publishOutcome": "publish_failed",
                                    },
                                }
                            )
                        else:
                            workspace_spec = request.workspace_spec or {}
                            starting_branch = str(
                                workspace_spec.get("startingBranch")
                                or workspace_spec.get("branch")
                                or "main"
                            ).strip() or "main"
                            target_branch = str(
                                (request.parameters or {}).get("targetBranch")
                                or workspace_spec.get("targetBranch")
                                or ""
                            ).strip()
                            merge_payload: dict[str, Any] = {"pr_url": pr_url}
                            if target_branch and target_branch != starting_branch:
                                merge_payload["target_branch"] = target_branch
                            merge_result = await self._execute_routed_activity(
                                "repo.merge_pr",
                                merge_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                            )
                            merged = bool(
                                merge_result.get("merged")
                                if isinstance(merge_result, dict)
                                else False
                            )
                            merge_summary = (
                                merge_result.get("summary")
                                if isinstance(merge_result, dict)
                                else ""
                            ) or ""
                            if merged:
                                self.final_result = self.final_result.model_copy(
                                    update={
                                        "metadata": {
                                            **result_meta,
                                            "publishOutcome": "branch_merged",
                                            "pullRequestUrl": pr_url,
                                            "mergeSha": (
                                                merge_result.get("mergeSha")
                                                if isinstance(merge_result, dict)
                                                else None
                                            ),
                                        }
                                    }
                                )
                            else:
                                self.final_result = self.final_result.model_copy(
                                    update={
                                        "summary": merge_summary
                                        or "Jules branch publication failed during merge.",
                                        "failure_class": "execution_error",
                                        "provider_error_code": "branch_publish_failed",
                                        "metadata": {
                                            **result_meta,
                                            "publishOutcome": "publish_failed",
                                            "pullRequestUrl": pr_url,
                                        },
                                    }
                                )

                if self.final_result is not None:
                    self.final_result = await self._evaluate_terminal_contract(
                        request=request,
                        result=self.final_result,
                    )

                # Prefer the canonical structured provider failure event for the
                # cooldown decision when present; fall back to text-marker codes.
                prefer_structured_failure = workflow.patched(
                    AGENT_RUN_STRUCTURED_PROVIDER_FAILURE_PATCH_ID
                )
                provider_failure_event = (
                    provider_failure_event_from_metadata(
                        self.final_result.metadata.get("providerFailure")
                    )
                    if self.final_result is not None
                    else None
                )
                if prefer_structured_failure and provider_failure_event is not None:
                    requires_provider_cooldown = (
                        provider_failure_event_requires_cooldown(
                            provider_failure_event
                        )
                    )
                elif self.final_result is not None:
                    requires_provider_cooldown = provider_error_requires_cooldown(
                        provider_error_code=self.final_result.provider_error_code,
                        retry_recommendation=self.final_result.retry_recommendation,
                    )
                else:
                    requires_provider_cooldown = False

                if (
                    request.agent_kind == "managed"
                    and manager_handle
                    and requires_provider_cooldown
                ):
                    if workflow.patched("gemini-429-cooldown-retry-signal"):
                        runtime_id = self._managed_runtime_id(request.agent_id)
                        profile_id = str(request.execution_profile_ref or self._assigned_profile_id or "").strip() or None
                        cooldown_seconds = self._cooldown_seconds_for_profile(profile_id)
                        if (
                            prefer_structured_failure
                            and provider_failure_event is not None
                        ):
                            # Prefer retry_after_seconds / reset_at from the
                            # structured event over the profile default.
                            cooldown_seconds = resolve_provider_cooldown_seconds(
                                provider_failure_event,
                                now=workflow.now(),
                                default_seconds=cooldown_seconds,
                            )
                        if (
                            workflow.patched(
                                AGENT_RUN_PROVIDER_COOLDOWN_EXPONENTIAL_BACKOFF_PATCH_ID
                            )
                            and not self._provider_failure_supplies_retry_timing(
                                provider_failure_event
                            )
                        ):
                            cooldown_seconds = self._next_provider_cooldown_seconds(
                                runtime_id=runtime_id,
                                profile_id=profile_id,
                                base_seconds=cooldown_seconds,
                            )
                        waiting_reason = self._build_managed_rate_limit_waiting_reason(
                            runtime_id=runtime_id,
                            profile_id=profile_id,
                            cooldown_seconds=cooldown_seconds,
                        )
                        parent_info = workflow.info().parent
                        await self._signal_parent_child_state_changed(
                            parent_info,
                            "awaiting_slot",
                            waiting_reason,
                        )
                        await manager_handle.signal(
                            "report_cooldown",
                            {
                                "profile_id": profile_id or request.execution_profile_ref,
                                "cooldown_seconds": cooldown_seconds,
                            },
                        )
                        await manager_handle.signal(
                            "release_slot",
                            self._release_slot_payload(
                                profile_id=profile_id or request.execution_profile_ref,
                                request=request,
                            ),
                        )
                        self._awaiting_slot_reason_override = waiting_reason
                        self._slot_wait_timeout_override_seconds = max(
                            cooldown_seconds + 60,
                            _SLOT_WAIT_TIMEOUT_SECONDS,
                        )
                        self.completion_event.clear()
                        self.final_result = None
                        self._assigned_profile_id = None
                        retry_execution_profile_ref = requested_execution_profile_ref
                        if (
                            sticky_pinned_profile_on_cooldown_retry
                            and retry_execution_profile_ref is None
                        ):
                            retry_execution_profile_ref = profile_id
                        if (
                            sticky_pinned_profile_on_cooldown_retry
                            and retry_execution_profile_ref is not None
                        ):
                            requested_execution_profile_ref = retry_execution_profile_ref
                        request.execution_profile_ref = retry_execution_profile_ref
                        self._skip_default_profile_pin_once = (
                            not sticky_pinned_profile_on_cooldown_retry
                            and requested_execution_profile_ref is None
                        )
                        self.run_status = RunStatus.awaiting_slot
                        continue  # Retries loop
                    else:
                        await manager_handle.signal(
                            "report_cooldown",
                            {
                                "profile_id": request.execution_profile_ref,
                                "cooldown_seconds": 300,
                            },
                        )
                        await manager_handle.signal(
                            "release_slot",
                            self._release_slot_payload(
                                profile_id=request.execution_profile_ref,
                                request=request,
                            ),
                        )
                        active_profile_id = (
                            str(
                                request.execution_profile_ref
                                or self._assigned_profile_id
                                or ""
                            ).strip()
                            or None
                        )
                        self.completion_event.clear()
                        self.final_result = None
                        self._assigned_profile_id = None
                        retry_execution_profile_ref = requested_execution_profile_ref
                        if (
                            sticky_pinned_profile_on_cooldown_retry
                            and retry_execution_profile_ref is None
                        ):
                            retry_execution_profile_ref = active_profile_id
                        if (
                            sticky_pinned_profile_on_cooldown_retry
                            and retry_execution_profile_ref is not None
                        ):
                            requested_execution_profile_ref = retry_execution_profile_ref
                        request.execution_profile_ref = retry_execution_profile_ref
                        self._skip_default_profile_pin_once = (
                            not sticky_pinned_profile_on_cooldown_retry
                            and requested_execution_profile_ref is None
                        )
                        continue  # Retries loop

                # Not a 429 or external agent
                if manager_handle and request.execution_profile_ref:
                    await manager_handle.signal(
                        "release_slot",
                        self._release_slot_payload(
                            profile_id=request.execution_profile_ref,
                            request=request,
                            reserve_for_followup=True,
                        ),
                    )

                self.final_result = self._enrich_result_metadata(
                    request=request,
                    result=self.final_result,
                )
                if (
                    workflow.patched(AGENT_RUN_RESILIENCY_POLICY_PATCH_ID)
                    and self.final_result is not None
                ):
                    result_metadata = dict(self.final_result.metadata or {})
                    result_metadata.setdefault(
                        "resiliencyPolicy",
                        dict(resiliency_policy),
                    )
                    self.final_result = self.final_result.model_copy(
                        update={"metadata": result_metadata}
                    )

                return await self._publish_terminal_result_with_compacted_replay_cleanup(
                    request=request,
                    result=self.final_result,
                    manager_handle=manager_handle,
                )

        except asyncio.TimeoutError:
            self.run_status = RunStatus.timed_out
            if request.agent_kind == "managed" and hasattr(request, "execution_profile_ref") and request.execution_profile_ref:
                try:
                    runtime_mapping = {"claude": "claude_code", "codex": "codex_cli"}
                    runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                    manager_id = self._manager_workflow_id(runtime_id)
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal(
                        "release_slot",
                        self._release_slot_payload(
                            profile_id=request.execution_profile_ref,
                            request=request,
                        ),
                    )
                except Exception:
                    self._get_logger().warning("Failed to release slot on timeout, which may lead to a leak.", exc_info=True)
            return await self._publish_terminal_result(
                request=request,
                result=self._timed_out_result(
                    request=request,
                    timeout_seconds=timeout_seconds,
                    elapsed_seconds=(
                        workflow.now() - overall_start
                    ).total_seconds(),
                    detail="timed out without completing the turn",
                ),
            )

        except CancelledError:
            tasks = []

            if request.agent_kind == "managed" and getattr(request, "execution_profile_ref", None):
                runtime_mapping = {"claude": "claude_code", "codex": "codex_cli"}
                runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                manager_id = self._manager_workflow_id(runtime_id)
                
                async def _release_slot():
                    try:
                        manager_handle = workflow.get_external_workflow_handle(manager_id)
                        await manager_handle.signal(
                            "release_slot",
                            self._release_slot_payload(
                                profile_id=request.execution_profile_ref,
                                request=request,
                            ),
                        )
                    except Exception:
                        # Errors are intentionally ignored to avoid masking the original cancellation
                        self._get_logger().warning("Failed to release slot on cancellation, which may lead to a leak.", exc_info=True)
                
                tasks.append(asyncio.shield(_release_slot()))

            if self.run_id is not None and self.agent_kind is not None:
                async def _cancel_agent():
                    try:
                        if self.agent_kind == "external" and self._external_agent_id is not None:
                            # Route external cancel through integration activity.
                            act_name = f"integration.{self._external_agent_id}.cancel"
                            await self._execute_routed_activity(
                                act_name,
                                ExternalAgentRunInput(runId=str(self.run_id or "")),
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,

                            )
                        else:
                            await self._execute_routed_activity(
                                "agent_runtime.cancel",
                                AgentRuntimeCancelInput(
                                    agentKind=self.agent_kind,
                                    runId=str(self.run_id or ""),
                                ),
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,

                            )
                    except Exception:
                        self._get_logger().warning("Failed to cancel agent runtime on cancellation.", exc_info=True)
                
                tasks.append(asyncio.shield(_cancel_agent()))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            raise
            
        except Exception:
            if request.agent_kind == "managed" and getattr(request, "execution_profile_ref", None):
                runtime_mapping = {"claude": "claude_code", "codex": "codex_cli"}
                runtime_id = runtime_mapping.get(request.agent_id, request.agent_id)
                manager_id = self._manager_workflow_id(runtime_id)
                try:
                    manager_handle = workflow.get_external_workflow_handle(manager_id)
                    await manager_handle.signal(
                        "release_slot",
                        self._release_slot_payload(
                            profile_id=request.execution_profile_ref,
                            request=request,
                        ),
                    )
                except Exception:
                    self._get_logger().warning("Failed to release slot on unexpected exception, which may lead to a leak.", exc_info=True)
            raise
