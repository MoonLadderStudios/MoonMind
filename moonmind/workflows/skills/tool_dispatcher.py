"""Activity-style tool dispatch and plan validation helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from .artifact_store import ArtifactStore
from .plan_validation import PlanValidationError, validate_plan_payload
from .tool_plan_contracts import Step, ToolFailure, ToolResult
from .tool_registry import (
    ToolRegistryError,
    ToolRegistrySnapshot,
    load_registry_snapshot_from_artifact,
)

ActivityHandler = Callable[
    [Step, Mapping[str, Any] | None],
    ToolResult | Awaitable[ToolResult],
]
SkillHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any] | None], ToolResult | Awaitable[ToolResult]
]

class ToolDispatchError(RuntimeError):
    """Raised when a tool invocation cannot be dispatched."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

@dataclass(slots=True)
class ToolActivityDispatcher:
    """Routes tool invocations based on activity type and tool key."""

    _activity_handlers: dict[str, ActivityHandler] = field(default_factory=dict)
    _skill_handlers: dict[tuple[str, str], SkillHandler] = field(default_factory=dict)
    _default_skill_handler: SkillHandler | None = None

    def register_activity(
        self, *, activity_type: str, handler: ActivityHandler
    ) -> None:
        normalized = str(activity_type or "").strip()
        if not normalized:
            raise ToolDispatchError(
                "invalid_dispatch", "activity_type must be non-empty"
            )
        self._activity_handlers[normalized] = handler

    def register_skill(
        self,
        *,
        skill_name: str,
        version: str,
        handler: SkillHandler,
    ) -> None:
        key = (str(skill_name or "").strip(), str(version or "").strip())
        if not key[0] or not key[1]:
            raise ToolDispatchError(
                "invalid_dispatch", "skill_name and version must be non-empty"
            )
        self._skill_handlers[key] = handler

    def register_default_skill_handler(self, *, handler: SkillHandler) -> None:
        """Register a generic fallback for mm.skill/mm.tool invocations.

        This is intended for runtime families where many skills share one generic
        execution surface (for example prompt-based CLI execution), while still
        allowing explicit per-skill handlers to override the fallback.
        """

        self._default_skill_handler = handler

    async def execute(
        self,
        *,
        invocation: Step,
        snapshot: ToolRegistrySnapshot,
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        definition = snapshot.get_tool(
            name=invocation.skill_name,
            version=invocation.skill_version,
        )
        activity_type = definition.executor.activity_type

        if activity_type in {"mm.tool.execute", "mm.skill.execute"}:
            handler = self._skill_handlers.get(invocation.skill_key)
            if handler is None:
                handler = self._default_skill_handler
            if handler is None:
                raise ToolDispatchError(
                    "tool_handler_not_registered",
                    "No "
                    f"{activity_type} handler registered for "
                    f"{invocation.skill_name}:{invocation.skill_version}",
                )
            result = handler(invocation.inputs, context)
        else:
            activity_handler = self._activity_handlers.get(activity_type)
            if activity_handler is None:
                raise ToolDispatchError(
                    "activity_handler_not_registered",
                    f"No handler registered for activity type {activity_type}",
                )
            result = activity_handler(invocation, context)

        if asyncio.iscoroutine(result):
            result = await result

        if not isinstance(result, ToolResult):
            raise ToolDispatchError(
                "invalid_skill_result",
                f"Handler returned unsupported result type: {type(result)!r}",
            )
        return result

async def execute_tool_activity(
    *,
    invocation_payload: Mapping[str, Any],
    registry_snapshot: ToolRegistrySnapshot,
    dispatcher: ToolActivityDispatcher,
    context: Mapping[str, Any] | None = None,
) -> ToolResult:
    """Execute one tool invocation payload through dispatcher semantics."""

    try:
        tool_payload = invocation_payload.get("tool")
        skill_payload = invocation_payload.get("skill")
        selected_payload: Mapping[str, Any] | None = None

        if isinstance(tool_payload, Mapping):
            tool_type = str(
                tool_payload.get("type") or tool_payload.get("kind") or "skill"
            ).strip()
            if tool_type and tool_type.lower() != "skill":
                raise ToolDispatchError(
                    "invalid_payload",
                    "tool.type must be 'skill' for the current runtime contract",
                )
            selected_payload = tool_payload
        elif isinstance(skill_payload, Mapping):
            selected_payload = skill_payload
        else:
            raise ToolDispatchError(
                "invalid_payload",
                "tool payload is required (skill is legacy alias)",
            )

        inputs = invocation_payload.get("inputs")
        if not isinstance(inputs, Mapping):
            inline_inputs = selected_payload.get("inputs")
            if not isinstance(inline_inputs, Mapping):
                inline_inputs = selected_payload.get("args")
            inputs = inline_inputs if isinstance(inline_inputs, Mapping) else {}

        invocation = Step(
            id=str(invocation_payload.get("id") or "").strip(),
            skill_name=str(
                selected_payload.get("name") or selected_payload.get("id") or ""
            ).strip(),
            skill_version=str(selected_payload.get("version") or "").strip(),
            inputs=inputs,
            options=(
                invocation_payload.get("options")
                if isinstance(invocation_payload.get("options"), Mapping)
                else {}
            ),
        )
        return await dispatcher.execute(
            invocation=invocation,
            snapshot=registry_snapshot,
            context=context,
        )
    except ToolFailure:
        raise
    except (ToolRegistryError, ToolDispatchError, ValueError) as exc:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=str(exc),
            retryable=False,
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise ToolFailure(
            error_code="INTERNAL",
            message=f"Unhandled tool execution error: {exc}",
            retryable=True,
        ) from exc

def plan_validate_activity(
    *,
    plan_artifact_ref: str,
    registry_snapshot_ref: str,
    artifact_store: ArtifactStore,
) -> dict[str, str]:
    """Deep validation activity: ``plan.validate``.

    Returns ``{"validated_plan_ref": <artifact_ref>}`` when validation succeeds.
    """

    snapshot = load_registry_snapshot_from_artifact(
        artifact_ref=registry_snapshot_ref,
        artifact_store=artifact_store,
    )
    plan_payload = artifact_store.get_json(plan_artifact_ref)
    if not isinstance(plan_payload, Mapping):
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message="Plan artifact payload must be a JSON object",
            retryable=False,
        )

    try:
        validated = validate_plan_payload(
            payload=plan_payload,
            registry_snapshot=snapshot,
        )
    except PlanValidationError as exc:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=str(exc),
            retryable=False,
            details={"validation_code": exc.code},
        ) from exc

    validated_artifact = artifact_store.put_json(
        validated.plan.to_payload(),
        metadata={
            "name": "validated_plan.json",
            "producer": "skill:plan.validate",
            "labels": ["plan", "validated"],
        },
    )

    return {"validated_plan_ref": validated_artifact.artifact_ref}

__all__ = [
    "ToolActivityDispatcher",
    "ToolDispatchError",
    "execute_tool_activity",
    "plan_validate_activity",
]
