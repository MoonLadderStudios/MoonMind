# Data Model: Jira Failure Handling

## JiraBrowserFailure

Represents a browser-facing failure from a MoonMind-owned Jira browser operation.

**Fields**:

- `code`: Stable MoonMind/Jira-browser error code, such as `jira_policy_denied`, `jira_request_failed`, or `jira_browser_request_failed`.
- `message`: Safe operator-facing message. Must not include raw credentials, secret-like values, raw provider traces, or unsanitized exception text.
- `source`: Constant value identifying the failure source as the Jira browser boundary.
- `action`: Optional Jira browser action context, such as project listing, board listing, issue listing, or issue detail lookup.

**Validation rules**:

- `code` and `message` are required for every failure response.
- `source` is required and must identify the Jira browser surface.
- `action` is included when the originating failure has a safe action name.
- Secret-like message content must be replaced with a generic safe message before reaching the browser.

## JiraEmptyState

Represents a successful Jira browser response with no selectable data.

**Fields**:

- `kind`: Empty area: projects, boards, columns, issues, or issue detail content.
- `items`: Empty collection or empty normalized content shape from the existing browser model.
- `message`: Browser-local display copy chosen by the frontend for the empty state.

**Validation rules**:

- Empty projects, boards, columns, and issues are successful states, not backend failures.
- Empty states must preserve enough response structure for the browser to render the surrounding selector or panel.
- Empty issue-detail content must not trigger import or draft mutation.

## JiraBrowserPanelError

Represents a local frontend rendering state for one failed Jira browser query.

**Fields**:

- `area`: Browser area where the failure occurred: project selector, board selector, column/story list, or issue preview.
- `message`: Inline error text shown inside the Jira browser panel.
- `canContinueManually`: Always true for Jira browser load failures.
- `blocksImport`: True only when the missing or failed data is required for the currently selected import action.

**Validation rules**:

- Panel errors must not set global Create page submit errors.
- Panel errors must not disable manual editing controls.
- Panel errors must include manual-continuation guidance.

## ManualTaskDraft

Existing Create page draft state that must remain independent from Jira availability.

**Fields in scope**:

- Preset objective / initial instructions.
- Step instructions and optional step metadata.
- Runtime, repository, publish, dependency, attachment, priority, attempt, and schedule settings.
- Submit button state and submission payload.

**Validation rules**:

- Jira load failures must not mutate any draft field.
- Jira issue-detail failures must not import text.
- Jira failures must not change objective precedence or submission payload shape.
- Valid manual task creation remains possible after Jira browser failure.

## State Transitions

```text
Jira query starts
  -> success with data
  -> success with empty state
  -> structured backend failure
  -> local browser-panel error

Issue detail selected
  -> detail loaded
  -> import action enabled
  -> explicit import mutates selected target only

Issue detail selected
  -> detail load fails
  -> local browser-panel error
  -> import action unavailable
  -> manual draft remains unchanged

Jira browser fails
  -> user closes or ignores browser
  -> manual editing continues
  -> existing Create submission path remains available
```
