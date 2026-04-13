# Data Model: Jira Create Browser

## JiraIntegrationConfig

Represents Create page Jira browser capability and endpoint discovery from runtime config.

Fields:

- `enabled`: Whether the Create page may render Jira browser entry points.
- `defaultProjectKey`: Optional project key to preselect when available.
- `defaultBoardId`: Optional board id to preselect when available.
- `rememberLastBoardInSession`: Whether a later phase may restore project or board during the current browser session.
- `endpoints`: MoonMind-owned path templates for connection verification, projects, boards, columns, issues, and issue detail.

Validation rules:

- Entry points render only when `enabled` is true and endpoint templates are present.
- Endpoint templates must represent MoonMind API paths, not external Jira URLs.

## JiraProject

Represents a selectable Jira project.

Fields:

- `key`: Stable project key.
- `name`: Human-readable project name.

Relationships:

- A project can have zero or more `JiraBoard` records.

## JiraBoard

Represents a selectable Jira board for a project.

Fields:

- `id`: Stable board identifier.
- `name`: Human-readable board name.
- `projectKey`: Optional project key association.

Relationships:

- A board has zero or more ordered `JiraColumn` records.
- A board has issue summaries grouped by column.

## JiraColumn

Represents a board-specific column resolved by MoonMind.

Fields:

- `id`: Stable column identifier.
- `name`: Human-readable column name.
- `count`: Optional issue count.

Validation rules:

- Columns render in the order returned by MoonMind.
- Empty columns remain renderable.

## JiraIssueSummary

Represents compact issue-list display data.

Fields:

- `issueKey`: Stable issue key.
- `summary`: Issue summary.
- `issueType`: Optional issue type label.
- `statusName`: Optional normalized or display status label.
- `assignee`: Optional assignee display value.
- `updatedAt`: Optional updated timestamp.

Relationships:

- Issue summaries are grouped by `JiraColumn.id`.
- Selecting a summary triggers issue-detail loading.

## JiraIssueDetail

Represents normalized issue preview data.

Fields:

- `issueKey`: Stable issue key.
- `summary`: Issue summary.
- `url`: Optional human-facing issue URL for display only.
- `issueType`: Optional issue type label.
- `column`: Optional resolved board column.
- `status`: Optional status id and name.
- `descriptionText`: Normalized description text.
- `acceptanceCriteriaText`: Normalized acceptance criteria text.
- `recommendedImports`: Optional target-specific import text for later phases.

Validation rules:

- The browser consumes normalized text and does not parse Jira rich-text formats.
- Loading detail does not mutate draft instructions or task submission state in Phase 4.

## JiraImportTarget

Represents the Create page field selected as the destination for a future import.

Variants:

- Preset instruction target: `Feature Request / Initial Instructions`.
- Step instruction target: a specific step's `Instructions` field.

Validation rules:

- Opening the browser from a field preselects that target.
- The browser displays the current target explicitly.

## JiraBrowserState

Represents transient Create page browser state.

Fields:

- `browserOpen`: Whether the shared browser surface is visible.
- `selectedProjectKey`: Current project selection.
- `selectedBoardId`: Current board selection.
- `activeColumnId`: Current column selection.
- `issuesByColumn`: Issue summaries keyed by column id.
- `selectedIssueKey`: Current issue selection.
- `currentTarget`: Current `JiraImportTarget`.
- `replaceAppendPreference`: Replace or append preference for later import phases.
- `loadingStates`: Project, board, column, issue-list, and issue-detail loading states.
- `errorStates`: Local browser errors for failed Jira data loading.

State transitions:

- Closed -> Opened from preset: set current target to preset and show browser.
- Closed -> Opened from step: set current target to selected step and show browser.
- Project changed: clear board, active column, and selected issue.
- Board changed: clear active column and selected issue.
- Column changed: update active issue list and clear selected issue.
- Issue selected: load detail and show preview without editing draft text.
- Error loading Jira data: show browser-local error and preserve manual Create page editing.
