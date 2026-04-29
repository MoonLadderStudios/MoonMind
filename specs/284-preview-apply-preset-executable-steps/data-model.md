# Data Model: Preview and Apply Preset Steps Into Executable Steps

## Preset Step Draft

- `localId`: UI-local stable identifier for the draft step.
- `stepType`: `preset` while the step is unresolved.
- `presetKey`: selected preset identity and scope.
- `presetInputs`: author-provided preset input values.
- `presetPreview`: optional deterministic expansion preview for the current selected preset/input state.

Validation rules:

- A Preset Step Draft without a selected preset cannot be previewed or applied.
- A Preset Step Draft must not be submitted as executable work by default.
- Changing the selected preset or inputs invalidates stale preview output.

## Preset Expansion Preview

- `previewSteps`: ordered generated step summaries with title and Step Type.
- `warnings`: ordered warning messages returned by expansion.
- `expandedSteps`: concrete generated step payloads retained for apply.

Validation rules:

- Preview is produced by the authoritative preset expansion path.
- Preview must be visible before apply.
- Failed preview does not mutate the draft.

## Preset-Derived Executable Step

- `type`: `tool` or `skill`.
- `title`: generated title.
- `instructions`: generated editable instructions.
- `tool` or `skill`: executable binding for the generated step type.
- `source`: optional preset provenance metadata.

Validation rules:

- Generated steps validate under their own Tool or Skill rules.
- Generated steps are editable after application.
- Executable submission contains generated Tool/Skill steps, not unresolved Preset placeholders.

## Preset Provenance

- `kind`: `preset-derived`.
- `presetId`: source preset identity when available.
- `presetVersion`: source version when available.
- `includePath`: optional nested source path.
- `originalStepId`: optional original source step identifier.

Usage rules:

- Provenance supports audit, review, reconstruction, and explicit update flows.
- Provenance must not be required for runtime correctness.
