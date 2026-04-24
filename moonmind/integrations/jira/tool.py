"""High-level Jira tool orchestration and policy enforcement."""

from __future__ import annotations

import re
from typing import Any

from moonmind.config.settings import AtlassianSettings, settings
from moonmind.integrations.jira.adf import ensure_adf_document
from moonmind.integrations.jira.auth import resolve_jira_connection
from moonmind.integrations.jira.client import JiraClient
from moonmind.integrations.jira.errors import JiraToolError
from moonmind.integrations.jira.models import (
    ALL_JIRA_ACTIONS,
    AddCommentRequest,
    CreateIssueRequest,
    CreateIssueLinkRequest,
    CreateSubtaskRequest,
    EditIssueRequest,
    GetCreateFieldsRequest,
    GetEditMetadataRequest,
    GetIssueRequest,
    GetTransitionsRequest,
    ListCreateIssueTypesRequest,
    SearchIssuesRequest,
    TransitionIssueRequest,
    VerifyConnectionRequest,
    normalize_action_name,
)

_JQL_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)

class JiraToolService:
    """Trusted Jira action layer used by the MCP tool registry."""

    def __init__(
        self,
        *,
        atlassian_settings: AtlassianSettings | None = None,
    ) -> None:
        self._settings = atlassian_settings or settings.atlassian

    def discoverable_actions(self) -> set[str]:
        configured = self._allowed_actions()
        if configured:
            return configured
        return set(ALL_JIRA_ACTIONS)

    def enabled(self) -> bool:
        return bool(self._settings.jira.jira_tool_enabled)

    async def create_issue(self, request: CreateIssueRequest) -> dict[str, Any]:
        self._ensure_enabled()
        self._ensure_action_allowed("create_issue")
        self._ensure_project_allowed(request.project_key)
        fields = dict(request.fields)
        fields["project"] = {"key": request.project_key}
        fields["issuetype"] = {"id": request.issue_type_id}
        fields["summary"] = request.summary
        description = ensure_adf_document(request.description)
        if description is not None:
            fields["description"] = description
        payload = await self._request_json(
            method="POST",
            path="/issue",
            action="create_issue",
            json_body={"fields": fields},
            context={"projectKey": request.project_key},
        )
        return {
            "created": True,
            "issueKey": payload.get("key"),
            "issueId": payload.get("id"),
            "self": payload.get("self"),
        }

    async def create_subtask(
        self, request: CreateSubtaskRequest
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self._ensure_action_allowed("create_subtask")
        self._ensure_project_allowed(request.project_key)
        self._ensure_project_allowed(self._project_from_issue_key(request.parent_issue_key))
        fields = dict(request.fields)
        fields["project"] = {"key": request.project_key}
        fields["issuetype"] = {"id": request.issue_type_id}
        fields["summary"] = request.summary
        fields["parent"] = {"key": request.parent_issue_key}
        description = ensure_adf_document(request.description)
        if description is not None:
            fields["description"] = description
        payload = await self._request_json(
            method="POST",
            path="/issue",
            action="create_subtask",
            json_body={"fields": fields},
            context={
                "projectKey": request.project_key,
                "parentIssueKey": request.parent_issue_key,
            },
        )
        return {
            "created": True,
            "issueKey": payload.get("key"),
            "issueId": payload.get("id"),
            "self": payload.get("self"),
        }

    async def create_issue_link(
        self, request: CreateIssueLinkRequest
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self._ensure_action_allowed("create_issue_link")
        self._ensure_project_allowed(self._project_from_issue_key(request.blocks_issue_key))
        self._ensure_project_allowed(self._project_from_issue_key(request.blocked_issue_key))
        try:
            await self._request_json(
                method="POST",
                path="/issueLink",
                action="create_issue_link",
                json_body={
                    "type": {"name": request.link_type},
                    "outwardIssue": {"key": request.blocks_issue_key},
                    "inwardIssue": {"key": request.blocked_issue_key},
                },
                context={
                    "blocksIssueKey": request.blocks_issue_key,
                    "blockedIssueKey": request.blocked_issue_key,
                    "linkType": request.link_type,
                },
            )
        except JiraToolError as exc:
            if exc.code == "jira_conflict_existing_link" or (
                exc.code == "jira_validation_failed"
                and "already exists" in str(exc).lower()
            ):
                return {
                    "linked": False,
                    "existing": True,
                    "blocksIssueKey": request.blocks_issue_key,
                    "blockedIssueKey": request.blocked_issue_key,
                    "linkType": request.link_type,
                }
            raise
        return {
            "linked": True,
            "blocksIssueKey": request.blocks_issue_key,
            "blockedIssueKey": request.blocked_issue_key,
            "linkType": request.link_type,
        }

    async def edit_issue(self, request: EditIssueRequest) -> dict[str, Any]:
        self._ensure_enabled()
        self._ensure_action_allowed("edit_issue")
        self._ensure_project_allowed(self._project_from_issue_key(request.issue_key))
        await self._request_json(
            method="PUT",
            path=f"/issue/{request.issue_key}",
            action="edit_issue",
            json_body={
                "fields": request.fields,
                "update": request.update,
            },
            context={"issueKey": request.issue_key},
        )
        return {"updated": True, "issueKey": request.issue_key}

    async def get_issue(self, request: GetIssueRequest) -> Any:
        self._ensure_enabled()
        self._ensure_action_allowed("get_issue")
        self._ensure_project_allowed(self._project_from_issue_key(request.issue_key))
        params: dict[str, Any] = {}
        if request.fields:
            params["fields"] = ",".join(request.fields)
        if request.expand:
            params["expand"] = ",".join(request.expand)
        return await self._request_json(
            method="GET",
            path=f"/issue/{request.issue_key}",
            action="get_issue",
            params=params or None,
            context={"issueKey": request.issue_key},
        )

    async def search_issues(self, request: SearchIssuesRequest) -> Any:
        self._ensure_enabled()
        self._ensure_action_allowed("search_issues")
        project_key = request.project_key
        allowed_projects = self._allowed_projects()
        if project_key:
            self._ensure_project_allowed(project_key)
        elif len(allowed_projects) == 1:
            project_key = next(iter(allowed_projects))
        elif len(allowed_projects) > 1:
            raise JiraToolError(
                "search_issues requires projectKey when multiple Jira projects are allowed.",
                code="jira_policy_denied",
                status_code=403,
                action="search_issues",
            )

        jql = request.jql
        if project_key:
            jql = self._scope_jql_to_project(jql, project_key)
        body: dict[str, Any] = {
            "jql": jql,
            "fields": request.fields,
            "maxResults": request.max_results,
        }
        if request.next_page_token:
            body["nextPageToken"] = request.next_page_token
        return await self._request_json(
            method="POST",
            path="/search/jql",
            action="search_issues",
            json_body=body,
            context={"projectKey": project_key or ""},
        )

    async def get_transitions(
        self, request: GetTransitionsRequest
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self._ensure_action_allowed("get_transitions")
        return await self._fetch_transitions(
            issue_key=request.issue_key,
            expand_fields=request.expand_fields,
        )

    async def _fetch_transitions(
        self,
        *,
        issue_key: str,
        expand_fields: bool = False,
    ) -> dict[str, Any]:
        self._ensure_project_allowed(self._project_from_issue_key(issue_key))
        params = {"expand": "transitions.fields"} if expand_fields else None
        payload = await self._request_json(
            method="GET",
            path=f"/issue/{issue_key}/transitions",
            action="get_transitions",
            params=params,
            context={"issueKey": issue_key},
        )
        return {"issueKey": issue_key, **payload}

    async def transition_issue(
        self, request: TransitionIssueRequest
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self._ensure_action_allowed("transition_issue")
        self._ensure_project_allowed(self._project_from_issue_key(request.issue_key))
        if self._settings.jira.jira_require_explicit_transition_lookup:
            transitions = await self._fetch_transitions(
                issue_key=request.issue_key,
            )
            available = {
                str(item.get("id", "")).strip()
                for item in transitions.get("transitions", [])
            }
            if request.transition_id not in available:
                raise JiraToolError(
                    "transitionId is not available for the target issue.",
                    code="jira_validation_failed",
                    status_code=422,
                    action="transition_issue",
                )
        await self._request_json(
            method="POST",
            path=f"/issue/{request.issue_key}/transitions",
            action="transition_issue",
            json_body={
                "transition": {"id": request.transition_id},
                "fields": request.fields,
                "update": request.update,
            },
            context={
                "issueKey": request.issue_key,
                "transitionId": request.transition_id,
            },
        )
        return {
            "transitioned": True,
            "issueKey": request.issue_key,
            "transitionId": request.transition_id,
        }

    async def add_comment(self, request: AddCommentRequest) -> Any:
        self._ensure_enabled()
        self._ensure_action_allowed("add_comment")
        self._ensure_project_allowed(self._project_from_issue_key(request.issue_key))
        body = ensure_adf_document(request.body)
        return await self._request_json(
            method="POST",
            path=f"/issue/{request.issue_key}/comment",
            action="add_comment",
            json_body={"body": body},
            context={"issueKey": request.issue_key},
        )

    async def list_create_issue_types(
        self, request: ListCreateIssueTypesRequest
    ) -> Any:
        self._ensure_enabled()
        self._ensure_action_allowed("list_create_issue_types")
        self._ensure_project_allowed(request.project_key)
        return await self._request_json(
            method="GET",
            path=f"/issue/createmeta/{request.project_key}/issuetypes",
            action="list_create_issue_types",
            context={"projectKey": request.project_key},
        )

    async def get_create_fields(
        self, request: GetCreateFieldsRequest
    ) -> Any:
        self._ensure_enabled()
        self._ensure_action_allowed("get_create_fields")
        self._ensure_project_allowed(request.project_key)
        return await self._request_json(
            method="GET",
            path=(
                f"/issue/createmeta/{request.project_key}/issuetypes/"
                f"{request.issue_type_id}"
            ),
            action="get_create_fields",
            context={
                "projectKey": request.project_key,
                "issueTypeId": request.issue_type_id,
            },
        )

    async def get_edit_metadata(
        self, request: GetEditMetadataRequest
    ) -> Any:
        self._ensure_enabled()
        self._ensure_action_allowed("get_edit_metadata")
        self._ensure_project_allowed(self._project_from_issue_key(request.issue_key))
        return await self._request_json(
            method="GET",
            path=f"/issue/{request.issue_key}/editmeta",
            action="get_edit_metadata",
            context={"issueKey": request.issue_key},
        )

    async def verify_connection(
        self, request: VerifyConnectionRequest
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self._ensure_action_allowed("verify_connection")
        if request.project_key:
            self._ensure_project_allowed(request.project_key)
            project = await self._request_json(
                method="GET",
                path=f"/project/{request.project_key}",
                action="verify_connection",
                context={"projectKey": request.project_key},
            )
            return {
                "ok": True,
                "projectKey": request.project_key,
                "projectName": project.get("name"),
            }
        profile = await self._request_json(
            method="GET",
            path="/myself",
            action="verify_connection",
            context={},
        )
        return {
            "ok": True,
            "accountId": profile.get("accountId"),
            "displayName": profile.get("displayName"),
        }

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

    def _ensure_enabled(self) -> None:
        if not self.enabled():
            raise JiraToolError(
                "Jira tools are not enabled.",
                code="tool_not_found",
                status_code=404,
            )

    def _allowed_projects(self) -> set[str]:
        raw = str(self._settings.jira.jira_allowed_projects or "").strip()
        if not raw:
            return set()
        return {
            item.strip().upper()
            for item in raw.split(",")
            if item.strip()
        }

    def _allowed_actions(self) -> set[str]:
        raw = str(self._settings.jira.jira_allowed_actions or "").strip()
        if not raw:
            return set()
        normalized = {
            normalize_action_name(item)
            for item in raw.split(",")
            if item.strip()
        }
        return {
            item for item in normalized if item in ALL_JIRA_ACTIONS
        }

    def _ensure_action_allowed(self, action: str) -> None:
        allowed = self._allowed_actions()
        if allowed and action not in allowed:
            raise JiraToolError(
                f"Action '{action}' is not allowed by Jira tool policy.",
                code="jira_policy_denied",
                status_code=403,
                action=action,
            )

    def _ensure_project_allowed(self, project_key: str) -> None:
        allowed = self._allowed_projects()
        if allowed and project_key.upper() not in allowed:
            raise JiraToolError(
                f"Project '{project_key}' is not allowed by Jira tool policy.",
                code="jira_policy_denied",
                status_code=403,
            )

    def _project_from_issue_key(self, issue_key: str) -> str:
        return issue_key.split("-", 1)[0]

    def _scope_jql_to_project(self, jql: str, project_key: str) -> str:
        match = _JQL_ORDER_BY_RE.search(jql)
        if match is None:
            return f"project = {project_key} AND ({jql})"
        predicate = jql[: match.start()].strip()
        order_by = jql[match.start() :].strip()
        if not predicate:
            return f"project = {project_key} {order_by}"
        return f"project = {project_key} AND ({predicate}) {order_by}"
