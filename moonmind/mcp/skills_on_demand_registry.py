"""Skills On Demand MCP command registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel, ValidationError

from moonmind.schemas.agent_skill_models import (
    RuntimeMaterializationMode,
    SkillSelector,
    SkillSelectorEntry,
    SkillsOnDemandQueryRequest,
    SkillsOnDemandRequest,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer
from moonmind.services.skill_resolution import AgentSkillResolver, SkillResolutionContext
from moonmind.services.skills_on_demand import SkillsOnDemandService
from moonmind.mcp.tool_registry import (
    ToolArgumentsValidationError,
    ToolMetadata,
    ToolNotFoundError,
    _ToolDefinition,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
from moonmind.workflows.skills.run_projection import load_resolved_skillset


@dataclass(frozen=True, slots=True)
class SkillsOnDemandToolExecutionContext:
    """Dependencies for MoonMind-owned Skills On Demand MCP commands."""

    enabled: bool
    workspace_root: str
    artifact_service: Any | None = None
    async_session_maker: Any | None = None
    skills_cache_root: str | None = None
    skills_workspace_root: str | None = None
    allow_repo_skills: bool = False
    allow_local_skills: bool = False


SkillsOnDemandToolHandler = Callable[
    [BaseModel, SkillsOnDemandToolExecutionContext], Awaitable[Any]
]


class SkillsOnDemandToolRegistry:
    """Registry for managed-runtime Skills On Demand control commands."""

    def __init__(self, *, expose_commands: bool = False) -> None:
        self._expose_commands = expose_commands
        self._tools: dict[str, _ToolDefinition] = {}
        self._register_default_tools()

    def list_tools(self) -> list[ToolMetadata]:
        if not self._expose_commands:
            return []
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
        context: SkillsOnDemandToolExecutionContext,
    ) -> Any:
        definition = self._tools.get(tool)
        if definition is None:
            raise ToolNotFoundError(tool)

        payload = dict(arguments or {})
        try:
            parsed_args = definition.argument_model.model_validate(payload)
        except ValidationError as exc:
            raise ToolArgumentsValidationError(tool, detail=str(exc)) from exc

        result = await definition.handler(parsed_args, context)
        return result.model_dump(mode="json")

    def _register_default_tools(self) -> None:
        self._register(
            "moonmind.skills.query",
            "Query metadata for policy-eligible MoonMind agent Skills.",
            SkillsOnDemandQueryRequest,
            self._handle_query,
        )
        self._register(
            "moonmind.skills.request",
            "Request activation of additional MoonMind agent Skills.",
            SkillsOnDemandRequest,
            self._handle_request,
        )

    def _register(
        self,
        name: str,
        description: str,
        argument_model: type[BaseModel],
        handler: SkillsOnDemandToolHandler,
    ) -> None:
        self._tools[name] = _ToolDefinition(
            name=name,
            description=description,
            argument_model=argument_model,
            handler=handler,
        )

    async def _handle_query(
        self,
        args: BaseModel,
        context: SkillsOnDemandToolExecutionContext,
    ) -> Any:
        if not isinstance(args, SkillsOnDemandQueryRequest):
            raise ToolArgumentsValidationError(
                "moonmind.skills.query", detail="Invalid payload type"
            )

        catalog_entries = []
        if context.enabled:
            resolver_context = SkillResolutionContext(
                snapshot_id=args.current_snapshot_ref
                or f"skillquery_mcp_{datetime.now(UTC).timestamp():.0f}",
                workspace_root=context.workspace_root,
                allow_repo_skills=context.allow_repo_skills,
                allow_local_skills=context.allow_local_skills,
                async_session_maker=context.async_session_maker,
            )
            catalog_entries = await AgentSkillResolver().query_catalog(
                SkillSelector(),
                resolver_context,
            )

        return await SkillsOnDemandService(
            enabled=context.enabled,
            catalog_entries=catalog_entries,
            allow_repo_skills=context.allow_repo_skills,
            allow_local_skills=context.allow_local_skills,
        ).query(args)

    async def _handle_request(
        self,
        args: BaseModel,
        context: SkillsOnDemandToolExecutionContext,
    ) -> Any:
        if not isinstance(args, SkillsOnDemandRequest):
            raise ToolArgumentsValidationError(
                "moonmind.skills.request", detail="Invalid payload type"
            )

        request = await self._with_loaded_active_snapshot(args, context)
        service = SkillsOnDemandService(enabled=context.enabled)
        initial_result = await service.request(request)
        if (
            initial_result.status != "denied"
            or initial_result.code != "enabled_mode_not_implemented"
        ):
            return initial_result

        active_snapshot = request.active_snapshot
        if active_snapshot is None:
            return initial_result

        requested = service.normalized_requested_skills(request)
        active_names = {skill.skill_name for skill in active_snapshot.skills}
        addition_selector = SkillSelector(
            include=[
                SkillSelectorEntry(name=name)
                for name in requested
                if name not in active_names
            ]
        )
        snapshot_id = f"skillset_mcp_{active_snapshot.snapshot_id}_derived"
        resolver_context = SkillResolutionContext(
            snapshot_id=snapshot_id,
            deployment_id=active_snapshot.deployment_id,
            workspace_root=context.workspace_root,
            allow_repo_skills=context.allow_repo_skills,
            allow_local_skills=context.allow_local_skills,
            async_session_maker=context.async_session_maker,
        )

        activities = AgentSkillsActivities(
            artifact_service=context.artifact_service,
            async_session_maker=context.async_session_maker,
        )
        try:
            resolved_additions = await AgentSkillResolver().resolve(
                addition_selector,
                resolver_context,
                base_entries=list(active_snapshot.skills),
            )
            derived_set = activities._build_on_demand_derived_skillset(
                active_snapshot=active_snapshot,
                resolved_additions=resolved_additions,
                request=request,
                snapshot_id=snapshot_id,
            )
            if context.artifact_service is not None:
                activity_info = _McpActivityInfo()
                derived_set = await activities._persist_file_backed_skill_content(
                    resolved_set=derived_set,
                    activity_info=activity_info,
                )
                derived_set = await activities._persist_resolved_skillset_manifest(
                    resolved_set=derived_set,
                    snapshot_id=derived_set.snapshot_id,
                    activity_info=activity_info,
                )
            materialization = await AgentSkillMaterializer(
                workspace_root=context.workspace_root,
                artifact_service=context.artifact_service,
                backing_root=context.skills_cache_root,
                source_preservation_root=context.skills_workspace_root,
            ).materialize(
                derived_set,
                request.runtime_id or "managed-runtime",
                RuntimeMaterializationMode.WORKSPACE_MOUNTED,
            )
            if materialization.metadata.get("runtimeRefreshFailed"):
                message = (
                    materialization.metadata.get("runtimeRefreshMessage")
                    or "Skills On Demand runtime refresh failed."
                )
                return service.denied_request_result(
                    request,
                    code="runtime_refresh_failed",
                    message=str(message).split("\n", 1)[0],
                )
        except ValueError as exc:
            return service.denied_request_result(
                request,
                code=activities._skills_on_demand_resolution_code(str(exc)),
                message=str(exc).split("\n", 1)[0],
            )
        except RuntimeError as exc:
            return service.denied_request_result(
                request,
                code=activities._skills_on_demand_runtime_code(str(exc)),
                message=str(exc).split("\n", 1)[0],
            )

        return await service.request(
            request,
            resolved_skillset=derived_set,
            materialization=materialization,
        )

    async def _with_loaded_active_snapshot(
        self,
        request: SkillsOnDemandRequest,
        context: SkillsOnDemandToolExecutionContext,
    ) -> SkillsOnDemandRequest:
        if not request.current_snapshot_ref:
            return request.model_copy(update={"active_snapshot": None})
        try:
            active_snapshot = await load_resolved_skillset(
                context.artifact_service,
                request.current_snapshot_ref,
            )
        except Exception:
            return request.model_copy(update={"active_snapshot": None})
        return request.model_copy(
            update={
                "active_snapshot": active_snapshot,
                "current_snapshot_ref": active_snapshot.snapshot_id,
            }
        )


@dataclass(frozen=True, slots=True)
class _McpActivityInfo:
    namespace: str = "default"
    workflow_id: str = "mcp.skills_on_demand"
    workflow_run_id: str = "mcp"
    activity_id: str = "mcp.skills_on_demand"


__all__ = ["SkillsOnDemandToolExecutionContext", "SkillsOnDemandToolRegistry"]
