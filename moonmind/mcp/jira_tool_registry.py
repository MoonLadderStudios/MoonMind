"""Jira MCP tool registry and dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel, ValidationError

from moonmind.integrations.jira.models import (
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
from moonmind.integrations.jira.tool import JiraToolService
from moonmind.mcp.tool_registry import (
    ToolArgumentsValidationError,
    ToolMetadata,
    ToolNotFoundError,
    _ToolDefinition,
)


@dataclass(frozen=True, slots=True)
class JiraToolExecutionContext:
    """Dependencies available to Jira tool handlers."""

    service: JiraToolService


JiraToolHandler = Callable[[BaseModel, JiraToolExecutionContext], Awaitable[Any]]


class JiraToolRegistry:
    """Registry for trusted Jira-related MCP tools."""

    def __init__(self, *, enabled_actions: set[str] | None = None) -> None:
        self._enabled_actions = enabled_actions or set()
        self._tools: dict[str, _ToolDefinition] = {}
        self._register_default_tools()

    def list_tools(self) -> list[ToolMetadata]:
        output: list[ToolMetadata] = []
        for definition in sorted(self._tools.values(), key=lambda item: item.name):
            output.append(
                ToolMetadata(
                    name=definition.name,
                    description=definition.description,
                    input_schema=definition.argument_model.model_json_schema(
                        by_alias=True
                    ),
                )
            )
        return output

    async def call_tool(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any] | None,
        context: JiraToolExecutionContext,
    ) -> Any:
        definition = self._tools.get(tool)
        if definition is None:
            raise ToolNotFoundError(tool)

        payload = dict(arguments or {})
        try:
            parsed_args = definition.argument_model.model_validate(payload)
        except ValidationError as exc:
            raise ToolArgumentsValidationError(tool, detail=str(exc)) from exc

        return await definition.handler(parsed_args, context)

    def _register_default_tools(self) -> None:
        self._register(
            "jira.create_issue",
            "Create a Jira issue through the trusted MoonMind Jira tool path.",
            CreateIssueRequest,
            self._handle_create_issue,
        )
        self._register(
            "jira.create_issue_link",
            "Create a Jira issue dependency link through the trusted MoonMind Jira tool path.",
            CreateIssueLinkRequest,
            self._handle_create_issue_link,
        )
        self._register(
            "jira.create_subtask",
            "Create a Jira sub-task through the trusted MoonMind Jira tool path.",
            CreateSubtaskRequest,
            self._handle_create_subtask,
        )
        self._register(
            "jira.edit_issue",
            "Edit mutable fields on a Jira issue.",
            EditIssueRequest,
            self._handle_edit_issue,
        )
        self._register(
            "jira.get_issue",
            "Fetch one Jira issue.",
            GetIssueRequest,
            self._handle_get_issue,
        )
        self._register(
            "jira.search_issues",
            "Search Jira issues with JQL.",
            SearchIssuesRequest,
            self._handle_search_issues,
        )
        self._register(
            "jira.get_transitions",
            "List valid Jira transitions for an issue.",
            GetTransitionsRequest,
            self._handle_get_transitions,
        )
        self._register(
            "jira.transition_issue",
            "Apply an explicit Jira workflow transition.",
            TransitionIssueRequest,
            self._handle_transition_issue,
        )
        self._register(
            "jira.add_comment",
            "Add a comment to a Jira issue.",
            AddCommentRequest,
            self._handle_add_comment,
        )
        self._register(
            "jira.list_create_issue_types",
            "List available Jira issue types for one project.",
            ListCreateIssueTypesRequest,
            self._handle_list_create_issue_types,
        )
        self._register(
            "jira.get_create_fields",
            "Get Jira create-field metadata for one project and issue type.",
            GetCreateFieldsRequest,
            self._handle_get_create_fields,
        )
        self._register(
            "jira.get_edit_metadata",
            "Get editable Jira field metadata for one issue.",
            GetEditMetadataRequest,
            self._handle_get_edit_metadata,
        )
        self._register(
            "jira.verify_connection",
            "Verify Jira connectivity and permissions through the trusted Jira binding.",
            VerifyConnectionRequest,
            self._handle_verify_connection,
        )

    def _register(
        self,
        name: str,
        description: str,
        argument_model: type[BaseModel],
        handler: JiraToolHandler,
    ) -> None:
        action = normalize_action_name(name)
        if self._enabled_actions and action not in self._enabled_actions:
            return
        self._tools[name] = _ToolDefinition(
            name=name,
            description=description,
            argument_model=argument_model,
            handler=handler,
        )

    async def _handle_create_issue(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> dict[str, Any]:
        if not isinstance(args, CreateIssueRequest):
            raise ToolArgumentsValidationError(
                "jira.create_issue", detail="Invalid payload type"
            )
        return await context.service.create_issue(args)

    async def _handle_create_subtask(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> dict[str, Any]:
        if not isinstance(args, CreateSubtaskRequest):
            raise ToolArgumentsValidationError(
                "jira.create_subtask", detail="Invalid payload type"
            )
        return await context.service.create_subtask(args)

    async def _handle_create_issue_link(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> dict[str, Any]:
        if not isinstance(args, CreateIssueLinkRequest):
            raise ToolArgumentsValidationError(
                "jira.create_issue_link", detail="Invalid payload type"
            )
        return await context.service.create_issue_link(args)

    async def _handle_edit_issue(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> dict[str, Any]:
        if not isinstance(args, EditIssueRequest):
            raise ToolArgumentsValidationError(
                "jira.edit_issue", detail="Invalid payload type"
            )
        return await context.service.edit_issue(args)

    async def _handle_get_issue(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> Any:
        if not isinstance(args, GetIssueRequest):
            raise ToolArgumentsValidationError(
                "jira.get_issue", detail="Invalid payload type"
            )
        return await context.service.get_issue(args)

    async def _handle_search_issues(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> Any:
        if not isinstance(args, SearchIssuesRequest):
            raise ToolArgumentsValidationError(
                "jira.search_issues", detail="Invalid payload type"
            )
        return await context.service.search_issues(args)

    async def _handle_get_transitions(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> dict[str, Any]:
        if not isinstance(args, GetTransitionsRequest):
            raise ToolArgumentsValidationError(
                "jira.get_transitions", detail="Invalid payload type"
            )
        return await context.service.get_transitions(args)

    async def _handle_transition_issue(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> dict[str, Any]:
        if not isinstance(args, TransitionIssueRequest):
            raise ToolArgumentsValidationError(
                "jira.transition_issue", detail="Invalid payload type"
            )
        return await context.service.transition_issue(args)

    async def _handle_add_comment(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> Any:
        if not isinstance(args, AddCommentRequest):
            raise ToolArgumentsValidationError(
                "jira.add_comment", detail="Invalid payload type"
            )
        return await context.service.add_comment(args)

    async def _handle_list_create_issue_types(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> Any:
        if not isinstance(args, ListCreateIssueTypesRequest):
            raise ToolArgumentsValidationError(
                "jira.list_create_issue_types", detail="Invalid payload type"
            )
        return await context.service.list_create_issue_types(args)

    async def _handle_get_create_fields(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> Any:
        if not isinstance(args, GetCreateFieldsRequest):
            raise ToolArgumentsValidationError(
                "jira.get_create_fields", detail="Invalid payload type"
            )
        return await context.service.get_create_fields(args)

    async def _handle_get_edit_metadata(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> Any:
        if not isinstance(args, GetEditMetadataRequest):
            raise ToolArgumentsValidationError(
                "jira.get_edit_metadata", detail="Invalid payload type"
            )
        return await context.service.get_edit_metadata(args)

    async def _handle_verify_connection(
        self, args: BaseModel, context: JiraToolExecutionContext
    ) -> dict[str, Any]:
        if not isinstance(args, VerifyConnectionRequest):
            raise ToolArgumentsValidationError(
                "jira.verify_connection", detail="Invalid payload type"
            )
        return await context.service.verify_connection(args)


__all__ = ["JiraToolExecutionContext", "JiraToolRegistry"]
