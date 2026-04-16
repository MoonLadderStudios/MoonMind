"""Temporal workflow boundary for post-publish merge automation gates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributeKey, SearchAttributePair
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.temporal_models import (
        MergeAutomationStartInput,
        PullRequestRefModel,
        ReadinessBlockerModel,
        ReadinessEvidenceModel,
        ResolverRunRefModel,
    )
    from moonmind.utils.logging import scrub_github_tokens
    from moonmind.workflows.temporal.activity_catalog import INTEGRATIONS_TASK_QUEUE


WORKFLOW_NAME = "MoonMind.MergeAutomation"
STATE_INITIALIZING = "initializing"
STATE_AWAITING_EXTERNAL = "awaiting_external"
STATE_EXECUTING = "executing"
STATE_FINALIZING = "finalizing"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"
OUTPUT_WAITING = "waiting"
OUTPUT_BLOCKED = "blocked"
OUTPUT_OPEN = "open"
OUTPUT_RESOLVER_LAUNCHED = "resolver_launched"
OUTPUT_EXPIRED = "expired"
TERMINAL_BLOCKER_KINDS = {
    "pull_request_closed",
    "stale_revision",
    "policy_denied",
}
KNOWN_BLOCKER_KINDS = {
    "checks_running",
    "checks_failed",
    "automated_review_pending",
    "jira_status_pending",
    "pull_request_closed",
    "stale_revision",
    "policy_denied",
    "external_state_unavailable",
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
    kind = str(payload.get("kind") or "external_state_unavailable").strip()
    if kind not in KNOWN_BLOCKER_KINDS:
        kind = "external_state_unavailable"
    return ReadinessBlockerModel.model_validate(
        {
            "kind": kind,
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


def build_continue_as_new_input(
    *,
    start_input: Mapping[str, Any] | MergeAutomationStartInput,
    blockers: list[Mapping[str, Any]] | list[ReadinessBlockerModel],
    cycle_count: int,
    resolver_history: list[Mapping[str, Any]] | list[ResolverRunRefModel],
    latest_head_sha: str,
    expire_at: str | None,
) -> dict[str, Any]:
    parsed = (
        start_input
        if isinstance(start_input, MergeAutomationStartInput)
        else MergeAutomationStartInput.model_validate(start_input)
    )
    payload = parsed.model_dump(by_alias=True, mode="json")
    payload["pullRequest"]["headSha"] = str(latest_head_sha or parsed.pull_request.head_sha)
    payload["blockers"] = [
        blocker.model_dump(by_alias=True, mode="json")
        if isinstance(blocker, ReadinessBlockerModel)
        else ReadinessBlockerModel.model_validate(blocker).model_dump(
            by_alias=True,
            mode="json",
        )
        for blocker in blockers
    ]
    payload["cycleCount"] = max(0, int(cycle_count))
    payload["resolverHistory"] = [
        ref.model_dump(by_alias=True, mode="json")
        if isinstance(ref, ResolverRunRefModel)
        else ResolverRunRefModel.model_validate(ref).model_dump(
            by_alias=True,
            mode="json",
        )
        for ref in resolver_history
    ]
    payload["expireAt"] = expire_at
    return payload


def _parse_expire_at(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindMergeAutomationWorkflow:
    """Wait for external PR readiness, then launch one pr-resolver run."""

    def __init__(self) -> None:
        self._status = STATE_INITIALIZING
        self._output_status = OUTPUT_WAITING
        self._input: MergeAutomationStartInput | None = None
        self._blockers: list[ReadinessBlockerModel] = []
        self._resolver_history: list[ResolverRunRefModel] = []
        self._external_event_count = 0
        self._last_handled_event_count = 0
        self._cycle_count = 0

    def _summary_payload(self) -> dict[str, Any]:
        pr = self._input.pull_request if self._input is not None else None
        return {
            "status": self._output_status,
            "workflowState": self._status,
            "outputStatus": self._output_status,
            "prNumber": pr.number if pr is not None else None,
            "prUrl": pr.url if pr is not None else None,
            "headSha": pr.head_sha if pr is not None else None,
            "cycles": self._cycle_count,
            "blockers": [
                blocker.model_dump(by_alias=True, mode="json")
                for blocker in self._blockers
            ],
            "resolverChildWorkflowIds": [
                resolver.workflow_id for resolver in self._resolver_history
            ],
            "resolverHistory": [
                resolver.model_dump(by_alias=True, mode="json")
                for resolver in self._resolver_history
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
        if self._resolver_history:
            return self._summary_payload()
        self._input = MergeAutomationStartInput.model_validate(payload)
        self._blockers = list(self._input.blockers)
        self._resolver_history = list(self._input.resolver_history)
        self._cycle_count = self._input.cycle_count
        self._status = STATE_AWAITING_EXTERNAL
        self._output_status = OUTPUT_WAITING
        self._publish_visibility()

        while True:
            expire_at = _parse_expire_at(self._input.expire_at)
            if expire_at is not None and workflow.now() >= expire_at:
                self._status = STATE_COMPLETED
                self._output_status = OUTPUT_EXPIRED
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
            self._cycle_count += 1
            self._blockers = list(evidence.blockers)
            if evidence.ready:
                self._status = STATE_EXECUTING
                self._output_status = OUTPUT_OPEN
                resolver_request = build_resolver_run_request(
                    parent_workflow_id=self._input.parent_workflow_id,
                    pull_request=self._input.pull_request,
                    jira_issue_key=self._input.jira_issue_key,
                    merge_method=self._input.config.resolver.merge_method,
                )
                resolver_payload = {
                    "parentWorkflowId": self._input.parent_workflow_id,
                    "pullRequest": self._input.pull_request.model_dump(
                        by_alias=True,
                        mode="json",
                    ),
                    "jiraIssueKey": self._input.jira_issue_key,
                    "mergeMethod": self._input.config.resolver.merge_method,
                    "idempotencyKey": deterministic_resolver_idempotency_key(
                        parent_workflow_id=self._input.parent_workflow_id,
                        repo=self._input.pull_request.repo,
                        pr_number=self._input.pull_request.number,
                        head_sha=self._input.pull_request.head_sha,
                    ),
                    "runInput": resolver_request,
                }
                result = await workflow.execute_activity(
                    "merge_automation.create_resolver_run",
                    resolver_payload,
                    start_to_close_timeout=timedelta(minutes=2),
                    task_queue=INTEGRATIONS_TASK_QUEUE,
                    retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
                    cancellation_type=ActivityCancellationType.TRY_CANCEL,
                )
                self._resolver_history.append(ResolverRunRefModel.model_validate(result))
                self._status = STATE_FINALIZING
                self._publish_visibility()
                self._status = STATE_COMPLETED
                self._output_status = OUTPUT_RESOLVER_LAUNCHED
                self._publish_visibility()
                return self._summary_payload()

            if any(blocker.kind in TERMINAL_BLOCKER_KINDS for blocker in self._blockers):
                self._status = STATE_FAILED
                self._output_status = OUTPUT_BLOCKED
                self._publish_visibility()
                return self._summary_payload()

            self._status = STATE_AWAITING_EXTERNAL
            self._output_status = OUTPUT_WAITING
            self._publish_visibility()
            try:
                target_event_count = self._external_event_count
                await workflow.wait_condition(
                    lambda: self._external_event_count > target_event_count,
                    timeout=timedelta(
                        seconds=self._input.config.timeouts.fallback_poll_seconds
                    ),
                )
                self._last_handled_event_count = self._external_event_count
            except Exception:
                # Timeout/cancellation from the wait is intentionally tolerated;
                # the next loop iteration refreshes readiness from provider state.
                pass
            try:
                info = workflow.info()
                should_continue = getattr(info, "is_continue_as_new_suggested", False)
            except Exception:
                should_continue = False
            if callable(should_continue):
                should_continue = should_continue()
            if should_continue:
                workflow.continue_as_new(
                    build_continue_as_new_input(
                        start_input=self._input,
                        blockers=self._blockers,
                        cycle_count=self._cycle_count,
                        resolver_history=self._resolver_history,
                        latest_head_sha=self._input.pull_request.head_sha,
                        expire_at=self._input.expire_at,
                    )
                )
