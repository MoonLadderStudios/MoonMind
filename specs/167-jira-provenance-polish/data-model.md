# Data Model: Jira Provenance Polish

## JiraImportProvenance

Represents local Create page metadata for a single successful Jira import.

### Fields

- `issueKey`: Jira issue key displayed in the provenance chip.
- `boardId`: Jira board selected when the import occurred.
- `importMode`: Import text mode selected when the import occurred.
- `targetType`: Import target kind, either preset instructions or step instructions.

### Validation Rules

- `issueKey` must be non-empty before a provenance chip is shown.
- `boardId` may be empty only if the Jira issue detail is imported without a selected board; it must not prevent chip display when `issueKey` exists.
- `importMode` must match one of the existing Jira import modes exposed by the Create page.
- `targetType` must match the active import target used for the successful import.

### State Transitions

- No provenance -> successful Jira import into preset instructions -> preset provenance set.
- No provenance -> successful Jira import into a step -> provenance set for that step only.
- Provenance present -> manual edit of the same field -> provenance cleared.
- Step provenance present -> step removed -> provenance for that step removed.

## JiraImportTarget

Represents the currently selected destination for imported Jira text.

### Fields

- `kind`: Preset or step.
- `localId`: Present only for step targets and identifies the local step receiving the import.

### Validation Rules

- A preset target maps only to Feature Request / Initial Instructions.
- A step target maps only to the step with the matching local id.
- If a step target no longer exists, import must be a no-op and must not write provenance to any other target.

## SessionJiraSelection

Represents session-only memory of the last Jira project and board selected in the Jira browser.

### Fields

- `projectKey`: Last selected Jira project key.
- `boardId`: Last selected Jira board id.
- `enabled`: Whether runtime config allows session memory.

### Validation Rules

- Values are read and written only when `enabled` is true.
- Clearing project selection clears both remembered project and board values.
- Clearing board selection clears the remembered board value.
- Storage access failures must be ignored and must not block Jira browsing, manual editing, or task submission.

### State Transitions

- Disabled -> select project/board -> no session memory written.
- Enabled -> select project -> project remembered and board memory cleared.
- Enabled -> select board -> board remembered.
- Enabled with remembered values -> open browser -> remembered values preselect project and board.
- Storage failure -> open browser -> default selection behavior proceeds.
