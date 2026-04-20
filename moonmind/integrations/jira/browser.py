"""Browser-facing Jira read service for the Create page."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Generic, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.config.settings import (
    AtlassianSettings,
    FeatureFlagsSettings,
    settings,
)
from moonmind.integrations.jira.auth import resolve_jira_connection
from moonmind.integrations.jira.client import JiraClient
from moonmind.integrations.jira.errors import JiraToolError

logger = logging.getLogger(__name__)

T = TypeVar("T")

_JIRA_PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+$")
_JIRA_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")
_BOARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_JIRA_ATTACHMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_JIRA_BROWSER_PAGE_SIZE = 50
_ACCEPTANCE_HEADING_RE = re.compile(
    r"(?im)^\s*(acceptance\s+criteria|acceptance|ac)\s*:?\s*$"
)


@dataclass(frozen=True)
class _JiraProjectScope:
    keys: frozenset[str]
    ids: frozenset[str]


class JiraBrowserModel(BaseModel):
    """Base model for Jira browser responses."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class JiraConnectionVerification(JiraBrowserModel):
    ok: bool
    account_id: str | None = Field(None, alias="accountId")
    display_name: str | None = Field(None, alias="displayName")
    project_key: str | None = Field(None, alias="projectKey")
    project_name: str | None = Field(None, alias="projectName")


class JiraProject(JiraBrowserModel):
    project_key: str = Field(..., alias="projectKey")
    name: str
    id: str | None = None

    @field_validator("project_key", mode="before")
    @classmethod
    def _normalize_project_key(cls, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not _JIRA_PROJECT_KEY_RE.fullmatch(normalized):
            raise ValueError("projectKey must match a Jira project-key pattern")
        return normalized

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return str(value or "").strip()


class JiraBoard(JiraBrowserModel):
    id: str
    name: str
    project_key: str = Field(..., alias="projectKey")
    type: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("board id is required")
        return normalized

    @field_validator("project_key", mode="before")
    @classmethod
    def _normalize_project_key(cls, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized:
            return ""
        if not _JIRA_PROJECT_KEY_RE.fullmatch(normalized):
            raise ValueError("projectKey must match a Jira project-key pattern")
        return normalized


class JiraColumn(JiraBrowserModel):
    id: str
    name: str
    order: int
    count: int = 0
    status_ids: list[str] = Field(default_factory=list, alias="statusIds")


class JiraBoardColumns(JiraBrowserModel):
    board: JiraBoard
    columns: list[JiraColumn]


class JiraIssueSummary(JiraBrowserModel):
    issue_key: str = Field(..., alias="issueKey")
    summary: str
    issue_type: str | None = Field(None, alias="issueType")
    status_id: str | None = Field(None, alias="statusId")
    status_name: str | None = Field(None, alias="statusName")
    assignee: str | None = None
    updated_at: str | None = Field(None, alias="updatedAt")
    column_id: str = Field(..., alias="columnId")

    @field_validator("issue_key", mode="before")
    @classmethod
    def _normalize_issue_key(cls, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not _JIRA_ISSUE_KEY_RE.fullmatch(normalized):
            raise ValueError("issueKey must match a Jira issue-key pattern")
        return normalized


class JiraBoardIssues(JiraBrowserModel):
    board_id: str = Field(..., alias="boardId")
    columns: list[JiraColumn]
    items_by_column: dict[str, list[JiraIssueSummary]] = Field(
        default_factory=dict,
        alias="itemsByColumn",
    )
    unmapped_items: list[JiraIssueSummary] = Field(
        default_factory=list,
        alias="unmappedItems",
    )


class JiraIssueColumn(JiraBrowserModel):
    id: str
    name: str


class JiraIssueStatus(JiraBrowserModel):
    id: str | None = None
    name: str | None = None


class JiraIssueRecommendations(JiraBrowserModel):
    preset_instructions: str = Field(..., alias="presetInstructions")
    step_instructions: str = Field(..., alias="stepInstructions")


class JiraIssueAttachment(JiraBrowserModel):
    id: str
    filename: str
    content_type: str = Field(..., alias="contentType")
    size_bytes: int | None = Field(None, alias="sizeBytes")
    download_url: str = Field(..., alias="downloadUrl")


class JiraIssueDetail(JiraBrowserModel):
    issue_key: str = Field(..., alias="issueKey")
    url: str | None = None
    summary: str
    issue_type: str | None = Field(None, alias="issueType")
    column: JiraIssueColumn | None = None
    status: JiraIssueStatus | None = None
    description_text: str = Field("", alias="descriptionText")
    acceptance_criteria_text: str = Field("", alias="acceptanceCriteriaText")
    attachments: list[JiraIssueAttachment] = Field(default_factory=list)
    recommended_imports: JiraIssueRecommendations = Field(..., alias="recommendedImports")


class JiraListResponse(JiraBrowserModel, Generic[T]):
    items: list[T]
    project_key: str | None = Field(None, alias="projectKey")


class JiraBrowserService:
    """Trusted read-only Jira browser service used by Mission Control."""

    def __init__(
        self,
        *,
        atlassian_settings: AtlassianSettings | None = None,
        feature_flags: FeatureFlagsSettings | None = None,
    ) -> None:
        self._settings = atlassian_settings or settings.atlassian
        self._feature_flags = feature_flags or settings.feature_flags

    async def verify_connection(
        self,
        project_key: str | None = None,
    ) -> JiraConnectionVerification:
        self._ensure_enabled()
        normalized_project = self._normalize_project_key_optional(project_key)
        if normalized_project:
            self._ensure_project_allowed(normalized_project)
            project = await self._request_json(
                method="GET",
                path=f"/project/{normalized_project}",
                action="jira_browser.verify_connection",
                context={"projectKey": normalized_project},
            )
            return JiraConnectionVerification(
                ok=True,
                projectKey=normalized_project,
                projectName=str(project.get("name") or normalized_project),
            )

        profile = await self._request_json(
            method="GET",
            path="/myself",
            action="jira_browser.verify_connection",
            context={},
        )
        return JiraConnectionVerification(
            ok=True,
            accountId=profile.get("accountId"),
            displayName=profile.get("displayName"),
        )

    async def list_projects(self) -> JiraListResponse[JiraProject]:
        self._ensure_enabled()
        allowed = sorted(self._allowed_projects())
        if allowed:
            async with self._jira_client() as client:
                results = await asyncio.gather(
                    *(
                        self._request_json_with_client(
                            client,
                            method="GET",
                            path=f"/project/{project_key}",
                            action="jira_browser.list_projects",
                            context={"projectKey": project_key},
                        )
                        for project_key in allowed
                    ),
                    return_exceptions=True,
                )
            projects = []
            for project_key, payload in zip(allowed, results, strict=True):
                if isinstance(payload, Exception):
                    logger.info(
                        "jira_browser_project_fetch_failed project_key=%s error_type=%s",
                        project_key,
                        type(payload).__name__,
                    )
                    continue
                projects.append(
                    self._normalize_project(
                        payload,
                        fallback_key=project_key,
                        policy_project_key=project_key,
                    )
                )
            return JiraListResponse[JiraProject](items=projects)

        payload = await self._request_json(
            method="GET",
            path="/project/search",
            action="jira_browser.list_projects",
            params={"maxResults": 50},
            context={},
        )
        if isinstance(payload, Mapping):
            values = payload.get("values", [])
        elif isinstance(payload, list):
            values = payload
        else:
            values = []
        return JiraListResponse[JiraProject](
            items=[self._normalize_project(item) for item in values]
        )

    async def list_boards(self, project_key: str) -> JiraListResponse[JiraBoard]:
        self._ensure_enabled()
        normalized_project = self._normalize_project_key(project_key)
        self._ensure_project_allowed(normalized_project)
        payload = await self._request_json(
            method="GET",
            path="agile:/board",
            action="jira_browser.list_boards",
            params={"projectKeyOrId": normalized_project, "maxResults": 50},
            context={"projectKey": normalized_project},
        )
        boards = [
            self._normalize_board(item, fallback_project_key=normalized_project)
            for item in payload.get("values", [])
        ]
        return JiraListResponse[JiraBoard](projectKey=normalized_project, items=boards)

    async def list_columns(
        self,
        board_id: str,
        project_key: str | None = None,
    ) -> JiraBoardColumns:
        self._ensure_enabled()
        normalized_board_id = self._normalize_board_id(board_id)
        normalized_project = self._normalize_project_key_optional(project_key)
        async with self._jira_client() as client:
            board = await self._fetch_board(
                normalized_board_id,
                client=client,
                policy_project_key=normalized_project,
            )
            config = await self._fetch_board_configuration(
                normalized_board_id,
                client=client,
                policy_project_key=normalized_project,
            )
        columns = self._normalize_columns(config)
        return JiraBoardColumns(board=board, columns=columns)

    async def list_issues(
        self,
        board_id: str,
        q: str | None = None,
        project_key: str | None = None,
    ) -> JiraBoardIssues:
        self._ensure_enabled()
        normalized_board_id = self._normalize_board_id(board_id)
        normalized_project = self._normalize_project_key_optional(project_key)
        async with self._jira_client() as client:
            columns_response = await self._list_columns_with_client(
                normalized_board_id,
                client=client,
                policy_project_key=normalized_project,
            )
            project_scope = (
                await self._fetch_project_scope(normalized_project, client=client)
                if normalized_project is not None
                else None
            )
            issues_payload = await self._fetch_board_issues(
                normalized_board_id,
                client=client,
            )
        columns = columns_response.columns
        status_to_column = {
            status_id: column.id
            for column in columns
            for status_id in column.status_ids
        }
        items_by_column: dict[str, list[JiraIssueSummary]] = {
            column.id: [] for column in columns
        }
        unmapped_items: list[JiraIssueSummary] = []
        filter_text = str(q or "").strip().lower()
        for raw_issue in issues_payload:
            if not self._issue_matches_policy_scope(
                raw_issue,
                normalized_project,
                project_scope=project_scope,
            ):
                continue
            summary = self._normalize_issue_summary(raw_issue, status_to_column)
            if (
                filter_text
                and filter_text not in summary.issue_key.lower()
                and filter_text not in summary.summary.lower()
            ):
                continue
            if summary.column_id == "__unmapped":
                unmapped_items.append(summary)
            else:
                items_by_column.setdefault(summary.column_id, []).append(summary)

        counted_columns = [
            column.model_copy(update={"count": len(items_by_column.get(column.id, []))})
            for column in columns
        ]
        return JiraBoardIssues(
            boardId=normalized_board_id,
            columns=counted_columns,
            itemsByColumn=items_by_column,
            unmappedItems=unmapped_items,
        )

    async def get_issue(
        self,
        issue_key: str,
        board_id: str | None = None,
        project_key: str | None = None,
    ) -> JiraIssueDetail:
        self._ensure_enabled()
        normalized_issue_key = self._normalize_issue_key(issue_key)
        normalized_project = self._normalize_project_key_optional(project_key)
        if normalized_project is not None:
            self._ensure_project_allowed(normalized_project)
        else:
            self._ensure_project_allowed(
                self._project_from_issue_key(normalized_issue_key)
            )
        normalized_board_id = (
            self._normalize_board_id(board_id) if board_id is not None else None
        )
        board_columns: list[JiraColumn] = []
        async with self._jira_client() as client:
            project_scope = (
                await self._fetch_project_scope(normalized_project, client=client)
                if normalized_project is not None
                else None
            )
            payload = await self._request_json_with_client(
                client,
                method="GET",
                path=f"/issue/{normalized_issue_key}",
                action="jira_browser.get_issue",
                params={
                    "fields": "*all",
                    "expand": "names",
                },
                context={"issueKey": normalized_issue_key},
            )
            self._ensure_issue_allowed_by_policy(payload, project_scope=project_scope)
            if normalized_board_id is not None:
                board_columns = (
                    await self._list_columns_with_client(
                        normalized_board_id,
                        client=client,
                        policy_project_key=normalized_project,
                    )
                ).columns
        return self._normalize_issue_detail(payload, board_columns=board_columns)

    async def download_issue_image_attachment(
        self,
        issue_key: str,
        attachment_id: str,
    ) -> tuple[JiraIssueAttachment, bytes, str]:
        self._ensure_enabled()
        normalized_issue_key = self._normalize_issue_key(issue_key)
        normalized_attachment_id = self._normalize_attachment_id(attachment_id)
        self._ensure_project_allowed(self._project_from_issue_key(normalized_issue_key))
        payload = await self._request_json(
            method="GET",
            path=f"/issue/{normalized_issue_key}",
            action="jira_browser.get_issue_attachment",
            params={"fields": "attachment"},
            context={"issueKey": normalized_issue_key},
        )
        fields = payload.get("fields") if isinstance(payload, Mapping) else {}
        if not isinstance(fields, Mapping):
            fields = {}
        attachment, content_url = self._find_issue_image_attachment(
            fields,
            issue_key=normalized_issue_key,
            attachment_id=normalized_attachment_id,
        )
        if attachment is None or not content_url:
            raise JiraToolError(
                "Jira could not find the requested image attachment on this issue.",
                code="jira_not_found",
                status_code=404,
                action="jira_browser.download_attachment",
            )

        download_path = self._safe_attachment_download_path(content_url)
        payload_bytes, response_content_type = await self._request_bytes(
            method="GET",
            path=download_path,
            action="jira_browser.download_attachment",
            context={
                "issueKey": normalized_issue_key,
                "attachmentId": normalized_attachment_id,
            },
        )
        content_type = response_content_type or attachment.content_type
        if not self._is_image_content_type(content_type):
            raise JiraToolError(
                "Jira attachment is not an image.",
                code="jira_validation_failed",
                status_code=422,
                action="jira_browser.download_attachment",
            )
        return attachment, payload_bytes, content_type

    @asynccontextmanager
    async def _jira_client(self) -> AsyncIterator[JiraClient]:
        connection = await resolve_jira_connection(self._settings)
        client = JiraClient(connection=connection)
        try:
            yield client
        finally:
            await client.aclose()

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
        async with self._jira_client() as client:
            return await self._request_json_with_client(
                client,
                method=method,
                path=path,
                action=action,
                params=params,
                json_body=json_body,
                context=context,
            )

    async def _request_json_with_client(
        self,
        client: JiraClient,
        *,
        method: str,
        path: str,
        action: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        return await client.request_json(
            method=method,
            path=path,
            action=action,
            params=params,
            json_body=json_body,
            context=context,
        )

    async def _request_bytes(
        self,
        *,
        method: str,
        path: str,
        action: str,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        async with self._jira_client() as client:
            return await client.request_bytes(
                method=method,
                path=path,
                action=action,
                params=params,
                context=context,
            )

    async def _list_columns_with_client(
        self,
        board_id: str,
        *,
        client: JiraClient,
        policy_project_key: str | None = None,
    ) -> JiraBoardColumns:
        board = await self._fetch_board(
            board_id,
            client=client,
            policy_project_key=policy_project_key,
        )
        config = await self._fetch_board_configuration(
            board_id,
            client=client,
            policy_project_key=policy_project_key,
        )
        columns = self._normalize_columns(config)
        return JiraBoardColumns(board=board, columns=columns)

    async def _fetch_board(
        self,
        board_id: str,
        *,
        client: JiraClient,
        policy_project_key: str | None = None,
    ) -> JiraBoard:
        payload = await self._request_json_with_client(
            client,
            method="GET",
            path=f"agile:/board/{board_id}",
            action="jira_browser.get_board",
            context={"boardId": board_id},
        )
        board = self._normalize_board(payload)
        if policy_project_key is not None:
            self._ensure_project_allowed(policy_project_key)
            await self._ensure_board_listed_for_project(
                board_id,
                policy_project_key,
                client=client,
            )
            return board.model_copy(update={"project_key": policy_project_key})
        if board.project_key:
            self._ensure_project_allowed(board.project_key)
        return board

    async def _fetch_board_configuration(
        self,
        board_id: str,
        *,
        client: JiraClient,
        policy_project_key: str | None = None,
    ) -> Mapping[str, Any]:
        payload = await self._request_json_with_client(
            client,
            method="GET",
            path=f"agile:/board/{board_id}/configuration",
            action="jira_browser.list_columns",
            context={"boardId": board_id},
        )
        if not isinstance(payload, Mapping):
            return {}
        location = payload.get("location")
        if isinstance(location, Mapping):
            project_key = str(location.get("projectKey") or "").strip().upper()
            if policy_project_key is not None:
                self._ensure_project_allowed(policy_project_key)
            elif project_key:
                self._ensure_project_allowed(project_key)
        return payload

    async def _ensure_board_listed_for_project(
        self,
        board_id: str,
        project_key: str,
        *,
        client: JiraClient,
    ) -> None:
        start_at = 0
        while True:
            payload = await self._request_json_with_client(
                client,
                method="GET",
                path="agile:/board",
                action="jira_browser.list_boards",
                params={
                    "projectKeyOrId": project_key,
                    "maxResults": _JIRA_BROWSER_PAGE_SIZE,
                    "startAt": start_at,
                },
                context={"projectKey": project_key},
            )
            values = payload.get("values", []) if isinstance(payload, Mapping) else []
            page_items = values if isinstance(values, list) else []
            if any(
                str(item.get("id") or "").strip() == board_id
                for item in page_items
                if isinstance(item, Mapping)
            ):
                return

            total = payload.get("total") if isinstance(payload, Mapping) else None
            returned = len(page_items)
            start_at += returned
            if returned < _JIRA_BROWSER_PAGE_SIZE:
                break
            if isinstance(total, int) and start_at >= total:
                break
        raise JiraToolError(
            "Project is not allowed by Jira policy.",
            code="jira_policy_denied",
            status_code=403,
            action="jira_browser.policy",
        )

    async def _fetch_board_issues(
        self,
        board_id: str,
        *,
        client: JiraClient,
    ) -> list[Mapping[str, Any]]:
        issues: list[Mapping[str, Any]] = []
        jql = self._board_issue_scope_jql()
        start_at = 0
        while True:
            payload = await self._request_json_with_client(
                client,
                method="GET",
                path=f"agile:/board/{board_id}/issue",
                action="jira_browser.list_issues",
                params={
                    "fields": "summary,issuetype,status,assignee,updated,project",
                    "jql": jql,
                    "maxResults": _JIRA_BROWSER_PAGE_SIZE,
                    "startAt": start_at,
                },
                context={"boardId": board_id},
            )
            raw_page_items = payload.get("issues", []) if isinstance(payload, Mapping) else []
            page_items = raw_page_items if isinstance(raw_page_items, list) else []
            issues.extend(item for item in page_items if isinstance(item, Mapping))

            total = payload.get("total") if isinstance(payload, Mapping) else None
            returned = len(page_items)
            start_at += returned
            if returned < _JIRA_BROWSER_PAGE_SIZE:
                break
            if isinstance(total, int) and start_at >= total:
                break
        return issues

    def _board_issue_scope_jql(self) -> str:
        recent_done_days = self._feature_flags.jira_create_page_recent_done_days
        if recent_done_days <= 0:
            return "statusCategory != Done"
        return f"(statusCategory != Done OR updated >= -{recent_done_days}d)"

    def _ensure_enabled(self) -> None:
        if not self._feature_flags.jira_create_page_enabled:
            raise JiraToolError(
                "Jira Create-page browser is not enabled.",
                code="tool_not_found",
                status_code=404,
            )

    def _allowed_projects(self) -> set[str]:
        raw = str(self._settings.jira.jira_allowed_projects or "").strip()
        if not raw:
            return set()
        return {item.strip().upper() for item in raw.split(",") if item.strip()}

    def _ensure_project_allowed(self, project_key: str) -> None:
        allowed = self._allowed_projects()
        if allowed and project_key.upper() not in allowed:
            raise JiraToolError(
                "Project is not allowed by Jira policy.",
                code="jira_policy_denied",
                status_code=403,
                action="jira_browser.policy",
            )

    async def _fetch_project_scope(
        self,
        project_key: str,
        *,
        client: JiraClient,
    ) -> _JiraProjectScope:
        self._ensure_project_allowed(project_key)
        payload = await self._request_json_with_client(
            client,
            method="GET",
            path=f"/project/{project_key}",
            action="jira_browser.get_project_scope",
            context={"projectKey": project_key},
        )
        keys = {project_key.upper()}
        ids: set[str] = set()
        if isinstance(payload, Mapping):
            payload_key = str(payload.get("key") or "").strip().upper()
            payload_id = str(payload.get("id") or "").strip()
            if payload_key:
                keys.add(payload_key)
            if payload_id:
                ids.add(payload_id)
        return _JiraProjectScope(keys=frozenset(keys), ids=frozenset(ids))

    def _issue_matches_policy_scope(
        self,
        payload: Mapping[str, Any],
        policy_project_key: str | None,
        *,
        project_scope: _JiraProjectScope | None = None,
    ) -> bool:
        if policy_project_key is not None:
            scope = project_scope or _JiraProjectScope(
                keys=frozenset({policy_project_key.upper()}),
                ids=frozenset(),
            )
            return self._issue_matches_project_scope(payload, scope)
        allowed = self._allowed_projects()
        if not allowed:
            return True
        issue_keys, _issue_ids = self._issue_project_identifiers(payload)
        return bool(issue_keys & allowed)

    def _ensure_issue_allowed_by_policy(
        self,
        payload: Mapping[str, Any],
        *,
        project_scope: _JiraProjectScope | None = None,
    ) -> None:
        if project_scope is not None:
            if self._issue_matches_project_scope(payload, project_scope):
                return
            raise JiraToolError(
                "Project is not allowed by Jira policy.",
                code="jira_policy_denied",
                status_code=403,
                action="jira_browser.policy",
            )

        allowed = self._allowed_projects()
        if not allowed:
            return
        issue_keys, _issue_ids = self._issue_project_identifiers(payload)
        if issue_keys & allowed:
            return
        raise JiraToolError(
            "Project is not allowed by Jira policy.",
            code="jira_policy_denied",
            status_code=403,
            action="jira_browser.policy",
        )

    def _issue_matches_project_scope(
        self,
        payload: Mapping[str, Any],
        project_scope: _JiraProjectScope,
    ) -> bool:
        issue_keys, issue_ids = self._issue_project_identifiers(payload)
        return bool(
            (issue_keys & project_scope.keys) or (issue_ids & project_scope.ids)
        )

    def _issue_project_identifiers(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[set[str], set[str]]:
        fields = (
            payload.get("fields") if isinstance(payload.get("fields"), Mapping) else {}
        )
        project = (
            fields.get("project") if isinstance(fields.get("project"), Mapping) else {}
        )
        keys: set[str] = set()
        ids: set[str] = set()
        project_key = str(project.get("key") or "").strip().upper()
        project_id = str(project.get("id") or "").strip()
        if project_key:
            keys.add(project_key)
        if project_id:
            ids.add(project_id)
        issue_key = str(payload.get("key") or "").strip().upper()
        if "-" in issue_key:
            keys.add(self._project_from_issue_key(issue_key))
        return keys, ids

    def _normalize_project(
        self,
        payload: Mapping[str, Any],
        *,
        fallback_key: str = "",
        policy_project_key: str = "",
    ) -> JiraProject:
        project_key = str(
            policy_project_key or payload.get("key") or fallback_key
        ).strip().upper()
        name = str(payload.get("name") or project_key).strip()
        return JiraProject(
            projectKey=project_key,
            name=name,
            id=str(payload.get("id")) if payload.get("id") is not None else None,
        )

    def _normalize_board(
        self,
        payload: Mapping[str, Any],
        *,
        fallback_project_key: str = "",
    ) -> JiraBoard:
        location = payload.get("location")
        location_project = ""
        if isinstance(location, Mapping):
            location_project = str(location.get("projectKey") or "").strip().upper()
        project_key = location_project or fallback_project_key
        return JiraBoard(
            id=str(payload.get("id") or "").strip(),
            name=str(payload.get("name") or "").strip(),
            projectKey=project_key,
            type=str(payload.get("type") or "").strip() or None,
        )

    def _normalize_columns(self, payload: Mapping[str, Any]) -> list[JiraColumn]:
        column_config = payload.get("columnConfig")
        raw_columns = []
        if isinstance(column_config, Mapping):
            raw_columns = list(column_config.get("columns") or [])
        columns: list[JiraColumn] = []
        used_ids: set[str] = set()
        for index, item in enumerate(raw_columns):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or f"Column {index + 1}").strip()
            column_id = self._unique_column_id(self._slugify(name), used_ids)
            statuses = item.get("statuses") or []
            status_ids = [
                str(status.get("id")).strip()
                for status in statuses
                if isinstance(status, Mapping) and str(status.get("id") or "").strip()
            ]
            columns.append(
                JiraColumn(
                    id=column_id,
                    name=name,
                    order=index,
                    count=0,
                    statusIds=status_ids,
                )
            )
        return columns

    def _normalize_issue_summary(
        self,
        payload: Mapping[str, Any],
        status_to_column: Mapping[str, str],
    ) -> JiraIssueSummary:
        fields = payload.get("fields") if isinstance(payload.get("fields"), Mapping) else {}
        status = fields.get("status") if isinstance(fields.get("status"), Mapping) else {}
        status_id = str(status.get("id") or "").strip() or None
        column_id = status_to_column.get(status_id or "", "__unmapped")
        issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), Mapping) else {}
        assignee = fields.get("assignee") if isinstance(fields.get("assignee"), Mapping) else {}
        return JiraIssueSummary(
            issueKey=str(payload.get("key") or "").strip().upper(),
            summary=str(fields.get("summary") or "").strip(),
            issueType=str(issue_type.get("name") or "").strip() or None,
            statusId=status_id,
            statusName=str(status.get("name") or "").strip() or None,
            assignee=str(assignee.get("displayName") or "").strip() or None,
            updatedAt=str(fields.get("updated") or "").strip() or None,
            columnId=column_id,
        )

    def _normalize_issue_detail(
        self,
        payload: Mapping[str, Any],
        *,
        board_columns: list[JiraColumn],
    ) -> JiraIssueDetail:
        fields = payload.get("fields") if isinstance(payload.get("fields"), Mapping) else {}
        names = payload.get("names") if isinstance(payload.get("names"), Mapping) else {}
        summary = str(fields.get("summary") or "").strip()
        description_text = _normalize_jira_text(fields.get("description"))
        acceptance_text = self._extract_acceptance_criteria(fields, names)
        if not acceptance_text:
            description_text, acceptance_text = self._split_description_acceptance(description_text)
        issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), Mapping) else {}
        status = fields.get("status") if isinstance(fields.get("status"), Mapping) else {}
        issue_key = str(payload.get("key") or "").strip().upper()
        status_id = str(status.get("id") or "").strip() or None
        column = self._column_for_status(status_id, board_columns)
        recommended = self._build_recommendations(
            issue_key=issue_key,
            summary=summary,
            description_text=description_text,
            acceptance_text=acceptance_text,
        )
        url = self._issue_url(payload)
        attachments = self._normalize_issue_attachments(fields, issue_key=issue_key)
        return JiraIssueDetail(
            issueKey=issue_key,
            url=url,
            summary=summary,
            issueType=str(issue_type.get("name") or "").strip() or None,
            column=column,
            status=JiraIssueStatus(
                id=status_id,
                name=str(status.get("name") or "").strip() or None,
            ),
            descriptionText=description_text,
            acceptanceCriteriaText=acceptance_text,
            attachments=attachments,
            recommendedImports=recommended,
        )

    def _column_for_status(
        self,
        status_id: str | None,
        board_columns: list[JiraColumn],
    ) -> JiraIssueColumn | None:
        if not status_id:
            return None
        for column in board_columns:
            if status_id in column.status_ids:
                return JiraIssueColumn(id=column.id, name=column.name)
        return None

    def _extract_acceptance_criteria(
        self,
        fields: Mapping[str, Any],
        names: Mapping[str, Any],
    ) -> str:
        for field_key, field_name in names.items():
            normalized_name = str(field_name or "").lower()
            if "acceptance" not in normalized_name and "criteria" not in normalized_name:
                continue
            value = fields.get(field_key)
            text = _normalize_jira_text(value)
            if text:
                return text
        return ""

    def _split_description_acceptance(self, description_text: str) -> tuple[str, str]:
        match = _ACCEPTANCE_HEADING_RE.search(description_text)
        if match is None:
            return description_text, ""
        before = description_text[: match.start()].strip()
        after = description_text[match.end() :].strip()
        return before, after

    def _build_recommendations(
        self,
        *,
        issue_key: str,
        summary: str,
        description_text: str,
        acceptance_text: str,
    ) -> JiraIssueRecommendations:
        preset_parts = [f"{issue_key}: {summary}".strip()]
        if description_text:
            preset_parts.append(description_text)
        step_parts = [f"Complete Jira issue {issue_key}: {summary}".strip()]
        if description_text:
            step_parts.append(f"Description\n{description_text}")
        if acceptance_text:
            step_parts.append(f"Acceptance criteria\n{acceptance_text}")
        return JiraIssueRecommendations(
            presetInstructions="\n\n".join(part for part in preset_parts if part),
            stepInstructions="\n\n".join(part for part in step_parts if part),
        )

    def _issue_url(self, payload: Mapping[str, Any]) -> str | None:
        browse = str(payload.get("browseUrl") or payload.get("url") or "").strip()
        if browse:
            return browse
        self_url = str(payload.get("self") or "")
        marker = "/rest/api/"
        if marker in self_url:
            return f"{self_url.split(marker, 1)[0]}/browse/{payload.get('key')}"
        return None

    def _normalize_issue_attachments(
        self,
        fields: Mapping[str, Any],
        *,
        issue_key: str,
    ) -> list[JiraIssueAttachment]:
        raw_attachments = fields.get("attachment")
        if not isinstance(raw_attachments, list):
            return []
        attachments: list[JiraIssueAttachment] = []
        for item in raw_attachments:
            if not isinstance(item, Mapping):
                continue
            attachment = self._normalize_issue_attachment(
                item,
                issue_key=issue_key,
            )
            if attachment is not None:
                attachments.append(attachment)
        return attachments

    def _find_issue_image_attachment(
        self,
        fields: Mapping[str, Any],
        *,
        issue_key: str,
        attachment_id: str,
    ) -> tuple[JiraIssueAttachment | None, str | None]:
        raw_attachments = fields.get("attachment")
        if not isinstance(raw_attachments, list):
            return None, None
        for item in raw_attachments:
            if not isinstance(item, Mapping):
                continue
            normalized_id = str(item.get("id") or "").strip()
            if normalized_id != attachment_id:
                continue
            attachment = self._normalize_issue_attachment(
                item,
                issue_key=issue_key,
            )
            return attachment, str(item.get("content") or "").strip() or None
        return None, None

    def _normalize_issue_attachment(
        self,
        item: Mapping[str, Any],
        *,
        issue_key: str,
    ) -> JiraIssueAttachment | None:
        attachment_id = str(item.get("id") or "").strip()
        filename = str(item.get("filename") or "").strip()
        content_type = (
            str(item.get("mimeType") or item.get("contentType") or "").strip().lower()
        )
        content_url = str(item.get("content") or "").strip()
        if (
            not attachment_id
            or not filename
            or not content_url
            or not self._is_image_content_type(content_type)
        ):
            return None
        size_value = item.get("size")
        try:
            size_bytes = int(size_value) if size_value is not None else None
        except (TypeError, ValueError):
            size_bytes = None
        return JiraIssueAttachment(
            id=attachment_id,
            filename=filename,
            contentType=content_type,
            sizeBytes=size_bytes,
            downloadUrl=f"/api/jira/issues/{issue_key}/attachments/{attachment_id}/content",
        )

    def _safe_attachment_download_path(self, download_url: str) -> str:
        parsed = urlparse(download_url)
        if not parsed.scheme and not parsed.netloc:
            path = parsed.path or download_url
            if parsed.query:
                path = f"{path}?{parsed.query}"
            return path
        allowed_origins = set()
        for raw_base in (
            self._settings.atlassian_site_url,
            self._settings.atlassian_url,
        ):
            base = urlparse(str(raw_base or ""))
            if base.scheme and base.netloc:
                allowed_origins.add((base.scheme, base.netloc))
        if self._settings.atlassian_cloud_id:
            allowed_origins.add(("https", "api.atlassian.com"))
            if parsed.scheme == "https" and self._is_atlassian_tenant_host(
                parsed.netloc
            ):
                allowed_origins.add((parsed.scheme, parsed.netloc))
        if not allowed_origins or (parsed.scheme, parsed.netloc) not in allowed_origins:
            raise JiraToolError(
                "Jira attachment download URL is outside the configured Jira origin.",
                code="jira_validation_failed",
                status_code=422,
                action="jira_browser.download_attachment",
            )
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path

    def _is_atlassian_tenant_host(self, host: str) -> bool:
        normalized = str(host or "").strip().lower()
        return normalized == "atlassian.net" or normalized.endswith(".atlassian.net")

    def _is_image_content_type(self, value: str) -> bool:
        return str(value or "").strip().lower().startswith("image/")

    def _normalize_project_key(self, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not _JIRA_PROJECT_KEY_RE.fullmatch(normalized):
            raise JiraToolError(
                "projectKey must match a Jira project-key pattern.",
                code="jira_validation_failed",
                status_code=422,
                action="jira_browser.validation",
            )
        return normalized

    def _normalize_project_key_optional(self, value: object) -> str | None:
        if value in (None, ""):
            return None
        return self._normalize_project_key(value)

    def _normalize_issue_key(self, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not _JIRA_ISSUE_KEY_RE.fullmatch(normalized):
            raise JiraToolError(
                "issueKey must match a Jira issue-key pattern.",
                code="jira_validation_failed",
                status_code=422,
                action="jira_browser.validation",
            )
        return normalized

    def _normalize_board_id(self, value: object) -> str:
        normalized = str(value or "").strip()
        if not _BOARD_ID_RE.fullmatch(normalized):
            raise JiraToolError(
                "boardId must be a non-empty Jira board identifier.",
                code="jira_validation_failed",
                status_code=422,
                action="jira_browser.validation",
            )
        return normalized

    def _normalize_attachment_id(self, value: object) -> str:
        normalized = str(value or "").strip()
        if not _JIRA_ATTACHMENT_ID_RE.fullmatch(normalized):
            raise JiraToolError(
                "attachmentId must be a non-empty Jira attachment identifier.",
                code="jira_validation_failed",
                status_code=422,
                action="jira_browser.validation",
            )
        return normalized

    def _project_from_issue_key(self, issue_key: str) -> str:
        return issue_key.split("-", 1)[0]

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "column"

    def _unique_column_id(self, column_id: str, used_ids: set[str]) -> str:
        candidate = column_id
        suffix = 2
        while candidate in used_ids:
            candidate = f"{column_id}-{suffix}"
            suffix += 1
        used_ids.add(candidate)
        return candidate


def _normalize_jira_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _collapse_text(value)
    if isinstance(value, Mapping):
        parts = _collect_adf_text(value)
        return _collapse_text("\n".join(parts))
    if isinstance(value, list):
        parts = [_normalize_jira_text(item) for item in value]
        return _collapse_text("\n".join(part for part in parts if part))
    return _collapse_text(str(value))


def _collect_adf_text(node: Any) -> list[str]:
    if not isinstance(node, Mapping):
        return []
    node_type = node.get("type")
    if node_type == "text":
        return [str(node.get("text") or "")]
    if node_type == "hardBreak":
        return ["\n"]
    content = node.get("content")
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    inline = node_type in {"paragraph", "heading", "listItem"}
    for child in content:
        child_parts = _collect_adf_text(child)
        if inline:
            parts.extend(child_parts)
        else:
            text = _collapse_text("".join(child_parts))
            if text:
                parts.append(text)
    if inline:
        return [_collapse_text("".join(parts))]
    return parts


def _collapse_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank and collapsed:
                collapsed.append("")
            previous_blank = True
            continue
        collapsed.append(line)
        previous_blank = False
    return "\n".join(collapsed).strip()
