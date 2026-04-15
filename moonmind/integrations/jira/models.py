"""Strict request models for Jira MCP tools."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

JiraActionName = Literal[
    "create_issue",
    "create_issue_link",
    "create_subtask",
    "edit_issue",
    "get_issue",
    "search_issues",
    "get_transitions",
    "transition_issue",
    "add_comment",
    "list_create_issue_types",
    "get_create_fields",
    "get_edit_metadata",
    "verify_connection",
]

ALL_JIRA_ACTIONS: tuple[JiraActionName, ...] = (
    "create_issue",
    "create_issue_link",
    "create_subtask",
    "edit_issue",
    "get_issue",
    "search_issues",
    "get_transitions",
    "transition_issue",
    "add_comment",
    "list_create_issue_types",
    "get_create_fields",
    "get_edit_metadata",
    "verify_connection",
)

_JIRA_PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+$")
_JIRA_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")


def normalize_action_name(value: str) -> str:
    """Normalize configured action names with or without the jira. prefix."""

    normalized = str(value or "").strip().lower()
    if normalized.startswith("jira."):
        normalized = normalized[5:]
    return normalized


class JiraBaseModel(BaseModel):
    """Base model for Jira tool requests."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class JiraProjectScopedRequest(JiraBaseModel):
    """Base request with a Jira project key."""

    project_key: str = Field(..., alias="projectKey")

    @field_validator("project_key", mode="before")
    @classmethod
    def _normalize_project_key(cls, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized:
            raise ValueError("projectKey is required")
        if not _JIRA_PROJECT_KEY_RE.fullmatch(normalized):
            raise ValueError("projectKey must match a Jira project-key pattern")
        return normalized


class JiraIssueScopedRequest(JiraBaseModel):
    """Base request with a Jira issue key."""

    issue_key: str = Field(..., alias="issueKey")

    @field_validator("issue_key", mode="before")
    @classmethod
    def _normalize_issue_key(cls, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized:
            raise ValueError("issueKey is required")
        if not _JIRA_ISSUE_KEY_RE.fullmatch(normalized):
            raise ValueError("issueKey must match a Jira issue-key pattern")
        return normalized


class CreateIssueRequest(JiraProjectScopedRequest):
    issue_type_id: str = Field(..., alias="issueTypeId")
    summary: str = Field(..., min_length=1, alias="summary")
    description: str | dict[str, Any] | None = Field(None, alias="description")
    fields: dict[str, Any] = Field(default_factory=dict, alias="fields")

    @field_validator("issue_type_id", mode="before")
    @classmethod
    def _normalize_issue_type_id(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("issueTypeId is required")
        return normalized


class CreateSubtaskRequest(CreateIssueRequest):
    parent_issue_key: str = Field(..., alias="parentIssueKey")

    @field_validator("parent_issue_key", mode="before")
    @classmethod
    def _normalize_parent_issue_key(cls, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized:
            raise ValueError("parentIssueKey is required")
        if not _JIRA_ISSUE_KEY_RE.fullmatch(normalized):
            raise ValueError("parentIssueKey must match a Jira issue-key pattern")
        return normalized


class CreateIssueLinkRequest(JiraBaseModel):
    blocks_issue_key: str = Field(..., alias="blocksIssueKey")
    blocked_issue_key: str = Field(..., alias="blockedIssueKey")
    link_type: str = Field("Blocks", alias="linkType")

    @field_validator("blocks_issue_key", "blocked_issue_key", mode="before")
    @classmethod
    def _normalize_issue_key(cls, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized:
            raise ValueError("issue key is required")
        if not _JIRA_ISSUE_KEY_RE.fullmatch(normalized):
            raise ValueError("issue key must match a Jira issue-key pattern")
        return normalized

    @field_validator("link_type", mode="before")
    @classmethod
    def _normalize_link_type(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("linkType is required")
        return normalized

    @field_validator("blocked_issue_key")
    @classmethod
    def _reject_self_link(cls, value: str, info: ValidationInfo) -> str:
        if value == info.data.get("blocks_issue_key"):
            raise ValueError("blocksIssueKey and blockedIssueKey must differ")
        return value


class EditIssueRequest(JiraIssueScopedRequest):
    fields: dict[str, Any] = Field(default_factory=dict, alias="fields")
    update: dict[str, Any] = Field(default_factory=dict, alias="update")


class GetIssueRequest(JiraIssueScopedRequest):
    fields: list[str] = Field(default_factory=list, alias="fields")
    expand: list[str] = Field(default_factory=list, alias="expand")


class SearchIssuesRequest(JiraBaseModel):
    jql: str = Field(..., min_length=1, alias="jql")
    project_key: str | None = Field(None, alias="projectKey")
    fields: list[str] = Field(default_factory=list, alias="fields")
    max_results: int = Field(50, ge=1, le=200, alias="maxResults")
    next_page_token: str | None = Field(None, alias="nextPageToken")

    @field_validator("project_key", mode="before")
    @classmethod
    def _normalize_project_key(cls, value: object) -> str | None:
        if value in (None, ""):
            return None
        normalized = str(value).strip().upper()
        if not _JIRA_PROJECT_KEY_RE.fullmatch(normalized):
            raise ValueError("projectKey must match a Jira project-key pattern")
        return normalized

    @field_validator("next_page_token", mode="before")
    @classmethod
    def _normalize_next_page_token(cls, value: object) -> str | None:
        if value in (None, ""):
            return None
        return str(value).strip()


class GetTransitionsRequest(JiraIssueScopedRequest):
    expand_fields: bool = Field(False, alias="expandFields")


class TransitionIssueRequest(JiraIssueScopedRequest):
    transition_id: str = Field(..., alias="transitionId")
    fields: dict[str, Any] = Field(default_factory=dict, alias="fields")
    update: dict[str, Any] = Field(default_factory=dict, alias="update")

    @field_validator("transition_id", mode="before")
    @classmethod
    def _normalize_transition_id(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("transitionId is required")
        return normalized


class AddCommentRequest(JiraIssueScopedRequest):
    body: str | dict[str, Any] = Field(..., alias="body")


class ListCreateIssueTypesRequest(JiraProjectScopedRequest):
    pass


class GetCreateFieldsRequest(JiraProjectScopedRequest):
    issue_type_id: str = Field(..., alias="issueTypeId")

    @field_validator("issue_type_id", mode="before")
    @classmethod
    def _normalize_issue_type_id(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("issueTypeId is required")
        return normalized


class GetEditMetadataRequest(JiraIssueScopedRequest):
    pass


class VerifyConnectionRequest(JiraBaseModel):
    project_key: str | None = Field(None, alias="projectKey")

    @field_validator("project_key", mode="before")
    @classmethod
    def _normalize_project_key(cls, value: object) -> str | None:
        if value in (None, ""):
            return None
        normalized = str(value).strip().upper()
        if not _JIRA_PROJECT_KEY_RE.fullmatch(normalized):
            raise ValueError("projectKey must match a Jira project-key pattern")
        return normalized
