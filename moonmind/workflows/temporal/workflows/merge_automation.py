"""Parent-owned Temporal workflow for post-publish merge automation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import SearchAttributeKey, SearchAttributePair
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.temporal_models import (
        MergeAutomationStartInput,
        ReadinessBlockerModel,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        INTEGRATIONS_TASK_QUEUE,
        WORKFLOW_TASK_QUEUE,
    )
    from moonmind.workflows.temporal.workflows.merge_gate import (
        DEFAULT_ACTIVITY_RETRY_POLICY,
        TERMINAL_BLOCKER_KINDS,
        _effective_expire_at,
        build_resolver_run_request,
        classify_readiness,
        deterministic_resolver_idempotency_key,
    )


WORKFLOW_NAME = "MoonMind.MergeAutomation"
STATE_WAITING = "waiting"
STATE_EXECUTING = "executing"
STATE_BLOCKED = "blocked"
STATE_MERGED = "merged"
STATE_ALREADY_MERGED = "already_merged"
STATE_EXPIRED = "expired"
STATE_FAILED = "failed"
DISPOSITION_REENTER_GATE = "reenter_gate"
DISPOSITION_MERGED = "merged"
DISPOSITION_ALREADY_MERGED = "already_merged"
DISPOSITION_MANUAL_REVIEW = "manual_review"
DISPOSITION_FAILED = "failed"
SUCCESS_DISPOSITIONS = frozenset({DISPOSITION_MERGED, DISPOSITION_ALREADY_MERGED})
NON_SUCCESS_DISPOSITIONS = frozenset({DISPOSITION_MANUAL_REVIEW, DISPOSITION_FAILED})
ALLOWED_DISPOSITIONS = SUCCESS_DISPOSITIONS | NON_SUCCESS_DISPOSITIONS | {
    DISPOSITION_REENTER_GATE
}


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindMergeAutomationWorkflow:
    """Wait for PR readiness, run pr-resolver as a child, and return a parent outcome."""

    def __init__(self) -> None:
        self._status = STATE_WAITING
        self._input: MergeAutomationStartInput | None = None
        self._blockers: list[ReadinessBlockerModel] = []
        self._resolver_child_workflow_ids: list[str] = []
        self._external_event_count = 0
        self._refresh_tracked_head_sha_on_next_evaluation = False

    def _summary_payload(self) -> dict[str, Any]:
        pr = self._input.pull_request if self._input is not None else None
        return {
            "status": self._status,
            "prNumber": pr.number if pr is not None else None,
            "prUrl": pr.url if pr is not None else None,
            "cycles": len(self._resolver_child_workflow_ids),
            "resolverChildWorkflowIds": list(self._resolver_child_workflow_ids),
            "lastHeadSha": pr.head_sha if pr is not None else None,
            "blockers": [
                blocker.model_dump(by_alias=True, mode="json")
                for blocker in self._blockers
            ],
        }

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

    def _failed_resolver_summary(
        self,
        *,
        summary: str,
        blocker_kind: str,
    ) -> dict[str, Any]:
        self._status = STATE_FAILED
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
        result = self._summary_payload()
        result["summary"] = summary
        self._publish_visibility()
        return result

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._input = MergeAutomationStartInput.model_validate(payload)
        self._status = STATE_WAITING
        expire_at = _effective_expire_at(self._input, started_at=workflow.now())
        self._publish_visibility()

        while True:
            if expire_at is not None and workflow.now() >= expire_at:
                self._status = STATE_EXPIRED
                self._publish_visibility()
                return self._summary_payload()

            evaluation = await workflow.execute_activity(
                "merge_automation.evaluate_readiness",
                self._input.model_dump(by_alias=True, mode="json"),
                start_to_close_timeout=timedelta(minutes=2),
                task_queue=INTEGRATIONS_TASK_QUEUE,
                retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                cancellation_type=ActivityCancellationType.TRY_CANCEL,
            )
            evidence = classify_readiness(
                evaluation if isinstance(evaluation, Mapping) else {},
                tracked_head_sha=self._input.pull_request.head_sha,
            )
            if self._refresh_tracked_head_sha_on_next_evaluation:
                self._refresh_tracked_head_sha_on_next_evaluation = False
                if self._refresh_tracked_head_sha(evaluation):
                    evidence = classify_readiness(
                        evaluation if isinstance(evaluation, Mapping) else {},
                        tracked_head_sha=self._input.pull_request.head_sha,
                    )
            self._blockers = list(evidence.blockers)
            if evidence.ready:
                self._status = STATE_EXECUTING
                self._publish_visibility()
                resolver_request = build_resolver_run_request(
                    parent_workflow_id=self._input.parent_workflow_id,
                    pull_request=self._input.pull_request,
                    jira_issue_key=self._input.jira_issue_key,
                    merge_method=self._input.config.resolver.merge_method,
                )
                resolver_workflow_id = deterministic_resolver_idempotency_key(
                    parent_workflow_id=self._input.parent_workflow_id,
                    repo=self._input.pull_request.repo,
                    pr_number=self._input.pull_request.number,
                    head_sha=self._input.pull_request.head_sha,
                )
                resolver_workflow_id = (
                    f"{resolver_workflow_id}:{len(self._resolver_child_workflow_ids) + 1}"
                )
                self._resolver_child_workflow_ids.append(resolver_workflow_id)
                resolver_result = await workflow.execute_child_workflow(
                    "MoonMind.Run",
                    resolver_request,
                    id=resolver_workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                    static_summary="Resolving pull request for merge automation",
                    static_details=f"Resolve {self._input.pull_request.url}",
                )
                resolver_status = str(
                    (resolver_result or {}).get("status")
                    if isinstance(resolver_result, Mapping)
                    else ""
                ).strip()
                resolver_disposition = self._resolver_disposition(resolver_result)
                if resolver_status != "success":
                    return self._failed_resolver_summary(
                        summary="pr-resolver child run did not complete successfully.",
                        blocker_kind=DISPOSITION_FAILED,
                    )
                if not resolver_disposition:
                    return self._failed_resolver_summary(
                        summary=(
                            "pr-resolver child result missing "
                            "mergeAutomationDisposition."
                        ),
                        blocker_kind="resolver_disposition_invalid",
                    )
                if resolver_disposition not in ALLOWED_DISPOSITIONS:
                    return self._failed_resolver_summary(
                        summary=(
                            "pr-resolver child result has unsupported "
                            "mergeAutomationDisposition: "
                            f"{resolver_disposition}"
                        ),
                        blocker_kind="resolver_disposition_invalid",
                    )
                if (
                    resolver_disposition == DISPOSITION_REENTER_GATE
                ):
                    self._refresh_tracked_head_sha_on_next_evaluation = (
                        not self._refresh_tracked_head_sha(resolver_result)
                    )
                    self._status = STATE_WAITING
                    self._publish_visibility()
                    continue
                if resolver_disposition == DISPOSITION_ALREADY_MERGED:
                    self._status = STATE_ALREADY_MERGED
                    summary = self._summary_payload()
                    self._publish_visibility()
                    return summary
                if resolver_disposition == DISPOSITION_MERGED:
                    self._status = STATE_MERGED
                    summary = self._summary_payload()
                    self._publish_visibility()
                    return summary
                if resolver_disposition == DISPOSITION_MANUAL_REVIEW:
                    return self._failed_resolver_summary(
                        summary="pr-resolver requested manual review.",
                        blocker_kind=DISPOSITION_MANUAL_REVIEW,
                    )
                if resolver_disposition == DISPOSITION_FAILED:
                    return self._failed_resolver_summary(
                        summary="pr-resolver reported failure.",
                        blocker_kind=DISPOSITION_FAILED,
                    )

            if any(blocker.kind in TERMINAL_BLOCKER_KINDS for blocker in self._blockers):
                self._status = STATE_BLOCKED
                self._publish_visibility()
                return self._summary_payload()

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
