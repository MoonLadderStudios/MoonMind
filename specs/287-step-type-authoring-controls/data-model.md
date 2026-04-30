# Data Model: Add Step Type Authoring Controls

## Step Draft

Represents one authored step in the Create page draft.

Fields relevant to MM-568:

- `stepType`: one of `skill`, `tool`, or `preset`.
- `instructions`: shared step instructions preserved across Step Type changes.
- Skill-specific state: selected skill, optional skill args, optional required capabilities.
- Tool-specific state: selected tool id, optional version, JSON inputs.
- Preset-specific state: selected preset key, input values, loaded detail, preview, reapply state.
- `stepTypeMessage`: transient user-visible feedback for Step Type changes.

## State Transitions

- Changing to the same Step Type leaves the draft unchanged.
- Changing from Skill to Tool or Preset preserves shared instructions and discards meaningful Skill-specific state with visible feedback.
- Changing from Tool to Skill or Preset preserves shared instructions and discards meaningful Tool-specific state with visible feedback.
- Changing from Preset to Tool or Skill preserves shared instructions and discards meaningful Preset-specific state, preview, and loaded detail with visible feedback.

## Validation Rules

- Exactly one Step Type is selected for each authored step.
- Only the selected Step Type's configuration form is visible.
- Incompatible hidden type-specific values are not treated as active submission data after a Step Type change.
