"""Compact saved-preset requirements carried across the planning boundary."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SAVED_PRESET_CAPABILITY_READINESS_PATCH = "run-saved-preset-capability-readiness-v1"


class SavedPresetCapabilitiesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: str = Field(min_length=1)
    definition_id: str = Field(min_length=1)
    presets: list[dict[str, str]]
    required_capabilities: list[str] = Field(default_factory=list)


def saved_preset_capability_check(
    parameters: Mapping[str, Any], *, principal: str
) -> SavedPresetCapabilitiesInput | None:
    """Project provenance only; never copy prompts, inputs, or runtime secrets."""
    system = parameters.get("system") or {}
    recurrence = system.get("recurrence") or {}
    definition_id = recurrence.get("definitionId")
    if not definition_id:
        return None
    task = parameters.get("task") or {}
    presets: list[dict[str, str]] = []

    def add(node: Mapping[str, Any], scope: str = "global") -> None:
        scope = str(node.get("scope") or scope)
        slug = node.get("slug")
        if slug:
            entry = {"slug": str(slug), "scope": scope}
            if node.get("scopeRef"):
                entry["scopeRef"] = str(node["scopeRef"])
            if entry not in presets:
                presets.append(entry)
        for child in node.get("includes") or []:
            add(child, scope)

    for applied in task.get("appliedStepTemplates") or []:
        composition = applied.get("composition")
        add(
            composition if isinstance(composition, Mapping) and composition else applied
        )
    if not presets:
        return None
    return SavedPresetCapabilitiesInput(
        principal=principal,
        definition_id=str(definition_id),
        presets=presets,
        required_capabilities=parameters.get("requiredCapabilities") or [],
    )
