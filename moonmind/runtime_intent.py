"""Validation helpers for authored workflow runtime intent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MODEL_TIER_KEY = "modelTier"
TIER_FALLBACK_KEY = "tierFallback"
HARD_OVERRIDE_AUDIT_KEY = "hardOverrideAudit"
TIER_PREVIEW_KEY = "tierPreview"
SUPPORTED_TIER_FALLBACKS = frozenset({"clamp", "strict"})


# MoonLadderStudios/MoonMind#4636: presence is part of authored intent.
MODEL_SELECTION_KEYS = ("modelTier", "model", "effort", "tierFallback")
SELECTION_DERIVED_KEYS = ("tierPreview", "hardOverrideAudit", "modelTierResolution")
_SELECTION_CONTAINERS = frozenset(
    {
        "runtime",
        "workflow",
        "task",
        "skill",
        "steps",
        "initialParameters",
        "initial_parameters",
        "payload",
        "authoredTaskInput",
        "authoredWorkflowInput",
        "parametersPatch",
        "parameters_patch",
        "draft",
    }
)


def model_selection_fields(runtime: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        key: runtime[key]
        for key in MODEL_SELECTION_KEYS
        if runtime is not None and key in runtime
    }


def is_custom_selection(runtime: Mapping[str, Any] | None) -> bool:
    return bool(
        runtime is not None
        and "modelTier" not in runtime
        and "model" in runtime
        and "effort" in runtime
    )


def merge_runtime_selection(
    parent: Mapping[str, Any], child: Mapping[str, Any]
) -> dict[str, Any]:
    """Replace selection as a unit while retaining unrelated runtime parameters.

    Partial legacy overrides retain field-by-field inheritance. A numbered tier
    and a complete nullable Custom pair each replace the parent's selection.
    Resolved snapshots remain diagnostics, never an authored companion value.
    """
    merged = dict(parent)
    if "modelTier" in child or is_custom_selection(child):
        for key in (
            *MODEL_SELECTION_KEYS,
            *SELECTION_DERIVED_KEYS,
            "requestedModel",
            "resolvedModel",
            "resolvedEffort",
            "modelSource",
            "effortSource",
        ):
            merged.pop(key, None)
    merged.update(child)
    if isinstance(parent.get("parameters"), Mapping) and isinstance(
        child.get("parameters"), Mapping
    ):
        merged["parameters"] = {**parent["parameters"], **child["parameters"]}
    return merged


def validate_model_selection_authoring(
    payload: Any,
    *,
    saved_payload: Any = None,
    field_name: str = "payload",
) -> None:
    """Reject new strict policy; saved_payload must come from a server record.

    This is used at mutation boundaries, separately from structural readers of
    accepted histories. Comparing field presence prevents a copied legacy flag
    or a changed ordinal/override from authorizing a new strict selection.
    """
    if isinstance(payload, Mapping):
        if payload.get("tierFallback") == "strict":
            if not isinstance(saved_payload, Mapping) or model_selection_fields(
                payload
            ) != model_selection_fields(saved_payload):
                raise RuntimeIntentValidationError(
                    f"{field_name}.tierFallback: new strict selections are no longer supported. "
                    "Choose a configured tier or Custom; unchanged saved strict requests remain supported."
                )
        for key, value in payload.items():
            if key in _SELECTION_CONTAINERS:
                saved = (
                    saved_payload.get(key)
                    if isinstance(saved_payload, Mapping)
                    else None
                )
                if (
                    saved is None
                    and key == "initial_parameters"
                    and isinstance(saved_payload, Mapping)
                ):
                    saved = saved_payload.get("initialParameters")
                validate_model_selection_authoring(
                    value, saved_payload=saved, field_name=f"{field_name}.{key}"
                )
    elif isinstance(payload, list):
        # Match stable step identities; moving a saved step must not change its intent.
        saved_by_id = (
            {
                item.get("id"): item
                for item in saved_payload
                if isinstance(item, Mapping) and item.get("id")
            }
            if isinstance(saved_payload, list)
            else {}
        )
        for index, value in enumerate(payload):
            saved = (
                saved_by_id.get(value.get("id"))
                if isinstance(value, Mapping) and value.get("id")
                else (
                    saved_payload[index]
                    if isinstance(saved_payload, list) and index < len(saved_payload)
                    else None
                )
            )
            validate_model_selection_authoring(
                value, saved_payload=saved, field_name=f"{field_name}[{index}]"
            )


async def validate_model_selection_submission(
    payload: Any,
    *,
    read_input_artifact: Callable[[str], Awaitable[Any]],
    saved_payload: Any = None,
    field_name: str = "payload",
) -> None:
    """Validate inline and artifact-backed intent before a public mutation.

    Artifact references already accepted in a server-loaded record retain their
    authority. New references are read with the caller's existing artifact access;
    neither possession of a ref nor an asserted legacy flag proves saved intent.
    """
    cache: dict[str, Any] = {}

    async def read(ref: str) -> Any:
        if ref not in cache:
            cache[ref] = await read_input_artifact(ref)
        return cache[ref]

    async def saved_context(saved: Any) -> Any:
        if not isinstance(saved, Mapping):
            return saved
        ref = saved.get("inputArtifactRef") or saved.get("input_artifact_ref")
        if not isinstance(ref, str) or not ref.strip():
            return saved
        artifact = await read(ref)
        if not isinstance(artifact, Mapping):
            return saved
        artifact = artifact.get("draft", artifact)
        if not isinstance(artifact, Mapping):
            return saved
        parameters_key = next(
            (
                key
                for key in ("initialParameters", "initial_parameters")
                if isinstance(saved.get(key), Mapping)
            ),
            None,
        )
        parameters = saved[parameters_key] if parameters_key else saved
        previous = {**artifact, **parameters}
        from moonmind.workflows.executions.execution_contract import (
            merge_workflow_input,
        )

        for key, alternative in (("workflow", "task"), ("task", "workflow")):
            original = artifact.get(key)
            replacement = parameters.get(key, parameters.get(alternative, {}))
            if isinstance(original, Mapping) and isinstance(replacement, Mapping):
                previous[key] = merge_workflow_input(
                    original, replacement, runtime_selection_is_complete=False
                )
        return {**saved, parameters_key: previous} if parameters_key else previous

    try:
        validate_model_selection_authoring(
            payload, saved_payload=saved_payload, field_name=field_name
        )
    except RuntimeIntentValidationError:
        # Artifact-only legacy input is also server-saved provenance. Merge its
        # source with current authored fields so superseded policy cannot revive.
        saved_payload = await saved_context(saved_payload)
        validate_model_selection_authoring(
            payload, saved_payload=saved_payload, field_name=field_name
        )

    async def visit(value: Any, saved: Any, path: str) -> None:
        if isinstance(value, Mapping):
            ref = value.get("inputArtifactRef") or value.get("input_artifact_ref")
            saved_ref = (
                (saved.get("inputArtifactRef") or saved.get("input_artifact_ref"))
                if isinstance(saved, Mapping)
                else None
            )
            if isinstance(ref, str) and ref.strip() and ref != saved_ref:
                artifact = await read(ref)
                if isinstance(artifact, Mapping):
                    artifact = artifact.get("draft", artifact)
                try:
                    validate_model_selection_authoring(
                        artifact, field_name=f"{path}.inputArtifactRef"
                    )
                except RuntimeIntentValidationError:
                    # Only retained strict intent needs the superseded source.
                    # A valid replacement must be able to repair a missing ref.
                    previous = await saved_context(saved)
                    if isinstance(previous, Mapping):
                        previous = previous.get(
                            "initialParameters",
                            previous.get("initial_parameters", previous),
                        )
                    validate_model_selection_authoring(
                        artifact,
                        saved_payload=previous,
                        field_name=f"{path}.inputArtifactRef",
                    )
            for key, child in value.items():
                if key in _SELECTION_CONTAINERS:
                    saved_child = saved.get(key) if isinstance(saved, Mapping) else None
                    await visit(child, saved_child, f"{path}.{key}")
        elif isinstance(value, list):
            saved_by_id = (
                {
                    item.get("id"): item
                    for item in saved
                    if isinstance(item, Mapping) and item.get("id")
                }
                if isinstance(saved, list)
                else {}
            )
            for index, child in enumerate(value):
                saved_child = (
                    saved_by_id.get(child.get("id"))
                    if isinstance(child, Mapping) and child.get("id")
                    else (
                        saved[index]
                        if isinstance(saved, list) and index < len(saved)
                        else None
                    )
                )
                await visit(child, saved_child, f"{path}[{index}]")

    await visit(payload, saved_payload, field_name)


class RuntimeIntentValidationError(ValueError):
    """Raised when authored runtime tier intent is invalid."""


class RuntimeTierPreview(BaseModel):
    """Shape-validated advisory Provider Profile snapshot.

    See MoonLadderStudios/MoonMind#3798.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile_id: str = Field(..., alias="profileId")
    profile_version: int | str = Field(..., alias="profileVersion")
    model: str | None = Field(..., alias="model")
    effort: str | None = Field(..., alias="effort")
    requested_tier: int | None = Field(None, alias="requestedTier")
    effective_tier: int | None = Field(None, alias="effectiveTier")
    fallback_reason: str | None = Field(None, alias="fallbackReason")

    @field_validator("profile_id", mode="before")
    @classmethod
    def _validate_profile_id(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()

    @field_validator("profile_version", mode="before")
    @classmethod
    def _validate_profile_version(cls, value: object) -> int | str:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("must be a positive integer or non-empty string")
        if isinstance(value, int):
            if value < 1:
                raise ValueError("must be a positive integer or non-empty string")
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be a positive integer or non-empty string")
        return normalized

    @field_validator("model", "effort", mode="before")
    @classmethod
    def _validate_optional_resolution_string(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be null or a non-empty string")
        return value.strip()

    @field_validator("requested_tier", "effective_tier", mode="before")
    @classmethod
    def _validate_optional_tier(cls, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("must be null or an integer greater than or equal to 1")
        return value

    @field_validator("fallback_reason", mode="before")
    @classmethod
    def _validate_optional_fallback_reason(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be null or a non-empty string")
        return value.strip()


def _preview_validation_message(
    *,
    field_name: str,
    error: ValidationError,
) -> str:
    detail = error.errors(include_url=False)[0]
    location = ".".join(str(part) for part in detail.get("loc", ()))
    message = str(detail.get("msg") or "is invalid")
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    path = f"{field_name}.{TIER_PREVIEW_KEY}"
    if location:
        path = f"{path}.{location}"
    return f"{path} {message}."


def validate_runtime_tier_intent(
    runtime: Mapping[str, Any] | None,
    *,
    field_name: str,
) -> dict[str, Any]:
    """Return a copied runtime payload after validating tier intent fields.

    MM-1171 implements the preset/workflow submission boundary from MM-1168's
    provider profile tier design. Generic runtime metadata remains open-ended,
    but modelTier and tierFallback are now explicit contract fields.
    """

    payload = dict(runtime or {})
    for key in ("model", "effort"):
        if (
            key in payload
            and payload[key] is not None
            and not isinstance(payload[key], str)
        ):
            raise RuntimeIntentValidationError(
                f"{field_name}.{key} must be a string or null."
            )
    if MODEL_TIER_KEY in payload:
        model_tier = payload[MODEL_TIER_KEY]
        if isinstance(model_tier, bool) or not isinstance(model_tier, int):
            raise RuntimeIntentValidationError(
                f"{field_name}.modelTier must be an integer."
            )
        if model_tier < 1:
            raise RuntimeIntentValidationError(
                f"{field_name}.modelTier must be greater than or equal to 1."
            )
    if TIER_FALLBACK_KEY in payload:
        tier_fallback = payload[TIER_FALLBACK_KEY]
        if tier_fallback not in SUPPORTED_TIER_FALLBACKS:
            supported = ", ".join(sorted(SUPPORTED_TIER_FALLBACKS))
            raise RuntimeIntentValidationError(
                f"{field_name}.tierFallback must be one of: {supported}."
            )
    if TIER_PREVIEW_KEY in payload:
        try:
            preview = RuntimeTierPreview.model_validate(payload[TIER_PREVIEW_KEY])
        except ValidationError as exc:
            raise RuntimeIntentValidationError(
                _preview_validation_message(field_name=field_name, error=exc)
            ) from exc
        payload[TIER_PREVIEW_KEY] = preview.model_dump(
            mode="json",
            by_alias=True,
            exclude_unset=True,
        )
    if HARD_OVERRIDE_AUDIT_KEY in payload:
        hard_override_audit = payload[HARD_OVERRIDE_AUDIT_KEY]
        if not isinstance(hard_override_audit, Mapping) or not hard_override_audit:
            raise RuntimeIntentValidationError(
                f"{field_name}.hardOverrideAudit must be a non-empty object."
            )
    if MODEL_TIER_KEY in payload:
        override_fields = [
            key for key in ("model", "effort") if payload.get(key) is not None
        ]
        if override_fields and HARD_OVERRIDE_AUDIT_KEY not in payload:
            payload[HARD_OVERRIDE_AUDIT_KEY] = {
                "source": "runtime_metadata",
                "fields": override_fields,
                "trace": ["MM-1168", "MM-1171"],
            }
    return payload
