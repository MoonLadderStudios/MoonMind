"""Parent-owned Temporal workflow for post-publish merge automation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from temporalio import workflow
from temporalio.common import SearchAttributeKey, SearchAttributePair
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.workflow import ActivityCancellationType, ChildWorkflowCancellationType

with workflow.unsafe.imports_passed_through():
    from pr_resolver_core.review_providers import review_comment_is_after
    from moonmind.config.settings import settings
    from moonmind.schemas.temporal_models import (
        AutomatedReviewFailureModel,
        MergeAutomationStartInput,
        ReadinessBlockerModel,
    )
    from moonmind.schemas.temporal_activity_models import ArtifactWriteCompleteInput
    from moonmind.utils.logging import redact_sensitive_text
    from moonmind.workflows.merge_automation_review import (
        REVIEW_REQUEST_POSTED_STATUSES,
        REVIEW_REQUEST_RETRY_GATE_STATUSES,
        build_review_request_key,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        AGENT_RUNTIME_TASK_QUEUE,
        ARTIFACTS_TASK_QUEUE,
        INTEGRATIONS_TASK_QUEUE,
        WORKFLOW_TASK_QUEUE,
    )
    from moonmind.workflows.temporal.typed_execution import execute_typed_activity
    from moonmind.workflows.temporal.workflows.merge_gate import (
        DEFAULT_ACTIVITY_RETRY_POLICY,
        FINISH_MODE_FIX_ONLY,
        FINISH_MODE_MERGE,
        FINISH_MODE_REVIEW_ONLY,
        TERMINAL_BLOCKER_KINDS,
        _effective_expire_at,
        build_resolver_run_request,
        classify_readiness,
        deterministic_resolver_idempotency_key,
        legacy_resolver_idempotency_key,
    )

WORKFLOW_NAME = "MoonMind.MergeAutomation"
STATE_WAITING = "waiting"
STATE_EXECUTING = "executing"
STATE_BLOCKED = "blocked"
STATE_MERGED = "merged"
STATE_ALREADY_MERGED = "already_merged"
STATE_REVIEW_CLEAN = "review_clean"
STATE_REVIEW_COMPLETE = "review_complete"
STATE_EXPIRED = "expired"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"
DISPOSITION_REENTER_GATE = "reenter_gate"
DISPOSITION_REQUEST_REVIEW = "request_review"
DISPOSITION_MERGED = "merged"
DISPOSITION_ALREADY_MERGED = "already_merged"
DISPOSITION_REVIEW_CLEAN = "review_clean"
DISPOSITION_MANUAL_REVIEW = "manual_review"
DISPOSITION_FAILED = "failed"
SUCCESS_DISPOSITIONS = frozenset(
    {DISPOSITION_MERGED, DISPOSITION_ALREADY_MERGED, DISPOSITION_REVIEW_CLEAN}
)
NON_SUCCESS_DISPOSITIONS = frozenset({DISPOSITION_MANUAL_REVIEW, DISPOSITION_FAILED})
ALLOWED_DISPOSITIONS = SUCCESS_DISPOSITIONS | NON_SUCCESS_DISPOSITIONS | {
    DISPOSITION_REENTER_GATE,
    DISPOSITION_REQUEST_REVIEW,
}
MAX_PUBLISHED_ARTIFACT_REFS = 20
MERGE_AUTOMATION_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH = (
    "merge-automation-workflow-child-task-queue-v2"
)
MERGE_AUTOMATION_RESOLVER_PARENT_GATE_ID_PATCH = (
    "merge-automation-resolver-parent-gate-id-v1"
)
MERGE_AUTOMATION_RESOLVER_FAILURE_SUMMARY_PATCH = (
    "merge-automation-resolver-failure-summary-v1"
)
MERGE_AUTOMATION_POST_RESOLVER_PROGRESS_RECOVERY_PATCH = (
    "merge-automation-post-resolver-progress-recovery-v1"
)
MERGE_AUTOMATION_RESOLVER_CONTINUATION_DELAY_PATCH = (
    "merge-automation-resolver-continuation-delay-v1"
)
MERGE_AUTOMATION_STRICT_CONTINUATION_IDENTITY_PATCH = (
    "merge-automation-strict-continuation-identity-v1"
)
MERGE_AUTOMATION_CONTINUATION_OBSERVABILITY_PATCH = (
    "merge-automation-continuation-observability-v1"
)
MERGE_AUTOMATION_RESOLVER_ATTEMPT_TITLE_PATCH = (
    "merge-automation-resolver-attempt-title-v1"
)
MERGE_AUTOMATION_OMNIGENT_RESOLVER_PLAN_PATCH = (
    "merge-automation-omnigent-resolver-plan-v1"
)
MERGE_AUTOMATION_RESOLVER_VERIFICATION_CAPABILITY_PATCH = (
    "merge-automation-resolver-verification-capability-v1"
)
MERGE_AUTOMATION_RESOLVER_MERGE_CONFIRMATION_PATCH = (
    "merge-automation-resolver-merge-confirmation-v1"
)
# Unchanged reenter_gate handoffs consume the no-progress budget even when no
# automated review loop is configured, so they cannot relaunch agents forever.
MERGE_AUTOMATION_BOUND_REENTER_WITHOUT_REVIEW_LOOP_PATCH = (
    "merge-automation-bound-reenter-without-review-loop-v1"
)
# Request/remediate/request loop for one configured automated review provider.
# Guarded so histories recorded before the loop existed keep replaying their
# original gate decisions.
MERGE_AUTOMATION_REVIEW_LOOP_PATCH = "merge-automation-review-loop-v1"
MERGE_AUTOMATION_SELECTED_REVIEW_REQUEST_PATCH_PREFIX = (
    "merge-automation-selected-review-request-v1:"
)
MERGE_AUTOMATION_ACTIONABLE_CI_FAILURE_PATCH_PREFIX = (
    "merge-automation-actionable-ci-failure-v1:"
)
MERGE_AUTOMATION_MISSING_CI_WAIT_PATCH_PREFIX = (
    "merge-automation-missing-ci-wait-v1:"
)
MERGE_AUTOMATION_SELECTED_REVIEW_REQUEST_CYCLE_BUDGET_PATCH_PREFIX = (
    "merge-automation-selected-review-request-cycle-budget-v1:"
)
MERGE_AUTOMATION_REVIEW_ADOPTION_GUARD_PATCH_PREFIX = (
    "merge-automation-review-adoption-guard-v1:"
)
MERGE_AUTOMATION_REVIEW_FAILURE_SETTLEMENT_PATCH_PREFIX = (
    "merge-automation-review-failure-settlement-v1:"
)
MERGE_AUTOMATION_RESTORED_REVIEW_IDENTITY_PATCH = (
    "merge-automation-restored-review-identity-v1"
)
MAX_PUBLISHED_REVIEW_CYCLES = 20
# Typed routing for validated pr-resolver terminal verdicts
# (MoonLadderStudios/MoonMind#4223). Guarded so histories recorded before the
# policy existed keep replaying the legacy immediate-fail gate decision.
MERGE_AUTOMATION_PR_RESOLVER_VERDICT_ROUTING_PATCH = (
    "merge-automation-pr-resolver-verdict-routing-v1"
)
NEXT_STEP_RUN_FULL_REMEDIATION = "run_full_remediation"
NEXT_STEP_RETRY_FINALIZE_AFTER_BACKOFF = "retry_finalize_after_backoff"
NEXT_STEP_MANUAL_REVIEW = "manual_review"
NEXT_STEP_ATTEMPTS_EXHAUSTED = "attempts_exhausted"
MAX_PUBLISHED_RESOLVER_VERDICT_CYCLES = 20
RESOLVER_ISSUE_RECOVERY_NONE = "none"
RESOLVER_ISSUE_RECOVERY_COMPLETED = "completed"
RESOLVER_ISSUE_RECOVERY_REENTER_GATE = "reenter_gate"


def _parse_review_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _review_completion_is_fresh(
    *, requested_at: Any, completed_at: Any, completion_kind: Any, completion_id: Any
) -> bool:
    requested = _parse_review_timestamp(requested_at)
    completed = _parse_review_timestamp(completed_at)
    return bool(
        str(completion_kind or "").strip()
        and isinstance(completion_id, int)
        and not isinstance(completion_id, bool)
        and completion_id > 0
        and requested is not None
        and completed is not None
        and completed >= requested
    )


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindMergeAutomationWorkflow:
    """Wait for PR readiness, run pr-resolver as a child, and return a parent outcome."""

    @staticmethod
    def _workflow_child_task_queue() -> str:
        if workflow.patched(MERGE_AUTOMATION_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH):
            return settings.temporal.user_workflow_v2_task_queue
        return WORKFLOW_TASK_QUEUE

    @staticmethod
    def _resolver_parent_workflow_id() -> str:
        return str(workflow.info().workflow_id).strip()

    def __init__(self) -> None:
        self._status = STATE_WAITING
        self._input: MergeAutomationStartInput | None = None
        self._blockers: list[ReadinessBlockerModel] = []
        self._resolver_child_workflow_ids: list[str] = []
        self._gate_snapshot_artifact_refs: list[str] = []
        self._resolver_attempt_artifact_refs: list[str] = []
        self._summary_artifact_ref: str | None = None
        self._post_merge_jira_resolution_artifact_ref: str | None = None
        self._post_merge_jira_transition_artifact_ref: str | None = None
        self._post_merge_jira_result: dict[str, Any] | None = None
        self._post_merge_github_result: dict[str, Any] | None = None
        self._external_event_count = 0
        self._continuation_observability_enabled = False
        self._resolver_attempt_titles_enabled = False
        self._continuation_counters: dict[str, int] = {
            "continuation_requested": 0,
            "continuation_accepted": 0,
            "continuation_rejected_ownership": 0,
            "continuation_rejected_schema": 0,
            "continuation_wait_started": 0,
            "continuation_wait_completed": 0,
            "continuation_cycle_completed": 0,
            "legacy_continuation_fallback_used": 0,
        }
        self._refresh_tracked_head_sha_on_next_evaluation = False
        self._review_loop_enabled = False
        self._review_cycles: list[dict[str, Any]] = []
        self._active_review_request: dict[str, Any] | None = None
        self._last_progress_signature: str | None = None
        self._no_progress_cycles = 0
        self._summary: str | None = None
        # Validated pr-resolver verdict routing (#4223): one record per
        # resolver child, bounded by verdict+reason+head progress.
        self._resolver_verdict_cycles: list[dict[str, Any]] = []
        self._resolver_verdict_stop_reason: str | None = None
        self._remediation_child_workflow_ids: list[str] = []
        self._verdict_routing_enabled = False

    def _summary_payload(self) -> dict[str, Any]:
        pr = self._input.pull_request if self._input is not None else None
        artifact_refs = {
            "summary": self._summary_artifact_ref,
            "gateSnapshots": self._published_artifact_refs(
                self._gate_snapshot_artifact_refs
            ),
            "resolverAttempts": self._published_artifact_refs(
                self._resolver_attempt_artifact_refs
            ),
        }
        if self._post_merge_jira_resolution_artifact_ref:
            artifact_refs["postMergeJiraResolution"] = (
                self._post_merge_jira_resolution_artifact_ref
            )
        if self._post_merge_jira_transition_artifact_ref:
            artifact_refs["postMergeJiraTransition"] = (
                self._post_merge_jira_transition_artifact_ref
            )
        payload = {
            "status": self._status,
            "prNumber": pr.number if pr is not None else None,
            "prUrl": pr.url if pr is not None else None,
            "cycles": len(self._resolver_child_workflow_ids),
            "resolverChildWorkflowIds": list(self._resolver_child_workflow_ids),
            "latestHeadSha": pr.head_sha if pr is not None else None,
            "blockers": [
                blocker.model_dump(by_alias=True, mode="json")
                for blocker in self._blockers
            ],
            "artifactRefs": artifact_refs,
        }
        if self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
            payload["finishMode"] = FINISH_MODE_REVIEW_ONLY
        if self._review_loop_active():
            payload["reviewLoop"] = {
                "enabled": True,
                "provider": self._review_loop_config().provider,
                "cycles": len(self._review_cycles),
                "maxCycles": self._review_loop_config().max_cycles,
                "noProgressCycles": self._no_progress_cycles,
                "activeRequest": (
                    dict(self._active_review_request)
                    if self._active_review_request
                    else None
                ),
                "cycleRecords": [
                    dict(cycle)
                    for cycle in self._review_cycles[-MAX_PUBLISHED_REVIEW_CYCLES:]
                ],
            }
        if self._continuation_observability_enabled:
            payload["continuationCounters"] = dict(self._continuation_counters)
        if self._verdict_routing_enabled or self._resolver_verdict_cycles:
            payload["resolverVerdictCycles"] = [
                dict(cycle)
                for cycle in self._resolver_verdict_cycles[
                    -MAX_PUBLISHED_RESOLVER_VERDICT_CYCLES:
                ]
            ]
            payload["remediationChildWorkflowIds"] = list(
                self._remediation_child_workflow_ids
            )
            if self._resolver_verdict_stop_reason:
                payload["resolverVerdictStopReason"] = (
                    self._resolver_verdict_stop_reason
                )
        if self._summary:
            payload["summary"] = self._summary
        if self._post_merge_jira_result is not None:
            payload["postMergeJira"] = dict(self._post_merge_jira_result)
        if self._post_merge_github_result is not None:
            payload["postMergeGithub"] = dict(self._post_merge_github_result)
        return payload

    @staticmethod
    def _published_artifact_refs(refs: list[str]) -> list[str]:
        return list(refs[-MAX_PUBLISHED_ARTIFACT_REFS:])

    @staticmethod
    def _artifact_id_from_ref(artifact_ref: Any) -> str:
        if isinstance(artifact_ref, Mapping):
            return str(
                artifact_ref.get("artifact_id") or artifact_ref.get("artifactId") or ""
            )
        return str(
            getattr(artifact_ref, "artifact_id", "")
            or getattr(artifact_ref, "artifactId", "")
        )

    def _principal(self) -> str:
        if self._input is None:
            return "merge-automation"
        return self._input.principal or self._input.parent_workflow_id

    def _resolver_owner_type(self) -> str:
        return "system" if self._principal() == "system" else "user"

    def _resolver_search_attributes(self) -> dict[str, list[str]]:
        attributes = {
            "mm_owner_type": [self._resolver_owner_type()],
            "mm_owner_id": [self._principal()],
            "mm_entry": ["user_workflow"],
        }
        if self._input is not None:
            attributes["mm_repo"] = [self._input.pull_request.repo]
        return attributes

    @staticmethod
    def _resolver_child_memo(
        resolver_request: Mapping[str, Any],
        *,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        initial_parameters = resolver_request.get("initial_parameters")
        if not isinstance(initial_parameters, Mapping):
            initial_parameters = {}
        task_payload = initial_parameters.get("task")
        if not isinstance(task_payload, Mapping):
            task_payload = {}
        runtime_payload = task_payload.get("runtime")
        if not isinstance(runtime_payload, Mapping):
            runtime_payload = {}
        tool_payload = task_payload.get("tool")
        if not isinstance(tool_payload, Mapping):
            tool_payload = {}
        skill_payload = task_payload.get("skill")
        if not isinstance(skill_payload, Mapping):
            skill_payload = {}
        target_runtime = (
            str(
                initial_parameters.get("targetRuntime")
                or runtime_payload.get("mode")
                or runtime_payload.get("targetRuntime")
                or ""
            ).strip()[:80]
            or None
        )
        target_skill = (
            str(
                tool_payload.get("name")
                or tool_payload.get("id")
                or skill_payload.get("id")
                or skill_payload.get("name")
                or initial_parameters.get("targetSkill")
                or ""
            ).strip()[:160]
            or None
        )
        title = (
            str(resolver_request.get("title") or "Resolve PR").strip() or "Resolve PR"
        )
        if attempt is not None:
            title = f"{title} (Attempt {attempt})"
        memo: dict[str, Any] = {
            "entry": "user_workflow",
            "title": title,
            "summary": "Resolver child workflow for merge automation.",
        }
        if target_runtime:
            memo["targetRuntime"] = target_runtime
        if target_skill:
            memo["targetSkill"] = target_skill
        return memo

    async def _prepare_omnigent_resolver_request(
        self,
        resolver_request: Mapping[str, Any],
        *,
        resolver_workflow_id: str,
    ) -> dict[str, Any]:
        """Compile fresh Omnigent authority for a Temporal-started child."""

        if self._input is None:
            raise RuntimeError("merge automation input is unavailable")
        template = self._input.resolver_template
        target_runtime = str(template.get("targetRuntime") or "").strip().lower()
        if target_runtime != "omnigent":
            return dict(resolver_request)
        parent_plan = template.get("parentOmnigentExecutionPlan")
        activity_payload: dict[str, Any] = {
            "principal": self._principal(),
            "parentWorkflowId": self._input.parent_workflow_id,
            "childWorkflowId": resolver_workflow_id,
            "childWorkflowRequest": dict(resolver_request),
        }
        if isinstance(parent_plan, Mapping):
            activity_payload["parentExecutionPlan"] = dict(parent_plan)
        prepared = await workflow.execute_activity(
            "omnigent.prepare_child_execution_plan",
            activity_payload,
            task_queue=AGENT_RUNTIME_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=5),
            schedule_to_close_timeout=timedelta(minutes=10),
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        if not isinstance(prepared, Mapping):
            raise ValueError(
                "Omnigent child execution-plan preparation returned an invalid "
                "workflow request."
            )
        initial_parameters = prepared.get("initial_parameters")
        if not isinstance(initial_parameters, Mapping):
            raise ValueError(
                "Omnigent child execution-plan preparation omitted initial parameters."
            )
        if not isinstance(initial_parameters.get("omnigentExecutionPlan"), Mapping):
            raise ValueError(
                "Omnigent child execution-plan preparation omitted plan authority."
            )
        if not str(initial_parameters.get("resolvedSkillsetRef") or "").strip():
            raise ValueError(
                "Omnigent child execution-plan preparation omitted Skill authority."
            )
        return dict(prepared)

    async def _write_json_artifact(self, *, name: str, payload: dict[str, Any]) -> str | None:
        try:
            artifact_ref, _upload_desc = await workflow.execute_activity(
                "artifact.create",
                {
                    "principal": self._principal(),
                    "name": name,
                    "content_type": "application/json",
                },
                task_queue=ARTIFACTS_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                schedule_to_close_timeout=timedelta(seconds=120),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            artifact_id = self._artifact_id_from_ref(artifact_ref)
            if not artifact_id:
                return None
            await execute_typed_activity(
                "artifact.write_complete",
                ArtifactWriteCompleteInput(
                    principal=self._principal(),
                    artifact_id=artifact_id,
                    payload=(json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(
                        "utf-8"
                    ),
                    content_type="application/json",
                ),
                task_queue=ARTIFACTS_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=60),
                schedule_to_close_timeout=timedelta(seconds=120),
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            )
            return artifact_id
        except CancelledError:
            raise
        except Exception:
            return None

    async def _write_gate_snapshot(self, *, evidence_ready: bool) -> None:
        snapshot_name = (
            "artifacts/merge_automation/gate_snapshots/"
            f"{len(self._resolver_child_workflow_ids)}.json"
        )
        artifact_id = await self._write_json_artifact(
            name=snapshot_name,
            payload={
                "status": self._status,
                "ready": evidence_ready,
                "summary": self._summary_payload(),
            },
        )
        if artifact_id:
            self._gate_snapshot_artifact_refs.append(artifact_id)

    async def _write_resolver_attempt(
        self, *, workflow_id: str, result: Any | None = None
    ) -> None:
        payload: dict[str, Any] = {
            "status": self._status,
            "workflowId": workflow_id,
            "attempt": len(self._resolver_child_workflow_ids),
            "summary": self._summary_payload(),
        }
        if isinstance(result, Mapping):
            payload["result"] = {
                "status": result.get("status"),
                "mergeAutomationDisposition": result.get("mergeAutomationDisposition"),
                "headSha": result.get("headSha"),
            }
            if self._verdict_routing_enabled:
                verdict = self._pr_resolver_verdict(result)
                for key in (
                    "prResolverStatus",
                    "finalReason",
                    "nextStep",
                    "terminalContractEvidenceRef",
                    "retryAfterSeconds",
                    "prResolverVerdictSummary",
                ):
                    if verdict.get(key) is not None:
                        payload["result"][key] = verdict.get(key)
            continuation = result.get("gatedContinuation")
            if isinstance(continuation, Mapping):
                normalized = {
                    key: continuation.get(key)
                    for key in (
                        "schemaVersion",
                        "gateType",
                        "action",
                        "reason",
                        "notBefore",
                        "retryAfterSeconds",
                        "executionRef",
                        "headSha",
                        "ownerWorkflowId",
                        "ownerRunId",
                        "ownerWorkflowType",
                        "childWorkflowId",
                        "childRunId",
                    )
                    if continuation.get(key) is not None
                }
                payload["continuation"] = normalized
                payload["continuationTimingSource"] = (
                    "skill_not_before"
                    if normalized.get("notBefore")
                    else (
                        "skill_retry_after"
                        if normalized.get("retryAfterSeconds") is not None
                        else "legacy_fallback"
                    )
                )
        attempt_name = (
            "artifacts/merge_automation/resolver_attempts/"
            f"{len(self._resolver_child_workflow_ids)}.json"
        )
        artifact_id = await self._write_json_artifact(name=attempt_name, payload=payload)
        if artifact_id:
            self._resolver_attempt_artifact_refs.append(artifact_id)

    async def _finish(self) -> dict[str, Any]:
        payload = self._summary_payload()
        artifact_id = await self._write_json_artifact(
            name="reports/merge_automation_summary.json",
            payload=payload,
        )
        if artifact_id:
            self._summary_artifact_ref = artifact_id
            payload = self._summary_payload()
        self._publish_visibility()
        return payload

    def _publish_visibility(self) -> None:
        workflow.upsert_memo({"summary": self._summary_payload()})
        workflow.upsert_search_attributes(
            [
                SearchAttributePair(SearchAttributeKey.for_keyword("mm_state"), self._status),
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_entry"),
                    "merge_automation",
                ),
            ]
        )

    @workflow.signal(name="merge_automation.external_event")
    def external_event(self, _payload: dict[str, Any]) -> None:
        self._external_event_count += 1

    @workflow.query
    def summary(self) -> dict[str, Any]:
        return self._summary_payload()

    @staticmethod
    def _resolver_disposition(resolver_result: Any) -> str:
        if not isinstance(resolver_result, Mapping):
            return ""
        return str(resolver_result.get("mergeAutomationDisposition") or "").strip()

    @staticmethod
    def _compact_verdict_text(value: Any, *, max_chars: int = 500) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            return ""
        if len(candidate) > max_chars:
            return candidate[: max_chars - 3].rstrip() + "..."
        return candidate

    @classmethod
    def _pr_resolver_verdict(cls, resolver_result: Mapping[str, Any]) -> dict[str, Any]:
        """Project the Skill's validated terminal verdict without reinterpreting it.

        Only the verdict facts the Skill wrote are carried (status, reason,
        next step, evidence ref, retry delay, head). MoonMind routes on these
        values; it never reclassifies the blocker or selects a different fix.
        """

        status = cls._compact_verdict_text(
            resolver_result.get("prResolverStatus")
            or resolver_result.get("pr_resolver_status")
            or resolver_result.get("status"),
            max_chars=80,
        )
        reason = cls._compact_verdict_text(
            resolver_result.get("prResolverReason")
            or resolver_result.get("pr_resolver_reason")
            or resolver_result.get("final_reason")
            or resolver_result.get("finalReason")
            or resolver_result.get("reason"),
            max_chars=500,
        )
        next_step = cls._compact_verdict_text(
            resolver_result.get("prResolverNextStep")
            or resolver_result.get("pr_resolver_next_step")
            or resolver_result.get("next_step")
            or resolver_result.get("nextStep"),
            max_chars=80,
        )
        head_sha = cls._compact_verdict_text(
            resolver_result.get("headSha")
            or resolver_result.get("head_sha")
            or resolver_result.get("latestHeadSha"),
            max_chars=80,
        )
        evidence_ref = cls._compact_verdict_text(
            resolver_result.get("terminalContractEvidenceRef")
            or resolver_result.get("terminal_contract_evidence_ref"),
            max_chars=200,
        )
        retry_after: int | None = None
        for key in (
            "retryAfterSeconds",
            "retry_after_seconds",
            "prResolverRetryAfterSeconds",
        ):
            raw = resolver_result.get(key)
            if raw is None or isinstance(raw, bool):
                continue
            try:
                candidate = int(raw) if not isinstance(raw, int) else raw
            except (TypeError, ValueError):
                continue
            if candidate >= 1:
                retry_after = candidate
                break
        if retry_after is None:
            continuation = resolver_result.get("gatedContinuation")
            if isinstance(continuation, Mapping):
                raw = continuation.get("retryAfterSeconds")
                if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
                    retry_after = raw
        summary = cls._compact_verdict_text(
            resolver_result.get("prResolverVerdictSummary")
            or resolver_result.get("summary"),
            max_chars=1600,
        )
        verdict: dict[str, Any] = {}
        if status:
            verdict["prResolverStatus"] = status
        if reason:
            verdict["finalReason"] = reason
        if next_step:
            verdict["nextStep"] = next_step
        if head_sha:
            verdict["headSha"] = head_sha
        if evidence_ref:
            verdict["terminalContractEvidenceRef"] = evidence_ref
        if retry_after is not None:
            verdict["retryAfterSeconds"] = retry_after
        if summary:
            verdict["prResolverVerdictSummary"] = summary
        return verdict

    @staticmethod
    def _resolver_verdict_signature(verdict: Mapping[str, Any]) -> str:
        """Bound cycles by progress: identical verdict+reason+head means no progress."""

        status = str(verdict.get("prResolverStatus") or "").strip().lower()
        reason = str(verdict.get("finalReason") or "").strip().lower()
        head = str(verdict.get("headSha") or "").strip().lower()
        return f"{status}|{reason}|{head}"

    def _record_resolver_verdict_cycle(
        self,
        *,
        resolver_workflow_id: str,
        disposition: str,
        verdict: Mapping[str, Any],
    ) -> dict[str, Any]:
        cycle = {
            "cycle": len(self._resolver_verdict_cycles) + 1,
            "resolverChildWorkflowId": resolver_workflow_id,
            "disposition": disposition,
            "headSha": str(verdict.get("headSha") or ""),
            "prResolverStatus": str(verdict.get("prResolverStatus") or ""),
            "finalReason": str(verdict.get("finalReason") or ""),
            "nextStep": str(verdict.get("nextStep") or ""),
            "terminalContractEvidenceRef": str(
                verdict.get("terminalContractEvidenceRef") or ""
            ),
        }
        if verdict.get("retryAfterSeconds") is not None:
            cycle["retryAfterSeconds"] = verdict.get("retryAfterSeconds")
        if verdict.get("prResolverVerdictSummary"):
            cycle["summary"] = verdict.get("prResolverVerdictSummary")
        self._resolver_verdict_cycles.append(cycle)
        return cycle

    @staticmethod
    def _normalize_remediation_artifact_ref(value: Any) -> str:
        """Normalize a terminal evidence ref to an ``artifact://`` ref.

        The runtime materializer only recognizes top-level ``gateResultRef`` /
        ``remainingWorkRef`` values using the ``artifact://`` scheme, while the
        ``remediate-issue`` Skill requires materialized local paths and stops
        when it receives only an unreadable reference.
        """

        candidate = str(value or "").strip()
        if not candidate:
            return ""
        if candidate.startswith("artifact://"):
            remainder = candidate.removeprefix("artifact://").strip()
            return candidate if remainder else ""
        return f"artifact://{candidate}"

    def _build_remediation_run_request(
        self,
        *,
        verdict: Mapping[str, Any],
        resolver_workflow_id: str,
    ) -> dict[str, Any]:
        """Build a bounded remediate-issue child request from Skill evidence.

        The Skill's validated evidence ref is routed through the supported
        ``gateResultRef`` / ``remainingWorkRef`` fields so the Activity can
        materialize it, and the child runs in the authoritative PR-head
        workspace with a workflow-owned publication handoff. MoonMind only
        routes, it never reclassifies the blocker or invents a fix plan.
        """

        pr = self._input.pull_request if self._input is not None else None
        evidence_ref = self._normalize_remediation_artifact_ref(
            verdict.get("terminalContractEvidenceRef")
        )
        head_sha = str(verdict.get("headSha") or "").strip()
        final_reason = str(verdict.get("finalReason") or "").strip()
        repo = pr.repo if pr is not None else ""
        head_branch = (
            str(pr.head_branch or "").strip()
            if pr is not None and pr.head_branch
            else ""
        )
        base_branch = (
            str(pr.base_branch or "").strip()
            if pr is not None and pr.base_branch
            else ""
        )
        target_branch = head_branch or base_branch
        initial_parameters: dict[str, Any] = {
            "repository": repo,
            "repo": repo,
            "publishMode": "auto",
            "task": {
                "instructions": (
                    "Apply the bounded remediate-issue Skill contract to the "
                    "validated pr-resolver terminal evidence. "
                    f"Reason: {final_reason or 'unknown'}. "
                    "Do not reimplement Skill semantics."
                ),
                "tool": {"type": "skill", "name": "remediate-issue"},
                "skill": {
                    "id": "remediate-issue",
                    "args": {
                        "gateResultRef": evidence_ref,
                        "remainingWorkRef": evidence_ref,
                        "remainingWorkPath": str(
                            verdict.get("terminalContractEvidenceRef") or ""
                        ),
                        "headSha": head_sha,
                        "finalReason": final_reason,
                        "resolverChildWorkflowId": resolver_workflow_id,
                    },
                },
                "publish": {"mode": "auto"},
            },
            "workspaceSpec": {
                "repository": repo,
                "branch": head_branch,
                "startingBranch": base_branch or target_branch,
                "targetBranch": target_branch,
            },
        }
        if evidence_ref:
            initial_parameters["gateResultRef"] = evidence_ref
            initial_parameters["remainingWorkRef"] = evidence_ref
        if pr is not None:
            initial_parameters["mergeGate"] = {
                "parentWorkflowId": self._resolver_parent_workflow_id(),
                "pullRequestUrl": pr.url,
                "headSha": head_sha or pr.head_sha,
            }
        return {
            "workflowType": "MoonMind.UserWorkflow",
            "parentWorkflowId": self._resolver_parent_workflow_id(),
            "title": (
                f"Remediate {final_reason or 'pr-resolver blocker'} "
                f"for PR #{pr.number if pr is not None else '?'}"
            ),
            "initial_parameters": initial_parameters,
        }

    async def _run_bounded_remediation(
        self,
        *,
        verdict: Mapping[str, Any],
        resolver_workflow_id: str,
    ) -> None:
        remediation_request = self._build_remediation_run_request(
            verdict=verdict,
            resolver_workflow_id=resolver_workflow_id,
        )
        remediation_workflow_id = f"{resolver_workflow_id}:remediation"
        self._remediation_child_workflow_ids.append(remediation_workflow_id)
        try:
            remediation_result = await workflow.execute_child_workflow(
                "MoonMind.UserWorkflow",
                remediation_request,
                id=remediation_workflow_id,
                task_queue=self._workflow_child_task_queue(),
                search_attributes=self._resolver_search_attributes(),
                cancellation_type=ChildWorkflowCancellationType.TRY_CANCEL,
                static_summary="Bounded pr-resolver remediation for merge automation",
                static_details=(
                    f"Remediate {verdict.get('finalReason') or 'pr-resolver blocker'}"
                ),
            )
        except CancelledError:
            raise
        except Exception:
            # A failed remediation step must not mask the Skill's validated
            # verdict; the gate re-opens and the progress bound decides.
            return
        if isinstance(remediation_result, Mapping):
            self._refresh_tracked_head_sha(remediation_result)

    async def _wait_for_pr_resolver_backoff(self, *, retry_after_seconds: int) -> None:
        try:
            await workflow.sleep(timedelta(seconds=int(retry_after_seconds)))
        except CancelledError:
            raise
        except Exception as exc:
            # Direct unit-boundary tests invoke run() without a Temporal
            # event loop. Production histories always use the timer above.
            if type(exc).__name__ != "_NotInWorkflowEventLoopError":
                raise

    def _skill_verdict_summary(
        self, *, verdict: Mapping[str, Any], disposition: str
    ) -> str:
        explicit = str(verdict.get("prResolverVerdictSummary") or "").strip()
        if explicit:
            return explicit
        status = str(verdict.get("prResolverStatus") or "").strip()
        reason = str(verdict.get("finalReason") or "").strip()
        next_step = str(verdict.get("nextStep") or "").strip()
        if status or reason or next_step:
            parts = [f"pr-resolver reported status '{status or 'unknown'}'"]
            if reason:
                parts.append(reason)
            if next_step:
                parts.append(f"next_step={next_step}")
            return "; ".join(parts)
        if disposition == DISPOSITION_MANUAL_REVIEW:
            return "pr-resolver requested manual review."
        return "pr-resolver reported failure."

    async def _route_pr_resolver_terminal(
        self,
        *,
        resolver_result: Mapping[str, Any],
        resolver_workflow_id: str,
        resolver_disposition: str,
    ) -> dict[str, Any] | None:
        """Route a validated terminal verdict; None means re-enter the gate.

        One typed policy (MoonLadderStudios/MoonMind#4223):
        - ``run_full_remediation`` → one bounded remediate-issue child, then
          re-enter the gate on the (possibly new) head.
        - ``retry_finalize_after_backoff`` → wait the Skill-supplied
          ``retryAfterSeconds`` with no agent launch, then re-enter.
        - ``manual_review``/``attempts_exhausted`` (or unrecognized) → stop
          with the Skill's summary.
        Cycles are bounded by progress, not count: an identical
        verdict+reason+head to the previous cycle stops instead of launching
        another resolver.
        """

        verdict = self._pr_resolver_verdict(resolver_result)
        # Fall back to the tracked head when the child omits it so progress
        # bounding still compares the revision the gate acted on.
        if not verdict.get("headSha") and self._input is not None:
            tracked = str(self._input.pull_request.head_sha or "").strip()
            if tracked:
                verdict = {**verdict, "headSha": tracked}
        cycle = self._record_resolver_verdict_cycle(
            resolver_workflow_id=resolver_workflow_id,
            disposition=resolver_disposition,
            verdict=verdict,
        )
        # Keep the gate's tracked head aligned with the revision the Skill
        # actually evaluated before any routing decision.
        self._refresh_tracked_head_sha(resolver_result)
        signature = self._resolver_verdict_signature(verdict)
        if len(self._resolver_verdict_cycles) >= 2:
            previous = self._resolver_verdict_cycles[-2]
            previous_signature = self._resolver_verdict_signature(previous)
            if signature and signature == previous_signature:
                self._resolver_verdict_stop_reason = (
                    "identical_verdict_head_no_progress"
                )
                cycle["stopReason"] = self._resolver_verdict_stop_reason
                return await self._failed_resolver_summary(
                    summary=self._skill_verdict_summary(
                        verdict=verdict, disposition=resolver_disposition
                    ),
                    blocker_kind=resolver_disposition,
                )
        next_step = str(verdict.get("nextStep") or "").strip().lower()
        if next_step == NEXT_STEP_RUN_FULL_REMEDIATION:
            await self._run_bounded_remediation(
                verdict=verdict,
                resolver_workflow_id=resolver_workflow_id,
            )
            self._status = STATE_WAITING
            self._publish_visibility()
            return None
        if next_step == NEXT_STEP_RETRY_FINALIZE_AFTER_BACKOFF:
            retry_after = verdict.get("retryAfterSeconds")
            if not isinstance(retry_after, int) or retry_after < 1:
                retry_after = (
                    self._input.config.timeouts.fallback_poll_seconds
                    if self._input is not None
                    else 300
                )
            self._resolver_verdict_stop_reason = None
            cycle["waitedRetryAfterSeconds"] = retry_after
            self._status = STATE_WAITING
            self._summary = (
                "pr-resolver requested a finalize retry after backoff; "
                f"waiting {retry_after}s before re-entering the gate."
            )
            self._publish_visibility()
            await self._wait_for_pr_resolver_backoff(
                retry_after_seconds=int(retry_after)
            )
            return None
        # ``manual_review``/``attempts_exhausted`` (or any unrecognized
        # next_step) stops the cycle with the Skill's summary.
        has_verdict_facts = any(
            str(resolver_result.get(key) or "").strip()
            for key in (
                "prResolverStatus",
                "prResolverReason",
                "prResolverNextStep",
                "final_reason",
                "finalReason",
                "next_step",
                "nextStep",
                "terminalContractEvidenceRef",
                "retryAfterSeconds",
                "prResolverVerdictSummary",
            )
        )
        if not has_verdict_facts:
            if resolver_disposition == DISPOSITION_MANUAL_REVIEW:
                self._resolver_verdict_stop_reason = "manual_review"
                return await self._failed_resolver_summary(
                    summary="pr-resolver requested manual review.",
                    blocker_kind=DISPOSITION_MANUAL_REVIEW,
                )
            self._resolver_verdict_stop_reason = "failed"
            return await self._failed_resolver_summary(
                summary="pr-resolver reported failure.",
                blocker_kind=DISPOSITION_FAILED,
            )
        self._resolver_verdict_stop_reason = (
            next_step if next_step else resolver_disposition
        )
        cycle["stopReason"] = self._resolver_verdict_stop_reason
        return await self._failed_resolver_summary(
            summary=self._skill_verdict_summary(
                verdict=verdict, disposition=resolver_disposition
            ),
            blocker_kind=resolver_disposition,
        )

    def _continuation_deadline(
        self, resolver_result: Mapping[str, Any], *, resolver_workflow_id: str
    ) -> datetime:
        raw = resolver_result.get("gatedContinuation")
        if not isinstance(raw, Mapping):
            raise ValueError("missing gated continuation contract")
        if (
            resolver_result.get("completionDisposition") != "gated_continuation"
            or raw.get("schemaVersion") != "gated-continuation/v1"
            or raw.get("gateType") != "merge_automation"
            or raw.get("action") != "reenter_gate"
        ):
            raise ValueError("invalid gated continuation contract")
        strict_identity = workflow.patched(
            MERGE_AUTOMATION_STRICT_CONTINUATION_IDENTITY_PATCH
        )
        if strict_identity:
            required_text = (
                "reason",
                "executionRef",
                "headSha",
                "ownerWorkflowId",
                "ownerRunId",
                "ownerWorkflowType",
                "childWorkflowId",
                "childRunId",
            )
            if any(not str(raw.get(key) or "").strip() for key in required_text):
                raise ValueError("incomplete gated continuation contract")
            info = workflow.info()
            if (
                raw.get("ownerWorkflowId") != info.workflow_id
                or raw.get("ownerRunId") != info.run_id
                or raw.get("ownerWorkflowType") != WORKFLOW_NAME
                or raw.get("childWorkflowId") != resolver_workflow_id
            ):
                raise ValueError("gated continuation ownership mismatch")
            if (
                raw.get("childRunId") != resolver_result.get("childRunId")
                or raw.get("executionRef") != resolver_result.get("executionRef")
            ):
                raise ValueError("gated continuation execution identity mismatch")
            head_sha = str(raw.get("headSha") or "").strip().lower()
            expected_head = str(resolver_result.get("headSha") or "").strip().lower()
            if not (7 <= len(head_sha) <= 64) or any(
                c not in "0123456789abcdef" for c in head_sha
            ):
                raise ValueError("invalid gated continuation headSha")
            if expected_head != head_sha:
                raise ValueError("gated continuation headSha mismatch")
        not_before = str(raw.get("notBefore") or "").strip()
        retry_after = raw.get("retryAfterSeconds")
        if not not_before and retry_after is None:
            if self._continuation_observability_enabled:
                self._continuation_counters["legacy_continuation_fallback_used"] += 1
            return workflow.now() + timedelta(
                seconds=self._input.config.timeouts.fallback_poll_seconds
            )
        if not_before and retry_after is not None:
            raise ValueError("gated continuation cannot provide both timing values")
        if retry_after is not None:
            if isinstance(retry_after, bool) or int(retry_after) < 1:
                raise ValueError("invalid gated continuation retryAfterSeconds")
            return workflow.now() + timedelta(seconds=int(retry_after))
        try:
            parsed = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid gated continuation notBefore") from exc
        if parsed.tzinfo is None:
            raise ValueError("gated continuation notBefore must be timezone-aware")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _head_sha_from_mapping(payload: Mapping[str, Any]) -> str:
        for key in ("headSha", "head_sha", "latestHeadSha", "latest_head_sha"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                return candidate
        for key in ("pullRequest", "pull_request", "mergeGate", "merge_gate"):
            nested = payload.get(key)
            if isinstance(nested, Mapping):
                candidate = MoonMindMergeAutomationWorkflow._head_sha_from_mapping(nested)
                if candidate:
                    return candidate
        return ""

    def _refresh_tracked_head_sha(self, payload: Any) -> bool:
        if self._input is None or not isinstance(payload, Mapping):
            return False
        head_sha = self._head_sha_from_mapping(payload)
        if not head_sha:
            return False
        self._input.pull_request.head_sha = head_sha
        return True

    @staticmethod
    def _stale_revision_can_track_current_head(evidence: Any) -> bool:
        blockers = list(getattr(evidence, "blockers", []) or [])
        stale_seen = False
        for blocker in blockers:
            kind = str(getattr(blocker, "kind", "") or "").strip()
            if kind == "stale_revision":
                stale_seen = True
                continue
        return stale_seen

    def _refresh_current_head_for_stale_wait(
        self,
        *,
        evaluation: Any,
        evidence: Any,
    ) -> Any:
        if self._input is None or not isinstance(evaluation, Mapping):
            return evidence
        if getattr(evidence, "ready", False) or getattr(
            evidence,
            "pull_request_merged",
            False,
        ):
            return evidence
        if not self._stale_revision_can_track_current_head(evidence):
            return evidence
        if not self._refresh_tracked_head_sha(evaluation):
            return evidence
        return classify_readiness(
            evaluation,
            tracked_head_sha=self._input.pull_request.head_sha,
            actionable_merge_conflicts=self._actionable_merge_conflicts_enabled(),
            actionable_ci_failures=self._actionable_ci_failures_enabled(evaluation),
        )

    def _should_refresh_pre_resolver_head(
        self,
        *,
        evaluation: Any,
        blockers: list[ReadinessBlockerModel],
    ) -> bool:
        if (
            self._input is None
            or self._resolver_child_workflow_ids
            or not isinstance(evaluation, Mapping)
        ):
            return False
        observed_head_sha = self._head_sha_from_mapping(evaluation)
        if not observed_head_sha:
            return False
        if observed_head_sha == self._input.pull_request.head_sha:
            return False
        return any(blocker.kind == "stale_revision" for blocker in blockers)

    @staticmethod
    def _actionable_merge_conflicts_enabled() -> bool:
        return workflow.patched("merge-automation-actionable-merge-conflict-v1")

    def _actionable_ci_failures_enabled(self, evaluation: Any = None) -> bool:
        if (
            not isinstance(evaluation, Mapping)
            or evaluation.get("actionableCiFailuresVersion") != "v1"
        ):
            return False
        observation_id = evaluation.get("readinessObservationId")
        if not isinstance(observation_id, str) or not observation_id.strip():
            return False
        # An old consumer can have recorded a wait even with new producer
        # evidence. Preserve that observation's branch, while a new poll's
        # Activity ID allows automatic adoption instead of caching False for
        # the entire retained workflow.
        observation_key = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()
        return workflow.patched(
            MERGE_AUTOMATION_ACTIONABLE_CI_FAILURE_PATCH_PREFIX + observation_key
        )

    @staticmethod
    def _missing_ci_wait_enabled(evaluation: Any) -> bool:
        if not isinstance(evaluation, Mapping) or not isinstance(
            evaluation.get("checksReported"), bool
        ):
            # Old producers and unavailable/disabled check reads cannot prove
            # absence. Do not reinterpret their incomplete checks as missing CI.
            return False
        observation_id = evaluation.get("readinessObservationId")
        if not isinstance(observation_id, str) or not observation_id.strip():
            return False
        # A new producer may already have been observed by an old consumer.
        # Preserve that poll on replay, then adopt on the next normal poll.
        observation_key = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()
        return workflow.patched(
            MERGE_AUTOMATION_MISSING_CI_WAIT_PATCH_PREFIX + observation_key
        )

    @staticmethod
    def _resolver_child_failure_summary(error: Exception) -> str:
        prefix = "pr-resolver child workflow failed before returning a result."
        cause: BaseException | None = error
        detail = ""
        for _ in range(8):
            if cause is None:
                break
            detail = str(getattr(cause, "message", None) or cause).strip()
            cause = getattr(cause, "cause", None) or cause.__cause__
        if not detail:
            return prefix
        return f"{prefix} {redact_sensitive_text(detail)}"[:2048]

    async def _failed_resolver_summary(
        self,
        *,
        summary: str,
        blocker_kind: str,
    ) -> dict[str, Any]:
        self._status = STATE_FAILED
        self._summary = summary
        self._blockers = [
            ReadinessBlockerModel.model_validate(
                {
                    "kind": blocker_kind,
                    "summary": summary,
                    "retryable": False,
                    "source": "pr-resolver",
                }
            )
        ]
        self._publish_visibility()
        return await self._finish()

    async def _complete_post_merge_jira(
        self,
        *,
        resolver_disposition: str,
    ) -> bool:
        if self._input is None:
            return True
        if not self._input.config.post_merge_jira.enabled:
            return True
        decision = await workflow.execute_activity(
            "merge_automation.complete_post_merge_jira",
            {
                "parentWorkflowId": self._input.parent_workflow_id,
                "parentRunId": self._input.parent_run_id,
                "resolverDisposition": resolver_disposition,
                "pullRequest": self._input.pull_request.model_dump(
                    by_alias=True, mode="json"
                ),
                "jiraIssueKey": self._input.jira_issue_key,
                "postMergeJira": self._input.config.post_merge_jira.model_dump(
                    by_alias=True, mode="json"
                ),
                "candidateContext": {
                    "publishContextIssueKey": self._input.jira_issue_key,
                    "prMetadataKeys": [self._input.jira_issue_key]
                    if self._input.jira_issue_key
                    else [],
                },
            },
            start_to_close_timeout=timedelta(minutes=2),
            task_queue=INTEGRATIONS_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        decision_map = dict(decision) if isinstance(decision, Mapping) else {}
        self._post_merge_jira_result = {
            key: value
            for key, value in decision_map.items()
            if key not in {"issueResolution", "transition", "artifactRefs"}
        }
        resolution = decision_map.get("issueResolution")
        if isinstance(resolution, Mapping):
            artifact_id = await self._write_json_artifact(
                name="artifacts/merge_automation/post_merge_jira_resolution.json",
                payload=dict(resolution),
            )
            if artifact_id:
                self._post_merge_jira_resolution_artifact_ref = artifact_id
        transition = decision_map.get("transition")
        if isinstance(transition, Mapping):
            artifact_id = await self._write_json_artifact(
                name="artifacts/merge_automation/post_merge_jira_transition.json",
                payload=dict(transition),
            )
            if artifact_id:
                self._post_merge_jira_transition_artifact_ref = artifact_id

        if self._post_merge_jira_resolution_artifact_ref:
            self._post_merge_jira_result["artifactRefs"] = {
                "resolution": self._post_merge_jira_resolution_artifact_ref
            }
        if self._post_merge_jira_transition_artifact_ref:
            self._post_merge_jira_result.setdefault("artifactRefs", {})[
                "transition"
            ] = self._post_merge_jira_transition_artifact_ref

        status = str(decision_map.get("status") or "").strip()
        required = bool(decision_map.get("required", True))
        if required and status in {"blocked", "failed"}:
            reason = str(
                decision_map.get("reason")
                or "Required post-merge Jira completion did not succeed."
            ).strip()
            self._status = STATE_FAILED
            self._summary = reason
            self._blockers = [
                ReadinessBlockerModel.model_validate(
                    {
                        "kind": DISPOSITION_FAILED,
                        "summary": reason,
                        "retryable": False,
                        "source": "jira",
                    }
                )
            ]
            self._publish_visibility()
            return False
        return True

    async def _complete_post_merge_github(
        self,
        *,
        resolver_disposition: str,
    ) -> bool:
        if self._input is None:
            return True
        if not self._input.config.post_merge_github.enabled:
            return True
        config = self._input.config.post_merge_github.model_dump(by_alias=True, mode="json")
        # Preserve the Activity payload for historical inputs without an override.
        if config.get("completionTargetRef") is None:
            config.pop("completionTargetRef", None)
        decision = await workflow.execute_activity(
            "merge_automation.complete_post_merge_github",
            {
                "parentWorkflowId": self._input.parent_workflow_id,
                "parentRunId": self._input.parent_run_id,
                "resolverDisposition": resolver_disposition,
                "pullRequest": self._input.pull_request.model_dump(
                    by_alias=True, mode="json"
                ),
                "postMergeGithub": config,
            },
            start_to_close_timeout=timedelta(minutes=2),
            task_queue=INTEGRATIONS_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        decision_map = dict(decision) if isinstance(decision, Mapping) else {}
        self._post_merge_github_result = decision_map
        status = str(decision_map.get("status") or "").strip()
        required = bool(decision_map.get("required", True))
        if required and status in {"blocked", "failed"}:
            reason = str(
                decision_map.get("reason")
                or decision_map.get("summary")
                or "Required post-merge GitHub completion did not succeed."
            ).strip()
            self._status = STATE_FAILED
            self._summary = reason
            self._blockers = [
                ReadinessBlockerModel.model_validate(
                    {
                        "kind": DISPOSITION_FAILED,
                        "summary": reason,
                        "retryable": False,
                        "source": "github",
                    }
                )
            ]
            self._publish_visibility()
            return False
        return True

    async def _complete_post_merge_integrations(
        self,
        *,
        resolver_disposition: str,
    ) -> bool:
        if not await self._complete_post_merge_jira(
            resolver_disposition=resolver_disposition
        ):
            return False
        return await self._complete_post_merge_github(
            resolver_disposition=resolver_disposition
        )

    def _finish_mode(self) -> str:
        if self._input is None:
            return FINISH_MODE_MERGE
        return self._input.config.finish_mode

    def _review_loop_config(self) -> Any:
        return self._input.config.review_loop

    def _review_loop_active(self) -> bool:
        if self._input is None:
            return False
        return bool(self._review_loop_enabled and self._review_loop_config().enabled)

    def _review_request_continuation(
        self,
        resolver_result: Mapping[str, Any],
        *,
        resolver_workflow_id: str,
    ) -> dict[str, Any]:
        """Validate the child's typed request for one fresh automated review."""

        raw = resolver_result.get("gatedContinuation")
        if not isinstance(raw, Mapping):
            raise ValueError("missing gated continuation contract")
        if (
            resolver_result.get("completionDisposition") != "gated_continuation"
            or raw.get("schemaVersion") != "gated-continuation/v2"
            or raw.get("gateType") != "merge_automation"
            or raw.get("action") != DISPOSITION_REQUEST_REVIEW
        ):
            raise ValueError("invalid review request continuation contract")
        required_text = (
            "reason",
            "provider",
            "executionRef",
            "headSha",
            "ownerWorkflowId",
            "ownerRunId",
            "ownerWorkflowType",
            "childWorkflowId",
            "childRunId",
        )
        if any(not str(raw.get(key) or "").strip() for key in required_text):
            raise ValueError("incomplete review request continuation contract")
        info = workflow.info()
        if (
            raw.get("ownerWorkflowId") != info.workflow_id
            or raw.get("ownerRunId") != info.run_id
            or raw.get("ownerWorkflowType") != WORKFLOW_NAME
            or raw.get("childWorkflowId") != resolver_workflow_id
        ):
            raise ValueError("review request continuation ownership mismatch")
        if (
            raw.get("childRunId") != resolver_result.get("childRunId")
            or raw.get("executionRef") != resolver_result.get("executionRef")
        ):
            raise ValueError("review request continuation identity mismatch")
        head_sha = str(raw.get("headSha") or "").strip().lower()
        if not (7 <= len(head_sha) <= 64) or any(
            c not in "0123456789abcdef" for c in head_sha
        ):
            raise ValueError("invalid review request continuation headSha")
        expected_head = str(resolver_result.get("headSha") or "").strip().lower()
        if expected_head and expected_head != head_sha:
            raise ValueError("review request continuation headSha mismatch")
        provider = str(raw.get("provider") or "").strip().lower()
        if provider != self._review_loop_config().provider:
            # The child may only ask for the *configured* provider; it never
            # supplies request text and never selects a different reviewer.
            raise ValueError("review request continuation provider not configured")
        return {
            "provider": provider,
            "headSha": head_sha,
            "reason": str(raw.get("reason") or "").strip(),
            "executionRef": str(raw.get("executionRef") or "").strip(),
            "progressSignature": str(raw.get("progressSignature") or "").strip()
            or None,
        }

    def _register_progress_signature(self, signature: str | None) -> bool:
        """Record a cycle signature and report whether progress was made."""

        normalized = str(signature or "").strip()
        if not normalized:
            # No signature is not evidence of progress, but it is also not
            # evidence of a stall; treat it as neutral.
            return True
        if normalized == self._last_progress_signature:
            self._no_progress_cycles += 1
            return False
        self._last_progress_signature = normalized
        self._no_progress_cycles = 0
        return True

    async def _write_review_cycle_artifact(self, cycle: Mapping[str, Any]) -> None:
        await self._write_json_artifact(
            name=(
                "artifacts/merge_automation/review_cycles/"
                f"{cycle.get('cycle')}.json"
            ),
            payload={"status": self._status, "cycle": dict(cycle)},
        )

    async def _blocked_review_summary(
        self,
        *,
        summary: str,
        blocker_kind: str,
    ) -> dict[str, Any]:
        self._status = STATE_BLOCKED
        self._summary = summary
        self._blockers = [
            ReadinessBlockerModel.model_validate(
                {
                    "kind": blocker_kind,
                    "summary": summary,
                    "retryable": False,
                    "source": "merge_automation",
                }
            )
        ]
        self._publish_visibility()
        return await self._finish()

    def _review_cycle_budget_blocker(self) -> ReadinessBlockerModel | None:
        config = self._review_loop_config()
        if len(self._review_cycles) < config.max_cycles:
            return None
        return ReadinessBlockerModel(
            kind="review_cycle_budget_exhausted",
            summary=(
                "Automated review loop stopped: the configured budget of "
                f"{config.max_cycles} review cycles is exhausted."
            ),
            retryable=False,
            source="merge_automation",
        )

    async def _request_automated_review(
        self,
        *,
        resolver_result: Mapping[str, Any],
        resolver_workflow_id: str,
    ) -> dict[str, Any] | None:
        """Own the review request side effect; return a terminal payload or None.

        ``None`` means "keep looping": the gate re-opens and either waits for the
        request result or adopts a newly observed head.
        """

        if self._input is None:
            return None
        if not self._review_loop_active():
            return await self._failed_resolver_summary(
                summary=(
                    "pr-resolver requested an automated review, but no review "
                    "loop is configured for this merge automation run."
                ),
                blocker_kind="resolver_continuation_invalid",
            )
        try:
            continuation = self._review_request_continuation(
                resolver_result,
                resolver_workflow_id=resolver_workflow_id,
            )
        except (TypeError, ValueError):
            if self._continuation_observability_enabled:
                self._continuation_counters["continuation_rejected_schema"] += 1
            return await self._failed_resolver_summary(
                summary="pr-resolver returned an invalid review request continuation.",
                blocker_kind="resolver_continuation_invalid",
            )

        return await self._post_automated_review(
            head_sha=continuation["headSha"],
            progress_signature=continuation.get("progressSignature"),
        )

    async def _post_automated_review(
        self,
        *,
        head_sha: str,
        progress_signature: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Post through the existing owning activity and durable request ledger."""

        config = self._review_loop_config()
        self._input.pull_request.head_sha = head_sha
        made_progress = self._register_progress_signature(progress_signature)
        if (
            not made_progress
            and self._no_progress_cycles >= config.max_consecutive_no_progress_cycles
        ):
            return await self._blocked_review_summary(
                summary=(
                    "Automated review loop stopped: "
                    f"{self._no_progress_cycles} consecutive cycles produced the "
                    "same outstanding work on the same head SHA."
                ),
                blocker_kind="review_loop_no_progress",
            )
        budget_blocker = self._review_cycle_budget_blocker()
        if budget_blocker is not None:
            return await self._blocked_review_summary(
                summary=budget_blocker.summary, blocker_kind=budget_blocker.kind
            )

        request_key = build_review_request_key(
            parent_workflow_id=self._resolver_parent_workflow_id(),
            repository=self._input.pull_request.repo,
            pr_number=self._input.pull_request.number,
            head_sha=head_sha,
            provider=config.provider,
        )
        request_payload = {
            "parentWorkflowId": self._resolver_parent_workflow_id(),
            "repository": self._input.pull_request.repo,
            "prNumber": self._input.pull_request.number,
            "expectedHeadSha": head_sha,
            "provider": config.provider,
            "requestKey": request_key,
        }
        if self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
            request_payload.update(
                {
                    "finishMode": FINISH_MODE_REVIEW_ONLY,
                    "parentExecutionPlan": self._input.parent_execution_plan.model_dump(
                        by_alias=True, mode="json"
                    ),
                    "principal": self._principal(),
                    "admittedParentWorkflowId": self._input.parent_workflow_id,
                    "parentRunId": self._input.parent_run_id,
                    **({"expiresAt": expires_at.isoformat()} if expires_at else {}),
                }
            )
        try:
            outcome = await workflow.execute_activity(
                "merge_automation.request_automated_review",
                request_payload,
                start_to_close_timeout=timedelta(minutes=2),
                task_queue=INTEGRATIONS_TASK_QUEUE,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
        except CancelledError:
            raise
        except Exception:
            return await self._blocked_review_summary(
                summary=(
                    "Automated review request could not be proven successful for "
                    f"head {head_sha}."
                ),
                blocker_kind="automated_review_request_failed",
            )

        outcome_map = dict(outcome) if isinstance(outcome, Mapping) else {}
        status = str(outcome_map.get("status") or "").strip()
        if status == "expired" and self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
            self._status = STATE_EXPIRED
            self._summary = "Review deadline expired before a new request was posted."
            self._publish_visibility()
            return await self._finish()
        if status not in REVIEW_REQUEST_POSTED_STATUSES:
            if status in REVIEW_REQUEST_RETRY_GATE_STATUSES:
                if self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
                    return await self._blocked_review_summary(
                        summary="The pull request changed before its review was requested.",
                        blocker_kind="stale_revision",
                    )
                observed = str(outcome_map.get("observedHeadSha") or "").strip()
                if observed and observed != head_sha:
                    self._input.pull_request.head_sha = observed
                else:
                    self._refresh_tracked_head_sha_on_next_evaluation = True
                self._active_review_request = None
                self._status = STATE_WAITING
                self._summary = (
                    "Automated review request skipped; the pull request changed "
                    "before the request was posted."
                )
                self._publish_visibility()
                return None
            return await self._blocked_review_summary(
                summary=(
                    "Automated review request could not be proven successful: "
                    f"{outcome_map.get('summary') or status or 'unknown outcome'}"
                ),
                blocker_kind="automated_review_request_failed",
            )

        if self._finish_mode() == FINISH_MODE_REVIEW_ONLY and not (
            outcome_map.get("headSha") == head_sha
            and self._review_only_request_is_bound(
                {**outcome_map, "requestKey": request_key}
            )
        ):
            return await self._blocked_review_summary(
                summary="The review request receipt does not prove the requested head.",
                blocker_kind="automated_review_request_failed",
            )
        request_comment_id = outcome_map.get("requestCommentId")
        cycle = {
            "cycle": len(self._review_cycles) + 1,
            "provider": config.provider,
            "headSha": head_sha,
            "requestKey": request_key,
            "requestCommentId": request_comment_id,
            "requestedAt": outcome_map.get("requestedAt"),
            "completionKind": None,
            "completionId": None,
            "completedAt": None,
            "status": "requested",
            "progressSignature": progress_signature,
        }
        self._review_cycles.append(cycle)
        self._active_review_request = {
            "provider": config.provider,
            "headSha": head_sha,
            "requestKey": request_key,
            "requestCommentId": request_comment_id,
            "requestedAt": outcome_map.get("requestedAt"),
        }
        self._status = STATE_WAITING
        self._summary = (
            f"Requested a fresh {config.provider} review for head {head_sha}."
        )
        self._publish_visibility()
        await self._write_review_cycle_artifact(cycle)
        return None

    def _active_review_cycle_matches(self, *, allow_missing_time: bool = False) -> bool:
        active = self._active_review_request
        cycle = self._review_cycles[-1] if self._review_cycles else None
        if (
            not active
            or not cycle
            or any(
                active.get(key) != cycle.get(key)
                for key in ("provider", "headSha", "requestKey", "requestCommentId")
            )
        ):
            return False
        active_at = _parse_review_timestamp(active.get("requestedAt"))
        cycle_at = _parse_review_timestamp(cycle.get("requestedAt"))
        return active_at == cycle_at or (
            allow_missing_time and (active_at is None or cycle_at is None)
        )

    def _review_adoption_blocker(
        self, evaluation: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Enforce retained request/cycle identity on new observations."""
        active = self._active_review_request
        observation = evaluation.get("readinessObservationId")
        selected = evaluation.get("automatedReviewRequestCommentId")
        if (
            not active
            or not isinstance(observation, str)
            or not observation.strip()
            or selected is None
            or evaluation.get("headSha") != active.get("headSha")
            or evaluation.get("automatedReviewRequestStale") is True
        ):
            return None
        observation_key = hashlib.sha256(observation.encode("utf-8")).hexdigest()
        if not workflow.patched(
            MERGE_AUTOMATION_REVIEW_ADOPTION_GUARD_PATCH_PREFIX + observation_key
        ):
            return None
        if self._review_cycles and not self._active_review_cycle_matches(
            allow_missing_time=True
        ):
            return {
                "kind": "automated_review_request_failed",
                "summary": "The active review request does not match its retained cycle identity.",
                "retryable": False,
                "source": "policy",
            }
        active_at = _parse_review_timestamp(active.get("requestedAt"))
        selected_at = _parse_review_timestamp(
            evaluation.get("automatedReviewRequestedAt")
        )
        cycle_at = (
            _parse_review_timestamp(self._review_cycles[-1].get("requestedAt"))
            if self._review_cycles
            else None
        )
        if (
            selected == active.get("requestCommentId")
            and selected_at is not None
            and any(
                retained is not None and retained != selected_at
                for retained in (active_at, cycle_at)
            )
        ):
            return {
                "kind": "automated_review_request_failed",
                "summary": "The observed request timestamp conflicts with its retained identity.",
                "retryable": False,
                "source": "policy",
            }
        return None

    def _selected_review_request_cycle_budget_enabled(
        self, observation_key: str
    ) -> bool:
        # Selection markers already exist in retained histories. A separate
        # observation decision preserves their adoption while bounding the
        # next new request seen after the workflow worker upgrades.
        return workflow.patched(
            MERGE_AUTOMATION_SELECTED_REVIEW_REQUEST_CYCLE_BUDGET_PATCH_PREFIX
            + observation_key
        )

    def _reconcile_selected_review_request(
        self, evaluation: Mapping[str, Any]
    ) -> ReadinessBlockerModel | None:
        """Retain a superseding provider request before settling its result.

        The GitHub Activity owns request selection. Keep each observed request
        in the existing cycle ledger so restored state never attributes B's
        completion to A. Old Activity results and recorded histories keep their
        original interpretation.
        """

        active = self._active_review_request
        selected_id = evaluation.get("automatedReviewRequestCommentId")
        selected_at = evaluation.get("automatedReviewRequestedAt")
        observation_id = evaluation.get("readinessObservationId")
        if (
            active is None
            or not isinstance(selected_id, int)
            or isinstance(selected_id, bool)
            or selected_id <= 0
            or _parse_review_timestamp(selected_at) is None
            or evaluation.get("headSha") != active.get("headSha")
            or evaluation.get("automatedReviewRequestStale") is True
            or not isinstance(observation_id, str)
            or not observation_id.strip()
        ):
            return
        # Reuse the Activity's observation identity so an old consumer's
        # recorded branch stays unchanged while a fresh poll can adopt B.
        observation_key = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()
        if not workflow.patched(
            MERGE_AUTOMATION_SELECTED_REVIEW_REQUEST_PATCH_PREFIX + observation_key
        ):
            return
        if selected_id == active.get("requestCommentId"):
            if _parse_review_timestamp(active.get("requestedAt")) is None:
                active["requestedAt"] = selected_at
            if (
                self._review_cycles
                and _parse_review_timestamp(self._review_cycles[-1].get("requestedAt"))
                is None
            ):
                self._review_cycles[-1]["requestedAt"] = selected_at
            return
        if not self._review_cycles:
            self._review_cycles.append({"cycle": 1, **active, "status": "requested"})
        if self._selected_review_request_cycle_budget_enabled(observation_key):
            budget_blocker = self._review_cycle_budget_blocker()
            if budget_blocker is not None:
                return budget_blocker
        previous = self._review_cycles[-1]
        if previous.get("status") == "requested":
            previous["status"] = "superseded"
        selected = {
            "provider": active["provider"],
            "headSha": active["headSha"],
            "requestKey": active["requestKey"],
            "requestCommentId": selected_id,
            "requestedAt": selected_at,
        }
        self._review_cycles.append(
            {
                "cycle": len(self._review_cycles) + 1,
                **selected,
                "status": "requested",
                "progressSignature": previous.get("progressSignature"),
                "completionKind": None,
                "completionId": None,
                "completedAt": None,
            }
        )
        self._active_review_request = selected

    def _review_failure_settlement_enabled(self, observation: Any) -> bool:
        if not isinstance(observation, str) or not observation.strip():
            return False
        observation_key = hashlib.sha256(observation.encode("utf-8")).hexdigest()
        return workflow.patched(
            MERGE_AUTOMATION_REVIEW_FAILURE_SETTLEMENT_PATCH_PREFIX + observation_key
        )


    def _settle_active_review_request(
        self, evaluation: Any
    ) -> ReadinessBlockerModel | None:
        """Bind an observed review result (or staleness) to the active request."""

        if not self._active_review_request or not isinstance(evaluation, Mapping):
            return
        adoption_blocker = self._review_adoption_blocker(evaluation)
        if adoption_blocker is not None:
            return ReadinessBlockerModel.model_validate(adoption_blocker)
        budget_blocker = self._reconcile_selected_review_request(evaluation)
        if budget_blocker is not None:
            return budget_blocker
        cycle = self._review_cycles[-1] if self._review_cycles else None
        if self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
            # The provider activity owns classification and request matching.
            # An incomplete or different-head projection cannot settle this
            # exact-head objective or make its restored request disappear.
            if (
                evaluation.get("headSha") != self._input.pull_request.head_sha
                or evaluation.get("automatedReviewRequestStale") is True
                or cycle is None
                or cycle.get("requestKey") != self._active_review_request.get("requestKey")
            ):
                return
            if evaluation.get("automatedReviewComplete") is True:
                if not _review_completion_is_fresh(
                    requested_at=self._active_review_request.get("requestedAt"),
                    completed_at=evaluation.get("automatedReviewCompletedAt"),
                    completion_kind=evaluation.get("automatedReviewCompletionKind"),
                    completion_id=evaluation.get("automatedReviewCompletionId"),
                ):
                    return
        failure_payload = evaluation.get("automatedReviewRequestFailure")
        if failure_payload is not None and self._review_failure_settlement_enabled(
            evaluation.get("readinessObservationId")
        ):
            invalid_failure = ReadinessBlockerModel(
                kind="automated_review_request_failed",
                source="policy",
                retryable=False,
                summary="The refusal does not prove a terminal result for the active review request.",
            )
            try:
                failure = AutomatedReviewFailureModel.model_validate(failure_payload)
            except ValueError:
                return invalid_failure
            requested_at = _parse_review_timestamp(
                self._active_review_request.get("requestedAt")
            )
            failed_at = _parse_review_timestamp(failure.failed_at)
            provider_refusal = any(
                isinstance(blocker, Mapping)
                and blocker.get("kind") == "automated_review_request_failed"
                and blocker.get("source") == self._active_review_request.get("provider")
                and isinstance(blocker.get("providerFailure"), Mapping)
                and blocker["providerFailure"].get("providerErrorClass")
                == failure.provider_error_class
                for blocker in (evaluation.get("blockers") or [])
            )
            active_comment_id = self._active_review_request.get("requestCommentId")
            selected_comment_id = evaluation.get("automatedReviewRequestCommentId")
            if (
                not isinstance(active_comment_id, int)
                or isinstance(active_comment_id, bool)
                or active_comment_id <= 0
                or not isinstance(selected_comment_id, int)
                or isinstance(selected_comment_id, bool)
                or evaluation.get("automatedReviewComplete") is True
                or evaluation.get("automatedReviewRequestStale") is True
                or evaluation.get("headSha") != self._active_review_request.get("headSha")
                or evaluation.get("pullRequestOpen") is not True
                or evaluation.get("pullRequestMerged") is True
                or evaluation.get("automatedReviewRequestCommentId")
                != self._active_review_request.get("requestCommentId")
                or _parse_review_timestamp(evaluation.get("automatedReviewRequestedAt"))
                != requested_at
                or not self._active_review_cycle_matches()
                or not provider_refusal
                or requested_at is None
                or failed_at is None
                or not review_comment_is_after(
                    failed_at,
                    failure.id,
                    requested_at,
                    self._active_review_request.get("requestCommentId"),
                )
            ):
                return invalid_failure
            cycle["status"] = "failed"
            cycle["requestFailure"] = failure.model_dump(by_alias=True, mode="json")
            self._active_review_request = None
            return None
        if evaluation.get("automatedReviewComplete") is True:
            if cycle is not None:
                cycle["completionKind"] = evaluation.get("automatedReviewCompletionKind")
                cycle["completionId"] = evaluation.get("automatedReviewCompletionId")
                cycle["completedAt"] = evaluation.get("automatedReviewCompletedAt")
                cycle["status"] = "completed"
            self._active_review_request = None
            return
        if evaluation.get("automatedReviewRequestStale") is True:
            # The head moved while waiting: the pending request can no longer
            # answer for the current revision, so it is invalidated and the next
            # cycle starts from the new head.
            if cycle is not None:
                cycle["status"] = "stale"
            self._active_review_request = None
            self._refresh_tracked_head_sha_on_next_evaluation = True


    async def _evaluate_readiness_once(
        self,
    ) -> tuple[Any, Any, dict[str, Any] | None]:
        if self._input is None:
            evaluation: dict[str, Any] = {}
            return (
                evaluation,
                classify_readiness(
                    evaluation,
                    tracked_head_sha="",
                    actionable_merge_conflicts=self._actionable_merge_conflicts_enabled(),
                    actionable_ci_failures=self._actionable_ci_failures_enabled(evaluation),
                ),
                None,
            )
        readiness_payload = self._input.model_dump(by_alias=True, mode="json")
        if self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
            # Review completion is independent of CI, including unavailable
            # check APIs under a deliberately limited repository connection.
            readiness_payload["mergeAutomationConfig"]["gate"]["github"]["checks"] = (
                "disabled"
            )
            readiness_payload["mergeAutomationConfig"]["gate"]["jira"]["status"] = (
                "disabled"
            )
        # Always publish the *live* request state so a restored input can never
        # make a settled request look active again.
        readiness_payload["activeReviewRequest"] = (
            dict(self._active_review_request) if self._active_review_request else None
        )
        evaluation = await workflow.execute_activity(
            "merge_automation.evaluate_readiness",
            readiness_payload,
            start_to_close_timeout=timedelta(minutes=2),
            task_queue=INTEGRATIONS_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
            cancellation_type=ActivityCancellationType.TRY_CANCEL,
        )
        receipt_rejected = False
        if (
            isinstance(evaluation, Mapping)
            and evaluation.get("automatedReviewRequestFailure") is not None
        ):
            evaluation = dict(evaluation)
            if not self._review_failure_settlement_enabled(
                evaluation.get("readinessObservationId")
            ):
                # Older consumers did not interpret this new receipt. Drop it
                # before typed reconstruction while replaying their observation.
                evaluation.pop("automatedReviewRequestFailure", None)
            else:
                try:
                    AutomatedReviewFailureModel.model_validate(
                        evaluation["automatedReviewRequestFailure"]
                    )
                except ValueError:
                    receipt_rejected = True
                    evaluation.pop("automatedReviewRequestFailure", None)
                    evaluation.update(
                        automatedReviewComplete=None,
                        automatedReviewCompletionKind=None,
                        automatedReviewCompletionId=None,
                        automatedReviewCompletedAt=None,
                    )
                    evaluation["blockers"] = [
                        *(evaluation.get("blockers") or []),
                        {
                            "kind": "automated_review_request_failed",
                            "source": "policy",
                            "retryable": False,
                            "summary": "The malformed refusal receipt does not prove a terminal result for the active review request.",
                        },
                    ]
        budget_blocker = None
        if self._review_loop_active():
            if (
                self._active_review_request
                and workflow.patched("merge-automation-active-review-barrier-v1")
                and isinstance(evaluation, Mapping)
                and evaluation.get("automatedReviewComplete") is not True
                and evaluation.get("automatedReviewRequestStale") is not True
            ):
                # Older/in-flight activity results can omit review evidence
                # when CI fails or conflicts are actionable. Unknown cannot
                # release a resolver while this request still owns the head.
                evaluation = {**evaluation, "automatedReviewComplete": False}
            if not receipt_rejected:
                budget_blocker = self._settle_active_review_request(
                    evaluation if isinstance(evaluation, Mapping) else {}
                )
        if budget_blocker is not None and isinstance(evaluation, Mapping):
            # Rejected or malformed receipts are not admitted cycle evidence.
            # Keep their bounded blocker, without passing corrupt data into
            # the typed readiness projection or changing retained history.
            evaluation = dict(evaluation)
            evaluation.pop("automatedReviewRequestFailure", None)
        evidence = classify_readiness(
            evaluation if isinstance(evaluation, Mapping) else {},
            tracked_head_sha=self._input.pull_request.head_sha,
            actionable_merge_conflicts=self._actionable_merge_conflicts_enabled(),
            actionable_ci_failures=self._actionable_ci_failures_enabled(evaluation),
        )
        terminal = None
        if budget_blocker is not None:
            terminal = await self._blocked_review_summary(
                summary=budget_blocker.summary,
                blocker_kind=budget_blocker.kind,
            )
        return evaluation, evidence, terminal

    def _review_only_request_is_bound(
        self, request: Mapping[str, Any], *, require_timestamp: bool = True
    ) -> bool:
        expected_key = build_review_request_key(
            parent_workflow_id=self._resolver_parent_workflow_id(),
            repository=self._input.pull_request.repo,
            pr_number=self._input.pull_request.number,
            head_sha=self._input.pull_request.head_sha,
            provider=self._review_loop_config().provider,
        )
        comment_id = request.get("requestCommentId")
        return bool(
            request.get("headSha") == self._input.pull_request.head_sha
            and request.get("provider") == self._review_loop_config().provider
            and request.get("requestKey") == expected_key
            and isinstance(comment_id, int)
            and not isinstance(comment_id, bool)
            and comment_id > 0
            and (
                not require_timestamp
                or _parse_review_timestamp(request.get("requestedAt")) is not None
            )
        )

    async def _run_review_only(self, *, expire_at: datetime | None) -> dict[str, Any]:
        """Request and await one fresh review without resolver or publication effects."""

        if not self._review_loop_active():
            return await self._blocked_review_summary(
                summary="Review-only automation requires a configured fresh reviewer.",
                blocker_kind="policy_denied",
            )
        if self._active_review_request and (
            not self._review_only_request_is_bound(
                self._active_review_request,
                require_timestamp=not workflow.patched(
                    MERGE_AUTOMATION_RESTORED_REVIEW_IDENTITY_PATCH
                ),
            )
            or not self._review_cycles
            or self._review_cycles[-1].get("requestKey")
            != self._active_review_request.get("requestKey")
            or (
                workflow.patched(MERGE_AUTOMATION_RESTORED_REVIEW_IDENTITY_PATCH)
                and not self._active_review_cycle_matches(allow_missing_time=True)
            )
        ):
            return await self._blocked_review_summary(
                summary="The restored review request is not bound to this owning gate.",
                blocker_kind="automated_review_request_failed",
            )

        while True:
            if expire_at is not None and workflow.now() >= expire_at:
                self._status = STATE_EXPIRED
                self._publish_visibility()
                return await self._finish()
            evaluation, evidence, terminal = await self._evaluate_readiness_once()
            if terminal is not None:
                return terminal
            observed = evaluation if isinstance(evaluation, Mapping) else {}
            self._blockers = list(evidence.blockers)
            await self._write_gate_snapshot(evidence_ready=False)
            # Readiness and artifact Activities may outlast the remaining budget;
            # neither a new request nor late completion can extend the objective.
            if expire_at is not None and workflow.now() >= expire_at:
                self._status = STATE_EXPIRED
                self._publish_visibility()
                return await self._finish()
            if (
                observed.get("headSha") != self._input.pull_request.head_sha
                or observed.get("automatedReviewRequestStale") is True
            ):
                return await self._blocked_review_summary(
                    summary="The pull request head changed during its requested review.",
                    blocker_kind="stale_revision",
                )
            if observed.get("pullRequestOpen") is False or evidence.pull_request_merged:
                return await self._blocked_review_summary(
                    summary="The pull request is no longer open for the requested review.",
                    blocker_kind="pull_request_closed",
                )
            if any(b.kind in TERMINAL_BLOCKER_KINDS for b in self._blockers):
                self._status = STATE_BLOCKED
                self._publish_visibility()
                return await self._finish()
            cycle = self._review_cycles[-1] if self._review_cycles else None
            if (
                observed.get("pullRequestOpen") is True
                and cycle is not None
                and cycle.get("status") == "completed"
                and self._review_only_request_is_bound(cycle)
                and _review_completion_is_fresh(
                    requested_at=cycle.get("requestedAt"),
                    completed_at=cycle.get("completedAt"),
                    completion_kind=cycle.get("completionKind"),
                    completion_id=cycle.get("completionId"),
                )
            ):
                self._status = STATE_REVIEW_COMPLETE
                self._summary = (
                    "The requested fresh review completed for this head. "
                    "CI and any review findings remain separate obligations."
                )
                # Completion was accepted above, after the deadline/head/open
                # checks and request-bound evidence validation. This final
                # snapshot only reports that decision; delayed artifact I/O
                # must not reopen the accepted outcome or imply merge readiness.
                await self._write_gate_snapshot(evidence_ready=True)
                self._publish_visibility()
                return await self._finish()
            # CI and merge-conflict readiness do not delay a review request.
            # Unknown external state does: do not infer permission to post.
            if (
                not self._active_review_request
                and not self._review_cycles
                and observed.get("pullRequestOpen") is True
                and not any(
                    b.kind == "external_state_unavailable" for b in self._blockers
                )
            ):
                terminal = await self._post_automated_review(
                    head_sha=self._input.pull_request.head_sha,
                    expires_at=expire_at,
                )
                if terminal is not None:
                    return terminal
            self._status = STATE_WAITING
            self._publish_visibility()
            timeout = timedelta(
                seconds=self._input.config.timeouts.fallback_poll_seconds
            )
            if expire_at is not None:
                timeout = min(timeout, max(timedelta(0), expire_at - workflow.now()))
            target_event_count = self._external_event_count
            try:
                await workflow.wait_condition(
                    lambda count=target_event_count: self._external_event_count > count,
                    timeout=timeout,
                )
            except TimeoutError:
                # The bounded wait elapsed; poll readiness again on the next loop.
                pass

    async def _recover_after_resolver_issue(
        self,
    ) -> tuple[str, dict[str, Any] | None]:
        if self._input is None:
            return RESOLVER_ISSUE_RECOVERY_NONE, None
        if not workflow.patched("merge-automation-post-resolver-merged-recovery-v1"):
            return RESOLVER_ISSUE_RECOVERY_NONE, None
        previous_head_sha = self._input.pull_request.head_sha
        evaluation, evidence, terminal = await self._evaluate_readiness_once()
        if terminal is not None:
            return RESOLVER_ISSUE_RECOVERY_COMPLETED, terminal
        observed_head_sha = (
            self._head_sha_from_mapping(evaluation)
            if isinstance(evaluation, Mapping)
            else ""
        )
        head_advanced = bool(
            observed_head_sha and observed_head_sha != previous_head_sha
        )
        if self._refresh_tracked_head_sha(evaluation):
            evidence = classify_readiness(
                evaluation if isinstance(evaluation, Mapping) else {},
                tracked_head_sha=self._input.pull_request.head_sha,
                actionable_merge_conflicts=self._actionable_merge_conflicts_enabled(),
                actionable_ci_failures=self._actionable_ci_failures_enabled(evaluation),
            )
        self._blockers = list(evidence.blockers)
        await self._write_gate_snapshot(evidence_ready=evidence.ready)
        if not evidence.pull_request_merged:
            if head_advanced and workflow.patched(
                MERGE_AUTOMATION_POST_RESOLVER_PROGRESS_RECOVERY_PATCH
            ):
                self._status = STATE_WAITING
                self._summary = (
                    "pr-resolver advanced the pull request head before ending "
                    "incompletely; returning to the merge gate."
                )
                self._publish_visibility()
                return RESOLVER_ISSUE_RECOVERY_REENTER_GATE, None
            return RESOLVER_ISSUE_RECOVERY_NONE, None
        self._summary = (
            "Pull request is already merged; recovered after resolver "
            "disposition validation failed."
        )
        if not await self._complete_post_merge_integrations(
            resolver_disposition=DISPOSITION_ALREADY_MERGED
        ):
            return RESOLVER_ISSUE_RECOVERY_COMPLETED, await self._finish()
        self._status = STATE_ALREADY_MERGED
        self._publish_visibility()
        return RESOLVER_ISSUE_RECOVERY_COMPLETED, await self._finish()

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._input = MergeAutomationStartInput.model_validate(payload)
        if self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
            parent = workflow.info().parent
            if (
                parent is None
                or parent.workflow_id != self._input.parent_workflow_id
                or parent.run_id != self._input.parent_run_id
            ):
                raise ApplicationError(
                    "review_only requires its owning Temporal parent",
                    type="ReviewOnlyParentAuthorityMismatch",
                    non_retryable=True,
                )
        self._continuation_observability_enabled = workflow.patched(
            MERGE_AUTOMATION_CONTINUATION_OBSERVABILITY_PATCH
        )
        self._resolver_attempt_titles_enabled = workflow.patched(
            MERGE_AUTOMATION_RESOLVER_ATTEMPT_TITLE_PATCH
        )
        self._review_loop_enabled = workflow.patched(
            MERGE_AUTOMATION_REVIEW_LOOP_PATCH
        )
        self._verdict_routing_enabled = workflow.patched(
            MERGE_AUTOMATION_PR_RESOLVER_VERDICT_ROUTING_PATCH
        )
        self._review_cycles = [
            cycle.model_dump(by_alias=True, mode="json")
            for cycle in self._input.review_cycles
        ]
        self._active_review_request = (
            self._input.active_review_request.model_dump(by_alias=True, mode="json")
            if self._input.active_review_request is not None
            else None
        )
        self._status = STATE_WAITING
        expire_at = _effective_expire_at(self._input, started_at=workflow.now())
        self._publish_visibility()

        if self._finish_mode() == FINISH_MODE_REVIEW_ONLY:
            return await self._run_review_only(expire_at=expire_at)

        while True:
            if expire_at is not None and workflow.now() >= expire_at:
                self._status = STATE_EXPIRED
                self._publish_visibility()
                return await self._finish()

            evaluation, evidence, terminal = await self._evaluate_readiness_once()
            if terminal is not None:
                return terminal
            if self._refresh_tracked_head_sha_on_next_evaluation:
                self._refresh_tracked_head_sha_on_next_evaluation = False
                if self._refresh_tracked_head_sha(evaluation):
                    evidence = classify_readiness(
                        evaluation if isinstance(evaluation, Mapping) else {},
                        tracked_head_sha=self._input.pull_request.head_sha,
                        actionable_merge_conflicts=(
                            self._actionable_merge_conflicts_enabled()
                        ),
                        actionable_ci_failures=self._actionable_ci_failures_enabled(evaluation),
                    )
            if workflow.patched("merge-automation-refresh-stale-current-head"):
                evidence = self._refresh_current_head_for_stale_wait(
                    evaluation=evaluation,
                    evidence=evidence,
                )
            elif workflow.patched(
                "merge-automation-pre-resolver-head-refresh-v1"
            ) and self._should_refresh_pre_resolver_head(
                evaluation=evaluation,
                blockers=list(evidence.blockers),
            ):
                self._refresh_tracked_head_sha(evaluation)
                evidence = classify_readiness(
                    evaluation if isinstance(evaluation, Mapping) else {},
                    tracked_head_sha=self._input.pull_request.head_sha,
                    actionable_merge_conflicts=self._actionable_merge_conflicts_enabled(),
                    actionable_ci_failures=self._actionable_ci_failures_enabled(evaluation),
                )
            self._blockers = list(evidence.blockers)
            await self._write_gate_snapshot(evidence_ready=evidence.ready)
            if evidence.pull_request_merged:
                self._refresh_tracked_head_sha(evaluation)
                if not await self._complete_post_merge_integrations(
                    resolver_disposition=DISPOSITION_ALREADY_MERGED
                ):
                    return await self._finish()
                self._status = STATE_ALREADY_MERGED
                self._publish_visibility()
                return await self._finish()
            if self._missing_ci_wait_enabled(evaluation):
                if evidence.checks_reported is False and any(
                    blocker.kind == "checks_running" for blocker in self._blockers
                ):
                    made_progress = self._register_progress_signature(
                        f"missing-ci|{evidence.head_sha}"
                    )
                    if (
                        not made_progress
                        and self._no_progress_cycles
                        >= self._review_loop_config().max_consecutive_no_progress_cycles
                    ):
                        return await self._blocked_review_summary(
                            summary=(
                                "No CI checks or gating statuses have reported for "
                                "the current head after the no-progress budget. "
                                "Check the CI workflow triggers for this PR's base "
                                "and head before retrying merge automation."
                            ),
                            blocker_kind="review_loop_no_progress",
                        )
                elif evidence.checks_reported is True and str(
                    self._last_progress_signature or ""
                ).startswith("missing-ci|"):
                    # Even queued/running checks establish that CI has started.
                    # A later missing signal begins a fresh bounded wait.
                    self._last_progress_signature = None
                    self._no_progress_cycles = 0
            if evidence.ready:
                self._status = STATE_EXECUTING
                self._publish_visibility()
                resolver_parent_workflow_id = self._input.parent_workflow_id
                if workflow.patched(MERGE_AUTOMATION_RESOLVER_PARENT_GATE_ID_PATCH):
                    resolver_parent_workflow_id = self._resolver_parent_workflow_id()
                resolver_request = build_resolver_run_request(
                    parent_workflow_id=resolver_parent_workflow_id,
                    pull_request=self._input.pull_request,
                    jira_issue_key=self._input.jira_issue_key,
                    merge_method=self._input.config.resolver.merge_method,
                    resolver_template=self._input.resolver_template,
                    review_loop=(
                        self._review_loop_config()
                        if self._review_loop_active()
                        else None
                    ),
                    finish_mode=self._finish_mode(),
                    legacy_capabilities=not workflow.patched(
                        MERGE_AUTOMATION_RESOLVER_VERIFICATION_CAPABILITY_PATCH
                    ),
                )
                resolver_workflow_id_factory = (
                    deterministic_resolver_idempotency_key
                    if workflow.patched("merge-automation-hashed-resolver-child-id")
                    else legacy_resolver_idempotency_key
                )
                resolver_workflow_id = resolver_workflow_id_factory(
                    parent_workflow_id=self._input.parent_workflow_id,
                    repo=self._input.pull_request.repo,
                    pr_number=self._input.pull_request.number,
                    head_sha=self._input.pull_request.head_sha,
                )
                resolver_attempt = len(self._resolver_child_workflow_ids) + 1
                resolver_workflow_id = f"{resolver_workflow_id}:{resolver_attempt}"
                if workflow.patched(MERGE_AUTOMATION_OMNIGENT_RESOLVER_PLAN_PATCH):
                    resolver_request = await self._prepare_omnigent_resolver_request(
                        resolver_request,
                        resolver_workflow_id=resolver_workflow_id,
                    )
                self._resolver_child_workflow_ids.append(resolver_workflow_id)
                child_kwargs: dict[str, Any] = {
                    "id": resolver_workflow_id,
                    "task_queue": self._workflow_child_task_queue(),
                    "search_attributes": self._resolver_search_attributes(),
                    "cancellation_type": ChildWorkflowCancellationType.TRY_CANCEL,
                    "static_summary": "Resolving pull request for merge automation",
                    "static_details": f"Resolve {self._input.pull_request.url}",
                }
                if workflow.patched("merge-automation-resolver-child-visibility-memo"):
                    child_kwargs["memo"] = self._resolver_child_memo(
                        resolver_request,
                        attempt=(
                            resolver_attempt
                            if self._resolver_attempt_titles_enabled
                            else None
                        ),
                    )
                try:
                    resolver_result = await workflow.execute_child_workflow(
                        "MoonMind.UserWorkflow",
                        resolver_request,
                        **child_kwargs,
                    )
                except CancelledError:
                    self._status = STATE_CANCELED
                    self._summary = (
                        "Merge automation canceled while resolver child was active."
                    )
                    await self._write_resolver_attempt(workflow_id=resolver_workflow_id)
                    self._publish_visibility()
                    return await self._finish()
                except Exception as exc:
                    await self._write_resolver_attempt(workflow_id=resolver_workflow_id)
                    recovery, recovered = await self._recover_after_resolver_issue()
                    if recovered is not None:
                        return recovered
                    if recovery == RESOLVER_ISSUE_RECOVERY_REENTER_GATE:
                        continue
                    summary = "pr-resolver child workflow failed before returning a result."
                    if workflow.patched(MERGE_AUTOMATION_RESOLVER_FAILURE_SUMMARY_PATCH):
                        summary = self._resolver_child_failure_summary(exc)
                    return await self._failed_resolver_summary(
                        summary=summary,
                        blocker_kind=DISPOSITION_FAILED,
                    )
                await self._write_resolver_attempt(
                    workflow_id=resolver_workflow_id,
                    result=resolver_result,
                )
                resolver_status = str(
                    (resolver_result or {}).get("status")
                    if isinstance(resolver_result, Mapping)
                    else ""
                ).strip()
                resolver_disposition = self._resolver_disposition(resolver_result)
                if resolver_status != "success":
                    recovery, recovered = await self._recover_after_resolver_issue()
                    if recovered is not None:
                        return recovered
                    if recovery == RESOLVER_ISSUE_RECOVERY_REENTER_GATE:
                        continue
                    return await self._failed_resolver_summary(
                        summary="pr-resolver child run did not complete successfully.",
                        blocker_kind=DISPOSITION_FAILED,
                    )
                if not resolver_disposition:
                    recovery, recovered = await self._recover_after_resolver_issue()
                    if recovered is not None:
                        return recovered
                    if recovery == RESOLVER_ISSUE_RECOVERY_REENTER_GATE:
                        continue
                    return await self._failed_resolver_summary(
                        summary=(
                            "pr-resolver child result missing "
                            "mergeAutomationDisposition."
                        ),
                        blocker_kind="resolver_disposition_invalid",
                    )
                if resolver_disposition not in ALLOWED_DISPOSITIONS:
                    recovery, recovered = await self._recover_after_resolver_issue()
                    if recovered is not None:
                        return recovered
                    if recovery == RESOLVER_ISSUE_RECOVERY_REENTER_GATE:
                        continue
                    return await self._failed_resolver_summary(
                        summary=(
                            "pr-resolver child result has unsupported "
                            "mergeAutomationDisposition: "
                            f"{resolver_disposition}"
                        ),
                        blocker_kind="resolver_disposition_invalid",
                    )
                if resolver_disposition == DISPOSITION_REQUEST_REVIEW:
                    if self._continuation_observability_enabled:
                        self._continuation_counters["continuation_requested"] += 1
                    terminal = await self._request_automated_review(
                        resolver_result=resolver_result
                        if isinstance(resolver_result, Mapping)
                        else {},
                        resolver_workflow_id=resolver_workflow_id,
                    )
                    if terminal is not None:
                        return terminal
                    if self._continuation_observability_enabled:
                        self._continuation_counters["continuation_accepted"] += 1
                        self._continuation_counters["continuation_cycle_completed"] += 1
                    continue
                if (
                    resolver_disposition == DISPOSITION_REENTER_GATE
                ):
                    if self._continuation_observability_enabled:
                        self._continuation_counters["continuation_requested"] += 1
                    self._refresh_tracked_head_sha_on_next_evaluation = (
                        not self._refresh_tracked_head_sha(resolver_result)
                    )
                    self._status = STATE_WAITING
                    self._publish_visibility()
                    if workflow.patched(
                        MERGE_AUTOMATION_RESOLVER_CONTINUATION_DELAY_PATCH
                    ):
                        try:
                            continuation_deadline = self._continuation_deadline(
                                resolver_result,
                                resolver_workflow_id=resolver_workflow_id,
                            )
                        except (TypeError, ValueError):
                            raw_continuation = (
                                resolver_result.get("gatedContinuation")
                                if isinstance(resolver_result, Mapping)
                                else None
                            )
                            rejection_counter = (
                                "continuation_rejected_ownership"
                                if isinstance(raw_continuation, Mapping)
                                and raw_continuation.get("ownerWorkflowId")
                                else "continuation_rejected_schema"
                            )
                            if self._continuation_observability_enabled:
                                self._continuation_counters[rejection_counter] += 1
                            return await self._failed_resolver_summary(
                                summary="pr-resolver returned an invalid gated continuation.",
                                blocker_kind="resolver_continuation_invalid",
                            )
                        if workflow.patched(
                            "merge-automation-bound-reenter-progress-v1"
                        ) and (
                            self._review_loop_active()
                            or workflow.patched(
                                MERGE_AUTOMATION_BOUND_REENTER_WITHOUT_REVIEW_LOOP_PATCH
                            )
                        ):
                            continuation = resolver_result.get("gatedContinuation") or {}
                            signature = continuation.get("progressSignature")
                            # Older payloads have no signature: an unchanged
                            # head/reason still cannot claim objective progress.
                            signature = signature or (
                                f"{self._input.pull_request.head_sha}|"
                                f"{continuation.get('reason', '')}"
                            )
                            made_progress = self._register_progress_signature(signature)
                            if (
                                not made_progress
                                and self._no_progress_cycles
                                >= self._review_loop_config().max_consecutive_no_progress_cycles
                            ):
                                reason = (
                                    str(continuation.get("reason") or "").strip()[:64]
                                    or "unspecified"
                                )
                                return await self._blocked_review_summary(
                                    summary=(
                                        "Resolver continuation budget exhausted: "
                                        "the same head and outstanding work repeatedly "
                                        f"returned to the gate (reason: {reason}). "
                                        "Inspect the resolver evidence and repair its "
                                        "blocker before retrying."
                                    ),
                                    blocker_kind="review_loop_no_progress",
                                )
                        if expire_at is not None and continuation_deadline >= expire_at:
                            self._status = STATE_EXPIRED
                            self._summary = (
                                "Merge automation expired during resolver "
                                "continuation wait."
                            )
                            self._publish_visibility()
                            return await self._finish()
                        delay = max(
                            0.0, (continuation_deadline - workflow.now()).total_seconds()
                        )
                        if self._continuation_observability_enabled:
                            self._continuation_counters["continuation_accepted"] += 1
                            self._continuation_counters["continuation_wait_started"] += 1
                        try:
                            await workflow.sleep(timedelta(seconds=delay))
                        except CancelledError:
                            self._status = STATE_CANCELED
                            self._summary = (
                                "Merge automation canceled during resolver "
                                "continuation wait."
                            )
                            self._publish_visibility()
                            return await self._finish()
                        except Exception as exc:
                            # Direct unit-boundary tests invoke run() without a
                            # Temporal event loop. Production histories always
                            # use the deterministic workflow timer above.
                            if type(exc).__name__ != "_NotInWorkflowEventLoopError":
                                raise
                        if self._continuation_observability_enabled:
                            self._continuation_counters["continuation_wait_completed"] += 1
                            self._continuation_counters["continuation_cycle_completed"] += 1
                    continue
                if resolver_disposition in {
                    DISPOSITION_MERGED,
                    DISPOSITION_ALREADY_MERGED,
                } and workflow.patched(
                    MERGE_AUTOMATION_RESOLVER_MERGE_CONFIRMATION_PATCH
                ):
                    # A resolver can repair and merge a newer revision without
                    # returning a head SHA. Re-read the tracked PR through its
                    # existing authority before finalizing either integration.
                    # Resolver prose or an echoed pre-repair SHA is not proof.
                    evaluation, evidence, terminal = (
                        await self._evaluate_readiness_once()
                    )
                    if terminal is not None:
                        return terminal
                    if (
                        not evidence.pull_request_merged
                        or not self._refresh_tracked_head_sha(evaluation)
                    ):
                        return await self._failed_resolver_summary(
                            summary=(
                                "Re-read the tracked pull request to confirm its "
                                "merge and exact head before post-merge finalization."
                            ),
                            blocker_kind="resolver_disposition_invalid",
                        )
                if resolver_disposition == DISPOSITION_ALREADY_MERGED:
                    if not await self._complete_post_merge_integrations(
                        resolver_disposition=resolver_disposition
                    ):
                        return await self._finish()
                    self._summary = None
                    self._status = STATE_ALREADY_MERGED
                    self._publish_visibility()
                    return await self._finish()
                if resolver_disposition == DISPOSITION_REVIEW_CLEAN:
                    if self._finish_mode() != FINISH_MODE_FIX_ONLY:
                        # This run was granted merge authority. A resolver that
                        # reports "clean but unmerged" has not done the job it
                        # was asked to do, and must never close the gate as
                        # success.
                        return await self._failed_resolver_summary(
                            summary=(
                                "pr-resolver reported review_clean for a run "
                                "that was granted merge authority."
                            ),
                            blocker_kind="resolver_disposition_invalid",
                        )
                    # fix_only finish mode: the gate opened with nothing left to
                    # address. No merge happened, so no post-merge integration
                    # is owed; the loop is terminally complete.
                    self._summary = (
                        "Review loop complete: no actionable comments remain and "
                        "this run was not configured to merge."
                    )
                    self._status = STATE_REVIEW_CLEAN
                    self._publish_visibility()
                    return await self._finish()
                if resolver_disposition == DISPOSITION_MERGED:
                    if not await self._complete_post_merge_integrations(
                        resolver_disposition=resolver_disposition
                    ):
                        return await self._finish()
                    self._summary = None
                    self._status = STATE_MERGED
                    self._publish_visibility()
                    return await self._finish()
                if resolver_disposition == DISPOSITION_MANUAL_REVIEW:
                    if self._verdict_routing_enabled:
                        routed = await self._route_pr_resolver_terminal(
                            resolver_result=resolver_result
                            if isinstance(resolver_result, Mapping)
                            else {},
                            resolver_workflow_id=resolver_workflow_id,
                            resolver_disposition=resolver_disposition,
                        )
                        if routed is not None:
                            return routed
                        continue
                    return await self._failed_resolver_summary(
                        summary="pr-resolver requested manual review.",
                        blocker_kind=DISPOSITION_MANUAL_REVIEW,
                    )
                if resolver_disposition == DISPOSITION_FAILED:
                    if self._verdict_routing_enabled:
                        routed = await self._route_pr_resolver_terminal(
                            resolver_result=resolver_result
                            if isinstance(resolver_result, Mapping)
                            else {},
                            resolver_workflow_id=resolver_workflow_id,
                            resolver_disposition=resolver_disposition,
                        )
                        if routed is not None:
                            return routed
                        continue
                    return await self._failed_resolver_summary(
                        summary="pr-resolver reported failure.",
                        blocker_kind=DISPOSITION_FAILED,
                    )

            if any(blocker.kind in TERMINAL_BLOCKER_KINDS for blocker in self._blockers):
                self._status = STATE_BLOCKED
                self._publish_visibility()
                return await self._finish()

            self._status = STATE_WAITING
            self._publish_visibility()
            try:
                target_event_count = self._external_event_count
                await workflow.wait_condition(
                    lambda: self._external_event_count > target_event_count,
                    timeout=timedelta(
                        seconds=self._input.config.timeouts.fallback_poll_seconds
                    ),
                )
            except TimeoutError:
                # Expected fallback poll wake-up when no external signal arrives.
                pass
