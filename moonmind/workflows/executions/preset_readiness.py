"""Compact saved-preset requirements carried across the planning boundary."""

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SAVED_PRESET_CAPABILITY_READINESS_PATCH = "run-saved-preset-capability-readiness-v1"

GITHUB_ISSUE_SEARCH_PRESET_SLUG = "github-issue-search-and-implement"
GITHUB_ISSUE_SEARCH_SCOPE_INPUT = "include_all_authors"


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
    task = parameters.get("workflow")
    if not isinstance(task, Mapping):
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


def _applied_search_templates_missing_scope(
    node: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Collect search-preset applications without an explicit scope choice.

    Inspects one ``appliedStepTemplates`` entry (or nested composition child)
    for the GitHub issue-search preset. Entries whose ``inputs`` predate the
    ``include_all_authors`` setting carry no scope choice: launching a new
    execution from them would silently reinterpret the saved all-author
    behavior as the new self-only default, so they require a refresh.
    """
    missing: list[dict[str, str]] = []
    slug = str(node.get("slug") or "").strip()
    scope = str(node.get("scope") or "global").strip() or "global"
    if slug == GITHUB_ISSUE_SEARCH_PRESET_SLUG:
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping) or (
            GITHUB_ISSUE_SEARCH_SCOPE_INPUT not in inputs
            and "includeAllAuthors" not in inputs
        ):
            missing.append({"slug": slug, "scope": scope})
    composition = node.get("composition")
    if isinstance(composition, Mapping):
        for child in composition.get("includes") or []:
            if isinstance(child, Mapping):
                missing.extend(_applied_search_templates_missing_scope(child))
    for child in node.get("includes") or []:
        if isinstance(child, Mapping):
            missing.extend(_applied_search_templates_missing_scope(child))
    return missing


def github_issue_search_scope_refresh_needed(
    parameters: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a refresh-required payload for pre-change frozen search schedules.

    Pure function of the saved parameters: deterministic and safe to evaluate
    inside workflow code. Returns ``None`` when no frozen GitHub issue-search
    application lacks the author-scope choice (fresh authoring, already
    refreshed schedules, other presets, and continued/historical runs are
    unaffected — historical versioning is enforced by the caller's patch and
    continuation guards, not here).
    """
    task = parameters.get("workflow")
    if not isinstance(task, Mapping):
        task = parameters.get("task")
    if not isinstance(task, Mapping):
        return None
    applied = task.get("appliedStepTemplates")
    if isinstance(applied, str):
        applied = task.get("applied_step_templates")
    if not isinstance(applied, Sequence) or isinstance(applied, (str, bytes)):
        return None
    missing: list[dict[str, str]] = []
    for template in applied:
        if not isinstance(template, Mapping):
            continue
        composition = template.get("composition")
        if isinstance(composition, Mapping) and composition:
            missing.extend(_applied_search_templates_missing_scope(composition))
        else:
            missing.extend(_applied_search_templates_missing_scope(template))
    if not missing:
        return None
    system = parameters.get("system")
    recurrence = system.get("recurrence") if isinstance(system, Mapping) else None
    definition_id = (
        str(recurrence.get("definitionId") or "").strip()
        if isinstance(recurrence, Mapping)
        else ""
    )
    detail = ", ".join(
        f"{entry['slug']} ({entry['scope']})" if entry.get("scope") else entry["slug"]
        for entry in missing
    )
    message = (
        "Saved schedule requires a plan refresh: the GitHub issue search plan "
        f"({detail}) was saved before the self-authored default and has no "
        "author-scope choice. In Workflow Create, reapply the listed preset — "
        'leaving "Include issues created by other users" unchecked keeps the '
        "new self-only default — and save a replacement schedule with the same "
        "repository, runtime, model, effort, and cadence; then retire the old "
        "schedule. Restarting workers does not refresh saved requirements."
    )
    payload: dict[str, Any] = {
        "status": "refresh_required",
        "code": "github_issue_search_author_scope_stale",
        "missingInputs": [GITHUB_ISSUE_SEARCH_SCOPE_INPUT],
        "presets": [
            {**entry, "reason": "author_scope_choice_missing"} for entry in missing
        ],
        "message": message,
    }
    if definition_id:
        payload["definitionId"] = definition_id
    return payload
