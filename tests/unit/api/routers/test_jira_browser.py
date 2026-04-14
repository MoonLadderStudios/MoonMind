"""Router tests for Jira browser endpoints."""

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
    JiraIssueAttachment,
    JiraIssueColumn,
    JiraIssueDetail,
    JiraIssueRecommendations,
    JiraIssueSummary,
    JiraListResponse,
    JiraProject,
)
from moonmind.integrations.jira.errors import JiraToolError

pytestmark = [pytest.mark.asyncio]

CURRENT_USER_DEP = get_current_user()


class _FakeJiraBrowserService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.raise_error: JiraToolError | None = None
        self.raise_unexpected = False
        self.attachment_filename = "wireframe.png"

    def _maybe_raise(self) -> None:
        if self.raise_unexpected:
            raise RuntimeError("unexpected Jira adapter failure")
        if self.raise_error is not None:
            raise self.raise_error

    async def verify_connection(
        self,
        project_key: str | None = None,
    ) -> JiraConnectionVerification:
        self.calls.append(("verify_connection", project_key))
        self._maybe_raise()
        return JiraConnectionVerification(ok=True, accountId="acct-1")

    async def list_projects(self) -> JiraListResponse[JiraProject]:
        self.calls.append(("list_projects", None))
        self._maybe_raise()
        return JiraListResponse(items=[JiraProject(projectKey="ENG", name="Engineering")])

    async def list_boards(self, project_key: str) -> JiraListResponse[JiraBoard]:
        self.calls.append(("list_boards", project_key))
        self._maybe_raise()
        return JiraListResponse(
            projectKey=project_key,
            items=[JiraBoard(id="42", name="Delivery", projectKey=project_key)],
        )

    async def list_columns(
        self,
        board_id: str,
        project_key: str | None = None,
    ) -> JiraBoardColumns:
        self.calls.append(("list_columns", {"board_id": board_id, "project_key": project_key}))
        self._maybe_raise()
        board = JiraBoard(id=board_id, name="Delivery", projectKey="ENG")
        return JiraBoardColumns(
            board=board,
            columns=[
                JiraColumn(id="to-do", name="To Do", order=0, count=0, statusIds=["1"])
            ],
        )

    async def list_issues(
        self,
        board_id: str,
        q: str | None = None,
        project_key: str | None = None,
    ) -> JiraBoardIssues:
        self.calls.append(
            (
                "list_issues",
                {"board_id": board_id, "q": q, "project_key": project_key},
            )
        )
        self._maybe_raise()
        column = JiraColumn(id="to-do", name="To Do", order=0, count=1, statusIds=["1"])
        item = JiraIssueSummary(
            issueKey="ENG-1",
            summary="Build browser",
            statusId="1",
            statusName="Open",
            columnId="to-do",
        )
        return JiraBoardIssues(
            boardId=board_id,
            columns=[column],
            itemsByColumn={"to-do": [item]},
        )

    async def get_issue(
        self,
        issue_key: str,
        board_id: str | None = None,
        project_key: str | None = None,
    ) -> JiraIssueDetail:
        self.calls.append(
            (
                "get_issue",
                {
                    "issue_key": issue_key,
                    "board_id": board_id,
                    "project_key": project_key,
                },
            )
        )
        self._maybe_raise()
        return JiraIssueDetail(
            issueKey=issue_key,
            summary="Build browser",
            column=(
                JiraIssueColumn(id="to-do", name="To Do")
                if board_id is not None
                else None
            ),
            descriptionText="Description",
            acceptanceCriteriaText="Given a board",
            recommendedImports=JiraIssueRecommendations(
                presetInstructions="ENG-1: Build browser",
                stepInstructions="Complete Jira story ENG-1",
            ),
        )

    async def download_issue_image_attachment(
        self,
        issue_key: str,
        attachment_id: str,
    ) -> tuple[JiraIssueAttachment, bytes, str]:
        self.calls.append(
            (
                "download_issue_image_attachment",
                {"issue_key": issue_key, "attachment_id": attachment_id},
            )
        )
        self._maybe_raise()
        return (
            JiraIssueAttachment(
                id=attachment_id,
                filename=self.attachment_filename,
                contentType="image/png",
                sizeBytes=4,
                downloadUrl=f"/api/jira/issues/{issue_key}/attachments/{attachment_id}/content",
            ),
            b"img\n",
            "image/png",
        )


@pytest.fixture
def router_app() -> tuple[FastAPI, _FakeJiraBrowserService]:
    app = FastAPI()
    service = _FakeJiraBrowserService()
    app.include_router(jira_browser_router.router)
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(id=None)
    app.dependency_overrides[jira_browser_router._get_service] = lambda: service
    return app, service


async def test_verify_connection_endpoint(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/connections/verify?projectKey=ENG")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "accountId": "acct-1"}
    assert service.calls == [("verify_connection", "ENG")]


async def test_projects_endpoint(router_app: tuple[FastAPI, _FakeJiraBrowserService]) -> None:
    app, _service = router_app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects")

    assert response.status_code == 200
    assert response.json()["items"] == [{"projectKey": "ENG", "name": "Engineering"}]


async def test_project_boards_endpoint(router_app: tuple[FastAPI, _FakeJiraBrowserService]) -> None:
    app, service = router_app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects/eng/boards")

    assert response.status_code == 200
    assert response.json()["projectKey"] == "ENG"
    assert response.json()["items"][0]["id"] == "42"
    assert service.calls == [("list_boards", "ENG")]


async def test_board_columns_endpoint(router_app: tuple[FastAPI, _FakeJiraBrowserService]) -> None:
    app, service = router_app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/boards/42/columns?projectKey=ENG")

    assert response.status_code == 200
    assert response.json()["columns"][0]["statusIds"] == ["1"]
    assert service.calls == [
        ("list_columns", {"board_id": "42", "project_key": "ENG"})
    ]


async def test_board_issues_endpoint(router_app: tuple[FastAPI, _FakeJiraBrowserService]) -> None:
    app, service = router_app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/boards/42/issues?q=browser&projectKey=ENG")

    assert response.status_code == 200
    assert response.json()["itemsByColumn"]["to-do"][0]["issueKey"] == "ENG-1"
    assert service.calls == [
        ("list_issues", {"board_id": "42", "q": "browser", "project_key": "ENG"})
    ]


async def test_issue_detail_endpoint(router_app: tuple[FastAPI, _FakeJiraBrowserService]) -> None:
    app, service = router_app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/issues/eng-1?boardId=42&projectKey=ENG")

    assert response.status_code == 200
    assert response.json()["recommendedImports"]["stepInstructions"] == "Complete Jira story ENG-1"
    assert response.json()["column"] == {"id": "to-do", "name": "To Do"}
    assert service.calls == [
        (
            "get_issue",
            {"issue_key": "ENG-1", "board_id": "42", "project_key": "ENG"},
        )
    ]


async def test_issue_attachment_download_endpoint(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/issues/eng-1/attachments/100/content")

    assert response.status_code == 200
    assert response.content == b"img\n"
    assert response.headers["content-type"] == "image/png"
    assert "wireframe.png" in response.headers["content-disposition"]
    assert service.calls == [
        (
            "download_issue_image_attachment",
            {"issue_key": "ENG-1", "attachment_id": "100"},
        )
    ]


async def test_issue_attachment_download_preserves_quoted_filename(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app
    service.attachment_filename = 'my "file".png'

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/issues/eng-1/attachments/100/content")

    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == "attachment; filename*=UTF-8''my%20%22file%22.png"
    )


async def test_router_maps_jira_errors_to_safe_details(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app
    service.raise_error = JiraToolError(
        "Project is not allowed.",
        code="jira_policy_denied",
        status_code=403,
        action="jira_browser.list_boards",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects/OPS/boards")

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail == {
        "code": "jira_policy_denied",
        "message": "Project is not allowed.",
        "source": "jira_browser",
        "action": "jira_browser.list_boards",
    }


async def test_router_sanitizes_secret_like_error_messages(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app
    service.raise_error = JiraToolError(
        "token=secret-value should not escape",
        code="jira_request_failed",
        status_code=502,
        action="jira_browser.get_issue",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/issues/ENG-1")

    detail = response.json()["detail"]
    assert response.status_code == 502
    assert detail["message"] == "Jira browser request failed."
    assert detail["source"] == "jira_browser"
    assert detail["action"] == "jira_browser.get_issue"
    assert "secret-value" not in str(detail)


async def test_router_sanitizes_bearer_token_error_messages(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app
    service.raise_error = JiraToolError(
        "upstream rejected Authorization Bearer abc123-secret",
        code="jira_request_failed",
        status_code=502,
        action="jira_browser.list_projects",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects")

    detail = response.json()["detail"]
    assert response.status_code == 502
    assert detail == {
        "code": "jira_request_failed",
        "message": "Jira browser request failed.",
        "source": "jira_browser",
        "action": "jira_browser.list_projects",
    }
    assert "abc123-secret" not in str(detail)


async def test_router_sanitizes_colon_separated_bearer_token_error_messages(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app
    service.raise_error = JiraToolError(
        "upstream rejected Bearer:abc123-secret",
        code="jira_request_failed",
        status_code=502,
        action="jira_browser.list_projects",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects")

    detail = response.json()["detail"]
    assert response.status_code == 502
    assert detail == {
        "code": "jira_request_failed",
        "message": "Jira browser request failed.",
        "source": "jira_browser",
        "action": "jira_browser.list_projects",
    }
    assert "abc123-secret" not in str(detail)


async def test_router_sanitizes_trace_like_error_messages(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app
    service.raise_error = JiraToolError(
        "Traceback (most recent call last): RuntimeError: raw provider failure",
        code="jira_request_failed",
        status_code=502,
        action="jira_browser.list_projects",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects")

    detail = response.json()["detail"]
    assert response.status_code == 502
    assert detail == {
        "code": "jira_request_failed",
        "message": "Jira browser request failed.",
        "source": "jira_browser",
        "action": "jira_browser.list_projects",
    }
    assert "Traceback" not in str(detail)


async def test_router_maps_unexpected_errors_to_structured_jira_browser_error(
    router_app: tuple[FastAPI, _FakeJiraBrowserService],
) -> None:
    app, service = router_app
    service.raise_unexpected = True

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jira/projects")

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "jira_browser_request_failed",
        "message": "Jira browser request failed.",
        "source": "jira_browser",
    }
