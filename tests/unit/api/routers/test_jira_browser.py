"""Router tests for the Jira Create-page browser API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_service.api.routers import jira_browser as jira_browser_router
from api_service.auth_providers import get_current_user
from moonmind.integrations.jira.browser import (
    JiraBoard,
    JiraBoardColumns,
    JiraBoardIssues,
    JiraColumn,
    JiraConnectionVerification,
    JiraIssueDetail,
    JiraIssueSummary,
    JiraProject,
    JiraProjectList,
    JiraProjectBoards,
    JiraRecommendedImports,
)
from moonmind.integrations.jira.errors import JiraToolError

pytestmark = [pytest.mark.asyncio]

CURRENT_USER_DEP = get_current_user()


class _FakeJiraBrowserService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.raise_error: JiraToolError | None = None

    def _maybe_raise(self) -> None:
        if self.raise_error is not None:
            raise self.raise_error

    async def verify_connection(
        self, project_key: str | None = None
    ) -> JiraConnectionVerification:
        self.calls.append(("verify_connection", project_key))
        self._maybe_raise()
        return JiraConnectionVerification(
            ok=True,
            accountId="acct-1",
            displayName="Ada",
        )

    async def list_projects(self) -> JiraProjectList:
        self.calls.append(("list_projects", None))
        self._maybe_raise()
        return JiraProjectList(
            items=[JiraProject(projectKey="ENG", name="Engineering", id="10001")]
        )

    async def list_project_boards(self, project_key: str) -> JiraProjectBoards:
        self.calls.append(("list_project_boards", project_key))
        self._maybe_raise()
        return JiraProjectBoards(
            projectKey="ENG",
            items=[
                JiraBoard(id="42", name="Delivery", projectKey="ENG", type="kanban")
            ],
        )

    async def list_board_columns(self, board_id: str) -> JiraBoardColumns:
        self.calls.append(("list_board_columns", board_id))
        self._maybe_raise()
        return JiraBoardColumns(
            board=JiraBoard(id="42", name="Delivery", projectKey="ENG"),
            columns=[
                JiraColumn(
                    id="to-do",
                    name="To Do",
                    order=0,
                    count=1,
                    statusIds=["1"],
                )
            ],
        )

    async def list_board_issues(
        self, board_id: str, query: str | None = None
    ) -> JiraBoardIssues:
        self.calls.append(
            ("list_board_issues", {"board_id": board_id, "query": query})
        )
        self._maybe_raise()
        return JiraBoardIssues(
            boardId="42",
            columns=[
                JiraColumn(
                    id="to-do",
                    name="To Do",
                    order=0,
                    count=1,
                    statusIds=["1"],
                )
            ],
            itemsByColumn={
                "to-do": [
                    JiraIssueSummary(
                        issueKey="ENG-1",
                        summary="Build browser",
                        statusId="1",
                        statusName="Backlog",
                        columnId="to-do",
                    )
                ]
            },
            unmappedItems=[],
        )

    async def get_issue_detail(
        self, issue_key: str, board_id: str | None = None
    ) -> JiraIssueDetail:
        self.calls.append(
            ("get_issue_detail", {"issue_key": issue_key, "board_id": board_id})
        )
        self._maybe_raise()
        return JiraIssueDetail(
            issueKey="ENG-1",
            summary="Build browser",
            descriptionText="Description",
            acceptanceCriteriaText="Acceptance",
            recommendedImports=JiraRecommendedImports(
                presetInstructions="ENG-1: Build browser\n\nDescription",
                stepInstructions=(
                    "Complete Jira story ENG-1: Build browser\n\n"
                    "Description\nDescription\n\nAcceptance criteria\nAcceptance"
                ),
            ),
        )


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> _FakeJiraBrowserService:
    service = _FakeJiraBrowserService()
    monkeypatch.setattr(jira_browser_router, "_jira_browser_service", service)
    return service


@pytest.fixture
def router_app(fake_service: _FakeJiraBrowserService) -> FastAPI:
    app = FastAPI()
    app.include_router(jira_browser_router.router)
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(id=None)
    return app


async def test_verify_connection_endpoint(router_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/connections/verify")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "accountId": "acct-1",
        "displayName": "Ada",
    }


async def test_project_board_column_issue_and_detail_endpoints(
    router_app: FastAPI,
    fake_service: _FakeJiraBrowserService,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        projects = await client.get("/api/jira/projects")
        boards = await client.get("/api/jira/projects/ENG/boards")
        columns = await client.get("/api/jira/boards/42/columns")
        issues = await client.get("/api/jira/boards/42/issues?q=browser")
        detail = await client.get("/api/jira/issues/ENG-1?boardId=42")

    assert projects.status_code == 200
    assert projects.json()["items"][0]["projectKey"] == "ENG"
    assert boards.status_code == 200
    assert boards.json()["items"][0]["id"] == "42"
    assert columns.status_code == 200
    assert columns.json()["columns"][0]["id"] == "to-do"
    assert issues.status_code == 200
    assert issues.json()["itemsByColumn"]["to-do"][0]["issueKey"] == "ENG-1"
    assert detail.status_code == 200
    assert detail.json()["recommendedImports"]["presetInstructions"].startswith("ENG-1")
    assert fake_service.calls[-2] == (
        "list_board_issues",
        {"board_id": "42", "query": "browser"},
    )
    assert fake_service.calls[-1] == (
        "get_issue_detail",
        {"issue_key": "ENG-1", "board_id": "42"},
    )


async def test_router_maps_jira_errors_to_safe_structured_detail(
    router_app: FastAPI,
    fake_service: _FakeJiraBrowserService,
) -> None:
    fake_service.raise_error = JiraToolError(
        "Project denied. sensitive-value-should-not-leak",
        code="jira_policy_denied",
        status_code=403,
        action="jira_browser_list_project_boards",
    )

    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects/OPS/boards")

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": "jira_policy_denied",
            "message": "Jira browser request failed.",
        }
    }


async def test_router_rejects_invalid_path_parameters(router_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=router_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/issues/not-an-issue")

    assert response.status_code == 422
