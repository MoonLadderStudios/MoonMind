# Data Model: Submit Discriminated Executable Payloads

## Executable Step

Represents a submitted task step that can enter runtime materialization.

Fields:
- `id`: optional stable local identity.
- `title`: optional display title.
- `instructions`: optional step instructions.
- `type`: optional during migration, but when present must be `tool` or `skill` for executable submission.
- `tool`: required for explicit Tool steps; contains `id` or `name`, optional `version`, and `inputs`.
- `skill`: required for explicit Skill steps when the step selects a skill directly; contains `id` and `args`.
- `source`: optional provenance metadata for audit and reconstruction.

Validation:
- `type: "tool"` requires a Tool sub-payload and forbids a Skill sub-payload.
- `type: "skill"` forbids a Tool sub-payload unless it is the legacy mirrored `tool.type: "skill"` selector currently used by the runtime.
- `type: "preset"` is not executable and is rejected at submission.
- `type: "activity"` and any other values are rejected.

## Preset Step

Represents an authoring-time placeholder.

Fields:
- `type: "preset"`
- `preset`: preset id/version/inputs

Validation:
- Valid only for preview/apply authoring flows.
- Rejected from executable task submission.

## Step Provenance

Represents optional audit metadata.

Fields:
- `kind`
- `presetId` or `presetSlug`
- `version`
- `includePath`
- `originalStepId`
- existing `presetProvenance` expansion metadata

Validation:
- Preserved when present.
- Not required for runtime materialization.
