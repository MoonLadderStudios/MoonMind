"""Authenticated MCP registry for bounded remediation evidence operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from moonmind.mcp.tool_registry import (
    ToolArgumentsValidationError,
    ToolMetadata,
    ToolNotFoundError,
    _ToolDefinition,
)
from moonmind.workflows.temporal.remediation_tools import (
    RemediationEvidenceToolService,
)


class RemediationEvidencePageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remediation_workflow_id: str = Field(alias="remediationWorkflowId")
    cursor: int = 0
    limit: int = 20
    include_content: bool = Field(False, alias="includeContent")
    max_content_bytes: int = Field(65_536, alias="maxContentBytes")


class RemediationLogRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remediation_workflow_id: str = Field(alias="remediationWorkflowId")
    agent_run_id: str = Field(alias="agentRunId")
    stream: str = "merged"
    cursor: str | None = None
    tail_lines: int | None = Field(None, alias="tailLines")


class RemediationFollowRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remediation_workflow_id: str = Field(alias="remediationWorkflowId")
    agent_run_id: str | None = Field(None, alias="agentRunId")
    from_sequence: int | None = Field(None, alias="fromSequence")


class RemediationArtifactRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remediation_workflow_id: str = Field(alias="remediationWorkflowId")
    artifact_ref: str | dict[str, Any] = Field(alias="artifactRef")
    include_content: bool = Field(False, alias="includeContent")
    max_content_bytes: int = Field(65_536, alias="maxContentBytes")


class RemediationActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remediation_workflow_id: str = Field(alias="remediationWorkflowId")
    authority_result: dict[str, Any] = Field(alias="authorityResult")
    guard_result: dict[str, Any] = Field(alias="guardResult")


@dataclass(frozen=True, slots=True)
class RemediationToolExecutionContext:
    service: RemediationEvidenceToolService
    principal: str


class RemediationToolRegistry:
    """Expose only bounded, link-authorized remediation reads."""

    _PAGE_TO_METHOD = {
        "remediation.read_execution_steps": "read_execution_and_step_details",
        "remediation.read_checkpoint_recovery": "read_checkpoint_and_recovery_manifests",
        "remediation.read_bridge_events": "read_bridge_event_pages",
        "remediation.read_capture_resources": "read_capture_and_resource_manifests",
        "remediation.read_cleanup_janitor": "read_cleanup_and_janitor_evidence",
        "remediation.read_branch_publication": "read_branch_and_publication_evidence",
        "remediation.read_policy_approvals": "read_policy_and_approval_snapshots",
    }

    def __init__(self) -> None:
        self._tools: dict[str, _ToolDefinition] = {}
        for name in self._PAGE_TO_METHOD:
            self._register(
                name,
                "Read one bounded, redacted page of linked remediation evidence.",
                RemediationEvidencePageRequest,
                self._handle_page,
            )
        self._register(
            "remediation.read_target_logs",
            "Read bounded, redacted target logs authorized by remediation context.",
            RemediationLogRequest,
            self._handle_logs,
        )
        self._register(
            "remediation.follow_target_logs",
            "Read the next bounded live target event page and return its cursor.",
            RemediationFollowRequest,
            self._handle_follow,
        )
        self._register(
            "remediation.read_target_artifact",
            "Read metadata and optionally bounded, redacted content for a context-linked artifact.",
            RemediationArtifactRequest,
            self._handle_artifact,
        )
        self._register(
            "remediation.execute_action",
            "Execute one pre-authorized and mutation-guarded typed remediation action.",
            RemediationActionRequest,
            self._handle_action,
        )

    def list_tools(self) -> list[ToolMetadata]:
        return [
            ToolMetadata(
                name=item.name,
                description=item.description,
                input_schema=item.argument_model.model_json_schema(by_alias=True),
            )
            for item in sorted(self._tools.values(), key=lambda value: value.name)
        ]

    async def call_tool(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any] | None,
        context: RemediationToolExecutionContext,
    ) -> Any:
        definition = self._tools.get(tool)
        if definition is None:
            raise ToolNotFoundError(tool)
        try:
            parsed = definition.argument_model.model_validate(dict(arguments or {}))
        except ValidationError as exc:
            raise ToolArgumentsValidationError(tool, detail=str(exc)) from exc
        return await definition.handler(parsed, (tool, context))

    def _register(self, name: str, description: str, model: type[BaseModel], handler: Any) -> None:
        self._tools[name] = _ToolDefinition(name, description, model, handler)

    async def _handle_page(
        self, request: RemediationEvidencePageRequest, dispatch: Any
    ) -> Any:
        tool, context = dispatch
        method = getattr(context.service, self._PAGE_TO_METHOD[tool])
        result = await method(
            remediation_workflow_id=request.remediation_workflow_id,
            cursor=request.cursor,
            limit=request.limit,
            include_content=request.include_content,
            max_content_bytes=request.max_content_bytes,
            principal=context.principal,
        )
        return asdict(result)

    async def _handle_logs(self, request: RemediationLogRequest, dispatch: Any) -> Any:
        _, context = dispatch
        result = await context.service.read_target_logs(
            remediation_workflow_id=request.remediation_workflow_id,
            agent_run_id=request.agent_run_id,
            stream=request.stream,
            cursor=request.cursor,
            tail_lines=request.tail_lines,
            principal=context.principal,
        )
        return asdict(result)

    async def _handle_follow(
        self, request: RemediationFollowRequest, dispatch: Any
    ) -> Any:
        _, context = dispatch
        result = await context.service.follow_target_logs(
            remediation_workflow_id=request.remediation_workflow_id,
            agent_run_id=request.agent_run_id,
            from_sequence=request.from_sequence,
            principal=context.principal,
        )
        return asdict(result) if is_dataclass(result) else result

    async def _handle_artifact(
        self, request: RemediationArtifactRequest, dispatch: Any
    ) -> Any:
        _, context = dispatch
        result = await context.service.read_target_artifact_bounded(
            remediation_workflow_id=request.remediation_workflow_id,
            artifact_ref=request.artifact_ref,
            include_content=request.include_content,
            max_content_bytes=request.max_content_bytes,
            principal=context.principal,
        )
        return asdict(result)

    async def _handle_action(
        self, request: RemediationActionRequest, dispatch: Any
    ) -> Any:
        _, context = dispatch
        return await context.service.execute_action(
            remediation_workflow_id=request.remediation_workflow_id,
            authority_result=request.authority_result,
            guard_result=request.guard_result,
            principal=context.principal,
        )


__all__ = ["RemediationToolExecutionContext", "RemediationToolRegistry"]
