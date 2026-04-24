from __future__ import annotations

from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import _ACTIVITY_HANDLER_ATTRS
from moonmind.workflows.temporal.workflows import merge_gate
from moonmind.workflows.temporal.workflows.merge_gate import (
    build_resolver_run_request,
    classify_readiness,
    deterministic_resolver_idempotency_key,
    build_continue_as_new_input,
    legacy_resolver_idempotency_key,
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

def test_merge_gate_module_does_not_define_legacy_workflow_class() -> None:
    assert not hasattr(merge_gate, "MoonMindMergeAutomationWorkflow")

def test_legacy_resolver_run_activity_is_not_registered() -> None:
    activity_types = {
        entry.activity_type for entry in build_default_activity_catalog().activities
    }

    assert "merge_automation.evaluate_readiness" in activity_types
    assert "merge_automation.create_resolver_run" not in activity_types
    assert "merge_automation.create_resolver_run" not in _ACTIVITY_HANDLER_ATTRS

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

def test_classify_readiness_treats_merged_pr_as_terminal_success_evidence() -> None:
    evidence = classify_readiness(
        {
            "headSha": "def456",
            "ready": False,
            "pullRequestOpen": False,
            "pullRequestMerged": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
            "policyAllowed": True,
        },
        tracked_head_sha="abc123",
    )

    assert evidence.pull_request_merged is True
    assert evidence.ready is False
    assert evidence.blockers == []

def test_classify_readiness_sanitizes_secret_like_blocker_summary() -> None:
    evidence = classify_readiness(
        {
            "headSha": "abc123",
            "pullRequestOpen": True,
            "checksComplete": False,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
            "policyAllowed": True,
            "blockers": [
                {
                    "kind": "checks_running",
                    "summary": "failed token=fixture-secret-value",
                    "retryable": True,
                    "source": "github",
                }
            ],
        },
        tracked_head_sha="abc123",
    )

    assert "token=" not in evidence.blockers[0].summary

def test_classify_readiness_allows_resolver_launch_when_checks_failed_but_are_complete() -> None:
    evidence = classify_readiness(
        {
            "headSha": "abc123",
            "ready": False,
            "pullRequestOpen": True,
            "checksComplete": True,
            "checksPassing": False,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
            "policyAllowed": True,
            "blockers": [
                {
                    "kind": "checks_failed",
                    "summary": "Required checks are failing.",
                    "retryable": True,
                    "source": "github",
                }
            ],
        },
        tracked_head_sha="abc123",
    )

    assert evidence.ready is True
    assert evidence.blockers == []

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
    assert first.startswith("resolver:pr:341:head:abc123:h:")
    assert len(first) <= 64
    assert "mm:parent" not in first
    assert "MoonLadderStudios" not in first
    assert "/" not in first

def test_legacy_resolver_idempotency_key_preserves_replay_format() -> None:
    legacy = legacy_resolver_idempotency_key(
        parent_workflow_id="mm:parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=341,
        head_sha="abc123",
    )

    assert legacy == "resolver:mm:parent:pr:341:head:abc123"

def test_build_resolver_run_request_uses_pr_resolver_and_publish_none() -> None:
    request = build_resolver_run_request(
        parent_workflow_id="mm:parent",
        pull_request=_pull_request(),
        jira_issue_key="MM-341",
        merge_method="squash",
    )

    assert request["workflow_type"] == "MoonMind.Run"
    assert request["initial_parameters"]["task"]["skill"]["id"] == "pr-resolver"
    assert request["initial_parameters"]["task"]["publish"]["mode"] == "none"
    assert request["initial_parameters"]["task"]["tool"]["name"] == "pr-resolver"
    assert request["initial_parameters"]["task"]["tool"]["type"] == "skill"
    assert request["initial_parameters"]["task"]["tool"]["version"] == "1.0"
    assert request["initial_parameters"]["publishMode"] == "none"
    assert request["initial_parameters"]["task"]["skill"]["args"]["pr"] == "341"

def test_build_resolver_run_request_pins_parent_provider_profile() -> None:
    request = build_resolver_run_request(
        parent_workflow_id="mm:parent",
        pull_request=_pull_request(),
        jira_issue_key=None,
        merge_method="squash",
        resolver_template={
            "targetRuntime": "codex_cli",
            "executionProfileRef": "codex_default",
            "model": "gpt-5.4",
            "effort": "high",
            "requiredCapabilities": ["git", "gh"],
        },
    )

    initial_parameters = request["initial_parameters"]
    runtime = initial_parameters["task"]["runtime"]
    assert initial_parameters["targetRuntime"] == "codex_cli"
    assert initial_parameters["requiredCapabilities"] == ["git", "gh"]
    assert runtime["mode"] == "codex_cli"
    assert runtime["executionProfileRef"] == "codex_default"
    assert runtime["model"] == "gpt-5.4"
    assert runtime["effort"] == "high"
    assert "profileId" not in runtime
    assert "providerProfile" not in runtime

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
