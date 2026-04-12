"""Browser-facing Jira read models and service for the Create page."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.config.settings import AtlassianSettings, settings
from moonmind.integrations.jira.auth import resolve_jira_connection
from moonmind.integrations.jira.client import JiraClient
from moonmind.integrations.jira.errors import JiraToolError

_JIRA_PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+$")
_JIRA_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")
_COLUMN_ID_RE = re.compile(r"[^a-z0-9]+")
_ACCEPTANCE_HEADING_RE = re.compile(
    r"^(acceptance\s+criteria|acceptance|ac)(\s*:)?$", re.IGNORECASE
)


class JiraBrowserModel(BaseModel):
    """Base model for browser-facing Jira responses."""

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
        return _normalize_project_key(value)


class JiraProjectList(JiraBrowserModel):
    items: list[JiraProject]


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
        return _normalize_project_key(value)


class JiraProjectBoards(JiraBrowserModel):
    project_key: str = Field(..., alias="projectKey")
    items: list[JiraBoard]

    @field_validator("project_key", mode="before")
    @classmethod
    def _normalize_project_key(cls, value: object) -> str:
        return _normalize_project_key(value)


class JiraColumn(JiraBrowserModel):
    id: str
    name: str
    order: int = Field(..., ge=0)
    count: int = Field(0, ge=0)
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
        return _normalize_issue_key(value)


class JiraBoardIssues(JiraBrowserModel):
    board_id: str = Field(..., alias="boardId")
    columns: list[JiraColumn]
    items_by_column: dict[str, list[JiraIssueSummary]] = Field(
        ..., alias="itemsByColumn"
    )
    unmapped_items: list[JiraIssueSummary] = Field(
        default_factory=list, alias="unmappedItems"
    )


class JiraColumnRef(JiraBrowserModel):
    id: str
    name: str


class JiraStatusRef(JiraBrowserModel):
    id: str
    name: str


class JiraRecommendedImports(JiraBrowserModel):
    preset_instructions: str = Field(..., alias="presetInstructions")
    step_instructions: str = Field(..., alias="stepInstructions")


class JiraIssueDetail(JiraBrowserModel):
    issue_key: str = Field(..., alias="issueKey")
    url: str | None = None
    summary: str
    issue_type: str | None = Field(None, alias="issueType")
    column: JiraColumnRef | None = None
    status: JiraStatusRef | None = None
    description_text: str = Field("", alias="descriptionText")
    acceptance_criteria_text: str = Field("", alias="acceptanceCriteriaText")
    recommended_imports: JiraRecommendedImports = Field(..., alias="recommendedImports")

    @field_validator("issue_key", mode="before")
    @classmethod
    def _normalize_issue_key(cls, value: object) -> str:
        return _normalize_issue_key(value)


class JiraBrowserService:
    """Trusted browser read service for the Create page Jira integration."""

    def __init__(
        self,
        *,
        atlassian_settings: AtlassianSettings | None = None,
        browser_enabled: bool | None = None,
    ) -> None:
        self._settings = atlassian_settings or settings.atlassian
        self._browser_enabled = (
            settings.feature_flags.jira_create_page_enabled
            if browser_enabled is None
            else browser_enabled
        )

    async def verify_connection(
        self, project_key: str | None = None
    ) -> JiraConnectionVerification:
        self._ensure_enabled()
        if project_key:
            normalized = _normalize_project_key(project_key)
            self._ensure_project_allowed(normalized)
            project = await self._request_json(
                method="GET",
                path=f"/project/{normalized}",
                action="jira_browser_verify_connection",
                context={"projectKey": normalized},
            )
            return JiraConnectionVerification(
                ok=True,
                projectKey=normalized,
                projectName=str(project.get("name") or normalized),
            )
        profile = await self._request_json(
            method="GET",
            path="/myself",
            action="jira_browser_verify_connection",
            context={},
        )
        return JiraConnectionVerification(
            ok=True,
            accountId=_optional_str(profile.get("accountId")),
            displayName=_optional_str(profile.get("displayName")),
        )

    async def list_projects(self) -> JiraProjectList:
        self._ensure_enabled()
        allowed = sorted(self._allowed_projects())
        if allowed:
            projects = [
                self._normalize_project(
                    await self._request_json(
                        method="GET",
                        path=f"/project/{project_key}",
                        action="jira_browser_list_projects",
                        context={"projectKey": project_key},
                    )
                )
                for project_key in allowed
            ]
            return JiraProjectList(items=projects)

        payload = await self._request_json(
            method="GET",
            path="/project/search",
            action="jira_browser_list_projects",
            params={"maxResults": 100},
            context={},
        )
        raw_items = payload.get("values", payload if isinstance(payload, list) else [])
        return JiraProjectList(
            items=[self._normalize_project(item) for item in raw_items]
        )

    async def list_project_boards(self, project_key: str) -> JiraProjectBoards:
        self._ensure_enabled()
        normalized = _normalize_project_key(project_key)
        self._ensure_project_allowed(normalized)
        payload = await self._request_json(
            method="GET",
            path="agile:/board",
            action="jira_browser_list_project_boards",
            params={"projectKeyOrId": normalized, "maxResults": 100},
            context={"projectKey": normalized},
        )
        raw_items = payload.get("values", payload if isinstance(payload, list) else [])
        boards = [
            self._normalize_board(item, fallback_project_key=normalized)
            for item in raw_items
        ]
        return JiraProjectBoards(projectKey=normalized, items=boards)

    async def list_board_columns(self, board_id: str) -> JiraBoardColumns:
        self._ensure_enabled()
        board = await self._get_board(board_id)
        self._ensure_project_allowed(board.project_key)
        columns = await self._get_columns(board.id)
        return JiraBoardColumns(board=board, columns=columns)

    async def list_board_issues(
        self, board_id: str, query: str | None = None
    ) -> JiraBoardIssues:
        self._ensure_enabled()
        board = await self._get_board(board_id)
        self._ensure_project_allowed(board.project_key)
        columns = await self._get_columns(board.id)
        status_to_column = {
            status_id: column.id
            for column in columns
            for status_id in column.status_ids
        }
        items_by_column: dict[str, list[JiraIssueSummary]] = {
            column.id: [] for column in columns
        }
        unmapped: list[JiraIssueSummary] = []
        payload = await self._request_json(
            method="GET",
            path=f"agile:/board/{board.id}/issue",
            action="jira_browser_list_board_issues",
            params={
                "maxResults": 100,
                "fields": "summary,issuetype,status,assignee,updated",
            },
            context={"boardId": board.id, "projectKey": board.project_key},
        )
        raw_issues = payload.get("issues", [])
        filter_text = str(query or "").strip().lower()
        for item in raw_issues:
            summary = self._normalize_issue_summary(item, status_to_column)
            if filter_text and filter_text not in summary.issue_key.lower() and (
                filter_text not in summary.summary.lower()
            ):
                continue
            if summary.column_id == "unmapped":
                unmapped.append(summary)
            else:
                items_by_column.setdefault(summary.column_id, []).append(summary)
        counted_columns = [
            column.model_copy(update={"count": len(items_by_column.get(column.id, []))})
            for column in columns
        ]
        return JiraBoardIssues(
            boardId=board.id,
            columns=counted_columns,
            itemsByColumn=items_by_column,
            unmappedItems=unmapped,
        )

    async def get_issue_detail(
        self, issue_key: str, board_id: str | None = None
    ) -> JiraIssueDetail:
        self._ensure_enabled()
        normalized = _normalize_issue_key(issue_key)
        self._ensure_project_allowed(normalized.split("-", 1)[0])
        payload = await self._request_json(
            method="GET",
            path=f"/issue/{normalized}",
            action="jira_browser_get_issue_detail",
            params={"fields": "summary,issuetype,status,description"},
            context={"issueKey": normalized},
        )
        fields = payload.get("fields", {})
        summary = str(fields.get("summary") or normalized).strip() or normalized
        issue_type = _nested_name(fields.get("issuetype"))
        status_payload = (
            fields.get("status") if isinstance(fields.get("status"), dict) else {}
        )
        status_id = _optional_str(status_payload.get("id"))
        status_name = _optional_str(status_payload.get("name"))
        description = _adf_to_text(fields.get("description"))
        description_text, acceptance_text = _split_acceptance_criteria(description)
        detail = JiraIssueDetail(
            issueKey=normalized,
            url=self._browse_url(payload, normalized),
            summary=summary,
            issueType=issue_type,
            status=(
                JiraStatusRef(id=status_id or "", name=status_name or "")
                if status_id or status_name
                else None
            ),
            descriptionText=description_text,
            acceptanceCriteriaText=acceptance_text,
            recommendedImports=JiraRecommendedImports(
                presetInstructions=_build_preset_import(
                    issue_key=normalized,
                    summary=summary,
                    description_text=description_text,
                ),
                stepInstructions=_build_step_import(
                    issue_key=normalized,
                    summary=summary,
                    description_text=description_text,
                    acceptance_text=acceptance_text,
                ),
            ),
        )
        if board_id:
            try:
                columns = await self._get_columns(str(board_id))
            except JiraToolError:
                columns = []
            if status_id:
                for column in columns:
                    if status_id in column.status_ids:
                        detail = detail.model_copy(
                            update={
                                "column": JiraColumnRef(
                                    id=column.id,
                                    name=column.name,
                                )
                            }
                        )
                        break
        return detail

    async def _get_board(self, board_id: str) -> JiraBoard:
        normalized = str(board_id or "").strip()
        if not normalized:
            raise JiraToolError(
                "boardId is required.",
                code="jira_validation_failed",
                status_code=422,
                action="jira_browser_get_board",
            )
        payload = await self._request_json(
            method="GET",
            path=f"agile:/board/{normalized}",
            action="jira_browser_get_board",
            context={"boardId": normalized},
        )
        return self._normalize_board(payload, fallback_project_key="")

    async def _get_columns(self, board_id: str) -> list[JiraColumn]:
        payload = await self._request_json(
            method="GET",
            path=f"agile:/board/{board_id}/configuration",
            action="jira_browser_get_board_columns",
            context={"boardId": board_id},
        )
        raw_columns = payload.get("columnConfig", {}).get("columns", [])
        columns: list[JiraColumn] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_columns):
            name = str(item.get("name") or f"Column {index + 1}").strip()
            column_id = _unique_column_id(_slugify(name), seen_ids)
            seen_ids.add(column_id)
            status_ids = [
                str(status.get("id")).strip()
                for status in item.get("statuses", [])
                if str(status.get("id") or "").strip()
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
        request_path = (
            _agile_url_from_api_base(connection.base_url, path[7:])
            if path.startswith("agile:/")
            else path
        )
        client = JiraClient(connection=connection)
        try:
            return await client.request_json(
                method=method,
                path=request_path,
                action=action,
                params=params,
                json_body=json_body,
                context=context,
            )
        finally:
            await client.aclose()

    def _normalize_project(self, item: Any) -> JiraProject:
        payload = item if isinstance(item, dict) else {}
        project_key = _normalize_project_key(payload.get("key"))
        return JiraProject(
            projectKey=project_key,
            name=str(payload.get("name") or project_key),
            id=_optional_str(payload.get("id")),
        )

    def _normalize_board(
        self, item: Any, *, fallback_project_key: str
    ) -> JiraBoard:
        payload = item if isinstance(item, dict) else {}
        location = (
            payload.get("location")
            if isinstance(payload.get("location"), dict)
            else {}
        )
        project_key = location.get("projectKey") or fallback_project_key
        return JiraBoard(
            id=str(payload.get("id") or "").strip(),
            name=str(payload.get("name") or payload.get("id") or "").strip(),
            projectKey=_normalize_project_key(project_key),
            type=_optional_str(payload.get("type")),
        )

    def _normalize_issue_summary(
        self,
        item: Any,
        status_to_column: dict[str, str],
    ) -> JiraIssueSummary:
        payload = item if isinstance(item, dict) else {}
        fields = (
            payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        )
        status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        status_id = _optional_str(status.get("id"))
        column_id = status_to_column.get(status_id or "", "unmapped")
        assignee = (
            fields.get("assignee")
            if isinstance(fields.get("assignee"), dict)
            else {}
        )
        return JiraIssueSummary(
            issueKey=payload.get("key"),
            summary=str(fields.get("summary") or payload.get("key") or "").strip(),
            issueType=_nested_name(fields.get("issuetype")),
            statusId=status_id,
            statusName=_optional_str(status.get("name")),
            assignee=_optional_str(assignee.get("displayName")),
            updatedAt=_optional_str(fields.get("updated")),
            columnId=column_id,
        )

    def _ensure_enabled(self) -> None:
        if not self._browser_enabled:
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
                f"Project '{project_key}' is not allowed by Jira policy.",
                code="jira_policy_denied",
                status_code=403,
            )

    def _browse_url(self, payload: dict[str, Any], issue_key: str) -> str | None:
        self_url = _optional_str(payload.get("self"))
        if not self_url:
            return None
        split = urlsplit(self_url)
        marker = "/rest/api/"
        if marker not in split.path:
            return None
        base_path = split.path.split(marker, 1)[0]
        return urlunsplit(
            (
                split.scheme,
                split.netloc,
                f"{base_path}/browse/{issue_key}",
                "",
                "",
            )
        )


def _normalize_project_key(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ValueError("projectKey is required")
    if not _JIRA_PROJECT_KEY_RE.fullmatch(normalized):
        raise ValueError("projectKey must match a Jira project-key pattern")
    return normalized


def _normalize_issue_key(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ValueError("issueKey is required")
    if not _JIRA_ISSUE_KEY_RE.fullmatch(normalized):
        raise ValueError("issueKey must match a Jira issue-key pattern")
    return normalized


def _optional_str(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _nested_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return _optional_str(value.get("name"))


def _slugify(value: str) -> str:
    slug = _COLUMN_ID_RE.sub("-", value.lower()).strip("-")
    return slug or "column"


def _unique_column_id(base: str, seen: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _adf_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    lines: list[str] = []

    def walk(node: Any, *, in_list_item: bool = False) -> None:
        if isinstance(node, str):
            lines.append(node)
            return
        if not isinstance(node, dict):
            return
        node_type = node.get("type")
        if node_type == "text":
            text = str(node.get("text") or "")
            if text:
                lines.append(text)
            return
        if node_type == "hardBreak":
            lines.append("\n")
            return
        if node_type == "listItem":
            for child in node.get("content", []):
                walk(child, in_list_item=True)
            lines.append("\n")
            return
        block_break = node_type in {"paragraph", "heading"}
        for child in node.get("content", []):
            walk(child, in_list_item=in_list_item)
        if block_break and not in_list_item:
            lines.append("\n")

    walk(value)
    text = "".join(lines)
    normalized_lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(line for line in normalized_lines).strip()


def _split_acceptance_criteria(text: str) -> tuple[str, str]:
    if not text.strip():
        return "", ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _ACCEPTANCE_HEADING_RE.fullmatch(line.strip()):
            description = "\n".join(lines[:index]).strip()
            acceptance = "\n".join(lines[index + 1 :]).strip()
            return description, acceptance
    return text.strip(), ""


def _build_preset_import(
    *,
    issue_key: str,
    summary: str,
    description_text: str,
) -> str:
    parts = [f"{issue_key}: {summary}"]
    if description_text:
        parts.append(description_text)
    return "\n\n".join(parts)


def _build_step_import(
    *,
    issue_key: str,
    summary: str,
    description_text: str,
    acceptance_text: str,
) -> str:
    parts = [f"Complete Jira story {issue_key}: {summary}"]
    if description_text:
        parts.append(f"Description\n{description_text}")
    if acceptance_text:
        parts.append(f"Acceptance criteria\n{acceptance_text}")
    return "\n\n".join(parts)


def _agile_url_from_api_base(base_url: str, agile_path: str) -> str:
    split = urlsplit(base_url)
    api_marker = "/rest/api/"
    base_path = split.path
    if api_marker in base_path:
        prefix = base_path.split(api_marker, 1)[0]
    else:
        prefix = base_path.rstrip("/")
    normalized_path = agile_path if agile_path.startswith("/") else f"/{agile_path}"
    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            f"{prefix}/rest/agile/1.0{normalized_path}",
            "",
            "",
        )
    )
