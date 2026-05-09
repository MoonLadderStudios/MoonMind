# Data Model: Preview and Apply Preset Steps

## Preset Step Draft

Temporary authoring state for a step whose Step Type is `Preset`.

Fields:

- `stepType`: normalized value `preset`
- `presetId`: selected preset/capability identifier
- `version`: selected active or previewable preset version
- `inputs`: schema-validated preset input values
- `previewState`: optional current preview result or stale/error state

Validation:

- `presetId` must resolve to an existing preset.
- `version` must be active or explicitly previewable.
- `inputs` must satisfy the selected preset input schema.
- Stale preview data must not be applied after `presetId`, `version`, or `inputs` change.

## Preset Expansion Preview

Deterministic generated step list plus warnings or errors shown before application.

Fields:

- `generatedSteps`: ordered Tool and/or Skill step previews with titles and Step Types
- `warnings`: visible non-blocking expansion warnings
- `errors`: blocking expansion or validation errors
- `sourcePreset`: preset identity/version and input snapshot used for expansion

Validation:

- Generated steps must satisfy their Tool or Skill contracts before application succeeds.
- Policy and step limits must be enforced before generated steps are inserted into the draft.

## Preset-Derived Executable Step

Concrete Tool or Skill step inserted by applying a valid preset expansion.

Fields:

- `stepType`: `tool` or `skill`
- `title`: editable generated title
- `tool` or `skill`: executable binding and inputs
- `provenance`: preset source metadata when available

Validation:

- The step validates under its own Tool or Skill rules before executable submission.
- Provenance is audit/reconstruction metadata, not hidden runtime work.

## Submission Boundary

Executable submission state derived from the authored draft.

Rules:

- Unresolved Preset steps must be expanded or rejected before runtime execution.
- Generated Tool and Skill steps submit as executable steps.
- Runtime workflows must not execute unresolved Preset steps by catalog lookup unless a separate linked-preset mode is explicitly introduced.
