"""Temporal workflow boundary for post-publish merge automation gates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.temporal_models import (
        MergeAutomationStartInput,
        PullRequestRefModel,
        ReadinessBlockerModel,
        ReadinessEvidenceModel,
        ResolverRunRefModel,
    )
    from moonmind.utils.logging import scrub_github_tokens


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
    pull_request_merged = (
        payload.get("pullRequestMerged") is True
        or payload.get("pull_request_merged") is True
    )
    blockers: list[ReadinessBlockerModel] = []
    if not pull_request_merged and head_sha != str(tracked_head_sha).strip():
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

    if not pull_request_merged and (
        payload.get("pullRequestOpen") is False
        or payload.get("pull_request_open") is False
    ):
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

    explicit_ready = bool(payload.get("ready", False)) and not pull_request_merged
    ready = explicit_ready and not deduped
    return ReadinessEvidenceModel.model_validate(
        {
            **dict(payload),
            "headSha": head_sha,
            "ready": ready,
            "pullRequestMerged": pull_request_merged,
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
                "tool": {"type": "skill", "name": "pr-resolver", "version": "1.0"},
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


def _effective_expire_at(
    start_input: MergeAutomationStartInput,
    *,
    started_at: datetime,
) -> datetime | None:
    explicit_expire_at = _parse_expire_at(start_input.expire_at)
    if explicit_expire_at is not None:
        return explicit_expire_at
    expire_after_seconds = start_input.config.timeouts.expire_after_seconds
    if expire_after_seconds is None:
        return None
    return started_at + timedelta(seconds=expire_after_seconds)
