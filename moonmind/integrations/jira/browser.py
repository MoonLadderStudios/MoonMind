"""Browser-facing Jira read service for the Create page."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.config.settings import (
    AtlassianSettings,
    FeatureFlagsSettings,
    settings,
)
from moonmind.integrations.jira.auth import resolve_jira_connection
from moonmind.integrations.jira.client import JiraClient
from moonmind.integrations.jira.errors import JiraToolError

T = TypeVar("T")

_JIRA_PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+$")
_JIRA_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")
_BOARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_ACCEPTANCE_HEADING_RE = re.compile(
    r"(?im)^\s*(acceptance\s+criteria|acceptance|ac)\s*:?\s*$"
)


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


class JiraIssueDetail(JiraBrowserModel):
    issue_key: str = Field(..., alias="issueKey")
    url: str | None = None
    summary: str
    issue_type: str | None = Field(None, alias="issueType")
    column: JiraIssueColumn | None = None
    status: JiraIssueStatus | None = None
    description_text: str = Field("", alias="descriptionText")
    acceptance_criteria_text: str = Field("", alias="acceptanceCriteriaText")
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
            projects = []
            for project_key in allowed:
                payload = await self._request_json(
                    method="GET",
                    path=f"/project/{project_key}",
                    action="jira_browser.list_projects",
                    context={"projectKey": project_key},
                )
                projects.append(self._normalize_project(payload, fallback_key=project_key))
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
            path="/rest/agile/1.0/board",
            action="jira_browser.list_boards",
            params={"projectKeyOrId": normalized_project, "maxResults": 50},
            context={"projectKey": normalized_project},
        )
        boards = [
            self._normalize_board(item, fallback_project_key=normalized_project)
            for item in payload.get("values", [])
        ]
        return JiraListResponse[JiraBoard](projectKey=normalized_project, items=boards)

    async def list_columns(self, board_id: str) -> JiraBoardColumns:
        self._ensure_enabled()
        normalized_board_id = self._normalize_board_id(board_id)
        board = await self._fetch_board(normalized_board_id)
        config = await self._fetch_board_configuration(normalized_board_id)
        columns = self._normalize_columns(config)
        return JiraBoardColumns(board=board, columns=columns)

    async def list_issues(
        self,
        board_id: str,
        q: str | None = None,
    ) -> JiraBoardIssues:
        self._ensure_enabled()
        normalized_board_id = self._normalize_board_id(board_id)
        columns_response = await self.list_columns(normalized_board_id)
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
        payload = await self._request_json(
            method="GET",
            path=f"/rest/agile/1.0/board/{normalized_board_id}/issue",
            action="jira_browser.list_issues",
            params={
                "fields": "summary,issuetype,status,assignee,updated",
                "maxResults": 50,
            },
            context={"boardId": normalized_board_id},
        )
        filter_text = str(q or "").strip().lower()
        for raw_issue in payload.get("issues", []):
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
    ) -> JiraIssueDetail:
        self._ensure_enabled()
        normalized_issue_key = self._normalize_issue_key(issue_key)
        self._ensure_project_allowed(self._project_from_issue_key(normalized_issue_key))
        payload = await self._request_json(
            method="GET",
            path=f"/issue/{normalized_issue_key}",
            action="jira_browser.get_issue",
            params={
                "fields": "*all",
                "expand": "names",
            },
            context={"issueKey": normalized_issue_key},
        )
        return self._normalize_issue_detail(payload, board_id=board_id)

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
        connection = await resolve_jira_connection(self._settings)
        client = JiraClient(connection=connection)
        try:
            return await client.request_json(
                method=method,
                path=path,
                action=action,
                params=params,
                json_body=json_body,
                context=context,
            )
        finally:
            await client.aclose()

    async def _fetch_board(self, board_id: str) -> JiraBoard:
        payload = await self._request_json(
            method="GET",
            path=f"/rest/agile/1.0/board/{board_id}",
            action="jira_browser.get_board",
            context={"boardId": board_id},
        )
        board = self._normalize_board(payload)
        if board.project_key:
            self._ensure_project_allowed(board.project_key)
        return board

    async def _fetch_board_configuration(self, board_id: str) -> Mapping[str, Any]:
        payload = await self._request_json(
            method="GET",
            path=f"/rest/agile/1.0/board/{board_id}/configuration",
            action="jira_browser.list_columns",
            context={"boardId": board_id},
        )
        if not isinstance(payload, Mapping):
            return {}
        location = payload.get("location")
        if isinstance(location, Mapping):
            project_key = str(location.get("projectKey") or "").strip().upper()
            if project_key:
                self._ensure_project_allowed(project_key)
        return payload

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

    def _normalize_project(
        self,
        payload: Mapping[str, Any],
        *,
        fallback_key: str = "",
    ) -> JiraProject:
        project_key = str(payload.get("key") or fallback_key).strip().upper()
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
        board_id: str | None,
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
        recommended = self._build_recommendations(
            issue_key=issue_key,
            summary=summary,
            description_text=description_text,
            acceptance_text=acceptance_text,
        )
        url = self._issue_url(payload)
        return JiraIssueDetail(
            issueKey=issue_key,
            url=url,
            summary=summary,
            issueType=str(issue_type.get("name") or "").strip() or None,
            status=JiraIssueStatus(
                id=str(status.get("id") or "").strip() or None,
                name=str(status.get("name") or "").strip() or None,
            ),
            descriptionText=description_text,
            acceptanceCriteriaText=acceptance_text,
            recommendedImports=recommended,
        )

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
        step_parts = [f"Complete Jira story {issue_key}: {summary}".strip()]
        if description_text:
            step_parts.append(f"Description\n{description_text}")
        if acceptance_text:
            step_parts.append(f"Acceptance criteria\n{acceptance_text}")
        return JiraIssueRecommendations(
            presetInstructions="\n\n".join(part for part in preset_parts if part),
            stepInstructions="\n\n".join(part for part in step_parts if part),
        )

    def _issue_url(self, payload: Mapping[str, Any]) -> str | None:
        browse = payload.get("browseUrl") or payload.get("url")
        if browse:
            return str(browse)
        self_url = str(payload.get("self") or "")
        marker = "/rest/api/"
        if marker in self_url:
            return f"{self_url.split(marker, 1)[0]}/browse/{payload.get('key')}"
        return None

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
