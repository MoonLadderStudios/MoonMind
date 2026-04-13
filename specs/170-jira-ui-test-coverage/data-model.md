# Data Model: Jira UI Test Coverage

This feature does not introduce durable production entities. The models below describe runtime state and validation fixtures that tests must exercise.

## Jira Runtime Capability

**Description**: Runtime Create-page metadata that controls whether Jira browser controls appear and which MoonMind-owned browser endpoints are available.

**Key fields**:

- `enabled`: whether Create-page Jira integration is visible.
- `endpoints`: MoonMind API paths for connection verification, projects, boards, columns, board issues, and issue detail.
- `defaultProjectKey`: optional project default.
- `defaultBoardId`: optional board default.
- `rememberLastBoardInSession`: whether session-only project and board selection memory is enabled.

**Validation rules**:

- Jira controls render only when enabled metadata and complete endpoint templates are present.
- Endpoint templates must be MoonMind-owned API paths, not raw Jira URLs.
- Defaults must not force stale selections when returned projects or boards do not contain them.

## Jira Browser Target

**Description**: The active field selected for Jira import.

**Variants**:

- Preset instructions target.
- Step instructions target with a stable local step identifier.

**Validation rules**:

- Opening from preset preselects preset instructions.
- Opening from a step preselects that exact step.
- Selecting an issue does not mutate the target.
- Import into a missing target step must not update another step.

## Jira Board Browser Model

**Description**: Normalized project, board, column, issue-list, and issue-detail data returned by MoonMind for the Create page.

**Key fields**:

- Project key and display name.
- Board ID, name, and project key.
- Column ID, name, order, count, and status IDs.
- Issue key, summary, issue type, status, assignee, update timestamp, and column ID.
- Issue detail description text, acceptance criteria text, and target-specific recommended import text.

**Validation rules**:

- Columns preserve board order.
- Issue grouping uses service-provided status-to-column mapping.
- Empty columns are represented.
- Unmapped statuses are safe and do not disappear silently.
- Issue detail is plain text for browser consumption.

## Jira Import Action

**Description**: Explicit user action that copies selected Jira text into the current target.

**Key fields**:

- `target`: preset or step.
- `mode`: preset brief, execution brief, description only, or acceptance criteria only.
- `writeMode`: replace or append.
- `selectedIssue`: normalized issue detail.

**Validation rules**:

- Replace overwrites only the target field.
- Append preserves existing field text and inserts a clear separator.
- Empty import text does not erase existing target text.
- Step import uses existing manual-edit behavior.

## Template-Bound Step Identity

**Description**: Marker that a preset-expanded step still matches its template-provided instructions.

**State transitions**:

- `template-bound` -> `manual-customized` when Jira import changes instructions away from template text.
- `template-bound` remains unchanged when instructions still match template text.

**Validation rules**:

- Jira import into a template-bound step detaches template instruction identity before submission when instructions diverge.
- Other steps retain their identities.

## Jira Failure Response

**Description**: Safe browser-facing error response for Jira browser operations.

**Key fields**:

- Stable error code.
- Safe operator-facing message.
- Browser source marker.
- Optional action context.

**Validation rules**:

- Raw credentials, authorization material, private-key text, tokens, and stack traces must not appear.
- Known Jira and policy failures are structured.
- Unexpected failures are normalized to safe structured browser errors.

## Manual Task Draft

**Description**: Existing Create page draft state that must remain usable when Jira is disabled or failing.

**Key fields**:

- Preset instructions.
- Step instructions.
- Runtime, repo, dependency, schedule, and submission controls.

**Validation rules**:

- Jira failures do not disable manual editing.
- Jira failures do not block valid manual task creation.
- Submission payload does not require Jira provenance, selections, or failure details.
