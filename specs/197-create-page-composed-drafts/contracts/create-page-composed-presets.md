# Contract: Create Page Composed Presets

## Scope

This contract defines the desired Create page behavior for composed preset draft authoring. It is a product/UI contract, not an executable API schema.

## Required Behaviors

- Preset selection alone is preview-only and does not mutate the draft.
- Preset application is explicit and server-expanded.
- Successful apply returns:
  - applied preset binding metadata,
  - grouped composition metadata,
  - flattened execution-facing steps,
  - per-step source/provenance metadata,
  - expansion digest.
- The browser draft stores applied composition through `AppliedPresetBinding`.
- Each expanded step stores source state through `StepDraft.source`.
- Grouped preview and flattened execution order are both visible or derivable.
- Manual edits detach affected step source relationships without deleting authored content.
- Reapply discloses still-bound updates and detached skips before confirmation.
- Save-as-preset preserves intact composition by default.
- Flatten-before-save is an explicit advanced action.
- Edit/rerun reconstruction preserves binding state when recoverable.
- Flat-only reconstruction shows a warning and never claims live binding state.
- Runtime submission remains flattened and does not carry nested preset execution semantics.

## Validation Expectations

- Documentation or UI tests must cover preview, apply, non-mutating selection, detachment, reapply disclosure, save-as-preset preservation, edit/rerun reconstruction, and degraded fallback.
- The Create page contract must use preset-bound terminology for composed preset state.
- Legacy `template-bound`, `appliedTemplates`, and `AppliedTemplateState` terms must not describe composed preset draft state in `docs/UI/CreatePage.md`.
