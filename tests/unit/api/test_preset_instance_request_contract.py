"""Instance preset authoring rejects obsolete human routing keys."""

import pytest
from pydantic import ValidationError

from api_service.api.schemas import (
    PresetCreateRequestSchema,
    PresetExpandRequestSchema,
    PresetFavoriteRequestSchema,
    PresetReviewRequestSchema,
    PresetSaveFromWorkflowRequestSchema,
    PresetStepBlueprintSchema,
)


@pytest.mark.parametrize("key", ["scope", "scopeRef", "scope_ref"])
@pytest.mark.parametrize("schema,payload", [
    (PresetCreateRequestSchema, {"title": "Test", "description": "Test"}),
    (PresetSaveFromWorkflowRequestSchema, {"title": "Test", "description": "Test"}),
    (PresetExpandRequestSchema, {}),
    (PresetFavoriteRequestSchema, {}),
    (PresetReviewRequestSchema, {"releaseStatus": "active"}),
    (PresetStepBlueprintSchema, {"kind": "include", "slug": "test"}),
])
def test_legacy_human_preset_routing_is_not_silently_reinterpreted(schema, payload, key):
    with pytest.raises(ValidationError, match="instance catalog"):
        schema.model_validate({**payload, key: "personal"})


@pytest.mark.parametrize("schema", [PresetCreateRequestSchema, PresetSaveFromWorkflowRequestSchema])
def test_publication_intent_is_explicit_and_defaults_to_draft(schema):
    payload = {"title": "Test", "description": "Test"}
    assert schema.model_validate(payload).release_status == "draft"
    assert schema.model_validate({**payload, "releaseStatus": "active"}).release_status == "active"
    with pytest.raises(ValidationError):
        schema.model_validate({**payload, "releaseStatus": "published"})


def test_instance_expansion_preserves_false_empty_and_null_values():
    inputs = {"flag": False, "empty": "", "selection": None, "items": []}
    parsed = PresetExpandRequestSchema.model_validate({"inputs": inputs, "version": "legacy-ignored"})
    assert parsed.inputs == inputs
    assert "version" not in parsed.model_dump()
