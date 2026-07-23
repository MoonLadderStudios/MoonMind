import asyncio
import dataclasses
import hashlib
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
    from api_service.services.provider_profile_readiness import (
        provider_profile_launch_ready_from_payload,
    )
    from moonmind.schemas.agent_skill_models import ResolvedSkillSet, SkillSelector
    from moonmind.schemas.container_job_models import (
        ContainerJobState,
        ContainerJobSubmitRequest,
        OwnerIdentity,
    )
    from moonmind.workloads.tool_bridge import CONTAINER_RUN_JOB_TOOL
    from moonmind.schemas.managed_session_models import (
        CodexManagedSessionBinding,
        CodexManagedSessionClearRequest,
        CodexManagedSessionHandle,
        CodexManagedSessionSnapshot,
        CodexManagedSessionWorkflowInput,
        TerminateCodexManagedSessionRequest,
        canonical_managed_session_runtime_id,
    )
    from moonmind.schemas.temporal_activity_models import (
        ArtifactReadInput,
        ArtifactWriteCompleteInput,
        DependencyStatusSnapshotInput,
        ExecutionTerminalStateInput,
        PlanGenerateInput,
        ResiliencePolicyCompileInput,
    )
    from moonmind.schemas.resilience_policy_models import (
        RESILIENCE_POLICY_CONTENT_TYPE,
        ResiliencePolicyEnvelope,
    )
    from moonmind.schemas.incident_reconstruction_models import (
        INCIDENT_RECONSTRUCTION_CONTENT_TYPE,
    )
    from moonmind.workflows.temporal.jules_bundle import (
        build_bundle_spec,
        eligible_for_bundle,
        is_jules_agent_runtime_node,
    )
    from moonmind.workflows.executions.routing import _coerce_bool
    from moonmind.workflows.executions.prepared_context import (
        ExecutionContextBundle,
        branch_turn_step_execution_manifest_projection,
        build_branch_turn_artifact_manifest,
        build_branch_turn_context_bundle,
        build_durable_retrieval_manifest_artifact,
        build_execution_context_bundle,
        build_prepared_input_manifest,
        build_recovery_prepared_artifact_refs,
        merge_prepared_raw_input_refs,
        select_step_prepared_context,
    )
    from moonmind.workflows.agent_skills.selection import selected_agent_skill
    from moonmind.config.settings import settings
    from moonmind.utils.logging import redact_sensitive_text, scrub_github_tokens
    from moonmind.workflows.temporal.jira_agent_skills import (
        JIRA_AGENT_SKILLS,
        JIRA_BACKED_AGENT_SKILLS,
    )
    from moonmind.workflows.temporal.story_output_tools import (
        JIRA_CHECK_BLOCKERS_TOOL_NAME,
    )
    from moonmind.workflows.temporal.typed_execution import execute_typed_activity
    from moonmind.workflows.executions.execution_contract import (
        build_effective_workflow_skill_selectors,
    )
    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        workflow_id_for_runtime,
    )
    from moonmind.workflows.temporal.workflows.pr_resolver import (
        build_pr_resolver_start_input,
        pr_resolver_identity_selector,
    )
    from moonmind.workflows.temporal.native_skill_bindings import (
        evaluate_pr_resolver_native_binding,
        require_skill_owned_pr_resolver_execution,
    )
    from moonmind.schemas.temporal_models import (
        DependencyResolvedSignalPayload,
        ExecutionProgressModel,
        FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
        STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
        StepExecutionIdentityModel,
        StepExecutionManifestModel,
        StepLedgerSnapshotModel,
        build_step_execution_id,
        build_step_execution_idempotency_key,
        normalize_dependency_ids,
    )
    from moonmind.workflows.temporal.step_checkpoints import (
        StepExecutionCheckpointBoundary,
        build_step_checkpoint_id,
    )
    from moonmind.workflows.executions.runtime_capabilities import (
        RuntimeCapabilityError,
        RuntimeExecutionCapabilities,
        resolve_runtime_execution_capabilities,
    )
    from moonmind.workflows.temporal.checkpoint_policy import (
        resolve_checkpoint_policy,
    )
    from moonmind.workflows.temporal.managed_session_errors import (
        is_managed_session_locator_mismatch_error,
    )

from moonmind.workflows.skills.skill_plan_contracts import parse_plan_definition
from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot, parse_tool_registry
from moonmind.workflows.skills.approval_policy import (
    ReviewRequest,
    StepGateResult,
    build_feedback_input,
    build_feedback_instruction,
    parse_step_gate_result,
    recommended_next_actions,
)
from moonmind.workflows.skills.tool_plan_contracts import REVIEW_VERDICTS
from moonmind.workflows.temporal.step_ledger import (
    TERMINAL_STEP_STATUSES,
    build_initial_step_rows,
    build_progress_summary,
    build_step_ledger_snapshot,
    clear_step_checkpoint_evidence,
    invalidate_downstream_steps_for_changed_output,
    mark_step_execution_manifest_evidence,
    materialize_preserved_steps,
    mark_step_checkpoint_evidence,
    preserved_outputs_for_dependencies,
    record_dependency_inputs_for_step,
    refresh_ready_steps,
    upsert_step_check,
    update_step_row,
    validate_preserved_dependency_outputs,
)
from moonmind.workflows.temporal.step_executions import (
    external_handoff_gate_decision,
    git_effect_metadata,
    logical_step_success_allowed,
    plan_reattempt_compensation,
    side_effect_record,
    step_execution_operation_idempotency_key,
    workspace_policy_metadata,
)
from moonmind.workflows.temporal.recovery_manifest import (
    build_failed_run_recovery_manifest,
)
from moonmind.workflows.temporal.recovery_state import (
    CheckpointRecoveryContract,
    deterministic_recovery_identity,
    validate_restore_result,
)
from moonmind.workflows.temporal.recovery_decision import validate_recovery_contract
from moonmind.workflows.temporal.incident_reconstruction import (
    build_incident_reconstruction_manifest,
    build_incident_trace_ref,
)
from moonmind.workflows.temporal.bounded_story_loop import (
    BoundedStoryLoopInput,
    LoopAttempt,
    LoopBudget,
    PublicationAction,
    TypedGateResult,
    compile_bounded_story_loop,
    evaluate_attempt_continuation,
    evaluate_publication_decision,
)
from moonmind.workflows.temporal.completion_summary import (
    is_generic_completion_summary,
)
from moonmind.workflows.temporal.publish_auto_evidence import (
    AutoPublishEvidenceError,
    parse_auto_publish_evidence,
)
from moonmind.workflows.temporal.title_search import tokenize_title
from moonmind.workflows.temporal.scheduled_start import temporal_scheduled_start_time
from moonmind.workflows.temporal.activity_catalog import (
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
JIRA_BLOCKER_RECHECK_MIN_ACTIVITY_ATTEMPTS = 3

DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()
RUN_EXPLICIT_RECOVERY_CONTRACT_PATCH = "run-explicit-recovery-contract-v1"


def bounded_story_loop_step_effects(
    attempt: LoopAttempt,
    gate: TypedGateResult,
) -> dict[str, Any]:
    """Return compact side-effect eligibility for a bounded story loop attempt."""

    publication = evaluate_publication_decision(
        action=PublicationAction.PR,
        latest_attempt=attempt,
        gate=gate,
    )
    refs = [
        ref
        for ref in (attempt.checkpoint_before_ref, attempt.checkpoint_after_ref)
        if ref
    ]
    return {
        "stepExecutionId": attempt.step_execution_id,
        "checkpointRefs": refs,
        "candidateDiffRef": attempt.candidate_diff_ref,
        "acceptedOutputRef": attempt.accepted_output_ref,
        "commitAllowed": attempt.commit_allowed,
        "publicationAllowed": publication.allowed,
        "publicationReason": publication.reason,
        "gateResultRef": gate.gate_result_ref,
        "remainingWorkRef": gate.remaining_work_ref,
    }


def bounded_story_loop_resume_decision(
    resume: Mapping[str, Any],
    *,
    current_selected_item_digest: str,
) -> dict[str, Any]:
    checkpoint_ref = str(resume.get("recoveryCheckpointRef") or "").strip()
    selected_digest = str(resume.get("selectedItemDigest") or "").strip()
    if not checkpoint_ref.startswith("artifact://"):
        return {
            "allowed": False,
            "reason": "recovery_checkpoint_ref_missing",
            "fallback": "none",
        }
    if selected_digest != str(current_selected_item_digest or "").strip():
        return {
            "allowed": False,
            "reason": "selected_item_digest_mismatch",
            "fallback": "none",
        }
    return {
        "allowed": True,
        "mode": "checkpoint_backed_resume",
        "loopId": resume.get("loopId"),
        "recoveryCheckpointRef": checkpoint_ref,
        "resumeFromAttemptOrdinal": resume.get("resumeFromAttemptOrdinal"),
        "fallback": "none",
    }


def bounded_story_loop_scope_guard(
    *,
    selected_item_digest: str,
    candidate_item_digests: Sequence[str],
    full_supervisor_enabled: bool,
) -> dict[str, Any]:
    if full_supervisor_enabled:
        return {
            "allowed": False,
            "reason": "full_autonomous_supervisor_gated",
        }
    selected = str(selected_item_digest or "").strip()
    candidates = [str(item or "").strip() for item in candidate_item_digests]
    if candidates != [selected]:
        return {
            "allowed": False,
            "reason": "unrelated_work_selection_rejected",
        }
    return {"allowed": True, "reason": "selected_item_only"}
_PR_OPTIONAL_AGENT_SKILLS = JIRA_AGENT_SKILLS
_PR_OPTIONAL_TASK_SKILLS = frozenset(
    {"jira-implement", *_PR_OPTIONAL_AGENT_SKILLS}
)
_CANONICAL_NO_COMMIT_TASK_PRESETS = frozenset({"github-issue-implement"})
_EXTERNAL_INTEGRATION_MONITOR_IDS = frozenset({"codex_cloud", "jules"})
_PUBLISH_NOT_REQUIRED_STATUSES = frozenset(
    {
        "not_required",
        "not-required",
        "notrequired",
        "not_applicable",
        "not-applicable",
    }
)
_DIRECT_EXECUTABLE_OUTPUT_KEYS = frozenset(
    {
        "diagnostics_artifact_ref",
        "diagnosticsArtifactRef",
        "evidence_refs",
        "evidenceRefs",
        "primary_report_ref",
        "primaryReportRef",
        "publish_outcome",
        "publishOutcome",
        "push_status",
        "pushStatus",
        "report_bundle",
        "reportBundle",
        "report_bundle_v",
        "reportBundleV",
        "report_scope",
        "reportScope",
        "report_type",
        "reportType",
        "stderr_artifact_ref",
        "stderrArtifactRef",
        "stdout_artifact_ref",
        "stdoutArtifactRef",
        "structured_ref",
        "structuredRef",
        "summary_ref",
        "summaryRef",
    }
)
RUN_AUTO_PUBLISH_METADATA_EVIDENCE_PATCH = "run-auto-publish-metadata-evidence-v1"
RUN_CHECKPOINT_RECOVERY_STATE_MACHINE_PATCH = "run-checkpoint-recovery-state-machine-v1"
RUN_PR_RESOLVER_OWNED_CONTINUATION_PATCH = "run-pr-resolver-owned-continuation-v1"
RUN_PR_RESOLVER_CONTINUATION_IDENTITY_PATCH = (
    "run-pr-resolver-continuation-identity-v1"
)
_REPORT_ONLY_PUBLISH_TYPES = frozenset({"security_pentest_report"})
_JIRA_ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_JIRA_BACKED_AGENT_SKILLS = frozenset(
    {"jira-implement", *JIRA_BACKED_AGENT_SKILLS}
)
_PLAIN_TEXT_BLOCKED_OUTCOME_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:result|verdict|outcome)\s*:\s*blocked\b[^\n]*"
)
_ALREADY_IMPLEMENTED_NO_WORK_PATTERN = re.compile(
    r"\balready\s+(?:implemented|done|complete(?:d)?|satisfied|in\s+place)\b",
    re.IGNORECASE,
)
_ALREADY_IMPLEMENTED_UNCERTAINTY_PATTERN = re.compile(
    r"\b(?:whether|if|unless|until|unconfirmed|unclear|unknown|"
    r"not\s+(?:confirm(?:ed)?|verified|clear|sure)|"
    r"(?:could\s+not|couldn't|cannot|can't|unable\s+to|did\s+not|didn't|"
    r"does\s+not|doesn't)\s+confirm|"
    r"(?:without|no)\s+confirmation)\b",
    re.IGNORECASE,
)
_MOONSPEC_GATE_PASSING_VERDICTS = frozenset({"FULLY_IMPLEMENTED"})
# Bounded budget for re-running a moonspec-verify step whose structured gate
# output violated the canonical contract (a parse problem, not a verifier
# judgment). Remediation implement cycles cannot fix malformed JSON.
_MOONSPEC_GATE_CONTRACT_REPAIR_MAX_ATTEMPTS = 2
_MOONSPEC_GATE_BLOCKING_VERDICTS = frozenset(
    {
        "ADDITIONAL_WORK_NEEDED",
        "NO_DETERMINATION",
        "BLOCKED",
        "FAILED_UNRECOVERABLE",
        "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
    }
)
_MOONSPEC_GATE_VERDICT_TEXT_PATTERN = re.compile(
    r"\b("
    r"FULLY_IMPLEMENTED|"
    r"ADDITIONAL_WORK_NEEDED|"
    r"NO_DETERMINATION|"
    r"ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION|"
    r"FAILED_UNRECOVERABLE|"
    r"BLOCKED"
    r")\b",
    re.IGNORECASE,
)
_MOONSPEC_REMEDIATION_TITLE_ATTEMPT_PATTERN = re.compile(
    r"\b(?P<attempt>\d+)\s+of\s+(?P<max_attempts>\d+)\b",
    re.IGNORECASE,
)
class RunWorkflowInput(TypedDict, total=False):
    """Input payload for the MoonMind.UserWorkflow workflow."""

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
    executionOutcome: dict[str, Any]
    finalizationOutcome: dict[str, Any]

USER_WORKFLOW_NAME = "MoonMind.UserWorkflow"
WORKFLOW_NAME = USER_WORKFLOW_NAME
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
STATE_NO_COMMIT = "no_commit"
STATE_CANCELED = "canceled"
STATE_FAILED = "failed"
CLOSE_STATUS_COMPLETED = "completed"
CLOSE_STATUS_CANCELED = "canceled"
CLOSE_STATUS_FAILED = "failed"
DEPENDENCY_RESOLUTION_NOT_APPLICABLE = "not_applicable"
DEPENDENCY_RESOLUTION_SATISFIED = "satisfied"
DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN = "satisfied_after_rerun"
DEPENDENCY_RESOLUTION_FAILED = "dependency_failed"
DEPENDENCY_RESOLUTION_BYPASSED = "bypassed"
DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE = "manual_override"
DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN = "waiting_for_successful_rerun"
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
# Replay-stable patch id for workflow-scoped Codex terminate activity+signal finalization.
RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_PATCH = "run-task-scoped-session-termination-v1"
RUN_BLOCKED_OUTCOME_SHORT_CIRCUIT_PATCH = "run-blocked-outcome-short-circuit-v1"
RUN_JIRA_BLOCKER_RECHECK_PATCH = "run-jira-blocker-recheck-v1"
RUN_FAILED_RESULT_BLOCKER_PATCH = "run-failed-result-blocker-v1"
RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH = "run-jira-blocker-wait-coalescing-v1"
RUN_JIRA_BLOCKER_RECHECK_RETRY_FLOOR_PATCH = (
    "run-jira-blocker-recheck-retry-floor-v1"
)
RUN_JIRA_BLOCKER_RECHECK_ASSESSMENT_CONTEXT_PATCH = (
    "run-jira-blocker-recheck-assessment-context-v1"
)
RUN_JIRA_BLOCKER_RECHECK_ASSESSMENT_CONTEXT_ALIAS_PATCH = (
    "run-jira-blocker-recheck-assessment-context-alias-v1"
)
# Replay-stable patch id for the v2 workflow-scoped Codex termination path. The
# identifier says "update" for in-flight history continuity, but current
# Temporal external workflow handles expose the session control surface by signal.
RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_UPDATE_PATCH = "run-task-scoped-session-termination-v2"
# Replay-stable patch id for workflow-scoped Codex termination through the
# AgentSession update handler. This path executes the remote terminate activity.
RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_UPDATE_EXECUTE_PATCH = (
    "run-task-scoped-session-termination-v3"
)
# Replay-stable patch id for skipping registry reads on agent-runtime-only plans.
RUN_CONDITIONAL_REGISTRY_READ_PATCH = "run-conditional-registry-read-v1"
RUN_PROVIDER_PROFILE_MANAGER_ID_PATCH = "provider-profile-manager-id-v1"
RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH = "run-workflow-child-task-queue-v2"
RUN_RUNTIME_PROFILE_CLEAR_FORWARDING_PATCH = "run-runtime-profile-clear-forwarding-v1"
RUN_UPDATE_INPUTS_VISIBILITY_REFRESH_PATCH = (
    "run-update-inputs-visibility-refresh-v1"
)
RUN_RECURRING_SCHEDULED_START_PATCH = "run-recurring-scheduled-start-v1"
RUN_MEMO_RUNTIME_INHERITANCE_PATCH = "run-memo-runtime-inheritance-v1"
DEPENDENCY_GATE_PATCH = "dependency-gate-v1"
# Replay-stable patch id for unified wait-through-rerun dependency behavior.
# Under this patch, a non-success prerequisite terminal outcome (failed,
# canceled, terminated, timed_out, unresolvable) keeps the dependent run
# blocked in waiting_on_dependencies until the prerequisite is rerun and
# completes, the dependent is canceled, or an operator bypasses the gate.
DEPENDENCY_WAIT_THROUGH_RERUN_PATCH = "dependency-wait-through-rerun-v1"
NATIVE_PR_CREATE_PAYLOAD_PATCH = "native-pr-create-payload-v1"
NATIVE_PR_BRANCH_DEFAULTS_PATCH = "native-pr-branch-defaults-v1"
NATIVE_PR_PUSH_STATUS_GATE_PATCH = "native-pr-push-status-gate-v1"
NATIVE_PR_LEASE_CONFLICT_GATE_PATCH = "native-pr-lease-conflict-gate-v1"
RUN_STOP_ON_PUBLISH_HANDOFF_FAILURE_PATCH = (
    "run-stop-on-publish-handoff-failure-v1"
)
# Replay-stable patch for recovering the confirmed PR URL from durable publish
# context before starting merge automation. Older histories retain their local
# variable-only handoff; new histories can no longer lose a PR observed by a
# prior step when a later plan step carries no publication output.
RUN_DURABLE_PUBLISH_CONTEXT_MERGE_HANDOFF_PATCH = (
    "run-durable-publish-context-merge-handoff-v1"
)
RUN_DIRECT_TOOL_REPORT_OUTPUTS_PATCH = "run-direct-tool-report-outputs-v1"
RUN_ASSESSMENT_PARAMETER_INJECTION_PATCH = (
    "run-assessment-parameter-injection-v1"
)
# Assert the producing Step Execution reached the `accepted` terminal
# disposition before an external handoff runs, in addition to the existing
# MoonSpec gate verdict block. Guarded for replay safety so in-flight runs keep
# their original verdict-only decisions.
RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH = (
    "run-handoff-accepted-disposition-gate-v1"
)
RUN_WORKFLOW_PUBLISH_OUTCOME_PATCH = "run-workflow-publish-outcome-v1"
RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH = "run-canonical-no-commit-outcome-v1"
RUN_UNGATED_CONTINUATION_DISPOSITION_PATCH = (
    "run-ungated-continuation-disposition-v1"
)
RUN_GATED_STEP_CONTINUATION_PATCH = "run-gated-step-continuation-v1"
# Merge-automation dispositions that are *continuations*: they only have meaning
# when a MoonMind.MergeAutomation gate re-enters and finalizes the merge. A
# standalone (ungated) resolver run that ends in one of these states has not
# resolved the PR and must not be reported as a success.
MERGE_AUTOMATION_CONTINUATION_DISPOSITIONS = frozenset({"reenter_gate"})
GATED_CONTINUATION_GATE_REGISTRY: Mapping[str, frozenset[str]] = {
    "merge_automation": MERGE_AUTOMATION_CONTINUATION_DISPOSITIONS,
}
RUN_PUBLISH_REPAIR_FEEDBACK_PATCH = "run-publish-repair-feedback-v1"
RUN_PREPUBLICATION_FAILURE_BLOCKS_REPAIR_PATCH = (
    "run-prepublication-failure-blocks-repair-v1"
)
RUN_PREPUBLICATION_FAILURE_BLOCKS_PUBLISH_PATCH = (
    "run-prepublication-failure-blocks-publish-v1"
)
RUN_FETCH_PROFILE_SNAPSHOTS_PATCH = "fetch-profile-snapshots-v1"
RUN_SLOT_CONTINUITY_PATCH = "run-slot-continuity-v1"
RUN_DEFER_WORKFLOW_SCOPED_SESSION_UNTIL_SLOT_PATCH = (
    "run-defer-task-scoped-session-until-slot-v1"
)
RUN_WORKFLOW_SCOPED_SESSION_CLEAR_BETWEEN_STEPS_PATCH = (
    "run-task-scoped-session-clear-between-steps-v1"
)
RUN_WORKFLOW_SCOPED_SESSION_CLEAR_PER_EXECUTION_PATCH = (
    "run-task-scoped-session-clear-per-execution-v2"
)
RUN_WORKFLOW_SCOPED_SESSION_CLEAR_ACTIVITY_SIGNAL_PATCH = (
    "run-task-scoped-session-clear-activity-signal-v1"
)
RUN_WORKFLOW_SCOPED_SESSION_CLEAR_UPDATE_AUTHORITATIVE_PATCH = (
    "run-task-scoped-session-clear-update-authoritative-v1"
)
RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_ACTIVITY_SIGNAL_PATCH = (
    "run-task-scoped-session-termination-v4"
)
RUN_TERMINAL_STATE_ACTIVITY_PATCH = "run-terminal-state-activity-v1"
# Replay-stable patch id for emitting a failed-run recovery manifest before
# terminal failure is reported (MM-881). Gated so in-flight histories that
# predate the manifest artifact writes keep replaying deterministically.
RUN_FAILED_RUN_RECOVERY_MANIFEST_PATCH = "run-failed-run-recovery-manifest-v1"
RUN_PAUSE_SAFE_BOUNDARIES_PATCH = "run-pause-safe-boundaries-v1"
# Replay-stable patch id for stamping mm_started_at when real work begins.
RUN_REAL_STARTED_AT_PATCH = "run-real-started-at-v1"
RUN_STEP_EXECUTION_MANIFEST_PATCH = "run-step-" + "attempt-manifest-v1"
RUN_CANONICAL_STEP_STATUS_VOCAB_PATCH = "run-canonical-step-status-vocabulary-v1"
RUN_CANONICAL_STEP_CHECKPOINTS_PATCH = "run-canonical-step-checkpoints-v1"
RUN_MANAGED_CHECKPOINT_AUTHORITY_PATCH = "run-managed-checkpoint-authority-v1"
RUN_MANAGED_CHECKPOINT_CAPTURE_PATCH = "run-managed-checkpoint-capture-v1"
RUN_MANAGED_CHECKPOINT_LOCATOR_GUARD_PATCH = (
    "run-managed-checkpoint-locator-guard-v1"
)
RUN_RUNTIME_EXECUTION_CAPABILITIES_PATCH = "run-runtime-execution-capabilities-v1"
RUN_DURABLE_FINALIZATION_OUTCOME_PATCH = "run-durable-finalization-outcome-v1"
RUN_SKIP_NO_PUBLISH_PREPUBLICATION_CHECKPOINT_PATCH = (
    "run-skip-no-publish-prepublication-checkpoint-v1"
)
FINALIZATION_CHECKPOINT_FAILED = "FINALIZATION_CHECKPOINT_FAILED"
FINALIZATION_PUBLICATION_FAILED = "FINALIZATION_PUBLICATION_FAILED"
FINALIZATION_RETRY_EXHAUSTED = "FINALIZATION_RETRY_EXHAUSTED"
RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH = (
    "run-emit-ephemeral-step-checkpoints-v1"
)
RUN_STEP_EXECUTION_NAMING_PATCH = "run-step-execution-naming-v1"
RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH = "run-checkpoint-branch-turn-context-v1"
RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH = (
    "run-omnigent-checkpoint-branch-turn-request-v1"
)
RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH = (
    "run-omnigent-authored-selection-compiler-v1"
)
RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH = (
    "run-already-implemented-jira-completion-v1"
)
RUN_MOONSPEC_VERIFY_PUBLICATION_GATE_PATCH = (
    "run-moonspec-verify-publication-gate-v1"
)
RUN_MOONSPEC_GATE_PREVIOUS_OUTPUTS_HANDOFF_PATCH = (
    "run-moonspec-gate-previous-outputs-handoff-v1"
)
RUN_MOONSPEC_VERIFY_REMEDIATION_INDEX_PATCH = (
    "run-moonspec-verify-remediation-index-v1"
)
RUN_MOONSPEC_REMEDIATION_STEP_SKIP_PATCH = "run-moonspec-remediation-step-skip-v1"
RUN_MOONSPEC_TITLE_REMEDIATION_DETECTION_PATCH = (
    "run-moonspec-title-remediation-detection-v1"
)
RUN_MOONSPEC_GATE_CONTRACT_REPAIR_PATCH = "run-moonspec-gate-contract-repair-v1"
RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH = (
    "run-bounded-story-loop-progress-budget-v1"
)
RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH = (
    "run-bounded-story-loop-feedback-progress-v1"
)
RUN_BOUNDED_STORY_LOOP_REMEDIATION_BUDGET_PATCH = (
    "run-bounded-story-loop-remediation-budget-v1"
)
RUN_MOONSPEC_GATE_CONTRACT_REPAIR_FRESH_SOURCE_PATCH = (
    "run-moonspec-gate-contract-repair-fresh-source-v1"
)
RUN_MOONSPEC_GATE_ENVIRONMENT_DRAFT_PUBLISH_PATCH = (
    "run-moonspec-gate-environment-draft-publish-v1"
)
RUN_MOONSPEC_ADDITIONAL_WORK_DRAFT_PUBLISH_PATCH = (
    "run-moonspec-additional-work-draft-publish-v1"
)
RUN_MOONSPEC_DRAFT_PUBLISH_RECOVERY_HANDOFF_PATCH = (
    "run-moonspec-draft-publish-recovery-handoff-v1"
)
RUN_AUTHORITATIVE_PUBLISH_OUTCOME_PATCH = "run-authoritative-publish-outcome-v1"
RUN_AUTHORITATIVE_PR_REQUIREMENT_PATCH = (
    "run-authoritative-pr-requirement-v1"
)
RUN_STEP_RETRY_OVERRIDES_PATCH = "run-step-retry-overrides-v1"
RUN_PROPAGATE_AGENT_CHILD_CANCELLATION_PATCH = (
    "run-propagate-agent-child-cancellation-v1"
)
# MM-880: compile + persist a versioned ResiliencePolicy envelope before step
# execution begins so every step execution can be traced to the policy values
# that governed it.
RUN_RESILIENCE_POLICY_PATCH = "run-resilience-policy-v1"
# MM-880: compile + persist a per-step ResiliencePolicy envelope when a plan node
# resolves to a provider profile that differs from the run-level inherited
# profile, so that step's manifest references the cooldown/rate-limit values that
# actually governed its child runtime instead of the run-level policy.
RUN_STEP_RESILIENCE_POLICY_PATCH = "run-step-resilience-policy-v1"
RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH = (
    "run-agent-runtime-retry-classification-v1"
)
RUN_FAIL_FAST_STEP_FAILURE_SUMMARY_PATCH = "run-fail-fast-step-failure-summary-v1"
# MM-884: stamp a stable correlation (trace) ref onto step-execution manifests
# and emit a single incident reconstruction manifest before terminal failure so
# policy, provider/profile/credential source, failed step, progress, workspace
# changes, side effects, checkpoint, cost, trace spans, logs, and artifacts are
# correlated under one trace id. Gated so in-flight histories that predate the
# trace-ref stamp / incident-manifest writes keep replaying deterministically.
RUN_INCIDENT_RECONSTRUCTION_PATCH = "run-incident-reconstruction-v1"
RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH = (
    "run-plan-routed-moonspec-remediation-v1"
)


@dataclasses.dataclass(frozen=True)
class MoonSpecRemediationSuccessor:
    """One exact, plan-authored remediation destination."""

    logical_step_id: str
    attempt: int
    max_attempts: int
    node_index: int


@dataclasses.dataclass(frozen=True)
class GateTransitionDecision:
    """Workflow-owned routing for a valid verifier semantic result."""

    disposition: str
    routing_disposition: str
    reason_code: str
    successor: MoonSpecRemediationSuccessor | None = None
# Replay-stable patch id for the status-only memo update emitted from
# _update_search_attributes. Histories that already recorded the prior ungated
# memo command require a reset/versioning cutover; see
# docs/tmp/RunStatusMemoUpsertCutover.md.
RUN_STATUS_MEMO_UPSERT_PATCH = "run-status-memo-upsert-v1"
RUN_JSON_ARTIFACT_WRITE_COMPLETE_PATCH = "run-json-artifact-write-complete-v1"
RUN_TEMPORAL_PR_RESOLVER_OWNERSHIP_PATCH = "run-temporal-pr-resolver-ownership-v1"
RUN_PR_RESOLVER_CAPABILITY_PREFLIGHT_PATCH = (
    "run-pr-resolver-capability-preflight-v1"
)
RUN_PR_RESOLVER_PUBLISH_EVIDENCE_REF_PATCH = (
    "run-pr-resolver-publish-evidence-ref-v1"
)
RUN_TRUSTED_PR_RESOLVER_NATIVE_BINDING_PATCH = (
    "run-trusted-pr-resolver-native-binding-v1"
)
RUN_PR_RESOLVER_SKILL_OWNED_EXECUTION_PATCH = (
    "run-pr-resolver-skill-owned-execution-v1"
)
RUN_RESOLVED_SKILL_TERMINAL_CONTRACT_PATCH = (
    "run-resolved-skill-terminal-contract-v1"
)
RUN_EXISTING_SKILLSET_TERMINAL_CONTRACT_PATCH = (
    "run-existing-skillset-terminal-contract-v1"
)
RUN_PR_RESOLVER_SELECTOR_RESOLUTION_PATCH = (
    "run-pr-resolver-selector-resolution-v1"
)


def _worker_capability_unavailable_error(
    worker_capability: Mapping[str, Any],
) -> exceptions.ApplicationError:
    return exceptions.ApplicationError(
        "MoonMind.PRResolver worker capability is unavailable; "
        "no resolver or remediation agent was launched.",
        dict(worker_capability),
        type="WORKER_CAPABILITY_UNAVAILABLE",
        non_retryable=True,
    )


MM_STARTED_AT_SEARCH_ATTRIBUTE = "mm_started_at"
_PROFILE_SYNC_RUNTIME_IDS = ("codex_cli", "claude_code")
_MANAGED_AGENT_IDS = frozenset(
    {
        "claude",
        "claude_code",
        "codex",
        "codex_cli",
        # Replay-only: preserve managed classification for pre-cutover histories.
        "gemini_cli",
    }
)

def _normalize_agent_runtime_id(agent_id: str) -> str:
    """Normalize runtime identifiers for managed/external dispatch decisions."""

    return str(agent_id).strip().lower().replace("-", "_")

def _legacy_manager_workflow_id(runtime_id: str) -> str:
    # Preserve legacy workflow IDs for in-flight histories. New executions use
    # provider-profile-manager IDs once the replay patch is active.
    return f"auth-profile-manager:{runtime_id}"

class MoonMindRunWorkflow:
    def _expected_workflow_name(self) -> str:
        return WORKFLOW_NAME

    def _supported_dependency_workflow_types(self) -> frozenset[str]:
        return frozenset({WORKFLOW_NAME, self._expected_workflow_name()})

    def _manager_workflow_id(self, runtime_id: str) -> str:
        if workflow.patched(RUN_PROVIDER_PROFILE_MANAGER_ID_PATCH):
            return workflow_id_for_runtime(runtime_id)
        return _legacy_manager_workflow_id(runtime_id)

    def _workflow_child_task_queue(self) -> str:
        if workflow.patched(RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH):
            return settings.temporal.user_workflow_v2_task_queue
        return WORKFLOW_TASK_QUEUE

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

    @staticmethod
    def _operator_failure_summary(exc: BaseException) -> str:
        """Return the most actionable bounded message from a nested failure chain."""

        generic_messages = {
            "Activity task failed",
            "Activity error",
            "Child Workflow execution failed",
            "Child workflow execution failed",
            "Workflow execution failed",
            "activity failed",
        }
        generic_types = (
            exceptions.ActivityError,
            exceptions.ChildWorkflowError,
        )
        chain: list[tuple[BaseException, str]] = []
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            message = str(current).strip()
            if message:
                chain.append((current, message))
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc

        for exc_obj, message in reversed(chain):
            if message not in generic_messages and not isinstance(exc_obj, generic_types):
                return message[:1000]
        if chain:
            return chain[-1][1][:1000]
        return exc.__class__.__name__

    def _bounded_operator_failure(
        self, exc: BaseException, *, max_chars: int = 500
    ) -> str:
        """Return redacted nested failure evidence suitable for durable summaries."""

        raw_message = self._operator_failure_summary(exc)
        sanitized = self._sanitize_operator_summary(
            redact_sensitive_text(raw_message)
        )
        return self._coerce_text(sanitized, max_chars=max_chars) or (
            exc.__class__.__name__
        )

    @staticmethod
    def _failure_root_cause(exc: BaseException) -> BaseException:
        """Walk the exception chain to the deepest non-generic cause."""

        generic_types = (
            exceptions.ActivityError,
            exceptions.ChildWorkflowError,
        )
        chain: list[BaseException] = []
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            chain.append(current)
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc
        for candidate in reversed(chain):
            if not isinstance(candidate, generic_types):
                return candidate
        return chain[-1] if chain else exc

    @classmethod
    def _classify_failure_category(cls, exc: BaseException) -> str:
        """Map an exception chain to one of the canonical errorCategory values.

        Categories align with `ExecutionTerminalStateInput.error_category`:
        ``user_error`` | ``integration_error`` | ``execution_error`` | ``system_error``.
        """

        # CancelledError is not a normal failure; callers must handle it before
        # invoking this helper, but classify defensively.
        if isinstance(exc, (CancelledError, asyncio.CancelledError)):
            return "execution_error"

        root = cls._failure_root_cause(exc)

        # Inspect ApplicationError.type when present (set by activities raising
        # typed errors per docs/Temporal/ErrorTaxonomy.md).
        application_types: list[str] = []
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            if isinstance(current, exceptions.ApplicationError):
                raw_type = getattr(current, "type", None)
                if isinstance(raw_type, str) and raw_type.strip():
                    application_types.append(raw_type.strip())
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc

        user_error_types = {"INVALID_INPUT"}
        integration_error_types = {
            "UnsupportedStatus",
            "ProfileResolutionError",
            "SlotAcquisitionTimeout",
            "RATE_LIMITED",
        }
        system_error_types = {"WORKER_CAPABILITY_UNAVAILABLE"}
        for app_type in reversed(application_types):
            if app_type in user_error_types:
                return "user_error"
            if app_type in integration_error_types:
                return "integration_error"
            if app_type in system_error_types:
                return "system_error"

        # Heuristic fallback based on the deepest root-cause type.
        root_type_name = root.__class__.__name__
        if root_type_name in {"ValueError", "TypeError", "KeyError"}:
            # These typically indicate malformed/invalid input.
            return "user_error"
        if "Timeout" in root_type_name or "Connection" in root_type_name:
            return "integration_error"
        return "execution_error"

    @staticmethod
    def _should_propagate_agent_child_cancellation(exc: BaseException) -> bool:
        return isinstance(
            exc,
            (CancelledError, asyncio.CancelledError),
        ) and workflow.patched(RUN_PROPAGATE_AGENT_CHILD_CANCELLATION_PATCH)

    # Canonical errorCategory tokens. These are machine classifications, not
    # operator-readable messages, so they must never be surfaced verbatim as a
    # step/plan summary.
    _ERROR_CATEGORY_TOKENS = frozenset(
        {"user_error", "integration_error", "execution_error", "system_error"}
    )

    @classmethod
    def _humanize_step_failure_summary(
        cls,
        *,
        summary: str | None,
        tool_name: str,
        failure_message: str | None,
    ) -> str:
        """Return an operator-actionable step-failure summary.

        When the only text a failed step result carries is a bare error-category
        token (e.g. a runtime that timed out and emitted ``execution_error`` with
        no provider detail), surface a descriptive line instead of propagating
        the token. Otherwise the token would become the terminal summary, the
        finish-outcome reason, and the workflow's ApplicationError message —
        leaving operators with nothing actionable. The raw category is preserved
        separately via ``errorCategory``.
        """
        text = (summary or "").strip()
        if text and text not in cls._ERROR_CATEGORY_TOKENS:
            return text
        category = (failure_message or "").strip()
        if category in cls._ERROR_CATEGORY_TOKENS:
            return (
                f"{tool_name} failed ({category}); the runtime reported no "
                "diagnostic detail — inspect step diagnostics/artifacts."
            )
        return f"{tool_name} failed"

    def _format_step_failure_exception_message(
        self,
        *,
        node_id: str,
        tool_name: str,
        result_status: str,
        step_failure_summary: str,
        failure_message: str | None,
        child_workflow_id: str | None,
        diagnostics_ref: str | None,
    ) -> str:
        """Build the fail-fast ApplicationError text for a failed plan step."""

        summary = (
            self._sanitize_operator_summary(step_failure_summary)
            or step_failure_summary
            or f"{tool_name} failed"
        )
        bounded_summary = self._coerce_text(summary, max_chars=900) or (
            f"{tool_name} failed"
        )
        message = (
            f"Plan step '{node_id}' ({tool_name}) returned status "
            f"{result_status}: {bounded_summary}"
        )
        details: list[str] = []
        raw_last_error = self._coerce_text(failure_message, max_chars=240)
        last_error = (
            self._sanitize_operator_summary(raw_last_error) or raw_last_error
        )
        if last_error and last_error not in bounded_summary:
            details.append(f"lastError={last_error}")
        child_id = self._coerce_text(child_workflow_id, max_chars=400)
        if child_id:
            details.append(f"childWorkflowId={child_id}")
        diag_ref = self._coerce_text(diagnostics_ref, max_chars=400)
        if diag_ref:
            details.append(f"diagnosticsRef={diag_ref}")
        if details:
            message = f"{message} ({'; '.join(details)})"
        return self._coerce_text(message, max_chars=1200) or message

    def _failure_diagnostic_from_exception(
        self,
        exc: BaseException,
        *,
        stage: str | None = None,
        step_id: str | None = None,
        step_title: str | None = None,
        source: str | None = None,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
    ) -> dict[str, Any]:
        """Build a bounded, redacted failure diagnostic from a failure chain.

        The returned dict is intentionally small and free of secrets so it
        can flow through the workflow's finish-summary contract and the
        terminal-state activity without leaking credential-bearing payloads.
        """

        raw_message = self._operator_failure_summary(exc)
        sanitized = self._sanitize_operator_summary(raw_message) or raw_message
        bounded_message = self._coerce_text(sanitized, max_chars=1000) or (
            exc.__class__.__name__
        )
        category = self._classify_failure_category(exc)
        root = self._failure_root_cause(exc)

        diagnostic: dict[str, Any] = {
            "stage": self._coerce_text(stage or self._state, max_chars=80),
            "category": category,
            "source": self._coerce_text(source, max_chars=40) or "workflow",
            "stepId": self._coerce_text(step_id, max_chars=120),
            "stepTitle": self._coerce_text(step_title, max_chars=200),
            "childWorkflowId": self._coerce_text(child_workflow_id, max_chars=400),
            "message": bounded_message,
            "rootCauseType": self._coerce_text(
                root.__class__.__name__, max_chars=80
            ),
            "diagnosticsRef": self._coerce_text(diagnostics_ref, max_chars=400),
        }
        current: BaseException | None = exc
        for _ in range(20):
            if current is None:
                break
            if (
                isinstance(current, exceptions.ApplicationError)
                and getattr(current, "type", None)
                == "WORKER_CAPABILITY_UNAVAILABLE"
            ):
                diagnostic.update(
                    {
                        "reasonCode": "worker_capability_unavailable",
                        "agentExecutionLaunched": False,
                    }
                )
                details = getattr(current, "details", ()) or ()
                detail = details[0] if details and isinstance(details[0], Mapping) else {}
                for source_key, target_key in (
                    ("workflowType", "workflowType"),
                    ("taskQueue", "taskQueue"),
                    ("registryFingerprint", "registryFingerprint"),
                    ("observedWorkerBuilds", "observedWorkerBuilds"),
                ):
                    if detail.get(source_key) is not None:
                        diagnostic[target_key] = detail[source_key]
                break
            next_exc = getattr(current, "cause", None)
            if not isinstance(next_exc, BaseException):
                next_exc = current.__cause__
            current = next_exc
        # Drop empty optional keys to keep the structure compact.
        return {key: value for key, value in diagnostic.items() if value is not None}

    def _record_failure_diagnostic(
        self,
        exc: BaseException,
        *,
        stage: str | None = None,
        step_id: str | None = None,
        step_title: str | None = None,
        source: str | None = None,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
    ) -> dict[str, Any]:
        """Capture a failure diagnostic on the workflow if none is set yet."""

        diagnostic = self._failure_diagnostic_from_exception(
            exc,
            stage=stage,
            step_id=step_id,
            step_title=step_title,
            source=source,
            child_workflow_id=child_workflow_id,
            diagnostics_ref=diagnostics_ref,
        )
        # First failure wins: keep the deepest available root cause and avoid
        # later generic wrapping handlers from overwriting it.
        if self._failure_diagnostic is None:
            self._failure_diagnostic = diagnostic
        return diagnostic

    def _record_step_execution_exception(
        self,
        exc: BaseException,
        *,
        logical_step_id: str,
        tool_name: str,
        source: str,
        updated_at: datetime,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
    ) -> dict[str, Any]:
        """Record step-scoped terminal failure evidence for raised executions."""

        diagnostic = self._record_failure_diagnostic(
            exc,
            stage=self._state,
            step_id=logical_step_id,
            step_title=tool_name,
            source=source,
            child_workflow_id=child_workflow_id,
            diagnostics_ref=diagnostics_ref,
        )
        self._mark_step_terminal(
            logical_step_id,
            status="failed",
            updated_at=updated_at,
            summary=diagnostic["message"],
            last_error=diagnostic["category"],
        )
        return diagnostic

    def _record_result_failure_diagnostic(
        self,
        *,
        stage: str | None,
        category: str | None,
        source: str,
        step_id: str,
        step_title: str,
        message: str,
        child_workflow_id: str | None = None,
        diagnostics_ref: str | None = None,
        terminal_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Capture a failure diagnostic from a completed-but-failed step result."""

        normalized_category = self._coerce_text(category, max_chars=80)
        if normalized_category not in {
            "user_error",
            "integration_error",
            "execution_error",
            "system_error",
        }:
            normalized_category = "execution_error"
        sanitized = self._sanitize_operator_summary(message) or message
        diagnostic: dict[str, Any] = {
            "stage": self._coerce_text(stage or self._state, max_chars=80),
            "category": normalized_category,
            "source": self._coerce_text(source, max_chars=40) or "workflow",
            "stepId": self._coerce_text(step_id, max_chars=120),
            "stepTitle": self._coerce_text(step_title, max_chars=200),
            "childWorkflowId": self._coerce_text(child_workflow_id, max_chars=400),
            "message": self._coerce_text(sanitized, max_chars=1000)
            or "plan step failed",
            "rootCauseType": "AgentRunResult"
            if source == "child_workflow"
            else "ActivityResult",
            "diagnosticsRef": self._coerce_text(diagnostics_ref, max_chars=400),
        }
        compact = {key: value for key, value in diagnostic.items() if value is not None}
        if isinstance(terminal_evidence, Mapping):
            for key in (
                "failureCode",
                "terminalContractId",
                "terminalContractMissingEvidence",
                "queuedChildCount",
                "queuedChildren",
            ):
                value = terminal_evidence.get(key)
                if value is not None:
                    compact[key] = value
        if self._failure_diagnostic is None:
            self._failure_diagnostic = compact
        return compact

    def __init__(self) -> None:
        self._state = STATE_INITIALIZING
        self._owner_type: Optional[str] = None
        self._owner_id: Optional[str] = None
        self._workflow_type: Optional[str] = None
        self._entry: Optional[str] = None
        self._repo: Optional[str] = None
        self._integration_label: Optional[str] = None
        self._integration: Optional[str] = None
        self._target_runtime: Optional[str] = None
        self._target_skill: Optional[str] = None
        self._runtime_inheritance_parameters: dict[str, Any] = {}
        self._close_status: Optional[str] = None
        self._title: Optional[str] = None
        self._summary: str = "Execution initialized."
        self._correlation_id: Optional[str] = None
        self._pull_request_url: Optional[str] = None
        self._publish_status: Optional[str] = None
        self._publish_reason: Optional[str] = None
        self._publish_context: dict[str, Any] = {}
        self._canonical_no_commit_outcome_enabled: bool = False
        self._authoritative_publish_outcome_enabled: bool = False
        self._publish_repair_attempts: int = 0
        self._operator_summary: Optional[str] = None
        self._last_step_id: Optional[str] = None
        self._last_step_summary: Optional[str] = None
        self._plan_blocked_message: Optional[str] = None
        self._moonspec_gate_verdict: Optional[str] = None
        self._moonspec_gate_reason: Optional[str] = None
        self._moonspec_environment_blocked_publish_action_snapshot: str = "fail"
        self._moonspec_draft_publication_reason: Optional[str] = None
        # A valid verifier can stop workflow routing without fabricating a
        # failed execution.  This compact evidence is added to incident output.
        self._workflow_control_stop: dict[str, Any] | None = None
        self._last_diagnostics_ref: Optional[str] = None
        # Bounded, redacted structured failure diagnostic captured at the
        # failure boundary. Surfaced in reports/run_summary.json and reused by
        # terminal-state recording so operators see the deepest root cause
        # instead of generic Temporal wrappers.
        self._failure_diagnostic: Optional[dict[str, Any]] = None
        self._merge_automation_disposition: Optional[str] = None
        self._merge_automation_head_sha: Optional[str] = None
        self._gated_continuation_request: Optional[dict[str, Any]] = None
        self._gated_continuation_execution_ref: Optional[str] = None
        self._report_created: bool = False
        self._report_ref: Optional[str] = None
        # MM-880: compact reference to the versioned ResiliencePolicy envelope
        # compiled for this run, attached before step execution begins.
        self._resilience_policy_ref: Optional[dict[str, Any]] = None
        # MM-880: provider profile id that governed the run-level policy, plus a
        # per-step / per-profile policy cache. Steps whose resolved provider
        # profile differs from the run-level one reference a policy compiled with
        # that profile's cooldown/rate-limit values rather than the run-level
        # policy.
        self._run_resilience_profile_id: Optional[str] = None
        self._step_resilience_policy_refs: dict[str, dict[str, Any]] = {}
        self._resilience_policy_refs_by_profile: dict[str, dict[str, Any]] = {}
        # MM-884: cost-attribution settings (runtime/model/effort/costCenter/
        # budgetRef) captured from the run-level resilience policy envelope, so
        # the incident reconstruction manifest can name the cost dimensions that
        # governed the run alongside observed cost where available.
        self._cost_attribution_settings: dict[str, Any] | None = None
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
        # Per-prerequisite tracking under wait-through-rerun. failureCount
        # increments on each distinct non-success terminal observation;
        # lastFailedAt records the most recent failure resolved-at timestamp
        # for the prerequisite. Both are surfaced in dependency outcomes for
        # operator diagnosis even after the prerequisite later succeeds.
        self._dependency_failure_counts: dict[str, int] = {}
        self._dependency_last_failed_at: dict[str, str] = {}
        self._remediation_context: dict[str, Any] = {}
        self._remediation_policy: dict[str, Any] = {}
        self._native_skill_binding_by_step: dict[str, dict[str, Any]] = {}
        self._resolved_skill_terminal_contract_by_step: dict[
            str, dict[str, Any]
        ] = {}

        # Artifact refs
        self._input_ref: Optional[str] = None
        self._plan_ref: Optional[str] = None
        self._logs_ref: Optional[str] = None
        self._summary_ref: Optional[str] = None
        self._finish_summary: dict[str, Any] | None = None
        self._recovery_source: dict[str, Any] | None = None
        self._recovery_failed_step_id: str | None = None
        self._recovery_workspace: dict[str, Any] = {}
        self._recovery_workspace_restored_ref: str | None = None
        self._checkpoint_recovery_contract: CheckpointRecoveryContract | None = None
        self._checkpoint_recovery_state: dict[str, Any] | None = None
        # Compact record of a resume-path checkpoint validation/restoration
        # failure (failureCode + checkpointRef), captured before the failure is
        # raised so the failed-run recovery manifest reports the real degraded
        # checkpoint outcome and blocks resume instead of silently full-rerunning.
        self._recovery_checkpoint_validation_failure: dict[str, Any] | None = None
        # Compact reference to the failed-run recovery manifest emitted before
        # terminal failure is reported (MM-881).
        self._recovery_manifest_ref: str | None = None
        self._recovery_manifest_summary: dict[str, Any] | None = None
        # MM-884: the built failed-run recovery manifest model, reused by the
        # incident reconstruction path so side-effect dispositions and the
        # checkpoint restore candidate are not recomputed or duplicated.
        self._recovery_manifest_model: Any = None
        # MM-884: compact reference + summary for the incident reconstruction
        # manifest correlating policy, provider, failed step, progress, changes,
        # side effects, checkpoint, cost, trace spans, logs, and artifacts.
        self._incident_reconstruction_ref: str | None = None
        self._incident_reconstruction_summary: dict[str, Any] | None = None
        # MM-884: sanitized provider failure envelope and observed cost captured
        # at the failure boundary (first failure wins) so the incident manifest
        # can correlate the provider/credential source and per-run cost where the
        # runtime reported it.
        self._provider_failure_envelope: dict[str, Any] | None = None
        self._observed_cost: dict[str, Any] | None = None
        self._prepared_artifact_refs: list[str] = []
        self._step_checkpoint_refs: dict[str, str] = {}
        self._previous_step_checkpoint_refs: dict[str, str] = {}
        self._step_checkpoint_refs_by_boundary: dict[str, dict[str, str]] = {}
        self._step_workspace_capture_inputs: dict[str, dict[str, Any]] = {}
        self._step_checkpoint_capture_outcomes: dict[str, dict[str, Any]] = {}
        self._step_external_agent_ids: dict[str, str] = {}
        self._step_execution_launch_blocks: set[str] = set()
        self._fresh_source_step_execution_attempts: set[str] = set()
        self._step_dependency_effects: dict[str, dict[str, Any]] = {}
        self._step_terminal_dispositions: dict[str, str] = {}
        self._step_execution_context_projections: dict[
            tuple[str, int], dict[str, Any]
        ] = {}
        self._step_execution_retrieval_manifest_artifacts: dict[
            tuple[str, int], dict[str, Any]
        ] = {}
        self._step_execution_branch_projections: dict[
            tuple[str, int], dict[str, Any]
        ] = {}
        self._step_execution_branch_artifact_manifests: dict[
            tuple[str, int], dict[str, Any]
        ] = {}
        # Side-effect records observed per logical step across attempts, keyed by
        # logical step id. Used to detect already-occurred non-idempotent
        # external effects when a reattempt starts (Section 11, rules 3-4).
        self._step_side_effect_records: dict[str, list[dict[str, Any]]] = {}
        # Subjects (prior-effect identities) already compensated, so a given
        # non-idempotent effect is reconciled at most once across reattempts.
        self._step_compensated_subjects: dict[str, set[str]] = {}
        # Observable compensation plan for the latest reattempt of each step.
        self._step_reattempt_compensations: dict[str, dict[str, Any]] = {}

        # State tracking
        self._paused: bool = False
        self._pause_resume_transition_in_progress: bool = False
        self._awaiting_external: bool = False
        self._waiting_reason: Optional[str] = None
        self._attention_required: bool = False
        self._jira_blocker_wait_active: bool = False
        self._jira_blocker_wait_skipped: bool = False
        self._jira_blocker_wait_started_at: datetime | None = None
        self._jira_blocker_wait_issue_keys: list[str] = []
        self._jira_blocker_wait_summary: str | None = None
        self._jira_blocker_wait_published_signature: str | None = None

        # Action flags
        self._cancel_requested = False
        self._approve_requested = False
        self._recovery_requested = False
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

        # Semantic "real work began" timestamp. Stamped exactly once when the
        # workflow first crosses from waiting/queue states into actual work
        # (first running step, or child run launching/running). Distinct from
        # Temporal's workflow start_time / execution_time, which fire as soon
        # as Temporal schedules the workflow even if it is awaiting a slot.
        self._started_at: datetime | None = None

        # Proposal tracking
        self._proposals_generated = 0
        self._proposals_submitted = 0
        self._proposals_delivered = 0
        self._proposals_errors: list[str] = []
        self._proposal_validation_errors: list[dict[str, Any]] = []
        self._proposal_delivery_failures: list[dict[str, Any]] = []
        self._proposal_external_links: list[dict[str, Any]] = []
        self._proposal_dedup_updates: list[dict[str, Any]] = []

        # Auth profile slot tracking for managed agent runs.
        # Set when a child AgentRun acquires a slot so the parent can
        # defensively release it if the child exits in a terminal state.
        self._assigned_profile_id: Optional[str] = None
        self._assigned_child_workflow_id: Optional[str] = None
        self._assigned_runtime_id: Optional[str] = None
        self._active_agent_child_workflow_id: Optional[str] = None
        self._active_agent_id: Optional[str] = None
        self._last_publish_repair_request: AgentExecutionRequest | None = None
        self._last_publish_repair_node_id: str | None = None
        self._codex_session_handle: Any | None = None
        self._codex_session_binding: CodexManagedSessionBinding | None = None
        self._codex_session_cleared_before_step_attempts: set[
            str | tuple[str, int]
        ] = set()
        self._trusted_issue_context: dict[str, Any] | None = None
        self._assessment_context: dict[str, Any] = {}
        self._step_ledger_rows: list[dict[str, Any]] = []
        self._step_ledger_by_id: dict[str, dict[str, Any]] = {}
        self._progress_snapshot: dict[str, Any] = {
            "total": 0,
            "pending": 0,
            "ready": 0,
            "executing": 0,
            "awaitingExternal": 0,
            "reviewing": 0,
            "completed": 0,
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

    @staticmethod
    def _coerce_retry_attempts_override(value: Any, *, field_name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{field_name} must be an integer >= 1")
        try:
            attempts = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer >= 1") from exc
        if attempts < 1:
            raise ValueError(f"{field_name} must be an integer >= 1")
        return attempts

    def _step_retry_attempts_override(
        self,
        *,
        node_inputs: Mapping[str, Any] | None = None,
        node_options: Mapping[str, Any] | None = None,
    ) -> int | None:
        if isinstance(node_options, Mapping):
            retries_override = node_options.get("retries_override")
            if isinstance(retries_override, Mapping):
                for key in ("max_attempts", "maxAttempts"):
                    if key in retries_override:
                        return self._coerce_retry_attempts_override(
                            retries_override[key],
                            field_name=f"node.options.retries_override.{key}",
                        )

        if isinstance(node_inputs, Mapping):
            for key in ("maxAttempts", "max_attempts"):
                if key in node_inputs:
                    return self._coerce_retry_attempts_override(
                        node_inputs[key],
                        field_name=f"node.inputs.{key}",
                    )
        return None

    def _execute_kwargs_for_route(
        self,
        route: TemporalActivityRoute,
        *,
        max_attempts_override: int | None = None,
    ) -> dict[str, Any]:
        retry_policy = self._retry_policy_for_route(route)
        if max_attempts_override is not None:
            retry_policy = dataclasses.replace(
                retry_policy,
                maximum_attempts=max_attempts_override,
            )
        kwargs: dict[str, Any] = {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": retry_policy,
        }
        if route.timeouts.heartbeat_timeout_seconds is not None:
            kwargs["heartbeat_timeout"] = timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            )
        return kwargs

    def _jira_blocker_recheck_retry_attempts_override(
        self,
        *,
        route: TemporalActivityRoute,
        execute_payload: Mapping[str, Any],
        step_retry_overrides_enabled: bool,
    ) -> int | None:
        if step_retry_overrides_enabled:
            invocation_payload = execute_payload.get("invocation_payload")
            if isinstance(invocation_payload, Mapping):
                invocation_inputs = invocation_payload.get("inputs")
                invocation_options = invocation_payload.get("options")
                explicit_override = self._step_retry_attempts_override(
                    node_inputs=(
                        invocation_inputs
                        if isinstance(invocation_inputs, Mapping)
                        else None
                    ),
                    node_options=(
                        invocation_options
                        if isinstance(invocation_options, Mapping)
                        else None
                    ),
                )
                if explicit_override is not None:
                    return explicit_override

        if (
            workflow.patched(RUN_JIRA_BLOCKER_RECHECK_RETRY_FLOOR_PATCH)
            and route.retries.max_attempts < JIRA_BLOCKER_RECHECK_MIN_ACTIVITY_ATTEMPTS
        ):
            return JIRA_BLOCKER_RECHECK_MIN_ACTIVITY_ATTEMPTS
        return None

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
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        artifact_create_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "artifact.create"
        )
        artifact_write_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "artifact.write_complete"
        )
        create_payload: dict[str, Any] = {
            "principal": self._principal(),
            "name": name,
            "content_type": content_type,
        }
        if metadata_json:
            create_payload["metadata_json"] = dict(metadata_json)
        artifact_create_result = await workflow.execute_activity(
            "artifact.create",
            create_payload,
            **self._execute_kwargs_for_route(artifact_create_route),
        )
        if isinstance(artifact_create_result, (list, tuple)):
            artifact_ref = artifact_create_result[0] if artifact_create_result else None
        else:
            artifact_ref = artifact_create_result
        artifact_id = (
            self._get_from_result(artifact_ref, "artifact_id")
            or self._get_from_result(artifact_ref, "artifactId")
            or ""
        )
        if not artifact_id:
            raise ValueError(f"artifact.create returned no artifact_id for {name}")
        if not workflow.patched(RUN_JSON_ARTIFACT_WRITE_COMPLETE_PATCH):
            return str(artifact_id)
        await execute_typed_activity(
            "artifact.write_complete",
            ArtifactWriteCompleteInput(
                principal=self._principal(),
                artifact_id=artifact_id,
                payload=json.dumps(payload).encode("utf-8"),
                content_type=content_type,
            ),
            **self._execute_kwargs_for_route(artifact_write_route),
        )
        return str(artifact_id)

    async def _record_step_execution_manifest(
        self,
        logical_step_id: str,
        *,
        phase: str,
        updated_at: datetime,
        reason: str,
        status: str | None = None,
        terminal_disposition: str | None = None,
        summary: str | None = None,
        input_refs: Sequence[str] = (),
        execution: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
    ) -> str | None:
        attempt = self._step_execution_for(logical_step_id)
        if attempt is None or attempt <= 0:
            return None
        if phase not in {"start", "launch_blocked", "terminal"}:
            raise ValueError(f"unsupported step execution manifest phase: {phase}")
        identity = StepExecutionIdentityModel(
            workflowId=workflow.info().workflow_id,
            runId=workflow.info().run_id,
            logicalStepId=logical_step_id,
            executionOrdinal=attempt,
        )
        branch_metadata = self._step_execution_branch_metadata(
            logical_step_id,
            attempt=attempt,
        )
        manifest_reason = "checkpoint_branch" if branch_metadata else reason
        step_execution_id = build_step_execution_id(identity)
        idempotency_key = build_step_execution_idempotency_key(identity, "manifest")
        source_execution_ordinal = self._step_execution_source_identity(
            logical_step_id,
            attempt=attempt,
        )
        lineage = self._step_execution_lineage(
            logical_step_id,
            reason=manifest_reason,
            source_execution_ordinal=source_execution_ordinal,
        )
        workspace = self._step_execution_workspace(
            logical_step_id,
            attempt=attempt,
            source_execution_ordinal=source_execution_ordinal,
        )
        execution_payload = {
            **self._step_execution_compact_execution_refs(logical_step_id),
            **(execution or {}),
        }
        if branch_metadata:
            execution_payload.setdefault(
                "checkpointBranchTurn",
                dict(branch_metadata),
            )
        # MM-880: stamp the versioned ResiliencePolicy reference governing this
        # step so it can be traced to the exact policy values that applied.
        # Steps whose resolved provider profile differs from the run-level one
        # reference a policy compiled with that node's profile; all others fall
        # back to the run-level policy.
        policy_ref = (
            self._step_resilience_policy_refs.get(logical_step_id)
            or self._resilience_policy_ref
        )
        if policy_ref:
            execution_payload.setdefault(
                "resiliencePolicyRef",
                dict(policy_ref),
            )
        # MM-884: stamp a stable correlation (trace) ref so this step execution
        # can be joined to the run's incident reconstruction and to its
        # trace/log slice through the same trace id.
        if workflow.patched(RUN_INCIDENT_RECONSTRUCTION_PATCH):
            trace_ref = build_incident_trace_ref(
                workflow_id=identity.workflow_id,
                run_id=identity.run_id,
                logical_step_id=identity.logical_step_id,
                execution_ordinal=identity.execution_ordinal,
            )
            execution_payload.setdefault(
                "traceRef",
                trace_ref.model_dump(by_alias=True, mode="json", exclude_none=True),
            )
        side_effects_payload: dict[str, Any] = {}
        side_effect_records: list[Mapping[str, Any]] = []
        outputs: Mapping[str, Any] | None = None
        checks: Sequence[Mapping[str, Any]] = ()
        dependency_effects: Mapping[str, Any] | None = None
        git_effect: Mapping[str, Any] | None = None
        resolved_status = status
        resolved_terminal_disposition = terminal_disposition
        resolved_summary = summary

        if phase in {"start", "launch_blocked"}:
            canonical_step_status_vocab = workflow.patched(
                RUN_CANONICAL_STEP_STATUS_VOCAB_PATCH
            )
            compensation_plan = self._orchestrate_reattempt_compensation(
                logical_step_id,
                execution_ordinal=attempt,
                updated_at=updated_at,
            )
            if compensation_plan and compensation_plan.get("requiresCompensation"):
                side_effects_payload["reattemptCompensation"] = compensation_plan
                side_effect_records = list(compensation_plan.get("compensations", ()))
            launch_blocked = phase == "launch_blocked" or (
                self._workspace_policy_launch_blocked(workspace)
            )
            resolved_status = (
                "blocked"
                if launch_blocked
                else "executing"
                if canonical_step_status_vocab
                else "running"
            )
            if launch_blocked:
                resolved_terminal_disposition = "blocked"
                resolved_summary = "Workspace policy rejected before launch."
        else:
            if not resolved_status or not resolved_terminal_disposition:
                raise ValueError(
                    "terminal step execution manifests require status and "
                    "terminal_disposition"
                )
            git_effect = self._step_execution_git_effect(
                logical_step_id,
                terminal_disposition=resolved_terminal_disposition,
            )
            outputs = self._step_execution_compact_output_refs(logical_step_id)
            checks = list(
                (self._step_ledger_row_for(logical_step_id) or {}).get("checks")
                or []
            )
            side_effects_payload = self._step_execution_side_effects(
                logical_step_id,
                git_effect=git_effect,
            )
            dependency_effects = self._step_dependency_effects.get(
                logical_step_id,
                {"invalidatedLogicalStepIds": []},
            )

        bounded_outputs = dict(outputs or {})
        if resolved_summary is not None:
            bounded_outputs.setdefault("summary", resolved_summary[:500])
        prepared_input_refs = self._combined_step_execution_input_refs(input_refs)
        input_payload: dict[str, Any] = {}
        if prepared_input_refs:
            input_payload["preparedInputRefs"] = prepared_input_refs
        workspace_payload = dict(workspace)
        if git_effect is not None:
            workspace_payload["gitEffect"] = dict(git_effect)
        if side_effect_records:
            side_effects_payload["records"] = [
                dict(record) for record in side_effect_records
            ]
        if phase in {"start", "launch_blocked"}:
            await self._persist_step_execution_retrieval_manifest(
                logical_step_id,
                attempt=identity.execution_ordinal,
            )
            branch_artifact_manifest_ref = (
                await self._persist_step_execution_branch_artifact_manifest(
                    logical_step_id,
                    attempt=identity.execution_ordinal,
                )
            )
            if branch_artifact_manifest_ref and branch_metadata:
                checkpoint_branch_turn = dict(
                    execution_payload.get("checkpointBranchTurn") or {}
                )
                checkpoint_branch_turn["artifactManifestRef"] = (
                    branch_artifact_manifest_ref
                )
                execution_payload["checkpointBranchTurn"] = checkpoint_branch_turn

        manifest = StepExecutionManifestModel(
            stepExecutionId=step_execution_id,
            workflowId=identity.workflow_id,
            runId=identity.run_id,
            logicalStepId=identity.logical_step_id,
            executionOrdinal=identity.execution_ordinal,
            lineage=dict(lineage) if lineage is not None else None,
            branch=dict(branch_metadata) if branch_metadata else None,
            reason=manifest_reason,
            status=resolved_status,
            terminalDisposition=resolved_terminal_disposition,
            startedAt=self._step_execution_started_at(logical_step_id) or updated_at,
            updatedAt=updated_at,
            input=input_payload,
            context=self._step_execution_manifest_context_projection(
                logical_step_id,
                attempt=identity.execution_ordinal,
                reason=manifest_reason,
                execution=execution_payload,
            ),
            workspace=workspace_payload,
            execution=execution_payload,
            outputs=bounded_outputs,
            checks=checks,
            sideEffects=side_effects_payload,
            dependencyEffects=dependency_effects,
            recoverySource=self._validate_recovery_source_for_execution(),
            budget=budget,
        )
        manifest_ref = await self._write_step_execution_manifest(
            logical_step_id,
            attempt=attempt,
            manifest=manifest,
            step_execution_id=step_execution_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
        )
        if phase in {"start", "launch_blocked"} and (
            resolved_terminal_disposition == "blocked"
        ):
            self._record_workspace_policy_launch_block(
                logical_step_id,
                attempt=attempt,
                updated_at=updated_at,
                reason=str(
                    workspace.get("rejectionReason")
                    or "missing_required_checkpoint_evidence"
                ),
            )
        if phase == "terminal" and resolved_terminal_disposition:
            self._step_terminal_dispositions[logical_step_id] = (
                resolved_terminal_disposition
            )
        self._sync_progress_snapshot(updated_at=updated_at)
        return manifest_ref

    async def _persist_step_execution_retrieval_manifest(
        self,
        logical_step_id: str,
        *,
        attempt: int,
    ) -> str | None:
        cache_key = (logical_step_id, attempt)
        retrieval_artifact = self._step_execution_retrieval_manifest_artifacts.get(
            cache_key
        )
        if not isinstance(retrieval_artifact, Mapping):
            return None
        existing_ref = retrieval_artifact.get("persistedArtifactRef")
        if isinstance(existing_ref, str) and existing_ref.strip():
            return existing_ref

        payload = retrieval_artifact.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("retrieval manifest artifact payload must be an object")
        metadata = retrieval_artifact.get("metadata")
        metadata_json = dict(metadata) if isinstance(metadata, Mapping) else {}
        metadata_json.setdefault("artifact_kind", "retrieval_manifest")
        metadata_json["logicalStepId"] = logical_step_id
        metadata_json["attempt"] = attempt

        manifest_ref = await self._write_json_artifact(
            name=f"reports/retrieval_manifests/{logical_step_id}_attempt_{attempt}.json",
            payload=dict(payload),
            content_type=str(
                retrieval_artifact.get("contentType") or "application/json"
            ),
            metadata_json=metadata_json,
        )
        persisted_artifact = dict(retrieval_artifact)
        persisted_artifact["persistedArtifactRef"] = manifest_ref
        self._step_execution_retrieval_manifest_artifacts[cache_key] = (
            persisted_artifact
        )

        cached_context = self._step_execution_context_projections.get(cache_key)
        if isinstance(cached_context, Mapping):
            updated_context = dict(cached_context)
            updated_context["retrievalManifestRef"] = manifest_ref
            self._step_execution_context_projections[cache_key] = updated_context
        return manifest_ref

    async def _persist_step_execution_branch_artifact_manifest(
        self,
        logical_step_id: str,
        *,
        attempt: int,
    ) -> str | None:
        cache_key = (logical_step_id, attempt)
        branch_artifact_manifest = self._step_execution_branch_artifact_manifests.get(
            cache_key
        )
        if not isinstance(branch_artifact_manifest, Mapping):
            return None
        existing_ref = branch_artifact_manifest.get("persistedArtifactRef")
        if isinstance(existing_ref, str) and existing_ref.strip():
            return existing_ref

        payload = {
            key: value
            for key, value in dict(branch_artifact_manifest).items()
            if key != "persistedArtifactRef"
        }
        branch_id = self._coerce_text(payload.get("branchId"), max_chars=200)
        branch_turn_id = self._coerce_text(payload.get("branchTurnId"), max_chars=200)
        if not branch_id or not branch_turn_id:
            raise ValueError(
                "checkpoint branch artifact manifest requires branchId and branchTurnId"
            )
        manifest_ref = await self._write_json_artifact(
            name=(
                "reports/checkpoint_branches/"
                f"{self._artifact_slug(branch_id)}/turns/"
                f"{self._artifact_slug(branch_turn_id)}/artifact_manifest.json"
            ),
            payload=payload,
            content_type="application/json",
            metadata_json={
                "artifact_kind": "checkpoint_branch_turn_artifact_manifest",
                "traceability": str(payload.get("traceability") or "MM-1089"),
                "branchId": branch_id,
                "branchTurnId": branch_turn_id,
                "logicalStepId": logical_step_id,
                "attempt": attempt,
            },
        )
        persisted_artifact = dict(branch_artifact_manifest)
        persisted_artifact["persistedArtifactRef"] = manifest_ref
        self._step_execution_branch_artifact_manifests[cache_key] = persisted_artifact

        cached_context = self._step_execution_context_projections.get(cache_key)
        if isinstance(cached_context, Mapping):
            updated_context = dict(cached_context)
            updated_context["branchArtifactManifestRef"] = manifest_ref
            branch_context = dict(updated_context.get("branch") or {})
            if branch_context:
                branch_context["artifactManifestRef"] = manifest_ref
                updated_context["branch"] = branch_context
            self._step_execution_context_projections[cache_key] = updated_context
        return manifest_ref

    async def _write_step_execution_manifest(
        self,
        logical_step_id: str,
        *,
        attempt: int,
        manifest: StepExecutionManifestModel,
        step_execution_id: str,
        idempotency_key: str,
        updated_at: datetime,
    ) -> str | None:
        try:
            manifest_ref = await self._write_json_artifact(
                name=(
                    f"reports/step_executions/{logical_step_id}_attempt_{attempt}.json"
                ),
                payload=manifest.model_dump(
                    by_alias=True,
                    exclude_none=True,
                    mode="json",
                ),
                content_type=STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
                metadata_json={
                    "artifact_kind": "step_execution_manifest",
                    "stepExecutionId": step_execution_id,
                    "logicalStepId": logical_step_id,
                    "attempt": attempt,
                    "idempotencyKey": idempotency_key,
                },
            )
        except Exception as exc:
            if exc.__class__.__name__ == "_NotInWorkflowEventLoopError":
                return None
            if (
                isinstance(exc, ValueError)
                and str(exc).startswith("artifact.create returned no artifact_id")
            ):
                self._get_logger().warning(
                    "Skipping step-execution manifest without artifact id.",
                    extra={
                        "event": "step_execution_manifest_missing_artifact_id",
                        "logical_step_id": logical_step_id,
                        "attempt": attempt,
                    },
                )
                return None
            raise
        for row in self._step_ledger_rows:
            if row.get("logicalStepId") != logical_step_id:
                continue
            refs = dict(row.get("refs") or {})
            history = [
                ref
                for ref in refs.get("stepExecutionManifestRefs", [])
                if isinstance(ref, str) and ref.strip()
            ]
            if manifest_ref not in history:
                history.append(manifest_ref)
            refs["latestStepExecutionManifestRef"] = manifest_ref
            refs["stepExecutionManifestRefs"] = history
            update_step_row(
                self._step_ledger_rows,
                logical_step_id,
                updated_at=updated_at,
                refs=refs,
            )
            mark_step_execution_manifest_evidence(
                self._step_ledger_rows,
                logical_step_id,
                updated_at=updated_at,
                step_execution_manifest_ref=manifest_ref,
            )
            self._sync_progress_snapshot(updated_at=updated_at)
            return manifest_ref
        return manifest_ref

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

    def _recovery_source_text(
        self,
        source: Mapping[str, Any],
        *keys: str,
    ) -> str:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _recovery_source_int(
        self,
        source: Mapping[str, Any],
        *keys: str,
    ) -> int | None:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value >= 1:
                return value
            if isinstance(value, str) and value.strip():
                try:
                    parsed = int(value.strip())
                except ValueError:
                    continue
                if parsed >= 1:
                    return parsed
        return None

    def _step_execution_source_identity(
        self,
        logical_step_id: str,
        *,
        attempt: int,
    ) -> dict[str, Any] | None:
        if attempt > 1:
            info = workflow.info()
            return {
                "workflowId": info.workflow_id,
                "runId": info.run_id,
                "logicalStepId": logical_step_id,
                "executionOrdinal": attempt - 1,
            }
        recovery_source = self._recovery_source
        if (
            not isinstance(recovery_source, Mapping)
            or logical_step_id != self._recovery_failed_step_id
        ):
            return None
        source_workflow_id = self._recovery_source_text(
            recovery_source,
            "sourceWorkflowId",
            "source_workflow_id",
        )
        source_run_id = self._recovery_source_text(
            recovery_source,
            "sourceRunId",
            "source_run_id",
        )
        source_logical_step_id = self._recovery_source_text(
            recovery_source,
            "failedStepId",
            "failed_step_id",
        )
        source_execution_ordinal = self._recovery_source_int(
            recovery_source,
            "failedStepExecution",
            "failed_step_execution",
        )
        if (
            not source_workflow_id
            or not source_run_id
            or not source_logical_step_id
            or not source_execution_ordinal
        ):
            return None
        return {
            "workflowId": source_workflow_id,
            "runId": source_run_id,
            "logicalStepId": source_logical_step_id,
            "executionOrdinal": source_execution_ordinal,
        }

    def _step_execution_lineage(
        self,
        logical_step_id: str,
        *,
        reason: str,
        source_execution_ordinal: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not source_execution_ordinal or logical_step_id != self._recovery_failed_step_id:
            return None
        return {
            "sourceWorkflowId": source_execution_ordinal["workflowId"],
            "sourceRunId": source_execution_ordinal["runId"],
            "sourceLogicalStepId": source_execution_ordinal["logicalStepId"],
            "sourceExecutionOrdinal": source_execution_ordinal["executionOrdinal"],
            "relationship": reason,
            "lineageExecutionOrdinal": int(source_execution_ordinal["executionOrdinal"])
            + (self._step_execution_for(logical_step_id) or 0),
        }

    def _step_execution_started_at(self, logical_step_id: str) -> datetime | None:
        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, Mapping):
            return None
        value = row.get("startedAt") or row.get("started_at")
        return value if isinstance(value, datetime) else None

    @staticmethod
    def _workspace_policy_launch_blocked(workspace: Mapping[str, Any]) -> bool:
        return workspace.get("evidenceAccepted") is False

    @staticmethod
    def _step_execution_launch_block_key(logical_step_id: str, attempt: int) -> str:
        return f"{logical_step_id}:{attempt}"

    def _prepare_moonspec_contract_repair_attempt(
        self,
        logical_step_id: str,
    ) -> None:
        """Allow the next read-only verifier attempt to start from source state.

        MoonSpec contract repair re-runs verification only; it does not continue
        source mutation from the previous verifier process. Managed agent runs
        already launch with fresh agent context against the workflow's durable
        branch, so requiring a mutable-workspace checkpoint here prevents the
        bounded repair from running without adding safety.
        """

        current_attempt = self._step_execution_for(logical_step_id) or 0
        next_attempt = current_attempt + 1
        self._fresh_source_step_execution_attempts.add(
            self._step_execution_launch_block_key(logical_step_id, next_attempt)
        )

    def _step_execution_uses_fresh_source(
        self,
        logical_step_id: str,
        *,
        attempt: int,
    ) -> bool:
        return (
            self._step_execution_launch_block_key(logical_step_id, attempt)
            in self._fresh_source_step_execution_attempts
        )

    def _record_workspace_policy_launch_block(
        self,
        logical_step_id: str,
        *,
        attempt: int,
        updated_at: datetime,
        reason: str,
    ) -> None:
        self._step_execution_launch_blocks.add(
            self._step_execution_launch_block_key(logical_step_id, attempt)
        )
        self._mark_step_terminal(
            logical_step_id,
            status="failed",
            updated_at=updated_at,
            summary="Workspace policy rejected before launch.",
            last_error=reason,
        )

    def _is_step_execution_launch_blocked(
        self,
        logical_step_id: str,
        *,
        attempt: int,
    ) -> bool:
        return (
            self._step_execution_launch_block_key(logical_step_id, attempt)
            in self._step_execution_launch_blocks
        )

    def _step_execution_workspace(
        self,
        logical_step_id: str,
        *,
        attempt: int,
        source_execution_ordinal: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if source_execution_ordinal and logical_step_id == self._recovery_failed_step_id:
            resolved_policy = resolve_checkpoint_policy(
                boundary="before_recovery_restoration",
                recovery_source=self._recovery_source
                if isinstance(self._recovery_source, Mapping)
                else None,
                runtime_kind=self._target_runtime,
            )
            workspace = workspace_policy_metadata(
                policy=resolved_policy.workspace_policy,
                checkpoint_ref=self._recovery_workspace_restored_ref,
                checkpoint_valid=bool(self._recovery_workspace_restored_ref),
            )
            workspace.update(
                {
                    "sourceExecutionOrdinal": dict(source_execution_ordinal),
                }
            )
            self._apply_step_checkpoint_manifest_refs(
                workspace,
                self._step_checkpoint_refs_by_boundary.get(logical_step_id, {}),
            )
            return workspace
        checkpoint_ref = self._step_checkpoint_refs.get(
            logical_step_id
        ) or self._previous_step_checkpoint_refs.get(logical_step_id)
        boundary_refs = self._step_checkpoint_refs_by_boundary.get(logical_step_id, {})
        if attempt > 1 and not self._step_execution_uses_fresh_source(
            logical_step_id,
            attempt=attempt,
        ):
            workspace = workspace_policy_metadata(
                policy="continue_from_previous_execution",
                checkpoint_ref=checkpoint_ref,
                checkpoint_valid=bool(checkpoint_ref) if checkpoint_ref else None,
            )
            workspace["sourceExecutionOrdinal"] = dict(source_execution_ordinal) if source_execution_ordinal else None
            self._apply_step_checkpoint_manifest_refs(workspace, boundary_refs)
            return workspace
        workspace = workspace_policy_metadata(
            policy="fresh_branch_from_source",
            checkpoint_ref=checkpoint_ref,
            checkpoint_valid=None,
        )
        if attempt > 1:
            workspace["sourceExecutionOrdinal"] = (
                dict(source_execution_ordinal)
                if source_execution_ordinal
                else None
            )
        self._apply_step_checkpoint_manifest_refs(workspace, boundary_refs)
        return workspace

    @staticmethod
    def _apply_step_checkpoint_manifest_refs(
        workspace: dict[str, Any],
        boundary_refs: Mapping[str, str],
    ) -> None:
        before_ref = str(boundary_refs.get("before_execution") or "").strip()
        after_ref = str(boundary_refs.get("after_execution") or "").strip()
        if before_ref:
            workspace["checkpointBeforeRef"] = before_ref
        if after_ref:
            workspace["checkpointAfterRef"] = after_ref

    def _step_execution_git_effect(
        self,
        logical_step_id: str,
        *,
        terminal_disposition: str,
    ) -> dict[str, Any]:
        outputs = self._step_execution_compact_output_refs(logical_step_id)
        row = self._step_ledger_row_for(logical_step_id)
        checkpoint_ref = None
        if isinstance(row, Mapping):
            raw_checkpoint = row.get("stateCheckpointRef")
            if isinstance(raw_checkpoint, str) and raw_checkpoint.strip():
                checkpoint_ref = raw_checkpoint.strip()
        disposition = (
            "accepted"
            if terminal_disposition == "accepted"
            else (
                "candidate"
                if terminal_disposition in {"retryable", "needs_human"}
                else (
                    "discarded"
                    if terminal_disposition in {"discarded", "failed_unrecoverable"}
                    else str(terminal_disposition or "none")
                )
            )
        )
        typed_artifact_ref = (
            outputs.get("primaryRef")
            or outputs.get("summaryRef")
            or outputs.get("logsRef")
            or outputs.get("stdoutRef")
        )
        try:
            return git_effect_metadata(
                disposition=disposition,  # type: ignore[arg-type]
                workspace_checkpoint_ref=checkpoint_ref,
                typed_artifact_ref=typed_artifact_ref,
                no_change_accepted=(
                    terminal_disposition == "accepted" and not typed_artifact_ref
                ),
            )
        except ValueError:
            return {
                "disposition": "candidate",
                "workspaceCheckpointRef": checkpoint_ref,
                "acceptedOutputPresent": False,
                "rejectionReason": "missing_accepted_output",
            }

    def _step_execution_compact_execution_refs(
        self,
        logical_step_id: str,
    ) -> dict[str, Any]:
        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, Mapping):
            return {}
        refs = row.get("refs")
        artifacts = row.get("artifacts")
        execution: dict[str, Any] = {}
        tool = row.get("tool")
        if isinstance(tool, Mapping) and str(tool.get("type") or "").strip() == (
            "agent_runtime"
        ):
            agent_id = str(tool.get("name") or "").strip()
            agent_kind = self._agent_kind_for_id(agent_id)
            execution["runtimeContextPolicy"] = (
                "fresh_agent_run"
                if agent_kind == "managed"
                else "external_provider_continuation"
            )
            execution_ordinal = self._step_execution_for(logical_step_id) or 1
            if agent_kind == "managed":
                reset = self._managed_reattempt_session_reset_evidence(
                    logical_step_id,
                    agent_id=agent_id,
                    execution_ordinal=execution_ordinal,
                )
                if reset:
                    execution["runtimeSessionReset"] = reset
            else:
                execution["externalProviderContinuation"] = (
                    self._external_provider_continuation_evidence(
                        logical_step_id,
                        execution_ordinal=execution_ordinal,
                    )
                )
        if isinstance(refs, Mapping):
            for source_key, target_key in (
                ("childWorkflowId", "childWorkflowId"),
                ("childRunId", "childRunId"),
                ("agentRunId", "agentRunId"),
            ):
                value = refs.get(source_key)
                if isinstance(value, str) and value.strip():
                    execution[target_key] = value.strip()
        if isinstance(artifacts, Mapping):
            diagnostics_ref = artifacts.get("runtimeDiagnostics")
            if isinstance(diagnostics_ref, str) and diagnostics_ref.strip():
                execution["diagnosticsRef"] = diagnostics_ref.strip()
        return execution

    def _managed_reattempt_session_reset_evidence(
        self,
        logical_step_id: str,
        *,
        agent_id: str,
        execution_ordinal: int,
    ) -> dict[str, Any] | None:
        if execution_ordinal <= 1:
            return None
        source_execution_ordinal = self._step_execution_source_identity(
            logical_step_id,
            attempt=execution_ordinal,
        )
        evidence: dict[str, Any] = {
            "requestedPolicy": "reuse_session_new_epoch",
            "resolvedPolicy": "fresh_agent_run",
            "semantics": "new_epoch_cleared_context",
            "clearContext": True,
            "newEpoch": True,
            "runtimeId": agent_id,
        }
        if source_execution_ordinal:
            evidence["sourceExecutionOrdinal"] = source_execution_ordinal
        previous_checkpoint_ref = self._previous_step_checkpoint_refs.get(
            logical_step_id
        )
        if previous_checkpoint_ref:
            evidence["availableCheckpointEvidence"] = {
                "stateCheckpointRef": previous_checkpoint_ref
            }
        else:
            evidence["availableCheckpointEvidence"] = {"available": False}
        return evidence

    def _external_provider_continuation_evidence(
        self,
        logical_step_id: str,
        *,
        execution_ordinal: int,
        context_bundle_ref: str | None = None,
        prepared_input_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        identity = StepExecutionIdentityModel(
            workflowId=workflow.info().workflow_id,
            runId=workflow.info().run_id,
            logicalStepId=logical_step_id,
            executionOrdinal=execution_ordinal,
        )
        context_refs: dict[str, Any] = {}
        if context_bundle_ref:
            context_refs["contextBundleRef"] = context_bundle_ref
        prepared_refs = [
            str(ref).strip() for ref in prepared_input_refs if str(ref).strip()
        ]
        if not prepared_refs:
            prepared_refs = list(self._prepared_artifact_refs)
        if prepared_refs:
            context_refs["preparedInputRefs"] = prepared_refs

        row = self._step_ledger_row_for(logical_step_id)
        row_state_checkpoint_ref = (
            str(row.get("stateCheckpointRef") or "").strip()
            if isinstance(row, Mapping)
            else ""
        )
        checkpoint_ref = self._step_checkpoint_refs.get(
            logical_step_id
        ) or row_state_checkpoint_ref or self._previous_step_checkpoint_refs.get(
            logical_step_id
        )
        checkpoint_evidence: dict[str, Any] = {}
        if checkpoint_ref:
            checkpoint_evidence["stateCheckpointRef"] = checkpoint_ref
        if isinstance(row, Mapping):
            for source_key, target_key in (
                ("workspaceCheckpointRef", "workspaceCheckpointRef"),
                ("stepCheckpointRef", "stepCheckpointRef"),
            ):
                value = row.get(source_key)
                if isinstance(value, str) and value.strip():
                    checkpoint_evidence[target_key] = value.strip()
        if not checkpoint_evidence:
            checkpoint_evidence["available"] = False
        records = [
            dict(record)
            for record in self._step_side_effect_records.get(logical_step_id, ())
        ]
        return {
            "attemptIdentity": {
                "workflowId": identity.workflow_id,
                "runId": identity.run_id,
                "logicalStepId": identity.logical_step_id,
                "executionOrdinal": identity.execution_ordinal,
                "stepExecutionId": build_step_execution_id(identity),
            },
            "contextRefs": context_refs,
            "knownSideEffects": {"records": records} if records else {"records": []},
            "checkpointEvidence": checkpoint_evidence,
        }

    def _step_execution_compact_output_refs(
        self,
        logical_step_id: str,
    ) -> dict[str, Any]:
        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, Mapping):
            return {}
        artifacts = row.get("artifacts")
        if not isinstance(artifacts, Mapping):
            return {}
        outputs: dict[str, Any] = {}
        for source_key, target_key in (
            ("outputSummary", "summaryRef"),
            ("outputPrimary", "primaryRef"),
            ("runtimeStdout", "stdoutRef"),
            ("runtimeStderr", "stderrRef"),
            ("runtimeMergedLogs", "logsRef"),
            ("externalStateRef", "externalStateRef"),
        ):
            value = artifacts.get(source_key)
            if isinstance(value, str) and value.strip():
                outputs[target_key] = value.strip()
        return outputs

    def _proposal_step_output_refs(
        self,
        logical_step_id: str,
    ) -> dict[str, Any]:
        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, Mapping):
            return {}
        artifacts = row.get("artifacts")
        if not isinstance(artifacts, Mapping):
            return {}
        outputs = self._step_execution_compact_output_refs(logical_step_id)
        for source_key, target_key in (
            ("runtimeDiagnostics", "diagnosticsRef"),
            ("providerSnapshot", "providerSnapshotRef"),
            ("stepExecutionManifestRef", "stepExecutionManifestRef"),
        ):
            value = artifacts.get(source_key)
            if isinstance(value, str) and value.strip():
                outputs[target_key] = value.strip()
        refs = row.get("refs")
        if isinstance(refs, Mapping):
            manifest_refs = refs.get("stepExecutionManifestRefs")
            if isinstance(manifest_refs, Sequence) and not isinstance(
                manifest_refs, (str, bytes)
            ):
                compact_manifest_refs = [
                    item.strip()
                    for item in manifest_refs
                    if isinstance(item, str) and item.strip()
                ]
                if compact_manifest_refs:
                    outputs["stepExecutionManifestRefs"] = compact_manifest_refs
        return outputs

    def _proposal_generation_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return redacted compact metadata for proposal generation activities."""

        task_node = parameters.get("workflow")
        if not isinstance(task_node, Mapping):
            task_node = parameters.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}

        compact_task: dict[str, Any] = {}
        for key in (
            "proposalTitle",
            "proposalIdea",
            "suggestedTitle",
            "titleSuggestion",
            "recommendedNextAction",
            "nextAction",
            "nextStep",
            "next_step",
        ):
            value = self._coerce_text(task.get(key), max_chars=500)
            if value:
                compact_task[key] = self._sanitize_operator_summary(value) or value

        compact_sections = {
            "runtime": (
                "mode",
                "model",
                "effort",
                "profileId",
                "providerProfile",
                "executionProfileRef",
            ),
            "git": ("branch", "startingBranch", "targetBranch", "baseBranch"),
            "publish": ("mode", "enabled", "strategy"),
        }
        for key, allowed_keys in compact_sections.items():
            value = self._proposal_compact_mapping(
                task.get(key),
                allowed_keys=allowed_keys,
                max_chars=200,
            )
            if value:
                compact_task[key] = value
        for key in ("skill", "tool", "skills"):
            value = self._proposal_compact_selector_metadata(task.get(key))
            if value:
                compact_task[key] = value

        compact_steps = self._proposal_compact_steps(task.get("steps"))
        if compact_steps:
            compact_task["steps"] = compact_steps

        authored_presets = task.get("authoredPresets")
        if isinstance(authored_presets, Sequence) and not isinstance(
            authored_presets, (str, bytes)
        ):
            compact_presets: list[dict[str, Any]] = []
            for index, preset in enumerate(authored_presets[:5]):
                if not isinstance(preset, Mapping):
                    continue
                compact_preset: dict[str, Any] = {}
                for key in ("id", "name", "source", "presetId", "presetDigest"):
                    value = self._coerce_text(preset.get(key), max_chars=160)
                    if value:
                        compact_preset[key] = value
                source_ref = self._coerce_text(
                    preset.get("sourceRef") or preset.get("artifactRef"),
                    max_chars=400,
                )
                if source_ref:
                    compact_preset["sourceRef"] = source_ref
                if compact_preset:
                    compact_preset["index"] = index
                    compact_presets.append(compact_preset)
            if compact_presets:
                compact_task["authoredPresets"] = compact_presets

        return {"workflow": compact_task} if compact_task else {}

    def _proposal_compact_mapping(
        self,
        value: object,
        *,
        allowed_keys: Sequence[str],
        max_chars: int,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        compact: dict[str, Any] = {}
        for key in allowed_keys:
            item = value.get(key)
            if isinstance(item, (bool, int, float)):
                compact[key] = item
                continue
            text = self._coerce_text(item, max_chars=max_chars)
            if text:
                compact[key] = text
        return compact

    def _proposal_compact_steps(self, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return []
        compact_steps: list[dict[str, Any]] = []
        for step in value[:50]:
            if not isinstance(step, Mapping):
                continue
            compact_step: dict[str, Any] = {}
            for key in ("id", "title", "type"):
                text = self._coerce_text(step.get(key), max_chars=160)
                if text:
                    compact_step[key] = text
            for key in ("tool", "skill", "skills"):
                selector = self._proposal_compact_selector_metadata(step.get(key))
                if selector:
                    compact_step[key] = selector
            source = self._proposal_compact_source_metadata(step.get("source"))
            if source:
                compact_step["source"] = source
            if any(
                key in compact_step for key in ("tool", "skill", "skills", "source")
            ):
                compact_steps.append(compact_step)
        return compact_steps

    def _proposal_compact_source_metadata(self, value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        compact: dict[str, Any] = {}
        for key in (
            "kind",
            "presetId",
            "presetSlug",
            "presetDigest",
            "originalStepId",
        ):
            text = self._coerce_text(value.get(key), max_chars=160)
            if text:
                compact[key] = text
        include_path = value.get("includePath")
        if isinstance(include_path, Sequence) and not isinstance(
            include_path, (str, bytes)
        ):
            compact_include_path = [
                text
                for item in include_path[:20]
                if (text := self._coerce_text(item, max_chars=160))
            ]
            if compact_include_path:
                compact["includePath"] = compact_include_path
        return compact

    def _proposal_compact_selector_metadata(self, value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        compact: dict[str, Any] = {}
        for key in ("id", "name", "version", "source", "mode"):
            text = self._coerce_text(value.get(key), max_chars=160)
            if text:
                compact[key] = text
        for key in ("include", "exclude", "sets"):
            items = value.get(key)
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
                continue
            compact_items: list[Any] = []
            for item in items[:20]:
                if isinstance(item, str):
                    text = self._coerce_text(item, max_chars=160)
                    if text:
                        compact_items.append(text)
                    continue
                if not isinstance(item, Mapping):
                    continue
                compact_item: dict[str, Any] = {}
                for item_key in ("id", "name", "version", "source"):
                    text = self._coerce_text(item.get(item_key), max_chars=160)
                    if text:
                        compact_item[item_key] = text
                if compact_item:
                    compact_items.append(compact_item)
            if compact_items:
                compact[key] = compact_items
        resolved_ref = self._coerce_text(
            value.get("resolvedSkillsetRef")
            or value.get("resolved_skillset_ref")
            or value.get("manifestRef")
            or value.get("manifest_ref"),
            max_chars=400,
        )
        if resolved_ref:
            compact["resolvedSkillsetRef"] = resolved_ref
        return compact

    def _proposal_generation_evidence_refs(self) -> dict[str, Any]:
        refs: dict[str, Any] = {
            "inputRef": self._input_ref,
            "planRef": self._plan_ref,
            "logsRef": self._logs_ref,
            "summaryRef": self._summary_ref,
            "finishSummaryRef": self._report_ref,
        }
        if self._last_diagnostics_ref:
            refs["diagnosticsRef"] = self._last_diagnostics_ref
        if self._last_step_id:
            step_refs = self._proposal_step_output_refs(self._last_step_id)
            if step_refs:
                refs["lastStep"] = {
                    "id": self._last_step_id,
                    "outputRefs": step_refs,
                }
        return {
            key: value
            for key, value in refs.items()
            if value is not None and value != {}
        }

    def _step_execution_side_effects(
        self,
        logical_step_id: str,
        *,
        git_effect: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        records = [
            dict(record)
            for record in self._step_side_effect_records.get(logical_step_id, ())
        ]
        summary = self._step_execution_side_effect_summary(
            records,
            git_effect=git_effect,
        )
        memory_effects = [
            dict(record["memory"])
            for record in records
            if record.get("class") == "memory_update"
            and isinstance(record.get("memory"), Mapping)
        ]
        if records:
            payload: dict[str, Any] = {"records": records, "summary": summary}
            if memory_effects:
                payload["memory"] = memory_effects
            return payload
        if summary:
            payload = {"summary": summary}
            if memory_effects:
                payload["memory"] = memory_effects
            return payload
        return {}

    def _step_execution_side_effect_summary(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        git_effect: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        categories = {
            "git": 0,
            "external": 0,
            "artifact": 0,
            "publication": 0,
            "compensation": 0,
            "memory": 0,
            "retrieval": 0,
            "record": 0,
        }
        by_class: dict[str, int] = {}
        by_disposition: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        if git_effect:
            disposition = str(git_effect.get("disposition") or "").strip()
            if disposition:
                categories["git"] = 1
                by_disposition[disposition] = by_disposition.get(disposition, 0) + 1
        for record in records:
            effect_class = str(record.get("class") or "unknown").strip() or "unknown"
            disposition = (
                str(record.get("disposition") or "unknown").strip() or "unknown"
            )
            kind = str(record.get("kind") or "normal").strip() or "normal"
            by_class[effect_class] = by_class.get(effect_class, 0) + 1
            by_disposition[disposition] = by_disposition.get(disposition, 0) + 1
            by_kind[kind] = by_kind.get(kind, 0) + 1
            categories["record"] += 1
            if kind == "compensation":
                categories["compensation"] += 1
            if (
                effect_class.startswith("external_")
                or effect_class == "provider_account"
            ):
                categories["external"] += 1
            elif effect_class == "artifact_write":
                categories["artifact"] += 1
            elif effect_class == "publication":
                categories["publication"] += 1
            elif effect_class == "memory_update":
                categories["memory"] += 1
            elif effect_class == "retrieval_index_update":
                categories["retrieval"] += 1
        return {
            "totalRecords": len(records),
            "categories": categories,
            "byClass": by_class,
            "byDisposition": by_disposition,
            "byKind": by_kind,
        }

    def _recovery_workspace_checkpoint_ref(
        self,
        workspace: Mapping[str, Any],
    ) -> str:
        return self._recovery_source_text(
            workspace,
            "checkpointRef",
            "checkpoint_ref",
            "workspaceCheckpointRef",
            "workspace_checkpoint_ref",
            "branchCheckpointRef",
            "branch_checkpoint_ref",
            "checkpointPayloadRef",
            "checkpoint_payload_ref",
        )

    def _recovery_workspace_has_evidence(
        self,
        workspace: Mapping[str, Any],
    ) -> bool:
        return any(
            isinstance(value, str) and value.strip()
            for value in workspace.values()
        )

    def _rebuild_step_ledger_index(self) -> None:
        self._step_ledger_by_id = {
            row["logicalStepId"]: row
            for row in self._step_ledger_rows
            if isinstance(row.get("logicalStepId"), str) and row["logicalStepId"]
        }

    def _validate_recovery_source_for_execution(self) -> dict[str, Any] | None:
        recovery_source = self._recovery_source
        if self._recovery_failed_step_id is not None:
            return dict(recovery_source) if recovery_source is not None else None
        self._recovery_failed_step_id = None
        self._recovery_workspace = {}
        self._recovery_workspace_restored_ref = None
        self._checkpoint_recovery_contract = None
        self._checkpoint_recovery_state = None
        if recovery_source is None:
            return None
        if not isinstance(recovery_source, Mapping):
            raise ValueError("Recovery source must be a compact mapping.")
        if not recovery_source:
            return None
        # Patch-gated so histories started before the explicit contract retain
        # their recorded validation path during replay.
        if self._workflow_patch_enabled(RUN_EXPLICIT_RECOVERY_CONTRACT_PATCH):
            validate_recovery_contract(recovery_source)

        source_workflow_id = self._recovery_source_text(
            recovery_source,
            "sourceWorkflowId",
            "source_workflow_id",
        )
        if not source_workflow_id:
            raise ValueError("Recovery source requires source workflow ID.")

        source_run_id = self._recovery_source_text(
            recovery_source,
            "sourceRunId",
            "source_run_id",
        )
        if not source_run_id:
            raise ValueError("Recovery source requires source run ID.")

        snapshot_ref = self._recovery_source_text(
            recovery_source,
            "sourceTaskInputSnapshotRef",
            "source_task_input_snapshot_ref",
        )
        if not snapshot_ref:
            raise ValueError("Recovery source requires task input snapshot ref.")

        failed_step_id = self._recovery_source_text(
            recovery_source,
            "failedStepId",
            "failed_step_id",
        )
        if not failed_step_id:
            raise ValueError("Recovery source requires failed step ID.")

        checkpoint_ref = self._recovery_source_text(
            recovery_source,
            "recoveryCheckpointRef",
            "recovery_checkpoint_ref",
        )
        if not checkpoint_ref:
            raise ValueError("Recovery source requires recovery checkpoint ref.")
        manifest_ref = self._recovery_source_text(
            recovery_source,
            "failedRunRecoveryManifestRef",
            "failed_run_recovery_manifest_ref",
        )
        if not manifest_ref:
            raise ValueError("Recovery source requires failed-run recovery manifest ref.")

        plan_identity = self._recovery_source_text(
            recovery_source,
            "sourcePlanRef",
            "source_plan_ref",
            "sourcePlanDigest",
            "source_plan_digest",
        )
        if not plan_identity:
            raise ValueError("Recovery source requires source plan identity.")

        workspace = (
            recovery_source.get("recoveryWorkspace")
            if "recoveryWorkspace" in recovery_source
            else recovery_source.get("recovery_workspace")
        )
        if not isinstance(workspace, Mapping):
            raise ValueError("Recovery source requires resume workspace checkpoint.")
        if not self._recovery_workspace_has_evidence(workspace):
            raise ValueError("Recovery source requires workspace evidence.")
        workspace_checkpoint_ref = self._recovery_workspace_checkpoint_ref(workspace)
        if workspace_checkpoint_ref and workspace_checkpoint_ref != checkpoint_ref:
            raise ValueError(
                "Recovery source checkpoint ref must match workspace evidence."
            )

        preserved_steps = recovery_source.get("preservedSteps") or recovery_source.get(
            "preserved_steps"
        )
        if preserved_steps is not None and not isinstance(preserved_steps, list):
            raise ValueError("Recovery source preserved steps must be a list.")
        for preserved in preserved_steps or []:
            if not isinstance(preserved, Mapping):
                continue
            logical_step_id = self._recovery_source_text(
                preserved,
                "logicalStepId",
                "logical_step_id",
            )
            if not logical_step_id:
                raise ValueError(
                    "Recovery source preserved step requires logical step ID."
                )
            status = self._recovery_source_text(preserved, "status").lower()
            if status == "succeeded":
                status = "completed"
            if status and status not in {"completed", "skipped"}:
                raise ValueError(
                    f"preserved step {logical_step_id} must be completed before Resume"
                )
            artifacts = preserved.get("artifacts")
            if not isinstance(artifacts, Mapping):
                raise ValueError(
                    f"preserved step {logical_step_id} requires recoverable output refs"
                )
            has_output_ref = any(
                isinstance(artifacts.get(key), str) and artifacts.get(key).strip()
                for key in ("outputSummary", "outputPrimary")
            )
            if not has_output_ref:
                raise ValueError(
                    f"preserved step {logical_step_id} requires recoverable output refs"
                )
            state_checkpoint_ref = self._recovery_source_text(
                preserved,
                "stateCheckpointRef",
                "state_checkpoint_ref",
            )
            if not state_checkpoint_ref:
                raise ValueError(
                    f"preserved step {logical_step_id} requires a state checkpoint ref"
                )

        explicit_contract = recovery_source.get("checkpointRecovery")
        if explicit_contract is None:
            explicit_contract = recovery_source.get("checkpoint_recovery")
        if explicit_contract is not None:
            contract = CheckpointRecoveryContract.model_validate(explicit_contract)
            restore_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                contract.restore_activity
            )
            if restore_route.fleet != "agent_runtime":
                raise ValueError(
                    "CHECKPOINT_CAPABILITY_INVALID: restore activity must run on "
                    "the agent_runtime fleet"
                )
            self._checkpoint_recovery_contract = contract
            self._checkpoint_recovery_state = {
                "status": "recovery_preflight_validated",
                "recoveryAction": contract.recovery_action,
                "resumePhase": contract.resume_phase,
                "sourceCheckpointRef": contract.source_checkpoint_ref,
                "sourceCheckpointBoundary": contract.source_checkpoint_boundary,
                "sourceCheckpointKind": contract.source_checkpoint_kind,
                "targetRuntimeId": contract.capabilities.runtime_id,
                "capabilitySetVersion": contract.capabilities.capability_set_version,
                "capabilityDigest": contract.capabilities.capability_digest,
                "restoreActivity": contract.restore_activity,
            }

        self._recovery_failed_step_id = failed_step_id
        self._recovery_workspace = dict(workspace)
        return dict(recovery_source)

    def _restore_recovery_workspace_for_failed_step(
        self,
        logical_step_id: str,
    ) -> str | None:
        if (
            not self._recovery_failed_step_id
            or logical_step_id != self._recovery_failed_step_id
        ):
            return None
        if self._recovery_workspace_restored_ref:
            return self._recovery_workspace_restored_ref
        checkpoint_ref = self._recovery_workspace_checkpoint_ref(self._recovery_workspace)
        if not checkpoint_ref:
            return None
        self._recovery_workspace_restored_ref = checkpoint_ref
        return checkpoint_ref

    async def _prepare_recovery_workspace_for_failed_step(
        self,
        logical_step_id: str,
    ) -> str | None:
        if (
            not self._recovery_failed_step_id
            or logical_step_id != self._recovery_failed_step_id
        ):
            return None
        if self._recovery_workspace_restored_ref:
            return self._recovery_workspace_restored_ref
        if not isinstance(self._recovery_source, Mapping):
            return None

        if (
            self._checkpoint_recovery_contract is not None
            and workflow.patched(RUN_CHECKPOINT_RECOVERY_STATE_MACHINE_PATCH)
        ):
            return await self._restore_checkpoint_recovery_workspace(logical_step_id)

        checkpoint_ref = self._recovery_workspace_checkpoint_ref(self._recovery_workspace)
        if not checkpoint_ref:
            return None

        checkpoint_payload = self._recovery_workspace.get("checkpoint")
        if not isinstance(checkpoint_payload, Mapping):
            checkpoint_payload = self._recovery_workspace.get("checkpointPayload")
        if not isinstance(checkpoint_payload, Mapping):
            checkpoint_payload = {
                key: value
                for key, value in self._recovery_workspace.items()
                if key not in {"checkpointRef", "checkpoint_ref"}
            }

        source_workflow_id = self._recovery_source_text(
            self._recovery_source,
            "sourceWorkflowId",
            "source_workflow_id",
        )
        source_run_id = self._recovery_source_text(
            self._recovery_source,
            "sourceRunId",
            "source_run_id",
        )
        execution_ordinal = self._recovery_source_int(
            self._recovery_source,
            "failedStepExecution",
            "failed_step_execution",
            "sourceExecutionOrdinal",
            "source_execution_ordinal",
        ) or 1
        task_input_snapshot_ref = self._recovery_source_text(
            self._recovery_source,
            "sourceTaskInputSnapshotRef",
            "source_task_input_snapshot_ref",
        )
        source_plan_ref = self._recovery_source_text(
            self._recovery_source,
            "sourcePlanRef",
            "source_plan_ref",
        )
        source_plan_digest = self._recovery_source_text(
            self._recovery_source,
            "sourcePlanDigest",
            "source_plan_digest",
        )
        resolved_policy = resolve_checkpoint_policy(
            boundary="before_recovery_restoration",
            recovery_source=self._recovery_source,
            runtime_kind=self._target_runtime,
        )
        workspace_policy = self._recovery_source_text(
            self._recovery_workspace,
            "workspacePolicy",
            "workspace_policy",
        ) or resolved_policy.workspace_policy

        validate_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "step_checkpoint.validate"
        )
        validate_payload = {
            "checkpoint": dict(checkpoint_payload),
            "expectedSource": {
                "workflowId": source_workflow_id,
                "runId": source_run_id,
                "logicalStepId": logical_step_id,
                "executionOrdinal": execution_ordinal,
            },
            "expectedTaskInputSnapshotRef": task_input_snapshot_ref,
            "expectedPlanRef": source_plan_ref or None,
            "expectedPlanDigest": source_plan_digest or None,
            "workspacePolicy": workspace_policy,
            "checkpointRef": checkpoint_ref,
        }
        validation = await workflow.execute_activity(
            validate_route.activity_type,
            validate_payload,
            **self._execute_kwargs_for_route(validate_route),
        )
        if not isinstance(validation, Mapping) or validation.get("valid") is not True:
            failure_code = "invalid_checkpoint"
            if isinstance(validation, Mapping):
                failure_code = str(validation.get("failureCode") or failure_code)
            self._recovery_checkpoint_validation_failure = {
                "failureCode": failure_code,
                "checkpointRef": checkpoint_ref,
            }
            raise ValueError(
                f"recovery checkpoint validation failed: {failure_code}"
            )

        target_workspace_locator = self._recovery_workspace.get(
            "targetWorkspaceLocator"
        )
        if not isinstance(target_workspace_locator, Mapping):
            target_workspace_locator = self._recovery_workspace.get(
                "target_workspace_locator"
            )
        if not isinstance(target_workspace_locator, Mapping):
            target_workspace_locator = None
        target_workspace_ref = self._recovery_source_text(
            self._recovery_workspace,
            "targetWorkspaceRef",
            "target_workspace_ref",
            "workspaceRef",
            "workspace_ref",
        )
        if target_workspace_locator is None:
            target_workspace_ref = target_workspace_ref or checkpoint_ref
        apply_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("workspace.apply_policy")
        apply_payload = {
            "identity": {
                "workflowId": source_workflow_id,
                "runId": source_run_id,
                "logicalStepId": logical_step_id,
                "executionOrdinal": execution_ordinal,
            },
            "workspacePolicy": workspace_policy,
            "checkpointRef": checkpoint_ref,
            "checkpoint": dict(checkpoint_payload),
            "expectedPlanRef": source_plan_ref or None,
            "expectedPlanDigest": source_plan_digest or None,
            "idempotencyKey": f"{checkpoint_ref}:workspace_policy:{workspace_policy}",
        }
        if target_workspace_locator is not None:
            apply_payload["targetWorkspaceLocator"] = dict(target_workspace_locator)
        if target_workspace_ref:
            apply_payload["targetWorkspaceRef"] = target_workspace_ref
        policy = await workflow.execute_activity(
            apply_route.activity_type,
            apply_payload,
            **self._execute_kwargs_for_route(apply_route),
        )
        if not isinstance(policy, Mapping) or policy.get("status") != "applied":
            failure_code = "policy_incompatible"
            if isinstance(policy, Mapping):
                failure_code = str(policy.get("failureCode") or failure_code)
            self._recovery_checkpoint_validation_failure = {
                "failureCode": failure_code,
                "checkpointRef": checkpoint_ref,
            }
            raise ValueError(
                f"recovery workspace policy application failed: {failure_code}"
            )

        workspace_ref = str(policy.get("workspaceRef") or target_workspace_ref).strip()
        self._recovery_workspace_restored_ref = workspace_ref or checkpoint_ref
        return self._recovery_workspace_restored_ref

    async def _restore_checkpoint_recovery_workspace(self, logical_step_id: str) -> str:
        """Invoke the frozen runtime-owned restore route exactly once per history."""
        contract = self._checkpoint_recovery_contract
        state = self._checkpoint_recovery_state
        if contract is None or state is None:
            raise ValueError("CHECKPOINT_RESTORATION_NOT_READY: recovery preflight missing")
        if state.get("status") == "recovery_workspace_restored":
            return str(state["restorationEvidenceRef"])

        source_ordinal = self._recovery_source_int(
            self._recovery_source or {}, "failedStepExecution", "failed_step_execution"
        ) or 1
        destination_id, idempotency_key = deterministic_recovery_identity(
            workflow_id=workflow.info().workflow_id,
            run_id=workflow.info().run_id,
            logical_step_id=logical_step_id,
            execution_ordinal=source_ordinal,
            checkpoint_ref=contract.source_checkpoint_ref,
        )
        state.update({
            "status": "recovery_restoring_workspace",
            "destinationAgentRunId": destination_id,
            "restoreIdempotencyKey": idempotency_key,
        })
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(contract.restore_activity)
        workspace = dict(self._recovery_workspace)
        source_locator = workspace.get("sourceWorkspaceLocator") or workspace.get(
            "workspaceLocator"
        )
        checkpoint = workspace.get("workspace") or workspace.get("checkpoint") or workspace
        result = await workflow.execute_activity(
            route.activity_type,
            {
                "schemaVersion": "v1",
                "recoveryIdentity": {
                    "workflowId": workflow.info().workflow_id,
                    "runId": workflow.info().run_id,
                    "logicalStepId": logical_step_id,
                    "executionOrdinal": source_ordinal + 1,
                },
                "source": {
                    "workflowId": self._recovery_source_text(self._recovery_source or {}, "sourceWorkflowId", "source_workflow_id"),
                    "runId": self._recovery_source_text(self._recovery_source or {}, "sourceRunId", "source_run_id"),
                    "logicalStepId": logical_step_id,
                    "executionOrdinal": source_ordinal,
                    "checkpointRef": contract.source_checkpoint_ref,
                    "checkpointBoundary": contract.source_checkpoint_boundary,
                    **({"sourceWorkspaceLocator": dict(source_locator)} if isinstance(source_locator, Mapping) else {}),
                },
                "checkpoint": {
                    "kind": contract.source_checkpoint_kind,
                    "baseCommit": checkpoint.get("baseCommit"),
                    "archiveRef": checkpoint.get("archiveRef"),
                    "archiveDigest": checkpoint.get("archiveDigest"),
                    "manifestRef": checkpoint.get("manifestRef"),
                    "manifestDigest": checkpoint.get("manifestDigest"),
                },
                "destination": {
                    "runtimeId": contract.capabilities.runtime_id,
                    "agentRunId": destination_id,
                    "repository": self._repo,
                    "relativePath": "repo",
                },
                "workspacePolicy": contract.workspace_policy,
                "resumePhase": contract.resume_phase,
                "capabilitySetVersion": contract.capabilities.capability_set_version,
                "capabilityDigest": contract.capabilities.capability_digest,
                "idempotencyKey": idempotency_key,
            },
            **self._execute_kwargs_for_route(route),
        )
        if not isinstance(result, Mapping):
            raise ValueError("CHECKPOINT_RESTORATION_NOT_READY: invalid restore result")
        locator, evidence_ref, evidence_digest = validate_restore_result(
            result,
            runtime_id=contract.capabilities.runtime_id,
            destination_agent_run_id=destination_id,
        )
        state.update({
            "status": "recovery_workspace_restored",
            "destinationWorkspaceLocator": locator,
            "restorationEvidenceRef": evidence_ref,
            "restorationEvidenceDigest": evidence_digest,
        })
        self._recovery_workspace_restored_ref = evidence_ref
        return evidence_ref

    def _preserved_outputs_for_step(
        self,
        logical_step_id: str,
    ) -> dict[str, dict[str, Any]]:
        return preserved_outputs_for_dependencies(
            self._step_ledger_rows,
            logical_step_id,
        )

    def _record_step_dependency_inputs(self, logical_step_id: str) -> None:
        try:
            record_dependency_inputs_for_step(
                self._step_ledger_rows,
                logical_step_id,
                workflow_id=workflow.info().workflow_id,
                run_id=workflow.info().run_id,
                updated_at=workflow.now(),
            )
        except KeyError:
            return

    def _record_downstream_dependency_effects(
        self,
        logical_step_id: str,
        *,
        updated_at: datetime,
    ) -> list[str]:
        try:
            invalidated = invalidate_downstream_steps_for_changed_output(
                self._step_ledger_rows,
                logical_step_id,
                workflow_id=workflow.info().workflow_id,
                run_id=workflow.info().run_id,
                updated_at=updated_at,
            )
        except KeyError:
            return []
        self._step_dependency_effects[logical_step_id] = {
            "invalidatedLogicalStepIds": list(invalidated),
        }
        return invalidated

    def _step_ledger_row_for(self, logical_step_id: str) -> dict[str, Any] | None:
        return self._step_ledger_by_id.get(logical_step_id)

    def _is_preserved_step(self, logical_step_id: str) -> bool:
        row = self._step_ledger_row_for(logical_step_id)
        return isinstance(row, Mapping) and isinstance(
            row.get("preservedFrom"),
            Mapping,
        )

    def _preserved_step_outputs(self, logical_step_id: str) -> dict[str, Any]:
        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, Mapping):
            return {}
        artifacts = row.get("artifacts")
        if not isinstance(artifacts, Mapping):
            return {}
        outputs: dict[str, Any] = {}
        output_summary = artifacts.get("outputSummary")
        if isinstance(output_summary, str) and output_summary.strip():
            outputs["outputSummaryRef"] = output_summary.strip()
        output_primary = artifacts.get("outputPrimary")
        if isinstance(output_primary, str) and output_primary.strip():
            outputs["outputPrimaryRef"] = output_primary.strip()
        if outputs:
            outputs["preservedFrom"] = dict(row.get("preservedFrom") or {})
        return outputs

    def _record_preserved_step_terminal_state(
        self,
        logical_step_id: str,
        node: Mapping[str, Any],
    ) -> None:
        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, Mapping):
            return
        terminal_disposition = self._coerce_text(
            row.get("terminalDisposition") or row.get("terminal_disposition"),
            max_chars=100,
        )
        if terminal_disposition:
            self._step_terminal_dispositions[logical_step_id] = terminal_disposition
        tool = self._plan_node_tool_mapping(node)
        tool = tool if isinstance(tool, Mapping) else {}
        tool_name = str(tool.get("name") or tool.get("id") or "").strip()
        if (
            terminal_disposition == "accepted"
            and self._is_moonspec_verify_step(
                tool_name=tool_name,
                node_inputs=self._node_inputs_mapping(node),
            )
            and self._normalize_moonspec_verify_verdict(self._moonspec_gate_verdict)
            is None
        ):
            self._moonspec_gate_verdict = "FULLY_IMPLEMENTED"
            self._publish_context["moonSpecGate"] = {
                "logicalStepId": logical_step_id,
                "verdict": "FULLY_IMPLEMENTED",
            }

    def _merge_preserved_dependency_outputs(
        self,
        logical_step_id: str,
        previous_step_outputs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        preserved_outputs = self._preserved_outputs_for_step(logical_step_id)
        if not preserved_outputs:
            return previous_step_outputs
        merged = dict(previous_step_outputs)
        merged["preservedOutputs"] = preserved_outputs
        return merged

    def _initialize_step_ledger(
        self,
        *,
        ordered_nodes: list[dict[str, Any]],
        dependency_map: dict[str, list[str]],
        updated_at: datetime,
    ) -> None:
        recovery_source = self._validate_recovery_source_for_execution() or {}
        self._step_ledger_rows = build_initial_step_rows(
            ordered_nodes=ordered_nodes,
            dependency_map=dependency_map,
            updated_at=updated_at,
        )
        self._rebuild_step_ledger_index()
        preserved_steps = recovery_source.get("preservedSteps") or recovery_source.get(
            "preserved_steps"
        )
        if isinstance(preserved_steps, list):
            source_workflow_id = self._recovery_source_text(
                recovery_source,
                "sourceWorkflowId",
                "source_workflow_id",
            )
            source_run_id = self._recovery_source_text(
                recovery_source,
                "sourceRunId",
                "source_run_id",
            )
            if source_workflow_id and source_run_id:
                materialize_preserved_steps(
                    self._step_ledger_rows,
                    source_workflow_id=source_workflow_id,
                    source_run_id=source_run_id,
                    preserved_steps=[
                        step for step in preserved_steps if isinstance(step, Mapping)
                    ],
                    updated_at=updated_at,
                )
                validate_preserved_dependency_outputs(
                    self._step_ledger_rows,
                    updated_at=updated_at,
                )
                refresh_ready_steps(self._step_ledger_rows, updated_at=updated_at)
        self._sync_progress_snapshot(updated_at=updated_at)

    def _capture_prepared_input_refs(self, parameters: Mapping[str, Any]) -> list[str]:
        task_payload = parameters.get("task")
        if not isinstance(task_payload, Mapping):
            self._prepared_artifact_refs = []
            return []
        manifest = build_prepared_input_manifest(task_payload)
        self._prepared_artifact_refs = build_recovery_prepared_artifact_refs(manifest)
        return list(self._prepared_artifact_refs)

    def _mark_step_running(
        self,
        logical_step_id: str,
        *,
        updated_at: datetime,
        summary: str | None = None,
        increment_attempt: bool = True,
    ) -> None:
        self._restore_recovery_workspace_for_failed_step(logical_step_id)
        if not self._try_update_step_row(
            logical_step_id,
            updated_at=updated_at,
            status="executing",
            summary=summary,
            waiting_reason=None,
            attention_required=False,
            increment_attempt=increment_attempt,
            set_started_at=True,
        ):
            return
        previous_checkpoint_ref = self._step_checkpoint_refs.get(logical_step_id)
        if not previous_checkpoint_ref:
            row = self._step_ledger_row_for(logical_step_id)
            if isinstance(row, Mapping):
                raw_ref = row.get("stateCheckpointRef")
                if isinstance(raw_ref, str) and raw_ref.strip():
                    previous_checkpoint_ref = raw_ref.strip()
        if previous_checkpoint_ref:
            self._previous_step_checkpoint_refs[logical_step_id] = (
                previous_checkpoint_ref
            )
        self._step_checkpoint_refs.pop(logical_step_id, None)
        self._step_checkpoint_refs_by_boundary.pop(logical_step_id, None)
        try:
            clear_step_checkpoint_evidence(
                self._step_ledger_rows,
                logical_step_id,
                updated_at=updated_at,
            )
        except KeyError:
            return
        # First time a logical step crosses into executing is the closest
        # existing semantic boundary for "real work began". Stamp the
        # execution-level mm_started_at exactly once here.
        if workflow.patched(RUN_REAL_STARTED_AT_PATCH):
            self._mark_real_work_started(now=updated_at)
        self._sync_progress_snapshot(updated_at=updated_at)

    def _record_step_side_effect(
        self,
        logical_step_id: str,
        *,
        effect_class: str,
        operation: str,
        target: str | None = None,
        idempotency_key: str | None = None,
        workflow_state_accepted: bool | None = None,
        effect_kind: str = "normal",
        reason: str | None = None,
        approved_workspace_roots: Sequence[str] = (),
        memory_effect: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Classify and record one side effect for the current step attempt.

        Records accumulate per logical step so that, when a later attempt
        starts, already-occurred non-idempotent external effects can be
        detected and compensated explicitly.
        """

        accepted = (
            self._step_terminal_dispositions.get(logical_step_id) == "accepted"
            if workflow_state_accepted is None
            else workflow_state_accepted
        )
        record = side_effect_record(
            effect_class=effect_class,  # type: ignore[arg-type]
            operation=operation,
            target=target,
            idempotency_key=idempotency_key,
            workflow_state_accepted=accepted,
            effect_kind=effect_kind,  # type: ignore[arg-type]
            reason=reason,
            approved_workspace_roots=approved_workspace_roots,
            memory_effect=memory_effect,
        )
        self._step_side_effect_records.setdefault(logical_step_id, []).append(record)
        return record

    def _finish_summary_side_effects(self) -> list[dict[str, Any]]:
        side_effects: list[dict[str, Any]] = []
        for records in self._step_side_effect_records.values():
            for record in records:
                if not isinstance(record, dict):
                    continue
                operation = self._coerce_text(record.get("operation"), max_chars=120)
                disposition = self._coerce_text(record.get("disposition"), max_chars=40)
                if not operation and not disposition:
                    continue
                provider_kind = self._coerce_text(
                    record.get("providerKind"),
                    max_chars=80,
                )
                summary = self._coerce_text(record.get("summary"), max_chars=300)
                side_effects.append(
                    {
                        "kind": provider_kind
                        or self._coerce_text(
                            record.get("class") or record.get("kind"), max_chars=80
                        )
                        or "external",
                        "status": "completed"
                        if disposition == "accepted"
                        else (disposition or "recorded"),
                        "summary": summary or operation or "Side effect recorded.",
                    }
                )
        return side_effects[:20]

    def _record_declared_side_effect(
        self,
        *,
        logical_step_id: str,
        outputs: Mapping[str, Any],
    ) -> None:
        if not self._canonical_no_commit_outcome_enabled:
            return
        declaration = outputs.get("sideEffect") or outputs.get("side_effect")
        if not isinstance(declaration, Mapping):
            return
        effect_class = self._coerce_text(
            declaration.get("effectClass") or declaration.get("effect_class"),
            max_chars=80,
        )
        if effect_class not in {
            "external_idempotent",
            "external_non_idempotent",
            "publication",
            "provider_account",
        }:
            return
        operation = self._coerce_text(declaration.get("operation"), max_chars=120)
        if not operation:
            return
        idempotency_key = self._coerce_text(
            declaration.get("idempotencyKey") or declaration.get("idempotency_key"),
            max_chars=200,
        )
        if effect_class == "external_idempotent" and not idempotency_key:
            return
        record = self._record_step_side_effect(
            logical_step_id,
            effect_class=effect_class,
            operation=operation,
            target=self._coerce_text(declaration.get("target"), max_chars=500),
            idempotency_key=idempotency_key,
            workflow_state_accepted=True,
            reason=self._coerce_text(declaration.get("summary"), max_chars=300),
        )
        if record is None:
            return
        provider_kind = self._coerce_text(
            declaration.get("kind") or declaration.get("provider"),
            max_chars=80,
        )
        summary = self._coerce_text(declaration.get("summary"), max_chars=300)
        if provider_kind:
            record["providerKind"] = provider_kind
        if summary:
            record["summary"] = summary

    def _is_jira_orchestrate_external_handoff_node(
        self,
        node: Mapping[str, Any],
    ) -> bool:
        node_inputs = self._node_inputs_mapping(node)
        annotations = node_inputs.get("annotations") or node.get("annotations")
        if not isinstance(annotations, Mapping):
            return False
        role = str(annotations.get("jiraOrchestrateRole") or "").strip().lower()
        return role in {"pull-request-handoff", "code-review-handoff"}

    def _jira_orchestrate_external_handoff_block_reason(
        self,
        node: Mapping[str, Any],
    ) -> str | None:
        if not self._is_jira_orchestrate_external_handoff_node(node):
            return None
        verdict = self._normalize_moonspec_verify_verdict(self._moonspec_gate_verdict)
        if verdict in _MOONSPEC_GATE_PASSING_VERDICTS:
            # The MoonSpec gate verdict approved advancement. Keep that block in
            # place and additionally assert the producing Step Execution reached
            # the `accepted` terminal disposition before any external handoff
            # runs (publication / Jira / merge / deploy / provider-account).
            return self._external_handoff_accepted_disposition_block_reason(node)
        gate_context = self._publish_context.get("moonSpecGate")
        logical_step_id = None
        if isinstance(gate_context, Mapping):
            logical_step_id = self._coerce_text(
                gate_context.get("logicalStepId"),
                max_chars=200,
            )
        if verdict:
            parts = [
                "Jira Orchestrate external handoff requires an accepted MoonSpec "
                f"verification terminal disposition; latest verdict was {verdict}"
            ]
            if logical_step_id:
                terminal = self._step_terminal_dispositions.get(logical_step_id)
                if terminal:
                    parts.append(f"gate step terminal disposition was {terminal}")
            return ". ".join(parts) + "."
        return (
            "Jira Orchestrate external handoff requires an accepted MoonSpec "
            "verification terminal disposition; no controlling verification gate "
            "has approved advancement."
        )

    def _external_handoff_operation(self, node: Mapping[str, Any]) -> str:
        """Map a Jira Orchestrate handoff node to its external operation id."""

        node_inputs = self._node_inputs_mapping(node)
        annotations = node_inputs.get("annotations") or node.get("annotations")
        role = ""
        if isinstance(annotations, Mapping):
            role = str(annotations.get("jiraOrchestrateRole") or "").strip().lower()
        if role == "pull-request-handoff":
            return "repo.publish"
        if role == "code-review-handoff":
            return "jira.add_comment"
        return "external.handoff"

    def _external_handoff_accepted_disposition_block_reason(
        self,
        node: Mapping[str, Any],
    ) -> str | None:
        """Assert the producing Step Execution is accepted before a handoff.

        Layered on top of the MoonSpec verdict block: a passing verdict alone is
        not sufficient. The producing verification Step Execution must also have
        reached the ``accepted`` terminal disposition (Section 7.3) before any
        external handoff (PR creation, Jira transition/comment, merge automation,
        deployment/publish, provider-account action) runs. A denied handoff is
        recorded as a blocked side effect at the boundary. Guarded for replay
        safety so in-flight runs keep their original verdict-only decisions.
        """

        if not workflow.patched(RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH):
            return None
        gate_context = self._publish_context.get("moonSpecGate")
        gate_logical_step_id = None
        if isinstance(gate_context, Mapping):
            gate_logical_step_id = self._coerce_text(
                gate_context.get("logicalStepId"),
                max_chars=200,
            )
        terminal = (
            self._step_terminal_dispositions.get(gate_logical_step_id)
            if gate_logical_step_id
            else None
        )
        operation = self._external_handoff_operation(node)
        decision = external_handoff_gate_decision(
            operation=operation,
            producing_step_terminal_disposition=terminal,
            gate_approved=True,
            gate_verdict=self._moonspec_gate_verdict,
        )
        if decision.get("allowed"):
            return None
        reason = (
            "Jira Orchestrate external handoff requires the producing MoonSpec "
            "verification Step Execution to reach an accepted terminal "
            f"disposition; {decision.get('reason')}."
        )
        node_id = str(node.get("id") or "external-handoff").strip() or "external-handoff"
        # Record the denied non-idempotent external action as a blocked side
        # effect at the actual handoff boundary (Section 11 rule 2). Repeated
        # gate evaluations for the same node must not duplicate the record.
        existing_records = self._step_side_effect_records.get(node_id, ())
        already_recorded = any(
            record.get("class") == "external_non_idempotent"
            and record.get("operation") == operation
            and record.get("disposition") == "blocked"
            for record in existing_records
        )
        if already_recorded:
            return reason
        self._record_step_side_effect(
            node_id,
            effect_class="external_non_idempotent",
            operation=operation,
            workflow_state_accepted=False,
            reason=reason,
        )
        return reason

    def _orchestrate_reattempt_compensation(
        self,
        logical_step_id: str,
        *,
        execution_ordinal: int,
        updated_at: datetime,
        policy_permits_non_idempotent_reattempt: bool = False,
    ) -> dict[str, Any] | None:
        """Plan and record idempotent, observable compensation for a reattempt.

        On any reattempt (``execution_ordinal > 1``) this inspects the side
        effects recorded on prior attempts of the same logical step. For each
        already-occurred non-idempotent external effect it derives an explicit
        compensation side-effect record with a deterministic idempotency key,
        records it as a side effect of the new attempt, and stores an
        observable plan projection. Compensation subjects are deduped across
        reattempts so a given external mutation is compensated at most once.

        Returns the compensation plan, or ``None`` when there is no prior
        attempt to reconcile.
        """

        if execution_ordinal is None or execution_ordinal <= 1:
            return None
        prior_records = list(self._step_side_effect_records.get(logical_step_id, ()))
        already_compensated = self._step_compensated_subjects.setdefault(
            logical_step_id, set()
        )
        plan = plan_reattempt_compensation(
            workflow_id=workflow.info().workflow_id,
            run_id=workflow.info().run_id,
            logical_step_id=logical_step_id,
            execution_ordinal=execution_ordinal,
            prior_side_effect_records=prior_records,
            already_compensated_subjects=sorted(already_compensated),
            policy_permits_non_idempotent_reattempt=(
                policy_permits_non_idempotent_reattempt
            ),
        )
        # Record each compensation as a side effect of the new attempt and mark
        # its subject compensated so successive reattempts do not repeat it.
        for compensation in plan.get("compensations", ()):
            self._step_side_effect_records.setdefault(logical_step_id, []).append(
                dict(compensation)
            )
            # Only treat a subject as compensated once its compensation was
            # actually accepted. A blocked compensation never reconciled the
            # prior effect, so it must remain eligible for a later reattempt
            # rather than being silently skipped.
            if compensation.get("disposition") == "accepted":
                subject = str(
                    (compensation.get("compensates") or {}).get("subject") or ""
                ).strip()
                if subject:
                    already_compensated.add(subject)
        if plan.get("requiresCompensation"):
            self._step_reattempt_compensations[logical_step_id] = plan
            self._get_logger().info(
                "Orchestrated reattempt compensation for %s execution %d: "
                "%d effect(s) accounted for",
                logical_step_id,
                execution_ordinal,
                len(plan.get("outstandingEffects", ())),
                extra={
                    "event": "step_reattempt_compensation",
                    "logical_step_id": logical_step_id,
                    "execution_ordinal": execution_ordinal,
                    "compensation_count": len(plan.get("compensations", ())),
                    "reattempt_allowed": plan.get("reattemptAllowed"),
                },
            )
        return plan

    def _combined_step_execution_input_refs(
        self,
        input_refs: Sequence[str],
    ) -> list[str]:
        refs: list[str] = []
        for ref in (*input_refs, *self._prepared_artifact_refs):
            candidate = str(ref or "").strip()
            if candidate and candidate not in refs:
                refs.append(candidate)
        return refs

    def _step_execution_prior_evidence_refs(
        self,
        logical_step_id: str,
        *,
        attempt: int,
    ) -> list[str]:
        if attempt <= 1:
            return []
        row = self._step_ledger_row_for(logical_step_id)
        refs = row.get("refs") if isinstance(row, Mapping) else None
        evidence_refs: list[str] = []
        if isinstance(refs, Mapping):
            for ref in refs.get("stepExecutionManifestRefs", ()):
                if isinstance(ref, str) and ref.strip():
                    evidence_refs.append(ref.strip())
            latest_ref = refs.get("latestStepExecutionManifestRef")
            if isinstance(latest_ref, str) and latest_ref.strip():
                evidence_refs.append(latest_ref.strip())
        checkpoint_ref = self._previous_step_checkpoint_refs.get(
            logical_step_id
        ) or self._step_checkpoint_refs.get(logical_step_id)
        if checkpoint_ref:
            evidence_refs.append(checkpoint_ref)
        return list(dict.fromkeys(evidence_refs))

    def _step_execution_checkpoint_refs(
        self,
        logical_step_id: str,
    ) -> dict[str, Any]:
        refs: dict[str, Any] = {}
        current_ref = self._step_checkpoint_refs.get(logical_step_id)
        previous_ref = self._previous_step_checkpoint_refs.get(logical_step_id)
        if current_ref:
            refs["current"] = current_ref
        if previous_ref:
            refs["previous"] = previous_ref
        for boundary, ref in self._step_checkpoint_refs_by_boundary.get(
            logical_step_id,
            {},
        ).items():
            if ref:
                refs[str(boundary)] = ref
        return refs

    def _step_execution_context_kwargs(
        self,
        logical_step_id: str,
        *,
        attempt: int,
        workspace: Mapping[str, Any] | None = None,
        policy_refs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_workspace = dict(workspace or {})
        workspace_policy = (
            resolved_workspace.get("workspacePolicy")
            or resolved_workspace.get("policy")
            or (
                "continue_from_previous_execution"
                if attempt > 1
                else "fresh_branch_from_source"
            )
        )
        workspace_baseline = {
            key: value
            for key, value in resolved_workspace.items()
            if key
            in {
                "workspaceRef",
                "checkpointRef",
                "checkpointValid",
                "stateCheckpointRef",
                "checkpointBeforeRef",
                "checkpointAfterRef",
                "sourceExecutionOrdinal",
            }
            and value is not None
        }
        return {
            "task_input_snapshot_ref": self._input_ref,
            "plan_ref": self._plan_ref,
            "workspace_policy": str(workspace_policy) if workspace_policy else None,
            "workspace_baseline": workspace_baseline,
            "checkpoint_refs": self._step_execution_checkpoint_refs(logical_step_id),
            "prior_evidence_refs": self._step_execution_prior_evidence_refs(
                logical_step_id,
                attempt=attempt,
            ),
            "quality_gate_profile": "repo-default",
            "policy_refs": dict(policy_refs or {}),
        }

    def _checkpoint_branch_turn_payload(
        self,
        *,
        node_inputs: Mapping[str, Any],
        runtime_block: Mapping[str, Any],
        metadata_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Return explicit checkpoint-branch turn metadata for a runtime node."""

        for path, source in (
            ("node.runtime", runtime_block),
            ("node.inputs", node_inputs),
            ("node.metadata", metadata_payload),
        ):
            payload = self._checkpoint_branch_turn_payload_from_source(
                source,
                path=path,
            )
            if payload is not None:
                return payload
        return None

    def _checkpoint_branch_turn_payload_from_source(
        self,
        source: Mapping[str, Any],
        *,
        path: str,
    ) -> dict[str, Any] | None:
        if not isinstance(source, Mapping):
            return None
        for key in (
            "checkpointBranchTurn",
            "checkpoint_branch_turn",
            "branchTurn",
            "branch_turn",
        ):
            value = source.get(key)
            if value is None:
                continue
            if not isinstance(value, Mapping):
                raise ValueError(f"{path}.{key} must be an object when provided")
            return self._json_mapping(value, path=f"{path}.{key}")

        moonmind_payload = source.get("moonmind")
        if isinstance(moonmind_payload, Mapping):
            nested = self._checkpoint_branch_turn_payload_from_source(
                moonmind_payload,
                path=f"{path}.moonmind",
            )
            if nested is not None:
                return nested

        branch_id_present = any(key in source for key in ("branchId", "branch_id"))
        branch_turn_present = any(
            key in source for key in ("branchTurnId", "branch_turn_id")
        )
        if branch_id_present and branch_turn_present:
            return self._json_mapping(source, path=path)
        return None

    def _checkpoint_branch_turn_text(
        self,
        payload: Mapping[str, Any],
        *keys: str,
    ) -> str | None:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"checkpoint branch turn {key} must be a string")
            normalized = value.strip()
            if normalized:
                return normalized
        return None

    def _checkpoint_branch_turn_instruction_ref(
        self,
        payload: Mapping[str, Any],
        *,
        node_inputs: Mapping[str, Any],
    ) -> str | None:
        explicit_ref = self._checkpoint_branch_turn_text(
            payload,
            "instructionArtifactRef",
            "instruction_artifact_ref",
            "instructionRef",
            "instruction_ref",
            "instructionsArtifactRef",
            "instructions_artifact_ref",
        )
        if explicit_ref:
            return explicit_ref
        for key in ("instructionRef", "instruction_ref", "instructions"):
            value = node_inputs.get(key)
            if isinstance(value, str) and value.strip().startswith("artifact://"):
                return value.strip()
        return None

    def _apply_omnigent_checkpoint_branch_turn_prompt(
        self,
        *,
        parameters: dict[str, Any],
        instruction_ref: str,
    ) -> None:
        """Route Omnigent branch-turn instructions through the adapter prompt block."""

        raw_omnigent = parameters.get("omnigent")
        if raw_omnigent is None:
            omnigent_payload: dict[str, Any] = {}
        elif isinstance(raw_omnigent, Mapping):
            omnigent_payload = dict(raw_omnigent)
        else:
            raise ValueError("parameters.omnigent must be an object")

        raw_prompt = omnigent_payload.get("prompt")
        if raw_prompt is None:
            prompt_payload: dict[str, Any] = {}
        elif isinstance(raw_prompt, Mapping):
            prompt_payload = dict(raw_prompt)
        else:
            raise ValueError("parameters.omnigent.prompt must be an object")

        prompt_payload.pop("text", None)
        prompt_payload["instructionRef"] = instruction_ref
        omnigent_payload["prompt"] = prompt_payload
        parameters["omnigent"] = omnigent_payload

    def _checkpoint_branch_turn_source_checkpoint(
        self,
        payload: Mapping[str, Any],
        *,
        node_id: str,
        wf_info: Any,
        context_workspace: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_source = payload.get("sourceCheckpoint") or payload.get(
            "source_checkpoint"
        )
        if raw_source is None:
            source: dict[str, Any] = {}
        elif isinstance(raw_source, Mapping):
            source = self._json_mapping(
                raw_source,
                path="checkpointBranchTurn.sourceCheckpoint",
            )
        else:
            raise ValueError(
                "checkpoint branch turn sourceCheckpoint must be an object"
            )

        for source_key, payload_keys in (
            ("workflowId", ("sourceWorkflowId", "source_workflow_id", "workflowId")),
            ("runId", ("sourceRunId", "source_run_id", "runId")),
            (
                "logicalStepId",
                (
                    "sourceLogicalStepId",
                    "source_logical_step_id",
                    "logicalStepId",
                ),
            ),
            (
                "sourceExecutionOrdinal",
                (
                    "sourceExecutionOrdinal",
                    "source_execution_ordinal",
                    "executionOrdinal",
                    "execution_ordinal",
                ),
            ),
            (
                "checkpointBoundary",
                (
                    "sourceCheckpointBoundary",
                    "source_checkpoint_boundary",
                    "checkpointBoundary",
                    "checkpoint_boundary",
                ),
            ),
            (
                "checkpointRef",
                (
                    "sourceCheckpointRef",
                    "source_checkpoint_ref",
                    "checkpointRef",
                    "checkpoint_ref",
                ),
            ),
            (
                "checkpointDigest",
                (
                    "sourceCheckpointDigest",
                    "source_checkpoint_digest",
                    "checkpointDigest",
                    "checkpoint_digest",
                ),
            ),
            (
                "sourceStateKind",
                (
                    "sourceStateKind",
                    "source_state_kind",
                ),
            ),
            (
                "sourceStateRef",
                (
                    "sourceStateRef",
                    "source_state_ref",
                ),
            ),
            (
                "sourceStateDigest",
                (
                    "sourceStateDigest",
                    "source_state_digest",
                ),
            ),
        ):
            if source.get(source_key) is not None:
                continue
            for payload_key in payload_keys:
                value = payload.get(payload_key)
                if value is not None:
                    source[source_key] = value
                    break

        workspace = context_workspace if isinstance(context_workspace, Mapping) else {}
        explicit_checkpoint_ref = source.get("checkpointRef") is not None
        if source.get("checkpointRef") is None:
            checkpoint_ref = (
                workspace.get("checkpointAfterRef")
                or workspace.get("checkpointRef")
                or workspace.get("stateCheckpointRef")
                or workspace.get("checkpointBeforeRef")
            )
            if checkpoint_ref is not None:
                source["checkpointRef"] = checkpoint_ref
                if source.get("checkpointBoundary") is None:
                    source["checkpointBoundary"] = (
                        "before_execution"
                        if checkpoint_ref == workspace.get("checkpointBeforeRef")
                        else "after_execution"
                    )
        elif source.get("checkpointBoundary") is None:
            source["checkpointBoundary"] = "after_execution"
        if source.get("checkpointDigest") is None:
            checkpoint_digest = (
                workspace.get("checkpointAfterDigest")
                or workspace.get("checkpointDigest")
                or workspace.get("stateCheckpointDigest")
                or workspace.get("checkpointBeforeDigest")
            )
            if checkpoint_digest is not None:
                source["checkpointDigest"] = checkpoint_digest
        if source.get("checkpointRef") is not None and not explicit_checkpoint_ref:
            source.setdefault("workflowId", wf_info.workflow_id)
            source.setdefault("runId", wf_info.run_id)
            source.setdefault("logicalStepId", node_id)
        return source

    def _checkpoint_branch_turn_workspace_policy(
        self,
        payload: Mapping[str, Any],
        *,
        context_kwargs: Mapping[str, Any],
    ) -> str:
        return (
            self._checkpoint_branch_turn_text(
                payload,
                "workspacePolicy",
                "workspace_policy",
            )
            or str(context_kwargs.get("workspace_policy") or "")
            or "fresh_branch_from_source"
        )

    def _step_execution_branch_metadata(
        self,
        logical_step_id: str,
        *,
        attempt: int,
    ) -> dict[str, Any] | None:
        if not self._workflow_patch_enabled(RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH):
            return None
        branch_metadata = self._step_execution_branch_projections.get(
            (logical_step_id, attempt)
        )
        if not isinstance(branch_metadata, Mapping):
            return None
        return dict(branch_metadata)

    def _step_execution_manifest_context_projection(
        self,
        logical_step_id: str,
        *,
        attempt: int,
        reason: str,
        execution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cached_context = self._step_execution_context_projections.get(
            (logical_step_id, attempt)
        )
        if isinstance(cached_context, Mapping):
            return dict(cached_context)
        runtime_selection: dict[str, Any] = {}
        if isinstance(execution, Mapping):
            for source_key, target_key in (
                ("runtimeId", "runtimeId"),
                ("toolName", "runtimeId"),
                ("model", "model"),
                ("effort", "effort"),
                ("executionProfileRef", "executionProfileRef"),
            ):
                value = execution.get(source_key)
                if value is not None and target_key not in runtime_selection:
                    runtime_selection[target_key] = value
        projection = build_execution_context_bundle(
            workflow_id=workflow.info().workflow_id,
            run_id=workflow.info().run_id,
            logical_step_id=logical_step_id,
            execution_ordinal=attempt,
            reason=reason,
            runtime_selection=runtime_selection,
            **self._step_execution_context_kwargs(
                logical_step_id,
                attempt=attempt,
                workspace=self._step_execution_workspace(
                    logical_step_id,
                    attempt=attempt,
                    source_execution_ordinal=self._step_execution_source_identity(
                        logical_step_id,
                        attempt=attempt,
                    ),
                ),
            ),
        ).to_manifest_projection()
        context = projection.get("context")
        if isinstance(context, Mapping):
            return dict(context)
        return {}

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
        metadata: Mapping[str, Any] | None = None,
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
                metadata=metadata,
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

    @staticmethod
    def _gate_check_metadata(
        *,
        gate_result_ref: str,
        gate: StepGateResult,
    ) -> dict[str, Any]:
        return {
            "gateResultRef": gate_result_ref,
            "gateVerdict": gate.verdict,
            "confidence": gate.confidence,
            "validatedRefs": dict(gate.validated_refs or {}),
            "invalidatedRefs": list(gate.invalidated_refs),
            "remainingWorkRef": gate.remaining_work_ref,
            "targetLogicalStepId": gate.target_logical_step_id,
            "workspacePolicyRecommendation": gate.workspace_policy_recommendation,
            "recommendedNextAction": gate.recommended_next_action,
            "invalid": gate.invalid,
            "degraded": gate.degraded,
        }

    def _step_execution_for(self, logical_step_id: str) -> int | None:
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
        outputs = self._effective_result_outputs(execution_result)
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
            "agentRunId": _output_ref("agentRunId", "agent_run_id")
            or _output_ref("agentRunId", "agent_run_id")
            or (
                str(
                    workload_metadata.get("agentRunId")
                    or workload_metadata.get("agentRunId")
                ).strip()
                if isinstance(workload_metadata, Mapping)
                and (
                    workload_metadata.get("agentRunId") is not None
                    or workload_metadata.get("agentRunId") is not None
                )
                else None
            ),
        }
        artifacts = {
            "outputSummary": _output_ref(
                "outputSummaryRef",
                "output_summary_ref",
                "summaryRef",
                "summary_ref",
            )
            or _artifact_class_ref("output.summary"),
            "outputPrimary": _output_ref(
                "outputPrimaryRef",
                "output_primary_ref",
                "primaryRef",
                "primary_ref",
                "primaryReportRef",
                "primary_report_ref",
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
            "runtimeDiagnostics": _output_ref(
                "diagnosticsRef",
                "diagnostics_ref",
                "diagnosticsArtifactRef",
                "diagnostics_artifact_ref",
            )
            or _artifact_class_ref("runtime.diagnostics"),
            "providerSnapshot": _output_ref(
                "providerSnapshotRef",
                "provider_snapshot_ref",
            ),
        }
        external_state_ref = _output_ref(
            "externalStateRef",
            "external_state_ref",
        )
        if external_state_ref is not None:
            artifacts["externalStateRef"] = external_state_ref
        checkpoint_ref = _output_ref(
            "stateCheckpointRef",
            "state_checkpoint_ref",
            "latestCheckpointRef",
            "latest_checkpoint_ref",
            "checkpointRef",
            "checkpoint_ref",
        ) or _artifact_class_ref("session.step_checkpoint", "state.checkpoint")

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
        self._record_step_workspace_capture_input(logical_step_id, outputs)
        if checkpoint_ref:
            try:
                mark_step_checkpoint_evidence(
                    self._step_ledger_rows,
                    logical_step_id,
                    updated_at=updated_at,
                    state_checkpoint_ref=checkpoint_ref,
                )
                self._step_checkpoint_refs[logical_step_id] = checkpoint_ref
            except KeyError:
                return
        self._sync_progress_snapshot(updated_at=updated_at)

    def _record_step_checkpoint_evidence(
        self,
        logical_step_id: str,
        *,
        updated_at: datetime,
        state_checkpoint_ref: str | None = None,
    ) -> str | None:
        checkpoint_ref = state_checkpoint_ref or self._step_checkpoint_refs.get(
            logical_step_id
        )
        try:
            row = mark_step_checkpoint_evidence(
                self._step_ledger_rows,
                logical_step_id,
                updated_at=updated_at,
                state_checkpoint_ref=checkpoint_ref,
            )
        except KeyError:
            return None
        recorded_checkpoint_ref = str(row.get("stateCheckpointRef") or "").strip()
        if recorded_checkpoint_ref:
            self._step_checkpoint_refs[logical_step_id] = recorded_checkpoint_ref
        self._sync_progress_snapshot(updated_at=updated_at)
        return self._step_checkpoint_refs.get(logical_step_id)

    async def _create_step_checkpoint_via_activity(
        self,
        *,
        identity: StepExecutionIdentityModel,
        boundary: StepExecutionCheckpointBoundary,
        task_input_snapshot_ref: str,
        workspace: Mapping[str, Any],
        created_at: datetime,
        plan_ref: str | None = None,
        plan_digest: str | None = None,
        prepared_input_refs: Sequence[str] = (),
        step_outputs: Mapping[str, Any] | None = None,
        diagnostic_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Write checkpoint evidence through the artifact activity boundary.

        Workflow code receives and forwards compact refs only. Workspace and git
        inspection must happen before this call in sandbox/service activities.
        """

        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("step_checkpoint.create")
        checkpoint_id = build_step_checkpoint_id(identity, boundary)
        payload = {
            "identity": identity.model_dump(by_alias=True, mode="json"),
            "boundary": boundary,
            "taskInputSnapshotRef": task_input_snapshot_ref,
            "workspace": dict(workspace),
            "createdAt": created_at.isoformat(),
            "planRef": plan_ref,
            "planDigest": plan_digest,
            "preparedInputRefs": list(prepared_input_refs),
            "stepOutputs": dict(step_outputs or {}),
            "diagnosticRefs": list(diagnostic_refs),
            "idempotencyKey": checkpoint_id,
        }
        result = await workflow.execute_activity(
            route.activity_type,
            payload,
            **self._execute_kwargs_for_route(route),
        )
        if isinstance(result, Mapping):
            checkpoint_ref = str(result.get("checkpointRef") or "").strip()
            if checkpoint_ref:
                boundary_key = str(boundary)
                try:
                    mark_step_checkpoint_evidence(
                        self._step_ledger_rows,
                        identity.logical_step_id,
                        updated_at=created_at,
                        step_checkpoint_ref=checkpoint_ref,
                        boundary=boundary_key,
                    )
                except KeyError:
                    pass
                else:
                    refs_by_boundary = self._step_checkpoint_refs_by_boundary.setdefault(
                        identity.logical_step_id,
                        {},
                    )
                    refs_by_boundary[boundary_key] = checkpoint_ref
                    self._sync_progress_snapshot(updated_at=created_at)
            return dict(result)
        raise ValueError("step_checkpoint.create returned a non-mapping result")

    def _canonical_step_checkpoint_identity(
        self,
        logical_step_id: str,
    ) -> StepExecutionIdentityModel | None:
        attempt = self._step_execution_for(logical_step_id)
        if attempt is None or attempt <= 0:
            return None
        return StepExecutionIdentityModel(
            workflowId=workflow.info().workflow_id,
            runId=workflow.info().run_id,
            logicalStepId=logical_step_id,
            executionOrdinal=attempt,
        )

    @staticmethod
    def _checkpoint_capture_text(
        payload: Mapping[str, Any],
        *keys: str,
    ) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _record_step_workspace_capture_input(
        self,
        logical_step_id: str,
        outputs: Mapping[str, Any],
    ) -> None:
        raw_agent_kind = outputs.get("agentKind") or outputs.get("agent_kind")
        raw_agent_id = outputs.get("agentId") or outputs.get("agent_id")
        agent_kind = str(raw_agent_kind or "").strip().lower()
        agent_id = str(raw_agent_id or "").strip()
        if not agent_id:
            agent_id = self._agent_id_from_runtime_inputs(
                node_inputs=outputs,
                fallback_name=None,
            )
            if agent_id:
                agent_kind = self._agent_kind_for_id(agent_id)
        elif not agent_kind:
            agent_kind = self._agent_kind_for_id(agent_id)
        if agent_kind == "external" and agent_id:
            self._step_external_agent_ids[logical_step_id] = agent_id.lower()

        candidates: list[Mapping[str, Any]] = [outputs]
        workspace_spec = outputs.get("workspaceSpec") or outputs.get("workspace_spec")
        if isinstance(workspace_spec, Mapping):
            candidates.append(workspace_spec)
        workload_metadata = outputs.get("workloadMetadata") or outputs.get(
            "workload_metadata"
        )
        if isinstance(workload_metadata, Mapping):
            candidates.append(workload_metadata)
        workload_result = outputs.get("workloadResult") or outputs.get(
            "workload_result"
        )
        if isinstance(workload_result, Mapping):
            metadata = workload_result.get("metadata")
            if isinstance(metadata, Mapping):
                candidates.append(metadata)
                nested_workload = metadata.get("workload")
                if isinstance(nested_workload, Mapping):
                    candidates.append(nested_workload)

        capture_input: dict[str, Any] = {}
        previous_capture = self._step_workspace_capture_inputs.get(
            logical_step_id, {}
        )
        capabilities: RuntimeExecutionCapabilities | None = None
        capability_snapshot = outputs.get("runtimeCapabilities") or outputs.get(
            "runtime_capabilities"
        )
        try:
            capability_policy_enabled = workflow.patched(
                RUN_RUNTIME_EXECUTION_CAPABILITIES_PATCH
            )
        except workflow._NotInWorkflowEventLoopError:
            # Pure unit callers model newly started histories.
            capability_policy_enabled = True
        if capability_policy_enabled:
            if isinstance(capability_snapshot, Mapping):
                capabilities = RuntimeExecutionCapabilities.model_validate(
                    capability_snapshot
                )
            elif agent_id:
                capabilities = resolve_runtime_execution_capabilities(agent_id)
            if capabilities is not None:
                capture_input["runtimeCapabilities"] = capabilities.model_dump(
                    by_alias=True, mode="json"
                )
                capture_input["captureAuthority"] = capabilities.workspace_authority
                capture_input["criticality"] = (
                    capabilities.post_execution_checkpoint_criticality
                )
                if capabilities.runtime_id == "omnigent":
                    try:
                        wf_info = workflow.info()
                        identity = StepExecutionIdentityModel(
                            workflowId=wf_info.workflow_id,
                            runId=wf_info.run_id,
                            logicalStepId=logical_step_id,
                            executionOrdinal=(
                                self._step_execution_for(logical_step_id) or 1
                            ),
                        )
                        locator_identity = (
                            f"{wf_info.workflow_id}:{build_step_execution_id(identity)}"
                        )
                        capture_input["workspaceLocator"] = {
                            "kind": "sandbox",
                            "workspaceId": hashlib.sha256(
                                locator_identity.encode("utf-8")
                            ).hexdigest()[:24],
                            "relativePath": "repo",
                        }
                    except workflow._NotInWorkflowEventLoopError:
                        # Pure unit callers cannot derive a durable workflow identity;
                        # the real workflow path always records this locator.
                        pass
        elif (
            agent_kind == "managed"
            or previous_capture.get("captureAuthority") == "managed_runtime"
        ):
            capture_input["captureAuthority"] = "managed_runtime"
        for candidate in candidates:
            workspace_locator = candidate.get("workspaceLocator") or candidate.get(
                "workspace_locator"
            )
            workspace_path = self._checkpoint_capture_text(
                candidate,
                "workspacePath",
                "workspace_path",
                "workspaceRoot",
                "workspace_root",
            )
            workspace_root_ref = self._checkpoint_capture_text(
                candidate,
                "workspaceRootRef",
                "workspace_root_ref",
            )
            external_state_ref = self._checkpoint_capture_text(
                candidate,
                "externalStateRef",
                "external_state_ref",
            )
            if isinstance(workspace_locator, Mapping):
                capture_input["workspaceLocator"] = dict(workspace_locator)
            elif workspace_path:
                capture_input["workspacePath"] = workspace_path
            elif workspace_root_ref:
                capture_input["workspaceRootRef"] = workspace_root_ref
            elif external_state_ref:
                capture_input["externalStateRef"] = external_state_ref
            else:
                continue

            base_commit = self._checkpoint_capture_text(
                candidate,
                "baseCommit",
                "base_commit",
            )
            if base_commit:
                capture_input["baseCommit"] = base_commit

            checkpoint_kind = self._checkpoint_capture_text(
                candidate,
                "checkpointKind",
                "checkpoint_kind",
                "workspaceCheckpointKind",
                "workspace_checkpoint_kind",
            )
            if checkpoint_kind:
                capture_input["kind"] = checkpoint_kind
            elif capabilities is None and (
                capability_policy_enabled
                or self._step_external_agent_ids.get(logical_step_id) != "omnigent"
            ):
                if base_commit:
                    capture_input["kind"] = "git_patch"
                else:
                    capture_input["kind"] = "worktree_archive"

            criticality = self._checkpoint_capture_text(
                candidate,
                "checkpointCriticality",
                "checkpoint_criticality",
            )
            if capabilities is None and criticality in {
                "required",
                "recoverability_only",
                "unsupported",
            }:
                capture_input["criticality"] = criticality
            break

        if capture_input:
            self._step_workspace_capture_inputs[logical_step_id] = capture_input

    async def _capture_canonical_step_checkpoint_workspace(
        self,
        logical_step_id: str,
        *,
        identity: StepExecutionIdentityModel,
        boundary: StepExecutionCheckpointBoundary,
    ) -> dict[str, Any] | None:
        capture_input = dict(
            self._step_workspace_capture_inputs.get(logical_step_id) or {}
        )
        if not capture_input:
            return None

        checkpoint_id = build_step_checkpoint_id(identity, boundary)
        managed_checkpoint_authority_enabled = workflow.patched(
            RUN_MANAGED_CHECKPOINT_AUTHORITY_PATCH
        )
        declared_authority = (
            capture_input.get("captureAuthority")
            if managed_checkpoint_authority_enabled
            and capture_input.get("captureAuthority")
            in {
                "moonmind_sandbox",
                "managed_runtime",
                "external_provider",
                "none",
            }
            else None
        )
        resolved_policy = resolve_checkpoint_policy(
            boundary=str(boundary),
            capabilities=(
                RuntimeExecutionCapabilities.model_validate(
                    capture_input["runtimeCapabilities"]
                )
                if isinstance(capture_input.get("runtimeCapabilities"), Mapping)
                else None
            ),
            workspace_locator=(
                capture_input.get("workspaceLocator")
                if isinstance(capture_input.get("workspaceLocator"), Mapping)
                else None
            ),
            recovery_source=self._recovery_source
            if isinstance(self._recovery_source, Mapping)
            else None,
            runtime_kind=(
                self._target_runtime
                if declared_authority in {"managed_runtime", "external_provider"}
                else None
            ),
            external_agent_id=self._step_external_agent_ids.get(logical_step_id),
            workspace_authority=declared_authority,
            agent_kind=(
                "managed"
                if managed_checkpoint_authority_enabled
                and capture_input.get("captureAuthority") == "managed_runtime"
                else None
            ),
        )
        managed_capture_enabled = workflow.patched(RUN_MANAGED_CHECKPOINT_CAPTURE_PATCH)
        supported_capture_activities = {"workspace.capture_checkpoint"}
        if managed_capture_enabled:
            supported_capture_activities.add(
                "agent_runtime.capture_workspace_checkpoint"
            )
        if resolved_policy.capture_activity not in supported_capture_activities:
            self._step_checkpoint_capture_outcomes[logical_step_id] = {
                "status": "unsupported",
                "failureCode": "CHECKPOINT_CAPABILITY_UNSUPPORTED",
                "boundary": str(boundary),
                "captureAuthority": resolved_policy.capture_authority,
                "captureActivity": (
                    None
                    if resolved_policy.capture_activity
                    == "agent_runtime.capture_workspace_checkpoint"
                    and not managed_capture_enabled
                    else resolved_policy.capture_activity
                ),
                "capabilityCriticality": resolved_policy.criticality,
            }
            return None

        if (
            resolved_policy.capture_activity
            == "agent_runtime.capture_workspace_checkpoint"
            and workflow.patched(RUN_MANAGED_CHECKPOINT_LOCATOR_GUARD_PATCH)
            and not isinstance(capture_input.get("workspaceLocator"), Mapping)
        ):
            # The parent cannot address a managed workspace until AgentRun returns
            # the runtime-owned locator. In particular, after_prepare and
            # before_execution run before the child exists. Defer capture instead
            # of sending a payload the strict activity contract must reject.
            self._step_checkpoint_capture_outcomes[logical_step_id] = {
                "status": "deferred",
                "failureCode": "CHECKPOINT_WORKSPACE_LOCATOR_UNAVAILABLE",
                "boundary": str(boundary),
                "captureAuthority": resolved_policy.capture_authority,
                "captureActivity": resolved_policy.capture_activity,
                "capabilityCriticality": resolved_policy.criticality,
            }
            return None

        claimed_kind = str(capture_input.get("kind") or "").strip() or None
        if (
            claimed_kind
            and claimed_kind not in resolved_policy.supported_checkpoint_kinds
        ):
            self._step_checkpoint_capture_outcomes[logical_step_id] = {
                "status": "failed",
                "failureCode": "CHECKPOINT_CAPABILITY_MISMATCH",
                "boundary": str(boundary),
                "claimedCheckpointKind": claimed_kind,
                "supportedCheckpointKind": resolved_policy.checkpoint_kind,
                "captureAuthority": resolved_policy.capture_authority,
            }
            raise RuntimeCapabilityError(
                f"checkpoint kind '{claimed_kind}' is incompatible with "
                f"{resolved_policy.capture_authority} capture; supported kinds: "
                f"{', '.join(resolved_policy.supported_checkpoint_kinds) or '(none)'}"
            )

        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            resolved_policy.capture_activity
        )
        payload = {
            "identity": identity.model_dump(by_alias=True, mode="json"),
            "boundary": boundary,
            "artifactNamespace": f"step-checkpoints/{identity.logical_step_id}",
            "idempotencyKey": f"{checkpoint_id}:capture",
        }
        if resolved_policy.capture_activity == "agent_runtime.capture_workspace_checkpoint":
            capabilities = RuntimeExecutionCapabilities.model_validate(
                capture_input["runtimeCapabilities"]
            )
            payload.update(
                {
                    "schemaVersion": "v1",
                    "checkpointKind": claimed_kind or resolved_policy.checkpoint_kind,
                    "expectedRuntimeId": capabilities.runtime_id,
                    "capabilitySetVersion": capabilities.capability_set_version,
                    "capabilityDigest": capabilities.capability_digest,
                    "capturePolicy": {
                        "includeTracked": True,
                        "includeUntracked": True,
                        "includeIgnored": False,
                        "redactionProfile": "managed-code-workspace-v1",
                    },
                }
            )
        else:
            payload["kind"] = claimed_kind or resolved_policy.checkpoint_kind
        if isinstance(capture_input.get("workspaceLocator"), Mapping):
            payload["workspaceLocator"] = dict(capture_input["workspaceLocator"])
        if capture_input.get("workspacePath"):
            payload["workspacePath"] = capture_input["workspacePath"]
        if capture_input.get("workspaceRootRef"):
            payload["workspaceRootRef"] = capture_input["workspaceRootRef"]
        if capture_input.get("externalStateRef"):
            payload["externalStateRef"] = capture_input["externalStateRef"]
        if capture_input.get("baseCommit"):
            payload["baseCommit"] = capture_input["baseCommit"]

        result = await workflow.execute_activity(
            route.activity_type,
            payload,
            **self._execute_kwargs_for_route(route),
        )
        if not isinstance(result, Mapping):
            return None
        if result.get("status") != "captured":
            return None
        workspace_evidence = result.get("workspace")
        if not isinstance(workspace_evidence, Mapping):
            return None
        if workspace_evidence.get("kind") == "ephemeral_workspace_ref":
            return None
        return {
            "workspace": dict(workspace_evidence),
            "diagnosticRefs": [
                str(ref).strip()
                for ref in result.get("diagnosticRefs", [])
                if str(ref).strip()
            ],
        }

    async def _record_canonical_step_checkpoint(
        self,
        logical_step_id: str,
        *,
        boundary: StepExecutionCheckpointBoundary,
        updated_at: datetime,
        step_outputs: Mapping[str, Any] | None = None,
        diagnostic_refs: Sequence[str] = (),
    ) -> str | None:
        if not workflow.patched(RUN_CANONICAL_STEP_CHECKPOINTS_PATCH):
            return None
        identity = self._canonical_step_checkpoint_identity(logical_step_id)
        if identity is None:
            return None
        task_input_snapshot_ref = (
            self._input_ref
            or f"temporal://{identity.workflow_id}/{identity.run_id}/task-input"
        )
        capture = await self._capture_canonical_step_checkpoint_workspace(
            logical_step_id,
            identity=identity,
            boundary=boundary,
        )
        if capture is None:
            return None
        capture_diagnostics = list(capture.get("diagnosticRefs") or [])
        result = await self._create_step_checkpoint_via_activity(
            identity=identity,
            boundary=boundary,
            task_input_snapshot_ref=task_input_snapshot_ref,
            workspace=capture["workspace"],
            created_at=updated_at,
            plan_ref=self._plan_ref
            or f"temporal://{identity.workflow_id}/{identity.run_id}/plan",
            prepared_input_refs=self._prepared_artifact_refs,
            step_outputs=step_outputs or self._step_execution_compact_output_refs(
                logical_step_id
            ),
            diagnostic_refs=[*capture_diagnostics, *diagnostic_refs],
        )
        checkpoint_ref = str(result.get("checkpointRef") or "").strip()
        if checkpoint_ref:
            self._step_checkpoint_refs[logical_step_id] = checkpoint_ref
        return checkpoint_ref or None

    def _record_primary_execution_outcome(
        self,
        logical_step_id: str,
        *,
        execution_result: Any,
        result_status: str,
        recorded_at: datetime,
    ) -> None:
        """Record canonical result refs before any auxiliary finalization starts."""

        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, dict):
            return
        compact_refs = self._proposal_step_output_refs(logical_step_id)
        output_refs = list(
            dict.fromkeys(
                str(value).strip()
                for key, value in compact_refs.items()
                if key.endswith("Ref") and isinstance(value, str) and value.strip()
            )
        )
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            outputs = {}
        diagnostics_ref = self._coerce_text(
            compact_refs.get("diagnosticsRef")
            or outputs.get("diagnosticsRef")
            or outputs.get("diagnostics_ref"),
            max_chars=400,
        )
        capture_input = self._step_workspace_capture_inputs.get(logical_step_id, {})
        raw_workspace_locator = capture_input.get("workspaceLocator")
        if isinstance(raw_workspace_locator, Mapping):
            # Preserve ownership/authority. Never collapse a new typed locator
            # to a path-like legacy projection.
            workspace_locator: dict[str, Any] | str | None = dict(raw_workspace_locator)
        else:
            # Read/write fallback retained only for histories whose capture
            # input predates typed locators.
            workspace_locator = self._coerce_text(
                capture_input.get("workspaceRootRef")
                or capture_input.get("workspacePath")
                or capture_input.get("externalStateRef"),
                max_chars=500,
            )
        result_ref = next(
            (
                compact_refs[key]
                for key in ("primaryRef", "summaryRef", "logsRef", "diagnosticsRef")
                if isinstance(compact_refs.get(key), str)
            ),
            None,
        )
        row["executionOutcome"] = {
            "status": "succeeded" if result_status == "COMPLETED" else "failed",
            "resultRef": result_ref,
            "outputRefs": output_refs,
            "diagnosticsRef": diagnostics_ref,
            "workspaceLocator": workspace_locator,
            "recordedAt": recorded_at.isoformat(),
        }
        row["finalizationOutcome"] = {
            "status": "not_started",
            "phase": "after_execution_checkpoint",
            "criticality": self._checkpoint_criticality(logical_step_id),
            "retryCount": 0,
            "updatedAt": recorded_at.isoformat(),
        }
        self._sync_progress_snapshot(updated_at=recorded_at)

    def _checkpoint_criticality(self, logical_step_id: str) -> str:
        capture_input = self._step_workspace_capture_inputs.get(logical_step_id, {})
        criticality = str(capture_input.get("criticality") or "required").strip()
        if criticality in {"required", "recoverability_only", "unsupported"}:
            return criticality
        return "required"

    async def _finalize_after_execution_checkpoint(
        self,
        logical_step_id: str,
        *,
        updated_at: datetime,
    ) -> None:
        """Isolate idempotent checkpoint finalization from the primary result."""

        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, dict):
            return
        previous_outcome = row.get("finalizationOutcome")
        if (
            isinstance(previous_outcome, Mapping)
            and previous_outcome.get("status") == "succeeded"
            and self._coerce_text(previous_outcome.get("checkpointRef"), max_chars=500)
        ):
            return
        retry_count = (
            int(previous_outcome.get("retryCount") or 0)
            if isinstance(previous_outcome, Mapping)
            else 0
        )
        criticality = self._checkpoint_criticality(logical_step_id)
        if criticality == "unsupported":
            row["finalizationOutcome"] = {
                "status": "unsupported",
                "phase": "after_execution_checkpoint",
                "criticality": criticality,
                "failureCode": None,
                "retryCount": 0,
                "message": "Checkpoint capture is unsupported by this workspace.",
                "updatedAt": updated_at.isoformat(),
            }
            return
        try:
            checkpoint_ref = await self._record_canonical_step_checkpoint(
                logical_step_id,
                boundary="after_execution",
                updated_at=updated_at,
            )
        except Exception as exc:
            status = "degraded" if criticality == "recoverability_only" else "failed"
            row["finalizationOutcome"] = {
                "status": status,
                "phase": "after_execution_checkpoint",
                "criticality": criticality,
                "failureCode": FINALIZATION_CHECKPOINT_FAILED,
                "terminalFailureCode": FINALIZATION_RETRY_EXHAUSTED,
                "retryCount": retry_count + 1,
                "message": self._bounded_operator_failure(exc),
                "updatedAt": workflow.now().isoformat(),
            }
            self._summary = (
                "Execution succeeded; finalization failed during the "
                "after-execution checkpoint."
            )
            self._attention_required = criticality == "required"
            self._update_memo()
            return
        capture_outcome = self._step_checkpoint_capture_outcomes.get(logical_step_id)
        if not checkpoint_ref and isinstance(capture_outcome, Mapping):
            capability_criticality = str(
                capture_outcome.get("capabilityCriticality") or criticality
            ).strip()
            if capability_criticality in {
                "required",
                "recoverability_only",
                "unsupported",
            }:
                criticality = capability_criticality
            failure_code = self._coerce_text(
                capture_outcome.get("failureCode"), max_chars=100
            )
            row["finalizationOutcome"] = {
                "status": "unsupported",
                "phase": "after_execution_checkpoint",
                "criticality": criticality,
                "failureCode": failure_code,
                "terminalFailureCode": None,
                "retryCount": 0,
                "checkpointRef": None,
                "message": "Checkpoint capture is unsupported by this runtime.",
                "updatedAt": updated_at.isoformat(),
            }
            return
        row["finalizationOutcome"] = {
            "status": "succeeded" if checkpoint_ref else "unsupported",
            "phase": "after_execution_checkpoint",
            "criticality": criticality,
            "failureCode": None,
            "retryCount": retry_count,
            "checkpointRef": checkpoint_ref,
            "message": None if checkpoint_ref else "No checkpoint-capable workspace was available.",
            "updatedAt": workflow.now().isoformat(),
        }
        if checkpoint_ref and retry_count:
            self._summary = "Execution succeeded; finalization retry completed."
            self._attention_required = False
            self._update_memo()

    def _record_publication_finalization_failure(
        self,
        logical_step_id: str,
        *,
        exc: Exception,
        updated_at: datetime,
    ) -> None:
        row = self._step_ledger_row_for(logical_step_id)
        if not isinstance(row, dict):
            return
        previous = row.get("finalizationOutcome")
        retry_count = 1
        if isinstance(previous, Mapping):
            retry_count = int(previous.get("retryCount") or 0) + 1
        row["finalizationOutcome"] = {
            "status": "failed",
            "phase": "publication",
            "criticality": "required",
            "failureCode": FINALIZATION_PUBLICATION_FAILED,
            "terminalFailureCode": FINALIZATION_RETRY_EXHAUSTED,
            "retryCount": retry_count,
            "message": self._bounded_operator_failure(exc),
            "updatedAt": updated_at.isoformat(),
        }
        previous_publish_reason = self._publish_reason
        self._publish_status = "failed"
        self._publish_reason = previous_publish_reason or (
            "Execution succeeded; finalization failed during publication."
        )
        self._summary = "Execution succeeded; finalization failed during publication."
        self._attention_required = True
        self._update_memo()

    async def _record_prepublication_checkpoint(
        self,
        logical_step_id: str,
        *,
        publish_mode: str,
        updated_at: datetime,
    ) -> bool:
        """Return whether a required pre-publication checkpoint failed."""

        if (
            workflow.patched(RUN_SKIP_NO_PUBLISH_PREPUBLICATION_CHECKPOINT_PATCH)
            and publish_mode == "none"
        ):
            return False
        try:
            await self._record_canonical_step_checkpoint(
                logical_step_id,
                boundary="before_publication",
                updated_at=updated_at,
            )
        except Exception as exc:
            if not workflow.patched(RUN_DURABLE_FINALIZATION_OUTCOME_PATCH):
                raise
            row = self._step_ledger_row_for(logical_step_id)
            if isinstance(row, dict):
                row["finalizationOutcome"] = {
                    "status": "failed",
                    "phase": "before_publication_checkpoint",
                    "criticality": "required",
                    "failureCode": FINALIZATION_CHECKPOINT_FAILED,
                    "terminalFailureCode": FINALIZATION_RETRY_EXHAUSTED,
                    "retryCount": 1,
                    "message": self._bounded_operator_failure(exc),
                    "updatedAt": workflow.now().isoformat(),
                }
            self._summary = (
                "Execution succeeded; finalization failed during the "
                "pre-publication checkpoint."
            )
            self._publish_status = "failed"
            self._publish_reason = self._summary
            self._attention_required = True
            self._update_memo()
            return True
        return False

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
        if normalized == "FULLY_IMPLEMENTED":
            return "passed"
        if normalized in {
            "ADDITIONAL_WORK_NEEDED",
            "BLOCKED",
            "FAILED_UNRECOVERABLE",
            "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
        }:
            return "failed"
        return "inconclusive"

    def _accepted_review_summary(self, verdict: str, *, retry_count: int) -> str:
        normalized = str(verdict or "").strip().upper()
        if normalized != "FULLY_IMPLEMENTED":
            return "Structured gate did not approve advancement"
        if retry_count > 0:
            retry_label = "retry" if retry_count == 1 else "retries"
            return f"Approved after {retry_count} {retry_label}"
        return "Approved by structured review"

    def _is_moonspec_verify_step(
        self,
        *,
        tool_name: str,
        node_inputs: Mapping[str, Any],
    ) -> bool:
        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool == "moonspec-verify":
            return True
        for key in ("selectedSkill", "skillId", "skill", "targetSkill"):
            value = node_inputs.get(key)
            if isinstance(value, str) and value.strip().lower() == "moonspec-verify":
                return True
            if isinstance(value, Mapping):
                nested = value.get("id") or value.get("name")
                if (
                    isinstance(nested, str)
                    and nested.strip().lower() == "moonspec-verify"
                ):
                    return True
        return False

    @staticmethod
    def _node_inputs_mapping(node: Mapping[str, Any]) -> Mapping[str, Any]:
        inputs = node.get("inputs")
        return inputs if isinstance(inputs, Mapping) else {}

    def _node_annotations_mapping(
        self,
        node: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        node_inputs = self._node_inputs_mapping(node)
        annotations = node_inputs.get("annotations") or node.get("annotations")
        return annotations if isinstance(annotations, Mapping) else {}

    @staticmethod
    def _moonspec_title_remediation_detection_enabled() -> bool:
        try:
            workflow.info()
        except Exception:
            return True
        try:
            return workflow.patched(RUN_MOONSPEC_TITLE_REMEDIATION_DETECTION_PATCH)
        except Exception:
            return True

    def _moonspec_remediation_title_role(self, node: Mapping[str, Any]) -> str:
        if not self._moonspec_title_remediation_detection_enabled():
            return ""
        node_inputs = self._node_inputs_mapping(node)
        title = (
            str(node_inputs.get("title") or node.get("title") or "")
            .strip()
            .lower()
        )
        if title.startswith("remediate verification gaps") or title.startswith(
            "remediate remaining gaps"
        ):
            return "moonspec-remediation"
        if title.startswith("verify remediation"):
            return "moonspec-verification-gate"
        return ""

    def _moonspec_step_role(self, node: Mapping[str, Any]) -> str:
        annotations = self._node_annotations_mapping(node)
        for key in ("jiraOrchestrateRole", "issueImplementRole"):
            role = str(annotations.get(key) or "").strip().lower()
            if role:
                return role
        return self._moonspec_remediation_title_role(node)

    @staticmethod
    def _coerce_positive_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return None
        return coerced if coerced >= 1 else None

    def _moonspec_remediation_attempt_metadata(
        self,
        node: Mapping[str, Any],
    ) -> tuple[int | None, int | None]:
        annotations = self._node_annotations_mapping(node)
        attempt = self._coerce_positive_int(
            annotations.get("moonSpecRemediationAttempt")
        )
        max_attempts = self._coerce_positive_int(
            annotations.get("moonSpecRemediationMaxAttempts")
        )
        if attempt is None or max_attempts is None:
            node_inputs = self._node_inputs_mapping(node)
            title = str(node_inputs.get("title") or node.get("title") or "").strip()
            if self._moonspec_remediation_title_role(node):
                match = _MOONSPEC_REMEDIATION_TITLE_ATTEMPT_PATTERN.search(title)
                if match:
                    title_attempt = self._coerce_positive_int(
                        match.group("attempt")
                    )
                    title_max_attempts = self._coerce_positive_int(
                        match.group("max_attempts")
                    )
                    if attempt is None:
                        attempt = title_attempt
                    if max_attempts is None:
                        max_attempts = title_max_attempts
        return attempt, max_attempts

    def _moonspec_remediation_attempt_within_budget(
        self,
        node: Mapping[str, Any],
    ) -> bool:
        attempt, max_attempts = self._moonspec_remediation_attempt_metadata(node)
        if attempt is None or max_attempts is None:
            return True
        return attempt <= max_attempts

    def _moonspec_remediation_budget_metadata(
        self,
        *,
        ordered_nodes: Sequence[Mapping[str, Any]],
        current_attempt: int | None,
        max_attempts: int | None,
    ) -> dict[str, Any]:
        """Project remediation consumption from actual active step-ledger rows."""
        remediation_ids = [
            str(candidate.get("id") or "")
            for candidate in ordered_nodes
            if self._is_moonspec_remediation_step(candidate)
            and self._moonspec_remediation_attempt_within_budget(candidate)
        ]
        attempts_started = sum(
            1
            for step_id in remediation_ids
            if step_id and (self._step_execution_for(step_id) or 0) > 0
        )
        attempts_completed = sum(
            1
            for step_id in remediation_ids
            if step_id
            and str((self._step_ledger_row_for(step_id) or {}).get("status") or "")
            == "completed"
        )
        return {
            "maxAttempts": max_attempts,
            "currentAttempt": current_attempt,
            "attemptsStarted": attempts_started,
            "attemptsCompleted": attempts_completed,
            "remainingAttempts": (
                max(0, max_attempts - attempts_started)
                if max_attempts is not None
                else None
            ),
            "exhausted": bool(
                max_attempts is not None and attempts_started >= max_attempts
            ),
        }

    @staticmethod
    def _accepted_verifier_semantic_verdict(verdict: str) -> str:
        """Preserve the verifier-owned semantic result in control evidence."""
        return verdict

    def _is_moonspec_remediation_step(self, node: Mapping[str, Any]) -> bool:
        node_inputs = self._node_inputs_mapping(node)
        if self._moonspec_step_role(node) == "moonspec-remediation":
            return True
        skill_node = node.get("skill")
        skill_id_from_node = (
            skill_node.get("id") or skill_node.get("name")
            if isinstance(skill_node, Mapping)
            else skill_node
        )
        selected_skill = str(
            node_inputs.get("selectedSkill")
            or node_inputs.get("skillId")
            or node_inputs.get("targetSkill")
            or skill_id_from_node
            or ""
        ).strip().lower()
        if selected_skill != "moonspec-implement":
            return False
        title = (
            str(node_inputs.get("title") or node.get("title") or "")
            .strip()
            .lower()
        )
        return title.startswith("remediate verification gaps") or title.startswith(
            "remediate remaining gaps"
        )

    def _has_remaining_moonspec_remediation_step(
        self,
        *,
        ordered_nodes: Sequence[Mapping[str, Any]],
        current_index: int,
    ) -> bool:
        for node in ordered_nodes[current_index + 1:]:
            if self._is_moonspec_remediation_step(
                node
            ) and self._moonspec_remediation_attempt_within_budget(node):
                return True
        return False

    def _resolve_next_moonspec_remediation_step(
        self,
        *,
        ordered_nodes: Sequence[Mapping[str, Any]],
        current_index: int,
    ) -> tuple[MoonSpecRemediationSuccessor | None, str]:
        """Resolve one exact successor after validating the annotated topology."""
        if current_index < 0 or current_index >= len(ordered_nodes):
            return None, "no_remediation_successor"
        current = ordered_nodes[current_index]
        current_role = self._moonspec_step_role(current)
        current_inputs = self._node_inputs_mapping(current)
        current_tool = self._plan_node_tool_mapping(current) or {}
        current_tool_name = str(
            current_tool.get("name") or current_tool.get("id") or ""
        )
        if current_role != "moonspec-verification-gate" and not (
            not current_role
            and self._is_moonspec_verify_step(
                tool_name=current_tool_name,
                node_inputs=current_inputs,
            )
        ):
            return None, "not_explicit_remediation_chain"
        current_attempt, current_max = self._moonspec_remediation_attempt_metadata(
            current
        )
        if current_attempt is None and current_role != "moonspec-verification-gate":
            current_attempt = 0
        if current_max is None and current_attempt == 0:
            declared_maxima = {
                maximum
                for candidate in ordered_nodes[current_index + 1 :]
                if self._moonspec_step_role(candidate)
                in {"moonspec-remediation", "moonspec-verification-gate"}
                for _, maximum in [
                    self._moonspec_remediation_attempt_metadata(candidate)
                ]
                if maximum is not None
            }
            if len(declared_maxima) == 1:
                current_max = next(iter(declared_maxima))
        if current_attempt is None or current_max is None:
            return None, "no_remediation_successor"
        if current_attempt >= current_max:
            return None, "remediation_budget_exhausted"

        expected_attempt = current_attempt + 1
        candidates: list[MoonSpecRemediationSuccessor] = []
        seen_attempts: set[int] = set()
        attempts_by_role: dict[str, set[int]] = {
            "moonspec-remediation": set(),
            "moonspec-verification-gate": set(),
        }
        final_gate_attempts: set[int] = set()
        malformed = False
        for node_index, candidate in enumerate(ordered_nodes):
            role = self._moonspec_step_role(candidate)
            if role not in {"moonspec-remediation", "moonspec-verification-gate"}:
                continue
            attempt, max_attempts = self._moonspec_remediation_attempt_metadata(
                candidate
            )
            if attempt is None or max_attempts != current_max:
                malformed = True
                continue
            if attempt > current_max:
                continue
            identity = (0 if role == "moonspec-remediation" else current_max) + attempt
            if identity in seen_attempts:
                malformed = True
            seen_attempts.add(identity)
            attempts_by_role[role].add(attempt)
            if (
                role == "moonspec-verification-gate"
                and self._node_annotations_mapping(candidate).get(
                    "moonSpecFinalRemediationGate"
                )
                is True
            ):
                final_gate_attempts.add(attempt)
            if role == "moonspec-remediation" and attempt == expected_attempt:
                candidates.append(
                    MoonSpecRemediationSuccessor(
                        logical_step_id=str(candidate.get("id") or ""),
                        attempt=attempt,
                        max_attempts=max_attempts,
                        node_index=node_index,
                    )
                )
        expected_attempts = set(range(1, current_max + 1))
        if (
            malformed
            or len(candidates) != 1
            or attempts_by_role["moonspec-remediation"] != expected_attempts
            or attempts_by_role["moonspec-verification-gate"] != expected_attempts
            or final_gate_attempts != {current_max}
        ):
            return None, "no_remediation_successor"
        successor = candidates[0]
        if successor.node_index != current_index + 1 or not successor.logical_step_id:
            return None, "no_remediation_successor"
        return successor, "verification_requested_remediation"

    def _resolve_gate_transition(
        self,
        *,
        verdict: Any,
        ordered_nodes: Sequence[Mapping[str, Any]],
        current_index: int,
    ) -> GateTransitionDecision:
        """Keep verifier semantics advisory and make plan routing authoritative."""
        normalized = str(getattr(verdict, "verdict", "") or "").strip().upper()
        explicit_chain = False
        if 0 <= current_index < len(ordered_nodes):
            current = ordered_nodes[current_index]
            current_role = self._moonspec_step_role(current)
            current_tool = self._plan_node_tool_mapping(current) or {}
            explicit_chain = current_role == "moonspec-verification-gate" or (
                not current_role
                and self._is_moonspec_verify_step(
                    tool_name=str(
                        current_tool.get("name") or current_tool.get("id") or ""
                    ),
                    node_inputs=self._node_inputs_mapping(current),
                )
                and any(
                    self._moonspec_step_role(candidate) == "moonspec-remediation"
                    for candidate in ordered_nodes[current_index + 1 :]
                )
            )
        if not explicit_chain:
            return GateTransitionDecision(
                "generic", "existing_gate_policy", "not_explicit_remediation_chain"
            )
        if bool(getattr(verdict, "invalid", False)) or bool(
            getattr(verdict, "degraded", False)
        ):
            return GateTransitionDecision(
                "invalid", "fail_invalid_result", "invalid_gate_result"
            )
        if normalized == "FULLY_IMPLEMENTED":
            return GateTransitionDecision(
                "accept", "exit_remediation_loop", "verification_passed"
            )
        if normalized == "ADDITIONAL_WORK_NEEDED":
            successor, reason = self._resolve_next_moonspec_remediation_step(
                ordered_nodes=ordered_nodes,
                current_index=current_index,
            )
            if successor is not None:
                return GateTransitionDecision(
                    "accept", "advance_to_next_remediation", reason, successor
                )
            return GateTransitionDecision(
                "accept", "stop_at_control_gate", reason
            )
        if normalized == "NO_DETERMINATION":
            if bool(getattr(verdict, "recoverable_in_current_runtime", False)):
                return GateTransitionDecision(
                    "retry", "retry_current_verifier", "recoverable_no_determination"
                )
            return GateTransitionDecision(
                "accept", "stop_at_control_gate", "unrecoverable_no_determination"
            )
        if normalized in {
            "BLOCKED",
            "FAILED_UNRECOVERABLE",
            "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
        }:
            return GateTransitionDecision(
                "accept", "stop_at_control_gate", "terminal_gate_verdict"
            )
        return GateTransitionDecision(
            "invalid", "fail_invalid_result", "invalid_gate_result"
        )

    def _record_moonspec_gate_transition_event(
        self,
        *,
        logical_step_id: str,
        node: Mapping[str, Any],
        verdict: str,
        transition: GateTransitionDecision,
        review_retries_consumed: int,
    ) -> None:
        """Emit one compact structured event for each plan-routed verifier result."""
        remediation_attempt, remediation_max = (
            self._moonspec_remediation_attempt_metadata(node)
        )
        self._get_logger().info(
            "moonspec_gate_transition %s",
            json.dumps(
                {
                    "event": "moonspec_gate_transition",
                    "logicalStepId": logical_step_id,
                    "verdict": verdict,
                    "disposition": transition.routing_disposition,
                    "reasonCode": transition.reason_code,
                    "nextLogicalStepId": (
                        transition.successor.logical_step_id
                        if transition.successor is not None
                        else None
                    ),
                    "reviewRetriesConsumed": review_retries_consumed,
                    "remediationAttempt": remediation_attempt,
                    "remediationMaxAttempts": remediation_max,
                },
                sort_keys=True,
            ),
        )

    def _moonspec_remediation_loop_skip_reason(
        self,
        node: Mapping[str, Any],
        *,
        tool_name: str,
        node_inputs: Mapping[str, Any],
    ) -> str | None:
        role = self._moonspec_step_role(node)
        is_remediation = self._is_moonspec_remediation_step(node)
        is_verification_gate = (
            role == "moonspec-verification-gate"
            and self._is_moonspec_verify_step(
                tool_name=tool_name,
                node_inputs=node_inputs,
            )
        )
        if not (is_remediation or is_verification_gate):
            return None

        attempt, max_attempts = self._moonspec_remediation_attempt_metadata(node)
        if (
            attempt is not None
            and max_attempts is not None
            and attempt > max_attempts
        ):
            return (
                "Skipped MoonSpec remediation loop step "
                f"{attempt}; configured maximum is {max_attempts}."
            )

        verdict = self._normalize_moonspec_verify_verdict(self._moonspec_gate_verdict)
        if verdict in _MOONSPEC_GATE_PASSING_VERDICTS:
            return (
                "Skipped MoonSpec remediation loop step because verification already "
                f"passed with verdict {verdict}."
            )
        return None

    def _extract_moonspec_verify_verdict(
        self,
        outputs: Mapping[str, Any],
    ) -> str | None:
        for source in self._moonspec_verify_sources(outputs):
            for key in (
                "verdict",
                "gateVerdict",
                "gate_verdict",
                "moonSpecVerdict",
                "moonspecVerdict",
                "verificationVerdict",
                "verification_verdict",
            ):
                raw_verdict = source.get(key)
                verdict = self._normalize_moonspec_verify_verdict(raw_verdict)
                if verdict:
                    return verdict
                if isinstance(raw_verdict, str) and raw_verdict.strip():
                    return "NO_DETERMINATION"
        return None

    def _moonspec_verify_sources(
        self,
        outputs: Mapping[str, Any],
    ) -> Iterable[Mapping[str, Any]]:
        for key in (
            "moonSpecVerify",
            "moonspecVerify",
            "moonspec_verify",
            "verification",
            "verificationResult",
            "verification_result",
            "gate",
            "review",
        ):
            candidate = outputs.get(key)
            if isinstance(candidate, Mapping):
                yield candidate
        yield outputs

    @staticmethod
    def _normalize_moonspec_verify_verdict(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
        if normalized == "PASS":
            normalized = "FULLY_IMPLEMENTED"
        elif normalized == "FAIL":
            normalized = "ADDITIONAL_WORK_NEEDED"
        if (
            normalized in _MOONSPEC_GATE_PASSING_VERDICTS
            or normalized in _MOONSPEC_GATE_BLOCKING_VERDICTS
        ):
            return normalized
        return None

    def _extract_moonspec_verify_verdict_from_text(self, value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        text = value[:4000]
        match = _MOONSPEC_GATE_VERDICT_TEXT_PATTERN.search(text)
        if not match:
            return None
        return self._normalize_moonspec_verify_verdict(match.group(1))

    def _moonspec_verify_report_text(
        self,
        outputs: Mapping[str, Any],
    ) -> str | None:
        for source in self._moonspec_verify_sources(outputs):
            for key in (
                "moonSpecVerifyReport",
                "moonspecVerifyReport",
                "verificationReport",
                "verification_report",
                "lastAssistantText",
                "assistantText",
                "summary",
                "message",
            ):
                value = source.get(key)
                if not isinstance(value, str):
                    continue
                text = value.strip()
                if (
                    "# MoonSpec Verification Report" in text
                    and "**Verdict**" in text
                ):
                    return text
            turn_metadata = source.get("turnMetadata")
            if isinstance(turn_metadata, Mapping):
                for key in ("assistantText", "lastAssistantText"):
                    value = turn_metadata.get(key)
                    if not isinstance(value, str):
                        continue
                    text = value.strip()
                    if (
                        "# MoonSpec Verification Report" in text
                        and "**Verdict**" in text
                    ):
                        return text
        return None

    def _moonspec_verify_gate_result(
        self,
        outputs: Mapping[str, Any],
    ) -> StepGateResult:
        payload: dict[str, Any] = {}
        for source in self._moonspec_verify_sources(outputs):
            for key in (
                "verdict",
                "gateVerdict",
                "gate_verdict",
                "moonSpecVerdict",
                "moonspecVerdict",
                "verificationVerdict",
                "verification_verdict",
            ):
                if source.get(key):
                    payload = dict(source)
                    payload["verdict"] = source.get(key)
                    return parse_step_gate_result(payload)

        return parse_step_gate_result({})

    def _moonspec_verify_gate_result_ref(
        self,
        outputs: Mapping[str, Any],
    ) -> str | None:
        for source in self._moonspec_verify_sources(outputs):
            for key in (
                "gateResultRef",
                "gate_result_ref",
                "stepGateResultRef",
                "step_gate_result_ref",
                "artifactRef",
                "artifact_ref",
            ):
                gate_result_ref = self._coerce_text(source.get(key), max_chars=400)
                if gate_result_ref:
                    return gate_result_ref
        return None

    def _moonspec_verify_contract_repair_feedback(
        self,
        *,
        execution_result: Any,
        tool_name: str,
        node_inputs: Mapping[str, Any],
    ) -> str | None:
        """Corrective feedback when a verify step's gate output is malformed.

        Returns text only for moonspec-verify steps whose structured gate
        payload failed contract validation (invalid/degraded), meaning the
        gate could not trust the verdict envelope. A ``None`` result means
        the output is either not a verify step or contract-clean.
        """
        if not self._is_moonspec_verify_step(
            tool_name=tool_name,
            node_inputs=node_inputs,
        ):
            return None
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return None
        gate_result = self._moonspec_verify_gate_result(outputs)
        if not (gate_result.invalid or gate_result.degraded):
            return None
        detail = gate_result.downgrade_reason or (
            "the structured verifier JSON was missing or failed contract "
            "validation"
        )
        verdicts = ", ".join(sorted(REVIEW_VERDICTS))
        next_actions = ", ".join(recommended_next_actions())
        return (
            "MoonSpec verify gate contract repair required: "
            f"{detail}. Rewrite the structured verifier JSON at the "
            "instructed verify artifact path using only canonical field "
            f"values (verdict one of: {verdicts}; recommendedNextAction one "
            f"of: {next_actions}), re-verify against the current workspace "
            "state, and finish with the Markdown MoonSpec Verification "
            "Report. Do not modify implementation source in this attempt."
        )

    def _record_moonspec_verify_gate(
        self,
        *,
        node_id: str,
        outputs: Mapping[str, Any],
    ) -> None:
        gate_result = self._moonspec_verify_gate_result(outputs)
        verdict = gate_result.verdict
        reason = (
            "MoonSpec verification report was present but structured gate output "
            "was missing."
            if gate_result.degraded and self._moonspec_verify_report_text(outputs)
            else None
        )
        if reason is None:
            for source in self._moonspec_verify_sources(outputs):
                reason = self._sanitize_operator_summary(
                    self._coerce_text(
                        source.get("operator_summary")
                        or source.get("operatorSummary")
                        or source.get("summary")
                        or source.get("message"),
                        max_chars=700,
                    )
                )
                if reason:
                    break
        diagnostics_ref = None
        for source in self._moonspec_verify_sources(outputs):
            diagnostics_ref = self._coerce_text(
                source.get("diagnostics_ref")
                or source.get("diagnosticsRef")
                or source.get("verificationReportRef")
                or source.get("verification_report_ref")
                or source.get("reportRef"),
                max_chars=400,
            )
            if diagnostics_ref:
                break
        self._moonspec_gate_verdict = verdict
        self._moonspec_gate_reason = reason
        declared_verdict = None
        for source in self._moonspec_verify_sources(outputs):
            declared_verdict = self._normalize_moonspec_verify_verdict(
                source.get("verdict")
            )
            if declared_verdict:
                break
        gate_context: dict[str, Any] = {
            "logicalStepId": node_id,
            "verdict": verdict,
            "declaredVerdict": declared_verdict,
            "confidence": gate_result.confidence,
            "validatedRefs": dict(gate_result.validated_refs or {}),
            "invalidatedRefs": list(gate_result.invalidated_refs),
            "remainingWorkRef": gate_result.remaining_work_ref,
            "recommendedNextAction": gate_result.recommended_next_action,
            "targetLogicalStepId": gate_result.target_logical_step_id,
            "workspacePolicyRecommendation": (
                gate_result.workspace_policy_recommendation
            ),
            "recoverableInCurrentRuntime": (
                gate_result.recoverable_in_current_runtime
            ),
            "invalid": gate_result.invalid,
            "degraded": gate_result.degraded,
        }
        if gate_result.downgrade_reason:
            gate_context["downgradeReason"] = gate_result.downgrade_reason
        gate_result_ref = self._moonspec_verify_gate_result_ref(outputs)
        if gate_result_ref:
            gate_context["gateResultRef"] = gate_result_ref
        if reason:
            gate_context["summary"] = reason
        if diagnostics_ref:
            gate_context["diagnosticsRef"] = diagnostics_ref
        self._publish_context["moonSpecGate"] = gate_context

    @staticmethod
    def _bounded_story_loop_artifact_ref(value: Any) -> str | None:
        ref = str(value or "").strip()
        if ref.startswith("artifact://"):
            return ref
        return None

    def _bounded_story_loop_gate_from_step_gate(
        self,
        *,
        gate_result: StepGateResult,
        gate_result_ref: str | None,
        logical_step_id: str,
        progress_budget_enabled: bool,
    ) -> TypedGateResult:
        terminal = self._step_terminal_dispositions.get(logical_step_id)
        terminal_disposition = (
            "accepted" if terminal == "accepted" else "failed_with_remaining_work"
        )
        progress_payload = {
            "verdict": gate_result.verdict,
            "issues": [dict(issue) for issue in gate_result.issues],
            "remainingWorkRef": gate_result.remaining_work_ref,
            "blockingEvidenceRefs": list(gate_result.blocking_evidence_refs),
            "recommendedNextAction": gate_result.recommended_next_action,
            "workspacePolicyRecommendation": (
                gate_result.workspace_policy_recommendation
            ),
            "recoverableInCurrentRuntime": gate_result.recoverable_in_current_runtime,
        }
        if self._patched_or_false_outside_workflow(
            RUN_BOUNDED_STORY_LOOP_FEEDBACK_PROGRESS_PATCH
        ):
            # Verifiers may report their remaining gaps in feedback even when
            # they omit the optional structured issues/remainingWorkRef fields.
            # Excluding that authoritative summary makes distinct verifier
            # reports look identical and can exhaust the no-progress budget
            # after the first remediation attempt.
            progress_payload["feedback"] = gate_result.feedback
        progress_signature = hashlib.sha256(
            json.dumps(
                progress_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return TypedGateResult.model_validate(
            {
                "verdict": gate_result.verdict,
                "terminalDisposition": terminal_disposition,
                "gateResultRef": self._bounded_story_loop_artifact_ref(
                    gate_result_ref
                ),
                # The verifier report is itself the authoritative remaining-work
                # evidence when it does not publish a separate extracted artifact.
                # Never admit ADDITIONAL_WORK_NEEDED without a durable reference.
                "remainingWorkRef": self._bounded_story_loop_artifact_ref(
                    gate_result.remaining_work_ref
                )
                or (
                    self._bounded_story_loop_artifact_ref(gate_result_ref)
                    if gate_result.verdict == "ADDITIONAL_WORK_NEEDED"
                    else None
                ),
                "diagnosticsRef": self._bounded_story_loop_artifact_ref(
                    next(iter(gate_result.blocking_evidence_refs), None)
                ),
                "progressSignature": (
                    progress_signature if progress_budget_enabled else None
                ),
                "degraded": gate_result.degraded,
            }
        )

    @staticmethod
    def _bounded_story_loop_workflow_identity() -> tuple[str, str]:
        try:
            info = workflow.info()
            return info.workflow_id, info.run_id
        except Exception:
            return "unit-test-workflow", "unit-test-run"

    def _bounded_story_loop_attempt_for_gate(
        self,
        *,
        logical_step_id: str,
        remediation_gate: bool,
    ) -> LoopAttempt:
        execution_ordinal = self._step_execution_for(logical_step_id) or 1
        workflow_id, run_id = self._bounded_story_loop_workflow_identity()
        identity = StepExecutionIdentityModel(
            workflowId=workflow_id,
            runId=run_id,
            logicalStepId=logical_step_id,
            executionOrdinal=execution_ordinal,
        )
        output_refs = self._step_execution_compact_output_refs(logical_step_id)
        accepted_output_ref = self._bounded_story_loop_artifact_ref(
            output_refs.get("primaryRef") or output_refs.get("summaryRef")
        )
        return LoopAttempt.model_validate(
            {
                "attemptOrdinal": execution_ordinal,
                "kind": "remediation" if remediation_gate else "implementation",
                "stepExecutionId": build_step_execution_id(identity),
                "checkpointBeforeRef": self._bounded_story_loop_artifact_ref(
                    self._step_checkpoint_refs.get(logical_step_id)
                ),
                "checkpointAfterRef": self._bounded_story_loop_artifact_ref(
                    self._step_checkpoint_refs.get(logical_step_id)
                ),
                "acceptedOutputRef": accepted_output_ref,
                "terminalDisposition": (
                    self._step_terminal_dispositions.get(logical_step_id)
                    or "failed_with_remaining_work"
                ),
            }
        )

    def _bounded_story_loop_continuation_decision(
        self,
        *,
        logical_step_id: str,
        gate_result: StepGateResult,
        gate_result_ref: str | None,
        ordered_nodes: Sequence[Mapping[str, Any]],
        current_index: int,
        plan_routed_moonspec_remediation_enabled: bool = True,
    ) -> dict[str, Any]:
        remediation_gate = current_index > 1 and self._is_moonspec_remediation_step(
            ordered_nodes[current_index - 2]
        )
        successor: MoonSpecRemediationSuccessor | None = None
        successor_reason = "legacy_remaining_remediation_scan"
        if not plan_routed_moonspec_remediation_enabled:
            has_remaining_remediation_step = (
                self._has_remaining_moonspec_remediation_step(
                    ordered_nodes=ordered_nodes,
                    current_index=current_index,
                )
            )
        else:
            successor, successor_reason = self._resolve_next_moonspec_remediation_step(
                ordered_nodes=ordered_nodes,
                current_index=current_index,
            )
            annotated_topology_declared = any(
                self._coerce_positive_int(
                    self._node_annotations_mapping(candidate).get(
                        "moonSpecRemediationAttempt"
                    )
                )
                is not None
                and self._coerce_positive_int(
                    self._node_annotations_mapping(candidate).get(
                        "moonSpecRemediationMaxAttempts"
                    )
                )
                is not None
                for candidate in ordered_nodes
                if self._moonspec_step_role(candidate)
                in {"moonspec-remediation", "moonspec-verification-gate"}
            )
            if successor_reason == "not_explicit_remediation_chain" or (
                successor_reason == "no_remediation_successor"
                and not annotated_topology_declared
            ):
                has_remaining_remediation_step = (
                    self._has_remaining_moonspec_remediation_step(
                        ordered_nodes=ordered_nodes,
                        current_index=current_index,
                    )
                )
            else:
                has_remaining_remediation_step = successor is not None
        progress_budget_enabled = self._patched_or_false_outside_workflow(
            RUN_BOUNDED_STORY_LOOP_PROGRESS_BUDGET_PATCH
        )
        gate = self._bounded_story_loop_gate_from_step_gate(
            gate_result=gate_result,
            gate_result_ref=gate_result_ref,
            logical_step_id=logical_step_id,
            progress_budget_enabled=progress_budget_enabled,
        )
        attempt = self._bounded_story_loop_attempt_for_gate(
            logical_step_id=logical_step_id,
            remediation_gate=remediation_gate,
        )
        loop_context = self._publish_context.get("boundedStoryLoop")
        prior_progress_signature = None
        prior_no_progress_attempts = 0
        if isinstance(loop_context, Mapping):
            prior_decision = loop_context.get("continuationDecision")
            if isinstance(prior_decision, Mapping):
                prior_gate = prior_decision.get("gate")
                if isinstance(prior_gate, Mapping):
                    prior_progress_signature = str(
                        prior_gate.get("progressSignature") or ""
                    ).strip() or None
                prior_budget = prior_decision.get("budget")
                if isinstance(prior_budget, Mapping):
                    prior_consumed = prior_budget.get("consumed")
                    if isinstance(prior_consumed, Mapping):
                        prior_no_progress_attempts = int(
                            prior_consumed.get(
                                "consecutiveNoProgressAttempts", 0
                            )
                            or 0
                        )
        repeated_progress = bool(
            progress_budget_enabled
            and gate.progress_signature
            and prior_progress_signature == gate.progress_signature
        )
        consecutive_no_progress_attempts = (
            prior_no_progress_attempts + 1 if repeated_progress else 0
        )
        max_no_progress_attempts = 1
        explicit_no_progress_budget = False
        if progress_budget_enabled and isinstance(loop_context, Mapping):
            loop_budgets = loop_context.get("budgets")
            if isinstance(loop_budgets, Mapping):
                configured_no_progress_attempts = self._coerce_positive_int(
                    loop_budgets.get("maxConsecutiveNoProgressAttempts")
                )
                if configured_no_progress_attempts is not None:
                    max_no_progress_attempts = configured_no_progress_attempts
                    explicit_no_progress_budget = True
        if (
            progress_budget_enabled
            and not explicit_no_progress_budget
            and self._patched_or_false_outside_workflow(
                RUN_BOUNDED_STORY_LOOP_REMEDIATION_BUDGET_PATCH
            )
        ):
            # A compiled remediation chain already has a finite, operator-visible
            # attempt budget. When no independent no-progress policy was authored,
            # let that declared budget govern repeated verifier results as well.
            # Defaulting this guard to one makes the first unchanged/sparse
            # post-remediation report shadow an explicit "attempt 1 of 6" plan.
            declared_remediation_maxima: set[int] = set()
            for candidate in ordered_nodes:
                if self._moonspec_step_role(candidate) not in {
                    "moonspec-remediation",
                    "moonspec-verification-gate",
                }:
                    continue
                _, maximum = self._moonspec_remediation_attempt_metadata(candidate)
                if maximum is not None:
                    declared_remediation_maxima.add(maximum)
            if len(declared_remediation_maxima) == 1:
                max_no_progress_attempts = next(iter(declared_remediation_maxima))
        budget = LoopBudget.model_validate(
            {
                "maxAttempts": 1,
                "maxConsecutiveNoProgressAttempts": max_no_progress_attempts,
                "maxRepeatedFailedCommands": 1,
                "maxUnsafeOrPolicyDeniedAttempts": 1,
                "consumed": {
                    "attempts": 0 if has_remaining_remediation_step else 1,
                    "consecutiveNoProgressAttempts": (
                        consecutive_no_progress_attempts
                    ),
                    "repeated_failed_commands": 0,
                    "unsafe_or_policy_denied_attempts": 0,
                },
            }
        )
        decision = evaluate_attempt_continuation(
            attempt=attempt,
            gate=gate,
            budget=budget,
            checkpoint_available=True,
            policy_allowed=True,
        )
        payload = decision.model_dump(by_alias=True, mode="json")
        payload["hasRemainingRemediationStep"] = has_remaining_remediation_step
        payload["remediationRoutingReason"] = successor_reason
        payload["nextLogicalStepId"] = (
            successor.logical_step_id if successor is not None else None
        )
        payload["gate"] = {
            "verdict": gate.verdict,
            "gateResultRef": gate.gate_result_ref,
            "remainingWorkRef": gate.remaining_work_ref,
            "diagnosticsRef": gate.diagnostics_ref,
            "progressSignature": gate.progress_signature,
        }
        payload["budget"] = budget.model_dump(by_alias=True, mode="json")
        payload["currentLogicalStepId"] = logical_step_id
        current_node = next(
            (
                candidate
                for candidate in ordered_nodes
                if str(candidate.get("id") or "") == logical_step_id
            ),
            (
                ordered_nodes[current_index]
                if 0 <= current_index < len(ordered_nodes)
                else {}
            ),
        )
        remediation_attempt, remediation_max = (
            self._moonspec_remediation_attempt_metadata(current_node)
        )
        review_retries = max(0, (self._step_execution_for(logical_step_id) or 1) - 1)
        persisted_review_budget: Mapping[str, Any] | None = None
        current_row = self._step_ledger_row_for(logical_step_id) or {}
        checks = current_row.get("checks")
        if isinstance(checks, Sequence):
            for check in reversed(checks):
                if not isinstance(check, Mapping):
                    continue
                candidate_budget = check.get("reviewGateBudget")
                if isinstance(candidate_budget, Mapping):
                    persisted_review_budget = candidate_budget
                    break
        payload["reviewGateBudget"] = (
            dict(persisted_review_budget)
            if persisted_review_budget is not None
            else {
                "maxExecutions": review_retries + 1,
                "executionsConsumed": review_retries + 1,
                "retriesConsumed": review_retries,
                "remainingExecutions": 0,
            }
        )
        payload["remediationBudget"] = self._moonspec_remediation_budget_metadata(
            ordered_nodes=ordered_nodes,
            current_attempt=remediation_attempt,
            max_attempts=remediation_max,
        )
        self._publish_context.setdefault("boundedStoryLoop", {})[
            "continuationDecision"
        ] = payload
        return payload

    def _blocking_moonspec_gate_reason(self) -> str | None:
        verdict = self._normalize_moonspec_verify_verdict(self._moonspec_gate_verdict)
        if verdict is None:
            return None
        if verdict in _MOONSPEC_GATE_PASSING_VERDICTS:
            return None
        gate_context = self._publish_context.get("moonSpecGate")
        diagnostics_ref = None
        downgrade_reason = None
        if isinstance(gate_context, Mapping):
            diagnostics_ref = self._coerce_text(
                gate_context.get("diagnosticsRef"), max_chars=400
            )
            downgrade_reason = self._coerce_text(
                gate_context.get("downgradeReason"), max_chars=700
            )
        parts = [
            "MoonSpec verification did not approve publication",
            f"verdict {verdict}",
        ]
        if downgrade_reason:
            parts.append(downgrade_reason)
        if self._moonspec_gate_reason:
            parts.append(self._moonspec_gate_reason)
        if diagnostics_ref:
            parts.append(f"verification report {diagnostics_ref}")
        reason = ". ".join(part.rstrip(".") for part in parts if part)
        return f"{reason}." if reason else None

    def _apply_blocking_moonspec_gate_to_publish(self) -> bool:
        if self._moonspec_draft_publication_reason is not None:
            # Draft publication (operator policy draft_pr) supersedes the
            # fail-closed block: the run publishes a draft PR annotated as
            # verification-incomplete instead of blocking publication.
            return False
        reason = self._blocking_moonspec_gate_reason()
        if not reason:
            return False
        self._plan_blocked_message = reason
        self._publish_status = "not_required"
        self._publish_reason = reason
        self._publish_context["publicationBlockedBy"] = "moonspec_verify"
        return True

    @staticmethod
    def _normalize_moonspec_environment_blocked_publish_action(value: Any) -> str:
        action = str(value or "fail").strip().lower()
        return action if action in {"fail", "draft_pr"} else "fail"

    def _moonspec_environment_blocked_publish_action(self) -> str:
        return self._normalize_moonspec_environment_blocked_publish_action(
            self._moonspec_environment_blocked_publish_action_snapshot
        )

    def _moonspec_gate_qualifies_for_draft_publish(self) -> bool:
        """Environment-class gate outcomes eligible for draft publication.

        Qualifying outcomes are verifier-declared environment failures
        (``BLOCKED``) and ``NO_DETERMINATION`` produced by downgrading a
        degraded/malformed gate payload. Genuine implementation-gap verdicts
        (``ADDITIONAL_WORK_NEEDED``, ``FAILED_UNRECOVERABLE``) never qualify.
        """
        if not self._moonspec_gate_verdict:
            return False
        verdict = self._normalize_moonspec_verify_verdict(self._moonspec_gate_verdict)
        if verdict == "BLOCKED":
            return True
        if verdict != "NO_DETERMINATION":
            return False
        gate_context = self._publish_context.get("moonSpecGate")
        if not isinstance(gate_context, Mapping):
            return False
        declared_verdict = self._normalize_moonspec_verify_verdict(
            gate_context.get("declaredVerdict")
        )
        if declared_verdict in {
            "ADDITIONAL_WORK_NEEDED",
            "FAILED_UNRECOVERABLE",
        }:
            return False
        return bool(gate_context.get("degraded") or gate_context.get("invalid"))

    def _moonspec_draft_publication_policy(
        self,
        *,
        environment_blocked_enabled: bool,
        additional_work_enabled: bool,
    ) -> str | None:
        verdict = self._normalize_moonspec_verify_verdict(
            self._moonspec_gate_verdict
        )
        if additional_work_enabled and verdict == "ADDITIONAL_WORK_NEEDED":
            return "draft_pr_on_additional_work_needed"
        if (
            environment_blocked_enabled
            and self._moonspec_environment_blocked_publish_action() == "draft_pr"
            and self._moonspec_gate_qualifies_for_draft_publish()
        ):
            return "draft_pr_on_environment_blocked"
        return None

    def _activate_moonspec_draft_publication(
        self,
        gate_reason: str,
        *,
        policy: str = "draft_pr_on_environment_blocked",
    ) -> str:
        summary = (
            "MoonSpec verification incomplete; publishing draft pull request "
            f"for operator review. {gate_reason}"
        )
        self._moonspec_draft_publication_reason = gate_reason
        self._attention_required = True
        gate_context = self._publish_context.get("moonSpecGate")
        if isinstance(gate_context, dict):
            gate_context["publicationPolicy"] = policy
        self._publish_context["moonSpecDraftPublication"] = {
            "policy": policy,
            "reason": gate_reason,
        }
        return summary

    def _moonspec_draft_publication_body_section(self) -> str:
        gate_context = self._publish_context.get("moonSpecGate")
        verdict = None
        diagnostics_ref = None
        gate_result_ref = None
        remaining_work_ref = None
        if isinstance(gate_context, Mapping):
            verdict = self._coerce_text(gate_context.get("verdict"), max_chars=80)
            diagnostics_ref = self._coerce_text(
                gate_context.get("diagnosticsRef"), max_chars=400
            )
            gate_result_ref = self._coerce_text(
                gate_context.get("gateResultRef"), max_chars=400
            )
            remaining_work_ref = self._coerce_text(
                gate_context.get("remainingWorkRef"), max_chars=400
            )
        draft_context = self._publish_context.get("moonSpecDraftPublication")
        policy = (
            draft_context.get("policy")
            if isinstance(draft_context, Mapping)
            else None
        )
        if policy == "draft_pr_on_additional_work_needed":
            explanation = (
                "the bounded remediation budget was exhausted with remaining "
                "implementation work"
            )
        else:
            explanation = (
                "the operator policy "
                "`workflow.moonspec_environment_blocked_publish_action` is "
                "`draft_pr`"
            )
        lines = [
            "## MoonSpec verification incomplete",
            "",
            (
                "This pull request was opened as a **draft** because MoonSpec "
                f"verification did not approve publication (verdict "
                f"{verdict or 'unknown'}) and {explanation}."
            ),
            "",
        ]
        reason = self._moonspec_draft_publication_reason
        if reason:
            lines.append(f"- Gate outcome: {reason}")
        if diagnostics_ref:
            lines.append(f"- Verification report: {diagnostics_ref}")
        if remaining_work_ref:
            lines.append(f"- Remaining work: {remaining_work_ref}")
        if gate_result_ref and gate_result_ref != diagnostics_ref:
            lines.append(f"- Verification gate result: {gate_result_ref}")
        lines.append("")
        lines.append(
            "Review the verification evidence before marking this pull "
            "request ready for review."
        )
        return "\n".join(lines)

    def _review_gate_budget_metadata(
        self,
        *,
        max_review_attempts: int,
        review_retry_count: int,
        max_consecutive_no_progress_attempts: int,
        consecutive_no_progress_attempts: int,
        verdict: str | None = None,
        recommended_next_action: str | None = None,
    ) -> dict[str, Any]:
        attempts_allowed = max_review_attempts + 1
        attempts_consumed = review_retry_count + 1
        remaining_executions = max(0, attempts_allowed - attempts_consumed)
        no_progress_exhausted = (
            consecutive_no_progress_attempts
            >= max_consecutive_no_progress_attempts
        )
        metadata: dict[str, Any] = {
            "gate": "approval_policy",
            "maxAttempts": attempts_allowed,
            "attemptsConsumed": attempts_consumed,
            "maxExecutions": attempts_allowed,
            "executionsConsumed": attempts_consumed,
            "retriesConsumed": review_retry_count,
            "remainingExecutions": remaining_executions,
            "additionalStopDimension": {
                "type": "consecutive_no_progress_attempts",
                "limit": max_consecutive_no_progress_attempts,
                "consumed": consecutive_no_progress_attempts,
                "remaining": max(
                    0,
                    max_consecutive_no_progress_attempts
                    - consecutive_no_progress_attempts,
                ),
                "exhausted": no_progress_exhausted,
            },
            "stopRules": [
                "structured_gate_verdict_required",
                "accepted_output_evidence_required",
                "consecutive_no_progress_attempts_exhaustion_stops_before_publication",
                "budget_exhaustion_stops_before_publication",
            ],
            "exhausted": remaining_executions == 0 or no_progress_exhausted,
        }
        if verdict:
            metadata["gateVerdict"] = str(verdict).strip().upper()
        if recommended_next_action:
            metadata["recommendedNextAction"] = recommended_next_action
        return metadata

    def _review_gate_retry_allowed(
        self,
        *,
        verdict: Any,
        review_retry_count: int,
        max_review_attempts: int,
        consecutive_no_progress_attempts: int,
        max_consecutive_no_progress_attempts: int,
    ) -> bool:
        normalized = str(getattr(verdict, "verdict", "") or "").strip().upper()
        if review_retry_count >= max_review_attempts:
            return False
        if (
            consecutive_no_progress_attempts
            >= max_consecutive_no_progress_attempts
        ):
            return False
        recommended_next_action = (
            str(getattr(verdict, "recommended_next_action", "") or "")
            .strip()
            .lower()
        )
        if normalized == "ADDITIONAL_WORK_NEEDED":
            return recommended_next_action == "reattempt_current_step"
        return normalized == "NO_DETERMINATION" and bool(
            getattr(verdict, "recoverable_in_current_runtime", False)
        )

    @staticmethod
    def _gate_transition_allows_review_retry(
        *,
        plan_routed_moonspec_remediation_enabled: bool,
        transition: GateTransitionDecision,
    ) -> bool:
        """Keep pre-cutover review retries independent of new plan routing."""
        return (
            not plan_routed_moonspec_remediation_enabled
            or transition.disposition in {"generic", "retry"}
        )

    @staticmethod
    def _review_gate_verdict_made_progress(verdict: Any) -> bool:
        normalized = str(getattr(verdict, "verdict", "") or "").strip().upper()
        recommended_next_action = (
            str(getattr(verdict, "recommended_next_action", "") or "")
            .strip()
            .lower()
        )
        remaining_work_ref = str(
            getattr(verdict, "remaining_work_ref", "") or ""
        ).strip()
        return (
            normalized == "ADDITIONAL_WORK_NEEDED"
            and recommended_next_action == "reattempt_current_step"
            and bool(remaining_work_ref)
        )

    def _terminal_disposition_for_gate_stop(self, verdict: Any) -> str:
        normalized = str(getattr(verdict, "verdict", "") or "").strip().upper()
        if normalized == "BLOCKED":
            return "blocked"
        if normalized == "FAILED_UNRECOVERABLE":
            return "failed_unrecoverable"
        if normalized == "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION":
            return "environment_contaminated_by_skill_projection"
        if normalized == "ADDITIONAL_WORK_NEEDED":
            return "failed_with_remaining_work"
        return "needs_human"

    def _step_has_accepted_output_evidence(
        self,
        logical_step_id: str,
        execution_result: Any,
    ) -> bool:
        outputs = self._effective_result_outputs(execution_result)
        row_outputs = self._step_execution_compact_output_refs(logical_step_id)
        merged_outputs: dict[str, Any] = {}
        if isinstance(outputs, Mapping):
            merged_outputs.update(dict(outputs))
            self._merge_direct_output_evidence(merged_outputs, outputs)
        merged_outputs.update(row_outputs)
        return logical_step_success_allowed(outputs=merged_outputs)

    @staticmethod
    def _merge_direct_output_evidence(
        merged_outputs: dict[str, Any],
        outputs: Mapping[str, Any],
    ) -> None:
        for source_key, target_key in (
            ("primary_report_ref", "primaryRef"),
            ("primaryReportRef", "primaryRef"),
            ("summary_ref", "summaryRef"),
            ("summaryRef", "summaryRef"),
        ):
            value = outputs.get(source_key)
            if isinstance(value, str) and value.strip():
                merged_outputs.setdefault(target_key, value.strip())

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

    @staticmethod
    def _truncate_json_context_value(
        value: Any,
        *,
        max_chars: int,
        truncated_paths: list[str] | None = None,
        path: str = "",
    ) -> Any:
        suffix = "...[truncated]"
        if isinstance(value, str):
            if len(value) <= max_chars:
                return value
            if truncated_paths is not None:
                truncated_paths.append(path or "<root>")
            return value[: max(0, max_chars - len(suffix))] + suffix
        if isinstance(value, Mapping):
            return {
                str(key): MoonMindRunWorkflow._truncate_json_context_value(
                    nested,
                    max_chars=max_chars,
                    truncated_paths=truncated_paths,
                    path=f"{path}.{key}" if path else str(key),
                )
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [
                MoonMindRunWorkflow._truncate_json_context_value(
                    nested,
                    max_chars=max_chars,
                    truncated_paths=truncated_paths,
                    path=f"{path}[{index}]" if path else f"[{index}]",
                )
                for index, nested in enumerate(value)
            ]
        return value

    @staticmethod
    def _trusted_previous_outputs_context(
        previous_outputs: object,
    ) -> dict[str, Any] | None:
        if not isinstance(previous_outputs, Mapping):
            return None
        trusted_source = str(previous_outputs.get("trustedSource") or "").strip()
        context_keys_by_source: dict[str, tuple[str, ...]] = {
            "moonmind.jira.get_issue": (
                "jiraIssueKey",
                "jiraPresetBrief",
                "presetBrief",
                "jiraStepInstructions",
                "artifactPath",
                "resolvedSourceDesignPath",
                "sourceResolution",
                "jiraIssue",
                "summary",
            ),
            "moonmind.github.get_issue": (
                "repository",
                "issueNumber",
                "issueRef",
                "issueUrl",
                "githubIssue",
                "issue",
                "presetBrief",
                "artifactPath",
                "title",
                "body",
                "labels",
                "summary",
            ),
        }
        context_keys = context_keys_by_source.get(trusted_source)
        if context_keys is None:
            return None

        context: dict[str, Any] = {"trustedSource": trusted_source}
        for key in context_keys:
            value = previous_outputs.get(key)
            if value in (None, "", {}, []):
                continue
            context[key] = value
        if len(context) <= 1:
            return None
        return context

    @staticmethod
    def _dump_trusted_context_json(
        context: Mapping[str, Any],
        truncated_paths: list[str],
    ) -> str:
        payload_context = dict(context)
        if truncated_paths:
            payload_context["contextTruncation"] = {
                "truncated": True,
                "truncatedKeys": sorted(set(truncated_paths)),
            }
        return json.dumps(
            payload_context,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ": "),
        )

    @staticmethod
    def _trusted_context_payload(context: Mapping[str, Any]) -> tuple[str, bool]:
        max_payload_chars = 24000
        truncated_paths: list[str] = []
        compact_context = {
            key: MoonMindRunWorkflow._truncate_json_context_value(
                value,
                max_chars=6000,
                truncated_paths=truncated_paths,
                path=str(key),
            )
            for key, value in context.items()
        }
        payload = MoonMindRunWorkflow._dump_trusted_context_json(
            compact_context,
            truncated_paths,
        )
        if len(payload) <= max_payload_chars:
            return payload, bool(truncated_paths)

        essential_context = {
            key: compact_context[key]
            for key in (
                "trustedSource",
                "jiraIssueKey",
                "repository",
                "issueNumber",
                "issueRef",
                "issueUrl",
                "summary",
                "artifactPath",
                "resolvedSourceDesignPath",
                "sourceResolution",
                "jiraPresetBrief",
                "presetBrief",
                "jiraStepInstructions",
                "githubIssue",
                "issue",
                "title",
                "body",
            )
            if key in compact_context
        }
        essential_keys = set(essential_context)
        essential_truncated_paths = [
            path
            for path in truncated_paths
            if path.split(".", 1)[0].split("[", 1)[0] in essential_keys
        ]
        essential_context = {
            key: MoonMindRunWorkflow._truncate_json_context_value(
                value,
                max_chars=3000,
                truncated_paths=essential_truncated_paths,
                path=str(key),
            )
            for key, value in essential_context.items()
        }
        return (
            MoonMindRunWorkflow._dump_trusted_context_json(
                essential_context,
                essential_truncated_paths,
            ),
            bool(essential_truncated_paths),
        )

    @staticmethod
    def _trusted_previous_outputs_instruction(
        previous_outputs: object,
    ) -> str | None:
        context = MoonMindRunWorkflow._trusted_previous_outputs_context(
            previous_outputs
        )
        if not context:
            return None

        payload, truncated = MoonMindRunWorkflow._trusted_context_payload(context)
        truncation_note = ""
        if truncated:
            truncation_note = (
                " Some long values were truncated for workflow-history "
                "compactness; they end with the marker ...[truncated] and are "
                "listed in contextTruncation.truncatedKeys. Truncated values are "
                "not authoritative for completeness: recover the full content "
                "only through MoonMind trusted tool surfaces (for example "
                "jira.get_issue through the MoonMind MCP tool path, or the "
                "authenticated GitHub issue tooling) before relying on those "
                "fields, and never treat hidden truncated content as a "
                "requirement."
            )
        return (
            "MoonMind trusted previous step context:\n"
            "The following JSON was produced by MoonMind's trusted issue tool path. "
            "Treat it as authoritative for this step. Do not use provider-native "
            "Jira/Atlassian or GitHub connectors, web scraping, or guessed issue "
            "content to replace it."
            f"{truncation_note}\n"
            f"```json\n{payload}\n```"
        )

    def _append_trusted_previous_outputs_to_agent_inputs(
        self,
        node_inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        previous_context = self._trusted_previous_outputs_instruction(
            node_inputs.get("previousOutputs")
        )
        if not previous_context:
            return dict(node_inputs)
        merged_inputs = dict(node_inputs)
        for key in (
            "instructions",
            "instructionRef",
        ):
            instruction = merged_inputs.get(key)
            if isinstance(instruction, str) and instruction.strip():
                if "MoonMind trusted previous step context:" in instruction:
                    return merged_inputs
                merged_inputs[key] = instruction.rstrip() + "\n\n" + previous_context
                return merged_inputs
        merged_inputs["instructions"] = previous_context
        return merged_inputs

    def _record_trusted_issue_context(self, outputs: Mapping[str, Any]) -> None:
        context = self._trusted_previous_outputs_context(outputs)
        if context:
            self._trusted_issue_context = context

    @staticmethod
    def _patched_or_false_outside_workflow(patch_id: str) -> bool:
        try:
            return workflow.patched(patch_id)
        except Exception as exc:
            if exc.__class__.__name__ == "_NotInWorkflowEventLoopError":
                return False
            raise

    def _record_assessment_context(self, outputs: Mapping[str, Any]) -> None:
        record_aliases = self._patched_or_false_outside_workflow(
            RUN_JIRA_BLOCKER_RECHECK_ASSESSMENT_CONTEXT_ALIAS_PATCH
        )
        for aliases in (
            ("assessmentArtifactRef", "assessment_artifact_ref"),
            ("assessmentVerdict", "assessment_verdict"),
        ):
            for key in aliases:
                value = outputs.get(key)
                if value in (None, "", {}, []):
                    continue
                if record_aliases:
                    for alias in aliases:
                        self._assessment_context[alias] = value
                else:
                    self._assessment_context[key] = value
                break

    def _merge_assessment_context_into_result(self, execution_result: Any) -> Any:
        if not self._assessment_context:
            return execution_result
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return execution_result

        merged_outputs = dict(outputs)
        changed = False
        for aliases in (
            ("assessmentArtifactRef", "assessment_artifact_ref"),
            ("assessmentVerdict", "assessment_verdict"),
        ):
            if any(
                merged_outputs.get(alias) not in (None, "", {}, [])
                for alias in aliases
            ):
                continue
            for alias in aliases:
                value = self._assessment_context.get(alias)
                if value in (None, "", {}, []):
                    continue
                merged_outputs[alias] = value
                changed = True
        if not changed:
            return execution_result

        if isinstance(execution_result, Mapping):
            merged_result = dict(execution_result)
            merged_result["outputs"] = merged_outputs
            return merged_result
        model_copy = getattr(execution_result, "model_copy", None)
        if callable(model_copy):
            return model_copy(update={"outputs": merged_outputs})
        if dataclasses.is_dataclass(execution_result):
            try:
                return dataclasses.replace(execution_result, outputs=merged_outputs)
            except TypeError:
                return execution_result
        return execution_result

    def _merge_trusted_issue_context(
        self,
        previous_outputs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        merged = dict(previous_outputs)
        for key in (
            "assessmentArtifactRef",
            "assessment_artifact_ref",
            "assessmentVerdict",
            "assessment_verdict",
        ):
            value = self._assessment_context.get(key)
            if value not in (None, "", {}, []):
                merged.setdefault(key, value)
        if self._patched_or_false_outside_workflow(
            RUN_MOONSPEC_GATE_PREVIOUS_OUTPUTS_HANDOFF_PATCH
        ):
            gate_context = self._publish_context.get("moonSpecGate")
            if isinstance(gate_context, Mapping) and gate_context:
                # The workflow-owned gate is the latest controlling verifier
                # result. Carry it across intervening agent steps so trusted
                # finalizers do not depend on another workspace's relative path
                # or on unstructured agent prose.
                merged["moonSpecVerify"] = dict(gate_context)
                gate_result_ref = self._coerce_text(
                    gate_context.get("gateResultRef"),
                    max_chars=400,
                )
                if gate_result_ref:
                    merged["moonSpecVerifyArtifactRef"] = gate_result_ref
        if not self._trusted_issue_context:
            return merged if merged != previous_outputs else previous_outputs
        if self._trusted_previous_outputs_context(previous_outputs):
            return merged if merged != previous_outputs else previous_outputs
        for key, value in self._trusted_issue_context.items():
            merged.setdefault(key, value)
        return merged

    def _publish_repair_feedback_instruction(
        self,
        *,
        failure_message: str,
    ) -> str:
        branch = self._coerce_text(self._publish_context.get("branch"), max_chars=120)
        base_ref = self._coerce_text(
            self._publish_context.get("baseRef"),
            max_chars=120,
        )
        pull_request_url = self._coerce_text(
            self._publish_context.get("pullRequestUrl"),
            max_chars=200,
        )
        lines = [
            "Publish postcondition repair required.",
            "",
            f"Observed failure: {failure_message.strip()}",
        ]
        if branch:
            lines.append(f"Expected publish branch: `{branch}`.")
        if base_ref:
            lines.append(f"Expected comparison base: `{base_ref}`.")
        if pull_request_url:
            lines.append(f"Existing pull request URL: {pull_request_url}.")
        lines.extend(
            [
                "",
                (
                    "Repair the repository state so MoonMind can complete "
                    "`publishMode=pr`:"
                ),
                (
                    "- Ensure the completed workflow changes are committed on the "
                    "expected publish branch."
                ),
                (
                    "- If the work was committed on another local branch, move "
                    "or cherry-pick the workflow commits onto the expected publish "
                    "branch."
                ),
                "- Do not transition Jira during this repair turn.",
                (
                    "- Do not push or create a pull request unless an explicit "
                    "workflow step still requires it; managed publishing will push "
                    "and create the PR after this turn."
                ),
                "- Finish with a concise summary of the branch and commit state.",
            ]
        )
        return "\n".join(lines)

    def _publish_repair_is_available(self, *, parameters: Mapping[str, Any]) -> bool:
        if (
            workflow.patched(RUN_PREPUBLICATION_FAILURE_BLOCKS_REPAIR_PATCH)
            and self._publish_status == "failed"
        ):
            return False
        if self._publish_repair_attempts >= 1:
            return False
        if self._publish_mode(parameters) != "pr":
            return False
        if self._plan_blocked_message:
            return False
        request = self._last_publish_repair_request
        return request is not None and request.agent_kind == "managed"

    @staticmethod
    def _is_jira_agent_skill_name(value: Any) -> bool:
        candidate = str(value or "").strip().lower()
        return bool(candidate and candidate in _PR_OPTIONAL_AGENT_SKILLS)

    async def _execution_publish_repair(
        self,
        *,
        parameters: Mapping[str, Any],
        failure_message: str,
    ) -> dict[str, Any] | None:
        if not workflow.patched(RUN_PUBLISH_REPAIR_FEEDBACK_PATCH):
            return None
        if not self._publish_repair_is_available(parameters=parameters):
            return None

        base_request = self._last_publish_repair_request
        if base_request is None:
            return None

        self._publish_repair_attempts += 1
        repair_node_id = "publish-repair"
        wf_info = workflow.info()
        repair_parameters = dict(base_request.parameters or {})
        repair_parameters["publishMode"] = "pr"
        metadata_payload = (
            dict(repair_parameters.get("metadata"))
            if isinstance(repair_parameters.get("metadata"), Mapping)
            else {}
        )
        moonmind_payload = (
            dict(metadata_payload.get("moonmind"))
            if isinstance(metadata_payload.get("moonmind"), Mapping)
            else {}
        )
        moonmind_payload["publishRepair"] = {
            "attempt": self._publish_repair_attempts,
            "sourceNodeId": self._last_publish_repair_node_id,
        }
        metadata_payload["moonmind"] = moonmind_payload
        repair_parameters["metadata"] = metadata_payload

        repair_request = base_request.model_copy(
            update={
                "instruction_ref": self._publish_repair_feedback_instruction(
                    failure_message=failure_message
                ),
                "idempotency_key": (
                    f"{wf_info.workflow_id}:{repair_node_id}:"
                    f"{self._publish_repair_attempts}:{wf_info.run_id}"
                ),
                "parameters": repair_parameters,
            }
        )
        repair_request = await self._maybe_bind_workflow_scoped_session(repair_request)
        child_workflow_id = (
            f"{wf_info.workflow_id}:agent:{repair_node_id}:"
            f"{self._publish_repair_attempts}"
        )
        self._active_agent_child_workflow_id = child_workflow_id
        self._active_agent_id = repair_request.agent_id
        self._summary = "Repairing PR publish postcondition."
        self._update_memo()
        try:
            child_result = await workflow.execute_child_workflow(
                "MoonMind.AgentRun",
                repair_request,
                id=child_workflow_id,
                task_queue=self._workflow_child_task_queue(),
            )
        finally:
            self._active_agent_child_workflow_id = None
            self._active_agent_id = None

        execution_result = self._map_agent_run_result(child_result)
        if self._activity_result_status(execution_result) != "COMPLETED":
            return None
        return execution_result

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

        if workflow.patched(DEPENDENCY_WAIT_THROUGH_RERUN_PATCH):
            self._record_dependency_outcome_wait_through_rerun(
                prerequisite_workflow_id=prerequisite_workflow_id,
                terminal_state=terminal_state,
                close_status=close_status,
                resolved_at=resolved_at,
                failure_category=failure_category,
                message=message,
            )
            return

        # Legacy fail-fast path preserved for in-flight workflows whose history
        # predates DEPENDENCY_WAIT_THROUGH_RERUN_PATCH.
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

        if terminal_state in {STATE_COMPLETED, STATE_NO_COMMIT}:
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

    def _record_dependency_outcome_wait_through_rerun(
        self,
        *,
        prerequisite_workflow_id: str,
        terminal_state: str,
        close_status: str | None,
        resolved_at: str,
        failure_category: str | None,
        message: str | None,
    ) -> None:
        existing = self._dependency_outcomes_by_id.get(prerequisite_workflow_id)
        existing_resolution = (
            str(existing.get("resolution") or "") if existing else ""
        )

        if self._dependency_resolution in (
            DEPENDENCY_RESOLUTION_BYPASSED,
            DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE,
        ):
            return

        if terminal_state in {STATE_COMPLETED, STATE_NO_COMMIT}:
            # Idempotency: stale completed signals after already satisfied are no-ops.
            if existing_resolution in (
                DEPENDENCY_RESOLUTION_SATISFIED,
                DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN,
                DEPENDENCY_RESOLUTION_BYPASSED,
                DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE,
            ):
                return

            had_failures = (
                self._dependency_failure_counts.get(prerequisite_workflow_id, 0) > 0
            )
            per_dep_resolution = (
                DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN
                if had_failures
                else DEPENDENCY_RESOLUTION_SATISFIED
            )

            outcome: dict[str, Any] = {
                "workflowId": prerequisite_workflow_id,
                "terminalState": terminal_state,
                "closeStatus": close_status,
                "resolvedAt": resolved_at,
                "failureCategory": None,
                "message": message,
                "resolution": per_dep_resolution,
                "failureCount": self._dependency_failure_counts.get(
                    prerequisite_workflow_id, 0
                ),
                "lastFailedAt": self._dependency_last_failed_at.get(
                    prerequisite_workflow_id
                ),
            }
            self._dependency_outcomes_by_id[prerequisite_workflow_id] = outcome
            self._unresolved_dependency_ids.discard(prerequisite_workflow_id)

            if not self._unresolved_dependency_ids:
                self._dependency_resolution = self._compute_top_level_resolution()

            if had_failures:
                self._get_logger().info(
                    "Dependency gate satisfied after rerun",
                    extra={
                        "event": "dependency_gate_satisfied_after_rerun",
                        "prerequisite_workflow_id": prerequisite_workflow_id,
                        "failure_count": self._dependency_failure_counts.get(
                            prerequisite_workflow_id, 0
                        ),
                        "wait_duration_ms": self._dependency_wait_duration_ms,
                    },
                )
            return

        # Non-success terminal: stay blocked, accumulate diagnostics. Stale
        # failure observations after the prerequisite is already satisfied
        # or the gate was bypassed are ignored.
        if existing_resolution in (
            DEPENDENCY_RESOLUTION_SATISFIED,
            DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN,
            DEPENDENCY_RESOLUTION_BYPASSED,
            DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE,
        ):
            return

        last_failed_at = self._dependency_last_failed_at.get(
            prerequisite_workflow_id
        )
        existing_waiting = (
            existing_resolution == DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN
        )
        existing_message = str(existing.get("message") or "") if existing else ""
        incoming_message = str(message or "")
        is_duplicate_observation = (
            last_failed_at == resolved_at
            or (
                existing_waiting
                and existing.get("terminalState") == terminal_state
                and existing.get("closeStatus") == close_status
                and existing.get("failureCategory") == failure_category
                and existing_message == incoming_message
            )
        )
        if not is_duplicate_observation:
            self._dependency_failure_counts[prerequisite_workflow_id] = (
                self._dependency_failure_counts.get(prerequisite_workflow_id, 0)
                + 1
            )
            self._dependency_last_failed_at[prerequisite_workflow_id] = resolved_at

        failure_count = self._dependency_failure_counts.get(
            prerequisite_workflow_id, 0
        )
        last_failed = self._dependency_last_failed_at.get(prerequisite_workflow_id)

        diagnostic_message = message or (
            f"Prerequisite execution '{prerequisite_workflow_id}' reached "
            f"terminal state '{terminal_state}'; waiting for successful rerun."
        )

        outcome = {
            "workflowId": prerequisite_workflow_id,
            "terminalState": terminal_state,
            "closeStatus": close_status,
            "resolvedAt": None,
            "failureCategory": failure_category,
            "message": diagnostic_message,
            "resolution": DEPENDENCY_RESOLUTION_WAITING_FOR_RERUN,
            "failureCount": failure_count,
            "lastFailedAt": last_failed,
        }
        self._dependency_outcomes_by_id[prerequisite_workflow_id] = outcome

        if not is_duplicate_observation:
            self._get_logger().info(
                "Dependency gate waiting for successful rerun",
                extra={
                    "event": "dependency_gate_waiting_for_rerun",
                    "prerequisite_workflow_id": prerequisite_workflow_id,
                    "terminal_state": terminal_state,
                    "close_status": close_status,
                    "failure_count": failure_count,
                },
            )

    def _compute_top_level_resolution(self) -> str:
        if not self._declared_dependencies:
            return DEPENDENCY_RESOLUTION_NOT_APPLICABLE

        if self._dependency_resolution in (
            DEPENDENCY_RESOLUTION_BYPASSED,
            DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE,
        ):
            return self._dependency_resolution

        any_rerun = False
        for workflow_id in self._declared_dependencies:
            outcome = self._dependency_outcomes_by_id.get(workflow_id, {})
            resolution = outcome.get("resolution")
            if resolution == DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE:
                return DEPENDENCY_RESOLUTION_MANUAL_OVERRIDE
            if resolution == DEPENDENCY_RESOLUTION_BYPASSED:
                return DEPENDENCY_RESOLUTION_BYPASSED
            if resolution == DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN:
                any_rerun = True

        return (
            DEPENDENCY_RESOLUTION_SATISFIED_AFTER_RERUN
            if any_rerun
            else DEPENDENCY_RESOLUTION_SATISFIED
        )

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

        # Normalize "succeeded" to "completed" so the outcome recorder can
        # satisfy the gate with either canonical successful terminal state.
        terminal_state = signal.terminal_state
        if terminal_state == "succeeded":
            terminal_state = "completed"

        is_terminal_failure = terminal_state not in {STATE_COMPLETED, STATE_NO_COMMIT}
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
                "resolution": DEPENDENCY_RESOLUTION_BYPASSED,
                "failureCount": self._dependency_failure_counts.get(
                    prerequisite_workflow_id, 0
                ),
                "lastFailedAt": self._dependency_last_failed_at.get(
                    prerequisite_workflow_id
                ),
            }

        self._unresolved_dependency_ids.clear()
        self._dependency_failure = None
        self._failed_dependency_id = None
        self._dependency_resolution = DEPENDENCY_RESOLUTION_BYPASSED
        if self._jira_blocker_wait_active:
            self._jira_blocker_wait_skipped = True
            self._paused = False
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

            if (
                workflow_type
                and workflow_type not in self._supported_dependency_workflow_types()
            ):
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

            if state in {STATE_COMPLETED, STATE_NO_COMMIT}:
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
        self._dependency_failure_counts = {}
        self._dependency_last_failed_at = {}
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
            while not self._cancel_requested and (
                (self._dependency_failure is None and self._unresolved_dependency_ids)
                or self._paused
            ):
                try:
                    await workflow.wait_condition(
                        lambda: self._cancel_requested
                        or (self._dependency_failure is not None and not self._paused)
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
        self._canonical_no_commit_outcome_enabled = workflow.patched(
            RUN_CANONICAL_NO_COMMIT_OUTCOME_PATCH
        )
        self._authoritative_publish_outcome_enabled = workflow.patched(
            RUN_AUTHORITATIVE_PUBLISH_OUTCOME_PATCH
        )
        self._get_logger().info(
            "Starting MoonMind.UserWorkflow workflow",
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
                    if not self._paused:
                        break
                    await self._wait_if_paused_at_safe_boundary()
                    if self._cancel_requested:
                        break
                    continue

                self._reschedule_requested = False
                try:
                    await workflow.wait_condition(
                        lambda: self._reschedule_requested
                        or self._cancel_requested
                        or self._paused,
                        timeout=delay,
                    )
                except asyncio.TimeoutError:
                    # Timeout means no reschedule/cancel happened before the scheduled time.
                    self._get_logger().debug(
                        "Scheduled delay elapsed without reschedule/cancel."
                    )

                if self._cancel_requested:
                    break
                if self._paused:
                    await self._wait_if_paused_at_safe_boundary()
                    if self._cancel_requested:
                        break
                    continue

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
            diagnostic = self._record_failure_diagnostic(
                exc,
                stage=self._state,
                source="workflow",
            )
            await self._run_finalizing_stage(
                parameters=parameters, status="failed", error=diagnostic["message"]
            )
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=diagnostic["message"])
            await self._record_terminal_state(
                state=STATE_FAILED,
                close_status=CLOSE_STATUS_FAILED,
                summary=diagnostic["message"],
                error_category=diagnostic["category"],
            )
            raise exceptions.ApplicationError(
                diagnostic["message"],
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
            diagnostic = self._record_failure_diagnostic(
                exc,
                stage=self._state,
                source="workflow",
            )
            await self._run_finalizing_stage(
                parameters=parameters, status="failed", error=diagnostic["message"]
            )
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=diagnostic["message"])
            await self._record_terminal_state(
                state=STATE_FAILED,
                close_status=CLOSE_STATUS_FAILED,
                summary=diagnostic["message"],
                error_category=diagnostic["category"],
            )
            raise exceptions.ApplicationError(
                diagnostic["message"],
                non_retryable=True,
            ) from exc
        except Exception as exc:
            diagnostic = self._record_failure_diagnostic(
                exc,
                stage=self._state,
                source="workflow",
            )
            failure_summary = diagnostic["message"]
            await self._run_finalizing_stage(
                parameters=parameters, status="failed", error=failure_summary
            )
            self._close_status = CLOSE_STATUS_FAILED
            self._set_state(STATE_FAILED, summary=failure_summary)
            await self._record_terminal_state(
                state=STATE_FAILED,
                close_status=CLOSE_STATUS_FAILED,
                summary=failure_summary,
                error_category=diagnostic["category"],
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
            if (
                publish_failure
                and workflow.patched(RUN_PR_RESOLVER_CONTINUATION_IDENTITY_PATCH)
                and self._is_merge_automation_gated(parameters)
                and (
                    (
                        self._gated_continuation_request
                        and self._gated_continuation_failure_message(parameters) is None
                    )
                    or self._merge_automation_disposition
                    in {"merged", "already_merged"}
                )
            ):
                output_status = "success"
                output_message = (
                    "Workflow completed an authoritative durable continuation "
                    "handoff to merge automation."
                    if self._gated_continuation_request
                    else "Workflow completed authoritative PR resolver terminal evidence."
                )
                publish_failure = False
            if publish_failure:
                finalizing_status = "failed"
                finalizing_error = output_message
            elif output_status == "no_commit":
                finalizing_error = self._publish_reason or output_message

        continuation_failure = False
        if not publish_failure and workflow.patched(
            RUN_UNGATED_CONTINUATION_DISPOSITION_PATCH
        ):
            continuation_message = self._continuation_disposition_failure_message(
                parameters
            )
            if continuation_message:
                continuation_failure = True
                output_status = "needs_continuation"
                output_message = continuation_message
                finalizing_status = "failed"
                finalizing_error = continuation_message

        await self._run_finalizing_stage(
            parameters=parameters, status=finalizing_status, error=finalizing_error
        )
        if self._cancel_requested:
            self._close_status = CLOSE_STATUS_CANCELED
            self._set_state(STATE_CANCELED, summary="Execution canceled.")
            await self._record_terminal_state(
                state=STATE_CANCELED,
                close_status=CLOSE_STATUS_CANCELED,
                summary="Execution canceled.",
            )
            return {"status": "canceled"}

        if publish_failure or continuation_failure:
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

        terminal_state = (
            STATE_NO_COMMIT if output_status == "no_commit" else STATE_COMPLETED
        )
        if self._moonspec_draft_publication_reason is not None:
            # Draft publication completes the run but requires operator
            # attention: the verification evidence is incomplete.
            self._attention_required = True
            output_message = (
                "Workflow completed with a draft pull request; MoonSpec "
                "verification is incomplete and requires operator review. "
                f"{self._moonspec_draft_publication_reason}"
            )
        self._close_status = CLOSE_STATUS_COMPLETED
        self._set_state(terminal_state, summary=output_message)
        await self._record_terminal_state(
            state=terminal_state,
            close_status=CLOSE_STATUS_COMPLETED,
            summary=output_message,
        )

        output: RunWorkflowOutput = {
            "status": output_status,
            "message": output_message,
        }
        if workflow.patched(RUN_DURABLE_FINALIZATION_OUTCOME_PATCH):
            for row in reversed(self._step_ledger_rows):
                execution_outcome = row.get("executionOutcome")
                finalization_outcome = row.get("finalizationOutcome")
                if isinstance(execution_outcome, Mapping):
                    output["executionOutcome"] = dict(execution_outcome)
                if isinstance(finalization_outcome, Mapping):
                    output["finalizationOutcome"] = dict(finalization_outcome)
                if execution_outcome or finalization_outcome:
                    break
        if self._proposals_generated > 0 or self._proposals_submitted > 0:
            output["proposals_generated"] = self._proposals_generated
            output["proposals_submitted"] = self._proposals_submitted
        if self._merge_automation_disposition:
            output["mergeAutomationDisposition"] = self._merge_automation_disposition
        if self._gated_continuation_request:
            gated_continuation = dict(self._gated_continuation_request)
            if workflow.patched(RUN_PR_RESOLVER_OWNED_CONTINUATION_PATCH):
                parent_info = workflow.info().parent
                if parent_info is not None and self._is_merge_automation_gated(parameters):
                    gated_continuation.update(
                        {
                            "ownerWorkflowId": parent_info.workflow_id,
                            "ownerRunId": parent_info.run_id,
                            "ownerWorkflowType": "MoonMind.MergeAutomation",
                            "childWorkflowId": workflow.info().workflow_id,
                            "childRunId": workflow.info().run_id,
                        }
                    )
                output["completionDisposition"] = "gated_continuation"
            output["gatedContinuation"] = gated_continuation
            if workflow.patched(RUN_PR_RESOLVER_CONTINUATION_IDENTITY_PATCH):
                output["executionRef"] = self._gated_continuation_execution_ref
                output["childRunId"] = gated_continuation.get("childRunId")
                output["headSha"] = gated_continuation.get("headSha")
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
        expected_workflow_name = self._expected_workflow_name()
        if workflow_type != expected_workflow_name:
            raise ValueError(f"workflowType must be {expected_workflow_name}")

        self._workflow_type = workflow_type
        self._entry = "user_workflow"
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
        self._moonspec_environment_blocked_publish_action_snapshot = (
            self._normalize_moonspec_environment_blocked_publish_action(
                parameters.get("moonspecEnvironmentBlockedPublishAction")
                or parameters.get("moonspec_environment_blocked_publish_action")
            )
        )
        recovery_source = self._mapping_value(parameters, "recoverySource", "recovery_source")
        self._recovery_source = (
            dict(recovery_source) if isinstance(recovery_source, Mapping) else None
        )
        self._target_runtime = self._runtime_visibility_from_parameters(parameters)
        self._target_skill = self._skill_visibility_from_parameters(parameters)
        self._runtime_inheritance_parameters = (
            self._runtime_inheritance_parameters_from_parameters(parameters)
        )
        task_parameters = self._mapping_value(parameters, "workflow")
        if not task_parameters:
            task_parameters = self._mapping_value(parameters, "task")
        self._record_bounded_story_loop_context(parameters, task_parameters)
        self._record_remediation_context(parameters, task_parameters)
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
        self._record_integration_from_parameters(parameters)

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
        if (
            not scheduled_for
            and self._has_recurrence_metadata(parameters)
            and workflow.patched(RUN_RECURRING_SCHEDULED_START_PATCH)
        ):
            temporal_scheduled_for = temporal_scheduled_start_time(workflow.info())
            if temporal_scheduled_for is not None:
                scheduled_for = temporal_scheduled_for.isoformat()

        if input_ref:
            self._input_ref = input_ref
        if plan_ref:
            self._plan_ref = plan_ref
        if scheduled_for:
            self._scheduled_for = scheduled_for

        return workflow_type, parameters, input_ref, plan_ref, scheduled_for

    def _record_integration_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> None:
        integration = self._string_from_mapping(parameters, "integration")
        if integration:
            integration = integration.strip().lower()
        self._integration_label = integration
        self._integration = (
            integration
            if integration in _EXTERNAL_INTEGRATION_MONITOR_IDS
            else None
        )

    def _record_remediation_context(
        self,
        parameters: Mapping[str, Any],
        task_parameters: Mapping[str, Any],
    ) -> None:
        remediation = task_parameters.get("remediation")
        self._remediation_context = (
            dict(remediation) if isinstance(remediation, Mapping) else {}
        )

        policy = (
            task_parameters.get("remediationPolicy")
            or task_parameters.get("remediation_policy")
            or parameters.get("remediationPolicy")
            or parameters.get("remediation_policy")
        )
        self._remediation_policy = dict(policy) if isinstance(policy, Mapping) else {}

    def _skill_remediation_context(self) -> dict[str, Any]:
        if not self._remediation_context and not self._remediation_policy:
            return {}
        context: dict[str, Any] = {
            "is_remediation_workflow": bool(self._remediation_context),
        }
        if self._remediation_context:
            context["remediation"] = dict(self._remediation_context)
        if self._remediation_policy:
            context["remediation_policy"] = dict(self._remediation_policy)
        return context

    def _has_recurrence_metadata(self, parameters: Mapping[str, Any]) -> bool:
        system_payload = parameters.get("system")
        if not isinstance(system_payload, Mapping):
            return False
        recurrence_payload = system_payload.get("recurrence")
        return isinstance(recurrence_payload, Mapping) and bool(recurrence_payload)

    def _record_bounded_story_loop_context(
        self,
        parameters: Mapping[str, Any],
        task_parameters: Mapping[str, Any],
    ) -> None:
        raw_loop = task_parameters.get("boundedStoryLoop") or parameters.get(
            "boundedStoryLoop"
        )
        if raw_loop is None:
            return
        if not isinstance(raw_loop, Mapping):
            raise ValueError("boundedStoryLoop must be an object when provided")

        loop_payload = self._json_mapping(raw_loop, path="boundedStoryLoop")
        full_supervisor_enabled = _coerce_bool(
            loop_payload.pop("fullSupervisorEnabled", None)
            or loop_payload.pop("full_supervisor_enabled", None),
            default=False,
        )
        loop_input = BoundedStoryLoopInput.model_validate(loop_payload)
        compiled = compile_bounded_story_loop(loop_input)
        scope = bounded_story_loop_scope_guard(
            selected_item_digest=loop_input.selected_item_digest,
            candidate_item_digests=[loop_input.selected_item_digest],
            full_supervisor_enabled=full_supervisor_enabled,
        )
        if not scope.get("allowed"):
            raise ValueError(str(scope.get("reason") or "bounded story loop rejected"))

        self._publish_context["boundedStoryLoop"] = {
            "selectedItemRef": compiled.selected_item_ref,
            "selectedItemDigest": compiled.selected_item_digest,
            "nodeKinds": [node.kind for node in compiled.nodes],
            "publishMode": loop_input.publish_mode,
            "mergeAutomationEnabled": loop_input.merge_automation_enabled,
            "budgets": loop_input.budgets.model_dump(by_alias=True, mode="json"),
            "scopeGuard": scope,
        }

    def _runtime_visibility_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> str | None:
        task_payload = (
            self._mapping_value(parameters, "workflow")
            or self._mapping_value(parameters, "task")
            or {}
        )
        task_runtime_payload = (
            self._mapping_value(task_payload, "runtime")
            if isinstance(task_payload, Mapping)
            else {}
        ) or {}
        runtime_payload = self._mapping_value(parameters, "runtime") or {}
        authored_payload = self._mapping_value(parameters, "authoredTaskInput") or {}
        authored_runtime_payload = (
            self._mapping_value(authored_payload, "runtime")
            if isinstance(authored_payload, Mapping)
            else {}
        ) or {}
        return (
            self._coerce_text(parameters.get("targetRuntime"), max_chars=80)
            or self._coerce_text(parameters.get("target_runtime"), max_chars=80)
            or self._coerce_text(parameters.get("mode"), max_chars=80)
            or self._coerce_text(task_runtime_payload.get("mode"), max_chars=80)
            or self._coerce_text(task_runtime_payload.get("targetRuntime"), max_chars=80)
            or self._coerce_text(
                task_runtime_payload.get("target_runtime"), max_chars=80
            )
            or self._coerce_text(runtime_payload.get("mode"), max_chars=80)
            or self._coerce_text(runtime_payload.get("targetRuntime"), max_chars=80)
            or self._coerce_text(runtime_payload.get("target_runtime"), max_chars=80)
            or self._coerce_text(authored_runtime_payload.get("mode"), max_chars=80)
            or self._coerce_text(
                authored_runtime_payload.get("targetRuntime"), max_chars=80
            )
            or self._coerce_text(
                authored_runtime_payload.get("target_runtime"), max_chars=80
            )
        )

    def _runtime_inheritance_parameters_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        task_payload = (
            self._mapping_value(parameters, "workflow")
            or self._mapping_value(parameters, "task")
            or {}
        )
        task_runtime = self._mapping_value(task_payload, "runtime") or {}
        runtime_payload = self._mapping_value(parameters, "runtime") or {}
        authored_payload = self._mapping_value(parameters, "authoredTaskInput") or {}
        authored_runtime = self._mapping_value(authored_payload, "runtime") or {}

        selection: dict[str, Any] = {}
        for source in (
            parameters,
            task_payload,
            task_runtime,
            runtime_payload,
            authored_runtime,
        ):
            for key, value in self._runtime_selection_from_source(source).items():
                selection.setdefault(key, value)
        if self._target_runtime:
            selection.setdefault("targetRuntime", self._target_runtime)

        compact: dict[str, Any] = {}
        runtime: dict[str, Any] = {}
        for selection_key, parameter_key, runtime_key in (
            ("targetRuntime", "targetRuntime", "mode"),
            ("model", "model", "model"),
            ("effort", "effort", "effort"),
            ("executionProfileRef", "profileId", "executionProfileRef"),
        ):
            value = selection.get(selection_key)
            if value is None:
                continue
            compact[parameter_key] = value
            runtime[runtime_key] = value
        if runtime:
            compact["workflow"] = {"runtime": runtime}
        return compact

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
        task_payload = (
            self._mapping_value(parameters, "workflow")
            or self._mapping_value(parameters, "task")
            or {}
        )
        if not isinstance(task_payload, Mapping):
            return None
        tool_payload = self._mapping_value(task_payload, "tool") or {}
        skill_payload = self._mapping_value(task_payload, "skill") or {}
        tool_type = str(
            tool_payload.get("type") or tool_payload.get("kind") or ""
        ).strip().lower()
        if tool_type in {"", "skill"}:
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

    async def _compile_and_record_resilience_policy(
        self, *, parameters: Mapping[str, Any]
    ) -> None:
        """MM-880: compile + persist the versioned ResiliencePolicy for this run.

        Captures the resilience values that govern the run (attempts, timeouts,
        no-progress handling, provider cooldowns, checkpoint requirements,
        side-effect idempotency, outbound scanning, observability, and cost
        attribution) into one deterministic, artifact-backed envelope before
        step execution begins, and records a compact reference for forensic
        traceability. Compilation happens at an activity boundary so the values
        are captured once and never inferred from environment/provider state
        after the fact.
        """

        if self._resilience_policy_ref is not None:
            return

        provider_profile_id = self._inherited_execution_profile_ref(parameters)
        self._run_resilience_profile_id = provider_profile_id
        policy_ref = await self._compile_resilience_policy_envelope_ref(
            provider_profile_id=provider_profile_id,
            parameters=parameters,
            artifact_name="reports/resilience_policy.json",
        )
        self._resilience_policy_ref = policy_ref
        # Seed the per-profile cache so steps that inherit the run-level profile
        # reuse this policy instead of recompiling an identical one.
        self._resilience_policy_refs_by_profile[provider_profile_id or ""] = policy_ref
        self._update_memo()

    def _resolve_provider_cooldown_inputs(
        self, provider_profile_id: str | None
    ) -> tuple[int | None, dict[str, Any]]:
        """Extract a profile's cooldown + rate-limit policy from snapshots.

        The ``provider_profile.list`` activity serializes a DB-backed profile's
        rate-limit policy as a bare strategy string (for example ``"backoff"``),
        while OAuth/in-memory snapshots may carry a mapping. Preserve either
        shape so the compiled ResiliencePolicy never silently drops the
        configured rate-limit strategy (which later failure/cooldown review
        relies on to know which policy governed the run).
        """

        cooldown_after_429_seconds: int | None = None
        rate_limit_policy: dict[str, Any] = {}
        snapshots = getattr(self, "_profile_snapshots", None)
        if provider_profile_id and isinstance(snapshots, Mapping):
            snapshot = snapshots.get(provider_profile_id)
            if isinstance(snapshot, Mapping):
                raw_cooldown = snapshot.get("cooldownAfter429Seconds")
                if raw_cooldown is None:
                    raw_cooldown = snapshot.get("cooldown_after_429_seconds")
                if isinstance(raw_cooldown, int) and not isinstance(raw_cooldown, bool):
                    cooldown_after_429_seconds = raw_cooldown
                raw_rate_limit_policy = snapshot.get("rateLimitPolicy")
                if raw_rate_limit_policy is None:
                    raw_rate_limit_policy = snapshot.get("rate_limit_policy")
                if isinstance(raw_rate_limit_policy, Mapping):
                    rate_limit_policy = dict(raw_rate_limit_policy)
                elif isinstance(raw_rate_limit_policy, str):
                    strategy = raw_rate_limit_policy.strip()
                    if strategy:
                        rate_limit_policy = {"strategy": strategy}
        return cooldown_after_429_seconds, rate_limit_policy

    async def _compile_resilience_policy_envelope_ref(
        self,
        *,
        provider_profile_id: str | None,
        parameters: Mapping[str, Any],
        artifact_name: str,
    ) -> dict[str, Any]:
        """Compile + persist a versioned ResiliencePolicy and return its ref.

        Shared by the run-level policy and per-step policies. Compilation
        happens at an activity boundary so the values are captured once and
        never inferred from environment/provider state after the fact.
        """

        cooldown_after_429_seconds, rate_limit_policy = (
            self._resolve_provider_cooldown_inputs(provider_profile_id)
        )
        compile_input = ResiliencePolicyCompileInput(
            workflowId=workflow.info().workflow_id,
            runId=workflow.info().run_id,
            compiledAt=workflow.now().isoformat(),
            runtimeId=self._target_runtime,
            providerProfileId=provider_profile_id,
            cooldownAfter429Seconds=cooldown_after_429_seconds,
            rateLimitPolicy=rate_limit_policy,
            model=self._coerce_text(parameters.get("model"), max_chars=160),
            effort=self._coerce_text(parameters.get("effort"), max_chars=80),
        )

        compile_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "resilience.compile_policy"
        )
        envelope_payload = await workflow.execute_activity(
            "resilience.compile_policy",
            compile_input.model_dump(by_alias=True, exclude_none=True, mode="json"),
            **self._execute_kwargs_for_route(compile_route),
        )
        if isinstance(envelope_payload, tuple) and envelope_payload:
            envelope_payload = envelope_payload[0]
        envelope = ResiliencePolicyEnvelope.model_validate(envelope_payload)

        # MM-884: capture the run-level cost-attribution settings once (the
        # run-level policy is compiled first) so the incident reconstruction
        # manifest can name the cost dimensions that governed the run.
        if self._cost_attribution_settings is None:
            self._cost_attribution_settings = envelope.cost_attribution.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )

        envelope_ref = await self._write_json_artifact(
            name=artifact_name,
            payload=envelope.model_dump(by_alias=True, mode="json"),
            content_type=RESILIENCE_POLICY_CONTENT_TYPE,
            metadata_json={
                "artifact_kind": "resilience_policy_envelope",
                "policyId": envelope.policy_id,
                "policyVersion": envelope.policy_version,
                "digest": envelope.digest,
            },
        )
        return envelope.compact_ref(envelope_ref=envelope_ref).model_dump(
            by_alias=True, mode="json"
        )

    async def _resolve_step_resilience_policy_ref(
        self,
        *,
        node_id: str,
        execution_profile_ref: str | None,
        parameters: Mapping[str, Any],
    ) -> None:
        """Stamp a per-step ResiliencePolicy ref when a node overrides the profile.

        MM-880: when a plan node resolves to a provider profile that differs
        from the run-level inherited profile, its step manifests must reference
        a policy compiled with that node's profile (cooldown + rate-limit)
        rather than the run-level policy. Without this, a step using a
        node-level profile would be traced to the wrong cooldown/rate-limit
        values during failure review. Policies are cached per profile so a
        profile is compiled at most once per run.
        """

        if not workflow.patched(RUN_STEP_RESILIENCE_POLICY_PATCH):
            return
        if self._resilience_policy_ref is None:
            return
        profile_id = (execution_profile_ref or "").strip() or None
        run_profile_id = (self._run_resilience_profile_id or "").strip() or None
        if profile_id is None or profile_id == run_profile_id:
            # No override (or it matches the run-level profile): the run-level
            # policy already governs this step.
            return
        cached = self._resilience_policy_refs_by_profile.get(profile_id)
        if cached is None:
            cached = await self._compile_resilience_policy_envelope_ref(
                provider_profile_id=profile_id,
                parameters=parameters,
                artifact_name=(
                    "reports/resilience_policy_"
                    f"{self._artifact_slug(profile_id)}.json"
                ),
            )
            self._resilience_policy_refs_by_profile[profile_id] = cached
        self._step_resilience_policy_refs[node_id] = cached

    @staticmethod
    def _artifact_slug(value: str) -> str:
        """Return a filesystem-safe slug for embedding in an artifact name."""

        slug = "".join(
            char if char.isalnum() or char in {"-", "_"} else "-"
            for char in str(value)
        ).strip("-")
        return slug or "profile"

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

        # MM-880: compile + persist the versioned ResiliencePolicy envelope
        # before any step executes, so every step execution can be traced to the
        # exact policy values that governed it.
        if workflow.patched(RUN_RESILIENCE_POLICY_PATCH):
            await self._compile_and_record_resilience_policy(parameters=parameters)

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
        registry_snapshot: ToolRegistrySnapshot | None = None

        async def load_registry_snapshot() -> ToolRegistrySnapshot:
            nonlocal registry_snapshot
            if registry_snapshot is not None:
                return registry_snapshot
            registry_ref = str(plan_definition.metadata.registry_snapshot.artifact_ref)
            registry_payload = await execute_typed_activity(
                "artifact.read",
                ArtifactReadInput(
                    principal=self._principal(),
                    artifact_ref=registry_ref,
                ),
                **self._execute_kwargs_for_route(artifact_read_route),
            )
            registry_dict = self._decode_json_payload(
                registry_payload,
                error_message="registry snapshot must resolve to a JSON object",
            )
            registry_snapshot = ToolRegistrySnapshot(
                digest=str(plan_definition.metadata.registry_snapshot.digest),
                artifact_ref=registry_ref,
                skills=parse_tool_registry(registry_dict),
            )
            return registry_snapshot

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
        self._capture_prepared_input_refs(parameters)

        task_payload = parameters.get("task")
        task_skills = (
            task_payload.get("skills")
            if isinstance(task_payload, Mapping)
            else parameters.get("skills")
        )
        failure_mode = plan_definition.policy.failure_mode
        publish_mode = self._publish_mode(parameters)
        pr_publish_optional = self._pr_publish_optional_for_plan(
            ordered_nodes
        ) or self._pr_publish_optional_for_task(
            parameters,
            include_applied_templates=True,
        )
        require_pull_request_url = (
            publish_mode == "pr"
            and self._integration is None
            and not pr_publish_optional
        )
        pull_request_url: str | None = None
        # Keep this patch command in its historical position, after the
        # jules-bundling marker and before any lazy registry read. Removing or
        # reordering it strands in-flight user workflow histories before
        # cancellation/failure handling.
        workflow.patched(RUN_CONDITIONAL_REGISTRY_READ_PATCH)
        step_retry_overrides_enabled = workflow.patched(RUN_STEP_RETRY_OVERRIDES_PATCH)
        plan_routed_moonspec_remediation_enabled = workflow.patched(
            RUN_PLAN_ROUTED_MOONSPEC_REMEDIATION_PATCH
        )
        previous_step_outputs: Mapping[str, Any] = {}
        execution_result: Any = None
        for index, node in enumerate(ordered_nodes, start=1):
            await self._wait_if_paused_at_safe_boundary()
            if self._cancel_requested:
                return

            tool = self._plan_node_tool_mapping(node)
            if not isinstance(tool, Mapping):
                raise ValueError("plan node tool definition is required")

            tool_type = (
                str(tool.get("type") or tool.get("kind") or "")
                .strip()
                .lower()
            )
            tool_name = str(
                tool.get("name") or tool.get("id") or ""
            ).strip()
            node_id = str(node.get("id") or "unknown")
            raw_node_inputs = node.get("inputs")
            original_node_inputs = (
                dict(raw_node_inputs) if isinstance(raw_node_inputs, Mapping) else {}
            )
            if self._is_preserved_step(node_id):
                self._record_preserved_step_terminal_state(node_id, node)
                preserved_outputs = self._preserved_step_outputs(node_id)
                if preserved_outputs:
                    previous_step_outputs = preserved_outputs
                continue
            current_step_row = self._step_ledger_row_for(node_id)
            if (
                isinstance(current_step_row, Mapping)
                and current_step_row.get("status") == "pending"
            ):
                continue
            if workflow.patched(RUN_MOONSPEC_REMEDIATION_STEP_SKIP_PATCH):
                skip_reason = self._moonspec_remediation_loop_skip_reason(
                    node,
                    tool_name=tool_name,
                    node_inputs=original_node_inputs,
                )
                if skip_reason:
                    self._mark_step_terminal(
                        node_id,
                        status="skipped",
                        updated_at=workflow.now(),
                        summary=skip_reason,
                        last_error=None,
                    )
                    self._summary = skip_reason
                    self._refresh_step_readiness(updated_at=workflow.now())
                    self._update_memo()
                    continue
            handoff_block_reason = (
                self._jira_orchestrate_external_handoff_block_reason(node)
            )
            if handoff_block_reason:
                self._plan_blocked_message = handoff_block_reason
                self._publish_status = "not_required"
                self._publish_reason = handoff_block_reason
                self._publish_context["publicationBlockedBy"] = "moonspec_verify"
                self._summary = handoff_block_reason
                self._mark_remaining_plan_steps_skipped(
                    ordered_nodes=ordered_nodes,
                    completed_index=index - 2,
                    summary=handoff_block_reason,
                )
                self._refresh_step_readiness(updated_at=workflow.now())
                self._update_memo()
                break
            if tool_type == "skill":
                await load_registry_snapshot()
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
            max_consecutive_no_progress_attempts = max(1, max_review_attempts + 1)
            if review_gate_active:
                raw_no_progress_attempts = getattr(
                    approval_policy,
                    "max_consecutive_no_progress_attempts",
                    None,
                )
                if raw_no_progress_attempts is not None:
                    max_consecutive_no_progress_attempts = int(
                        raw_no_progress_attempts
                    )
            review_retry_count = 0
            consecutive_no_progress_attempts = 0
            moonspec_contract_repair_attempts = 0
            previous_review_feedback: str | None = None
            previous_review_issues: tuple[Mapping[str, Any], ...] = ()
            result_status: str | None = None
            execution_result = None
            accepted_execution = False
            accepted_gate_verdict = "FULLY_IMPLEMENTED"
            accepted_gate_action = "advance"
            accepted_gate_disposition = "accepted"
            accepted_gate_budget: dict[str, Any] | None = None
            gate_stop_requested = False
            step_failure_summary: str | None = None
            blocked_outcome_wait_skipped = False
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
                current_previous_outputs = self._merge_preserved_dependency_outputs(
                    node_id,
                    previous_step_outputs,
                )
                current_previous_outputs = self._merge_trusted_issue_context(
                    current_previous_outputs
                )
                if current_previous_outputs:
                    node_inputs["previousOutputs"] = dict(current_previous_outputs)
                self._record_step_dependency_inputs(node_id)

                self._step_count = index
                self._summary = (
                    f"Executing plan step {index}/{len(ordered_nodes)}: {tool_name}"
                )
                self._update_memo()
                self._refresh_step_readiness(updated_at=workflow.now())
                if node_id == self._recovery_failed_step_id:
                    await self._record_canonical_step_checkpoint(
                        node_id,
                        boundary="before_recovery_restoration",
                        updated_at=workflow.now(),
                    )
                await self._prepare_recovery_workspace_for_failed_step(node_id)
                self._mark_step_running(
                    node_id,
                    updated_at=workflow.now(),
                    summary=self._summary,
                )
                capture_input_source = dict(node_inputs)
                workflow_workspace_spec = self._mapping_value(
                    parameters,
                    "workspaceSpec",
                    "workspace_spec",
                )
                if (
                    isinstance(workflow_workspace_spec, Mapping)
                    and "workspaceSpec" not in capture_input_source
                    and "workspace_spec" not in capture_input_source
                ):
                    capture_input_source["workspaceSpec"] = dict(
                        workflow_workspace_spec
                    )
                self._record_step_workspace_capture_input(
                    node_id,
                    capture_input_source,
                )
                current_step_execution = self._step_execution_for(node_id) or 1
                attempt_reason = (
                    "quality_gate_failed"
                    if previous_review_feedback
                    else (
                        "recover_from_failed_step"
                        if node_id == self._recovery_failed_step_id
                        else (
                            "initial_execution"
                            if current_step_execution == 1
                            else "runtime_recovered"
                        )
                    )
                )
                await self._record_canonical_step_checkpoint(
                    node_id,
                    boundary="after_prepare",
                    updated_at=workflow.now(),
                )

                system_retries = 0
                while system_retries <= 3:
                    route = None
                    execute_payload = None
                    await self._wait_if_paused_at_safe_boundary()
                    if self._cancel_requested:
                        return
                    await self._record_canonical_step_checkpoint(
                        node_id,
                        boundary="before_execution",
                        updated_at=workflow.now(),
                    )
                    step_execution_naming_enabled = workflow.patched(
                        RUN_STEP_EXECUTION_NAMING_PATCH
                    )
                    step_execution_manifest_enabled = workflow.patched(
                        RUN_STEP_EXECUTION_MANIFEST_PATCH
                    )

                    if (
                        tool_type != "agent_runtime"
                        and step_execution_manifest_enabled
                    ):
                        await self._record_step_execution_manifest(
                            node_id,
                            phase="start",
                            updated_at=workflow.now(),
                            summary=self._summary,
                            reason=attempt_reason,
                            input_refs=(
                                node_inputs.get("inputRefs")
                                if isinstance(node_inputs.get("inputRefs"), list)
                                else []
                            ),
                            execution={
                                "kind": tool_type,
                                "toolName": tool_name,
                                "idempotencyKey": step_execution_operation_idempotency_key(
                                    workflow_id=workflow.info().workflow_id,
                                    run_id=workflow.info().run_id,
                                    logical_step_id=node_id,
                                    execution_ordinal=current_step_execution,
                                    operation="execute",
                                ),
                            },
                            budget=self._review_gate_budget_metadata(
                                max_review_attempts=max_review_attempts,
                                review_retry_count=review_retry_count,
                                max_consecutive_no_progress_attempts=(
                                    max_consecutive_no_progress_attempts
                                ),
                                consecutive_no_progress_attempts=(
                                    consecutive_no_progress_attempts
                                ),
                            )
                            if review_gate_active
                            else None,
                        )
                    if (
                        tool_type != "agent_runtime"
                        and self._is_step_execution_launch_blocked(
                            node_id,
                            attempt=current_step_execution,
                        )
                    ):
                        execution_result = {
                            "status": "FAILED",
                            "outputs": {
                                "error": "missing_required_checkpoint_evidence",
                                "summary": "Workspace policy rejected before launch.",
                            },
                        }
                        result_status = "FAILED"
                        break

                    route = None
                    execute_payload = None
                    child_workflow_id: str | None = None
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
                                step_execution=current_step_execution,
                                queue_order=index,
                                attempt_reason=attempt_reason,
                            )
                            if (
                                node_id == self._recovery_failed_step_id
                                and self._checkpoint_recovery_state is not None
                            ):
                                locator = self._checkpoint_recovery_state.get(
                                    "destinationWorkspaceLocator"
                                )
                                evidence_ref = self._checkpoint_recovery_state.get(
                                    "restorationEvidenceRef"
                                )
                                if not isinstance(locator, Mapping) or not evidence_ref:
                                    raise ValueError(
                                        "CHECKPOINT_RESTORATION_NOT_READY: recovered "
                                        "AgentRun requires destination locator and evidence"
                                    )
                                recovered_workspace = dict(request.workspace_spec)
                                recovered_workspace["workspaceLocator"] = dict(locator)
                                recovered_workspace["restorationEvidenceRef"] = evidence_ref
                                recovered_workspace["sourceCheckpointRef"] = (
                                    self._checkpoint_recovery_state[
                                        "sourceCheckpointRef"
                                    ]
                                )
                                recovered_workspace["capabilityDigest"] = (
                                    self._checkpoint_recovery_state["capabilityDigest"]
                                )
                                request = request.model_copy(
                                    update={"workspace_spec": recovered_workspace}
                                )
                            await self._resolve_step_resilience_policy_ref(
                                node_id=node_id,
                                execution_profile_ref=request.execution_profile_ref,
                                parameters=parameters,
                            )
                            if step_execution_manifest_enabled:
                                await self._record_step_execution_manifest(
                                    node_id,
                                    phase="start",
                                    updated_at=workflow.now(),
                                    summary=self._summary,
                                    reason=attempt_reason,
                                    input_refs=(
                                        node_inputs.get("inputRefs")
                                        if isinstance(node_inputs.get("inputRefs"), list)
                                        else []
                                    ),
                                    execution={
                                        "kind": tool_type,
                                        "toolName": tool_name,
                                        "idempotencyKey": step_execution_operation_idempotency_key(
                                            workflow_id=workflow.info().workflow_id,
                                            run_id=workflow.info().run_id,
                                            logical_step_id=node_id,
                                            execution_ordinal=current_step_execution,
                                            operation="execute",
                                        ),
                                    },
                                    budget=self._review_gate_budget_metadata(
                                        max_review_attempts=max_review_attempts,
                                        review_retry_count=review_retry_count,
                                        max_consecutive_no_progress_attempts=(
                                            max_consecutive_no_progress_attempts
                                        ),
                                        consecutive_no_progress_attempts=(
                                            consecutive_no_progress_attempts
                                        ),
                                    )
                                    if review_gate_active
                                    else None,
                                )
                                request = (
                                    await self._request_with_persisted_retrieval_ref(
                                        request,
                                        logical_step_id=node_id,
                                        attempt=current_step_execution,
                                    )
                                )
                            slot_continuity_enabled = workflow.patched(
                                RUN_SLOT_CONTINUITY_PATCH
                            )
                            if self._is_step_execution_launch_blocked(
                                node_id,
                                attempt=current_step_execution,
                            ):
                                execution_result = {
                                    "status": "FAILED",
                                    "outputs": {
                                        "error": "missing_required_checkpoint_evidence",
                                        "summary": (
                                            "Workspace policy rejected before launch."
                                        ),
                                    },
                                }
                                result_status = "FAILED"
                                break
                            if slot_continuity_enabled:
                                self._mark_slot_continuity_for_next_step(
                                    request=request,
                                    ordered_nodes=ordered_nodes,
                                    current_index=index,
                                )
                            await self._maybe_clear_workflow_scoped_session_before_step(
                                request=request,
                                logical_step_id=node_id,
                            )
                            request = await self._maybe_bind_workflow_scoped_session(request)
                            selected_skill_for_repair = node_inputs.get(
                                "selectedSkill"
                            )
                            if (
                                request.agent_kind == "managed"
                                and not self._is_jira_agent_skill_name(
                                    selected_skill_for_repair
                                )
                            ):
                                self._last_publish_repair_request = request
                                self._last_publish_repair_node_id = node_id
                            child_workflow_id = (
                                f"{workflow.info().workflow_id}:agent:{node_id}"
                            )
                            if current_step_execution > 1:
                                step_execution_label = "attempt"
                                if step_execution_naming_enabled:
                                    step_execution_label = "execution"
                                child_workflow_id = (
                                    f"{child_workflow_id}:{step_execution_label}{current_step_execution}"
                                )
                            if system_retries > 0:
                                child_workflow_id = (
                                    f"{child_workflow_id}:retry{system_retries}"
                                )
                            temporal_pr_resolver = (
                                bool(
                                    self._native_skill_binding_by_step.get(
                                        node_id, {}
                                    ).get("eligible")
                                )
                                and workflow.patched(
                                    RUN_TEMPORAL_PR_RESOLVER_OWNERSHIP_PATCH
                                )
                            )
                            if (
                                str(selected_skill_for_repair or "").strip().lower()
                                == "pr-resolver"
                            ):
                                binding = self._native_skill_binding_by_step.get(
                                    node_id,
                                    {
                                        "eligible": False,
                                        "host": "cli",
                                        "reasonCode": "resolved_skill_evidence_unavailable",
                                        "identity": {},
                                    },
                                )
                                self._publish_context["prResolverNativeBinding"] = dict(
                                    binding
                                )
                                if not temporal_pr_resolver:
                                    portable_note = (
                                        "\n\nHost decision: execute the resolved pr-resolver "
                                        "Skill directly because "
                                        f"{binding.get('reasonCode')}. Follow its SKILL.md "
                                        "workflow and packaged helpers, and publish its "
                                        "terminal evidence. MoonMind must not substitute "
                                        "native PR snapshot, comment, gate, or merge logic."
                                    )
                                    request = request.model_copy(
                                        update={
                                            "instruction_ref": (
                                                str(request.instruction_ref or "")
                                                + portable_note
                                            )
                                        }
                                    )
                            if temporal_pr_resolver:
                                child_workflow_id = f"{child_workflow_id}:pr-resolver"
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
                                if temporal_pr_resolver:
                                    worker_capability: dict[str, Any] = {}
                                    if workflow.patched(
                                        RUN_PR_RESOLVER_CAPABILITY_PREFLIGHT_PATCH
                                    ):
                                        capability_result = await workflow.execute_activity(
                                            "worker.verify_workflow_capability",
                                            {
                                                "workflowType": "MoonMind.PRResolver",
                                                "taskQueue": self._workflow_child_task_queue(),
                                            },
                                            task_queue=INTEGRATIONS_TASK_QUEUE,
                                            start_to_close_timeout=timedelta(seconds=15),
                                            retry_policy=RetryPolicy(maximum_attempts=1),
                                        )
                                        worker_capability = (
                                            dict(capability_result)
                                            if isinstance(capability_result, Mapping)
                                            else {}
                                        )
                                        if not worker_capability.get("available"):
                                            raise _worker_capability_unavailable_error(
                                                worker_capability
                                            )
                                    resolved_pull_request = None
                                    merge_gate = parameters.get("mergeGate")
                                    merge_gate = (
                                        merge_gate
                                        if isinstance(merge_gate, Mapping)
                                        else {}
                                    )
                                    repository, selector = (
                                        pr_resolver_identity_selector(
                                            request=request,
                                            node_inputs=node_inputs,
                                            workflow_parameters=parameters,
                                        )
                                    )
                                    try:
                                        numeric_selector = int(selector)
                                    except (TypeError, ValueError):
                                        numeric_selector = 0
                                    if (
                                        workflow.patched(
                                            RUN_PR_RESOLVER_SELECTOR_RESOLUTION_PATCH
                                        )
                                        and numeric_selector <= 0
                                        and not str(
                                            merge_gate.get("pullRequestUrl") or ""
                                        ).strip()
                                    ):
                                        selector_result = await workflow.execute_activity(
                                            "pr_resolver.resolve_selector",
                                            {
                                                "repository": repository,
                                                "selector": selector,
                                            },
                                            task_queue=INTEGRATIONS_TASK_QUEUE,
                                            start_to_close_timeout=timedelta(minutes=2),
                                            retry_policy=RetryPolicy(
                                                maximum_attempts=3
                                            ),
                                        )
                                        resolved_pull_request = (
                                            dict(selector_result)
                                            if isinstance(selector_result, Mapping)
                                            else {}
                                        )
                                        if not resolved_pull_request.get("resolved"):
                                            raise ValueError(
                                                str(
                                                    resolved_pull_request.get("summary")
                                                    or "PR selector could not be resolved."
                                                )
                                            )
                                    resolver_input = build_pr_resolver_start_input(
                                        request=request,
                                        node_inputs=node_inputs,
                                        workflow_parameters=parameters,
                                        parent_workflow_id=workflow.info().workflow_id,
                                        parent_run_id=workflow.info().run_id,
                                        principal=self._principal(),
                                        step_id=node_id,
                                        resolved_pull_request=resolved_pull_request,
                                        implementation_identity=(
                                            self._native_skill_binding_by_step.get(
                                                node_id, {}
                                            ).get("identity")
                                            or {}
                                        ),
                                        worker_capability=worker_capability,
                                    )
                                    child_result = await workflow.execute_child_workflow(
                                        "MoonMind.PRResolver",
                                        resolver_input.model_dump(
                                            by_alias=True, mode="json"
                                        ),
                                        id=child_workflow_id,
                                        task_queue=self._workflow_child_task_queue(),
                                        cancellation_type=(
                                            ChildWorkflowCancellationType.TRY_CANCEL
                                        ),
                                    )
                                else:
                                    child_result = await workflow.execute_child_workflow(
                                        "MoonMind.AgentRun",
                                        request,
                                        id=child_workflow_id,
                                        task_queue=self._workflow_child_task_queue(),
                                    )
                            finally:
                                self._active_agent_child_workflow_id = None
                                self._active_agent_id = None
                            execution_result = self._map_agent_run_result(child_result)
                        except Exception as exc:
                            if self._should_propagate_agent_child_cancellation(exc):
                                raise
                            diagnostic = self._record_failure_diagnostic(
                                exc,
                                stage=self._state,
                                step_id=node_id,
                                step_title=tool_name,
                                source="child_workflow",
                                child_workflow_id=child_workflow_id,
                            )
                            self._mark_step_terminal(
                                node_id,
                                status="failed",
                                updated_at=workflow.now(),
                                summary=diagnostic["message"],
                                last_error=diagnostic["category"],
                            )
                            if failure_mode == "FAIL_FAST":
                                raise
                            result_status = "FAILED"
                            break

                    elif self._is_step_execution_launch_blocked(
                        node_id,
                        attempt=current_step_execution,
                    ):
                        execution_result = {
                            "status": "FAILED",
                            "outputs": {
                                "error": "missing_required_checkpoint_evidence",
                                "summary": "Workspace policy rejected before launch.",
                            },
                        }
                        result_status = "FAILED"
                        break

                    elif tool_type == "skill" and tool_name == CONTAINER_RUN_JOB_TOOL:
                        try:
                            execution_result = await self._execute_container_job_tool(
                                node_inputs=node_inputs,
                                node_id=node_id,
                                execution_ordinal=current_step_execution,
                            )
                        except Exception as exc:
                            diagnostic = self._record_step_execution_exception(
                                exc,
                                logical_step_id=node_id,
                                tool_name=tool_name,
                                source="container_job",
                                updated_at=workflow.now(),
                            )
                            if failure_mode == "FAIL_FAST":
                                raise
                            execution_result = {
                                "status": "FAILED",
                                "outputs": {
                                    "error": diagnostic.get("category"),
                                    "summary": diagnostic.get("message"),
                                },
                            }

                    elif tool_type == "skill":
                        snapshot = await load_registry_snapshot()
                        definition = snapshot.get_skill(name=tool_name)
                        route = DEFAULT_ACTIVITY_CATALOG.resolve_skill(definition)
                        node_options = (
                            node.get("options")
                            if isinstance(node.get("options"), Mapping)
                            else {}
                        )
                        execute_payload = {
                            "registry_snapshot_ref": snapshot.artifact_ref,
                            "principal": self._principal(),
                            "invocation_payload": {
                                "id": node_id,
                                "tool": {
                                    "type": tool_type,
                                    "name": tool_name,
                                },
                                "skill": {
                                    "name": tool_name,
                                },
                                "inputs": node_inputs,
                                "options": node_options,
                            },
                            "context": {
                                "namespace": workflow.info().namespace,
                                "workflow_id": workflow.info().workflow_id,
                                "run_id": workflow.info().run_id,
                                "node_id": node_id,
                                **self._skill_remediation_context(),
                            },
                            "idempotency_key": step_execution_operation_idempotency_key(
                                workflow_id=workflow.info().workflow_id,
                                run_id=workflow.info().run_id,
                                logical_step_id=node_id,
                                execution_ordinal=current_step_execution,
                                operation="execute",
                            ),
                        }
                        max_attempts_override = (
                            self._step_retry_attempts_override(
                                node_inputs=node_inputs,
                                node_options=node_options,
                            )
                            if step_retry_overrides_enabled
                            else None
                        )
                        try:
                            execution_result = await workflow.execute_activity(
                                route.activity_type,
                                execute_payload,
                                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                                **self._execute_kwargs_for_route(
                                    route,
                                    max_attempts_override=max_attempts_override,
                                ),
                            )
                        except Exception as exc:
                            diagnostic = self._record_step_execution_exception(
                                exc,
                                logical_step_id=node_id,
                                tool_name=tool_name,
                                source="activity",
                                updated_at=workflow.now(),
                            )
                            if step_execution_manifest_enabled:
                                await self._record_step_execution_manifest(
                                    node_id,
                                    phase="terminal",
                                    updated_at=workflow.now(),
                                    reason=attempt_reason,
                                    status="failed",
                                    terminal_disposition="failed_unrecoverable",
                                    summary=diagnostic.get("message"),
                                    execution={
                                        "kind": tool_type,
                                        "toolName": tool_name,
                                        "activityType": route.activity_type,
                                    },
                                )
                            if failure_mode == "FAIL_FAST":
                                raise
                            result_status = "FAILED"
                            break

                    else:
                        raise ValueError(
                            f"unsupported plan node tool.type: '{tool_type}'; "
                            "expected 'agent_runtime' or 'skill'"
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
                    if workflow.patched(RUN_DURABLE_FINALIZATION_OUTCOME_PATCH):
                        outcome_recorded_at = workflow.now()
                        self._record_primary_execution_outcome(
                            node_id,
                            execution_result=execution_result,
                            result_status=result_status,
                            recorded_at=outcome_recorded_at,
                        )
                        self._update_memo()
                        await self._finalize_after_execution_checkpoint(
                            node_id,
                            updated_at=outcome_recorded_at,
                        )
                    else:
                        await self._record_canonical_step_checkpoint(
                            node_id,
                            boundary="after_execution",
                            updated_at=workflow.now(),
                        )
                    if (
                        result_status == "COMPLETED"
                        and workflow.patched(RUN_JIRA_BLOCKER_RECHECK_PATCH)
                        and self._jira_blocker_waitable_result(
                            execution_result,
                            tool_type=tool_type,
                            tool_name=tool_name,
                            selected_skill=node_inputs.get("selectedSkill"),
                        )
                    ):
                        execution_result, blocked_outcome_wait_skipped = (
                            await self._wait_for_jira_blocker_resolution(
                                execution_result=execution_result,
                                node_id=node_id,
                                tool_name=tool_name,
                                tool_type=tool_type,
                                route=route,
                                execute_payload=execute_payload,
                                selected_skill=node_inputs.get("selectedSkill"),
                                node=node,
                                node_inputs=node_inputs,
                                task_skills=task_skills,
                                workflow_parameters=parameters,
                                failure_mode=failure_mode,
                                step_retry_overrides_enabled=step_retry_overrides_enabled,
                            )
                        )
                        if self._cancel_requested:
                            return
                        result_status = self._activity_result_status(execution_result)
                        if result_status is None:
                            if failure_mode == "FAIL_FAST":
                                raise ValueError(
                                    "plan node execution result is missing required "
                                    "status field"
                                )
                            break
                        self._record_step_result_evidence(
                            node_id,
                            execution_result=execution_result,
                            updated_at=workflow.now(),
                        )
                        if workflow.patched(RUN_DURABLE_FINALIZATION_OUTCOME_PATCH):
                            self._record_primary_execution_outcome(
                                node_id,
                                execution_result=execution_result,
                                result_status=result_status,
                                recorded_at=workflow.now(),
                            )
                            self._update_memo()
                    if result_status != "COMPLETED":
                        failure_message = self._activity_result_failure_message(
                            execution_result
                        )
                        provider_failure_summary = (
                            self._activity_result_provider_failure_summary(
                                execution_result
                            )
                        )
                        operator_failure_summary = (
                            provider_failure_summary
                            or self._activity_result_operator_summary(execution_result)
                            or failure_message
                        )
                        step_failure_summary = self._humanize_step_failure_summary(
                            summary=operator_failure_summary,
                            tool_name=tool_name,
                            failure_message=failure_message,
                        )

                        if workflow.patched(RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH):
                            retryable = self._activity_result_retryable(
                                execution_result,
                                failure_message=failure_message,
                                tool_type=tool_type,
                            )
                        else:
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
                            await self._record_step_execution_manifest(
                                node_id,
                                phase="terminal",
                                updated_at=workflow.now(),
                                reason=attempt_reason,
                                status="failed",
                                terminal_disposition="retryable",
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
                            current_step_execution = self._step_execution_for(node_id) or (
                                current_step_execution + 1
                            )
                            attempt_reason = "runtime_recovered"
                            continue

                        diagnostics_ref = None
                        outputs = self._get_from_result(execution_result, "outputs")
                        if (
                            isinstance(outputs, Mapping)
                            and workflow.patched(RUN_FAILED_RESULT_BLOCKER_PATCH)
                        ):
                            diagnostics_ref = self._coerce_text(
                                outputs.get("diagnosticsRef")
                                or outputs.get("diagnostics_ref"),
                                max_chars=400,
                            )
                            self._record_result_failure_diagnostic(
                                stage=self._state,
                                category=failure_message,
                                source=(
                                    "child_workflow"
                                    if tool_type == "agent_runtime"
                                    else "activity"
                                ),
                                step_id=node_id,
                                step_title=tool_name,
                                message=step_failure_summary,
                                child_workflow_id=child_workflow_id,
                                diagnostics_ref=diagnostics_ref,
                                terminal_evidence=outputs,
                            )
                            # MM-884: capture the sanitized provider failure
                            # envelope and observed cost (first failure wins) so
                            # the incident reconstruction manifest can correlate
                            # the provider/credential source and per-run cost.
                            self._capture_incident_failure_evidence(outputs)
                        self._mark_step_terminal(
                            node_id,
                            status="failed",
                            updated_at=workflow.now(),
                            summary=step_failure_summary,
                            last_error=failure_message,
                        )
                        await self._record_step_execution_manifest(
                            node_id,
                            phase="terminal",
                            updated_at=workflow.now(),
                            reason=attempt_reason,
                            status="failed",
                            terminal_disposition="failed_unrecoverable",
                        )
                        if failure_mode == "FAIL_FAST":
                            if provider_failure_summary:
                                raise ValueError(provider_failure_summary)
                            if workflow.patched(
                                RUN_FAIL_FAST_STEP_FAILURE_SUMMARY_PATCH
                            ):
                                raise ValueError(
                                    self._format_step_failure_exception_message(
                                        node_id=node_id,
                                        tool_name=tool_name,
                                        result_status=result_status,
                                        step_failure_summary=step_failure_summary,
                                        failure_message=failure_message,
                                        child_workflow_id=child_workflow_id,
                                        diagnostics_ref=diagnostics_ref,
                                    )
                                )
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

                if workflow.patched(RUN_MOONSPEC_GATE_CONTRACT_REPAIR_PATCH):
                    contract_repair_feedback = (
                        self._moonspec_verify_contract_repair_feedback(
                            execution_result=execution_result,
                            tool_name=tool_name,
                            node_inputs=node_inputs,
                        )
                    )
                    if (
                        contract_repair_feedback
                        and moonspec_contract_repair_attempts
                        < _MOONSPEC_GATE_CONTRACT_REPAIR_MAX_ATTEMPTS
                    ):
                        moonspec_contract_repair_attempts += 1
                        if workflow.patched(
                            RUN_MOONSPEC_GATE_CONTRACT_REPAIR_FRESH_SOURCE_PATCH
                        ):
                            self._prepare_moonspec_contract_repair_attempt(node_id)
                        self._upsert_step_check(
                            node_id,
                            kind="moonspec_gate_contract",
                            status="failed",
                            summary=self._bounded_review_summary(
                                contract_repair_feedback,
                                fallback=(
                                    "MoonSpec verify output failed gate "
                                    "contract validation"
                                ),
                            ),
                            retry_count=moonspec_contract_repair_attempts,
                            artifact_ref=None,
                        )
                        previous_review_feedback = contract_repair_feedback
                        previous_review_issues = ()
                        # The repair budget is tracked separately from the
                        # approval-policy review budget: leave
                        # current_review_attempt unchanged so a contract
                        # repair never consumes review-gate attempts.
                        continue

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
                gate_result = parse_step_gate_result(
                    await workflow.execute_activity(
                        "step.review",
                        review_request.to_payload(),
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        **self._execute_kwargs_for_route(review_route),
                    )
                )
                review_verdict = gate_result.to_review_verdict()
                step_execution = self._step_execution_for(node_id) or 0
                gate_result_ref = await self._write_json_artifact(
                    name=(
                        "reports/gate_result_"
                        f"{node_id}_attempt_{step_execution}.json"
                    ),
                    payload=gate_result.to_payload(),
                )
                gate_check_metadata = self._gate_check_metadata(
                    gate_result_ref=gate_result_ref,
                    gate=gate_result,
                )
                review_check_status = self._check_status_for_review_verdict(
                    review_verdict.verdict
                )

                if review_verdict.verdict != "FULLY_IMPLEMENTED":
                    failed_review_summary = self._bounded_review_summary(
                        review_verdict.feedback,
                        fallback="Structured gate did not approve advancement",
                    )
                    self._upsert_step_check(
                        node_id,
                        kind="approval_policy",
                        status=review_check_status,
                        summary=failed_review_summary,
                        retry_count=review_retry_count + 1,
                        artifact_ref=gate_result_ref,
                        metadata=gate_check_metadata,
                    )
                    transition = self._resolve_gate_transition(
                        verdict=review_verdict,
                        ordered_nodes=ordered_nodes,
                        current_index=index - 1,
                    )
                    if (
                        plan_routed_moonspec_remediation_enabled
                        and transition.disposition != "generic"
                    ):
                        self._record_moonspec_gate_transition_event(
                            logical_step_id=node_id,
                            node=node,
                            verdict=review_verdict.verdict,
                            transition=transition,
                            review_retries_consumed=review_retry_count,
                        )
                    plan_routed_verifier = (
                        plan_routed_moonspec_remediation_enabled
                        and transition.disposition == "accept"
                    )
                    if plan_routed_verifier and self._step_has_accepted_output_evidence(
                        node_id, execution_result
                    ):
                        current_attempt, current_max = (
                            self._moonspec_remediation_attempt_metadata(node)
                        )
                        # The verifier owns the semantic verdict.  Accepting its
                        # evidence as a completed control operation must not
                        # rewrite terminal outcomes as remediation work.
                        accepted_gate_verdict = (
                            self._accepted_verifier_semantic_verdict(
                                review_verdict.verdict
                            )
                        )
                        accepted_gate_action = transition.routing_disposition
                        accepted_gate_disposition = "accepted"
                        next_logical_step_id = (
                            transition.successor.logical_step_id
                            if transition.successor is not None
                            else None
                        )
                        review_budget = self._review_gate_budget_metadata(
                            max_review_attempts=max_review_attempts,
                            review_retry_count=review_retry_count,
                            max_consecutive_no_progress_attempts=(
                                max_consecutive_no_progress_attempts
                            ),
                            consecutive_no_progress_attempts=(
                                consecutive_no_progress_attempts
                            ),
                            verdict=review_verdict.verdict,
                            recommended_next_action=(
                                review_verdict.recommended_next_action
                            ),
                        )
                        remediation_budget = (
                            self._moonspec_remediation_budget_metadata(
                                ordered_nodes=ordered_nodes,
                                current_attempt=current_attempt,
                                max_attempts=current_max,
                            )
                        )
                        accepted_gate_budget = {
                            "reviewGateBudget": review_budget,
                            "remediationBudget": remediation_budget,
                        }
                        self._upsert_step_check(
                            node_id,
                            kind="approval_policy",
                            status="passed",
                            summary=(
                                "Valid verifier result accepted; routing to "
                                + (
                                    f"remediation attempt {transition.successor.attempt}."
                                    if transition.successor is not None
                                    else "the workflow control gate."
                                )
                            ),
                            retry_count=review_retry_count,
                            artifact_ref=gate_result_ref,
                            metadata={
                                **gate_check_metadata,
                                "workflowTransition": accepted_gate_action,
                                "transitionReasonCode": transition.reason_code,
                                "targetLogicalStepId": next_logical_step_id,
                                "reviewEvidenceRetriesConsumed": review_retry_count,
                                "remediationAttemptsConsumed": remediation_budget[
                                    "attemptsStarted"
                                ],
                                "remediationAttemptsMaximum": current_max,
                                "reviewGateBudget": review_budget,
                                "remediationBudget": remediation_budget,
                            },
                        )
                        accepted_execution = True
                        break
                    if self._review_gate_verdict_made_progress(review_verdict):
                        consecutive_no_progress_attempts = 0
                    else:
                        consecutive_no_progress_attempts += 1
                    if self._gate_transition_allows_review_retry(
                        plan_routed_moonspec_remediation_enabled=(
                            plan_routed_moonspec_remediation_enabled
                        ),
                        transition=transition,
                    ) and self._review_gate_retry_allowed(
                        verdict=review_verdict,
                        review_retry_count=review_retry_count,
                        max_review_attempts=max_review_attempts,
                        consecutive_no_progress_attempts=(
                            consecutive_no_progress_attempts
                        ),
                        max_consecutive_no_progress_attempts=(
                            max_consecutive_no_progress_attempts
                        ),
                    ):
                        review_retry_count += 1
                        previous_review_feedback = (
                            review_verdict.feedback
                            or "Structured gate requested another bounded attempt."
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
                    await self._record_canonical_step_checkpoint(
                        node_id,
                        boundary="after_gate",
                        updated_at=workflow.now(),
                    )
                    await self._record_step_execution_manifest(
                        node_id,
                        phase="terminal",
                        updated_at=workflow.now(),
                        reason=attempt_reason,
                        status="blocked"
                        if review_verdict.verdict == "BLOCKED"
                        else "failed",
                        terminal_disposition=self._terminal_disposition_for_gate_stop(
                            review_verdict
                        ),
                        budget=self._review_gate_budget_metadata(
                            max_review_attempts=max_review_attempts,
                            review_retry_count=review_retry_count,
                            max_consecutive_no_progress_attempts=(
                                max_consecutive_no_progress_attempts
                            ),
                            consecutive_no_progress_attempts=(
                                consecutive_no_progress_attempts
                            ),
                            verdict=review_verdict.verdict,
                            recommended_next_action=(
                                review_verdict.recommended_next_action
                            ),
                        ),
                    )
                    gate_stop_requested = True
                    if failure_mode == "FAIL_FAST":
                        raise ValueError(
                            "plan node structured gate stopped with verdict "
                            f"{review_verdict.verdict}"
                        )
                    break

                if not self._step_has_accepted_output_evidence(
                    node_id,
                    execution_result,
                ):
                    missing_evidence_summary = (
                        "Structured gate passed without accepted output evidence"
                    )
                    self._upsert_step_check(
                        node_id,
                        kind="approval_policy",
                        status="failed",
                        summary=missing_evidence_summary,
                        retry_count=review_retry_count,
                        artifact_ref=gate_result_ref,
                        metadata={
                            **gate_check_metadata,
                            "recommendedNextAction": "needs_human",
                        },
                    )
                    self._mark_step_terminal(
                        node_id,
                        status="failed",
                        updated_at=workflow.now(),
                        summary=missing_evidence_summary,
                        last_error="missing_accepted_output_evidence",
                    )
                    await self._record_canonical_step_checkpoint(
                        node_id,
                        boundary="after_gate",
                        updated_at=workflow.now(),
                    )
                    await self._record_step_execution_manifest(
                        node_id,
                        phase="terminal",
                        updated_at=workflow.now(),
                        reason=attempt_reason,
                        status="failed",
                        terminal_disposition="needs_human",
                        budget=self._review_gate_budget_metadata(
                            max_review_attempts=max_review_attempts,
                            review_retry_count=review_retry_count,
                            max_consecutive_no_progress_attempts=(
                                max_consecutive_no_progress_attempts
                            ),
                            consecutive_no_progress_attempts=(
                                consecutive_no_progress_attempts
                            ),
                            verdict=review_verdict.verdict,
                            recommended_next_action="needs_human",
                        ),
                    )
                    gate_stop_requested = True
                    if failure_mode == "FAIL_FAST":
                        raise ValueError(missing_evidence_summary)
                    break

                passing_transition = self._resolve_gate_transition(
                    verdict=review_verdict,
                    ordered_nodes=ordered_nodes,
                    current_index=index - 1,
                )
                if (
                    plan_routed_moonspec_remediation_enabled
                    and passing_transition.disposition == "accept"
                    and passing_transition.routing_disposition
                    == "exit_remediation_loop"
                ):
                    accepted_gate_verdict = review_verdict.verdict
                    accepted_gate_action = passing_transition.routing_disposition
                    self._record_moonspec_gate_transition_event(
                        logical_step_id=node_id,
                        node=node,
                        verdict=review_verdict.verdict,
                        transition=passing_transition,
                        review_retries_consumed=review_retry_count,
                    )
                self._upsert_step_check(
                    node_id,
                    kind="approval_policy",
                    status=review_check_status,
                    summary=self._accepted_review_summary(
                        review_verdict.verdict,
                        retry_count=review_retry_count,
                    ),
                    retry_count=review_retry_count,
                    artifact_ref=gate_result_ref,
                    metadata={
                        **gate_check_metadata,
                        "recommendedNextAction": (
                            review_verdict.recommended_next_action or "advance"
                        ),
                    },
                )
                await self._record_canonical_step_checkpoint(
                    node_id,
                    boundary="after_gate",
                    updated_at=workflow.now(),
                )
                accepted_execution = True
                break

            if not accepted_execution:
                if gate_stop_requested:
                    self._plan_blocked_message = (
                        self._plan_blocked_message
                        or "Structured gate stopped before downstream handoff."
                    )
                    self._publish_status = "not_required"
                    self._publish_reason = self._plan_blocked_message
                    self._refresh_step_readiness(updated_at=workflow.now())
                    continue
                if (
                    result_status != "COMPLETED"
                    and (
                        publish_mode in {"pr", "branch"}
                        or workflow.patched(RUN_FAILED_RESULT_BLOCKER_PATCH)
                    )
                ):
                    # Failed managed runs may still carry independently verified
                    # terminal-checkpoint publication evidence. Project it before
                    # the failure short-circuit so operators can recover the work.
                    self._record_publish_result(
                        parameters=parameters,
                        execution_result=execution_result,
                    )
                    self._plan_blocked_message = (
                        step_failure_summary
                        or self._activity_result_provider_failure_summary(
                            execution_result
                        )
                        or self._activity_result_failure_message(execution_result)
                        or f"{tool_name} failed"
                    )
                    self._publish_status = "not_required"
                    self._publish_reason = self._plan_blocked_message
                    require_pull_request_url = False
                    pull_request_url = None
                    self._summary = self._plan_blocked_message
                    self._refresh_step_readiness(updated_at=workflow.now())
                    self._update_memo()
                    continue
                continue

            self._mark_step_terminal(
                node_id,
                status="completed",
                updated_at=workflow.now(),
                summary=self._get_from_result(execution_result, "summary")
                or self._summary,
                last_error=None,
            )
            outputs = self._effective_result_outputs(execution_result)
            if isinstance(outputs, Mapping):
                self._record_declared_side_effect(
                    logical_step_id=node_id,
                    outputs=outputs,
                )
            self._record_downstream_dependency_effects(
                node_id,
                updated_at=workflow.now(),
            )
            await self._record_step_execution_manifest(
                node_id,
                phase="terminal",
                updated_at=workflow.now(),
                reason=attempt_reason,
                status="completed",
                terminal_disposition=accepted_gate_disposition,
                budget=accepted_gate_budget
                or self._review_gate_budget_metadata(
                    max_review_attempts=max_review_attempts,
                    review_retry_count=review_retry_count,
                    max_consecutive_no_progress_attempts=(
                        max_consecutive_no_progress_attempts
                    ),
                    consecutive_no_progress_attempts=(
                        consecutive_no_progress_attempts
                    ),
                    verdict=accepted_gate_verdict,
                    recommended_next_action=accepted_gate_action,
                )
                if review_gate_active
                else None,
            )
            self._record_step_checkpoint_evidence(
                node_id,
                updated_at=workflow.now(),
                state_checkpoint_ref=self._step_checkpoint_refs.get(node_id),
            )
            self._refresh_step_readiness(updated_at=workflow.now())
            self._record_execution_context(
                node_id=node_id,
                execution_result=execution_result,
            )
            if (
                self._gated_continuation_request
                and workflow.patched(RUN_GATED_STEP_CONTINUATION_PATCH)
            ):
                continuation_blocked_message = (
                    self._gated_continuation_failure_message(parameters)
                )
                if continuation_blocked_message:
                    self._plan_blocked_message = continuation_blocked_message
                    self._publish_status = "not_required"
                    self._publish_reason = continuation_blocked_message
                    require_pull_request_url = False
                    pull_request_url = None
                    self._summary = continuation_blocked_message
                    self._mark_remaining_plan_steps_skipped(
                        ordered_nodes=ordered_nodes,
                        completed_index=index - 1,
                        summary=continuation_blocked_message,
                    )
                    self._refresh_step_readiness(updated_at=workflow.now())
                    self._update_memo()
                    break
            blocked_message = (
                None
                if blocked_outcome_wait_skipped
                else self._blocked_outcome_message(execution_result)
            )
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
            publish_status_before = self._publish_status
            prepublication_checkpoint_failed = (
                await self._record_prepublication_checkpoint(
                    node_id,
                    publish_mode=publish_mode,
                    updated_at=workflow.now(),
                )
            )
            if prepublication_checkpoint_failed:
                break
            publication_raised = False
            try:
                await self._record_publish_result_from_execution(
                    parameters=parameters,
                    execution_result=execution_result,
                )
            except Exception as exc:
                if not workflow.patched(RUN_DURABLE_FINALIZATION_OUTCOME_PATCH):
                    raise
                self._record_publication_finalization_failure(
                    node_id,
                    exc=exc,
                    updated_at=workflow.now(),
                )
                publication_raised = True
            if (
                not publication_raised
                and self._publish_status == "failed"
                and publish_status_before != "failed"
                and workflow.patched(RUN_DURABLE_FINALIZATION_OUTCOME_PATCH)
            ):
                self._record_publication_finalization_failure(
                    node_id,
                    exc=RuntimeError(self._publish_reason or "Publish failed"),
                    updated_at=workflow.now(),
                )
            if workflow.patched(RUN_MOONSPEC_VERIFY_PUBLICATION_GATE_PATCH):
                outputs_for_gate = self._get_from_result(execution_result, "outputs")
                if (
                    isinstance(outputs_for_gate, Mapping)
                    and self._is_moonspec_verify_step(
                        tool_name=tool_name,
                        node_inputs=node_inputs,
                    )
                ):
                    step_gate_result = self._moonspec_verify_gate_result(
                        outputs_for_gate
                    )
                    step_gate_result_ref = self._moonspec_verify_gate_result_ref(
                        outputs_for_gate
                    )
                    self._record_moonspec_verify_gate(
                        node_id=node_id,
                        outputs=outputs_for_gate,
                    )
                    gate_verdict = self._normalize_moonspec_verify_verdict(
                        self._moonspec_gate_verdict
                    )
                    blocking_gate_reason = self._blocking_moonspec_gate_reason()
                    remaining_remediation_index = (
                        index - 1
                        if workflow.patched(
                            RUN_MOONSPEC_VERIFY_REMEDIATION_INDEX_PATCH
                        )
                        else index
                    )
                    continuation_decision = (
                        self._bounded_story_loop_continuation_decision(
                            logical_step_id=node_id,
                            gate_result=step_gate_result,
                            gate_result_ref=step_gate_result_ref,
                            ordered_nodes=ordered_nodes,
                            current_index=remaining_remediation_index,
                            plan_routed_moonspec_remediation_enabled=(
                                plan_routed_moonspec_remediation_enabled
                            ),
                        )
                    )
                    if blocking_gate_reason and not bool(
                        continuation_decision.get("continueLoop")
                    ):
                        transition_reason = "terminal_gate_verdict"
                        normalized_gate = str(gate_verdict or "").upper()
                        budget_payload = continuation_decision.get("budget")
                        consumed_payload = (
                            budget_payload.get("consumed")
                            if isinstance(budget_payload, Mapping)
                            else None
                        )
                        if (
                            isinstance(consumed_payload, Mapping)
                            and int(
                                consumed_payload.get(
                                    "consecutiveNoProgressAttempts", 0
                                )
                                or 0
                            )
                            >= int(
                                budget_payload.get(
                                    "maxConsecutiveNoProgressAttempts", 1
                                )
                                or 1
                            )
                        ):
                            transition_reason = "no_progress_attempts_exhausted"
                        elif (
                            normalized_gate == "ADDITIONAL_WORK_NEEDED"
                            and plan_routed_moonspec_remediation_enabled
                        ):
                            _, transition_reason = (
                                self._resolve_next_moonspec_remediation_step(
                                    ordered_nodes=ordered_nodes,
                                    current_index=index - 1,
                                )
                            )
                        elif normalized_gate == "NO_DETERMINATION":
                            transition_reason = "unrecoverable_no_determination"
                        remediation_budget = continuation_decision.get(
                            "remediationBudget"
                        )
                        review_budget = continuation_decision.get("reviewGateBudget")
                        self._workflow_control_stop = {
                            "kind": "workflow_gate",
                            "reasonCode": transition_reason,
                            "logicalStepId": node_id,
                            "verdict": normalized_gate,
                            "gateResultRef": step_gate_result_ref,
                            "remainingWorkRef": step_gate_result.remaining_work_ref,
                            "reviewGateBudget": (
                                dict(review_budget)
                                if isinstance(review_budget, Mapping)
                                else None
                            ),
                            "remediationBudget": (
                                dict(remediation_budget)
                                if isinstance(remediation_budget, Mapping)
                                else None
                            ),
                        }
                        control_stop_summary = {
                            "remediation_budget_exhausted": (
                                "Skipped because remediation budget was exhausted "
                                "after verification attempt "
                                f"{(self._workflow_control_stop.get('remediationBudget') or {}).get('currentAttempt')}."
                            ),
                            "no_remediation_successor": (
                                "Skipped because no explicit remediation successor exists."
                            ),
                            "no_progress_attempts_exhausted": (
                                "Skipped because consecutive remediation cycles "
                                "produced no new progress."
                            ),
                            "terminal_gate_verdict": (
                                "Skipped because verification returned "
                                f"{normalized_gate}."
                            ),
                            "unrecoverable_no_determination": (
                                "Skipped because verification could not obtain "
                                "recoverable evidence."
                            ),
                        }.get(transition_reason, blocking_gate_reason)
                        environment_draft_publish_enabled = workflow.patched(
                            RUN_MOONSPEC_GATE_ENVIRONMENT_DRAFT_PUBLISH_PATCH
                        )
                        additional_work_draft_publish_enabled = workflow.patched(
                            RUN_MOONSPEC_ADDITIONAL_WORK_DRAFT_PUBLISH_PATCH
                        )
                        draft_publication_policy = (
                            self._moonspec_draft_publication_policy(
                                environment_blocked_enabled=(
                                    environment_draft_publish_enabled
                                ),
                                additional_work_enabled=(
                                    additional_work_draft_publish_enabled
                                ),
                            )
                        )
                        if (
                            publish_mode == "pr"
                            and draft_publication_policy is not None
                        ):
                            draft_summary = (
                                self._activate_moonspec_draft_publication(
                                    blocking_gate_reason,
                                    policy=draft_publication_policy,
                                )
                            )
                            self._summary = draft_summary
                            # Draft publication is an explicit recovery handoff.
                            # The remaining plan is skipped below, so preserve
                            # already-pushed changes for the post-loop native PR
                            # boundary even when an earlier auxiliary tool result
                            # left a stale not_required/no-commit projection.
                            if workflow.patched(
                                RUN_MOONSPEC_DRAFT_PUBLISH_RECOVERY_HANDOFF_PATCH
                            ) and self._execution_result_has_publishable_changes(
                                execution_result
                            ):
                                require_pull_request_url = True
                                if self._publish_status in {
                                    "not_required",
                                    "skipped",
                                }:
                                    self._publish_status = None
                                    self._publish_reason = None
                                    self._publish_context.pop(
                                        "noCommitPublish", None
                                    )
                                    self._publish_context.pop(
                                        "noChangePublish", None
                                    )
                            self._mark_remaining_plan_steps_skipped(
                                ordered_nodes=ordered_nodes,
                                completed_index=index - 1,
                                summary=draft_summary,
                            )
                            self._refresh_step_readiness(
                                updated_at=workflow.now()
                            )
                            self._update_memo()
                            break
                        self._plan_blocked_message = control_stop_summary
                        self._publish_status = "not_required"
                        self._publish_reason = control_stop_summary
                        self._publish_context["publicationBlockedBy"] = (
                            "moonspec_verify"
                        )
                        self._summary = control_stop_summary
                        self._mark_remaining_plan_steps_skipped(
                            ordered_nodes=ordered_nodes,
                            completed_index=index - 1,
                            summary=control_stop_summary,
                        )
                        self._refresh_step_readiness(updated_at=workflow.now())
                        self._update_memo()
                        break
            if self._publish_status == "not_required":
                require_pull_request_url = False
                pull_request_url = None
            if (
                self._publish_status == "failed"
                and publish_status_before != "failed"
                and workflow.patched(RUN_STOP_ON_PUBLISH_HANDOFF_FAILURE_PATCH)
            ):
                publish_failure_summary = self._publish_reason or "Publish failed"
                self._summary = publish_failure_summary
                self._mark_step_terminal(
                    node_id,
                    status="failed",
                    updated_at=workflow.now(),
                    summary=publish_failure_summary,
                    last_error="publish_failed",
                )
                self._mark_remaining_plan_steps_skipped(
                    ordered_nodes=ordered_nodes,
                    completed_index=index - 1,
                    summary=publish_failure_summary,
                )
                self._refresh_step_readiness(updated_at=workflow.now())
                self._update_memo()
                break
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
                self._record_assessment_context(outputs_for_story_output)
                self._record_trusted_issue_context(outputs_for_story_output)
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
                            f"{story_output_mode.title()} issue output succeeded; "
                            "no PR output required"
                        )
                        self._publish_context["storyOutputMode"] = story_output_mode
            if require_pull_request_url and pull_request_url is None:
                pull_request_url = self._extract_pull_request_url(execution_result)

                # If still not found, check the diagnostics artifact if present
                if pull_request_url is None:
                    outputs = self._get_from_result(execution_result, "outputs") or {}
                    diag_ref = outputs.get("diagnostics_ref") or outputs.get(
                        "diagnosticsRef"
                    )
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

        if workflow.patched(RUN_AUTHORITATIVE_PR_REQUIREMENT_PATCH):
            require_pull_request_url = self._authoritative_pr_requirement(
                publish_mode=publish_mode,
                pr_publish_optional=pr_publish_optional,
            )
            pull_request_url = pull_request_url or self._coerce_text(
                self._publish_context.get("pullRequestUrl"),
                max_chars=500,
            )

        if (
            workflow.patched(RUN_PREPUBLICATION_FAILURE_BLOCKS_PUBLISH_PATCH)
            and self._publish_status == "failed"
        ):
            require_pull_request_url = False
            pull_request_url = None

        if (
            workflow.patched(RUN_MOONSPEC_VERIFY_PUBLICATION_GATE_PATCH)
            and self._apply_blocking_moonspec_gate_to_publish()
        ):
            require_pull_request_url = False
            pull_request_url = None

        if require_pull_request_url and pull_request_url is None:
            repair_candidate_outputs: Mapping[str, Any] = {}
            if execution_result is not None:
                maybe_outputs = self._get_from_result(execution_result, "outputs")
                if isinstance(maybe_outputs, Mapping):
                    repair_candidate_outputs = maybe_outputs
            if (
                str(repair_candidate_outputs.get("push_status") or "").strip()
                == "no_commits"
            ):
                repair_failure_message = self._publish_reason or (
                    self._compose_no_commit_publish_reason(publish_mode="pr")
                )
                repair_result = await self._execution_publish_repair(
                    parameters=parameters,
                    failure_message=repair_failure_message,
                )
                if repair_result is not None:
                    execution_result = repair_result
                    self._record_execution_context(
                        node_id="publish-repair",
                        execution_result=execution_result,
                    )
                    await self._record_publish_result_from_execution(
                        parameters=parameters,
                        execution_result=execution_result,
                    )
                    pull_request_url = self._extract_pull_request_url(
                        execution_result
                    )

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
            elif (
                workflow.patched(RUN_MOONSPEC_VERIFY_PUBLICATION_GATE_PATCH)
                and self._apply_blocking_moonspec_gate_to_publish()
            ):
                self._get_logger().info(
                    "Skipping native PR creation: latest MoonSpec verification gate "
                    "did not approve publication."
                )
            else:
                agent_outputs = {}
                if execution_result is not None:
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
                    task_payload = self._mapping_value(parameters, "workflow")
                    if not task_payload:
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
                if self._publish_status == "not_required":
                    self._get_logger().info(
                        "Skipping native PR creation: publish output not required."
                    )
                elif (
                    push_status == "no_commits"
                    and self._moonspec_draft_publication_reason is None
                ):
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
                elif not head_branch:
                    raise ValueError(
                        "publishMode 'pr' requested but no PR head branch could be resolved from provider outputs, workspace metadata, or runtime planner generation"
                    )
                elif not self._repo:
                    raise ValueError(
                        "publishMode 'pr' requested but no repository was available to create the pull request natively"
                    )
                else:
                    self._get_logger().info(
                        f"Creating PR natively from {head_branch} into {base_branch} for repo {self._repo}"
                    )
                    self._publish_context["branch"] = head_branch
                    self._publish_context["baseRef"] = base_branch
                    if self._moonspec_draft_publication_reason is not None:
                        pr_body = (
                            pr_body.rstrip()
                            + "\n\n"
                            + self._moonspec_draft_publication_body_section()
                        )
                    create_payload = {
                        "repo": self._repo,
                        "head": head_branch,
                        "base": base_branch,
                        "title": pr_title,
                        "body": pr_body,
                    }
                    if self._moonspec_draft_publication_reason is not None:
                        create_payload["draft"] = True
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
                        adopted = self._get_from_result(create_result, "adopted")
                        summary = self._get_from_result(create_result, "summary") or ""
                        created_head_sha = self._coerce_text(
                            self._get_from_result(create_result, "headSha"),
                            max_chars=80,
                        )
                        if created_head_sha:
                            self._publish_context["headSha"] = created_head_sha
                        if (
                            self._moonspec_draft_publication_reason is not None
                            and not created
                            and not adopted
                        ):
                            raise ValueError(
                                "draft PR publication was rejected: "
                                f"{summary or 'existing pull request is not a draft'}"
                            )
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
            workflow.patched(RUN_DURABLE_PUBLISH_CONTEXT_MERGE_HANDOFF_PATCH)
            and not self._publish_context.get("publicationBlockedBy")
        ):
            pull_request_url = pull_request_url or self._coerce_text(
                self._publish_context.get("pullRequestUrl"),
                max_chars=500,
            )
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
            if self._merge_automation_requested(parameters):
                self._publish_context["mergeAutomationStatus"] = "not_applicable"
        # Persist the PR URL so the workflow output can determine if a PR was created.
        self._pull_request_url = pull_request_url
        publish_mode = self._publish_mode(parameters)
        if publish_mode == "pr" and pull_request_url:
            self._publish_status = "published"
            self._publish_reason = "published pull request"
            self._publish_context["pullRequestUrl"] = pull_request_url
        await self._complete_already_implemented_jira_if_needed(
            parameters=parameters,
        )
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

    def _effective_result_outputs(self, result: Any) -> Mapping[str, Any] | None:
        outputs = self._get_from_result(result, "outputs")
        if isinstance(outputs, Mapping):
            return outputs
        if not workflow.patched(RUN_DIRECT_TOOL_REPORT_OUTPUTS_PATCH):
            return None
        if isinstance(result, Mapping) and not _DIRECT_EXECUTABLE_OUTPUT_KEYS.isdisjoint(
            result
        ):
            return result
        return None

    def _effective_result_metadata(self, result: Any) -> Mapping[str, Any] | None:
        metadata = self._get_from_result(result, "metadata")
        return metadata if isinstance(metadata, Mapping) else None

    def _auto_publish_evidence_sources(self, result: Any) -> list[Mapping[str, Any]]:
        sources: list[Mapping[str, Any]] = []
        if (
            isinstance(result, Mapping)
            and workflow.patched(RUN_PR_RESOLVER_PUBLISH_EVIDENCE_REF_PATCH)
        ):
            sources.append(result)
        outputs = self._effective_result_outputs(result)
        metadata = (
            self._effective_result_metadata(result)
            if workflow.patched(RUN_AUTO_PUBLISH_METADATA_EVIDENCE_PATCH)
            else None
        )
        for source in (outputs, metadata):
            if isinstance(source, Mapping) and source not in sources:
                sources.append(source)
        return sources

    @staticmethod
    def _inline_auto_publish_evidence(value: Any) -> Any:
        if isinstance(value, Mapping) or isinstance(value, bytes):
            return value
        if isinstance(value, str) and value.lstrip().startswith("{"):
            return value
        return None

    @staticmethod
    def _auto_publish_artifact_ref(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if candidate.startswith("art_") or candidate.startswith("artifact:"):
            return candidate
        return None

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

    def _activity_result_operator_summary(self, result: Any) -> str | None:
        outputs = self._get_from_result(result, "outputs")
        if isinstance(outputs, Mapping):
            summary = outputs.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
        summary = self._get_from_result(result, "summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        return None

    def _activity_result_retryable(
        self,
        result: Any,
        *,
        failure_message: str,
        tool_type: str,
    ) -> bool:
        if failure_message == "system_error":
            return True
        if failure_message != "execution_error" or tool_type != "agent_runtime":
            return False

        outputs = self._get_from_result(result, "outputs")
        if not isinstance(outputs, Mapping):
            return True

        provider_failure = outputs.get("providerFailure")
        if not isinstance(provider_failure, Mapping):
            provider_failure = {}
        turn_metadata = outputs.get("turnMetadata")
        if not isinstance(turn_metadata, Mapping):
            turn_metadata = {}

        def _first_text(*values: Any) -> str | None:
            for value in values:
                if isinstance(value, str) and value.strip():
                    return value.strip().lower()
            return None

        provider_error_code = _first_text(
            outputs.get("providerErrorCode"),
            outputs.get("provider_error_code"),
            provider_failure.get("providerErrorCode"),
            provider_failure.get("provider_error_code"),
        )
        provider_error_class = _first_text(
            provider_failure.get("providerErrorClass"),
            provider_failure.get("provider_error_class"),
        )
        retry_recommendation = _first_text(
            outputs.get("retryRecommendation"),
            outputs.get("retry_recommendation"),
            provider_failure.get("retryRecommendation"),
            provider_failure.get("retry_recommendation"),
        )
        failure_class = _first_text(
            outputs.get("failureClass"),
            outputs.get("failure_class"),
            provider_failure.get("failureClass"),
            provider_failure.get("failure_class"),
        )
        turn_failure_class = _first_text(
            turn_metadata.get("failureClass"),
            turn_metadata.get("failure_class"),
        )

        if provider_error_code in {"401", "403"}:
            return False
        if provider_error_class in {"auth", "credential_scope"}:
            return False
        if retry_recommendation in {"reauthenticate", "expand_credential_scope"}:
            return False
        if failure_class in {"user_error", "permanent"}:
            return False
        if turn_failure_class == "permanent":
            return False
        return True

    def _activity_result_provider_failure_summary(self, result: Any) -> str | None:
        outputs = self._get_from_result(result, "outputs")
        if not isinstance(outputs, Mapping):
            return None

        provider_failure = outputs.get("providerFailure")
        if not isinstance(provider_failure, Mapping):
            provider_failure = {}

        def _first_text(*values: Any) -> str | None:
            for value in values:
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        provider_error_code = _first_text(
            outputs.get("providerErrorCode"),
            provider_failure.get("providerErrorCode"),
            outputs.get("provider_error_code"),
            provider_failure.get("provider_error_code"),
        )
        retry_recommendation = _first_text(
            outputs.get("retryRecommendation"),
            provider_failure.get("retryRecommendation"),
            outputs.get("retry_recommendation"),
            provider_failure.get("retry_recommendation"),
        )
        reason = _first_text(
            # MM-882: canonical sanitized summary; ``reason`` is read only as an
            # in-flight fallback for run histories that predate the envelope.
            provider_failure.get("sanitizedSummary"),
            provider_failure.get("reason"),
            outputs.get("summary"),
            self._get_from_result(result, "summary"),
        )
        profile_id = _first_text(
            outputs.get("profileId"),
            outputs.get("profile_id"),
            provider_failure.get("profileId"),
            provider_failure.get("profile_id"),
        )
        failure_class = _first_text(
            outputs.get("failureClass"),
            outputs.get("failure_class"),
            provider_failure.get("failureClass"),
            provider_failure.get("failure_class"),
            outputs.get("error"),
        )
        diagnostics_ref = _first_text(
            outputs.get("diagnosticsRef"),
            outputs.get("diagnostics_ref"),
            provider_failure.get("diagnosticsRef"),
            provider_failure.get("diagnostics_ref"),
        )
        turn_metadata = outputs.get("turnMetadata")
        turn_failure_cause: str | None = None
        retry_recommended_action: str | None = None
        if isinstance(turn_metadata, Mapping):
            turn_failure_cause = _first_text(
                turn_metadata.get("failureCause"),
                turn_metadata.get("failure_cause"),
            )
            retry_recommended_action = _first_text(
                turn_metadata.get("retryRecommendedAction"),
                turn_metadata.get("retry_recommended_action"),
            )

        if not provider_error_code and not retry_recommendation:
            has_agent_failure_evidence = bool(
                diagnostics_ref
                or turn_failure_cause
                or retry_recommended_action
                or outputs.get("turnStatus") == "failed"
            )
            if not has_agent_failure_evidence or not reason:
                return None

            summary = "Agent runtime failed"
            if failure_class:
                summary = f"{summary} with {failure_class}"
            if profile_id:
                summary = f"{summary} for profile {profile_id}"
            if turn_failure_cause:
                summary = f"{summary} due to {turn_failure_cause}"
            summary = f"{summary}: {reason}"
            details = []
            if retry_recommended_action:
                details.append(f"retryRecommendedAction: {retry_recommended_action}")
            if diagnostics_ref:
                details.append(f"diagnosticsRef: {diagnostics_ref}")
            if details:
                summary = f"{summary} ({'; '.join(details)})"
            return summary

        normalized_code = (provider_error_code or "").strip().lower()
        normalized_retry = (retry_recommendation or "").strip().lower()
        if normalized_code == "401" or normalized_retry == "reauthenticate":
            summary = "Provider authentication failed"
        else:
            summary = "Provider request failed"

        if provider_error_code:
            if provider_error_code.isdigit():
                summary = f"{summary} with HTTP {provider_error_code}"
            else:
                summary = f"{summary} with provider error {provider_error_code}"
        if profile_id:
            summary = f"{summary} for profile {profile_id}"
        if reason:
            summary = f"{summary}: {reason}"
        if retry_recommendation:
            summary = f"{summary} (retryRecommendation: {retry_recommendation})"
        return summary

    def _capture_incident_failure_evidence(self, outputs: Any) -> None:
        """Capture sanitized provider failure + observed cost for incident review.

        First failure wins, mirroring ``_failure_diagnostic``. Only the canonical
        structured provider failure fields (MM-882) are retained -- the legacy
        raw ``reason`` text is deliberately dropped so no raw provider payload is
        carried into the incident manifest. Observed token/cost is captured only
        ``where available`` from the agent result.
        """

        if not workflow.patched(RUN_INCIDENT_RECONSTRUCTION_PATCH):
            return
        if not isinstance(outputs, Mapping):
            return

        if self._provider_failure_envelope is None:
            envelope = outputs.get("providerFailure")
            if isinstance(envelope, Mapping):
                # Retain only sanitized / structured fields; never the raw
                # provider ``reason`` text.
                sanitized_keys = (
                    "providerErrorClass",
                    "providerErrorCode",
                    "retryRecommendation",
                    "retryAfterSeconds",
                    "resetAt",
                    "quotaScope",
                    "credentialScope",
                    "providerRequestId",
                    "rawErrorRef",
                    "sanitizedSummary",
                )
                sanitized = {
                    key: envelope[key]
                    for key in sanitized_keys
                    if envelope.get(key) is not None
                }
                if sanitized:
                    self._provider_failure_envelope = sanitized

        if self._observed_cost is None:
            observed = self._extract_observed_cost(outputs)
            if observed:
                self._observed_cost = observed

    @staticmethod
    def _extract_observed_cost(outputs: Mapping[str, Any]) -> dict[str, Any]:
        """Extract observed token/cost values from agent result outputs.

        Searches the result outputs and any ``turnMetadata`` for the canonical
        token/cost key names (mirrors ``ModelCostEstimate.to_metadata``). Returns
        an empty mapping when the runtime did not report usage -- observed cost is
        only ever populated ``where available``.
        """

        sources: list[Mapping[str, Any]] = [outputs]
        turn_metadata = outputs.get("turnMetadata")
        if isinstance(turn_metadata, Mapping):
            sources.append(turn_metadata)
        usage = outputs.get("usage")
        if isinstance(usage, Mapping):
            sources.append(usage)

        observed: dict[str, Any] = {}
        token_keys = (
            "inputTokens",
            "input_tokens",
            "promptTokens",
            "prompt_tokens",
            "outputTokens",
            "output_tokens",
            "completionTokens",
            "completion_tokens",
            "totalTokens",
            "total_tokens",
        )
        cost_keys = (
            "costEstimateUsd",
            "cost_estimate_usd",
            "estimatedCostUsd",
            "estimated_cost_usd",
            "costUsd",
            "cost_usd",
            "totalCostUsd",
            "total_cost_usd",
        )
        pricing_keys = ("pricingSource", "pricing_source")
        for source in sources:
            for key in (*token_keys, *cost_keys, *pricing_keys):
                if key in observed:
                    continue
                value = source.get(key)
                if value is not None:
                    observed[key] = value
        return observed

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
            match = _PLAIN_TEXT_BLOCKED_OUTCOME_PATTERN.search(value)
            if match:
                summary = self._coerce_text(value[match.start():], max_chars=700)
                if summary:
                    return f"Workflow blocked by plan step: {summary}"

        return None

    def _jira_blocker_waitable_result(
        self,
        execution_result: Any,
        *,
        tool_type: str,
        tool_name: str,
        selected_skill: Any = None,
    ) -> bool:
        normalized_tool_type = str(tool_type or "").strip().lower()
        normalized_tool_name = str(tool_name or "").strip()
        normalized_selected_skill = str(selected_skill or "").strip()
        if normalized_tool_type == "skill":
            is_check_blockers = normalized_tool_name == JIRA_CHECK_BLOCKERS_TOOL_NAME
        elif normalized_tool_type == "agent_runtime":
            is_check_blockers = (
                normalized_selected_skill == JIRA_CHECK_BLOCKERS_TOOL_NAME
                or normalized_tool_name == JIRA_CHECK_BLOCKERS_TOOL_NAME
            )
        else:
            is_check_blockers = False
        if not is_check_blockers:
            return False
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return False
        return self._is_blocked_outcome_value(outputs.get("decision"))

    def _jira_blocker_issue_keys(self, execution_result: Any) -> list[str]:
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return []
        issue_keys: list[str] = []
        blockers = outputs.get("blockingIssues")
        if blockers is None:
            blockers = outputs.get("blocking_issues")
        if isinstance(blockers, Sequence) and not isinstance(blockers, (str, bytes)):
            for blocker in blockers:
                if not isinstance(blocker, Mapping):
                    continue
                issue_key = self._coerce_text(
                    blocker.get("issueKey") or blocker.get("issue_key"),
                    max_chars=80,
                )
                if issue_key and issue_key not in issue_keys:
                    issue_keys.append(issue_key)
        if issue_keys:
            return issue_keys
        target_issue_key = self._coerce_text(
            outputs.get("targetIssueKey")
            or outputs.get("target_issue_key")
            or outputs.get("issueKey")
            or outputs.get("jiraIssueKey"),
            max_chars=80,
        )
        return [target_issue_key] if target_issue_key else []

    def _jira_blocker_wait_signature(self, execution_result: Any) -> str:
        outputs = self._get_from_result(execution_result, "outputs")
        if not isinstance(outputs, Mapping):
            return ""
        blockers = outputs.get("blockingIssues")
        if blockers is None:
            blockers = outputs.get("blocking_issues")
        compact_blockers: list[dict[str, Any]] = []
        if isinstance(blockers, Sequence) and not isinstance(blockers, (str, bytes)):
            for blocker in blockers:
                if not isinstance(blocker, Mapping):
                    continue
                compact_blockers.append(
                    {
                        "issueKey": self._coerce_text(
                            blocker.get("issueKey") or blocker.get("issue_key"),
                            max_chars=80,
                        ),
                        "status": self._coerce_text(
                            blocker.get("status"),
                            max_chars=80,
                        ),
                        "statusKnown": bool(
                            blocker.get("statusKnown")
                            if "statusKnown" in blocker
                            else blocker.get("status_known")
                        ),
                        "done": bool(blocker.get("done")),
                    }
                )
        return json.dumps(
            {
                "decision": self._coerce_text(outputs.get("decision"), max_chars=40),
                "targetIssueKey": self._coerce_text(
                    outputs.get("targetIssueKey")
                    or outputs.get("target_issue_key")
                    or outputs.get("issueKey")
                    or outputs.get("jiraIssueKey"),
                    max_chars=80,
                ),
                "blockingIssues": sorted(
                    compact_blockers,
                    key=lambda item: str(item.get("issueKey") or ""),
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def _compact_jira_blocker_recheck_payload(
        self,
        execute_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        invocation_payload = execute_payload.get("invocation_payload")
        if not isinstance(invocation_payload, Mapping):
            return dict(execute_payload)
        original_inputs = invocation_payload.get("inputs")
        if not isinstance(original_inputs, Mapping):
            return dict(execute_payload)

        def first_text(*values: Any) -> str | None:
            for value in values:
                text = self._coerce_text(value, max_chars=120)
                if text:
                    return text
            return None

        nested = original_inputs.get("blockerPreflight")
        if not isinstance(nested, Mapping):
            nested = original_inputs.get("blocker_preflight")
        if not isinstance(nested, Mapping):
            nested = original_inputs.get("jira")
        if not isinstance(nested, Mapping):
            nested = {}

        target_issue_key = first_text(
            original_inputs.get("targetIssueKey"),
            original_inputs.get("target_issue_key"),
            original_inputs.get("issueKey"),
            original_inputs.get("issue_key"),
            original_inputs.get("jiraIssueKey"),
            original_inputs.get("jira_issue_key"),
            nested.get("targetIssueKey"),
            nested.get("target_issue_key"),
            nested.get("issueKey"),
            nested.get("issue_key"),
        )
        if not target_issue_key:
            return dict(execute_payload)

        link_type = first_text(
            original_inputs.get("linkType"),
            original_inputs.get("link_type"),
            nested.get("linkType"),
            nested.get("link_type"),
        )
        compact_inputs: dict[str, Any] = {"targetIssueKey": target_issue_key}
        blocker_preflight: dict[str, Any] = {"targetIssueKey": target_issue_key}
        if link_type:
            blocker_preflight["linkType"] = link_type
            compact_inputs["linkType"] = link_type
        compact_inputs["blockerPreflight"] = blocker_preflight

        compact_invocation = {
            key: value
            for key, value in invocation_payload.items()
            if key not in {"inputs", "previousOutputs", "previous_outputs"}
        }
        compact_invocation["inputs"] = compact_inputs

        compact_payload = dict(execute_payload)
        compact_payload["invocation_payload"] = compact_invocation
        return compact_payload

    def _enter_jira_blocker_wait(
        self,
        *,
        node_id: str,
        execution_result: Any,
    ) -> None:
        summary = self._blocked_outcome_message(execution_result) or (
            "Waiting for Jira blocker resolution."
        )
        issue_keys = self._jira_blocker_issue_keys(execution_result)
        if self._jira_blocker_wait_started_at is None:
            self._jira_blocker_wait_started_at = workflow.now()
        signature = self._jira_blocker_wait_signature(execution_result)
        coalesce_wait = workflow.patched(RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH)
        should_publish = (
            not self._jira_blocker_wait_active
            or signature != self._jira_blocker_wait_published_signature
        )
        if not self._jira_blocker_wait_active:
            self._jira_blocker_wait_skipped = False
        self._jira_blocker_wait_active = True
        self._jira_blocker_wait_issue_keys = issue_keys
        self._jira_blocker_wait_summary = summary
        self._waiting_reason = "jira_blocker_wait"
        self._attention_required = False
        if coalesce_wait and not should_publish:
            return
        if coalesce_wait:
            self._jira_blocker_wait_published_signature = signature
        self._set_state(STATE_WAITING_ON_DEPENDENCIES, summary=summary)
        self._mark_step_waiting(
            node_id,
            status="awaiting_external",
            updated_at=workflow.now(),
            waiting_reason="Waiting for Jira blocker resolution",
            summary=summary,
        )
        self._update_search_attributes()
        self._update_memo()

    def _clear_jira_blocker_wait(self) -> None:
        self._jira_blocker_wait_active = False
        self._jira_blocker_wait_started_at = None
        self._jira_blocker_wait_issue_keys = []
        self._jira_blocker_wait_summary = None
        self._jira_blocker_wait_published_signature = None
        if self._waiting_reason == "jira_blocker_wait":
            self._waiting_reason = None
        self._attention_required = False

    async def _reexecute_jira_blocker_agent_runtime(
        self,
        *,
        node: Mapping[str, Any],
        node_inputs: Mapping[str, Any],
        node_id: str,
        tool_name: str,
        task_skills: Any,
        workflow_parameters: Mapping[str, Any],
        recheck_count: int,
        failure_mode: str,
    ) -> Any:
        try:
            resolved_skillset_ref = await self._resolve_agent_node_skillset_ref(
                task_skills=task_skills,
                node_skills=node.get("skills"),
                node_inputs=node_inputs,
                node_id=node_id,
                existing_skillset_ref=self._existing_agent_skillset_ref(
                    parameters=workflow_parameters,
                    node=node,
                    node_inputs=node_inputs,
                ),
            )
            request = self._build_agent_execution_request(
                node_inputs=dict(node_inputs),
                node_id=node_id,
                tool_name=tool_name,
                resolved_skillset_ref=resolved_skillset_ref,
                workflow_parameters=workflow_parameters,
                step_execution=self._step_execution_for(node_id) or 1,
                queue_order=self._step_execution_for(node_id) or 1,
                attempt_reason="policy_revalidation",
            )
            await self._resolve_step_resilience_policy_ref(
                node_id=node_id,
                execution_profile_ref=request.execution_profile_ref,
                parameters=workflow_parameters,
            )
            child_workflow_id = (
                f"{workflow.info().workflow_id}:agent:{node_id}:"
                f"jira-blocker-recheck{recheck_count}"
            )
            self._active_agent_child_workflow_id = child_workflow_id
            self._active_agent_id = request.agent_id
            try:
                child_result = await workflow.execute_child_workflow(
                    "MoonMind.AgentRun",
                    request,
                    id=child_workflow_id,
                    task_queue=self._workflow_child_task_queue(),
                )
            finally:
                self._active_agent_child_workflow_id = None
                self._active_agent_id = None
            return self._map_agent_run_result(child_result)
        except Exception as exc:
            diagnostic = self._record_failure_diagnostic(
                exc,
                stage=self._state,
                step_id=node_id,
                step_title=f"Re-check of Jira blockers for {tool_name}",
                source="child_workflow",
            )
            self._mark_step_terminal(
                node_id,
                status="failed",
                updated_at=workflow.now(),
                summary=diagnostic["message"],
                last_error=diagnostic["category"],
            )
            if failure_mode == "FAIL_FAST":
                raise
            return {
                "status": "FAILED",
                "outputs": {
                    "error": diagnostic["category"],
                    "summary": diagnostic["message"],
                },
            }

    async def _wait_for_jira_blocker_resolution(
        self,
        *,
        execution_result: Any,
        node_id: str,
        tool_name: str,
        tool_type: str,
        failure_mode: str,
        route: Any | None = None,
        execute_payload: Mapping[str, Any] | None = None,
        selected_skill: Any = None,
        node: Mapping[str, Any] | None = None,
        node_inputs: Mapping[str, Any] | None = None,
        task_skills: Any = None,
        workflow_parameters: Mapping[str, Any] | None = None,
        agent_request: Any = None,
        step_retry_overrides_enabled: bool = False,
    ) -> tuple[Any, bool]:
        skipped = False
        recheck_count = 0
        current_result = execution_result
        preserve_assessment_context = workflow.patched(
            RUN_JIRA_BLOCKER_RECHECK_ASSESSMENT_CONTEXT_PATCH
        )
        if preserve_assessment_context:
            outputs = self._get_from_result(current_result, "outputs")
            if isinstance(outputs, Mapping):
                self._record_assessment_context(outputs)
        while self._jira_blocker_waitable_result(
            current_result,
            tool_type=tool_type,
            tool_name=tool_name,
            selected_skill=selected_skill,
        ):
            self._enter_jira_blocker_wait(
                node_id=node_id,
                execution_result=current_result,
            )
            try:
                await workflow.wait_condition(
                    lambda: (
                        self._cancel_requested
                        or self._jira_blocker_wait_skipped
                        or self._paused
                    ),
                    timeout=DEPENDENCY_RECONCILE_INTERVAL,
                )
            except asyncio.TimeoutError:
                # Timeout is the expected path for periodic blocker rechecks.
                pass
            if self._cancel_requested:
                return current_result, skipped
            if self._jira_blocker_wait_skipped:
                skipped = True
                break

            recheck_count += 1
            coalesce_wait = workflow.patched(RUN_JIRA_BLOCKER_WAIT_COALESCING_PATCH)
            if not coalesce_wait:
                self._clear_jira_blocker_wait()
            await self._wait_if_paused_at_safe_boundary()
            if self._cancel_requested:
                return current_result, skipped
            if not coalesce_wait:
                self._set_state(STATE_EXECUTING, summary="Re-checking Jira blockers.")
                self._mark_step_running(
                    node_id,
                    updated_at=workflow.now(),
                    summary=f"Re-checking Jira blockers for {tool_name}",
                    increment_attempt=False,
                )
                await self._record_step_execution_manifest(
                    node_id,
                    phase="start",
                    updated_at=workflow.now(),
                    reason="policy_revalidation",
                )
            recheck_attempt = self._step_execution_for(node_id) or 0
            if self._is_step_execution_launch_blocked(
                node_id,
                attempt=recheck_attempt,
            ):
                current_result = {
                    "status": "FAILED",
                    "outputs": {
                        "error": "missing_required_checkpoint_evidence",
                        "summary": (
                            "Workspace policy rejected before launch."
                        ),
                    },
                }
                break
            if str(tool_type or "").strip().lower() == "agent_runtime":
                if agent_request is not None:
                    child_workflow_id = (
                        f"{workflow.info().workflow_id}:agent:{node_id}:"
                        f"jira-blocker-recheck{recheck_count}"
                    )
                    try:
                        self._active_agent_child_workflow_id = child_workflow_id
                        self._active_agent_id = getattr(agent_request, "agent_id", None)
                        child_result = await workflow.execute_child_workflow(
                            "MoonMind.AgentRun",
                            agent_request,
                            id=child_workflow_id,
                            task_queue=self._workflow_child_task_queue(),
                        )
                        current_result = self._map_agent_run_result(child_result)
                    except Exception as exc:
                        diagnostic = self._record_failure_diagnostic(
                            exc,
                            stage=self._state,
                            step_id=node_id,
                            step_title=f"Re-check of Jira blockers for {tool_name}",
                            source="child_workflow",
                            child_workflow_id=child_workflow_id,
                        )
                        self._mark_step_terminal(
                            node_id,
                            status="failed",
                            updated_at=workflow.now(),
                            summary=diagnostic["message"],
                            last_error=diagnostic["category"],
                        )
                        if failure_mode == "FAIL_FAST":
                            raise
                        break
                    finally:
                        self._active_agent_child_workflow_id = None
                        self._active_agent_id = None
                elif node is None or node_inputs is None or workflow_parameters is None:
                    raise ValueError(
                        "agent_runtime Jira blocker recheck requires node context"
                    )
                else:
                    current_result = await self._reexecute_jira_blocker_agent_runtime(
                        node=node,
                        node_inputs=node_inputs,
                        node_id=node_id,
                        tool_name=tool_name,
                        task_skills=task_skills,
                        workflow_parameters=workflow_parameters,
                        recheck_count=recheck_count,
                        failure_mode=failure_mode,
                    )
            else:
                if route is None or execute_payload is None:
                    raise ValueError(
                        "skill Jira blocker recheck requires activity context"
                    )
                max_attempts_override = (
                    self._jira_blocker_recheck_retry_attempts_override(
                        route=route,
                        execute_payload=execute_payload,
                        step_retry_overrides_enabled=step_retry_overrides_enabled,
                    )
                )
                if coalesce_wait:
                    recheck_payload = self._compact_jira_blocker_recheck_payload(
                        execute_payload
                    )
                else:
                    recheck_payload = dict(execute_payload)
                idempotency_key = recheck_payload.get("idempotency_key")
                if isinstance(idempotency_key, str) and idempotency_key.strip():
                    recheck_payload["idempotency_key"] = (
                        f"{idempotency_key}_jira_blocker_recheck_{recheck_count}"
                    )
                try:
                    current_result = await workflow.execute_activity(
                        route.activity_type,
                        recheck_payload,
                        cancellation_type=ActivityCancellationType.TRY_CANCEL,
                        **self._execute_kwargs_for_route(
                            route,
                            max_attempts_override=max_attempts_override,
                        ),
                    )
                except Exception as exc:
                    diagnostic = self._record_failure_diagnostic(
                        exc,
                        stage=self._state,
                        step_id=node_id,
                        step_title=f"Re-check of Jira blockers for {tool_name}",
                        source="activity",
                    )
                    self._mark_step_terminal(
                        node_id,
                        status="failed",
                        updated_at=workflow.now(),
                        summary=diagnostic["message"],
                        last_error=diagnostic["category"],
                    )
                    if failure_mode == "FAIL_FAST":
                        raise
                    break
            if preserve_assessment_context:
                current_result = self._merge_assessment_context_into_result(
                    current_result
                )
                outputs = self._get_from_result(current_result, "outputs")
                if isinstance(outputs, Mapping):
                    self._record_assessment_context(outputs)
            self._record_step_result_evidence(
                node_id,
                execution_result=current_result,
                updated_at=workflow.now(),
            )
            if self._activity_result_status(current_result) != "COMPLETED":
                break

        self._clear_jira_blocker_wait()
        if skipped:
            self._set_state(
                STATE_EXECUTING,
                summary="Jira blocker wait skipped by operator.",
            )
        self._update_search_attributes()
        self._update_memo()
        return current_result, skipped

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
            current_row = next(
                (
                    row
                    for row in self._step_ledger_rows
                    if row.get("logicalStepId") == node_id
                ),
                None,
            )
            if str((current_row or {}).get("status") or "") in TERMINAL_STEP_STATUSES:
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
        return normalized if normalized in {"auto", "none", "branch", "pr"} else ""

    def _managed_session_runtime_id(
        self, request: AgentExecutionRequest
    ) -> str | None:
        if request.agent_kind != "managed":
            return None
        return canonical_managed_session_runtime_id(request.agent_id)

    def _workflow_scoped_session_workflow_id(self, runtime_id: str) -> str:
        return f"{workflow.info().workflow_id}:session:{runtime_id}"

    def _workflow_scoped_session_visibility(
        self,
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

    def _workflow_scoped_session_static_details(
        self,
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

    def _pending_agent_step_refs(
        self,
        *,
        child_workflow_id: str,
        request: AgentExecutionRequest,
    ) -> dict[str, str]:
        refs = {"childWorkflowId": child_workflow_id}
        if request.managed_session is not None:
            agent_run_id = str(request.managed_session.agent_run_id or "").strip()
            if agent_run_id:
                refs["agentRunId"] = agent_run_id
        return refs

    async def _ensure_workflow_scoped_codex_session(
        self, request: AgentExecutionRequest
    ) -> CodexManagedSessionBinding | None:
        runtime_id = self._managed_session_runtime_id(request)
        if runtime_id is None:
            return None
        if self._codex_session_binding is not None:
            return self._codex_session_binding

        recovery_agent_run_id = ""
        if self._checkpoint_recovery_state is not None:
            recovery_agent_run_id = str(
                self._checkpoint_recovery_state.get("destinationAgentRunId") or ""
            ).strip()
        agent_run_id = recovery_agent_run_id or workflow.info().workflow_id
        session_input = CodexManagedSessionWorkflowInput(
            agentRunId=agent_run_id,
            runtimeId=runtime_id,
            executionProfileRef=request.execution_profile_ref,
        )
        session_workflow_id = self._workflow_scoped_session_workflow_id(runtime_id)
        if recovery_agent_run_id:
            session_workflow_id = f"{session_workflow_id}:recovery"
        initial_binding = CodexManagedSessionBinding.from_input(
            workflow_id=session_workflow_id,
            session_input=session_input,
        )
        self._codex_session_handle = await workflow.start_child_workflow(
            "MoonMind.AgentSession",
            session_input,
            id=session_workflow_id,
            task_queue=self._workflow_child_task_queue(),
            search_attributes=self._workflow_scoped_session_visibility(
                binding=initial_binding
            ),
            static_summary="Workflow-scoped managed runtime session",
            static_details=self._workflow_scoped_session_static_details(
                binding=initial_binding
            ),
        )
        self._codex_session_binding = initial_binding
        return self._codex_session_binding

    async def _maybe_bind_workflow_scoped_session(
        self, request: AgentExecutionRequest
    ) -> AgentExecutionRequest:
        if workflow.patched(RUN_DEFER_WORKFLOW_SCOPED_SESSION_UNTIL_SLOT_PATCH):
            runtime_id = self._managed_session_runtime_id(request)
            if runtime_id is None:
                return request
            if self._codex_session_binding is not None:
                return request.model_copy(
                    update={"managed_session": self._codex_session_binding}
                )

            parameters = dict(request.parameters or {})
            metadata = (
                dict(parameters.get("metadata"))
                if isinstance(parameters.get("metadata"), Mapping)
                else {}
            )
            moonmind_metadata = (
                dict(metadata.get("moonmind"))
                if isinstance(metadata.get("moonmind"), Mapping)
                else {}
            )
            moonmind_metadata["deferManagedSessionUntilSlot"] = {
                "runtimeId": runtime_id,
                "agentRunId": workflow.info().workflow_id,
            }
            metadata["moonmind"] = moonmind_metadata
            parameters["metadata"] = metadata
            return request.model_copy(update={"parameters": parameters})

        binding = await self._ensure_workflow_scoped_codex_session(request)
        if binding is None:
            return request
        return request.model_copy(update={"managed_session": binding})

    async def _maybe_clear_workflow_scoped_session_before_step(
        self,
        *,
        request: AgentExecutionRequest,
        logical_step_id: str,
    ) -> None:
        if not workflow.patched(RUN_WORKFLOW_SCOPED_SESSION_CLEAR_BETWEEN_STEPS_PATCH):
            return
        if (
            request.step_execution is not None
            and request.step_execution.runtime_context_policy
            == "reuse_session_same_epoch"
        ):
            return
        execution_ordinal = self._step_execution_for(logical_step_id) or 1
        # New histories dedupe by Step Execution identity (logical step +
        # attempt ordinal). Histories that predate this patch keep the old
        # logical-step dedupe key so replay does not schedule a new clear
        # activity when a later attempt is observed.
        if workflow.patched(RUN_WORKFLOW_SCOPED_SESSION_CLEAR_PER_EXECUTION_PATCH):
            clear_key: str | tuple[str, int] = (logical_step_id, execution_ordinal)
        else:
            clear_key = logical_step_id
        if clear_key in self._codex_session_cleared_before_step_attempts:
            return
        binding = self._codex_session_binding
        if binding is None:
            return
        if self._managed_session_runtime_id(request) != binding.runtime_id:
            return
        session_handle = workflow.get_external_workflow_handle(binding.workflow_id)
        reason = f"Clearing workflow-scoped context before step {logical_step_id}"
        request_id = step_execution_operation_idempotency_key(
            workflow_id=workflow.info().workflow_id,
            run_id=workflow.info().run_id,
            logical_step_id=logical_step_id,
            execution_ordinal=execution_ordinal,
            operation="clear_session",
        )
        if workflow.patched(
            RUN_WORKFLOW_SCOPED_SESSION_CLEAR_UPDATE_AUTHORITATIVE_PATCH
        ):
            try:
                await self._clear_workflow_scoped_session_via_update(
                    session_handle=session_handle,
                    binding=binding,
                    reason=reason,
                    request_id=request_id,
                )
            except AttributeError as exc:
                self._get_logger().warning(
                    "Workflow-scoped managed-session clear update unsupported for %s; "
                    "falling back to activity and signal: %s",
                    binding.session_id,
                    exc,
                )
                await self._clear_workflow_scoped_session_via_activity_then_signal(
                    session_handle=session_handle,
                    binding=binding,
                    reason=reason,
                    request_id=request_id,
                )
        elif workflow.patched(RUN_WORKFLOW_SCOPED_SESSION_CLEAR_ACTIVITY_SIGNAL_PATCH):
            await self._clear_workflow_scoped_session_via_activity_then_signal(
                session_handle=session_handle,
                binding=binding,
                reason=reason,
                request_id=request_id,
            )
        else:
            try:
                execute_update = getattr(session_handle, "execute_update", None)
                if execute_update is None:
                    raise AttributeError(
                        "'ExternalWorkflowHandle' object has no attribute "
                        "'execute_update'"
                    )
                result = await execute_update(
                    "ClearSession",
                    {
                        "reason": reason,
                        "requestId": request_id,
                    },
                )
                session_state = self._get_from_result(
                    result, "sessionState"
                ) or self._get_from_result(result, "session_state")
                if isinstance(session_state, Mapping):
                    session_epoch = self._get_from_result(
                        session_state, "sessionEpoch"
                    )
                    if isinstance(session_epoch, int) and session_epoch >= 1:
                        self._codex_session_binding = binding.model_copy(
                            update={"session_epoch": session_epoch}
                        )
            except AttributeError as exc:
                self._get_logger().warning(
                    "Workflow-scoped managed-session clear update unsupported for %s; "
                    "falling back to activity and signal: %s",
                    binding.session_id,
                    exc,
                )
                await self._clear_workflow_scoped_session_via_activity_then_signal(
                    session_handle=session_handle,
                    binding=binding,
                    reason=reason,
                    request_id=request_id,
                )
        self._codex_session_cleared_before_step_attempts.add(clear_key)

    async def _clear_workflow_scoped_session_via_update(
        self,
        *,
        session_handle: workflow.ExternalWorkflowHandle,
        binding: CodexManagedSessionBinding,
        reason: str,
        request_id: str,
    ) -> None:
        execute_update = getattr(session_handle, "execute_update", None)
        if execute_update is None:
            raise AttributeError(
                "'ExternalWorkflowHandle' object has no attribute 'execute_update'"
            )
        result = await execute_update(
            "ClearSession",
            {
                "reason": reason,
                "requestId": request_id,
            },
        )
        session_state = self._get_from_result(
            result, "sessionState"
        ) or self._get_from_result(result, "session_state")
        if isinstance(session_state, Mapping):
            session_epoch = self._get_from_result(session_state, "sessionEpoch")
            if isinstance(session_epoch, int) and session_epoch >= 1:
                self._codex_session_binding = binding.model_copy(
                    update={"session_epoch": session_epoch}
                )

    async def _terminate_workflow_scoped_sessions(self, *, reason: str) -> None:
        binding = self._codex_session_binding
        try:
            if binding is not None:
                session_handle = workflow.get_external_workflow_handle(
                    binding.workflow_id
                )
                if workflow.patched(
                    RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_ACTIVITY_SIGNAL_PATCH
                ):
                    await self._terminate_workflow_scoped_session_via_activity_then_signal(
                        session_handle=session_handle,
                        binding=binding,
                        reason=reason,
                    )
                elif workflow.patched(
                    RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_UPDATE_EXECUTE_PATCH
                ):
                    try:
                        execute_update = getattr(session_handle, "execute_update", None)
                        if execute_update is None:
                            raise AttributeError(
                                "'ExternalWorkflowHandle' object has no attribute "
                                "'execute_update'"
                            )
                        await execute_update("TerminateSession", {"reason": reason})
                    except Exception as exc:
                        self._get_logger().warning(
                            "Workflow-scoped managed-session terminate update failed for %s: %s",
                            binding.session_id,
                            exc,
                        )
                        await self._terminate_workflow_scoped_session_via_activity_then_signal(
                            session_handle=session_handle,
                            binding=binding,
                            reason=reason,
                        )
                elif workflow.patched(RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_UPDATE_PATCH):
                    await session_handle.signal(
                        "control_action",
                        {
                            "action": "terminate_session",
                            "reason": reason,
                        },
                    )
                elif workflow.patched(RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_PATCH):
                    try:
                        await self._terminate_workflow_scoped_session_via_activity(
                            binding=binding,
                            reason=reason,
                        )
                    except Exception as exc:
                        self._get_logger().warning(
                            "Workflow-scoped managed-session terminate activity failed for %s; "
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

    async def _clear_workflow_scoped_session_via_activity_then_signal(
        self,
        *,
        session_handle: workflow.ExternalWorkflowHandle,
        binding: CodexManagedSessionBinding,
        reason: str,
        request_id: str,
    ) -> None:
        clear_handle = await self._clear_workflow_scoped_session_via_activity(
            binding=binding,
            reason=reason,
            request_id=request_id,
        )
        session_state = clear_handle.session_state
        self._codex_session_binding = binding.model_copy(
            update={"session_epoch": session_state.session_epoch}
        )
        await session_handle.signal(
            "control_action",
            {
                "action": "clear_session",
                "reason": reason,
                "requestId": request_id,
                "containerId": session_state.container_id,
                "threadId": session_state.thread_id,
            },
        )

    async def _clear_workflow_scoped_session_via_activity(
        self,
        *,
        binding: CodexManagedSessionBinding,
        reason: str,
        request_id: str | None = None,
    ) -> CodexManagedSessionHandle:
        snapshot_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "agent_runtime.load_session_snapshot"
        )
        snapshot = await self._load_workflow_scoped_session_snapshot_for_clear(
            binding=binding,
            snapshot_route=snapshot_route,
        )
        if not snapshot.container_id or not snapshot.thread_id:
            raise ValueError("Workflow-scoped managed session cannot be cleared before launch")
        clear_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "agent_runtime.clear_session"
        )
        try:
            clear_payload = await self._execute_workflow_scoped_session_clear_activity(
                snapshot=snapshot,
                clear_route=clear_route,
                reason=reason,
                request_id=request_id,
            )
        except Exception as exc:
            if not is_managed_session_locator_mismatch_error(exc):
                raise
            refreshed_snapshot = (
                await self._load_workflow_scoped_session_snapshot_for_clear(
                    binding=binding,
                    snapshot_route=snapshot_route,
                )
            )
            if not refreshed_snapshot.container_id or not refreshed_snapshot.thread_id:
                raise ValueError(
                    "Workflow-scoped managed session cannot be cleared before launch"
                ) from exc
            clear_payload = await self._execute_workflow_scoped_session_clear_activity(
                snapshot=refreshed_snapshot,
                clear_route=clear_route,
                reason=reason,
                request_id=request_id,
            )
        return CodexManagedSessionHandle.model_validate(clear_payload)

    async def _load_workflow_scoped_session_snapshot_for_clear(
        self,
        *,
        binding: CodexManagedSessionBinding,
        snapshot_route: Any,
    ) -> CodexManagedSessionSnapshot:
        snapshot_payload = await workflow.execute_activity(
            snapshot_route.activity_type,
            binding.model_dump(mode="json", by_alias=True),
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
            **self._execute_kwargs_for_route(snapshot_route),
        )
        return CodexManagedSessionSnapshot.model_validate(snapshot_payload)

    async def _execute_workflow_scoped_session_clear_activity(
        self,
        *,
        snapshot: CodexManagedSessionSnapshot,
        clear_route: Any,
        reason: str,
        request_id: str | None,
    ) -> Any:
        next_thread_id = (
            f"thread:{snapshot.binding.session_id}:{snapshot.binding.session_epoch + 1}"
        )
        return await workflow.execute_activity(
            clear_route.activity_type,
            CodexManagedSessionClearRequest(
                sessionId=snapshot.binding.session_id,
                sessionEpoch=snapshot.binding.session_epoch,
                containerId=snapshot.container_id,
                threadId=snapshot.thread_id,
                newThreadId=next_thread_id,
                reason=reason,
                requestId=request_id,
            ).model_dump(mode="json", by_alias=True),
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
            **self._execute_kwargs_for_route(clear_route),
        )

    async def _terminate_workflow_scoped_session_via_activity_then_signal(
        self,
        *,
        session_handle: workflow.ExternalWorkflowHandle,
        binding: CodexManagedSessionBinding,
        reason: str,
    ) -> None:
        try:
            await self._terminate_workflow_scoped_session_via_activity(
                binding=binding,
                reason=reason,
            )
        except Exception as exc:
            self._get_logger().warning(
                "Workflow-scoped managed-session terminate activity failed for %s; "
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

    async def _terminate_workflow_scoped_session_via_activity(
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
                "generating workflow proposals.",
                "generating workflow proposals",
                "finalizing execution.",
                "finalizing execution",
            }
            or normalized.startswith("executing plan step")
            or (normalized.startswith("executed") and "plan step" in normalized)
        )

    def _resolve_publish_payload(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        task_payload = self._mapping_value(parameters, "workflow")
        if not task_payload:
            task_payload = self._mapping_value(parameters, "task")
        publish_payload = self._mapping_value(parameters, "publish")
        if publish_payload:
            return publish_payload
        nested_publish = task_payload.get("publish") if isinstance(task_payload, dict) else None
        if isinstance(nested_publish, Mapping):
            return self._json_mapping(nested_publish, path="parameters.workflow.publish")
        return {}

    def _proposal_telemetry_signals(self) -> list[dict[str, Any]]:
        """Build compact run-quality signals for proposal generation."""

        signals: list[dict[str, Any]] = []

        def add_signal(
            *,
            signal_type: str,
            summary: str | None,
            severity: str = "medium",
            diagnostics_ref: str | None = None,
            extra: dict[str, Any] | None = None,
        ) -> None:
            bounded_summary = self._coerce_text(summary, max_chars=500)
            if not bounded_summary:
                return
            signal: dict[str, Any] = {
                "type": signal_type,
                "tags": [signal_type],
                "severity": severity,
                "summary": bounded_summary,
            }
            bounded_ref = self._coerce_text(diagnostics_ref, max_chars=400)
            if bounded_ref:
                signal["diagnostics_ref"] = bounded_ref
            if extra:
                signal.update(extra)
            signals.append(signal)

        if self._publish_repair_attempts > 0:
            add_signal(
                signal_type="retry",
                severity="high" if self._publish_repair_attempts >= 2 else "medium",
                summary=(
                    "Publish required "
                    f"{self._publish_repair_attempts} repair attempt"
                    f"{'s' if self._publish_repair_attempts != 1 else ''}."
                ),
                extra={"retries": self._publish_repair_attempts},
            )

        summary = self._coerce_text(self._last_step_summary, max_chars=500)
        diagnostics_ref = self._coerce_text(self._last_diagnostics_ref, max_chars=400)
        summary_lc = (summary or "").lower()
        if summary:
            if any(term in summary_lc for term in ("flaky", "flake")):
                add_signal(
                    signal_type="flaky_test",
                    summary=summary,
                    diagnostics_ref=diagnostics_ref,
                )
            if "retry" in summary_lc or "retried" in summary_lc:
                add_signal(
                    signal_type="retry",
                    summary=summary,
                    diagnostics_ref=diagnostics_ref,
                )
            if any(term in summary_lc for term in ("loop", "repeated")):
                add_signal(
                    signal_type="loop_detected",
                    severity="high",
                    summary=summary,
                    diagnostics_ref=diagnostics_ref,
                )
            if any(
                term in summary_lc
                for term in ("missing file", "missing ref", "not found")
            ):
                add_signal(
                    signal_type="missing_ref",
                    severity="high",
                    summary=summary,
                    diagnostics_ref=diagnostics_ref,
                )
            if any(term in summary_lc for term in ("artifact", "diagnostic")):
                add_signal(
                    signal_type="artifact_gap",
                    summary=summary,
                    diagnostics_ref=diagnostics_ref,
                )

        return signals[:3]

    def _proposal_generation_requested(self, parameters: Mapping[str, Any]) -> bool:
        if workflow.patched("run-workflow-nested-propose-tasks"):
            task_node = parameters.get("task")
            if isinstance(task_node, Mapping):
                propose_tasks_value = (
                    task_node["proposeTasks"]
                    if "proposeTasks" in task_node
                    else parameters.get("proposeTasks")
                )
                return _coerce_bool(propose_tasks_value, default=False)
            return _coerce_bool(parameters.get("proposeTasks"), default=False)
        else:
            return _coerce_bool(parameters.get("proposeTasks"), default=False)

    @staticmethod
    def _compact_proposal_rows(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, dict)]

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

    def _normalize_native_pr_base_branch(self, value: Any) -> str | None:
        branch = self._coerce_text(value)
        if not branch:
            return None
        if branch.startswith("refs/remotes/origin/"):
            branch = branch.removeprefix("refs/remotes/origin/")
        elif branch.startswith("refs/heads/"):
            branch = branch.removeprefix("refs/heads/")
        if branch.startswith("origin/"):
            branch = branch.removeprefix("origin/")
        return branch

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
                self._publish_context.get("branch"),
                agent_outputs.get("branch"),
                agent_outputs.get("targetBranch"),
                workspace_spec.get("targetBranch"),
                last_node_inputs.get("targetBranch"),
            )
            base_candidates = (
                self._resolve_publish_base_branch(publish_payload),
                agent_outputs.get("push_base_branch"),
                agent_outputs.get("baseBranch"),
                agent_outputs.get("base_branch"),
                self._publish_context.get("baseRef"),
                workspace_spec.get("startingBranch"),
                last_node_inputs.get("startingBranch"),
                "main",
            )
        else:
            head_candidates = (
                agent_outputs.get("push_branch"),
                self._publish_context.get("branch"),
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
                self._publish_context.get("baseRef"),
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
                    self._normalize_native_pr_base_branch(value)
                    for value in base_candidates
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
        if status == "lease_conflict":
            return workflow.patched(NATIVE_PR_LEASE_CONFLICT_GATE_PATCH)
        if status == "protected_branch":
            return workflow.patched(NATIVE_PR_PUSH_STATUS_GATE_PATCH)
        return False

    def _extract_pull_request_url(self, result: Any) -> str | None:
        outputs = self._get_from_result(result, "outputs")
        if not isinstance(outputs, Mapping):
            return None

        for provider_key in (
            "providerNativePullRequest",
            "provider_native_pull_request",
        ):
            provider_native = outputs.get(provider_key)
            if not isinstance(provider_native, Mapping):
                continue
            for field in ("url", "pullRequestUrl", "prUrl"):
                value = provider_native.get(field)
                if not isinstance(value, str) or not value.strip():
                    continue
                match = _GITHUB_PR_URL_PATTERN.search(value)
                if match is not None:
                    return match.group(0)

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
        if (
            self._publish_status == "failed"
            and workflow.patched(RUN_STOP_ON_PUBLISH_HANDOFF_FAILURE_PATCH)
        ):
            return

        publish_mode = self._publish_mode(parameters)
        if publish_mode == "auto":
            self._record_auto_publish_result(execution_result)
            return
        if publish_mode not in {"pr", "branch"}:
            return

        outputs = self._effective_result_outputs(execution_result)
        if not isinstance(outputs, Mapping):
            return
        terminal_publication = outputs.get("terminalPublication")
        if isinstance(terminal_publication, Mapping):
            compact_terminal = {
                key: terminal_publication.get(key)
                for key in (
                    "intent", "status", "reasonCode", "source", "attempted",
                    "commitCreated", "branchPushed", "branchName", "branchUrl",
                    "headSha", "baseBranch", "remoteVerified", "evidenceRef",
                    "idempotencyKey",
                )
                if terminal_publication.get(key) is not None
            }
            self._publish_context["terminalPublication"] = compact_terminal
            if compact_terminal.get("remoteVerified") is True:
                self._publish_context["branch"] = compact_terminal.get("branchName")
                self._publish_context["headSha"] = compact_terminal.get("headSha")
                self._publish_context["baseRef"] = compact_terminal.get("baseBranch")
        self._record_no_commit_publish_evidence(outputs)

        not_required_reason = self._publish_not_required_reason(outputs)
        if not_required_reason is not None:
            self._publish_status = "not_required"
            self._publish_reason = not_required_reason
            if self._merge_automation_requested(parameters):
                self._publish_context["mergeAutomationStatus"] = "not_applicable"
            return

        report_not_required_reason = self._report_only_publish_not_required_reason(
            outputs
        )
        if report_not_required_reason is not None:
            self._publish_status = "not_required"
            self._publish_reason = report_not_required_reason
            if self._merge_automation_requested(parameters):
                self._publish_context["mergeAutomationStatus"] = "not_applicable"
            return

        push_status = self._coerce_text(outputs.get("push_status"))
        if push_status is None:
            return
        self._record_publish_metadata_context(outputs)

        if push_status == "no_commits":
            if publish_mode == "pr" and (
                self._pr_publish_optional_for_task(
                    parameters, include_applied_templates=True
                )
                or self._is_canonical_no_commit_task(parameters)
            ):
                self._publish_status = "not_required"
                self._publish_reason = self._compose_no_commit_publish_reason(
                    publish_mode=publish_mode,
                    pr_publish_optional=True,
                )
                if self._merge_automation_requested(parameters):
                    self._publish_context["mergeAutomationStatus"] = "not_applicable"
                return
            self._publish_status = "skipped"
            self._publish_reason = self._compose_no_commit_publish_reason(
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

        if push_status == "lease_conflict":
            if not workflow.patched(NATIVE_PR_LEASE_CONFLICT_GATE_PATCH):
                return
            push_error = self._coerce_text(outputs.get("push_error"), max_chars=200)
            push_branch = self._coerce_text(outputs.get("push_branch"), max_chars=120)
            self._publish_status = "failed"
            if push_branch and push_error:
                self._publish_reason = (
                    f"publish failed: remote branch '{push_branch}' changed "
                    f"before publish completed: {push_error}"
                )
            elif push_branch:
                self._publish_reason = (
                    f"publish failed: remote branch '{push_branch}' changed "
                    "before publish completed"
                )
            else:
                self._publish_reason = (
                    "publish failed: remote branch changed before publish completed"
                )
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

    async def _record_publish_result_from_execution(
        self,
        *,
        parameters: Mapping[str, Any],
        execution_result: Any,
    ) -> None:
        if self._publish_mode(parameters) == "auto":
            await self._resolve_auto_publish_evidence_ref(execution_result)
            if (
                self._publish_status == "failed"
                and str(self._publish_reason or "").startswith(
                    "auto_publish_evidence_read_failed:"
                )
            ):
                return
        self._record_publish_result(
            parameters=parameters,
            execution_result=execution_result,
        )

    async def _resolve_auto_publish_evidence_ref(self, execution_result: Any) -> None:
        resolver_ref_contract = workflow.patched(
            RUN_PR_RESOLVER_PUBLISH_EVIDENCE_REF_PATCH
        )
        sources = self._auto_publish_evidence_sources(execution_result)
        if not sources:
            return
        ref: Any = None
        for source in sources:
            for key in (
                "publishResult",
                "publish_result",
                "publishEvidence",
                "publish_evidence",
                "autoPublishEvidence",
                "auto_publish_evidence",
            ):
                if key in source:
                    if not resolver_ref_contract:
                        return
                    value = source.get(key)
                    if self._inline_auto_publish_evidence(value) is not None:
                        return
                    ref = self._auto_publish_artifact_ref(value) or ref
        for source in sources:
            output_refs = source.get("outputRefs") or source.get("output_refs")
            if not isinstance(output_refs, Mapping):
                continue
            ref = (
                output_refs.get("publish_result.json")
                or output_refs.get("publishResult")
                or output_refs.get("publish_result")
            )
            if resolver_ref_contract and not ref:
                ref = output_refs.get("publishEvidence") or output_refs.get(
                    "publish_evidence"
                )
            if isinstance(ref, str) and ref.strip():
                break
        if not isinstance(ref, str) or not ref.strip():
            return

        evidence_ref = ref.strip()
        self._publish_context["evidenceRef"] = evidence_ref
        artifact_read_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("artifact.read")
        try:
            evidence_payload = await execute_typed_activity(
                "artifact.read",
                ArtifactReadInput(
                    principal=self._principal(),
                    artifact_ref=evidence_ref,
                ),
                **self._execute_kwargs_for_route(artifact_read_route),
            )
        except Exception as exc:
            self._publish_status = "failed"
            self._publish_reason = f"auto_publish_evidence_read_failed: {exc}"
            return
        self._publish_context["autoPublishEvidence"] = evidence_payload

    def _record_auto_publish_result(self, execution_result: Any) -> None:
        resolver_ref_contract = workflow.patched(
            RUN_PR_RESOLVER_PUBLISH_EVIDENCE_REF_PATCH
        )
        evidence_payload: Any = None
        for source in self._auto_publish_evidence_sources(execution_result):
            for key in (
                "publishResult",
                "publish_result",
                "publishEvidence",
                "publish_evidence",
                "autoPublishEvidence",
                "auto_publish_evidence",
            ):
                if key in source:
                    value = source.get(key)
                    if not resolver_ref_contract:
                        evidence_payload = value
                        break
                    inline = self._inline_auto_publish_evidence(value)
                    if inline is not None:
                        evidence_payload = inline
                        break
                    ref = self._auto_publish_artifact_ref(value)
                    if ref:
                        self._publish_context["evidenceRef"] = ref
            if evidence_payload is None:
                output_refs = source.get("outputRefs") or source.get("output_refs")
                if isinstance(output_refs, Mapping):
                    ref = (
                        output_refs.get("publish_result.json")
                        or output_refs.get("publishResult")
                        or output_refs.get("publish_result")
                    )
                    if resolver_ref_contract and not ref:
                        ref = output_refs.get("publishEvidence") or output_refs.get(
                            "publish_evidence"
                        )
                    if isinstance(ref, str) and ref.strip():
                        self._publish_context["evidenceRef"] = ref.strip()
                        break
            else:
                break
        if evidence_payload is None:
            evidence_payload = self._publish_context.get("autoPublishEvidence")

        if evidence_payload is None:
            self._publish_status = "failed"
            self._publish_reason = "auto_publish_evidence_missing"
            return

        try:
            evidence = parse_auto_publish_evidence(evidence_payload)
        except AutoPublishEvidenceError as exc:
            self._publish_status = "failed"
            self._publish_reason = str(exc)
            return

        self._publish_context.update(
            {
                "mode": "auto",
                "owner": "agent",
                "status": evidence.status,
                "action": evidence.action,
                "skillId": evidence.skill_id,
                "repository": evidence.repository,
                "branch": evidence.branch,
                "localHead": evidence.local_head,
                "remoteBranchHead": evidence.remote_branch_head,
                "remoteVerified": evidence.remote_verified,
                "pushed": evidence.pushed,
                "merged": evidence.merged,
                "prUrl": evidence.pr_url,
                "blockedReason": evidence.blocked_reason,
                "verificationCommands": list(evidence.verification_commands),
            }
        )
        if evidence.status == "blocked":
            self._publish_status = "failed"
            self._publish_reason = evidence.blocked_reason or "auto_publish_blocked"
        elif evidence.status == "failed":
            self._publish_status = "failed"
            self._publish_reason = evidence.blocked_reason or "auto_publish_failed"
        elif evidence.status == "no_op_verified":
            self._publish_status = "not_required"
            self._publish_reason = "auto publish no-op verified"
        else:
            self._publish_status = "published"
            self._publish_reason = (
                "auto publish verified merge"
                if evidence.merged
                else "auto publish verified push"
            )
            if evidence.pr_url:
                self._pull_request_url = evidence.pr_url

    def _report_only_publish_not_required_reason(
        self,
        outputs: Mapping[str, Any],
    ) -> str | None:
        report_type = self._coerce_text(
            outputs.get("report_type") or outputs.get("reportType"),
            max_chars=120,
        )
        report_bundle = outputs.get("report_bundle") or outputs.get("reportBundle")
        if not report_type and isinstance(report_bundle, Mapping):
            report_type = self._coerce_text(
                report_bundle.get("report_type") or report_bundle.get("reportType"),
                max_chars=120,
            )
        if report_type not in _REPORT_ONLY_PUBLISH_TYPES:
            return None

        primary_ref = self._coerce_text(
            outputs.get("primary_report_ref") or outputs.get("primaryReportRef"),
            max_chars=200,
        )
        if not primary_ref and isinstance(report_bundle, Mapping):
            primary_bundle_ref = report_bundle.get(
                "primary_report_ref"
            ) or report_bundle.get("primaryReportRef")
            if isinstance(primary_bundle_ref, Mapping):
                primary_ref = self._coerce_text(
                    primary_bundle_ref.get("artifact_id")
                    or primary_bundle_ref.get("artifactId"),
                    max_chars=200,
                )
            else:
                primary_ref = self._coerce_text(primary_bundle_ref, max_chars=200)
        if not primary_ref:
            return None
        return (
            "security.pentest.run produced a final report artifact; "
            "PR/branch publication is not applicable"
        )

    def _publish_not_required_reason(self, outputs: Mapping[str, Any]) -> str | None:
        for source in self._publish_outcome_sources(outputs):
            status = self._coerce_text(
                source.get("publishStatus")
                or source.get("publish_status")
                or source.get("publishOutcome")
                or source.get("publish_outcome")
                or source.get("status")
                or source.get("outcome"),
                max_chars=80,
            )
            pr_required = source.get("prRequired")
            if pr_required is None:
                pr_required = source.get("pr_required")
            required = source.get("required")

            status_not_required = bool(
                status and status.lower() in _PUBLISH_NOT_REQUIRED_STATUSES
            )
            pr_explicitly_not_required = pr_required is False or required is False
            if not status_not_required and not pr_explicitly_not_required:
                continue

            reason = self._coerce_text(
                source.get("publishReason")
                or source.get("publish_reason")
                or source.get("reason")
                or source.get("summary")
                or source.get("message")
                or outputs.get("operator_summary")
                or outputs.get("operatorSummary"),
                max_chars=700,
            )
            return reason or "publish output not required"
        return None

    def _publish_outcome_sources(
        self, outputs: Mapping[str, Any]
    ) -> Iterable[Mapping[str, Any]]:
        for key in ("publishOutcome", "publish_outcome", "publish"):
            candidate = outputs.get(key)
            if isinstance(candidate, Mapping):
                yield candidate
        yield outputs

    @staticmethod
    def _is_successful_jira_story_output(*, mode: str, status: str) -> bool:
        if mode == "jira":
            return status in {
                "jira_created",
                "jira_partial",
                "jira_noop",
            }
        if mode == "github":
            return status in {
                "github_created",
                "github_partial",
                "github_noop",
            }
        return False

    @staticmethod
    def _trusted_jira_output_summary(outputs: Mapping[str, Any]) -> str | None:
        story_output = outputs.get("storyOutput") or outputs.get("story_output")
        if isinstance(story_output, Mapping):
            status = str(story_output.get("status") or "").strip()
            created_count = story_output.get("createdCount")
            story_count = story_output.get("storyCount")
            eligible_count = story_output.get("eligibleStoryCount")
            dependency_mode = str(story_output.get("dependencyMode") or "").strip()
            if status in {"jira_created", "jira_partial", "jira_noop"}:
                verb = "Created" if status != "jira_noop" else "Created no"
                parts = [f"{verb} Jira issues"]
                if isinstance(created_count, int):
                    parts.append(f"created={created_count}")
                if isinstance(eligible_count, int):
                    parts.append(f"eligible={eligible_count}")
                if isinstance(story_count, int):
                    parts.append(f"stories={story_count}")
                if dependency_mode:
                    parts.append(f"dependencyMode={dependency_mode}")
                return "; ".join(parts) + "."
            reason = str(story_output.get("reason") or "").strip()
            if status and (status.startswith("jira_") or status == "fallback"):
                suffix = f": {reason}" if reason else ""
                return f"Jira story output finished with status {status}{suffix}."

        if isinstance(story_output, Mapping):
            status = str(story_output.get("status") or "").strip()
            created_count = story_output.get("createdCount")
            story_count = story_output.get("storyCount")
            eligible_count = story_output.get("eligibleStoryCount")
            dependency_mode = str(story_output.get("dependencyMode") or "").strip()
            dependency_count = story_output.get("dependencyCount")
            if status in {"github_created", "github_partial", "github_noop"}:
                verb = "Created" if status != "github_noop" else "Created no"
                parts = [f"{verb} GitHub issues"]
                if isinstance(created_count, int):
                    parts.append(f"created={created_count}")
                if isinstance(eligible_count, int):
                    parts.append(f"eligible={eligible_count}")
                if isinstance(story_count, int):
                    parts.append(f"stories={story_count}")
                if dependency_mode:
                    parts.append(f"dependencyMode={dependency_mode}")
                if isinstance(dependency_count, int):
                    parts.append(f"dependencyCount={dependency_count}")
                return "; ".join(parts) + "."
            reason = str(story_output.get("reason") or "").strip()
            if status and status.startswith("github_"):
                suffix = f": {reason}" if reason else ""
                return f"GitHub story output finished with status {status}{suffix}."

        orchestration = outputs.get("jiraOrchestration") or outputs.get(
            "jira_orchestration"
        )
        if isinstance(orchestration, Mapping):
            status = str(
                orchestration.get("workflowStatus")
                or orchestration.get("status")
                or ""
            ).strip()
            created_count = orchestration.get(
                "createdWorkflowCount", orchestration.get("createdTaskCount")
            )
            story_count = orchestration.get("storyCount")
            dependency_count = orchestration.get("dependencyCount")
            parts = []
            if isinstance(created_count, int):
                parts.append(f"createdWorkflows={created_count}")
            if isinstance(story_count, int):
                parts.append(f"stories={story_count}")
            if isinstance(dependency_count, int):
                parts.append(f"dependencies={dependency_count}")
            failures = orchestration.get("failures")
            if isinstance(failures, list) and failures:
                first_failure = failures[0] if isinstance(failures[0], Mapping) else {}
                message = str(first_failure.get("message") or "").strip()
                error_code = str(first_failure.get("errorCode") or "").strip()
                if error_code or message:
                    message = message.rstrip(".")
                    parts.append(
                        "firstFailure="
                        + ": ".join(part for part in (error_code, message) if part)
                    )
            if status:
                suffix = f" ({'; '.join(parts)})" if parts else ""
                return f"Jira downstream workflow creation {status}{suffix}."

        github_orchestration = outputs.get("githubWorkflowOrchestration") or outputs.get(
            "github_workflow_orchestration"
        )
        if isinstance(github_orchestration, Mapping):
            status = str(github_orchestration.get("status") or "").strip()
            created_count = github_orchestration.get("createdWorkflowCount")
            story_count = github_orchestration.get("storyCount")
            dependency_count = github_orchestration.get("dependencyCount")
            parts = []
            if isinstance(created_count, int):
                parts.append(f"createdWorkflows={created_count}")
            if isinstance(story_count, int):
                parts.append(f"stories={story_count}")
            if isinstance(dependency_count, int):
                parts.append(f"workflowDependencies={dependency_count}")
            failures = github_orchestration.get("failures")
            if isinstance(failures, list) and failures:
                first_failure = failures[0] if isinstance(failures[0], Mapping) else {}
                message = str(first_failure.get("message") or "").strip()
                error_code = str(first_failure.get("errorCode") or "").strip()
                if error_code or message:
                    message = message.rstrip(".")
                    parts.append(
                        "firstFailure="
                        + ": ".join(part for part in (error_code, message) if part)
                    )
            if status:
                suffix = f" ({'; '.join(parts)})" if parts else ""
                return f"GitHub downstream workflow creation {status}{suffix}."
        return None

    def _record_execution_context(self, *, node_id: str, execution_result: Any) -> None:
        outputs = self._effective_result_outputs(execution_result)
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
        trusted_jira_summary = self._trusted_jira_output_summary(outputs)
        if meaningful_operator_summary:
            self._operator_summary = operator_summary
        elif trusted_jira_summary:
            self._operator_summary = self._sanitize_operator_summary(
                self._coerce_text(trusted_jira_summary, max_chars=1600)
            )

        step_summary = (
            meaningful_operator_summary
            or trusted_jira_summary
            or self._coerce_text(
                outputs.get("summary") or outputs.get("message"),
                max_chars=1600,
            )
        )
        self._last_step_summary = self._sanitize_operator_summary(
            step_summary
        )

        self._last_diagnostics_ref = self._coerce_text(
            outputs.get("diagnostics_ref") or outputs.get("diagnosticsRef"),
            max_chars=200,
        )
        gated_continuation = self._normalize_gated_continuation_request(
            outputs,
            node_id=node_id,
        )
        self._gated_continuation_request = gated_continuation
        self._gated_continuation_execution_ref = self._coerce_text(
            outputs.get("terminalContractExecutionRef"),
            max_chars=240,
        )
        if gated_continuation:
            self._publish_context["gatedContinuation"] = gated_continuation
        else:
            self._publish_context.pop("gatedContinuation", None)
        merge_automation_disposition = self._coerce_text(
            outputs.get("mergeAutomationDisposition")
            or outputs.get("merge_automation_disposition"),
            max_chars=80,
        )
        if (
            not merge_automation_disposition
            and gated_continuation
            and not gated_continuation.get("validationError")
            and gated_continuation.get("gateType") == "merge_automation"
        ):
            typed_action = self._normalize_gate_type(
                self._coerce_text(gated_continuation.get("action"), max_chars=80)
            )
            if typed_action in MERGE_AUTOMATION_CONTINUATION_DISPOSITIONS:
                merge_automation_disposition = typed_action
        self._merge_automation_disposition = merge_automation_disposition or None
        if self._merge_automation_disposition:
            self._publish_context["mergeAutomationDisposition"] = (
                self._merge_automation_disposition
            )
        else:
            self._publish_context.pop("mergeAutomationDisposition", None)
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
        self._record_publish_metadata_context(outputs)
        self._record_provider_native_publish_context(outputs)
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

    def _record_publish_metadata_context(self, source: Mapping[str, Any]) -> None:
        raw_metadata = source.get("prMetadata") or source.get("pr_metadata")
        if not isinstance(raw_metadata, Mapping):
            return
        title = self._coerce_text(raw_metadata.get("title"), max_chars=200)
        body = self._coerce_text(
            raw_metadata.get("body"), max_chars=5000, flatten=False
        )
        if not title or not body:
            return
        metadata: dict[str, Any] = {"title": title, "body": body}
        for output_key, context_key, max_chars in (
            ("jiraIssueKey", "jiraIssueKey", 80),
            ("moonSpecPath", "moonSpecPath", 300),
            ("source", "source", 80),
        ):
            value = self._coerce_text(raw_metadata.get(output_key), max_chars=max_chars)
            if value:
                metadata[context_key] = value
        self._publish_context["prMetadata"] = metadata

    def _record_provider_native_publish_context(
        self, source: Mapping[str, Any]
    ) -> None:
        raw_provider_pr = source.get("providerNativePullRequest") or source.get(
            "provider_native_pull_request"
        )
        if not isinstance(raw_provider_pr, Mapping):
            return

        readiness_state = self._coerce_text(
            raw_provider_pr.get("readinessState")
            or raw_provider_pr.get("readiness_state"),
            max_chars=80,
        )
        if readiness_state:
            self._publish_context["readinessState"] = readiness_state

        head_branch = self._coerce_text(
            raw_provider_pr.get("headBranch")
            or raw_provider_pr.get("head_branch")
            or raw_provider_pr.get("branch"),
            max_chars=120,
        )
        if head_branch:
            self._publish_context["branch"] = head_branch

        base_branch = self._coerce_text(
            raw_provider_pr.get("baseBranch")
            or raw_provider_pr.get("base_branch")
            or raw_provider_pr.get("baseRef")
            or raw_provider_pr.get("base_ref"),
            max_chars=120,
        )
        if base_branch:
            self._publish_context["baseRef"] = base_branch

        provider_metadata = raw_provider_pr.get("metadata")
        if isinstance(provider_metadata, Mapping):
            self._record_publish_metadata_context({"prMetadata": provider_metadata})
            compact_metadata: dict[str, Any] = {}
            for key, value in provider_metadata.items():
                compact_key = self._coerce_text(key, max_chars=80)
                if not compact_key:
                    continue
                if isinstance(value, (str, int, float, bool)) or value is None:
                    compact_metadata[compact_key] = value
                else:
                    compact_value = self._coerce_text(value, max_chars=500)
                    if compact_value:
                        compact_metadata[compact_key] = compact_value
            if compact_metadata:
                self._publish_context["providerNativePrMetadata"] = compact_metadata

        provider_record: dict[str, Any] = {}
        for key, value in (
            ("url", self._publish_context.get("pullRequestUrl")),
            ("readinessState", readiness_state),
            ("headBranch", head_branch),
            ("baseBranch", base_branch),
            ("source", self._coerce_text(raw_provider_pr.get("source"), max_chars=80)),
        ):
            if value:
                provider_record[key] = value
        if provider_record:
            self._publish_context["providerNativePullRequest"] = provider_record

    def _record_report_result(self, execution_result: Any) -> None:
        metadata = self._get_from_result(execution_result, "metadata")
        outputs = self._effective_result_outputs(execution_result)
        report_sources = [
            source
            for source in (metadata, outputs)
            if isinstance(source, Mapping)
        ]
        if not report_sources:
            return

        report_ref = ""
        report_bundle: Any = None
        for source in report_sources:
            report_ref = self._coerce_text(
                source.get("primaryReportRef")
                or source.get("primary_report_ref"),
                max_chars=200,
            )
            report_bundle = source.get("reportBundle") or source.get("report_bundle")
            if report_ref or isinstance(report_bundle, Mapping):
                break
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

    def _record_no_commit_publish_evidence(self, outputs: Mapping[str, Any]) -> None:
        push_status = self._coerce_text(outputs.get("push_status"), max_chars=80)
        if self._authoritative_publish_outcome_enabled and push_status == "pushed":
            stale_no_commit = self._publish_context.pop("noCommitPublish", None)
            stale_no_change = self._publish_context.pop("noChangePublish", None)
            if (
                stale_no_commit is not None or stale_no_change is not None
            ) and self._publish_status in {
                "not_required",
                "skipped",
            }:
                self._publish_status = None
                self._publish_reason = None
            return
        if push_status == "no_commits":
            self._publish_context["noCommitPublish"] = {"status": "no_commits"}
            return

        for key in ("noChanges", "no_changes", "repositoryUnchanged"):
            if outputs.get(key) is True:
                self._publish_context["noCommitPublish"] = {"status": key}
                return

        publish_outcome = outputs.get("publishOutcome") or outputs.get(
            "publish_outcome"
        )
        if isinstance(publish_outcome, Mapping):
            for key in ("noChanges", "no_changes", "repositoryUnchanged"):
                if publish_outcome.get(key) is True:
                    self._publish_context["noCommitPublish"] = {"status": key}
                    return

    def _authoritative_pr_requirement(
        self,
        *,
        publish_mode: str,
        pr_publish_optional: bool,
    ) -> bool:
        """Derive the PR gate from durable publication evidence after all steps."""

        if publish_mode != "pr" or self._integration is not None:
            return False
        if self._publish_status == "failed" or self._publish_context.get(
            "publicationBlockedBy"
        ):
            return False
        commit_count = self._publish_context.get("commitCount")
        has_commits = (
            not isinstance(commit_count, bool)
            and isinstance(commit_count, (int, float))
            and int(commit_count) > 0
        ) or (
            isinstance(commit_count, str)
            and commit_count.strip().isdigit()
            and int(commit_count.strip()) > 0
        )
        if has_commits or self._coerce_text(
            self._publish_context.get("pullRequestUrl"), max_chars=500
        ):
            return True
        if (
            self._publish_status == "published"
            and self._publish_context.get("storyOutputMode") in {"jira", "github"}
        ):
            return False
        if self._publish_status in {"not_required", "skipped"}:
            return False
        return not pr_publish_optional

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

    def _pr_publish_optional_for_task(
        self,
        parameters: Mapping[str, Any],
        *,
        include_applied_templates: bool = False,
    ) -> bool:
        task_payload = self._mapping_value(parameters, "workflow")
        if not task_payload:
            task_payload = self._mapping_value(parameters, "task")
        skill_names = self._task_skill_names(
            parameters,
            task_payload,
            include_applied_templates=False,
        )
        skill_names = skill_names | self._task_applied_template_skill_names(
            parameters,
            task_payload,
        )
        skill_names = skill_names | self._task_applied_template_slugs(
            parameters,
            task_payload,
            require_composition=True,
        )
        if include_applied_templates:
            applied_template_slugs = self._task_applied_template_slugs(
                parameters,
                task_payload,
            )
            if applied_template_slugs:
                skill_names.discard("auto")
            skill_names = skill_names | applied_template_slugs
        if not skill_names:
            return False
        return skill_names.issubset(_PR_OPTIONAL_TASK_SKILLS)

    def _is_canonical_no_commit_task(
        self,
        parameters: Mapping[str, Any],
    ) -> bool:
        if not self._canonical_no_commit_outcome_enabled:
            return False
        task_payload = self._mapping_value(parameters, "workflow")
        if not task_payload:
            task_payload = self._mapping_value(parameters, "task")
        return bool(
            self._task_applied_template_slugs(parameters, task_payload)
            & _CANONICAL_NO_COMMIT_TASK_PRESETS
        )

    def _task_skill_names(
        self,
        parameters: Mapping[str, Any],
        task_payload: Mapping[str, Any],
        *,
        include_applied_templates: bool = True,
    ) -> set[str]:
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

        if include_applied_templates:
            skill_names = skill_names | self._task_applied_template_skill_names(
                parameters,
                task_payload,
            )
            skill_names = skill_names | self._task_applied_template_slugs(
                parameters,
                task_payload,
            )

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
                    else:
                        name = self._coerce_text(item, max_chars=120)
                    if name:
                        skill_names.add(name.lower())
        return skill_names

    def _task_applied_template_skill_names(
        self,
        parameters: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> set[str]:
        skill_names: set[str] = set()
        for payload in (parameters, task_payload):
            applied_templates = payload.get("appliedStepTemplates")
            if applied_templates is None:
                applied_templates = payload.get("applied_step_templates")
            if not isinstance(applied_templates, Sequence) or isinstance(
                applied_templates,
                (str, bytes, bytearray),
            ):
                continue
            for template in applied_templates:
                if not isinstance(template, Mapping):
                    continue
                for key in ("tool", "skill"):
                    nested = template.get(key)
                    if isinstance(nested, Mapping):
                        name = self._coerce_text(
                            nested.get("name") or nested.get("id"),
                            max_chars=120,
                        )
                        if name:
                            skill_names.add(name.lower())
                template_skills = template.get("skills")
                if isinstance(template_skills, Mapping):
                    include = template_skills.get("include")
                    if isinstance(include, Sequence) and not isinstance(
                        include,
                        (str, bytes),
                    ):
                        for item in include:
                            if isinstance(item, Mapping):
                                name = self._coerce_text(
                                    item.get("name") or item.get("id"),
                                    max_chars=120,
                                )
                            else:
                                name = self._coerce_text(item, max_chars=120)
                            if name:
                                skill_names.add(name.lower())
        return skill_names

    def _task_applied_template_slugs(
        self,
        parameters: Mapping[str, Any],
        task_payload: Mapping[str, Any],
        *,
        require_composition: bool = False,
    ) -> set[str]:
        slugs: set[str] = set()
        for payload in (parameters, task_payload):
            applied_templates = payload.get("appliedStepTemplates")
            if applied_templates is None:
                applied_templates = payload.get("applied_step_templates")
            if not isinstance(applied_templates, Sequence) or isinstance(
                applied_templates,
                (str, bytes, bytearray),
            ):
                continue
            for template in applied_templates:
                if not isinstance(template, Mapping):
                    continue
                slug_sources: list[Any] = [template]
                composition = template.get("composition")
                if require_composition and not isinstance(composition, Mapping):
                    continue
                for include_source in (composition, template):
                    if not isinstance(include_source, Mapping):
                        continue
                    includes = include_source.get("includes")
                    if isinstance(includes, Sequence) and not isinstance(
                        includes,
                        (str, bytes, bytearray),
                    ):
                        slug_sources.extend(includes)
                for slug_source in slug_sources:
                    if not isinstance(slug_source, Mapping):
                        continue
                    slug = self._coerce_text(
                        slug_source.get("slug")
                        or slug_source.get("presetSlug")
                        or slug_source.get("preset_slug")
                        or slug_source.get("templateSlug")
                        or slug_source.get("template_slug")
                        or slug_source.get("id")
                        or slug_source.get("name"),
                        max_chars=120,
                    )
                    if slug:
                        slugs.add(slug.lower())
        return slugs

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

    def _compose_no_commit_publish_reason(
        self,
        *,
        publish_mode: str,
        pr_publish_optional: bool = False,
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
        if publish_mode == "pr" and pr_publish_optional:
            parts.append(
                "No pull request was required because this issue implementation "
                "workflow "
                "completed without repository changes"
            )
        elif publish_mode == "pr":
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
        elif publish_mode == "pr" and pr_publish_optional:
            parts.append(
                "no structured agent report confirmed whether the issue was "
                "already implemented"
            )

        reason = ". ".join(part.rstrip(".") for part in parts if part)
        return f"{reason}." if reason else "No repository commit was needed."

    def _compose_success_completion_message(
        self,
        *,
        publish_detail: str | None = None,
        publish_mode: str = "",
    ) -> str:
        parts = ["Workflow completed successfully"]
        detail = self._coerce_text(publish_detail, max_chars=900)
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

    def _actual_parent_workflow_id(self) -> str | None:
        try:
            parent_info = workflow.info().parent
        except Exception:
            return None
        if parent_info is None:
            return None
        return self._coerce_text(parent_info.workflow_id, max_chars=200)

    def _is_merge_automation_gated(self, parameters: Mapping[str, Any]) -> bool:
        """Return True when this run was launched by a MoonMind.MergeAutomation gate.

        Merge automation tags the resolver child's parameters with a ``mergeGate``
        block carrying the owning ``parentWorkflowId`` (see
        ``build_resolver_run_request``). The payload is user-controlled for
        standalone runs, so ownership is trusted only when the tagged parent matches
        Temporal's actual parent workflow id.
        """

        merge_gate = self._mapping_value(parameters, "mergeGate", "merge_gate")
        if not merge_gate:
            return False
        parent = self._coerce_text(
            merge_gate.get("parentWorkflowId")
            or merge_gate.get("parent_workflow_id"),
            max_chars=200,
        )
        if not parent:
            return False
        return parent == self._actual_parent_workflow_id()

    @staticmethod
    def _normalize_gate_type(value: str | None) -> str:
        return (value or "").strip().lower().replace("-", "_")

    @classmethod
    def _known_gated_continuation_action(cls, action: str) -> bool:
        normalized = cls._normalize_gate_type(action)
        return any(
            normalized in actions
            for actions in GATED_CONTINUATION_GATE_REGISTRY.values()
        )

    def _compact_continuation_mapping(
        self,
        value: Any,
        *,
        max_key_chars: int = 80,
        max_value_chars: int = 400,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        compact: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = self._coerce_text(raw_key, max_chars=max_key_chars)
            if not key:
                continue
            if isinstance(raw_value, bool) or raw_value is None:
                compact[key] = raw_value
            elif isinstance(raw_value, (int, float)):
                compact[key] = raw_value
            elif isinstance(raw_value, str):
                text = self._sanitize_operator_summary(
                    self._coerce_text(raw_value, max_chars=max_value_chars)
                )
                if text:
                    compact[key] = text
        return compact

    def _normalize_gated_continuation_request(
        self,
        outputs: Mapping[str, Any],
        *,
        node_id: str,
    ) -> dict[str, Any] | None:
        raw_request = outputs.get("gatedContinuation") or outputs.get(
            "gated_continuation"
        )
        request_source = "typed"
        if isinstance(raw_request, Mapping):
            raw_gate_type = self._coerce_text(
                raw_request.get("gateType") or raw_request.get("gate_type"),
                max_chars=80,
            )
            raw_action = self._coerce_text(
                raw_request.get("action") or raw_request.get("disposition"),
                max_chars=80,
            )
            reason = self._coerce_text(raw_request.get("reason"), max_chars=700)
            target_step = self._coerce_text(
                raw_request.get("targetLogicalStepId")
                or raw_request.get("target_logical_step_id"),
                max_chars=120,
            )
            evidence_refs = self._compact_continuation_mapping(
                raw_request.get("evidenceRefs") or raw_request.get("evidence_refs")
            )
            side_effects = self._compact_continuation_mapping(
                raw_request.get("sideEffects") or raw_request.get("side_effects"),
                max_value_chars=200,
            )
            budget = self._compact_continuation_mapping(
                raw_request.get("budget"),
                max_value_chars=120,
            )
            not_before = self._coerce_text(
                raw_request.get("notBefore") or raw_request.get("not_before"),
                max_chars=80,
            )
            retry_after_seconds = raw_request.get("retryAfterSeconds")
            if retry_after_seconds is None:
                retry_after_seconds = raw_request.get("retry_after_seconds")
            execution_ref = self._coerce_text(
                raw_request.get("executionRef") or raw_request.get("execution_ref"),
                max_chars=240,
            )
            head_sha = self._coerce_text(
                raw_request.get("headSha") or raw_request.get("head_sha"),
                max_chars=64,
            )
        else:
            legacy_disposition = self._coerce_text(
                outputs.get("mergeAutomationDisposition")
                or outputs.get("merge_automation_disposition"),
                max_chars=80,
            )
            if not legacy_disposition or not self._known_gated_continuation_action(
                legacy_disposition
            ):
                return None
            request_source = "legacy_merge_automation_disposition"
            raw_gate_type = "merge_automation"
            raw_action = legacy_disposition
            reason = (
                "Legacy pr-resolver merge automation disposition requires the "
                "workflow-owned merge gate to continue."
            )
            target_step = node_id
            evidence_refs = {}
            side_effects = {"externalPullRequest": True}
            budget = {}
            not_before = None
            retry_after_seconds = None
            execution_ref = None
            head_sha = self._coerce_text(outputs.get("headSha"), max_chars=64)

        gate_type = self._normalize_gate_type(raw_gate_type)
        action = self._normalize_gate_type(raw_action)
        continuation: dict[str, Any] = {
            "schemaVersion": "gated-continuation/v1",
            "source": request_source,
            "logicalStepId": node_id,
            "gateType": gate_type,
            "action": action,
        }
        if target_step:
            continuation["targetLogicalStepId"] = target_step
        if reason:
            continuation["reason"] = self._sanitize_operator_summary(reason) or reason
        if evidence_refs:
            continuation["evidenceRefs"] = evidence_refs
        if side_effects:
            continuation["sideEffects"] = side_effects
        if budget:
            continuation["budget"] = budget
        if not_before:
            continuation["notBefore"] = not_before
        if retry_after_seconds is not None:
            continuation["retryAfterSeconds"] = retry_after_seconds
        if execution_ref:
            continuation["executionRef"] = execution_ref
        if head_sha:
            continuation["headSha"] = head_sha

        allowed_actions = GATED_CONTINUATION_GATE_REGISTRY.get(gate_type)
        if allowed_actions is None:
            continuation["validationError"] = "unsupported_gate_type"
        elif action not in allowed_actions:
            continuation["validationError"] = "unsupported_gate_action"
        return continuation

    def _gated_continuation_failure_message(
        self,
        parameters: Mapping[str, Any],
    ) -> str | None:
        request = self._gated_continuation_request
        if not isinstance(request, Mapping):
            return None

        gate_type = self._normalize_gate_type(
            self._coerce_text(request.get("gateType"), max_chars=80)
        )
        action = self._normalize_gate_type(
            self._coerce_text(request.get("action"), max_chars=80)
        )
        validation_error = self._coerce_text(
            request.get("validationError"),
            max_chars=80,
        )
        if validation_error:
            return (
                "Step requested gated continuation with unsupported contract: "
                f"{validation_error} for gateType='{gate_type or 'unknown'}' "
                f"action='{action or 'unknown'}'. MoonMind did not continue the "
                "step because no trusted workflow-owned gate can validate it."
            )

        if gate_type == "merge_automation" and self._is_merge_automation_gated(
            parameters
        ):
            return None

        reason = self._coerce_text(request.get("reason"), max_chars=700)
        detail = (
            f" Reason: {reason.rstrip('.')}. "
            if reason
            else " "
        )
        return (
            "Step requested gated continuation "
            f"gateType='{gate_type or 'unknown'}' action='{action or 'unknown'}'."
            f"{detail}"
            "This workflow is not owned by that gate, so MoonMind cannot wait, "
            "re-execute the logical step, or advance safely. Re-submit the work "
            "under the owning gate or resolve the external condition manually."
        )

    def _continuation_disposition_failure_message(
        self, parameters: Mapping[str, Any]
    ) -> str | None:
        """Return a failure message for an ungated continuation disposition.

        ``reenter_gate`` (and any future continuation disposition) tells
        ``MoonMind.MergeAutomation`` to re-open the readiness gate, poll CI, and
        finalize the merge on a later cycle. When the resolver runs as a standalone
        top-level ``MoonMind.UserWorkflow`` there is no owning gate, so the
        continuation is dropped: CI/merge finalization never happens and the pull
        request is left unresolved. Reporting such a run as ``success`` is a
        false-green outcome, so we surface it as an actionable failure instead.
        """

        message = self._gated_continuation_failure_message(parameters)
        if message:
            return message
        if self._gated_continuation_request:
            return None

        disposition = self._normalize_gate_type(self._merge_automation_disposition)
        if disposition not in MERGE_AUTOMATION_CONTINUATION_DISPOSITIONS:
            return None
        if self._is_merge_automation_gated(parameters):
            return None
        return (
            "pr-resolver reported mergeAutomationDisposition="
            f"'{disposition}', a continuation state that requires a "
            "MoonMind.MergeAutomation gate to re-enter, poll CI, and finalize the "
            "merge. This run is not owned by merge automation, so the pull request "
            "was not resolved. Re-submit the resolution under merge automation or "
            "finalize the merge manually."
        )

    def _determine_publish_completion(
        self,
        *,
        parameters: Mapping[str, Any],
    ) -> tuple[str, str, bool]:
        if self._plan_blocked_message:
            return ("failed", self._plan_blocked_message, True)

        for row in self._step_ledger_rows:
            finalization_outcome = row.get("finalizationOutcome")
            if not isinstance(finalization_outcome, Mapping):
                continue
            if (
                finalization_outcome.get("status") == "failed"
                and finalization_outcome.get("criticality") == "required"
            ):
                failure_message = self._coerce_text(
                    finalization_outcome.get("message"), max_chars=500
                )
                if not failure_message and self._publish_status == "failed":
                    failure_message = self._coerce_text(
                        self._publish_reason, max_chars=500
                    )
                if not failure_message:
                    phase = self._coerce_text(
                        finalization_outcome.get("phase"), max_chars=100
                    )
                    failure_message = (
                        "Required step finalization failed during "
                        f"{phase.replace('_', ' ')}."
                        if phase
                        else "Required step finalization failed."
                    )
                return (
                    "failed",
                    failure_message,
                    True,
                )

        publish_mode = self._publish_mode(parameters)
        if (
            self._authoritative_publish_outcome_enabled
            and self._moonspec_draft_publication_reason is not None
        ):
            pull_request_url = self._coerce_text(
                self._pull_request_url
                or self._publish_context.get("pullRequestUrl"),
                max_chars=500,
            )
            if pull_request_url:
                return (
                    "failed",
                    "Workflow failed MoonSpec verification; incomplete work was "
                    f"preserved in draft pull request {pull_request_url}. "
                    f"{self._moonspec_draft_publication_reason}",
                    True,
                )
            return (
                "failed",
                "Workflow failed MoonSpec verification and draft pull request "
                "publication did not complete. "
                f"{self._moonspec_draft_publication_reason}",
                True,
            )
        if self._publish_status == "skipped":
            if publish_mode == "pr":
                self._publish_status = "failed"
                return (
                    "failed",
                    self._publish_reason
                    or "publishMode 'pr' requested but no local changes were produced",
                    True,
                )
            return ("no_commit", "No repository commit was needed.", False)

        if self._publish_status == "failed":
            return (
                "failed",
                self._publish_reason or "Publish failed",
                True,
            )

        if self._publish_status == "not_required":
            if self._report_requested(parameters) and not self._report_created:
                return (
                    "failed",
                    "reportOutput requested but no final report was created",
                    True,
                )
            if self._is_canonical_no_commit_outcome(parameters):
                return (
                    "no_commit",
                    self._compose_success_completion_message(
                        publish_detail=self._publish_reason,
                        publish_mode=publish_mode,
                    ),
                    False,
                )
            return (
                "success",
                self._compose_success_completion_message(
                    publish_detail=self._publish_reason,
                    publish_mode=publish_mode,
                ),
                False,
            )

        missing_outcome = self._missing_required_outcome_reason(
            parameters=parameters,
            publish_mode=publish_mode,
        )
        if missing_outcome:
            self._publish_status = "failed"
            self._publish_reason = missing_outcome
            return ("failed", missing_outcome, True)

        if publish_mode == "auto" and self._publish_status is None:
            self._publish_status = "failed"
            self._publish_reason = "auto_publish_evidence_missing"
            return ("failed", self._publish_reason, True)

        if publish_mode == "none":
            return (
                "success",
                self._compose_success_completion_message(publish_mode=publish_mode),
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

    def _is_canonical_no_commit_outcome(
        self,
        parameters: Mapping[str, Any],
    ) -> bool:
        if not self._canonical_no_commit_outcome_enabled:
            return False
        if self._publish_mode(parameters) not in {"pr", "branch"}:
            return False
        if self._report_requested(parameters):
            return False
        if not self._has_no_commit_publish_evidence():
            return False
        return not self._pull_request_created() and not self._branch_published()

    def _missing_required_outcome_reason(
        self,
        *,
        parameters: Mapping[str, Any],
        publish_mode: str,
    ) -> str | None:
        if self._publish_status == "not_required":
            return None
        if publish_mode == "pr" and not self._pull_request_created():
            return "publishMode 'pr' requested but no PR was created"
        if publish_mode == "branch" and self._publish_status is None:
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
        task_payload = self._mapping_value(parameters, "workflow")
        if not task_payload:
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
        task_payload = self._mapping_value(parameters, "workflow")
        if not task_payload:
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
            raw_post_merge_github = candidate.get(
                "postMergeGithub"
            ) or candidate.get("post_merge_github")
            post_merge_github = (
                dict(raw_post_merge_github)
                if isinstance(raw_post_merge_github, Mapping)
                else {}
            )
            github_issue = self._canonical_github_issue_from_parameters(parameters)
            if github_issue:
                post_merge_github.setdefault("enabled", True)
                post_merge_github.setdefault("required", True)
                post_merge_github.setdefault("repository", github_issue["repository"])
                post_merge_github.setdefault(
                    "issueNumber", github_issue["issueNumber"]
                )
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
                "postMergeGithub": post_merge_github,
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

    def _canonical_github_issue_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        task_payload = self._mapping_value(parameters, "workflow")
        if not task_payload:
            task_payload = self._mapping_value(parameters, "task")
        mappings: list[Mapping[str, Any]] = [parameters, task_payload]
        for source in (parameters, task_payload):
            for key in ("inputs", "input"):
                nested = source.get(key)
                if isinstance(nested, Mapping):
                    mappings.append(nested)
        templates = task_payload.get("appliedStepTemplates")
        if isinstance(templates, Sequence) and not isinstance(
            templates, (str, bytes)
        ):
            for template in templates:
                if not isinstance(template, Mapping):
                    continue
                mappings.append(template)
                nested = template.get("inputs")
                if isinstance(nested, Mapping):
                    mappings.append(nested)
        for mapping in mappings:
            issue = mapping.get("github_issue") or mapping.get("githubIssue")
            if not isinstance(issue, Mapping):
                continue
            repository = self._coerce_text(
                issue.get("repository") or issue.get("repo"), max_chars=240
            )
            try:
                issue_number = int(issue.get("number") or issue.get("issueNumber"))
            except (TypeError, ValueError):
                issue_number = 0
            if repository and issue_number > 0:
                return {
                    "repository": repository,
                    "issueNumber": issue_number,
                }
        return None

    def _canonical_jira_issue_key_from_parameters(
        self,
        parameters: Mapping[str, Any],
    ) -> str | None:
        # Canonical runtime parameters carry the task payload under "workflow";
        # only legacy/submission-shaped payloads use "task". Resolve "workflow"
        # first, but treat an empty workflow object as absent so mixed in-flight
        # payloads still inspect the legacy task fallback. Mismatching this key
        # silently drops the issue key, which disables post-merge Jira completion
        # and leaves the issue in Code Review. This mirrors
        # _merge_automation_request's own lookup.
        task_payload = self._mapping_value(parameters, "workflow")
        if not task_payload:
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
        for templates in (
            task_payload.get("appliedStepTemplates"),
            task_payload.get("applied_step_templates"),
        ):
            if not isinstance(templates, Sequence) or isinstance(
                templates, (str, bytes)
            ):
                continue
            for template in templates:
                if not isinstance(template, Mapping):
                    continue
                mappings.append(template)
                for key in ("inputs", "input", "inputMapping", "input_mapping"):
                    nested = template.get(key)
                    if isinstance(nested, Mapping):
                        mappings.append(nested)
        for mapping in mappings:
            candidate = self._jira_issue_key_from_mapping(mapping)
            if candidate:
                return candidate
        return None

    def _jira_issue_key_from_mapping(self, mapping: Mapping[str, Any]) -> str | None:
        candidate = self._coerce_text(
            mapping.get("jiraIssueKey")
            or mapping.get("jira_issue_key")
            or mapping.get("issueKey")
            or mapping.get("issue_key"),
            max_chars=40,
        )
        normalized = str(candidate or "").strip().upper()
        if normalized and _JIRA_ISSUE_KEY_PATTERN.fullmatch(normalized):
            return normalized
        for key in ("jiraIssue", "jira_issue", "jira"):
            nested = mapping.get(key)
            if not isinstance(nested, Mapping):
                continue
            nested_candidate = self._coerce_text(
                nested.get("key")
                or nested.get("issueKey")
                or nested.get("issue_key"),
                max_chars=40,
            )
            normalized_nested = str(nested_candidate or "").strip().upper()
            if normalized_nested and _JIRA_ISSUE_KEY_PATTERN.fullmatch(
                normalized_nested
            ):
                return normalized_nested
        return None

    def _is_jira_backed_task(
        self,
        parameters: Mapping[str, Any],
        task_payload: Mapping[str, Any],
    ) -> bool:
        skill_names = self._task_skill_names(parameters, task_payload)
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
                "postMergeGithub": request.get("postMergeGithub") or {},
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
        post_merge_github = result_map.get("postMergeGithub")
        if isinstance(post_merge_github, Mapping):
            summary["postMergeGithub"] = dict(post_merge_github)
        return summary

    async def _complete_already_implemented_jira_if_needed(
        self,
        *,
        parameters: Mapping[str, Any],
    ) -> None:
        if not workflow.patched(RUN_ALREADY_IMPLEMENTED_JIRA_COMPLETION_PATCH):
            return
        if self._publish_status != "not_required":
            return
        if self._publish_mode(parameters) != "pr":
            return
        if not self._pr_publish_optional_for_task(
            parameters,
            include_applied_templates=True,
        ):
            return
        if not self._has_no_commit_publish_evidence():
            return
        evidence = self._already_implemented_no_work_evidence()
        if not evidence:
            return

        issue_key = self._canonical_jira_issue_key_from_parameters(parameters)
        if not issue_key:
            raise ValueError(
                "Jira completion required for already-implemented no-change result, "
                "but no authoritative Jira issue key was found."
            )

        post_merge_jira = self._already_implemented_jira_completion_config(
            parameters=parameters,
            issue_key=issue_key,
        )
        decision = await workflow.execute_activity(
            "merge_automation.complete_post_merge_jira",
            {
                "parentWorkflowId": workflow.info().workflow_id,
                "parentRunId": workflow.info().run_id,
                "resolverDisposition": "already_implemented_no_commit",
                "jiraIssueKey": issue_key,
                "postMergeJira": post_merge_jira,
                "candidateContext": {
                    "taskOriginIssueKey": issue_key,
                    "taskMetadataIssueKey": issue_key,
                    "publishContextIssueKey": issue_key,
                    "alreadyImplementedEvidence": evidence[:700],
                },
            },
            start_to_close_timeout=timedelta(minutes=2),
            task_queue=INTEGRATIONS_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        decision_map = dict(decision) if isinstance(decision, Mapping) else {}
        compact_result = {
            key: value
            for key, value in decision_map.items()
            if key not in {"issueResolution", "transition", "artifactRefs"}
        }
        compact_result.setdefault("issueKey", issue_key)
        self._publish_context["alreadyImplementedJiraCompletion"] = compact_result

        status = self._coerce_text(decision_map.get("status"), max_chars=80)
        if status not in {"succeeded", "noop_already_done"}:
            reason = self._coerce_text(
                decision_map.get("reason"),
                max_chars=500,
            )
            raise ValueError(
                reason
                or "Jira completion failed for already-implemented no-change result."
            )

        completion_summary = self._already_implemented_jira_completion_summary(
            decision_map=decision_map,
            issue_key=issue_key,
        )
        if completion_summary and completion_summary not in str(
            self._publish_reason or ""
        ):
            self._publish_reason = (
                f"{str(self._publish_reason or '').rstrip('. ')}. "
                f"{completion_summary}"
            ).strip()

    def _has_no_commit_publish_evidence(self) -> bool:
        evidence = self._publish_context.get("noCommitPublish")
        if not isinstance(evidence, Mapping):
            evidence = self._publish_context.get("noChangePublish")
        if not isinstance(evidence, Mapping):
            return False
        status = self._coerce_text(evidence.get("status"), max_chars=80)
        return bool(status)

    def _already_implemented_no_work_evidence(self) -> str | None:
        for candidate in (
            self._publish_reason,
            self._operator_summary,
            self._last_step_summary,
        ):
            text = self._coerce_text(candidate, max_chars=900)
            if not text:
                continue
            match = _ALREADY_IMPLEMENTED_NO_WORK_PATTERN.search(text)
            if not match:
                continue
            uncertainty_context = text[
                max(0, match.start() - 140) : min(len(text), match.end() + 60)
            ]
            if _ALREADY_IMPLEMENTED_UNCERTAINTY_PATTERN.search(uncertainty_context):
                continue
            return text
        return None

    def _already_implemented_jira_completion_config(
        self,
        *,
        parameters: Mapping[str, Any],
        issue_key: str,
    ) -> dict[str, Any]:
        request = self._merge_automation_request(parameters) or {}
        post_merge_jira = request.get("postMergeJira")
        if not isinstance(post_merge_jira, Mapping):
            post_merge_jira = {}
        config = dict(post_merge_jira)
        config["enabled"] = True
        config["required"] = True
        config["issueKey"] = issue_key
        config.setdefault("strategy", "done_category")
        return config

    def _already_implemented_jira_completion_summary(
        self,
        *,
        decision_map: Mapping[str, Any],
        issue_key: str,
    ) -> str:
        status = self._coerce_text(decision_map.get("status"), max_chars=80)
        if status == "succeeded":
            transition_name = self._coerce_text(
                decision_map.get("transitionName"),
                max_chars=80,
            )
            if transition_name:
                return f"Jira issue {issue_key} was moved to {transition_name}."
            return f"Jira issue {issue_key} was moved to Done."
        if status == "noop_already_done":
            return f"Jira issue {issue_key} was already in a done-category status."
        return ""

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
        if self._moonspec_draft_publication_reason is not None:
            self._publish_context["mergeAutomationStatus"] = "not_applicable"
            self._publish_context["mergeAutomationSummary"] = (
                "Merge automation is disabled for an attention-required "
                "MoonSpec draft pull request."
            )
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
                task_queue=self._workflow_child_task_queue(),
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
        except Exception as exc:
            self._awaiting_external = False
            self._waiting_reason = None
            self._publish_context["mergeAutomationStatus"] = "failed"
            self._record_failure_diagnostic(
                exc,
                stage=self._state,
                source="child_workflow",
                child_workflow_id=workflow_id,
                step_title="merge automation",
            )
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
        """Resolve effective workflow/step skill intent before AgentRun launch."""

        selected_skill = selected_agent_skill(node_inputs)
        if selected_skill == "auto":
            selected_skill = ""
        if existing_skillset_ref:
            if selected_skill and self._workflow_patch_enabled(
                RUN_EXISTING_SKILLSET_TERMINAL_CONTRACT_PATCH
            ):
                artifact_read_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                    "artifact.read"
                )
                resolved_payload = await execute_typed_activity(
                    "artifact.read",
                    ArtifactReadInput(
                        principal=self._principal(),
                        artifact_ref=existing_skillset_ref,
                    ),
                    **self._execute_kwargs_for_route(artifact_read_route),
                )
                resolved = ResolvedSkillSet.model_validate(
                    self._decode_json_payload(
                        resolved_payload,
                        error_message=(
                            "resolvedSkillsetRef must resolve to a JSON object"
                        ),
                    )
                )
                self._record_resolved_selected_skill(
                    resolved=resolved,
                    selected_skill=selected_skill,
                    node_id=node_id,
                    terminal_contract_enabled=True,
                )
            return existing_skillset_ref

        effective = build_effective_workflow_skill_selectors(
            task_skills,
            node_skills if node_skills is not None else node_inputs.get("skills"),
        )
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
            self._record_resolved_selected_skill(
                resolved=resolved,
                selected_skill=selected_skill,
                node_id=node_id,
                terminal_contract_enabled=self._workflow_patch_enabled(
                    RUN_RESOLVED_SKILL_TERMINAL_CONTRACT_PATCH
                ),
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

    def _record_resolved_selected_skill(
        self,
        *,
        resolved: Any,
        selected_skill: str,
        node_id: str,
        terminal_contract_enabled: bool,
    ) -> None:
        """Record trusted selected-Skill launch metadata from its snapshot."""

        normalized_skill = str(selected_skill).strip().lower()
        resolved_skill_names = self._resolved_skillset_skill_names(resolved)
        if normalized_skill not in resolved_skill_names:
            raise ValueError(
                f"selected skill '{selected_skill}' was not resolved into the "
                "agent skill snapshot"
            )
        selected_entry = self._resolved_skillset_entry(
            resolved,
            normalized_skill,
        )
        if terminal_contract_enabled:
            terminal_contract = self._resolved_skill_terminal_contract(selected_entry)
            if terminal_contract is not None:
                self._resolved_skill_terminal_contract_by_step[node_id] = (
                    terminal_contract
                )
        if normalized_skill != "pr-resolver":
            return
        if workflow.patched(RUN_PR_RESOLVER_SKILL_OWNED_EXECUTION_PATCH):
            decision = require_skill_owned_pr_resolver_execution(selected_entry)
            self._native_skill_binding_by_step[node_id] = {
                "eligible": decision.eligible,
                "host": decision.host,
                "reasonCode": decision.reason_code,
                "identity": dict(decision.identity),
            }
        elif workflow.patched(RUN_TRUSTED_PR_RESOLVER_NATIVE_BINDING_PATCH):
            decision = evaluate_pr_resolver_native_binding(selected_entry)
            self._native_skill_binding_by_step[node_id] = {
                "eligible": decision.eligible,
                "host": decision.host,
                "reasonCode": decision.reason_code,
                "identity": dict(decision.identity),
            }
        else:
            # Replay path for histories created before native routing was bound
            # to immutable resolved-skill evidence. Those histories selected the
            # native host by canonical name, so preserve that child-workflow
            # command only for their replay.
            self._native_skill_binding_by_step[node_id] = {
                "eligible": True,
                "host": "temporal",
                "reasonCode": "legacy_name_binding_replay",
                "identity": {},
            }

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
    def _resolved_skillset_entry(resolved: Any, skill_name: str) -> Any:
        skills = (
            resolved.get("skills")
            if isinstance(resolved, WorkflowMapping)
            else getattr(resolved, "skills", None)
        )
        if not isinstance(skills, Iterable) or isinstance(skills, (str, bytes)):
            return None
        normalized = str(skill_name or "").strip().lower()
        for entry in skills:
            raw_name = (
                entry.get("skill_name")
                or entry.get("skillName")
                or entry.get("name")
                if isinstance(entry, WorkflowMapping)
                else getattr(entry, "skill_name", None)
                or getattr(entry, "skillName", None)
                or getattr(entry, "name", None)
            )
            if str(raw_name or "").strip().lower() == normalized:
                return entry
        return None

    @staticmethod
    def _resolved_skill_terminal_contract(
        resolved_entry: Any,
    ) -> dict[str, Any] | None:
        """Return the compact terminal contract selected with the Skill."""

        if resolved_entry is None:
            return None
        if isinstance(resolved_entry, WorkflowMapping):
            raw_contract = resolved_entry.get(
                "terminal_contract",
                resolved_entry.get("terminalContract"),
            )
        else:
            raw_contract = getattr(resolved_entry, "terminal_contract", None)
            if raw_contract is None:
                raw_contract = getattr(resolved_entry, "terminalContract", None)
        if hasattr(raw_contract, "model_dump"):
            raw_contract = raw_contract.model_dump(mode="json")
        if not isinstance(raw_contract, WorkflowMapping):
            return None

        def _contract_text(*keys: str) -> str:
            for key in keys:
                value = raw_contract.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""

        contract_id = _contract_text("contract_id", "contractId")
        owner = _contract_text("owner")
        evidence_kind = _contract_text("evidence_kind", "evidenceKind")
        relative_path = _contract_text("relative_path", "relativePath")
        expected_schema_version = _contract_text(
            "expected_schema_version",
            "expectedSchemaVersion",
        )
        if not all(
            (
                contract_id,
                owner,
                evidence_kind,
                relative_path,
                expected_schema_version,
            )
        ):
            return None
        return {
            "contractId": contract_id,
            "owner": owner,
            "evidenceKind": evidence_kind,
            "relativePath": relative_path,
            "expectedSchemaVersion": expected_schema_version,
        }

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

    @staticmethod
    def _normalized_runtime_command_payload(raw: Any) -> Any:
        if not isinstance(raw, Mapping):
            return raw
        payload = dict(raw)
        legacy_terms = ("runtime", "capability", "version")
        removed_keys = (
            legacy_terms[0]
            + legacy_terms[1][:1].upper()
            + legacy_terms[1][1:]
            + legacy_terms[2][:1].upper()
            + legacy_terms[2][1:],
            "_".join(legacy_terms),
        )
        for key in removed_keys:
            payload.pop(key, None)
        return payload

    @staticmethod
    def _default_moonspec_verify_artifact_path(node_id: str) -> str:
        safe_node_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(node_id or "")).strip("-")
        if not safe_node_id:
            safe_node_id = "verify"
        return f"var/artifacts/moonspec-verify/{safe_node_id}.json"

    def _ensure_moonspec_verify_parameters(
        self,
        *,
        parameters: dict[str, Any],
        node_inputs: Mapping[str, Any],
        node_id: str,
    ) -> None:
        nested_inputs = node_inputs.get("inputs")
        skill_inputs = nested_inputs if isinstance(nested_inputs, Mapping) else {}
        verify_artifact_path = None
        for source in (parameters, node_inputs, skill_inputs):
            for key in (
                "verify_artifact_path",
                "verifyArtifactPath",
                "verification_artifact_path",
                "verificationArtifactPath",
            ):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    verify_artifact_path = value.strip()
                    break
            if verify_artifact_path:
                break
        parameters["verify_artifact_path"] = (
            verify_artifact_path
            or self._default_moonspec_verify_artifact_path(node_id)
        )

    def _ensure_assessment_parameters(
        self,
        *,
        parameters: dict[str, Any],
        node_inputs: Mapping[str, Any],
    ) -> None:
        """Propagate the assessment handoff path so its verdict can be published.

        The initial-assessment agent step writes a structured verdict JSON to
        ``assessment_artifact_path`` in its workspace. Surfacing that path in the
        agent parameters lets ``agent_runtime.publish_artifacts`` publish the JSON
        as a durable MoonMind artifact and hand downstream steps an
        ``assessmentArtifactRef`` — a bridge-compatible verdict channel that does
        not depend on a shared filesystem (the assessment may run on an Omnigent
        host whose workspace the deterministic Jira tools cannot mount).

        Unlike moonspec-verify this applies to any agent skill (the assessment
        step runs as ``auto``); callers gate it away from moonspec-verify steps,
        which merely READ the assessment path, so only the writer publishes.
        """
        if parameters.get("assessment_artifact_path"):
            return
        nested_inputs = node_inputs.get("inputs")
        skill_inputs = nested_inputs if isinstance(nested_inputs, Mapping) else {}
        for source in (node_inputs, skill_inputs):
            for key in ("assessment_artifact_path", "assessmentArtifactPath"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    parameters["assessment_artifact_path"] = value.strip()
                    return

    def _build_agent_execution_request(
        self,
        *,
        node_inputs: dict[str, Any],
        node_id: str,
        tool_name: str,
        resolved_skillset_ref: str | None = None,
        workflow_parameters: Mapping[str, Any] | None = None,
        step_execution: int | None = None,
        queue_order: int | None = None,
        attempt_reason: str = "initial_execution",
    ) -> "AgentExecutionRequest":
        """Build an ``AgentExecutionRequest`` from plan-node inputs and workflow context."""
        node_inputs = self._append_trusted_previous_outputs_to_agent_inputs(node_inputs)
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
        if step_execution is not None and step_execution > 0:
            idempotency_key = step_execution_operation_idempotency_key(
                workflow_id=wf_info.workflow_id,
                run_id=wf_info.run_id,
                logical_step_id=node_id,
                execution_ordinal=step_execution,
                operation="agent_execute",
            )
        else:
            idempotency_key = f"{wf_info.workflow_id}:{node_id}:{wf_info.run_id}"

        workspace_spec: dict[str, Any] = {}
        for source_label, raw_workspace_spec in (
            ("workflow.workspaceSpec", (
                workflow_parameters.get("workspaceSpec")
                if isinstance(workflow_parameters, Mapping)
                else None
            )),
            ("workflow.workspace_spec", (
                workflow_parameters.get("workspace_spec")
                if isinstance(workflow_parameters, Mapping)
                else None
            )),
            ("node.runtime.workspaceSpec", runtime_block.get("workspaceSpec")),
            ("node.runtime.workspace_spec", runtime_block.get("workspace_spec")),
            ("node.workspaceSpec", node_inputs.get("workspaceSpec")),
            ("node.workspace_spec", node_inputs.get("workspace_spec")),
        ):
            if isinstance(raw_workspace_spec, Mapping):
                workspace_spec.update(
                    self._json_mapping(
                        raw_workspace_spec,
                        path=source_label,
                    )
                )
        for ws_key in (
            "repository",
            "repo",
            "startingBranch",
            "targetBranch",
            "baseBranch",
            "publishBaseBranch",
            "branch",
        ):
            ws_val = node_inputs.get(ws_key)
            if ws_val is not None:
                workspace_spec[ws_key] = ws_val

        parameters: dict[str, Any] = {}
        for param_key in (
            "model",
            "requestedModel",
            "modelTier",
            "tierFallback",
            "tierPreview",
            "effort",
            "publishMode",
            "commitMessage",
            "publishBaseBranch",
            "allowed_tools",
            "priority",
            "stepCount",
            "maxAttempts",
            "steps",
            "storyOutput",
            "story_output",
            "storyBreakdownPath",
            "story_breakdown_path",
            "storyBreakdownMarkdownPath",
            "story_breakdown_markdown_path",
        ):
            param_val = runtime_block.get(param_key)
            if param_val is None:
                param_val = node_inputs.get(param_key)
            if param_val is None and workflow_parameters is not None:
                param_val = workflow_parameters.get(param_key)
            if param_val is not None:
                parameters[param_key] = param_val
        if (
            self._workflow_patch_enabled(
                RUN_PR_RESOLVER_CONTINUATION_IDENTITY_PATCH
            )
            and isinstance(workflow_parameters, Mapping)
        ):
            merge_gate = workflow_parameters.get("mergeGate")
            if isinstance(merge_gate, Mapping):
                parameters["mergeGate"] = self._json_mapping(
                    merge_gate,
                    path="workflow.mergeGate",
                )
        raw_omnigent_parameters = runtime_block.get("omnigent")
        if raw_omnigent_parameters is None:
            raw_omnigent_parameters = node_inputs.get("omnigent")
        if raw_omnigent_parameters is None and isinstance(workflow_parameters, Mapping):
            raw_omnigent_parameters = workflow_parameters.get("omnigent")
        if raw_omnigent_parameters is not None:
            if not isinstance(raw_omnigent_parameters, Mapping):
                raise ValueError(f"node[{node_id}].omnigent must be an object")
            if self._workflow_patch_enabled(
                RUN_OMNIGENT_AUTHORED_SELECTION_COMPILER_PATCH
            ):
                parameters["omnigent"] = self._compile_authored_omnigent_selection(
                    raw_omnigent_parameters,
                    path=f"node[{node_id}].omnigent",
                )
            else:
                # Preserve the exact historical activity input for workflows whose
                # histories predate the product compiler boundary.
                parameters["omnigent"] = self._json_mapping(
                    raw_omnigent_parameters,
                    path=f"node[{node_id}].omnigent",
                )
        if agent_id == "omnigent" and execution_profile_ref:
            profile_snapshots = getattr(self, "_profile_snapshots", None)
            snapshot = (
                profile_snapshots.get(execution_profile_ref)
                if isinstance(profile_snapshots, Mapping)
                else None
            )
            if isinstance(snapshot, Mapping):
                profile_agent_name = self._coerce_text(
                    snapshot.get("agentName") or snapshot.get("agent_name"),
                    max_chars=255,
                )
                if not profile_agent_name and snapshot.get("runtime_id") == "codex_cli":
                    profile_agent_name = "codex"
                if profile_agent_name:
                    omnigent_parameters = (
                        dict(parameters.get("omnigent"))
                        if isinstance(parameters.get("omnigent"), Mapping)
                        else {}
                    )
                    agent_parameters = (
                        dict(omnigent_parameters.get("agent"))
                        if isinstance(omnigent_parameters.get("agent"), Mapping)
                        else {}
                    )
                    agent_parameters.setdefault("agentName", profile_agent_name)
                    omnigent_parameters["agent"] = agent_parameters
                    parameters["omnigent"] = omnigent_parameters
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
        compact_skill_payload: dict[str, Any] = {}
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
            raw_skill_payload = node_inputs.get("skill")
            if isinstance(raw_skill_payload, Mapping):
                for key in (
                    "name",
                    "contentRef",
                    "contentDigest",
                    "inputContractDigest",
                    "inputs",
                    "sideEffect",
                ):
                    if key in raw_skill_payload:
                        compact_skill_payload[key] = self._json_value(
                            raw_skill_payload[key],
                            path=f"node[{node_id}].skill.{key}",
                        )
            if compact_skill_payload:
                compact_skill_payload.setdefault("name", selected_skill)
                parameters["skill"] = dict(compact_skill_payload)
            if selected_skill.lower() == "moonspec-verify":
                self._ensure_moonspec_verify_parameters(
                    parameters=parameters,
                    node_inputs=node_inputs,
                    node_id=node_id,
                )
        assessment_parameter_injection_needed = False
        nested_inputs = node_inputs.get("inputs")
        skill_inputs = nested_inputs if isinstance(nested_inputs, Mapping) else {}
        for source in (node_inputs, skill_inputs):
            for key in ("assessment_artifact_path", "assessmentArtifactPath"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    assessment_parameter_injection_needed = True
                    break
            if assessment_parameter_injection_needed:
                break
        # The initial-assessment step (skill: auto) writes the verdict artifact;
        # moonspec-verify steps only read it, so exclude them from publication.
        # Guard the parameter injection so in-flight histories that already
        # scheduled an agent step keep replaying with their recorded command
        # arguments.
        if (
            assessment_parameter_injection_needed
            and workflow.patched(RUN_ASSESSMENT_PARAMETER_INJECTION_PATCH)
            and str(selected_skill or "").strip().lower() != "moonspec-verify"
        ):
            self._ensure_assessment_parameters(
                parameters=parameters,
                node_inputs=node_inputs,
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

        step_execution = self._step_execution_for(node_id)
        if step_execution is not None:
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
            step_ledger_payload = {
                "logicalStepId": node_id,
                "scope": "step",
            }
            if workflow.patched(RUN_STEP_EXECUTION_NAMING_PATCH):
                step_ledger_payload["executionOrdinal"] = step_execution
            else:
                step_ledger_payload["attempt"] = step_execution
            moonmind_payload["stepLedger"] = step_ledger_payload
            metadata_payload["moonmind"] = moonmind_payload
            parameters["metadata"] = metadata_payload

        node_for_remediation_metadata = {"id": node_id, "inputs": node_inputs}
        remediation_role = self._moonspec_step_role(node_for_remediation_metadata)
        if remediation_role in {
            "moonspec-remediation",
            "moonspec-verification-gate",
        }:
            attempt, max_attempts = self._moonspec_remediation_attempt_metadata(
                node_for_remediation_metadata
            )
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
            cadence_payload: dict[str, Any] = {
                "cadence": "attempt_scoped_remediation_verification",
                "role": remediation_role,
            }
            if attempt is not None:
                cadence_payload["attempt"] = attempt
                cadence_payload["attemptArtifactPath"] = (
                    f"reports/remediation_attempt-{attempt}.json"
                )
                cadence_payload["verificationArtifactPath"] = (
                    f"reports/remediation_verification-{attempt}.json"
                )
            if max_attempts is not None:
                cadence_payload["maxAttempts"] = max_attempts
            verify_artifact_path = (
                parameters.get("verify_artifact_path")
                or parameters.get("verifyArtifactPath")
                or node_inputs.get("verify_artifact_path")
                or node_inputs.get("verifyArtifactPath")
            )
            if isinstance(verify_artifact_path, str) and verify_artifact_path.strip():
                cadence_payload["latestVerificationPath"] = (
                    verify_artifact_path.strip()
                )
            moonmind_payload["remediationCadence"] = cadence_payload
            metadata_payload["moonmind"] = moonmind_payload
            parameters["metadata"] = metadata_payload

        input_refs = node_inputs.get("inputRefs") or []
        task_payload_for_context: Mapping[str, Any] | None = None
        prepared_context = None
        if isinstance(workflow_parameters, Mapping):
            task_payload = workflow_parameters.get("task")
            if isinstance(task_payload, Mapping):
                task_payload_for_context = task_payload
                prepared_manifest = build_prepared_input_manifest(task_payload)
                if prepared_manifest.has_entries:
                    prepared_context = select_step_prepared_context(
                        prepared_manifest,
                        logical_step_id=node_id,
                    )
                    if prepared_context.input_refs:
                        if agent_kind != "managed":
                            input_refs = merge_prepared_raw_input_refs(
                                input_refs,
                                prepared_context,
                            )
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
                        moonmind_payload["preparedContext"] = (
                            prepared_context.to_metadata()
                        )
                        metadata_payload["moonmind"] = moonmind_payload
                        parameters["metadata"] = metadata_payload

        runtime_selection = {
            "runtimeId": agent_id,
            "agentKind": agent_kind,
        }
        if parameters.get("model") is not None:
            runtime_selection["model"] = parameters["model"]
        if parameters.get("effort") is not None:
            runtime_selection["effort"] = parameters["effort"]
        if execution_profile_ref:
            runtime_selection["executionProfileRef"] = execution_profile_ref
        if selected_skill:
            runtime_selection["skillId"] = selected_skill
        skill_source_policy: dict[str, Any] = {
            "repoSkills": "resolver_policy_enforced",
            "localSkills": "resolver_policy_enforced",
            "checkedInSkillMutation": "prohibited",
        }
        if resolved_skillset_ref:
            skill_source_policy["resolvedSkillsetRef"] = resolved_skillset_ref
        if selected_skill:
            skill_source_policy["selectedSkill"] = selected_skill
        execution_ordinal = step_execution or 1
        context_workspace = self._step_execution_workspace(
            node_id,
            attempt=execution_ordinal,
            source_execution_ordinal=self._step_execution_source_identity(
                node_id,
                attempt=execution_ordinal,
            ),
        )
        context_policy_refs: dict[str, Any] = {
            "skillSourcePolicy": skill_source_policy,
        }
        if execution_profile_ref:
            context_policy_refs["executionProfileRef"] = execution_profile_ref
        runtime_context_policy = (
            "fresh_agent_run"
            if agent_kind == "managed"
            else "external_provider_continuation"
        )
        retrieval_context = None
        retrieval_manifest_artifact = None
        memory_proposals = None
        memory_context = None
        fix_patterns = None
        if isinstance(task_payload_for_context, Mapping):
            raw_retrieval_context = (
                task_payload_for_context.get("attemptRetrieval")
                or task_payload_for_context.get("retrievalManifest")
                or task_payload_for_context.get("retrieval")
            )
            if isinstance(raw_retrieval_context, Mapping):
                retrieval_context = raw_retrieval_context
                retrieval_manifest_artifact = build_durable_retrieval_manifest_artifact(
                    retrieval_context
                )
            raw_memory_proposals = (
                task_payload_for_context.get("memoryProposals")
                or task_payload_for_context.get("memoryEffects")
                or task_payload_for_context.get("memory")
            )
            if isinstance(raw_memory_proposals, Sequence) and not isinstance(
                raw_memory_proposals,
                (str, bytes, bytearray),
            ):
                memory_proposals = [
                    proposal
                    for proposal in raw_memory_proposals
                    if isinstance(proposal, Mapping)
                ]
            raw_memory_context = (
                task_payload_for_context.get("memoryContext")
                or task_payload_for_context.get("memory_context")
            )
            if isinstance(raw_memory_context, Mapping):
                memory_context = raw_memory_context
            raw_fix_patterns = (
                task_payload_for_context.get("matchedFixPatterns")
                or task_payload_for_context.get("fixPatterns")
            )
            if isinstance(raw_fix_patterns, Sequence) and not isinstance(
                raw_fix_patterns,
                (str, bytes, bytearray),
            ):
                fix_patterns = [
                    pattern
                    for pattern in raw_fix_patterns
                    if isinstance(pattern, Mapping)
                ]
        context_kwargs = self._step_execution_context_kwargs(
            node_id,
            attempt=execution_ordinal,
            workspace=context_workspace,
            policy_refs=context_policy_refs,
        )
        branch_turn_payload = None
        branch_projection = None
        branch_artifact_manifest = None
        branch_instruction_ref = None
        branch_id = None
        branch_turn_id = None
        metadata_payload_for_branch = (
            parameters.get("metadata")
            if isinstance(parameters.get("metadata"), dict)
            else {}
        )
        candidate_branch_turn_payload = self._checkpoint_branch_turn_payload(
            node_inputs=node_inputs,
            runtime_block=runtime_block,
            metadata_payload=metadata_payload_for_branch,
        )
        if candidate_branch_turn_payload is not None and self._workflow_patch_enabled(
            RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH
        ):
            branch_turn_payload = candidate_branch_turn_payload
        if branch_turn_payload is not None:
            source_checkpoint = self._checkpoint_branch_turn_source_checkpoint(
                branch_turn_payload,
                node_id=node_id,
                wf_info=wf_info,
                context_workspace=context_workspace,
            )
            branch_id = self._checkpoint_branch_turn_text(
                branch_turn_payload,
                "branchId",
                "branch_id",
            )
            branch_turn_id = self._checkpoint_branch_turn_text(
                branch_turn_payload,
                "branchTurnId",
                "branch_turn_id",
            )
            instruction_ref = self._checkpoint_branch_turn_instruction_ref(
                branch_turn_payload,
                node_inputs=node_inputs,
            )
            branch_instruction_ref = instruction_ref
            instruction_digest = self._checkpoint_branch_turn_text(
                branch_turn_payload,
                "instructionDigest",
                "instruction_digest",
                "instructionArtifactDigest",
                "instruction_artifact_digest",
            )
            runtime_policy = (
                self._checkpoint_branch_turn_text(
                    branch_turn_payload,
                    "runtimeContextPolicy",
                    "runtime_context_policy",
                )
                or runtime_context_policy
            )
            attempt_context = build_branch_turn_context_bundle(
                workflow_id=wf_info.workflow_id,
                run_id=wf_info.run_id,
                logical_step_id=node_id,
                execution_ordinal=execution_ordinal,
                branch_id=branch_id or "",
                branch_turn_id=branch_turn_id or "",
                source_checkpoint=source_checkpoint,
                instruction_artifact_ref=instruction_ref or "",
                instruction_digest=instruction_digest or "",
                runtime_context_policy=runtime_policy,
                workspace_policy=self._checkpoint_branch_turn_workspace_policy(
                    branch_turn_payload,
                    context_kwargs=context_kwargs,
                ),
                task_input_snapshot_ref=context_kwargs.get("task_input_snapshot_ref"),
                plan_ref=context_kwargs.get("plan_ref"),
                plan_digest=context_kwargs.get("plan_digest"),
                initial_instruction_ref=self._checkpoint_branch_turn_text(
                    branch_turn_payload,
                    "initialInstructionRef",
                    "initial_instruction_ref",
                    "initialInstructionArtifactRef",
                    "initial_instruction_artifact_ref",
                ),
                initial_instruction_digest=self._checkpoint_branch_turn_text(
                    branch_turn_payload,
                    "initialInstructionDigest",
                    "initial_instruction_digest",
                    "initialInstructionArtifactDigest",
                    "initial_instruction_artifact_digest",
                ),
                parent_branch_id=self._checkpoint_branch_turn_text(
                    branch_turn_payload,
                    "parentBranchId",
                    "parent_branch_id",
                ),
                parent_turn_id=self._checkpoint_branch_turn_text(
                    branch_turn_payload,
                    "parentTurnId",
                    "parent_turn_id",
                ),
                label=self._checkpoint_branch_turn_text(
                    branch_turn_payload,
                    "label",
                ),
                git_work_branch=self._checkpoint_branch_turn_text(
                    branch_turn_payload,
                    "gitWorkBranch",
                    "git_work_branch",
                ),
                prepared_context=prepared_context,
                workspace_baseline=context_kwargs.get("workspace_baseline"),
                prior_evidence_refs=context_kwargs.get("prior_evidence_refs"),
                bounded_summaries=context_kwargs.get("bounded_summaries"),
                branch_comparison_refs=context_kwargs.get("branch_comparison_refs"),
                runtime_selection=runtime_selection,
                policy_refs=context_kwargs.get("policy_refs"),
                retrieval=retrieval_context,
                memory_proposals=memory_proposals,
                memory_context=memory_context,
                fix_patterns=fix_patterns,
            )
            branch_projection = branch_turn_step_execution_manifest_projection(
                attempt_context
            ).get("branch")
            if isinstance(branch_projection, Mapping):
                self._step_execution_branch_projections[
                    (node_id, execution_ordinal)
                ] = dict(branch_projection)
            artifact_refs: dict[str, str] = {}
            source_ref = (
                attempt_context.branch.get("rootCheckpointRef")
                if attempt_context.branch
                else None
            )
            if source_ref:
                artifact_refs["input.branch.root_checkpoint.json"] = str(source_ref)
            initial_ref = self._checkpoint_branch_turn_text(
                branch_turn_payload,
                "initialInstructionRef",
                "initial_instruction_ref",
                "initialInstructionArtifactRef",
                "initial_instruction_artifact_ref",
            )
            if initial_ref:
                artifact_refs["input.branch.initial_instructions.md"] = initial_ref
            if instruction_ref:
                artifact_refs["input.branch_turn.instructions.md"] = instruction_ref
            branch_artifact_manifest = build_branch_turn_artifact_manifest(
                branch_id=branch_id or "",
                branch_turn_id=branch_turn_id or "",
                context_bundle=attempt_context,
                artifact_refs=artifact_refs,
            )
            self._step_execution_branch_artifact_manifests[
                (node_id, execution_ordinal)
            ] = dict(branch_artifact_manifest)
        else:
            attempt_context = build_execution_context_bundle(
                workflow_id=wf_info.workflow_id,
                run_id=wf_info.run_id,
                logical_step_id=node_id,
                execution_ordinal=execution_ordinal,
                prepared_context=prepared_context,
                **context_kwargs,
                runtime_selection=runtime_selection,
                retrieval=retrieval_context,
                memory_proposals=memory_proposals,
                memory_context=memory_context,
                fix_patterns=fix_patterns,
            )
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
        moonmind_payload["executionContext"] = attempt_context.model_dump(
            by_alias=True,
            exclude_none=True,
        )
        moonmind_payload["stepExecutionManifestProjection"] = (
            attempt_context.to_manifest_projection()
        )
        if isinstance(branch_projection, Mapping) and isinstance(
            branch_artifact_manifest,
            Mapping,
        ):
            branch_context = dict(attempt_context.branch or {})
            checkpoint_branch_turn = {
                "branchId": branch_context.get("branchId"),
                "branchTurnId": branch_context.get("branchTurnId"),
                "sourceCheckpointRef": branch_context.get("sourceCheckpointRef"),
                "sourceCheckpointDigest": branch_context.get(
                    "sourceCheckpointDigest"
                ),
                "rootCheckpointRef": branch_context.get("rootCheckpointRef"),
                "contextBundleRef": attempt_context.context_bundle_ref,
                "contextBundleDigest": attempt_context.context_bundle_digest,
                "builderVersion": attempt_context.builder_version,
                "artifactManifestDigest": branch_artifact_manifest.get(
                    "artifactManifestDigest"
                ),
            }
            moonmind_payload["checkpointBranchTurn"] = {
                key: value
                for key, value in checkpoint_branch_turn.items()
                if value is not None
            }
            moonmind_payload["checkpointBranchTurnArtifactManifest"] = dict(
                branch_artifact_manifest
            )
        if retrieval_manifest_artifact is not None:
            moonmind_payload["retrievalManifestArtifact"] = retrieval_manifest_artifact
            self._step_execution_retrieval_manifest_artifacts[
                (node_id, execution_ordinal)
            ] = dict(retrieval_manifest_artifact)
        if queue_order is not None:
            moonmind_payload["queueOrder"] = queue_order
        workflow_start_time = getattr(wf_info, "start_time", None)
        if isinstance(workflow_start_time, datetime):
            moonmind_payload["queuedAt"] = workflow_start_time.isoformat()
        metadata_payload["moonmind"] = moonmind_payload
        parameters["metadata"] = metadata_payload
        projection_context = attempt_context.to_manifest_projection().get("context")
        if isinstance(projection_context, Mapping):
            self._step_execution_context_projections[
                (node_id, execution_ordinal)
            ] = dict(projection_context)
        step_execution_identity = StepExecutionIdentityModel(
            workflowId=wf_info.workflow_id,
            runId=wf_info.run_id,
            logicalStepId=node_id,
            executionOrdinal=step_execution or 1,
        )
        step_execution_payload: dict[str, Any] = {
            "schemaVersion": "v1",
            "workflowId": wf_info.workflow_id,
            "runId": wf_info.run_id,
            "logicalStepId": node_id,
            "executionOrdinal": step_execution_identity.execution_ordinal,
            "stepExecutionId": build_step_execution_id(step_execution_identity),
            "reason": "checkpoint_branch" if branch_projection else attempt_reason,
            "runtimeContextPolicy": (
                attempt_context.runtime_context_policy or runtime_context_policy
            ),
            "contextBundleRef": attempt_context.context_bundle_ref,
            "contextBundleDigest": attempt_context.context_bundle_digest,
            "preparedInputRefs": list(attempt_context.prepared_input_refs),
            "resolvedSkillsetRef": resolved_skillset_ref,
            "runtimeSelection": dict(runtime_selection),
            "skillSourcePolicy": skill_source_policy,
        }
        if attempt_context.retrieval_manifest_ref:
            step_execution_payload["retrievalManifestRef"] = (
                attempt_context.retrieval_manifest_ref
            )
        if attempt_context.memory_manifest_ref:
            step_execution_payload["memoryManifestRef"] = (
                attempt_context.memory_manifest_ref
            )
        if attempt_context.memory_context_ref:
            step_execution_payload["memoryContextRef"] = (
                attempt_context.memory_context_ref
            )
        if isinstance(branch_projection, Mapping):
            step_execution_payload["branch"] = dict(branch_projection)
        if isinstance(branch_artifact_manifest, Mapping):
            step_execution_payload["branchArtifactManifest"] = dict(
                branch_artifact_manifest
            )
        if agent_kind == "managed":
            session_reset = self._managed_reattempt_session_reset_evidence(
                node_id,
                agent_id=agent_id,
                execution_ordinal=step_execution_identity.execution_ordinal,
            )
            if session_reset:
                step_execution_payload["runtimeSessionReset"] = session_reset
        else:
            step_execution_payload["externalProviderContinuation"] = (
                self._external_provider_continuation_evidence(
                    node_id,
                    execution_ordinal=step_execution_identity.execution_ordinal,
                    context_bundle_ref=attempt_context.context_bundle_ref,
                    prepared_input_refs=attempt_context.prepared_input_refs,
                )
            )

        runtime_command_payload = self._normalized_runtime_command_payload(
            node_inputs.get("runtimeCommand") or node_inputs.get("runtime_command")
        )

        request_instruction_ref = (
            branch_instruction_ref
            or node_inputs.get("instructions")
            or node_inputs.get("instructionRef")
        )
        if (
            branch_instruction_ref
            and agent_kind == "external"
            and _normalize_agent_runtime_id(agent_id) == "omnigent"
            and self._workflow_patch_enabled(
                RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH
            )
        ):
            if not branch_id or not branch_turn_id:
                raise ValueError(
                    "Omnigent checkpoint branch turn requires branchId and branchTurnId"
                )
            self._apply_omnigent_checkpoint_branch_turn_prompt(
                parameters=parameters,
                instruction_ref=branch_instruction_ref,
            )
            if isinstance(branch_projection, Mapping):
                git_work_branch = branch_projection.get("gitWorkBranch")
                if isinstance(git_work_branch, str) and git_work_branch.strip():
                    workspace_spec["branch"] = git_work_branch.strip()
                    workspace_spec["startingBranch"] = git_work_branch.strip()
            request_instruction_ref = None
            idempotency_key = (
                f"{wf_info.workflow_id}:{branch_id}:{branch_turn_id}:omnigent"
            )

        # Repository work delegated through Omnigent carries durable ownership,
        # never a worker or daemon filesystem path. The activity resolves this
        # identity after validating the current workflow and step execution.
        if (
            agent_kind == "external"
            and _normalize_agent_runtime_id(agent_id) == "omnigent"
        ):
            locator_identity = (
                f"{wf_info.workflow_id}:{step_execution_payload['stepExecutionId']}"
            )
            workspace_spec["workspaceLocator"] = {
                "kind": "sandbox",
                "workspaceId": hashlib.sha256(
                    locator_identity.encode("utf-8")
                ).hexdigest()[:24],
                "relativePath": "repo",
            }

        terminal_contract_payload: dict[str, Any] | None = None
        resolved_terminal_contract = (
            self._resolved_skill_terminal_contract_by_step.get(node_id)
        )
        if isinstance(resolved_terminal_contract, Mapping):
            terminal_contract_payload = {
                **dict(resolved_terminal_contract),
                "executionRef": step_execution_payload["stepExecutionId"],
            }
        else:
            side_effect = compact_skill_payload.get("sideEffect")
            if isinstance(side_effect, Mapping):
                contract_id = str(
                    side_effect.get("terminalContractId") or ""
                ).strip()
                outcome_artifact = str(
                    side_effect.get("outcomeArtifact") or ""
                ).strip()
                expected_schema_version = str(
                    side_effect.get("terminalSchemaVersion") or ""
                ).strip()
                if contract_id and outcome_artifact and expected_schema_version:
                    terminal_contract_payload = {
                        "contractId": contract_id,
                        "owner": "agent",
                        "evidenceKind": "workspace_json",
                        "relativePath": outcome_artifact,
                        "expectedSchemaVersion": expected_schema_version,
                        "executionRef": step_execution_payload[
                            "stepExecutionId"
                        ],
                    }

        terminal_continuation_authority = None
        if (
            selected_skill == "pr-resolver"
            and terminal_contract_payload is not None
            and terminal_contract_payload.get("contractId")
            == "pr_resolver_terminal.v1"
            and self._is_merge_automation_gated(parameters)
            and workflow.patched(RUN_PR_RESOLVER_OWNED_CONTINUATION_PATCH)
        ):
            parent_info = workflow.info().parent
            terminal_continuation_authority = {
                "schemaVersion": "terminal-continuation-authority/v1",
                "gateType": "merge_automation",
                "ownerWorkflowId": parent_info.workflow_id,
                "ownerRunId": parent_info.run_id,
                "ownerWorkflowType": "MoonMind.MergeAutomation",
                "allowedActions": ["reenter_gate"],
                "source": "validated_temporal_parent",
            }

        return AgentExecutionRequest(
            agent_kind=agent_kind,
            agent_id=agent_id,
            execution_profile_ref=execution_profile_ref,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            instruction_ref=request_instruction_ref,
            runtime_command=runtime_command_payload,
            step_execution=step_execution_payload,
            resolved_skillset_ref=resolved_skillset_ref,
            terminal_contract=terminal_contract_payload,
            terminal_continuation_authority=terminal_continuation_authority,
            input_refs=input_refs,
            workspace_spec=workspace_spec,
            skill=compact_skill_payload,
            parameters=parameters,
            timeout_policy=node_inputs.get("timeoutPolicy") or {},
            retry_policy=node_inputs.get("retryPolicy") or {},
            approval_policy=node_inputs.get("approvalPolicy") or {},
            callback_policy=node_inputs.get("callbackPolicy") or {},
            profile_selector=profile_selector,
        )

    async def _request_with_persisted_retrieval_ref(
        self,
        request: AgentExecutionRequest,
        *,
        logical_step_id: str,
        attempt: int,
    ) -> AgentExecutionRequest:
        retrieval_artifact = self._step_execution_retrieval_manifest_artifacts.get(
            (logical_step_id, attempt)
        )
        branch_artifact_manifest = self._step_execution_branch_artifact_manifests.get(
            (logical_step_id, attempt)
        )
        if not isinstance(retrieval_artifact, Mapping) and not isinstance(
            branch_artifact_manifest,
            Mapping,
        ):
            return request

        update_payload: dict[str, Any] = {}
        metadata = dict(request.parameters.get("metadata") or {})
        moonmind_metadata = dict(metadata.get("moonmind") or {})
        if isinstance(retrieval_artifact, Mapping):
            persisted_ref = retrieval_artifact.get("persistedArtifactRef")
        else:
            persisted_ref = None
        execution_context = dict(moonmind_metadata.get("executionContext") or {})
        current_context_bundle = None
        if (
            isinstance(persisted_ref, str)
            and persisted_ref.strip()
            and execution_context
        ):
            # ``retrievalManifestRef`` is part of the context-bundle digest, so
            # rebuild through the model to recompute ``contextBundleRef`` /
            # ``contextBundleDigest`` instead of swapping the ref in place and
            # leaving a stale, internally inconsistent digest.
            rebuilt_context = ExecutionContextBundle.model_validate(
                execution_context
            ).with_retrieval_manifest_ref(persisted_ref)
            moonmind_metadata["executionContext"] = rebuilt_context.model_dump(
                by_alias=True,
                exclude_none=True,
            )
            moonmind_metadata["stepExecutionManifestProjection"] = (
                rebuilt_context.to_manifest_projection()
            )
            current_context_bundle = rebuilt_context
            retrieval_manifest_artifact = dict(
                moonmind_metadata.get("retrievalManifestArtifact") or {}
            )
            retrieval_manifest_artifact["persistedArtifactRef"] = persisted_ref
            moonmind_metadata["retrievalManifestArtifact"] = retrieval_manifest_artifact
        elif execution_context:
            current_context_bundle = ExecutionContextBundle.model_validate(
                execution_context
            )
        branch_artifact_ref = None
        if isinstance(branch_artifact_manifest, Mapping):
            candidate_ref = branch_artifact_manifest.get("persistedArtifactRef")
            if isinstance(candidate_ref, str) and candidate_ref.strip():
                branch_artifact_ref = candidate_ref.strip()
        if branch_artifact_ref and current_context_bundle is not None:
            branch_turn = dict(moonmind_metadata.get("checkpointBranchTurn") or {})
            branch_turn["contextBundleRef"] = current_context_bundle.context_bundle_ref
            branch_turn["contextBundleDigest"] = (
                current_context_bundle.context_bundle_digest
            )
            branch_turn["artifactManifestRef"] = branch_artifact_ref
            existing_branch_manifest = dict(
                moonmind_metadata.get("checkpointBranchTurnArtifactManifest")
                or branch_artifact_manifest
            )
            actual_refs = {
                str(entry.get("name")): str(entry.get("artifactRef"))
                for entry in existing_branch_manifest.get("artifacts", ())
                if isinstance(entry, Mapping)
                and str(entry.get("name") or "").strip()
                and str(entry.get("artifactRef") or "").strip()
            }
            branch_id = branch_turn.get("branchId") or existing_branch_manifest.get(
                "branchId"
            )
            branch_turn_id = branch_turn.get(
                "branchTurnId"
            ) or existing_branch_manifest.get("branchTurnId")
            if branch_id and branch_turn_id:
                branch_manifest_payload = build_branch_turn_artifact_manifest(
                    branch_id=str(branch_id),
                    branch_turn_id=str(branch_turn_id),
                    context_bundle=current_context_bundle,
                    artifact_refs=actual_refs,
                )
            else:
                branch_manifest_payload = existing_branch_manifest
                branch_manifest_payload["contextBundleRef"] = (
                    current_context_bundle.context_bundle_ref
                )
                branch_manifest_payload["contextBundleDigest"] = (
                    current_context_bundle.context_bundle_digest
                )
            previous_branch_payload = {
                key: value
                for key, value in dict(branch_artifact_manifest).items()
                if key != "persistedArtifactRef"
            }
            if branch_manifest_payload != previous_branch_payload:
                self._step_execution_branch_artifact_manifests[
                    (logical_step_id, attempt)
                ] = dict(branch_manifest_payload)
                persisted_branch_ref = (
                    await self._persist_step_execution_branch_artifact_manifest(
                        logical_step_id,
                        attempt=attempt,
                    )
                )
                if persisted_branch_ref:
                    branch_artifact_ref = persisted_branch_ref
            branch_manifest_payload["persistedArtifactRef"] = branch_artifact_ref
            branch_turn["artifactManifestDigest"] = branch_manifest_payload.get(
                "artifactManifestDigest"
            )
            branch_turn["artifactManifestRef"] = branch_artifact_ref
            moonmind_metadata["checkpointBranchTurn"] = branch_turn
            moonmind_metadata["checkpointBranchTurnArtifactManifest"] = (
                branch_manifest_payload
            )
            self._step_execution_branch_artifact_manifests[
                (logical_step_id, attempt)
            ] = dict(branch_manifest_payload)
            projection = dict(
                moonmind_metadata.get("stepExecutionManifestProjection") or {}
            )
            projection_context = dict(projection.get("context") or {})
            if projection_context:
                projection_branch = dict(projection_context.get("branch") or {})
                if projection_branch:
                    projection_branch["artifactManifestRef"] = branch_artifact_ref
                    projection_context["branch"] = projection_branch
                projection_context["branchArtifactManifestRef"] = branch_artifact_ref
                projection["context"] = projection_context
                moonmind_metadata["stepExecutionManifestProjection"] = projection
        metadata["moonmind"] = moonmind_metadata
        parameters = dict(request.parameters)
        parameters["metadata"] = metadata
        update_payload["parameters"] = parameters

        if (
            request.step_execution is not None
            and isinstance(persisted_ref, str)
            and persisted_ref.strip()
        ):
            update_payload["step_execution"] = request.step_execution.model_copy(
                update={"retrieval_manifest_ref": persisted_ref}
            )

        return request.model_copy(update=update_payload)

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
                        if self._workflow_is_replaying():
                            return profile_id
                        raise ValueError(
                            "Inherited execution_profile_ref '%s' targets runtime "
                            "'%s' but child runtime is '%s'."
                            % (
                                profile_id,
                                self._managed_runtime_id(source_agent_id),
                                self._managed_runtime_id(agent_id),
                            )
                        )
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
            raise ValueError(
                "%s execution_profile_ref '%s' is not a known profile for this "
                "runtime." % (source_label, profile_id)
            )
        if not isinstance(profile_snapshots, Mapping):
            return profile_id
        snapshot = profile_snapshots.get(profile_id)
        if not isinstance(snapshot, Mapping):
            return profile_id
        if not provider_profile_launch_ready_from_payload(dict(snapshot)):
            raise ValueError(
                "%s execution_profile_ref '%s' is not launch-ready for this "
                "runtime." % (source_label, profile_id)
            )
        runtime_id = self._coerce_text(snapshot.get("runtime_id"), max_chars=160)
        if not runtime_id or not agent_id:
            return profile_id
        child_runtime_id = self._managed_runtime_id(agent_id)
        compatible_runtime_ids = {child_runtime_id}
        if child_runtime_id == "omnigent":
            compatible_runtime_ids.add("codex_cli")
        if runtime_id not in compatible_runtime_ids:
            if self._workflow_is_replaying():
                return profile_id
            raise ValueError(
                "%s execution_profile_ref '%s' belongs to runtime '%s' but child "
                "runtime is '%s'."
                % (source_label, profile_id, runtime_id, child_runtime_id)
            )
        return profile_id

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

    @staticmethod
    def _workflow_patch_enabled(patch_id: str) -> bool:
        try:
            return bool(workflow.patched(patch_id))
        except Exception as exc:
            if exc.__class__.__name__ == "_NotInWorkflowEventLoopError":
                return False
            raise

    @staticmethod
    def _workflow_is_replaying() -> bool:
        try:
            return bool(workflow.unsafe.is_replaying())
        except Exception as exc:
            if exc.__class__.__name__ == "_NotInWorkflowEventLoopError":
                return False
            raise

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
            or runtime_block.get("agentId")
            or runtime_block.get("agent_id")
            or node_inputs.get("targetRuntime")
            or node_inputs.get("agentId")
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

    async def _execute_container_job_tool(
        self,
        *,
        node_inputs: Mapping[str, Any],
        node_id: str,
        execution_ordinal: int,
    ) -> dict[str, Any]:
        """Submit once, then durably poll the separately owned job record."""

        info = workflow.info()
        owner = OwnerIdentity(
            principalId=self._principal(), principalType="service"
        )
        spec = node_inputs.get("spec")
        if not isinstance(spec, Mapping):
            raise ValueError("container.run_job inputs.spec is required")
        resolved_spec = dict(spec)
        if "workspaceRef" not in resolved_spec:
            runtime_id = self._managed_runtime_id(self._target_runtime or "")
            if not runtime_id:
                raise ValueError(
                    "container.run_job requires workspaceRef when no managed runtime is selected"
                )
            resolved_spec["workspaceRef"] = {
                "kind": "managed_runtime",
                "runtimeId": runtime_id,
                "agentRunId": info.workflow_id,
                "relativePath": "repo",
            }
        request = ContainerJobSubmitRequest.model_validate(
            {
                "contractVersion": node_inputs.get("contractVersion", "v1"),
                "idempotencyKey": node_inputs.get("idempotencyKey"),
                "source": {
                    "source": "workflow",
                    "callerRequestId": node_inputs.get("callerRequestId"),
                    "workflowId": info.workflow_id,
                    "runId": info.run_id,
                    "stepId": node_id,
                },
                "spec": resolved_spec,
            }
        )
        owner_payload = owner.model_dump(mode="json", by_alias=True)
        request_payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        submit_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("container_job.submit")
        accepted = await workflow.execute_activity(
            submit_route.activity_type,
            {"owner": owner_payload, "request": request_payload},
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
            **self._execute_kwargs_for_route(submit_route),
        )
        job_id = str(self._get_from_result(accepted, "jobId") or "")
        if not job_id:
            raise ValueError("container-job submission returned no jobId")

        status_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("container_job.status")
        terminal_states = {
            ContainerJobState.SUCCEEDED.value,
            ContainerJobState.FAILED.value,
            ContainerJobState.CANCELED.value,
            ContainerJobState.TIMED_OUT.value,
            ContainerJobState.REJECTED.value,
        }
        snapshot: Any = None
        while True:
            if self._cancel_requested:
                cancel_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("container_job.cancel")
                await workflow.execute_activity(
                    cancel_route.activity_type,
                    {
                        "owner": owner_payload,
                        "jobId": job_id,
                        "request": {
                            "idempotencyKey": (
                                f"{info.workflow_id}:{node_id}:{execution_ordinal}:cancel"
                            )
                        },
                    },
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    **self._execute_kwargs_for_route(cancel_route),
                )
                return {"status": "CANCELLED", "outputs": {"jobId": job_id, "state": "canceling"}}
            snapshot = await workflow.execute_activity(
                status_route.activity_type,
                {"owner": owner_payload, "jobId": job_id},
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
                **self._execute_kwargs_for_route(status_route),
            )
            state = str(self._get_from_result(snapshot, "state") or "")
            if state in terminal_states:
                break
            await workflow.sleep(timedelta(seconds=5))

        terminal = self._get_from_result(snapshot, "terminal")
        outputs = {
            "jobId": job_id,
            "state": str(self._get_from_result(snapshot, "state") or ""),
            "logsRef": self._get_from_result(snapshot, "logsRef"),
            "artifactsRef": self._get_from_result(snapshot, "artifactsRef"),
        }
        if isinstance(terminal, Mapping):
            outputs.update(
                {
                    "exitCode": terminal.get("exitCode"),
                    "failureClass": terminal.get("failureClass"),
                    "summary": terminal.get("message"),
                }
            )
        outputs = {key: value for key, value in outputs.items() if value is not None}
        state = outputs["state"]
        return {
            "status": "COMPLETED" if state == ContainerJobState.SUCCEEDED.value else (
                "CANCELLED" if state == ContainerJobState.CANCELED.value else "FAILED"
            ),
            "outputs": outputs,
        }

    def _map_agent_run_result(self, result: Any) -> dict[str, Any]:
        """Convert ``AgentRunResult`` to the dict format the execution loop expects."""
        if isinstance(result, dict):
            failure = result.get("failure_class") or result.get("failureClass")
            summary = result.get("summary") or ""
            output_refs = result.get("output_refs") or result.get("outputRefs") or []
            diagnostics_ref = result.get("diagnostics_ref") or result.get(
                "diagnosticsRef"
            )
            provider_error_code = result.get("provider_error_code") or result.get(
                "providerErrorCode"
            )
            retry_recommendation = result.get("retry_recommendation") or result.get(
                "retryRecommendation"
            )
            metadata = result.get("metadata") or {}
        else:
            failure = getattr(result, "failure_class", None)
            summary = getattr(result, "summary", "") or ""
            output_refs = getattr(result, "output_refs", []) or []
            diagnostics_ref = getattr(result, "diagnostics_ref", None)
            provider_error_code = getattr(result, "provider_error_code", None)
            retry_recommendation = getattr(result, "retry_recommendation", None)
            metadata = getattr(result, "metadata", {}) or {}

        status = "FAILED" if failure else "COMPLETED"
        outputs = {
            "summary": summary,
            "output_refs": output_refs,
            "error": failure or "",
        }
        if diagnostics_ref:
            outputs["diagnosticsRef"] = diagnostics_ref
        if provider_error_code:
            outputs["providerErrorCode"] = provider_error_code
        if retry_recommendation:
            outputs["retryRecommendation"] = retry_recommendation
        if isinstance(metadata, Mapping):
            for key, value in metadata.items():
                outputs.setdefault(key, value)

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
            and workflow.patched("MoonMind.UserWorkflow.jules_one_shot_bundle")
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
                f"Monitor MoonMind.UserWorkflow workflow for {self._repo or 'the requested workflow'}.",
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
                not self._recovery_requested
                and not self._cancel_requested
                and not _poll_terminal
            ):
                self._wait_cycle_count += 1
                try:
                    await workflow.wait_condition(
                        lambda: self._recovery_requested or self._cancel_requested,
                        timeout=timedelta(seconds=poll_interval_seconds),
                    )
                except asyncio.TimeoutError:
                    # No external signal arrived in this interval; proceed to status polling.
                    pass

                if self._recovery_requested or self._cancel_requested:
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
                            self._recovery_requested = True
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
                self._recovery_requested = False

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
            if not settings.workflow.enable_proposals:
                self._get_logger().info("Workflow proposal generation is globally disabled")
                return

        self._set_state(STATE_PROPOSALS, summary="Generating workflow proposals.")

        try:
            proposal_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                "proposal.generate"
            )
            generate_payload = {
                "principal": self._principal(),
                "workflow_id": workflow.info().workflow_id,
                "run_id": workflow.info().run_id,
                "repo": self._repo,
                "parameters": self._proposal_generation_parameters(parameters),
                "evidenceRefs": self._proposal_generation_evidence_refs(),
                "observability": {
                    "operatorSummary": self._operator_summary,
                    "lastStep": {
                        "id": self._last_step_id,
                        "summary": self._last_step_summary,
                        "diagnosticsRef": self._last_diagnostics_ref,
                    },
                },
            }
            telemetry_signals = self._proposal_telemetry_signals()
            if telemetry_signals:
                generate_payload["telemetrySignals"] = telemetry_signals
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
            task_node = parameters.get("workflow")
            if not isinstance(task_node, dict):
                task_node = parameters.get("task")
            task = task_node if isinstance(task_node, dict) else {}
            policy = task.get("proposalPolicy")
            policy_payload: dict[str, Any] = {}
            if isinstance(policy, dict):
                from moonmind.workflows.executions.execution_contract import WorkflowProposalPolicy

                try:
                    parsed_policy = WorkflowProposalPolicy.model_validate(policy)
                    policy_payload = parsed_policy.model_dump(
                        by_alias=True,
                        exclude_none=True,
                    )
                except Exception as exc:
                    self._get_logger().warning(
                        "Failed to validate workflow.proposalPolicy: %s", exc
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
                self._proposals_submitted = int(
                    submit_result.get("submitted_count")
                    or submit_result.get("submittedCount")
                    or 0
                )
                self._proposals_delivered = int(
                    submit_result.get("delivered_count")
                    or submit_result.get("deliveredCount")
                    or 0
                )
                self._proposal_validation_errors.extend(
                    self._compact_proposal_rows(
                        submit_result.get("validation_errors")
                        or submit_result.get("validationErrors")
                    )
                )
                self._proposal_delivery_failures.extend(
                    self._compact_proposal_rows(
                        submit_result.get("delivery_failures")
                        or submit_result.get("deliveryFailures")
                    )
                )
                self._proposal_external_links.extend(
                    self._compact_proposal_rows(
                        submit_result.get("external_links")
                        or submit_result.get("externalLinks")
                    )
                )
                self._proposal_dedup_updates.extend(
                    self._compact_proposal_rows(
                        submit_result.get("dedup_updates")
                        or submit_result.get("dedupUpdates")
                    )
                )
                errors = submit_result.get("errors") or []
                self._proposals_errors.extend(errors)
        except Exception as exc:
            self._get_logger().warning(
                "Proposal submission failed (best-effort): %s", exc
            )
            self._proposals_errors.append(f"submission failed: {str(exc)[:200]}")

    def _finish_summary_failure_summary(self) -> dict[str, Any] | None:
        moonspec_summary = self._moonspec_gate_failure_summary()
        if moonspec_summary is not None:
            return moonspec_summary
        return self._agent_runtime_failure_summary()

    def _moonspec_gate_failure_summary(self) -> dict[str, Any] | None:
        gate_context = self._publish_context.get("moonSpecGate")
        if not isinstance(gate_context, Mapping):
            return None
        if self._publish_context.get("publicationBlockedBy") != "moonspec_verify":
            return None

        verdict = self._coerce_text(gate_context.get("verdict"), max_chars=80)
        if not verdict:
            return None
        classification = self._coerce_text(
            gate_context.get("classification"), max_chars=200
        )
        raw_summary = self._coerce_text(gate_context.get("summary"), max_chars=1000)
        combined_text = " ".join(
            part
            for part in (
                verdict,
                classification,
                raw_summary,
                self._publish_reason,
                self._plan_blocked_message,
            )
            if isinstance(part, str) and part
        )
        blockers = self._validation_blockers_from_text(combined_text)
        category = self._moonspec_failure_category(
            classification=classification,
            blockers=blockers,
            text=combined_text,
        )
        if category == "validation_environment_blocked":
            recommended_next_action = "restore_validation_environment"
        elif category == "validation_evidence_missing":
            recommended_next_action = "provide_current_head_validation_evidence"
        else:
            recommended_next_action = "address_verification_gaps"

        compact: dict[str, Any] = {
            "type": "moonspec_verification_gate",
            "category": category,
            "blockedBy": "moonspec_verify",
            "verdict": verdict,
            "recommendedNextAction": recommended_next_action,
            "summary": self._moonspec_failure_headline(
                verdict=verdict,
                classification=classification,
                category=category,
            ),
        }
        if classification:
            compact["classification"] = classification
        downgrade_reason = self._coerce_text(
            gate_context.get("downgradeReason"), max_chars=700
        )
        if downgrade_reason:
            compact["downgradeReason"] = downgrade_reason
        diagnostics_ref = self._coerce_text(
            gate_context.get("diagnosticsRef"), max_chars=400
        )
        if diagnostics_ref:
            compact["diagnosticsRef"] = diagnostics_ref
        if blockers:
            compact["blockers"] = blockers
        publish_context = self._compact_publish_failure_context()
        if publish_context:
            compact["publishContext"] = publish_context
        return compact

    def _validation_blockers_from_text(self, text: str) -> list[str]:
        lowered = text.lower()
        blockers: list[str] = []
        if (
            (
                "native" in lowered
                or "windows/wsl" in lowered
                or "ue/toolchain" in lowered
            )
            and (
                "missing" in lowered
                or "unavailable" in lowered
                or "not run" in lowered
            )
        ):
            blockers.append("native_unreal_toolchain_missing")
        if "unauthorized" in lowered and ("ghcr" in lowered or "docker" in lowered):
            blockers.append("docker_registry_unauthorized")
        if (
            "ci" in lowered
            and (
                "current-head" in lowered
                or "pending" in lowered
                or "no run evidence" in lowered
            )
        ):
            blockers.append("current_head_ci_evidence_missing")
        return blockers

    @staticmethod
    def _moonspec_failure_category(
        *,
        classification: str | None,
        blockers: list[str],
        text: str,
    ) -> str:
        lowered = " ".join(
            part for part in (classification or "", text) if part
        ).lower()
        if (
            "environment" in lowered
            or "infrastructure" in lowered
            or "docker_registry_unauthorized" in blockers
            or "native_unreal_toolchain_missing" in blockers
        ):
            return "validation_environment_blocked"
        if "ci" in lowered or "evidence" in lowered or "required lane" in lowered:
            return "validation_evidence_missing"
        return "verification_gaps_remaining"

    @staticmethod
    def _moonspec_failure_headline(
        *,
        verdict: str,
        classification: str | None,
        category: str,
    ) -> str:
        if classification:
            return (
                f"MoonSpec verification blocked publication: {verdict}. "
                f"{classification.rstrip('.')}."
            )
        if category == "validation_environment_blocked":
            suffix = "validation environment unavailable"
        elif category == "validation_evidence_missing":
            suffix = "required validation evidence missing"
        else:
            suffix = "verification gaps remain"
        return f"MoonSpec verification blocked publication: {verdict}. {suffix}."

    def _compact_publish_failure_context(self) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key in (
            "branch",
            "baseRef",
            "headSha",
            "commitCount",
            "pullRequestUrl",
        ):
            value = self._publish_context.get(key)
            if key == "commitCount":
                if isinstance(value, int):
                    compact[key] = value
                continue
            text = self._coerce_text(value, max_chars=500)
            if text:
                compact[key] = text
        return compact

    def _agent_runtime_failure_summary(self) -> dict[str, Any] | None:
        if not isinstance(self._failure_diagnostic, Mapping):
            return None
        diagnostic = self._failure_diagnostic
        message = self._coerce_text(diagnostic.get("message"), max_chars=1000)
        if not message:
            return None
        lowered = message.lower()
        failure_cause = None
        if "app_server_protocol_empty_turn" in lowered:
            failure_cause = "app_server_protocol_empty_turn"
        retry_action = self._retry_action_from_failure_message(message)
        diagnostics_ref = self._coerce_text(
            diagnostic.get("diagnosticsRef"), max_chars=400
        )

        if failure_cause == "app_server_protocol_empty_turn":
            category = "transient_agent_runtime"
            recommended_next_action = (
                "retry_finalization_after_clear_session"
                if self._pull_request_url
                or self._compact_publish_failure_context().get("pullRequestUrl")
                else "retry_step_after_clear_session"
            )
            retry_action = retry_action or "clear_session"
        else:
            category = self._coerce_text(diagnostic.get("category"), max_chars=80)
            if category not in {
                "user_error",
                "integration_error",
                "execution_error",
                "system_error",
            }:
                category = "execution_error"
            recommended_next_action = "inspect_diagnostics"

        compact: dict[str, Any] = {
            "type": "agent_runtime_failure",
            "category": category,
            "recommendedNextAction": recommended_next_action,
            "summary": message,
        }
        if failure_cause:
            compact["failureCause"] = failure_cause
        if retry_action:
            compact["retryRecommendedAction"] = retry_action
        if diagnostics_ref:
            compact["diagnosticsRef"] = diagnostics_ref
        partial_success = self._partial_success_summary()
        if partial_success:
            compact["partialSuccess"] = partial_success
        return compact

    @staticmethod
    def _retry_action_from_failure_message(message: str) -> str | None:
        marker = "retryRecommendedAction:"
        index = message.find(marker)
        if index < 0:
            return None
        remainder = message[index + len(marker) :].strip()
        token = remainder.split(";", 1)[0].split(")", 1)[0].strip()
        return token or None

    def _partial_success_summary(self) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        gate_context = self._publish_context.get("moonSpecGate")
        if isinstance(gate_context, Mapping):
            verdict = self._coerce_text(gate_context.get("verdict"), max_chars=80)
            if verdict:
                compact["moonSpecVerdict"] = verdict
        pull_request_url = self._coerce_text(
            self._pull_request_url or self._publish_context.get("pullRequestUrl"),
            max_chars=500,
        )
        if pull_request_url:
            compact["pullRequestUrl"] = pull_request_url
        branch = self._coerce_text(self._publish_context.get("branch"), max_chars=500)
        if branch:
            compact["branch"] = branch
        head_sha = self._coerce_text(self._publish_context.get("headSha"), max_chars=80)
        if head_sha:
            compact["headSha"] = head_sha
        return compact

    async def _emit_failed_run_recovery_manifest(
        self,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Build and persist the failed-run recovery manifest (MM-881).

        Returns ``(compact_summary, manifest_artifact_ref)``. When the patch is
        active a compact, ref-only summary is always returned so the recovery
        evidence is carried in the execution-linked finish summary even if the
        durable artifact write fails; the artifact ref is returned when the
        durable write succeeds. The manifest is fail-closed: it never reports
        resume as allowed unless validated checkpoint evidence is present, so a
        failed run cannot silently degrade into a full rerun presented as
        resume.
        """

        if not workflow.patched(RUN_FAILED_RUN_RECOVERY_MANIFEST_PATCH):
            return None, None
        try:
            failed_step_id = self._recovery_failed_step_id or self._coerce_text(
                (self._failure_diagnostic or {}).get("stepId"), max_chars=200
            )
            capture_input = self._step_workspace_capture_inputs.get(
                failed_step_id or "", {}
            )
            capability_payload = capture_input.get("runtimeCapabilities")
            capabilities = (
                RuntimeExecutionCapabilities.model_validate(capability_payload)
                if isinstance(capability_payload, Mapping)
                else None
            )
            restore_route_registered = False
            if capabilities and capabilities.checkpoint_restore_activity:
                try:
                    DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                        capabilities.checkpoint_restore_activity
                    )
                    restore_route_registered = True
                except Exception:
                    restore_route_registered = False
            manifest = build_failed_run_recovery_manifest(
                workflow_id=workflow.info().workflow_id,
                run_id=workflow.info().run_id,
                created_at=workflow.now(),
                step_ledger_rows=self._step_ledger_rows,
                terminal_dispositions=self._step_terminal_dispositions,
                checkpoint_refs_by_boundary=self._step_checkpoint_refs_by_boundary,
                side_effect_records=self._step_side_effect_records,
                failure_diagnostic=self._failure_diagnostic,
                recovery_failed_step_id=self._recovery_failed_step_id,
                checkpoint_validation_failure=(
                    self._recovery_checkpoint_validation_failure
                ),
                checkpoint_kind=self._coerce_text(
                    capture_input.get("kind") or capture_input.get("checkpointKind"),
                    max_chars=100,
                ),
                runtime_capabilities=capabilities,
                restore_route_registered=restore_route_registered,
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to build failed-run recovery manifest: %s", exc
            )
            return None, None

        # MM-884: keep the built model so the incident reconstruction path reuses
        # its side-effect dispositions and checkpoint restore candidate.
        self._recovery_manifest_model = manifest
        manifest_payload = manifest.model_dump(by_alias=True, mode="json")
        manifest_ref: str | None = None
        try:
            manifest_ref = await self._write_json_artifact(
                name="reports/recovery_manifest.json",
                payload=manifest_payload,
                content_type=FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
                metadata_json={
                    "artifact_kind": "failed_run_recovery_manifest",
                    "resumeAllowed": manifest.resume_allowed,
                    "failedLogicalStepId": manifest.failed_logical_step_id or "",
                },
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to persist failed-run recovery manifest artifact: %s", exc
            )

        summary: dict[str, Any] = {
            "schemaVersion": manifest.schema_version,
            "contentType": manifest.content_type,
            "resumeAllowed": manifest.resume_allowed,
            "failedLogicalStepId": manifest.failed_logical_step_id,
            "failedExecutionOrdinal": manifest.failed_execution_ordinal,
            "validationResult": manifest.validation.result,
        }
        if manifest.last_accepted_step is not None:
            summary["lastAcceptedStepId"] = (
                manifest.last_accepted_step.logical_step_id
            )
        if manifest.validation.checkpoint_ref:
            summary["checkpointRef"] = manifest.validation.checkpoint_ref
        if manifest.blocked_reason:
            summary["blockedReason"] = manifest.blocked_reason
        if manifest_ref:
            summary["manifestRef"] = manifest_ref
        self._recovery_manifest_ref = manifest_ref
        self._recovery_manifest_summary = summary
        return summary, manifest_ref

    def _incident_progress_projection(self) -> dict[str, Any]:
        """Return a bounded progress projection for the incident manifest."""

        snapshot = self._progress_snapshot or {}
        keys = (
            "total",
            "pending",
            "ready",
            "executing",
            "reviewing",
            "completed",
            "failed",
            "skipped",
            "canceled",
            "currentStepTitle",
            "updatedAt",
        )
        projection: dict[str, Any] = {}
        for key in keys:
            value = snapshot.get(key)
            if value is None:
                continue
            if key == "currentStepTitle":
                projection[key] = str(value)[:200]
            else:
                projection[key] = value
        return projection

    def _incident_workspace_changes(self) -> list[dict[str, Any]]:
        """Return bounded per-step workspace-change refs for the incident manifest."""

        changes: list[dict[str, Any]] = []
        for row in self._step_ledger_rows:
            if not isinstance(row, Mapping):
                continue
            disposition = self._step_terminal_dispositions.get(
                str(row.get("logicalStepId") or "")
            ) or row.get("terminalDisposition")
            refs = row.get("refs")
            git_disposition: Any = None
            if isinstance(refs, Mapping):
                git_disposition = refs.get("gitDisposition") or refs.get("git_disposition")
            if not disposition and not git_disposition:
                continue
            entry: dict[str, Any] = {
                "logicalStepId": str(row.get("logicalStepId") or "")[:200],
            }
            if disposition:
                entry["terminalDisposition"] = str(disposition)[:120]
            if git_disposition:
                entry["gitDisposition"] = str(git_disposition)[:120]
            changes.append(entry)
        return changes

    def _incident_artifact_refs(self) -> dict[str, str]:
        """Collect durable artifact refs the incident manifest links (not copies)."""

        refs: dict[str, str] = {}
        if self._recovery_manifest_ref:
            refs["recoveryManifest"] = self._recovery_manifest_ref
        if isinstance(self._resilience_policy_ref, Mapping):
            envelope_ref = self._resilience_policy_ref.get("envelopeRef")
            if isinstance(envelope_ref, str) and envelope_ref.strip():
                refs["resiliencePolicyEnvelope"] = envelope_ref.strip()
        if self._logs_ref:
            refs["logs"] = self._logs_ref
        if self._plan_ref:
            refs["plan"] = self._plan_ref
        if self._input_ref:
            refs["taskInput"] = self._input_ref
        if isinstance(self._failure_diagnostic, Mapping):
            diagnostics_ref = self._failure_diagnostic.get("diagnosticsRef")
            if isinstance(diagnostics_ref, str) and diagnostics_ref.strip():
                refs["diagnostics"] = diagnostics_ref.strip()
        elif self._last_diagnostics_ref:
            refs["diagnostics"] = self._last_diagnostics_ref
        return refs

    async def _emit_incident_reconstruction_manifest(
        self,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Build and persist the incident reconstruction manifest (MM-884).

        Correlates policy, provider/profile/credential source, failed step,
        progress, workspace changes, accepted/blocked side effects, the
        checkpoint restore candidate, cost (settings + observed where available),
        trace spans across every boundary, logs, and artifacts under one stable
        trace id. Returns ``(compact_summary, manifest_artifact_ref)``. The
        manifest links durable evidence (recovery manifest, policy envelope,
        logs) rather than duplicating it, and redacts/refs raw provider payloads.
        """

        if not workflow.patched(RUN_INCIDENT_RECONSTRUCTION_PATCH):
            return None, None
        try:
            manifest = build_incident_reconstruction_manifest(
                workflow_id=workflow.info().workflow_id,
                run_id=workflow.info().run_id,
                created_at=workflow.now(),
                external_correlation_id=self._correlation_id,
                policy_ref=self._resilience_policy_ref,
                provider_profile_id=self._run_resilience_profile_id,
                runtime_id=self._target_runtime,
                provider_failure=self._provider_failure_envelope,
                cost_attribution_settings=self._cost_attribution_settings,
                observed_cost=self._observed_cost,
                recovery_manifest=self._recovery_manifest_model,
                recovery_manifest_ref=self._recovery_manifest_ref,
                failure_diagnostic=self._failure_diagnostic,
                progress_summary=self._incident_progress_projection(),
                workspace_changes=self._incident_workspace_changes(),
                logs_ref=self._logs_ref,
                artifact_refs=self._incident_artifact_refs(),
                control_stop=self._workflow_control_stop,
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to build incident reconstruction manifest: %s", exc
            )
            return None, None

        manifest_payload = manifest.model_dump(
            by_alias=True, mode="json", exclude_none=True
        )
        manifest_ref: str | None = None
        try:
            manifest_ref = await self._write_json_artifact(
                name="reports/incident_reconstruction.json",
                payload=manifest_payload,
                content_type=INCIDENT_RECONSTRUCTION_CONTENT_TYPE,
                metadata_json={
                    "artifact_kind": "incident_reconstruction_manifest",
                    "traceId": manifest.trace.trace_id,
                    "failedLogicalStepId": manifest.failed_logical_step_id or "",
                },
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to persist incident reconstruction manifest artifact: %s",
                exc,
            )

        present_kinds = [item.kind for item in manifest.evidence if item.present]
        summary: dict[str, Any] = {
            "schemaVersion": manifest.schema_version,
            "contentType": manifest.content_type,
            "traceId": manifest.trace.trace_id,
            "failedLogicalStepId": manifest.failed_logical_step_id,
            "evidencePresent": present_kinds,
            "traceBoundaries": [span.boundary for span in manifest.trace_spans],
        }
        if manifest.control_stop is not None:
            summary["controlStop"] = manifest.control_stop.model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        if manifest.cost is not None:
            summary["costObserved"] = manifest.cost.observed_available
        if manifest.provider is not None and manifest.provider.provider_error_class:
            summary["providerErrorClass"] = manifest.provider.provider_error_class
        if manifest_ref:
            summary["manifestRef"] = manifest_ref
        self._incident_reconstruction_ref = manifest_ref
        self._incident_reconstruction_summary = summary
        return summary, manifest_ref

    async def _run_finalizing_stage(
        self, *, parameters: dict[str, Any], status: str, error: Optional[str] = None
    ) -> None:
        try:
            await self._terminate_workflow_scoped_sessions(reason=status)
        except Exception as exc:
            self._get_logger().warning(
                "Failed to terminate workflow-scoped agent sessions: %s", exc
            )
        try:
            self._get_logger().info("Generating finish summary.")

            publish_mode = self._publish_mode(parameters)
            publish_status = "skipped" if status != "success" else "published"
            publish_reason = (
                "run did not complete successfully"
                if status in ("failed", "canceled")
                else "No repository changes were available to commit or publish."
                if status == "no_commit"
                else (
                    "publishing disabled"
                    if publish_mode == "none"
                    else "published successfully"
                )
            )

            # Map Temporal status back to FinishOutcome code.
            code = "FAILED" if status == "failed" else "NO_COMMIT"
            if status == "canceled":
                code = "CANCELLED"

            if workflow.patched(RUN_WORKFLOW_PUBLISH_OUTCOME_PATCH):
                if status == "success":
                    if publish_mode == "none":
                        code = "PUBLISH_DISABLED"
                        publish_status = "skipped"
                        publish_reason = "publishing disabled"
                    elif publish_mode == "auto" and self._publish_status == "published":
                        if self._publish_context.get("merged") is True:
                            code = "PUBLISHED_PR"
                            publish_reason = (
                                self._publish_reason or "auto publish verified merge"
                            )
                        else:
                            code = "PUBLISHED_BRANCH"
                            publish_reason = (
                                self._publish_reason or "auto publish verified push"
                            )
                        publish_status = "published"
                    elif publish_mode == "auto" and self._publish_status == "not_required":
                        code = "NO_COMMIT"
                        publish_status = "skipped"
                        publish_reason = (
                            self._publish_reason or "auto publish no-op verified"
                        )
                    elif self._publish_status == "skipped":
                        code = "NO_COMMIT"
                        publish_status = "skipped"
                        publish_reason = (
                            self._publish_reason
                            or "No repository changes were available to commit or publish."
                        )
                    elif self._publish_status == "not_required":
                        code = (
                            "NO_COMMIT"
                            if self._is_canonical_no_commit_outcome(parameters)
                            else "PUBLISH_DISABLED"
                        )
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

            diagnostic_reason: str | None = None
            if status == "failed" and isinstance(self._failure_diagnostic, dict):
                diagnostic_reason = self._failure_diagnostic.get("message") or None

            finish_outcome_reason = diagnostic_reason or error or "completed"
            if code == "NO_COMMIT":
                finish_outcome_reason = "No repository commit was needed."

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
                    "reason": finish_outcome_reason,
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
                    "deliveredCount": self._proposals_delivered,
                    "validationErrors": self._proposal_validation_errors,
                    "deliveryFailures": self._proposal_delivery_failures,
                    "externalLinks": self._proposal_external_links,
                    "dedupUpdates": self._proposal_dedup_updates,
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
            side_effects = self._finish_summary_side_effects()
            if side_effects:
                finish_summary["sideEffects"] = side_effects
            if code == "NO_COMMIT":
                finish_summary["publish"]["reasonCode"] = "no_commit"
                finish_summary["publish"]["commitCreated"] = False
                finish_summary["publish"]["branchPushed"] = False
                finish_summary["publish"]["prUrl"] = None
            if publish_mode == "auto":
                finish_summary["publish"]["owner"] = "agent"
                finish_summary["publish"]["evidenceRequired"] = True
                if self._publish_context.get("blockedReason"):
                    finish_summary["publish"]["blockedReason"] = (
                        self._publish_context.get("blockedReason")
                    )
            if self._publish_context:
                terminal_publication = self._publish_context.get(
                    "terminalPublication"
                )
                if isinstance(terminal_publication, Mapping):
                    # Keep preservation evidence on the canonical publish block
                    # consumed by terminal-state persistence and execution detail.
                    # The failed finish outcome above remains authoritative.
                    finish_summary["publish"].update(
                        {
                            key: terminal_publication.get(key)
                            for key in (
                                "intent",
                                "status",
                                "reasonCode",
                                "source",
                                "attempted",
                                "commitCreated",
                                "branchPushed",
                                "branchName",
                                "branchUrl",
                                "headSha",
                                "baseBranch",
                                "remoteVerified",
                                "evidenceRef",
                                "idempotencyKey",
                            )
                            if terminal_publication.get(key) is not None
                        }
                    )
                finish_summary["publishContext"] = dict(self._publish_context)
                merge_automation_summary = self._merge_automation_summary_from_context()
                if merge_automation_summary:
                    finish_summary["mergeAutomation"] = merge_automation_summary
            if status == "failed":
                failure_summary = self._finish_summary_failure_summary()
                if failure_summary:
                    finish_summary["failureSummary"] = failure_summary
            last_step_summary = self._last_step_summary
            last_step_id = self._last_step_id
            last_diagnostics_ref = self._last_diagnostics_ref
            last_step_error: str | None = None
            if status == "failed" and isinstance(self._failure_diagnostic, dict):
                diag = self._failure_diagnostic
                # Prefer the diagnostic when it covers a specific failed step so
                # the operator sees the failing tool, not the last successful
                # step recorded earlier in the run.
                diag_step_id = diag.get("stepId")
                if diag_step_id:
                    last_step_id = diag_step_id
                    last_step_summary = diag.get("message") or last_step_summary
                    if diag.get("diagnosticsRef"):
                        last_diagnostics_ref = diag.get("diagnosticsRef")
                elif diag.get("message"):
                    last_step_summary = last_step_summary or diag.get("message")
                last_step_error = diag.get("category")

            if (
                last_step_id
                or last_step_summary
                or last_diagnostics_ref
                or last_step_error
            ):
                last_step_block: dict[str, Any] = {
                    "id": last_step_id,
                    "summary": last_step_summary,
                    "diagnosticsRef": last_diagnostics_ref,
                }
                if last_step_error:
                    last_step_block["lastError"] = last_step_error
                finish_summary["lastStep"] = last_step_block

            if status == "failed" and isinstance(self._failure_diagnostic, dict):
                finish_summary["failure"] = dict(self._failure_diagnostic)

            if status == "failed":
                # Checkpoint-backed recovery is the default failed-run path: emit
                # a recovery manifest (durable artifact + compact reference)
                # before the terminal failure is reported (MM-881).
                manifest_summary, _ = await self._emit_failed_run_recovery_manifest()
                if manifest_summary:
                    finish_summary["recoveryManifest"] = manifest_summary
                # MM-884: emit the single incident reconstruction manifest
                # (durable artifact + compact reference) correlating policy,
                # provider, failed step, progress, changes, side effects,
                # checkpoint, cost, trace spans, logs, and artifacts before
                # terminal failure is reported. Built after the recovery manifest
                # so it can reuse the recovery side-effect/checkpoint evidence.
                incident_summary, _ = (
                    await self._emit_incident_reconstruction_manifest()
                )
                if incident_summary:
                    finish_summary["incidentReconstruction"] = incident_summary

            self._finish_summary = finish_summary

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
            finish_outcome = (
                self._finish_summary.get("finishOutcome")
                or self._finish_summary.get("finish_outcome")
                if isinstance(self._finish_summary, dict)
                else None
            )
            if not isinstance(finish_outcome, dict):
                finish_outcome = {}
            activity_task = asyncio.create_task(
                execute_typed_activity(
                    "execution.record_terminal_state",
                    ExecutionTerminalStateInput(
                        workflowId=workflow.info().workflow_id,
                        state=state,
                        closeStatus=close_status,
                        summary=summary,
                        finishOutcomeCode=(
                            str(finish_outcome.get("code") or "").strip() or None
                        ),
                        finishOutcomeStage=(
                            str(finish_outcome.get("stage") or "").strip() or None
                        ),
                        finishOutcomeReason=(
                            str(finish_outcome.get("reason") or "").strip() or None
                        ),
                        finishSummary=self._finish_summary,
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

    def _compile_authored_omnigent_selection(
        self, value: Mapping[str, Any], *, path: str
    ) -> dict[str, Any]:
        """Compile product-owned Omnigent intent without accepting authority."""

        allowed_fields: dict[str, frozenset[str] | None] = {
            "endpointRef": None,
            "executionTargetRef": None,
            "launchPolicyRef": None,
            "agent": frozenset({"harnessOverride"}),
            "capture": frozenset(
                {
                    "required",
                    "retentionDays",
                    "changedFiles",
                    "workspaceFiles",
                    "sessionFiles",
                    "deleteOmnigentSessionAfterHarvest",
                }
            ),
            "session": frozenset({"title", "labels"}),
            "prompt": frozenset({"text", "instructionRef"}),
            "productIntent": None,
            "contextRefs": None,
            "workspaceContextRefs": None,
            "repositoryContextRefs": None,
        }
        normalized = self._json_mapping(value, path=path)

        forbidden_authority_keys = {
            "hostid",
            "dockervolume",
            "volumename",
            "credentialgeneration",
            "leaseid",
            "providerleaseid",
            "hostleaseid",
            "absolutebindsource",
            "bindsource",
            "registrationtoken",
            "profileauthorization",
            "moonmindprofileauthorization",
        }

        def reject_authority(item: Any, *, item_path: str) -> None:
            if isinstance(item, Mapping):
                for nested_key, nested_value in item.items():
                    canonical_key = re.sub(r"[^a-z0-9]", "", nested_key.lower())
                    if canonical_key in forbidden_authority_keys:
                        raise ValueError(
                            f"{item_path}.{nested_key} is trusted authority and "
                            "cannot be authored"
                        )
                    reject_authority(
                        nested_value,
                        item_path=f"{item_path}.{nested_key}",
                    )
            elif isinstance(item, list):
                for index, nested_value in enumerate(item):
                    reject_authority(
                        nested_value,
                        item_path=f"{item_path}[{index}]",
                    )

        reject_authority(normalized, item_path=path)
        unknown = sorted(set(normalized) - set(allowed_fields))
        if unknown:
            raise ValueError(
                f"{path} contains unsupported authored fields: {', '.join(unknown)}"
            )

        for key, nested_allowlist in allowed_fields.items():
            if key not in normalized or nested_allowlist is None:
                continue
            nested = normalized[key]
            if not isinstance(nested, Mapping):
                raise ValueError(f"{path}.{key} must be an object")
            nested_unknown = sorted(set(nested) - set(nested_allowlist))
            if nested_unknown:
                raise ValueError(
                    f"{path}.{key} contains unsupported authored fields: "
                    + ", ".join(nested_unknown)
                )

        agent = normalized.get("agent")
        if isinstance(agent, Mapping):
            harness = agent.get("harnessOverride")
            if harness != "codex-native":
                raise ValueError(
                    f"{path}.agent.harnessOverride must be codex-native"
                )
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

    def _jira_blocker_wait_metadata(self) -> dict[str, Any]:
        if (
            not self._jira_blocker_wait_active
            and not self._jira_blocker_wait_issue_keys
            and not self._jira_blocker_wait_summary
        ):
            return {}
        wait_duration_ms = 0
        if self._jira_blocker_wait_started_at is not None:
            elapsed = workflow.now() - self._jira_blocker_wait_started_at
            wait_duration_ms = max(0, int(elapsed.total_seconds() * 1000))
        return {
            "jira_blocker_wait": {
                "active": self._jira_blocker_wait_active,
                "skipped": self._jira_blocker_wait_skipped,
                "issueKeys": list(self._jira_blocker_wait_issue_keys),
                "summary": self._jira_blocker_wait_summary,
                "waitDurationMs": wait_duration_ms,
            }
        }

    def _mark_real_work_started(self, *, now: datetime | None = None) -> None:
        """Stamp ``mm_started_at`` once, when the workflow first does real work.

        This is the MoonMind semantic "started" timestamp; do not use Temporal's
        workflow ``start_time`` / ``execution_time`` for this — they fire when
        the workflow is scheduled, even while it is still awaiting a provider
        slot. Idempotent across replay and across cooldown/requeue cycles: once
        set, it is never overwritten.
        """
        if self._started_at is not None:
            return
        started_at = now or workflow.now()
        self._started_at = started_at
        try:
            workflow.upsert_search_attributes(
                [
                    SearchAttributePair(
                        SearchAttributeKey.for_datetime(
                            MM_STARTED_AT_SEARCH_ATTRIBUTE
                        ),
                        started_at,
                    )
                ]
            )
        except Exception as exc:
            self._get_logger().warning(
                "Failed to upsert mm_started_at search attribute",
                extra={"error": str(exc)},
            )

    def _update_search_attributes(self) -> None:
        memo: dict[str, Any] = {
            "waiting_reason": self._waiting_reason,
            "attention_required": self._attention_required,
        }
        if workflow.patched(RUN_STATUS_MEMO_UPSERT_PATCH):
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
                self._entry or "user_workflow",
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
        title_tokens = tokenize_title(self._title)
        if title_tokens:
            # mm_title is a KeywordList of the title's word tokens. Temporal SQL
            # visibility supports neither LIKE nor substring matching, so
            # operators word-match titles via KeywordList membership
            # (`mm_title = "word"`). Keep tokenization identical to the
            # executions list endpoint (see title_search).
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword_list("mm_title"),
                    title_tokens,
                )
            )
        if self._repo:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_repo"),
                    self._repo,
                )
            )
        integration_label = self._integration_label or self._integration
        if integration_label:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_integration"),
                    integration_label,
                )
            )
        if self._target_runtime:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword_list("mm_target_runtime"),
                    [self._target_runtime],
                )
            )
        if self._target_skill:
            pairs.append(
                SearchAttributePair(
                    SearchAttributeKey.for_keyword_list("mm_target_skill"),
                    [self._target_skill],
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
        if isinstance(self._step_count, int) and self._step_count > 0:
            memo_dict["mm_current_step_order"] = self._step_count
        if workflow.patched("run-memo-runtime-skill-visibility"):
            if self._target_runtime:
                memo_dict["targetRuntime"] = self._target_runtime
            if self._target_skill:
                memo_dict["targetSkill"] = self._target_skill
        if (
            workflow.patched(RUN_MEMO_RUNTIME_INHERITANCE_PATCH)
            and self._runtime_inheritance_parameters
        ):
            memo_dict["parameters"] = dict(self._runtime_inheritance_parameters)
        if self._input_ref:
            memo_dict["input_artifact_ref"] = self._input_ref
        if self._plan_ref:
            memo_dict["plan_artifact_ref"] = self._plan_ref
        if self._logs_ref:
            memo_dict["logs_artifact_ref"] = self._logs_ref
        if self._summary_ref:
            memo_dict["summary_artifact_ref"] = self._summary_ref
        # MM-880: expose the versioned ResiliencePolicy reference for forensic
        # review. Only populated once the policy has been compiled for the run.
        if self._resilience_policy_ref:
            memo_dict["resilience_policy_ref"] = dict(self._resilience_policy_ref)
        # MM-884: link the durable incident reconstruction manifest for failed
        # runs so dashboard/report surfaces reach correlated evidence
        # without duplicating it in workflow history.
        if self._incident_reconstruction_ref:
            memo_dict["incident_reconstruction_ref"] = self._incident_reconstruction_ref
        if self._pull_request_url:
            memo_dict["pull_request_url"] = self._pull_request_url
        merge_automation_summary = self._merge_automation_summary_from_context()
        if merge_automation_summary:
            memo_dict["merge_automation"] = merge_automation_summary
        memo_dict.update(self._dependency_metadata())
        memo_dict.update(self._jira_blocker_wait_metadata())

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
            if workflow.patched(RUN_REAL_STARTED_AT_PATCH):
                self._mark_real_work_started()
            self._set_state(STATE_EXECUTING, summary="Launching agent...")
        elif new_state == "running":
            self._waiting_reason = None
            self._attention_required = False
            if workflow.patched(RUN_REAL_STARTED_AT_PATCH):
                self._mark_real_work_started()
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

    @workflow.signal
    def managed_session_bound(self, payload: dict) -> None:
        """Record a workflow-scoped managed session started after slot admission."""

        binding_payload = payload.get("binding") if isinstance(payload, dict) else None
        if not isinstance(binding_payload, Mapping):
            return
        try:
            binding = CodexManagedSessionBinding.model_validate(binding_payload)
        except Exception as exc:
            self._get_logger().warning(
                "Ignoring invalid managed_session_bound payload",
                extra={"error": str(exc)},
            )
            return
        self._codex_session_binding = binding
        self._get_logger().debug(
            "Workflow-scoped managed session bound: %s",
            binding.session_id,
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
        The node_id typically indicates the agent kind (e.g., "jules", "claude").
        """
        # Simple heuristic: extract from the workflow ID
        # Format: parent_id:agent:node_id[:retry<N>]
        parts = child_workflow_id.split(":")
        if len(parts) >= 3:
            node_id = parts[2]
            # Map common node IDs to runtime IDs
            mapping = {
                "jules": "jules",
                "claude": "claude_code",
                "codex": "codex_cli",
            }
            return mapping.get(node_id)
        return None

    @workflow.update(name="Pause")
    async def pause(self) -> None:
        self._pause_resume_transition_in_progress = True
        previous_waiting_reason = self._waiting_reason
        try:
            self._paused = True
            self._waiting_reason = "Paused by user"
            if not await self._forward_lifecycle_update_to_active_child("Pause"):
                self._paused = False
                self._waiting_reason = previous_waiting_reason
                raise RuntimeError(
                    "Failed to forward Pause update to active child workflow."
                )
            self._update_search_attributes()
        finally:
            self._pause_resume_transition_in_progress = False

    @pause.validator
    def validate_pause(self) -> None:
        if self._pause_resume_transition_in_progress:
            raise ValueError("Pause/Resume transition already in progress.")
        if self._paused:
            raise ValueError("Workflow is already paused.")
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot pause a completed workflow.")

    @workflow.update(name="Resume")
    async def resume(self, payload: dict[str, Any] | None = None) -> None:
        self._pause_resume_transition_in_progress = True
        previous_paused = self._paused
        previous_waiting_reason = self._waiting_reason
        try:
            self._paused = False
            self._waiting_reason = None
            if not await self._forward_lifecycle_update_to_active_child("Resume"):
                self._paused = previous_paused
                self._waiting_reason = previous_waiting_reason
                raise RuntimeError(
                    "Failed to forward Resume update to active child workflow."
                )
            await self._forward_operator_message_to_active_child(payload)
            if self._awaiting_external:
                self._recovery_requested = True
            self._update_search_attributes()
        finally:
            self._pause_resume_transition_in_progress = False

    @resume.validator
    def validate_resume(self, payload: dict[str, Any] | None = None) -> None:
        if self._pause_resume_transition_in_progress:
            raise ValueError("Pause/Resume transition already in progress.")
        if not self._paused and not self._awaiting_external:
            raise ValueError("Workflow is not paused or awaiting external completion.")
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot resume a completed workflow.")

    @workflow.update(name="Approve")
    async def approve(self, payload: dict[str, Any] | None = None) -> None:
        self._approve_requested = True
        await self._forward_operator_message_to_active_child(payload)
        if self._awaiting_external:
            self._recovery_requested = True

    @approve.validator
    def validate_approve(self, payload: dict[str, Any] | None = None) -> None:
        if self._state in (STATE_COMPLETED, STATE_CANCELED, STATE_FAILED):
            raise ValueError("Cannot approve a completed workflow.")

    @workflow.update(name="SkipDependencyWait")
    def skip_dependency_wait(self) -> None:
        if self._jira_blocker_wait_active:
            self._jira_blocker_wait_skipped = True
            self._paused = False
            self._summary = "Jira blocker wait skipped by operator."
            self._update_search_attributes()
            self._update_memo()
            return
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
        if self._jira_blocker_wait_active:
            return
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
        message = self._extract_operator_message(payload)
        if message is None:
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
            self._recovery_requested = True

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

    async def _forward_lifecycle_update_to_active_child(self, update_name: str) -> bool:
        if not self._active_agent_child_workflow_id:
            return True
        handle = workflow.get_external_workflow_handle(
            self._active_agent_child_workflow_id
        )
        try:
            await handle.execute_update(update_name)
        except Exception as exc:
            self._get_logger().warning(
                "Failed to forward %s update to active child workflow %s: %s",
                update_name,
                self._active_agent_child_workflow_id,
                exc,
            )
            return False
        return True

    @staticmethod
    def _runtime_selection_from_source(
        source: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(source, Mapping):
            return {}
        selection: dict[str, Any] = {}
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
            ("mode", "targetRuntime"),
        ):
            value = source.get(source_key)
            if value is None:
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            selection[target_key] = value
        return selection

    @staticmethod
    def _source_requests_runtime_profile_clear(
        source: Mapping[str, Any] | None,
    ) -> bool:
        if not isinstance(source, Mapping):
            return False
        for source_key in (
            "executionProfileRef",
            "execution_profile_ref",
            "profileId",
            "profile_id",
            "providerProfile",
        ):
            if source_key not in source:
                continue
            value = source.get(source_key)
            if isinstance(value, str) and value.strip().lower() in {"", "auto"}:
                return True
        return False

    def _runtime_selection_update_payload(
        self, payload: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        parameters_patch = payload.get("parameters_patch") or payload.get(
            "parametersPatch"
        )
        if not isinstance(parameters_patch, Mapping):
            return None

        preserve_profile_clear = workflow.patched(
            RUN_RUNTIME_PROFILE_CLEAR_FORWARDING_PATCH
        )
        profile_clear_requested = False
        selection: dict[str, Any] = {}
        for source in (parameters_patch,):
            if preserve_profile_clear and self._source_requests_runtime_profile_clear(
                source
            ):
                profile_clear_requested = True
            selection.update(self._runtime_selection_from_source(source))

        runtime_block = parameters_patch.get("runtime")
        if isinstance(runtime_block, Mapping):
            if (
                preserve_profile_clear
                and self._source_requests_runtime_profile_clear(runtime_block)
            ):
                profile_clear_requested = True
            selection.update(self._runtime_selection_from_source(runtime_block))

        workflow_payload = parameters_patch.get("workflow")
        if isinstance(workflow_payload, Mapping):
            workflow_runtime = workflow_payload.get("runtime")
            if isinstance(workflow_runtime, Mapping):
                if (
                    preserve_profile_clear
                    and self._source_requests_runtime_profile_clear(workflow_runtime)
                ):
                    profile_clear_requested = True
                selection.update(self._runtime_selection_from_source(workflow_runtime))

        authored_payload = parameters_patch.get("authoredTaskInput")
        if isinstance(authored_payload, Mapping):
            authored_runtime = authored_payload.get("runtime")
            if isinstance(authored_runtime, Mapping):
                if (
                    preserve_profile_clear
                    and self._source_requests_runtime_profile_clear(authored_runtime)
                ):
                    profile_clear_requested = True
                selection.update(self._runtime_selection_from_source(authored_runtime))

        if profile_clear_requested:
            selection["executionProfileRef"] = ""
        if not selection:
            return None
        selection["parametersPatch"] = dict(parameters_patch)
        return selection

    def _apply_visibility_from_parameters_patch(
        self, parameters_patch: Mapping[str, Any]
    ) -> bool:
        """Refresh parent runtime/skill facets from an accepted input edit."""

        changed = False
        target_runtime = self._runtime_visibility_from_parameters(parameters_patch)
        if target_runtime and target_runtime != self._target_runtime:
            self._target_runtime = target_runtime
            changed = True

        target_skill = self._skill_visibility_from_parameters(parameters_patch)
        if target_skill and target_skill != self._target_skill:
            self._target_skill = target_skill
            changed = True

        if changed:
            self._update_search_attributes()
            self._update_memo()
        return changed

    async def _forward_runtime_selection_update_to_active_child(
        self, payload: Mapping[str, Any] | None
    ) -> bool:
        if not self._active_agent_child_workflow_id:
            return False
        if str(self._active_agent_id or "").strip().lower() in {
            "jules",
            "jules_api",
        }:
            return False
        runtime_update = self._runtime_selection_update_payload(payload)
        if runtime_update is None:
            return False
        handle = workflow.get_external_workflow_handle(
            self._active_agent_child_workflow_id
        )
        await handle.signal("update_runtime_selection", runtime_update)
        self._summary = "Runtime selection update sent to active agent."
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
            if workflow.patched(RUN_UPDATE_INPUTS_VISIBILITY_REFRESH_PATCH):
                self._apply_visibility_from_parameters_patch(parameters_patch)
        forwarded_runtime_update = (
            await self._forward_runtime_selection_update_to_active_child(payload)
        )
        forwarded = await self._forward_operator_message_to_active_child(payload)
        return {
            "accepted": True,
            "forwardedOperatorMessage": forwarded,
            "forwardedRuntimeSelectionUpdate": forwarded_runtime_update,
        }

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": self._state,
            "paused": self._paused,
            "cancel_requested": self._cancel_requested,
            "canceling": self._cancel_requested and self._state != STATE_CANCELED,
            "step_count": self._step_count,
            "summary": self._summary,
            "awaiting_external": self._awaiting_external,
            "waiting_reason": self._waiting_reason,
        }
        for row in reversed(self._step_ledger_rows):
            if isinstance(row.get("executionOutcome"), Mapping):
                payload["executionOutcome"] = dict(row["executionOutcome"])
            if isinstance(row.get("finalizationOutcome"), Mapping):
                payload["finalizationOutcome"] = dict(row["finalizationOutcome"])
            if "executionOutcome" in payload or "finalizationOutcome" in payload:
                break
        return payload

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
            prepared_artifact_refs=self._prepared_artifact_refs,
            queried_at=workflow.now(),
        )
        return StepLedgerSnapshotModel.model_validate(snapshot).model_dump(
            by_alias=True,
            mode="json",
        )


@workflow.defn(name=USER_WORKFLOW_NAME)
class MoonMindUserWorkflow(MoonMindRunWorkflow):
    """Renamed-contract user Workflow Execution for the MM-730 hard switch."""

    def _expected_workflow_name(self) -> str:
        return USER_WORKFLOW_NAME

    @workflow.run
    async def run(self, input_payload: RunWorkflowInput) -> RunWorkflowOutput:
        return await super().run(input_payload)
