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
        MergeGateStartInput,
        ReadinessBlockerModel,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        INTEGRATIONS_TASK_QUEUE,
        WORKFLOW_TASK_QUEUE,
    )
    from moonmind.workflows.temporal.workflows.merge_gate import (
        DEFAULT_ACTIVITY_RETRY_POLICY,
        TERMINAL_BLOCKER_KINDS,
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
STATE_FAILED = "failed"


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindMergeAutomationWorkflow:
    """Wait for PR readiness, run pr-resolver as a child, and return a parent outcome."""

    def __init__(self) -> None:
        self._status = STATE_WAITING
        self._input: MergeGateStartInput | None = None
        self._blockers: list[ReadinessBlockerModel] = []
        self._resolver_child_workflow_ids: list[str] = []
        self._external_event_count = 0

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

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._input = MergeGateStartInput.model_validate(payload)
        self._status = STATE_WAITING
        self._publish_visibility()

        while True:
            evaluation = await workflow.execute_activity(
                "merge_gate.evaluate_readiness",
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
            self._blockers = list(evidence.blockers)
            if evidence.ready:
                self._status = STATE_EXECUTING
                self._publish_visibility()
                resolver_request = build_resolver_run_request(
                    parent_workflow_id=str(self._input.parent["workflowId"]),
                    pull_request=self._input.pull_request,
                    jira_issue_key=self._input.jira_issue_key,
                    merge_method=self._input.policy.merge_method,
                )
                resolver_workflow_id = deterministic_resolver_idempotency_key(
                    parent_workflow_id=str(self._input.parent["workflowId"]),
                    repo=self._input.pull_request.repo,
                    pr_number=self._input.pull_request.number,
                    head_sha=self._input.pull_request.head_sha,
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
                self._status = (
                    STATE_MERGED if resolver_status == "success" else STATE_FAILED
                )
                summary = self._summary_payload()
                if self._status == STATE_FAILED:
                    summary["summary"] = "pr-resolver child run did not complete successfully."
                self._publish_visibility()
                return summary

            if any(blocker.kind in TERMINAL_BLOCKER_KINDS for blocker in self._blockers):
                self._status = STATE_BLOCKED
                self._publish_visibility()
                return self._summary_payload()

            self._status = STATE_WAITING
            self._publish_visibility()
            try:
                await workflow.wait_condition(
                    lambda: self._external_event_count > 0,
                    timeout=timedelta(minutes=5),
                )
            except Exception:
                pass
