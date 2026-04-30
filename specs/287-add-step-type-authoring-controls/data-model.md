# Data Model: Add Step Type Authoring Controls

## Step Draft

A user-authored task step in the Create page.

Fields relevant to MM-568:

- `localId`: Stable client-side identity for editing and rendering.
- `instructions`: Compatible cross-type text preserved when Step Type changes.
- `stepType`: User-facing discriminator with values `skill`, `tool`, or `preset`.
- `skillId`: Skill-specific selection, active only when `stepType` is `skill`.
- `skillArgs`: Skill-specific JSON input text, active only when `stepType` is `skill`.
- `stepSkillRequiredCapabilities`: Skill-specific capability list, active only when `stepType` is `skill`.
- `toolName`: Tool-specific identifier, active only when `stepType` is `tool`.
- `toolVersion`: Optional Tool version, active only when `stepType` is `tool`.
- `toolInputs`: Tool-specific JSON input text, active only when `stepType` is `tool`.
- `presetKey`: Preset-specific selection, active only when `stepType` is `preset`.
- `presetInputValues`: Preset-specific input values, active only when `stepType` is `preset`.
- `presetPreview`: Preset-specific preview result, active only when `stepType` is `preset`.

Validation rules:

- Every authored step has exactly one `stepType`.
- `skill` steps submit Skill configuration and do not submit hidden Tool or Preset configuration as active state.
- `tool` steps require a selected governed Tool before submission and do not submit hidden Skill configuration as active state.
- `preset` steps must be previewed and applied before executable submission.
- `instructions` remain available across Step Type changes.

## Step Type

The user-facing discriminator for authoring a step.

Allowed values:

- `skill`: Ask an agent to perform work using reusable behavior.
- `tool`: Run a typed integration or system operation directly.
- `preset`: Insert a reusable set of configured steps.

State transitions:

- `skill -> tool`: Preserve instructions; hide Skill controls; require Tool selection before submission.
- `skill -> preset`: Preserve instructions; hide Skill controls; show Preset selection and preview/apply controls.
- `tool -> skill`: Preserve instructions; show Skill controls.
- `tool -> preset`: Preserve instructions; show Preset controls.
- `preset -> skill` or `preset -> tool`: Preserve instructions; hide Preset controls and preview state.

## Type-Specific Configuration

Controls displayed below the Step Type selector.

Rules:

- Only the selected Step Type's configuration controls are visible.
- Hidden incompatible values may remain in draft state for non-destructive editing, but they must not be submitted as active configuration for another Step Type.
- Preset state is scoped per Step Draft and must not leak between steps.
