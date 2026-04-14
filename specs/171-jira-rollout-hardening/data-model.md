# Data Model: Jira Create-Page Rollout Hardening

## JiraIntegrationConfig

Represents Create-page Jira UI capability in the boot payload.

Fields:

- `enabled`: boolean; Jira browser controls render only when true.
- `defaultProjectKey`: optional project key selected when available.
- `defaultBoardId`: optional board id selected when available.
- `rememberLastBoardInSession`: boolean; enables best-effort browser-session project/board memory.
- `endpoints`: MoonMind-owned endpoint templates for connection verification, projects, boards, columns, issues, and issue detail.

Validation rules:

- Endpoint templates must be relative MoonMind API paths.
- Presence of endpoint templates alone is insufficient; `enabled` must also be true.
- Defaults may be blank and must not block manual task creation.

## JiraProject

Represents a Jira project available to browse.

Fields:

- `projectKey`: stable display key.
- `name`: display name.
- `id`: optional provider id.

Validation rules:

- Project keys are normalized to uppercase.
- Project policy boundaries apply before board or issue browsing.

## JiraBoard

Represents a board within a project.

Fields:

- `id`: stable board identifier.
- `name`: display name.
- `projectKey`: associated project key.
- `type`: optional board type.

Validation rules:

- Board id must be non-empty and safe for path interpolation.
- Board project key must satisfy configured Jira policy boundaries when known.

## JiraColumn

Represents an ordered column in one Jira board.

Fields:

- `id`: stable Create-page column id.
- `name`: display name.
- `order`: board order.
- `count`: issue count for list rendering.
- `statusIds`: provider status ids mapped to this column.

Validation rules:

- Columns preserve board order.
- Duplicate column names receive unique stable ids.
- Empty column lists are valid and must render as empty states.

## JiraIssueSummary

Represents one issue in a board issue list.

Fields:

- `issueKey`: issue key.
- `summary`: list title.
- `issueType`: optional issue type display name.
- `statusId`: optional provider status id.
- `statusName`: optional provider status name.
- `assignee`: optional assignee display name.
- `updatedAt`: optional updated timestamp.
- `columnId`: resolved column id or unmapped bucket id.

Validation rules:

- Issue keys are normalized to uppercase.
- Column membership is resolved server-side from status-to-column mapping.
- Issues with unmapped statuses remain visible through an explicit unmapped collection.

## JiraBoardIssues

Represents grouped board issue results.

Fields:

- `boardId`: requested board id.
- `columns`: ordered board columns with counts.
- `itemsByColumn`: issue summaries keyed by column id.
- `unmappedItems`: issue summaries that could not be mapped to a board column.

Validation rules:

- Every returned column has an `itemsByColumn` bucket.
- Empty columns remain present.
- Optional query filtering applies to issue key and summary only.

## JiraIssueDetail

Represents a Create-page-ready issue preview and import source.

Fields:

- `issueKey`: issue key.
- `url`: optional provider browse URL.
- `summary`: title.
- `issueType`: optional issue type.
- `column`: optional resolved board column.
- `status`: optional provider status id/name.
- `descriptionText`: normalized description text.
- `acceptanceCriteriaText`: normalized acceptance criteria text.
- `recommendedImports.presetInstructions`: text optimized for preset objective target.
- `recommendedImports.stepInstructions`: text optimized for step instruction target.

Validation rules:

- Rich text must be normalized server-side.
- Acceptance criteria may come from a dedicated field or from a recognizable section in the description.
- The browser imports normalized text; it does not parse provider rich text.

## JiraImportTarget

Represents the Create-page field selected for import.

Variants:

- `preset`: preset objective field.
- `step`: one step instruction field identified by local step id.

Validation rules:

- Opening the browser from a field preselects that field.
- Changing target does not automatically mutate draft text.
- Missing step targets abort import without draft mutation.

## JiraImportProvenance

Represents advisory local metadata for a field that received Jira text.

Fields:

- `issueKey`: imported issue key.
- `boardId`: board used for import when known.
- `columnId`: issue column used for import when known.
- `importMode`: selected import mode.
- `targetType`: preset or step.

Validation rules:

- Provenance is local UI metadata and is not submitted with task payload in the MVP.
- Provenance clears when the associated field is manually edited or removed.
- Reopening Jira from an imported field prefers this context when available.

## State Transitions

### Browser Selection

1. Closed browser has no active import action.
2. Opening from preset or step sets the import target.
3. Project selection resets board, column, and issue selection.
4. Board selection resets column and issue selection.
5. Column selection resets issue selection.
6. Issue selection loads preview only; no draft mutation occurs.

### Import

1. Operator selects an issue and import mode.
2. Operator chooses replace or append.
3. Preset target updates preset objective only.
4. Step target updates only selected step instructions.
5. Template-derived step import marks that step manually customized when instructions differ.
6. Provenance is recorded when issue identity is available.

### Preset Reapply

1. Preset applied state records the applied preset objective.
2. Jira import changes preset objective after application.
3. Reapply-needed message appears.
4. Existing expanded steps remain unchanged until explicit reapply.
