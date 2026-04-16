"""Temporal workflow boundary for post-publish merge automation gates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributeKey, SearchAttributePair
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.temporal_models import (
        MergeGateStartInput,
        PullRequestRefModel,
        ReadinessBlockerModel,
        ReadinessEvidenceModel,
        ResolverRunRefModel,
    )
    from moonmind.utils.logging import scrub_github_tokens
    from moonmind.workflows.temporal.activity_catalog import INTEGRATIONS_TASK_QUEUE


WORKFLOW_NAME = "MoonMind.MergeGate"
STATE_WAITING = "waiting"
STATE_BLOCKED = "blocked"
STATE_OPEN = "open"
STATE_RESOLVER_LAUNCHED = "resolver_launched"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"
TERMINAL_BLOCKER_KINDS = {
    "pull_request_closed",
    "stale_revision",
    "policy_denied",
}
DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)
_TOKEN_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(token|password|authorization|cookie)=\S+"
)


def sanitize_blocker_summary(value: str | None) -> str:
    """Return a compact blocker summary safe for workflow state and UI."""

    text = scrub_github_tokens(str(value or "")).strip()
    text = re.sub(r"(?i)\btoken=\[REDACTED\]", "token:<redacted>", text)
    text = _TOKEN_ASSIGNMENT_PATTERN.sub(r"\1:<redacted>", text)
    return (text or "External readiness is blocked.")[:500]


def _blocker_from_mapping(payload: Mapping[str, Any]) -> ReadinessBlockerModel:
    return ReadinessBlockerModel.model_validate(
        {
            "kind": payload.get("kind") or "external_state_unavailable",
            "summary": sanitize_blocker_summary(payload.get("summary")),
            "retryable": bool(payload.get("retryable", True)),
            "source": payload.get("source"),
        }
    )


def _default_blocker(kind: str, summary: str, *, retryable: bool, source: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "summary": summary,
        "retryable": retryable,
        "source": source,
    }


def classify_readiness(
    payload: Mapping[str, Any],
    *,
    tracked_head_sha: str,
) -> ReadinessEvidenceModel:
    """Normalize provider readiness evidence into bounded merge-gate evidence."""

    head_sha = str(payload.get("headSha") or payload.get("head_sha") or "").strip()
    blockers: list[ReadinessBlockerModel] = []
    if head_sha != str(tracked_head_sha).strip():
        blockers.append(
            ReadinessBlockerModel(
                kind="stale_revision",
                summary="Pull request revision changed before the gate opened.",
                retryable=False,
                source="github",
            )
        )

    for raw in payload.get("blockers") or []:
        if isinstance(raw, Mapping):
            blockers.append(_blocker_from_mapping(raw))

    if payload.get("pullRequestOpen") is False or payload.get("pull_request_open") is False:
        blockers.append(
            ReadinessBlockerModel.model_validate(
                _default_blocker(
                    "pull_request_closed",
                    "Pull request is closed.",
                    retryable=False,
                    source="github",
                )
            )
        )
    if payload.get("policyAllowed") is False or payload.get("policy_allowed") is False:
        blockers.append(
            ReadinessBlockerModel.model_validate(
                _default_blocker(
                    "policy_denied",
                    "Merge automation policy denied resolver launch.",
                    retryable=False,
                    source="policy",
                )
            )
        )
    if payload.get("checksComplete") is False or payload.get("checks_complete") is False:
        blockers.append(
            ReadinessBlockerModel.model_validate(
                _default_blocker(
                    "checks_running",
                    "Required checks are still running.",
                    retryable=True,
                    source="github",
                )
            )
        )
    elif payload.get("checksPassing") is False or payload.get("checks_passing") is False:
        blockers.append(
            ReadinessBlockerModel.model_validate(
                _default_blocker(
                    "checks_failed",
                    "Required checks are failing.",
                    retryable=True,
                    source="github",
                )
            )
        )
    if (
        payload.get("automatedReviewComplete") is False
        or payload.get("automated_review_complete") is False
    ):
        blockers.append(
            ReadinessBlockerModel.model_validate(
                _default_blocker(
                    "automated_review_pending",
                    "Automated review has not completed.",
                    retryable=True,
                    source="github",
                )
            )
        )
    if payload.get("jiraStatusAllowed") is False or payload.get("jira_status_allowed") is False:
        blockers.append(
            ReadinessBlockerModel.model_validate(
                _default_blocker(
                    "jira_status_pending",
                    "Jira status is not yet allowed for merge automation.",
                    retryable=True,
                    source="jira",
                )
            )
        )

    deduped: list[ReadinessBlockerModel] = []
    seen: set[tuple[str, str]] = set()
    for blocker in blockers:
        key = (blocker.kind, blocker.summary)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)

    explicit_ready = bool(payload.get("ready", False))
    ready = explicit_ready and not deduped
    return ReadinessEvidenceModel.model_validate(
        {
            **dict(payload),
            "headSha": head_sha,
            "ready": ready,
            "blockers": [b.model_dump(by_alias=True) for b in deduped],
        }
    )


def deterministic_resolver_idempotency_key(
    *,
    parent_workflow_id: str,
    repo: str,
    pr_number: int,
    head_sha: str,
) -> str:
    return f"resolver:{parent_workflow_id}:{repo}:{pr_number}:{head_sha}"


def build_resolver_run_request(
    *,
    parent_workflow_id: str,
    pull_request: Mapping[str, Any] | PullRequestRefModel,
    jira_issue_key: str | None,
    merge_method: str,
) -> dict[str, Any]:
    pr = (
        pull_request
        if isinstance(pull_request, PullRequestRefModel)
        else PullRequestRefModel.model_validate(pull_request)
    )
    args = {
        "repo": pr.repo,
        "pr": str(pr.number),
        "mergeMethod": merge_method,
    }
    if pr.head_branch:
        args["branch"] = pr.head_branch
    if jira_issue_key:
        args["jiraIssueKey"] = jira_issue_key
    title = f"Resolve PR #{pr.number}"
    return {
        "workflowType": "MoonMind.Run",
        "title": title,
        "initialParameters": {
            "repo": pr.repo,
            "repository": pr.repo,
            "publishMode": "none",
            "task": {
                "instructions": (
                    f"Resolve and merge pull request {pr.url}. "
                    "Use pr-resolver and do not create another pull request."
                ),
                "tool": {"type": "skill", "name": "pr-resolver"},
                "skill": {"id": "pr-resolver", "args": args},
                "publish": {"mode": "none"},
            },
            "workspaceSpec": {
                "repository": pr.repo,
                "branch": pr.head_branch,
                "startingBranch": pr.base_branch,
                "targetBranch": pr.head_branch,
            },
            "mergeGate": {
                "parentWorkflowId": parent_workflow_id,
                "pullRequestUrl": pr.url,
                "headSha": pr.head_sha,
            },
        },
    }


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindMergeGateWorkflow:
    """Wait for external PR readiness, then launch one pr-resolver run."""

    def __init__(self) -> None:
        self._status = STATE_WAITING
        self._input: MergeGateStartInput | None = None
        self._blockers: list[ReadinessBlockerModel] = []
        self._resolver_run: ResolverRunRefModel | None = None
        self._external_event_count = 0

    def _summary_payload(self) -> dict[str, Any]:
        pr = self._input.pull_request if self._input is not None else None
        return {
            "status": self._status,
            "pullRequestUrl": pr.url if pr is not None else None,
            "headSha": pr.head_sha if pr is not None else None,
            "blockers": [
                blocker.model_dump(by_alias=True, mode="json")
                for blocker in self._blockers
            ],
            "resolverRun": (
                self._resolver_run.model_dump(by_alias=True, mode="json")
                if self._resolver_run is not None
                else None
            ),
        }

    def _publish_visibility(self) -> None:
        workflow.upsert_memo({"summary": self._summary_payload()})
        workflow.upsert_search_attributes(
            [
                SearchAttributePair(SearchAttributeKey.for_keyword("mm_state"), self._status),
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_entry"),
                    "merge_gate",
                ),
            ]
        )

    @workflow.signal(name="merge_gate.external_event")
    def external_event(self, _payload: dict[str, Any]) -> None:
        self._external_event_count += 1

    @workflow.query
    def summary(self) -> dict[str, Any]:
        return self._summary_payload()

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._resolver_run is not None:
            return self._summary_payload()
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
                self._status = STATE_OPEN
                resolver_request = build_resolver_run_request(
                    parent_workflow_id=str(self._input.parent["workflowId"]),
                    pull_request=self._input.pull_request,
                    jira_issue_key=self._input.jira_issue_key,
                    merge_method=self._input.policy.merge_method,
                )
                resolver_payload = {
                    "parentWorkflowId": self._input.parent["workflowId"],
                    "pullRequest": self._input.pull_request.model_dump(
                        by_alias=True,
                        mode="json",
                    ),
                    "jiraIssueKey": self._input.jira_issue_key,
                    "mergeMethod": self._input.policy.merge_method,
                    "idempotencyKey": deterministic_resolver_idempotency_key(
                        parent_workflow_id=str(self._input.parent["workflowId"]),
                        repo=self._input.pull_request.repo,
                        pr_number=self._input.pull_request.number,
                        head_sha=self._input.pull_request.head_sha,
                    ),
                    "runInput": resolver_request,
                }
                result = await workflow.execute_activity(
                    "merge_gate.create_resolver_run",
                    resolver_payload,
                    start_to_close_timeout=timedelta(minutes=2),
                    task_queue=INTEGRATIONS_TASK_QUEUE,
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )
                self._resolver_run = ResolverRunRefModel.model_validate(result)
                self._status = STATE_COMPLETED
                self._publish_visibility()
                return self._summary_payload()

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
            except TimeoutError:
                # Expected fallback poll wake-up when no external signal arrives.
                pass
