# Data Model: Jira Preset Reapply Signaling

## PresetInstructionsDraft

Represents the current preset Feature Request / Initial Instructions field.

Fields:

- `text`: Current draft text in the preset instructions field.
- `lastAppliedText`: Preset instructions value used by the most recent explicit preset application.
- `hasAppliedPreset`: Whether at least one preset has been explicitly applied to the current draft.

Validation rules:

- Reapply-needed state can only be true when `hasAppliedPreset` is true.
- Jira import that leaves `text` effectively unchanged must not create reapply-needed state.
- Restoring `text` to `lastAppliedText` clears reapply-needed state.

State transitions:

- No applied preset -> Jira import changes `text` without reapply-needed state.
- Applied preset -> Jira import changes `text` to a value different from `lastAppliedText` and enters reapply-needed state.
- Reapply-needed -> operator explicitly reapplies preset and updates `lastAppliedText`.
- Reapply-needed -> operator restores `text` to `lastAppliedText` and clears reapply-needed state.

## AppliedPresetExpansion

Represents steps that already exist in the draft because the operator explicitly applied a preset.

Fields:

- `appliedTemplates`: Existing applied preset records for the draft.
- `expandedSteps`: Current visible step list after preset application and any subsequent manual edits.
- `statusMessage`: Non-blocking message shown in the preset section.

Validation rules:

- Jira import into preset instructions must not regenerate, replace, reorder, or remove `expandedSteps`.
- Reapply is an explicit operator action using the existing preset application flow.
- The reapply-needed message must be non-blocking; manual editing and Create remain available.

## StepDraft

Represents one Create page step.

Fields:

- `localId`: Client-local step identity used by Jira import targets.
- `instructions`: Current authored step instructions.
- `id`: Current step identity included in the task draft when still template-bound.
- `templateStepId`: Original template step identity from preset expansion.
- `templateInstructions`: Original template instructions from preset expansion.

Validation rules:

- A step is template-bound by instruction identity only when `id` equals `templateStepId` and `instructions` equals `templateInstructions`.
- Jira import into a missing `localId` must not update any other step.
- Jira import into a step must update only that step.

State transitions:

- Template-bound step -> Jira browser opened for that step and warning is visible.
- Template-bound step -> Jira import changes `instructions` and detaches template-bound instruction identity.
- Already-customized step -> Jira browser opened for that step and no template-bound warning is shown.

## JiraImportTarget

Represents where selected Jira text will be imported.

Variants:

- `preset`: The preset instructions field.
- `step`: A specific step instructions field identified by `localId`.

Validation rules:

- The browser has at most one active import target.
- Opening from preset preselects the preset target.
- Opening from a step preselects that step target.
- Switching or closing the browser without importing must not change draft text, reapply state, or template identity.

## ReapplyNeededState

Represents the transient UI state that preset-derived steps may be stale relative to current preset instructions.

Fields:

- `required`: Whether reapply is currently needed.
- `message`: The exact operator-facing message.
- `actionLabel`: The preset action label shown while reapply is needed.

Validation rules:

- `message` must be "Preset instructions changed. Reapply the preset to regenerate preset-derived steps." when `required` is true.
- `actionLabel` must clearly communicate reapply while `required` is true.
- `required` must not block manual editing, Jira browsing, or task creation.
