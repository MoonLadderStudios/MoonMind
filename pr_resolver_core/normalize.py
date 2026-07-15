"""Normalize host/provider-shaped snapshots into one canonical model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import CanonicalPullRequestSnapshot


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def normalize_temporal_snapshot(
    raw: Mapping[str, Any],
) -> CanonicalPullRequestSnapshot:
    blockers = tuple(
        item for item in (raw.get("blockers") or ()) if isinstance(item, Mapping)
    )
    kinds = tuple(_text(item.get("kind")).lower() for item in blockers)
    summaries = tuple(_text(item.get("summary")) for item in blockers)
    non_retryable_external_state = any(
        _text(item.get("kind")).lower() == "external_state_unavailable"
        and item.get("retryable") is False
        for item in blockers
    )
    checks_complete = raw.get("checksComplete")
    checks_passing = raw.get("checksPassing")
    recognized_blocker = bool(kinds)
    ready = raw.get("ready") is True
    malformed = not (
        raw.get("pullRequestMerged") is True
        or raw.get("pullRequestOpen") is False
        or recognized_blocker
        or isinstance(checks_complete, bool)
        or ready
    )
    degraded_kinds = {
        "checks_unavailable",
        "readiness_evidence_unavailable",
        "comments_unavailable",
        "authentication_unavailable",
        "publish_unavailable",
    }
    return CanonicalPullRequestSnapshot(
        repository=_text(raw.get("repository")),
        pr_number=raw.get("prNumber") if isinstance(raw.get("prNumber"), int) else None,
        pr_url=_text(raw.get("prUrl")),
        head_sha=_text(raw.get("headSha")),
        base_sha=_text(raw.get("baseSha")),
        merged=raw.get("pullRequestMerged") is True,
        open=raw.get("pullRequestOpen") is not False,
        draft=raw.get("draft") is True or "draft" in kinds,
        merge_conflict=bool({"merge_conflict", "merge_conflicts"} & set(kinds)),
        mergeability_unknown=bool(
            {
                "mergeability_unknown",
                "mergeability_transient",
            }
            & set(kinds)
        )
        or (
            "external_state_unavailable" in kinds
            and not non_retryable_external_state
        ),
        checks_complete=(ready or "checks_failed" in kinds or _bool(checks_complete)),
        checks_passing=(
            ready or ("checks_failed" not in kinds and _bool(checks_passing))
        ),
        checks_failed=(
            "checks_failed" in kinds
            or (
                not ready
                and _bool(checks_complete)
                and not _bool(checks_passing)
            )
        ),
        checks_degraded=bool(degraded_kinds & set(kinds)),
        checks_signal_available=isinstance(checks_complete, bool),
        actionable_comments=(
            "actionable_comments" in kinds
            or any(
                "requested changes" in summary.lower()
                or "changes requested" in summary.lower()
                for summary in summaries
            )
        ),
        comments_available="comments_unavailable" not in kinds,
        automated_review_pending="automated_review_pending" in kinds,
        publish_available=not bool(
            {"authentication_unavailable", "publish_unavailable"} & set(kinds)
        ),
        malformed=malformed,
        unknown_blocker=bool(
            set(kinds)
            - {
                "merge_conflict",
                "merge_conflicts",
                "checks_failed",
                "checks_running",
                "automated_review_pending",
                "actionable_comments",
                "mergeability_unknown",
                "mergeability_transient",
                "external_state_unavailable",
                "checks_unavailable",
                "readiness_evidence_unavailable",
                "comments_unavailable",
                "authentication_unavailable",
                "publish_unavailable",
                "draft",
            }
        )
        or non_retryable_external_state,
        blocker_kinds=kinds,
        blocker_summaries=summaries,
    )


def normalize_portable_snapshot(
    raw: Mapping[str, Any],
) -> CanonicalPullRequestSnapshot:
    pr = raw.get("pr") if isinstance(raw.get("pr"), Mapping) else {}
    ci = raw.get("ci") if isinstance(raw.get("ci"), Mapping) else {}
    comments_fetch = (
        raw.get("commentsFetch")
        if isinstance(raw.get("commentsFetch"), Mapping)
        else {}
    )
    comments_summary = (
        raw.get("commentsSummary")
        if isinstance(raw.get("commentsSummary"), Mapping)
        else {}
    )
    state = _text(pr.get("state")).upper()
    merge_state = _text(pr.get("mergeStateStatus")).upper()
    mergeable = pr.get("mergeable")
    mergeable_text = _text(mergeable).upper()
    signal_quality = _text(ci.get("signalQuality")).lower()
    authoritative_failures = _bool(ci.get("hasAuthoritativeFailures")) or (
        _bool(ci.get("hasFailures")) and signal_quality in {"", "ok"}
    )
    grace = comments_summary.get("codexReviewGrace")
    grace_active = isinstance(grace, Mapping) and grace.get("active") is True
    required_shapes_present = bool(pr) and bool(ci) and bool(comments_fetch)
    return CanonicalPullRequestSnapshot(
        repository=_text(raw.get("repository")),
        pr_number=pr.get("number") if isinstance(pr.get("number"), int) else None,
        pr_url=_text(pr.get("url")),
        head_sha=_text(pr.get("headRefOid") or raw.get("headSha")),
        base_sha=_text(pr.get("baseRefOid") or raw.get("baseSha")),
        merged=state == "MERGED",
        open=state not in {"CLOSED", "MERGED"},
        draft=pr.get("isDraft") is True,
        merge_conflict=(
            merge_state in {"BEHIND", "DIRTY"}
            or mergeable is False
            or mergeable_text in {"CONFLICTING", "DIRTY", "CONFLICT"}
        ),
        mergeability_unknown=(
            merge_state in {"UNKNOWN", "UNSTABLE", "BLOCKED"}
            or mergeable_text in {"UNKNOWN", "UNSTABLE"}
        ),
        checks_complete=not _bool(ci.get("isRunning")),
        checks_passing=(
            not _bool(ci.get("hasFailures"))
            and not _bool(ci.get("isRunning"))
            and signal_quality in {"", "ok"}
        ),
        checks_failed=authoritative_failures,
        checks_degraded=signal_quality not in {"", "ok"},
        checks_signal_available=bool(ci),
        actionable_comments=_bool(comments_summary.get("hasActionableComments")),
        comments_available=comments_fetch.get("succeeded") is True,
        comment_policy_enforced=(
            comments_summary.get("includeBotReviewComments") is True
        ),
        automated_review_pending=grace_active,
        publish_available=raw.get("publishAvailable") is not False,
        malformed=not required_shapes_present,
    )
