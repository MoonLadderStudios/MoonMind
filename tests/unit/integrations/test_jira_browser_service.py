"""Unit tests for the Jira browser read service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from moonmind.config.settings import AtlassianSettings, FeatureFlagsSettings, JiraSettings
from moonmind.integrations.jira.browser import JiraBrowserService
from moonmind.integrations.jira.errors import JiraToolError

pytestmark = [pytest.mark.asyncio]


class _StubJiraBrowserService(JiraBrowserService):
    def __init__(
        self,
        *,
        atlassian_settings: AtlassianSettings,
        feature_flags: FeatureFlagsSettings | None = None,
        responses: list[Any] | None = None,
    ) -> None:
        super().__init__(
            atlassian_settings=atlassian_settings,
            feature_flags=feature_flags or FeatureFlagsSettings(jira_create_page_enabled=True),
        )
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    @asynccontextmanager
    async def _jira_client(self) -> Any:
        yield object()

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
        return await self._pop_response(
            method=method,
            path=path,
            action=action,
            params=params,
            json_body=json_body,
            context=context,
        )

    async def _request_json_with_client(
        self,
        client: object,
        *,
        method: str,
        path: str,
        action: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        return await self._pop_response(
            method=method,
            path=path,
            action=action,
            params=params,
            json_body=json_body,
            context=context,
        )

    async def _pop_response(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: dict[str, Any] | None,
        json_body: Any,
        context: dict[str, Any] | None,
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


def _settings(
    *,
    allowed_projects: str | None = None,
) -> AtlassianSettings:
    return AtlassianSettings(
        jira=JiraSettings(jira_allowed_projects=allowed_projects),
    )


async def test_browser_service_requires_create_page_feature_flag() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(),
        feature_flags=FeatureFlagsSettings(jira_create_page_enabled=False),
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.verify_connection()

    assert excinfo.value.code == "tool_not_found"
    assert service.calls == []


async def test_browser_service_surfaces_missing_jira_configuration() -> None:
    service = JiraBrowserService(
        atlassian_settings=_settings(),
        feature_flags=FeatureFlagsSettings(jira_create_page_enabled=True),
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.verify_connection()

    assert excinfo.value.code == "jira_not_configured"


async def test_verify_connection_with_project_uses_allowed_project_boundary() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[{"id": "10000", "key": "ENG", "name": "Engineering"}],
    )

    result = await service.verify_connection(project_key="eng")

    assert result.ok is True
    assert result.project_key == "ENG"
    assert result.project_name == "Engineering"
    assert service.calls == [
        {
            "method": "GET",
            "path": "/project/ENG",
            "action": "jira_browser.verify_connection",
            "params": None,
            "json_body": None,
            "context": {"projectKey": "ENG"},
        }
    ]


async def test_verify_connection_denies_disallowed_project_before_request() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.verify_connection(project_key="OPS")

    assert excinfo.value.code == "jira_policy_denied"
    assert service.calls == []


async def test_list_projects_fetches_only_allowed_projects() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG,OPS"),
        responses=[
            {"id": "10000", "key": "ENG", "name": "Engineering"},
            {"id": "10001", "key": "OPS", "name": "Operations"},
        ],
    )

    result = await service.list_projects()

    assert [item.project_key for item in result.items] == ["ENG", "OPS"]
    assert [call["path"] for call in service.calls] == ["/project/ENG", "/project/OPS"]


async def test_list_projects_skips_failed_allowed_project_fetches() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG,OPS"),
        responses=[
            {"id": "10000", "key": "ENG", "name": "Engineering"},
            JiraToolError(
                "Jira could not find the requested project.",
                code="jira_not_found",
                status_code=404,
                action="jira_browser.list_projects",
            ),
        ],
    )

    result = await service.list_projects()

    assert [item.project_key for item in result.items] == ["ENG"]
    assert [call["path"] for call in service.calls] == ["/project/ENG", "/project/OPS"]


async def test_list_projects_returns_empty_response_when_jira_has_no_projects() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(),
        responses=[{"values": []}],
    )

    result = await service.list_projects()

    assert result.items == []
    assert service.calls[0]["path"] == "/project/search"


async def test_list_boards_uses_agile_path_alias() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "values": [
                    {
                        "id": 42,
                        "name": "Delivery",
                        "type": "kanban",
                        "location": {"projectKey": "ENG"},
                    }
                ]
            }
        ],
    )

    result = await service.list_boards("ENG")

    assert [item.id for item in result.items] == ["42"]
    assert service.calls[0]["path"] == "agile:/board"
    assert service.calls[0]["params"] == {
        "projectKeyOrId": "ENG",
        "maxResults": 50,
    }


async def test_list_boards_returns_empty_response_when_project_has_no_boards() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[{"values": []}],
    )

    result = await service.list_boards("ENG")

    assert result.project_key == "ENG"
    assert result.items == []


async def test_list_boards_enforces_project_allowlist() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.list_boards("OPS")

    assert excinfo.value.code == "jira_policy_denied"
    assert service.calls == []


async def test_browser_service_rejects_invalid_path_inputs_before_request() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
    )

    with pytest.raises(JiraToolError) as project_error:
        await service.list_boards("bad-key")
    with pytest.raises(JiraToolError) as board_error:
        await service.list_columns("../42")
    with pytest.raises(JiraToolError) as issue_error:
        await service.get_issue("not-an-issue-key")

    assert project_error.value.code == "jira_validation_failed"
    assert board_error.value.code == "jira_validation_failed"
    assert issue_error.value.code == "jira_validation_failed"
    assert service.calls == []


async def test_board_columns_preserve_order_and_status_mapping() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "id": 42,
                "name": "Delivery",
                "type": "kanban",
                "location": {"projectKey": "ENG"},
            },
            {
                "columnConfig": {
                    "columns": [
                        {
                            "name": "To Do",
                            "statuses": [{"id": "1", "name": "Open"}],
                        },
                        {
                            "name": "Done",
                            "statuses": [{"id": "3", "name": "Done"}],
                        },
                    ]
                }
            },
        ],
    )

    result = await service.list_columns("42")

    assert result.board.project_key == "ENG"
    assert [column.id for column in result.columns] == ["to-do", "done"]
    assert [column.order for column in result.columns] == [0, 1]
    assert result.columns[0].status_ids == ["1"]
    assert [call["path"] for call in service.calls] == [
        "agile:/board/42",
        "agile:/board/42/configuration",
    ]


async def test_board_columns_returns_empty_columns_when_configuration_has_no_columns() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "id": 42,
                "name": "Delivery",
                "type": "kanban",
                "location": {"projectKey": "ENG"},
            },
            {"columnConfig": {"columns": []}},
        ],
    )

    result = await service.list_columns("42")

    assert result.board.project_key == "ENG"
    assert result.columns == []


async def test_board_issues_group_by_status_and_keep_unmapped_bucket() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "id": 42,
                "name": "Delivery",
                "type": "kanban",
                "location": {"projectKey": "ENG"},
            },
            {
                "columnConfig": {
                    "columns": [
                        {"name": "To Do", "statuses": [{"id": "1", "name": "Open"}]},
                        {"name": "Done", "statuses": [{"id": "3", "name": "Done"}]},
                    ]
                }
            },
            {
                "issues": [
                    {
                        "key": "ENG-1",
                        "fields": {
                            "summary": "Build browser",
                            "issuetype": {"name": "Story"},
                            "status": {"id": "1", "name": "Open"},
                            "assignee": {"displayName": "Ada"},
                            "updated": "2026-04-10T19:30:00.000+0000",
                        },
                    },
                    {
                        "key": "ENG-2",
                        "fields": {
                            "summary": "Unknown status",
                            "status": {"id": "99", "name": "Blocked"},
                        },
                    },
                ]
            },
            {"issues": [], "total": 2},
        ],
    )

    result = await service.list_issues("42")

    assert [item.issue_key for item in result.items_by_column["to-do"]] == ["ENG-1"]
    assert result.items_by_column["done"] == []
    assert [item.issue_key for item in result.unmapped_items] == ["ENG-2"]
    assert result.unmapped_items[0].column_id == "__unmapped"
    assert [call["path"] for call in service.calls] == [
        "agile:/board/42",
        "agile:/board/42/configuration",
        "agile:/board/42/issue",
    ]
    assert service.calls[2]["params"] == {
        "fields": "summary,issuetype,status,assignee,updated",
        "maxResults": 50,
        "startAt": 0,
    }


async def test_board_issues_returns_empty_column_buckets_when_jira_has_no_issues() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "id": 42,
                "name": "Delivery",
                "type": "kanban",
                "location": {"projectKey": "ENG"},
            },
            {
                "columnConfig": {
                    "columns": [
                        {"name": "To Do", "statuses": [{"id": "1", "name": "Open"}]},
                        {"name": "Done", "statuses": [{"id": "3", "name": "Done"}]},
                    ]
                }
            },
            {"issues": [], "total": 0},
        ],
    )

    result = await service.list_issues("42")

    assert [column.id for column in result.columns] == ["to-do", "done"]
    assert result.items_by_column == {"to-do": [], "done": []}
    assert result.unmapped_items == []


async def test_board_issues_paginates_until_all_issues_are_loaded() -> None:
    first_page = [
        {
            "key": f"ENG-{index}",
            "fields": {
                "summary": f"Issue {index}",
                "status": {"id": "1", "name": "Open"},
            },
        }
        for index in range(1, 51)
    ]
    second_page = [
        {
            "key": "ENG-51",
            "fields": {
                "summary": "Issue 51",
                "status": {"id": "1", "name": "Open"},
            },
        }
    ]
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "id": 42,
                "name": "Delivery",
                "type": "kanban",
                "location": {"projectKey": "ENG"},
            },
            {
                "columnConfig": {
                    "columns": [
                        {"name": "To Do", "statuses": [{"id": "1", "name": "Open"}]},
                    ]
                }
            },
            {"issues": first_page, "total": 51},
            {"issues": second_page, "total": 51},
        ],
    )

    result = await service.list_issues("42")

    assert len(result.items_by_column["to-do"]) == 51
    issue_calls = [call for call in service.calls if call["path"] == "agile:/board/42/issue"]
    assert [call["params"]["startAt"] for call in issue_calls] == [0, 50]


async def test_board_issues_filters_by_query_text() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "id": 42,
                "name": "Delivery",
                "type": "kanban",
                "location": {"projectKey": "ENG"},
            },
            {
                "columnConfig": {
                    "columns": [
                        {"name": "To Do", "statuses": [{"id": "1", "name": "Open"}]},
                    ]
                }
            },
            {
                "issues": [
                    {
                        "key": "ENG-1",
                        "fields": {"summary": "Build browser", "status": {"id": "1"}},
                    },
                    {
                        "key": "ENG-2",
                        "fields": {"summary": "Write docs", "status": {"id": "1"}},
                    },
                ]
            },
            {"issues": [], "total": 2},
        ],
    )

    result = await service.list_issues("42", q="docs")

    assert [item.issue_key for item in result.items_by_column["to-do"]] == ["ENG-2"]


async def test_issue_detail_normalizes_text_and_recommended_imports() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "key": "ENG-123",
                "self": "https://jira.example/rest/api/3/issue/10001",
                "names": {"customfield_10042": "Acceptance Criteria"},
                "fields": {
                    "summary": "Add Jira browser",
                    "issuetype": {"name": "Story"},
                    "status": {"id": "1", "name": "Open"},
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "As an operator"},
                                    {"type": "hardBreak"},
                                    {"type": "text", "text": "I can browse Jira"},
                                ],
                            }
                        ],
                    },
                    "customfield_10042": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Given a board"}],
                            }
                        ],
                    },
                },
            }
        ],
    )

    result = await service.get_issue("ENG-123")

    assert result.description_text == "As an operator\nI can browse Jira"
    assert result.acceptance_criteria_text == "Given a board"
    assert "ENG-123: Add Jira browser" in result.recommended_imports.preset_instructions
    assert "Acceptance criteria\nGiven a board" in result.recommended_imports.step_instructions


async def test_issue_detail_ignores_blank_browse_url_and_falls_back_to_self_url() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "key": "ENG-123",
                "browseUrl": "  ",
                "self": "https://jira.example/rest/api/3/issue/10001",
                "fields": {
                    "summary": "Add Jira browser",
                    "status": {"id": "1", "name": "Open"},
                    "description": "Do the work",
                },
            }
        ],
    )

    result = await service.get_issue("ENG-123")

    assert result.url == "https://jira.example/browse/ENG-123"


async def test_issue_detail_maps_status_to_board_column_when_board_context_is_provided() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(allowed_projects="ENG"),
        responses=[
            {
                "key": "ENG-123",
                "fields": {
                    "summary": "Add Jira browser",
                    "status": {"id": "2", "name": "In Progress"},
                    "description": "Do the work",
                },
            },
            {
                "id": 42,
                "name": "Delivery",
                "type": "kanban",
                "location": {"projectKey": "ENG"},
            },
            {
                "columnConfig": {
                    "columns": [
                        {
                            "name": "In Progress",
                            "statuses": [{"id": "2", "name": "In Progress"}],
                        },
                    ]
                }
            },
        ],
    )

    result = await service.get_issue("ENG-123", board_id="42")

    assert result.column is not None
    assert result.column.id == "in-progress"
    assert result.column.name == "In Progress"
    assert [call["path"] for call in service.calls] == [
        "/issue/ENG-123",
        "agile:/board/42",
        "agile:/board/42/configuration",
    ]


async def test_issue_detail_falls_back_to_description_acceptance_section() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(),
        responses=[
            {
                "key": "ENG-123",
                "fields": {
                    "summary": "Add Jira browser",
                    "description": "Do the work\n\nAcceptance Criteria\n- It works",
                },
            }
        ],
    )

    result = await service.get_issue("ENG-123")

    assert result.description_text == "Do the work"
    assert result.acceptance_criteria_text == "- It works"


async def test_service_errors_do_not_include_secret_material() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_settings(),
        responses=[
            JiraToolError(
                "Jira request failed.",
                code="jira_request_failed",
                status_code=502,
                action="jira_browser.get_issue",
            )
        ],
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.get_issue("ENG-1")

    message = str(excinfo.value)
    assert "token" not in message.lower()
    assert "authorization" not in message.lower()
