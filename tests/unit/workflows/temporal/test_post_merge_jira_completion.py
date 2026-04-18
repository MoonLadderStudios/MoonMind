from __future__ import annotations

import pytest

from moonmind.workflows.temporal.post_merge_jira_completion import (
    JiraIssueCandidate,
    PostMergeJiraCompletionConfig,
    PostMergeJiraCompletionDecision,
    candidate_keys_from_payload,
    complete_post_merge_jira,
    resolve_issue_key,
    select_done_transition,
)


def _issue(key: str = "MM-403", category: str = "indeterminate") -> dict[str, object]:
    return {
        "key": key,
        "fields": {
            "status": {
                "name": "Code Review",
                "statusCategory": {"key": category, "name": category.title()},
            }
        },
    }


def _transition(
    transition_id: str = "41",
    name: str = "Done",
    category: str = "done",
    fields: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": transition_id,
        "name": name,
        "to": {
            "name": name,
            "statusCategory": {"key": category, "name": category.title()},
        },
        "fields": fields or {},
    }


def test_candidate_keys_prefers_explicit_post_merge_issue_key() -> None:
    candidates = candidate_keys_from_payload(
        {
            "jiraIssueKey": "MM-402",
            "postMergeJira": {"issueKey": "MM-403"},
            "candidateContext": {"prMetadataKeys": ["MM-404"]},
        }
    )

    assert [(item.issue_key, item.source) for item in candidates] == [
        ("MM-403", "explicit_post_merge")
    ]


@pytest.mark.asyncio
async def test_resolve_issue_key_deduplicates_and_detects_ambiguity() -> None:
    calls: list[str] = []

    async def get_issue(issue_key: str) -> dict[str, object]:
        calls.append(issue_key)
        return _issue(issue_key)

    resolved = await resolve_issue_key(
        [
            JiraIssueCandidate(issue_key="mm-403", source="merge_automation"),
            JiraIssueCandidate(issue_key="MM-403", source="task_origin"),
        ],
        get_issue=get_issue,
    )
    assert resolved["status"] == "resolved"
    assert resolved["issueKey"] == "MM-403"
    assert resolved["source"] == "merge_automation"
    assert [item["source"] for item in resolved["candidates"]] == [
        "merge_automation",
        "task_origin",
    ]
    assert calls == ["MM-403"]

    ambiguous = await resolve_issue_key(
        [
            JiraIssueCandidate(issue_key="MM-403", source="merge_automation"),
            JiraIssueCandidate(issue_key="MM-404", source="pr_metadata"),
        ],
        get_issue=get_issue,
    )
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["issueKey"] is None
    assert calls == ["MM-403", "MM-403", "MM-404"]


def test_select_done_transition_requires_exactly_one_safe_done_transition() -> None:
    config = PostMergeJiraCompletionConfig()

    selected = select_done_transition(
        issue=_issue(),
        transitions=[_transition("41", "Done")],
        config=config,
    )
    assert selected["status"] == "selected"
    assert selected["transition"]["transitionId"] == "41"

    ambiguous = select_done_transition(
        issue=_issue(),
        transitions=[_transition("41", "Done"), _transition("51", "Close")],
        config=config,
    )
    assert ambiguous["status"] == "blocked"
    assert ambiguous["transition"] is None

    required_field = select_done_transition(
        issue=_issue(),
        transitions=[
            _transition(
                "41",
                "Done",
                fields={"resolution": {"required": True, "name": "Resolution"}},
            )
        ],
        config=config,
    )
    assert required_field["status"] == "blocked"
    assert "required field" in required_field["reason"]


def test_select_done_transition_supports_already_done_noop_and_explicit_name() -> None:
    noop = select_done_transition(
        issue=_issue(category="done"),
        transitions=[_transition("41", "Done")],
        config=PostMergeJiraCompletionConfig(),
    )
    assert noop["status"] == "noop_already_done"

    explicit = select_done_transition(
        issue=_issue(),
        transitions=[_transition("41", "Resolve"), _transition("51", "Done")],
        config=PostMergeJiraCompletionConfig(transitionName="resolve"),
    )
    assert explicit["status"] == "selected"
    assert explicit["transition"]["transitionId"] == "41"


def test_completion_decision_is_compact_and_credential_free() -> None:
    decision = PostMergeJiraCompletionDecision(
        status="blocked",
        required=True,
        issueResolution={"status": "missing", "reason": "No issue key"},
        reason="No issue key",
        artifactRefs={"authorization": "Bearer should-not-escape"},
    )

    dumped = decision.to_summary()

    assert dumped["status"] == "blocked"
    assert "authorization" not in str(dumped).lower()


@pytest.mark.asyncio
async def test_complete_post_merge_jira_transitions_one_valid_issue() -> None:
    calls: list[tuple[str, object]] = []

    async def get_issue(issue_key: str) -> dict[str, object]:
        calls.append(("get_issue", issue_key))
        return _issue(issue_key)

    async def get_transitions(issue_key: str) -> list[dict[str, object]]:
        calls.append(("get_transitions", issue_key))
        return [_transition("41", "Done")]

    async def transition_issue(
        issue_key: str,
        transition_id: str,
        fields: dict[str, object],
    ) -> dict[str, object]:
        calls.append(("transition_issue", (issue_key, transition_id, fields)))
        return {"transitioned": True}

    decision = await complete_post_merge_jira(
        {
            "jiraIssueKey": "MM-403",
            "postMergeJira": {"enabled": True, "required": True},
        },
        get_issue=get_issue,
        get_transitions=get_transitions,
        transition_issue=transition_issue,
    )

    assert decision.status == "succeeded"
    assert decision.transitioned is True
    assert calls[-1] == ("transition_issue", ("MM-403", "41", {}))


@pytest.mark.asyncio
async def test_complete_post_merge_jira_returns_failed_decision_for_read_failures() -> None:
    calls: list[tuple[str, object]] = []

    async def get_issue(issue_key: str) -> dict[str, object]:
        calls.append(("get_issue", issue_key))
        if len([call for call in calls if call[0] == "get_issue"]) > 1:
            raise RuntimeError("temporary Jira read failure token=secret")
        return _issue(issue_key)

    async def get_transitions(issue_key: str) -> list[dict[str, object]]:
        calls.append(("get_transitions", issue_key))
        return [_transition("41", "Done")]

    async def transition_issue(
        issue_key: str,
        transition_id: str,
        fields: dict[str, object],
    ) -> dict[str, object]:
        calls.append(("transition_issue", (issue_key, transition_id, fields)))
        return {"transitioned": True}

    decision = await complete_post_merge_jira(
        {
            "jiraIssueKey": "MM-403",
            "postMergeJira": {"enabled": True, "required": False},
        },
        get_issue=get_issue,
        get_transitions=get_transitions,
        transition_issue=transition_issue,
    )

    assert decision.status == "failed"
    assert decision.required is False
    assert decision.issueResolution["issueKey"] == "MM-403"
    assert "secret" not in str(decision.to_summary())
    assert ("transition_issue", ("MM-403", "41", {})) not in calls
