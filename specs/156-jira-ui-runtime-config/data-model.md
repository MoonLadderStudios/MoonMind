# Data Model: Jira UI Runtime Config

This feature does not add database entities. It defines boot-time runtime configuration models derived from operator settings.

## Jira UI Rollout

Represents whether Jira browser discovery is available to the Create page.

Fields:

- `enabled`: boolean. Defaults to `false`.
- `defaultProjectKey`: string. Defaults to empty string. Normalized to uppercase project-key format when provided.
- `defaultBoardId`: string. Defaults to empty string. Trimmed when provided.
- `rememberLastBoardInSession`: boolean. Defaults to `true`.

Validation rules:

- Jira UI rollout is independent from backend Jira tool enablement.
- Empty `defaultProjectKey` and `defaultBoardId` are valid.
- Non-empty `defaultProjectKey` must be a valid Jira project key.

## Jira Source Contract

Represents MoonMind-owned endpoint templates advertised to the browser when Jira UI rollout is enabled.

Fields:

- `connections`: connection verification endpoint template.
- `projects`: project list endpoint template.
- `boards`: board list endpoint template for a project.
- `columns`: board column endpoint template.
- `issues`: board issue list endpoint template.
- `issue`: issue detail endpoint template.

Validation rules:

- All values must be MoonMind API paths.
- Values must not contain raw Jira domains, Jira credentials, tokens, or browser-side Jira auth hints.
- The entire `sources.jira` block is omitted when Jira UI rollout is disabled.

## Jira Integration Settings

Represents browser-safe Jira integration metadata under the runtime config `system` block.

Fields:

- `enabled`: boolean. Always `true` when the block is present.
- `defaultProjectKey`: string.
- `defaultBoardId`: string.
- `rememberLastBoardInSession`: boolean.

Validation rules:

- The entire `system.jiraIntegration` block is omitted when Jira UI rollout is disabled.
- The presence of `sources.jira` is not sufficient for future frontend rendering; `system.jiraIntegration.enabled` is the affirmative UI gate.

## Dashboard Runtime Configuration

Existing boot-time configuration object consumed by Mission Control.

Relationships:

- Contains `sources.jira` only when Jira UI rollout is enabled.
- Contains `system.jiraIntegration` only when Jira UI rollout is enabled.
- Retains existing non-Jira `sources` and `system` entries in both enabled and disabled states.

State transitions:

- Disabled to enabled: Jira source and system blocks appear with configured defaults.
- Enabled to disabled: Jira source and system blocks are omitted; existing Create page config remains intact.
