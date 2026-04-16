from __future__ import annotations

from moonmind.workflows.temporal.workflows.merge_gate import (
    build_resolver_run_request,
    classify_readiness,
    deterministic_resolver_idempotency_key,
    build_continue_as_new_input,
)


def _pull_request() -> dict[str, object]:
    return {
        "repo": "MoonLadderStudios/MoonMind",
        "number": 341,
        "url": "https://github.com/MoonLadderStudios/MoonMind/pull/341",
        "headSha": "abc123",
        "headBranch": "feature",
        "baseBranch": "main",
    }


def test_classify_readiness_marks_stale_revision_terminal() -> None:
    evidence = classify_readiness(
        {
            "headSha": "def456",
            "pullRequestOpen": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
            "policyAllowed": True,
        },
        tracked_head_sha="abc123",
    )

    assert not evidence.ready
    assert evidence.blockers[0].kind == "stale_revision"
    assert evidence.blockers[0].retryable is False


def test_classify_readiness_sanitizes_secret_like_blocker_summary() -> None:
    evidence = classify_readiness(
        {
            "headSha": "abc123",
            "pullRequestOpen": True,
            "checksComplete": False,
            "checksPassing": False,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
            "policyAllowed": True,
            "blockers": [
                {
                    "kind": "checks_failed",
                    "summary": "failed token=fixture-secret-value",
                    "retryable": True,
                    "source": "github",
                }
            ],
        },
        tracked_head_sha="abc123",
    )

    assert "token=" not in evidence.blockers[0].summary


def test_classify_readiness_maps_unknown_blocker_kind_to_external_unavailable() -> None:
    evidence = classify_readiness(
        {
            "headSha": "abc123",
            "ready": False,
            "blockers": [
                {
                    "kind": "provider_added_new_kind",
                    "summary": "Provider returned a new blocker shape.",
                    "retryable": True,
                    "source": "github",
                }
            ],
        },
        tracked_head_sha="abc123",
    )

    assert evidence.blockers[0].kind == "external_state_unavailable"


def test_deterministic_resolver_idempotency_key_is_revision_scoped() -> None:
    first = deterministic_resolver_idempotency_key(
        parent_workflow_id="mm:parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=341,
        head_sha="abc123",
    )
    second = deterministic_resolver_idempotency_key(
        parent_workflow_id="mm:parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=341,
        head_sha="def456",
    )

    assert first != second
    assert first == "resolver:mm:parent:MoonLadderStudios/MoonMind:341:abc123"


def test_build_resolver_run_request_uses_pr_resolver_and_publish_none() -> None:
    request = build_resolver_run_request(
        parent_workflow_id="mm:parent",
        pull_request=_pull_request(),
        jira_issue_key="MM-341",
        merge_method="squash",
    )

    assert request["initialParameters"]["task"]["skill"]["id"] == "pr-resolver"
    assert request["initialParameters"]["task"]["publish"]["mode"] == "none"
    assert request["initialParameters"]["task"]["tool"]["name"] == "pr-resolver"
    assert request["initialParameters"]["task"]["tool"]["type"] == "skill"
    assert request["initialParameters"]["task"]["tool"]["version"] == "1.0"
    assert request["initialParameters"]["publishMode"] == "none"
    assert request["initialParameters"]["task"]["skill"]["args"]["pr"] == "341"


def test_build_continue_as_new_input_preserves_compact_wait_state() -> None:
    payload = build_continue_as_new_input(
        start_input={
            "workflowType": "MoonMind.MergeAutomation",
            "parentWorkflowId": "mm:parent",
            "parentRunId": "run-1",
            "publishContextRef": "artifact://publish-context",
            "pullRequest": _pull_request(),
            "jiraIssueKey": "MM-351",
            "mergeAutomationConfig": {
                "timeouts": {"fallbackPollSeconds": 15},
                "resolver": {"mergeMethod": "squash"},
            },
            "resolverTemplate": {"repository": "MoonLadderStudios/MoonMind"},
        },
        blockers=[
            {
                "kind": "checks_running",
                "summary": "Checks are running.",
                "retryable": True,
                "source": "github",
            }
        ],
        cycle_count=3,
        resolver_history=[{"workflowId": "resolver-1", "created": True}],
        latest_head_sha="def456",
        expire_at="2026-04-17T00:00:00Z",
    )

    assert payload["workflowType"] == "MoonMind.MergeAutomation"
    assert payload["parentWorkflowId"] == "mm:parent"
    assert payload["publishContextRef"] == "artifact://publish-context"
    assert payload["pullRequest"]["number"] == 341
    assert payload["pullRequest"]["headSha"] == "def456"
    assert payload["blockers"][0]["kind"] == "checks_running"
    assert payload["cycleCount"] == 3
    assert payload["resolverHistory"][0]["workflowId"] == "resolver-1"
    assert payload["expireAt"] == "2026-04-17T00:00:00Z"
