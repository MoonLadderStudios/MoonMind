"""Unit tests for the high-level Jira tool service."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.config.settings import AtlassianSettings, JiraSettings
from moonmind.integrations.jira.errors import JiraToolError
from moonmind.integrations.jira.models import (
    AddCommentRequest,
    CreateIssueRequest,
    EditIssueRequest,
    SearchIssuesRequest,
    TransitionIssueRequest,
    VerifyConnectionRequest,
)
from moonmind.integrations.jira.tool import JiraToolService

pytestmark = [pytest.mark.asyncio]


class _StubJiraToolService(JiraToolService):
    def __init__(
        self,
        *,
        atlassian_settings: AtlassianSettings,
        responses: list[Any] | None = None,
    ) -> None:
        super().__init__(atlassian_settings=atlassian_settings)
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "action": action,
                "params": params,
                "json_body": json_body,
                "context": context,
            }
        )
        if not self._responses:
            return {}
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _build_settings(
    *,
    jira: JiraSettings | None = None,
    **overrides: object,
) -> AtlassianSettings:
    return AtlassianSettings(
        jira=jira or JiraSettings(jira_tool_enabled=True),
        **overrides,
    )


async def test_create_issue_converts_multiline_description_and_sanitizes_result() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(),
        responses=[
            {
                "key": "ENG-101",
                "id": "101",
                "self": "https://jira.example/rest/api/3/issue/101",
                "authorization": "Bearer should-not-escape",
            }
        ],
    )

    result = await service.create_issue(
        CreateIssueRequest(
            projectKey="ENG",
            issueTypeId="10001",
            summary="Add Jira integration",
            description="Line 1\nLine 2",
            fields={"priority": {"name": "High"}},
        )
    )

    assert result == {
        "created": True,
        "issueKey": "ENG-101",
        "issueId": "101",
        "self": "https://jira.example/rest/api/3/issue/101",
    }
    payload = service.calls[0]["json_body"]
    description = payload["fields"]["description"]
    assert description["type"] == "doc"
    assert description["content"][0]["content"][1] == {"type": "hardBreak"}
    assert "authorization" not in result


async def test_edit_issue_does_not_attempt_transition() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(),
        responses=[{}],
    )

    result = await service.edit_issue(
        EditIssueRequest(
            issueKey="ENG-123",
            fields={"summary": "Finalize design"},
        )
    )

    assert result == {"updated": True, "issueKey": "ENG-123"}
    assert len(service.calls) == 1
    assert service.calls[0]["method"] == "PUT"
    assert service.calls[0]["path"] == "/issue/ENG-123"
    assert "/transitions" not in service.calls[0]["path"]


async def test_search_issues_requires_project_key_when_multiple_projects_allowed() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_tool_enabled=True,
                jira_allowed_projects="ENG,OPS",
            )
        )
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.search_issues(SearchIssuesRequest(jql="status = 'Todo'"))

    assert excinfo.value.code == "jira_policy_denied"
    assert service.calls == []


async def test_transition_issue_requires_explicit_lookup_and_rejects_stale_transition() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_tool_enabled=True,
                jira_require_explicit_transition_lookup=True,
            )
        ),
        responses=[{"transitions": [{"id": "11", "name": "Done"}]}],
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.transition_issue(
            TransitionIssueRequest(
                issueKey="ENG-123",
                transitionId="31",
            )
        )

    assert excinfo.value.code == "jira_validation_failed"
    assert len(service.calls) == 1
    assert service.calls[0]["path"] == "/issue/ENG-123/transitions"
    assert service.calls[0]["method"] == "GET"


async def test_transition_issue_allows_preflight_without_get_transitions_allowlist() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_tool_enabled=True,
                jira_allowed_actions="transition_issue",
                jira_require_explicit_transition_lookup=True,
            )
        ),
        responses=[
            {"transitions": [{"id": "31", "name": "Done"}]},
            {},
        ],
    )

    result = await service.transition_issue(
        TransitionIssueRequest(
            issueKey="ENG-123",
            transitionId="31",
        )
    )

    assert result == {
        "transitioned": True,
        "issueKey": "ENG-123",
        "transitionId": "31",
    }
    assert [call["method"] for call in service.calls] == ["GET", "POST"]


async def test_add_comment_converts_plain_text_to_adf() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(),
        responses=[{"id": "20001"}],
    )

    result = await service.add_comment(
        AddCommentRequest(
            issueKey="ENG-123",
            body="First line\nSecond line",
        )
    )

    assert result == {"id": "20001"}
    body = service.calls[0]["json_body"]["body"]
    assert body["type"] == "doc"
    assert body["content"][0]["content"][1] == {"type": "hardBreak"}


async def test_search_issues_preserves_order_by_after_project_scoping() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(),
        responses=[{"issues": []}],
    )

    await service.search_issues(
        SearchIssuesRequest(
            projectKey="ENG",
            jql="status = 'Todo' ORDER BY created DESC",
        )
    )

    assert (
        service.calls[0]["json_body"]["jql"]
        == "project = ENG AND (status = 'Todo') ORDER BY created DESC"
    )


async def test_verify_connection_returns_project_result() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(),
        responses=[{"name": "Engineering Platform"}],
    )

    result = await service.verify_connection(VerifyConnectionRequest(projectKey="ENG"))

    assert result == {
        "ok": True,
        "projectKey": "ENG",
        "projectName": "Engineering Platform",
    }
    assert service.calls[0]["path"] == "/project/ENG"


async def test_action_allowlist_denies_disallowed_mutation_before_request() -> None:
    service = _StubJiraToolService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_tool_enabled=True,
                jira_allowed_actions="get_issue,search_issues",
            )
        )
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.create_issue(
            CreateIssueRequest(
                projectKey="ENG",
                issueTypeId="10001",
                summary="Blocked by policy",
            )
        )

    assert excinfo.value.code == "jira_policy_denied"
    assert service.calls == []
