"""MoonMind-owned Jira browser endpoints for the Create page."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.integrations.jira.browser import (
    JiraBoardColumns,
    JiraBoardIssues,
    JiraBrowserService,
    JiraConnectionVerification,
    JiraIssueDetail,
    JiraProjectBoards,
    JiraProjectList,
)
from moonmind.integrations.jira.errors import JiraToolError

router = APIRouter(prefix="/api/jira", tags=["jira-browser"])

_jira_browser_service = JiraBrowserService(
    atlassian_settings=settings.atlassian,
    browser_enabled=settings.feature_flags.jira_create_page_enabled,
)
_PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+$")
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")


def _safe_jira_exception(exc: JiraToolError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": "Jira browser request failed.",
        },
    )


def _validate_project_key(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if not _PROJECT_KEY_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "jira_validation_failed",
                "message": "projectKey must match a Jira project-key pattern.",
            },
        )
    return normalized


def _validate_issue_key(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if not _ISSUE_KEY_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "jira_validation_failed",
                "message": "issueKey must match a Jira issue-key pattern.",
            },
        )
    return normalized


@router.get(
    "/connections/verify",
    response_model=JiraConnectionVerification,
    response_model_exclude_none=True,
)
async def verify_connection(
    *,
    project_key: str | None = Query(None, alias="projectKey"),
    user: User = Depends(get_current_user()),
) -> JiraConnectionVerification:
    del user
    normalized_project = _validate_project_key(project_key) if project_key else None
    try:
        return await _jira_browser_service.verify_connection(normalized_project)
    except JiraToolError as exc:
        raise _safe_jira_exception(exc) from exc


@router.get("/projects", response_model=JiraProjectList)
async def list_projects(
    *,
    user: User = Depends(get_current_user()),
) -> JiraProjectList:
    del user
    try:
        return await _jira_browser_service.list_projects()
    except JiraToolError as exc:
        raise _safe_jira_exception(exc) from exc


@router.get("/projects/{project_key}/boards", response_model=JiraProjectBoards)
async def list_project_boards(
    project_key: str,
    *,
    user: User = Depends(get_current_user()),
) -> JiraProjectBoards:
    del user
    normalized_project = _validate_project_key(project_key)
    try:
        return await _jira_browser_service.list_project_boards(normalized_project)
    except JiraToolError as exc:
        raise _safe_jira_exception(exc) from exc


@router.get("/boards/{board_id}/columns", response_model=JiraBoardColumns)
async def list_board_columns(
    board_id: str,
    *,
    user: User = Depends(get_current_user()),
) -> JiraBoardColumns:
    del user
    try:
        return await _jira_browser_service.list_board_columns(board_id)
    except JiraToolError as exc:
        raise _safe_jira_exception(exc) from exc


@router.get("/boards/{board_id}/issues", response_model=JiraBoardIssues)
async def list_board_issues(
    board_id: str,
    *,
    query: str | None = Query(None, alias="q"),
    user: User = Depends(get_current_user()),
) -> JiraBoardIssues:
    del user
    try:
        return await _jira_browser_service.list_board_issues(board_id, query=query)
    except JiraToolError as exc:
        raise _safe_jira_exception(exc) from exc


@router.get("/issues/{issue_key}", response_model=JiraIssueDetail)
async def get_issue_detail(
    issue_key: str,
    *,
    board_id: str | None = Query(None, alias="boardId"),
    user: User = Depends(get_current_user()),
) -> JiraIssueDetail:
    del user
    normalized_issue = _validate_issue_key(issue_key)
    try:
        return await _jira_browser_service.get_issue_detail(
            normalized_issue,
            board_id=board_id,
        )
    except JiraToolError as exc:
        raise _safe_jira_exception(exc) from exc
