# UI Contract: Create Page Preset Preview and Apply

## Preset Authoring Area

When `Step Type = Preset`, the step editor shows:

- Preset selector.
- Preview action.
- Apply action enabled only after a current successful preview.
- Status/error text.

## Preview Behavior

Preview calls the existing preset expansion path using the selected preset version, normalized inputs, repository context, runtime context, and enforced step limits.

On success, the UI shows:

- Generated step count.
- Each generated step title.
- Each generated step Step Type.
- Expansion warnings, if returned.

Preview must not mutate the task draft's step list.

On failure, the UI shows the failure and leaves the draft unchanged.

## Apply Behavior

Apply uses the latest successful preview for that Preset step.

- The selected temporary Preset step is replaced by generated Tool and/or Skill steps.
- Generated steps remain editable through ordinary step controls.
- Source provenance from expansion is preserved when available.
- Applying without a current preview first triggers preview or is blocked with visible guidance.

## Submission Behavior

Submitting a task with any unresolved `Step Type = Preset` step is blocked unless a future linked-preset mode is explicitly present. This story does not add linked-preset mode.
