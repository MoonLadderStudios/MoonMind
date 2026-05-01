# Data Model: Preview and Apply Preset Steps

## Preset Step Draft

- `stepType`: `preset` while the user is configuring the temporary authoring placeholder.
- `preset`: selected preset scope, slug, version, and configured input values.
- Validation: must reference an existing active or previewable preset and schema-valid input values before preview/apply succeeds.
- State transition: `draft preset` -> `preview available` -> `applied executable steps`.

## Preset Expansion Preview

- `steps`: generated step list with titles, Step Types, instructions, executable Tool/Skill payloads, and optional source metadata.
- `warnings`: visible author-facing warnings returned by expansion.
- `errors`: visible failure state when expansion or generated-step validation fails.
- Validation: preview must be current for the selected preset and inputs before apply.

## Preset-Derived Executable Step

- `stepType`: concrete `tool` or `skill` after apply.
- `source`: available preset provenance metadata for audit/reconstruction.
- Validation: generated steps validate under their own Tool or Skill rules before executable submission.

## State Rules

1. Changing selected preset or inputs invalidates stale preview state.
2. Failed preview leaves the draft unchanged.
3. Applying a valid preview replaces the temporary Preset step with concrete executable steps.
4. Unresolved Preset steps are rejected at submission by default.
