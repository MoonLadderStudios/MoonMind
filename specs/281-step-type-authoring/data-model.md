# Data Model: Present Step Type Authoring

## Step Draft

Represents one user-authored task step in the Create page.

Fields relevant to this story:

- `localId`: stable local identity for the draft step.
- `instructions`: compatible freeform instructions retained across Step Type changes.
- `stepType`: selected Step Type value.
- `skillId`, `skillArgs`, `skillRequiredCapabilities`: Skill-specific draft values.
- `presetKey`, `presetPreview`: Preset-specific draft values.

## Step Type

User-facing discriminator for step authoring.

Allowed values:

- `tool`
- `skill`
- `preset`

Display labels:

- Tool
- Skill
- Preset

Validation rules:

- Every authored step has exactly one selected Step Type.
- Tool, Skill, and Preset each expose concise helper copy.
- The selected Step Type controls the visible type-specific controls.
- Hidden type-specific values may remain in draft state for recovery, but they are not submitted as active configuration for unrelated Step Types.

## Type-Specific Controls

Visible controls determined by `stepType`.

- Tool: typed-operation controls or placeholder validation surface.
- Skill: skill selector and Skill advanced fields.
- Preset: preset selection, preview, and apply controls.
