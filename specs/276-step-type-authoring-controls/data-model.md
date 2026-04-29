# Data Model: Step Type Authoring Controls

## Step Draft

Represents one authored step in the Create page.

Fields relevant to this story:

- `stepType`: one of `skill`, `tool`, or `preset`.
- `instructions`: compatible freeform step instructions retained when Step Type changes.
- `skillId`: Skill-specific selector value; used only when `stepType = skill`.
- `skillArgs`: Skill-specific advanced JSON; used only when `stepType = skill` and advanced options are visible.
- `skillRequiredCapabilities`: Skill-specific advanced CSV; used only when `stepType = skill` and advanced options are visible.
- Existing attachment, provenance, template, story output, and Jira orchestration fields remain unchanged.

## Step Type

User-facing discriminator for an authored step.

Allowed values:

- `tool`: shows Tool-specific configuration area.
- `skill`: shows Skill-specific configuration area.
- `preset`: shows Preset-specific configuration area.

State transitions:

- Any Step Type can change to any other Step Type.
- Instructions persist across transitions.
- Hidden incompatible Skill-specific values are cleared or excluded before submission when leaving Skill.
- Preset application continues to expand through the existing preset expansion flow.
