"""Unit tests for the Jira Create-page browser service."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.config.settings import AtlassianSettings, JiraSettings
from moonmind.integrations.jira.browser import JiraBrowserService
from moonmind.integrations.jira.errors import JiraToolError

pytestmark = [pytest.mark.asyncio]


class _StubJiraBrowserService(JiraBrowserService):
    def __init__(
        self,
        *,
        atlassian_settings: AtlassianSettings,
        responses: list[Any] | None = None,
    ) -> None:
        super().__init__(
            atlassian_settings=atlassian_settings,
            browser_enabled=True,
        )
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
) -> AtlassianSettings:
    return AtlassianSettings(
        jira=jira or JiraSettings(jira_enabled=True),
    )


async def test_verify_connection_uses_trusted_jira_boundary() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
        responses=[{"accountId": "acct-1", "displayName": "Ada Lovelace"}],
    )

    result = await service.verify_connection()

    assert result.model_dump(by_alias=True, exclude_none=True) == {
        "ok": True,
        "accountId": "acct-1",
        "displayName": "Ada Lovelace",
    }
    assert service.calls[0]["path"] == "/myself"
    assert service.calls[0]["action"] == "jira_browser_verify_connection"


async def test_verify_connection_with_project_enforces_policy_and_loads_project() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_enabled=True,
                jira_allowed_projects="ENG",
            )
        ),
        responses=[{"id": "10001", "key": "ENG", "name": "Engineering"}],
    )

    result = await service.verify_connection("eng")

    assert result.model_dump(by_alias=True, exclude_none=True) == {
        "ok": True,
        "projectKey": "ENG",
        "projectName": "Engineering",
    }
    assert service.calls == [
        {
            "method": "GET",
            "path": "/project/ENG",
            "action": "jira_browser_verify_connection",
            "params": None,
            "json_body": None,
            "context": {"projectKey": "ENG"},
        }
    ]


async def test_verify_connection_with_missing_jira_configuration_fails_safely() -> None:
    service = JiraBrowserService(
        atlassian_settings=_build_settings(),
        browser_enabled=True,
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.verify_connection()

    assert excinfo.value.code == "jira_not_configured"
    assert excinfo.value.status_code == 503


async def test_list_projects_fetches_only_allowed_projects_when_allowlist_exists() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_enabled=True,
                jira_allowed_projects="ENG,OPS",
            )
        ),
        responses=[
            {"id": "10001", "key": "ENG", "name": "Engineering"},
            {"id": "10002", "key": "OPS", "name": "Operations"},
        ],
    )

    result = await service.list_projects()

    assert result.model_dump(by_alias=True) == {
        "items": [
            {"id": "10001", "projectKey": "ENG", "name": "Engineering"},
            {"id": "10002", "projectKey": "OPS", "name": "Operations"},
        ]
    }
    assert [call["path"] for call in service.calls] == ["/project/ENG", "/project/OPS"]


async def test_list_projects_without_allowlist_uses_project_search() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
        responses=[
            {
                "values": [
                    {"id": "10001", "key": "ENG", "name": "Engineering"},
                    {"id": "10002", "key": "OPS", "name": "Operations"},
                ]
            }
        ],
    )

    result = await service.list_projects()

    assert result.model_dump(by_alias=True) == {
        "items": [
            {"id": "10001", "projectKey": "ENG", "name": "Engineering"},
            {"id": "10002", "projectKey": "OPS", "name": "Operations"},
        ]
    }
    assert service.calls[0]["path"] == "/project/search"
    assert service.calls[0]["params"] == {"maxResults": 100}


async def test_list_project_boards_rejects_denied_project_before_request() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_enabled=True,
                jira_allowed_projects="ENG",
            )
        )
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.list_project_boards("OPS")

    assert excinfo.value.code == "jira_policy_denied"
    assert service.calls == []


async def test_list_project_boards_normalizes_board_metadata() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
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

    result = await service.list_project_boards("eng")

    assert result.model_dump(by_alias=True) == {
        "projectKey": "ENG",
        "items": [
            {
                "id": "42",
                "name": "Delivery",
                "projectKey": "ENG",
                "type": "kanban",
            }
        ],
    }
    assert service.calls[0]["path"] == "agile:/board"
    assert service.calls[0]["params"] == {
        "projectKeyOrId": "ENG",
        "maxResults": 100,
    }


async def test_board_columns_normalize_config_order_and_status_mapping() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
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
                            "statuses": [{"id": "1", "name": "Backlog"}],
                        },
                        {
                            "name": "In Progress",
                            "statuses": [{"id": "3", "name": "Doing"}],
                        },
                    ]
                }
            },
        ],
    )

    result = await service.list_board_columns("42")

    assert result.model_dump(by_alias=True) == {
        "board": {
            "id": "42",
            "name": "Delivery",
            "projectKey": "ENG",
            "type": "kanban",
        },
        "columns": [
            {
                "id": "to-do",
                "name": "To Do",
                "order": 0,
                "count": 0,
                "statusIds": ["1"],
            },
            {
                "id": "in-progress",
                "name": "In Progress",
                "order": 1,
                "count": 0,
                "statusIds": ["3"],
            },
        ],
    }


async def test_board_issues_group_by_status_mapping_and_preserve_empty_columns() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
        responses=[
            {"id": 42, "name": "Delivery", "location": {"projectKey": "ENG"}},
            {
                "columnConfig": {
                    "columns": [
                        {"name": "To Do", "statuses": [{"id": "1"}]},
                        {"name": "Done", "statuses": [{"id": "9"}]},
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
                            "status": {"id": "1", "name": "Backlog"},
                            "assignee": {"displayName": "Ada"},
                            "updated": "2026-04-10T19:30:00.000+0000",
                        },
                    },
                    {
                        "key": "ENG-2",
                        "fields": {
                            "summary": "Unknown status",
                            "status": {"id": "777", "name": "Review"},
                        },
                    },
                ]
            },
        ],
    )

    result = await service.list_board_issues("42")
    payload = result.model_dump(by_alias=True)

    assert payload["itemsByColumn"]["to-do"][0]["issueKey"] == "ENG-1"
    assert payload["itemsByColumn"]["done"] == []
    assert payload["itemsByColumn"]["to-do"][0]["columnId"] == "to-do"
    assert payload["unmappedItems"][0]["issueKey"] == "ENG-2"
    assert payload["unmappedItems"][0]["columnId"] == "unmapped"
    assert payload["columns"][0]["count"] == 1
    assert payload["columns"][1]["count"] == 0


async def test_board_issues_filter_by_issue_key_or_summary() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
        responses=[
            {"id": 42, "name": "Delivery", "location": {"projectKey": "ENG"}},
            {
                "columnConfig": {
                    "columns": [{"name": "To Do", "statuses": [{"id": "1"}]}]
                }
            },
            {
                "issues": [
                    {
                        "key": "ENG-1",
                        "fields": {
                            "summary": "Build browser",
                            "status": {"id": "1", "name": "Backlog"},
                        },
                    },
                    {
                        "key": "ENG-2",
                        "fields": {
                            "summary": "Ship importer",
                            "status": {"id": "1", "name": "Backlog"},
                        },
                    },
                ]
            },
        ],
    )

    result = await service.list_board_issues("42", query="importer")
    payload = result.model_dump(by_alias=True)

    assert [item["issueKey"] for item in payload["itemsByColumn"]["to-do"]] == [
        "ENG-2"
    ]
    assert payload["columns"][0]["count"] == 1


async def test_issue_detail_normalizes_rich_text_and_recommended_imports() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
        responses=[
            {
                "key": "ENG-123",
                "fields": {
                    "summary": "Import Jira stories",
                    "issuetype": {"name": "Story"},
                    "status": {"id": "3", "name": "In Progress"},
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "As a user"}],
                            },
                            {
                                "type": "paragraph",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Acceptance Criteria",
                                    }
                                ],
                            },
                            {
                                "type": "bulletList",
                                "content": [
                                    {
                                        "type": "listItem",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [
                                                    {
                                                        "type": "text",
                                                        "text": "Can import text",
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                },
            }
        ],
    )

    result = await service.get_issue_detail("ENG-123")
    payload = result.model_dump(by_alias=True, exclude_none=True)

    assert payload["issueKey"] == "ENG-123"
    assert payload["descriptionText"] == "As a user"
    assert payload["acceptanceCriteriaText"] == "Can import text"
    assert payload["recommendedImports"]["presetInstructions"].startswith(
        "ENG-123: Import Jira stories"
    )
    assert "Acceptance criteria\nCan import text" in payload["recommendedImports"][
        "stepInstructions"
    ]


async def test_issue_detail_policy_denial_happens_before_request() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(
            jira=JiraSettings(
                jira_enabled=True,
                jira_allowed_projects="ENG",
            )
        )
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.get_issue_detail("OPS-1")

    assert excinfo.value.code == "jira_policy_denied"
    assert service.calls == []


async def test_issue_detail_missing_description_returns_empty_text() -> None:
    service = _StubJiraBrowserService(
        atlassian_settings=_build_settings(),
        responses=[
            {
                "key": "ENG-123",
                "fields": {
                    "summary": "Import Jira stories",
                    "status": {"id": "3", "name": "In Progress"},
                },
            }
        ],
    )

    result = await service.get_issue_detail("ENG-123")
    payload = result.model_dump(by_alias=True, exclude_none=True)

    assert payload["descriptionText"] == ""
    assert payload["acceptanceCriteriaText"] == ""
    assert payload["recommendedImports"] == {
        "presetInstructions": "ENG-123: Import Jira stories",
        "stepInstructions": "Complete Jira story ENG-123: Import Jira stories",
    }


async def test_browser_disabled_fails_before_request() -> None:
    service = JiraBrowserService(
        atlassian_settings=_build_settings(),
        browser_enabled=False,
    )

    with pytest.raises(JiraToolError) as excinfo:
        await service.verify_connection()

    assert excinfo.value.code == "tool_not_found"
