"""MoonMind-owned Jira browser endpoints for the Create page."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.integrations.jira.browser import (
    JiraBoard,
    JiraBoardColumns,
    JiraBoardIssues,
    JiraBrowserService,
    JiraConnectionVerification,
    JiraIssueDetail,
    JiraListResponse,
    JiraProject,
)
from moonmind.integrations.jira.errors import JiraToolError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jira", tags=["jira-browser"])

_SECRET_MESSAGE_RE = re.compile(
    r"(?i)(ghp_|github_pat_|AIza|ATATT|AKIA|token=|password=|authorization:|private key)"
)
_GENERIC_ERROR_MESSAGE = "Jira browser request failed."


def _get_service() -> JiraBrowserService:
    return JiraBrowserService(
        atlassian_settings=settings.atlassian,
        feature_flags=settings.feature_flags,
    )


def _to_http_exception(exc: JiraToolError) -> HTTPException:
    logger.info(
        "jira_browser_request_failed code=%s status=%s action=%s",
        exc.code,
        exc.status_code,
        exc.action,
    )
    message = str(exc.args[0]) if exc.args else _GENERIC_ERROR_MESSAGE
    if _SECRET_MESSAGE_RE.search(message):
        message = _GENERIC_ERROR_MESSAGE
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": message,
        },
    )


@router.get(
    "/connections/verify",
    response_model=JiraConnectionVerification,
    response_model_exclude_none=True,
)
async def verify_connection(
    project_key: str | None = Query(None, alias="projectKey"),
    _user: User = Depends(get_current_user()),
    service: JiraBrowserService = Depends(_get_service),
) -> JiraConnectionVerification:
    try:
        return await service.verify_connection(project_key=project_key)
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None


@router.get(
    "/projects",
    response_model=JiraListResponse[JiraProject],
    response_model_exclude_none=True,
)
async def list_projects(
    _user: User = Depends(get_current_user()),
    service: JiraBrowserService = Depends(_get_service),
) -> JiraListResponse[JiraProject]:
    try:
        return await service.list_projects()
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None


@router.get(
    "/projects/{project_key}/boards",
    response_model=JiraListResponse[JiraBoard],
    response_model_exclude_none=True,
)
async def list_project_boards(
    project_key: str,
    _user: User = Depends(get_current_user()),
    service: JiraBrowserService = Depends(_get_service),
) -> JiraListResponse[JiraBoard]:
    try:
        return await service.list_boards(project_key.upper())
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None


@router.get(
    "/boards/{board_id}/columns",
    response_model=JiraBoardColumns,
    response_model_exclude_none=True,
)
async def list_board_columns(
    board_id: str,
    _user: User = Depends(get_current_user()),
    service: JiraBrowserService = Depends(_get_service),
) -> JiraBoardColumns:
    try:
        return await service.list_columns(board_id)
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None


@router.get(
    "/boards/{board_id}/issues",
    response_model=JiraBoardIssues,
    response_model_exclude_none=True,
)
async def list_board_issues(
    board_id: str,
    q: str | None = Query(None),
    _user: User = Depends(get_current_user()),
    service: JiraBrowserService = Depends(_get_service),
) -> JiraBoardIssues:
    try:
        return await service.list_issues(board_id, q=q)
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None


@router.get(
    "/issues/{issue_key}",
    response_model=JiraIssueDetail,
    response_model_exclude_none=True,
)
async def get_issue(
    issue_key: str,
    board_id: str | None = Query(None, alias="boardId"),
    _user: User = Depends(get_current_user()),
    service: JiraBrowserService = Depends(_get_service),
) -> JiraIssueDetail:
    try:
        return await service.get_issue(issue_key.upper(), board_id=board_id)
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None
