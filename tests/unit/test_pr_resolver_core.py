from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

from pr_resolver_core import (
    CanonicalPullRequestSnapshot,
    IMPLEMENTATION_CONTRACT,
    RESOLVER_CORE_DIGEST,
    ResolverAction,
    ResolverEvent,
    ResolverPolicy,
    ResolverState,
    classify_snapshot,
    normalize_portable_snapshot,
    normalize_temporal_snapshot,
    reduce_resolver_state,
)


@pytest.mark.parametrize(
    ("temporal", "portable", "classification", "action"),
    [
        (
            {"pullRequestMerged": True},
            {"pr": {"state": "MERGED"}, "ci": {}, "commentsFetch": {}},
            "already_merged",
            ResolverAction.PUBLISH_TERMINAL,
        ),
        (
            {"pullRequestOpen": False},
            {"pr": {"state": "CLOSED"}, "ci": {}, "commentsFetch": {}},
            "manual_review",
            ResolverAction.STOP_MANUAL_REVIEW,
        ),
        (
            {"draft": True, "checksComplete": True, "checksPassing": True},
            {"pr": {"state": "OPEN", "isDraft": True}, "ci": {}, "commentsFetch": {}},
            "manual_review",
            ResolverAction.STOP_MANUAL_REVIEW,
        ),
        (
            {"ready": True, "blockers": []},
            {
                "pr": {"state": "OPEN", "mergeStateStatus": "CLEAN"},
                "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": {"includeBotReviewComments": True},
            },
            "ready_to_merge",
            ResolverAction.ATTEMPT_MERGE,
        ),
        (
            {"blockers": [{"kind": "merge_conflict"}]},
            {
                "pr": {"state": "OPEN", "mergeStateStatus": "DIRTY"},
                "ci": {},
                "commentsFetch": {},
            },
            "merge_conflicts",
            ResolverAction.RUN_REMEDIATION,
        ),
        (
            {"checksComplete": False, "checksPassing": False},
            {
                "pr": {"state": "OPEN", "mergeStateStatus": "CLEAN"},
                "ci": {"isRunning": True, "hasFailures": False, "signalQuality": "ok"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": {"includeBotReviewComments": True},
            },
            "ci_running",
            ResolverAction.WAIT,
        ),
        (
            {"checksComplete": True, "checksPassing": False},
            {
                "pr": {"state": "OPEN", "mergeStateStatus": "CLEAN"},
                "ci": {"isRunning": False, "hasFailures": True, "signalQuality": "ok"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": {"includeBotReviewComments": True},
            },
            "ci_failures",
            ResolverAction.RUN_REMEDIATION,
        ),
        (
            {"blockers": [{"kind": "checks_unavailable"}]},
            {
                "pr": {"state": "OPEN"},
                "ci": {"signalQuality": "degraded"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": {"includeBotReviewComments": True},
            },
            "manual_review",
            ResolverAction.STOP_MANUAL_REVIEW,
        ),
        (
            {"blockers": [{"kind": "comments_unavailable"}]},
            {
                "pr": {"state": "OPEN"},
                "ci": {"signalQuality": "ok"},
                "commentsFetch": {"succeeded": False},
            },
            "manual_review",
            ResolverAction.STOP_MANUAL_REVIEW,
        ),
        (
            {
                "blockers": [{"kind": "actionable_comments"}],
                "checksComplete": True,
                "checksPassing": True,
            },
            {
                "pr": {"state": "OPEN"},
                "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": {
                    "includeBotReviewComments": True,
                    "hasActionableComments": True,
                },
            },
            "actionable_comments",
            ResolverAction.RUN_REMEDIATION,
        ),
        (
            {
                "blockers": [{"kind": "automated_review_pending"}],
                "checksComplete": True,
                "checksPassing": True,
            },
            {
                "pr": {"state": "OPEN"},
                "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": {
                    "includeBotReviewComments": True,
                    "codexReviewGrace": {"active": True},
                },
            },
            "review_grace",
            ResolverAction.WAIT,
        ),
        (
            {"blockers": [{"kind": "external_state_unavailable"}]},
            {
                "pr": {"state": "OPEN", "mergeStateStatus": "UNKNOWN"},
                "ci": {"signalQuality": "ok"},
                "commentsFetch": {"succeeded": True},
                "commentsSummary": {"includeBotReviewComments": True},
            },
            "mergeability_transient",
            ResolverAction.WAIT,
        ),
    ],
)
def test_temporal_and_portable_hosts_share_classification(
    temporal, portable, classification, action
) -> None:
    temporal_decision = classify_snapshot(normalize_temporal_snapshot(temporal))
    portable_decision = classify_snapshot(normalize_portable_snapshot(portable))
    assert temporal_decision.classification == classification
    assert portable_decision.classification == classification
    assert temporal_decision.action is action
    assert portable_decision.action is action


def test_unknown_or_degraded_state_never_attempts_merge() -> None:
    for snapshot in (
        CanonicalPullRequestSnapshot(malformed=True),
        CanonicalPullRequestSnapshot(checks_degraded=True),
        CanonicalPullRequestSnapshot(comments_available=False),
        CanonicalPullRequestSnapshot(mergeability_unknown=True),
        CanonicalPullRequestSnapshot(publish_available=False),
    ):
        assert classify_snapshot(snapshot).action is not ResolverAction.ATTEMPT_MERGE


def test_state_reducer_enforces_no_progress_and_finalize_budgets() -> None:
    conflict = CanonicalPullRequestSnapshot(merge_conflict=True)
    first = reduce_resolver_state(
        previous_state=ResolverState(),
        snapshot=conflict,
        policy=ResolverPolicy(max_identical_blockers_without_progress=1),
        event=ResolverEvent(kind="snapshot", progress_signature="same"),
    )
    second = reduce_resolver_state(
        previous_state=first.state,
        snapshot=conflict,
        policy=ResolverPolicy(max_identical_blockers_without_progress=1),
        event=ResolverEvent(kind="snapshot", progress_signature="same"),
    )
    assert first.action is ResolverAction.RUN_REMEDIATION
    assert second.action is ResolverAction.STOP_MANUAL_REVIEW

    ready = CanonicalPullRequestSnapshot(checks_complete=True, checks_passing=True)
    exhausted = reduce_resolver_state(
        previous_state=ResolverState(finalize_attempts=1),
        snapshot=ready,
        policy=ResolverPolicy(max_finalize_attempts=1),
        event=ResolverEvent(kind="snapshot"),
    )
    assert exhausted.decision.reason_code == "finalize_budget_exhausted"


def test_core_has_no_host_or_side_effect_imports() -> None:
    forbidden = {
        "temporalio",
        "moonmind",
        "subprocess",
        "pathlib",
        "os",
        "random",
        "time",
        "requests",
        "httpx",
    }
    for module_name in ("models", "normalize", "classify", "transition"):
        module = __import__(f"pr_resolver_core.{module_name}", fromlist=[module_name])
        tree = ast.parse(inspect.getsource(module))
        imports = {
            node.names[0].name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
        } | {
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        }
        assert not (imports & forbidden), (module_name, imports & forbidden)


def test_core_exports_immutable_identity() -> None:
    assert IMPLEMENTATION_CONTRACT == "pr-resolver-core/v1"
    core_root = Path(inspect.getfile(classify_snapshot)).parent
    semantic_bytes = b"".join(
        (core_root / name).read_bytes()
        for name in ("models.py", "normalize.py", "classify.py", "transition.py")
    )
    assert RESOLVER_CORE_DIGEST == (
        "sha256:" + hashlib.sha256(semantic_bytes).hexdigest()
    )


def test_temporal_snapshot_handles_null_blockers() -> None:
    snapshot = normalize_temporal_snapshot(
        {"blockers": None, "checksComplete": False, "checksPassing": False}
    )

    assert snapshot.merge_conflict is False
    assert snapshot.actionable_comments is False
