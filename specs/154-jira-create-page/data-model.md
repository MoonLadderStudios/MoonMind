# Data Model: Jira Create Page Integration

## JiraIntegrationRuntimeCapability

Represents the Create-page Jira capability in dashboard runtime config.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `enabled` | boolean | Must be true for Jira entry points to render. |
| `defaultProjectKey` | string | Optional; normalized uppercase Jira project key when configured. |
| `defaultBoardId` | string | Optional; trimmed Jira board identifier when configured. |
| `rememberLastBoardInSession` | boolean | Enables session-only project/board memory in the browser. |
| `sources` | object | MoonMind-owned URL templates for browser operations. |

## JiraConnectionVerification

Represents whether the trusted Jira binding is usable for browser reads.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `ok` | boolean | True only when auth and policy checks pass. |
| `accountId` | string? | Returned when verification is account-scoped. |
| `displayName` | string? | Safe display label from Jira profile. |
| `projectKey` | string? | Present when verification is project-scoped. |
| `projectName` | string? | Safe project display name when available. |

## JiraProject

An allowed Jira project visible to the Create page.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `projectKey` | string | Uppercase Jira project key; must satisfy project-key validation. |
| `name` | string | Display name; empty names normalize to project key. |
| `id` | string? | Jira project ID when returned by Jira. |

## JiraBoard

Board available for browsing within a project.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `id` | string | Board identifier as a string. |
| `name` | string | Display name. |
| `projectKey` | string | Uppercase project key from request or Jira location. |
| `type` | string? | Board type, such as scrum or kanban, when available. |

## JiraColumn

Normalized board column.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `id` | string | Stable slug or Jira column identifier. |
| `name` | string | Display name. |
| `order` | integer | Board order, zero-based. |
| `count` | integer | Number of grouped issues; may be zero. |
| `statusIds` | string[] | Jira status IDs mapped to the column; not required by browser for grouping. |

## JiraIssueSummary

Issue row shown in a board column.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `issueKey` | string | Uppercase Jira issue key. |
| `summary` | string | Display summary. |
| `issueType` | string? | Display issue type. |
| `statusId` | string? | Jira status ID. |
| `statusName` | string? | Display status. |
| `assignee` | string? | Safe display label only. |
| `updatedAt` | string? | ISO-like timestamp from Jira when available. |
| `columnId` | string | Normalized column ID after server-side grouping. |

## JiraBoardIssues

Response shape for issue browsing by column.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `boardId` | string | Board identifier. |
| `columns` | JiraColumn[] | Always present, in board order. |
| `itemsByColumn` | map[string, JiraIssueSummary[]] | Includes empty arrays for empty columns. |
| `unmappedItems` | JiraIssueSummary[] | Optional safe bucket for statuses that cannot be mapped. |

## JiraIssueDetail

Issue preview and import source.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `issueKey` | string | Uppercase Jira issue key. |
| `url` | string? | Safe browser URL when available. |
| `summary` | string | Display summary. |
| `issueType` | string? | Display issue type. |
| `column` | object? | Normalized column ID/name when context is known. |
| `status` | object? | Jira status ID/name. |
| `descriptionText` | string | Normalized plain text; empty string when absent. |
| `acceptanceCriteriaText` | string | Normalized plain text; empty string when absent. |
| `recommendedImports.presetInstructions` | string | Target-specific preset brief source. |
| `recommendedImports.stepInstructions` | string | Target-specific execution brief source. |

## JiraImportTarget

Local Create-page target for import.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `kind` | enum | `preset-objective` or `step-instructions`. |
| `stepLocalId` | string? | Required when `kind` is `step-instructions`. |

## JiraImportState

Local Create-page browser state.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `isOpen` | boolean | Only one browser may be open. |
| `target` | JiraImportTarget | Current import target. |
| `selectedProjectKey` | string | Optional until project selected. |
| `selectedBoardId` | string | Optional until board selected. |
| `activeColumnId` | string | Optional until columns load. |
| `selectedIssueKey` | string | Optional until issue selected. |
| `importMode` | enum | `preset-brief`, `execution-brief`, `description-only`, `acceptance-only`. |
| `writeMode` | enum | `replace` or `append`. |
| `loadingState` | object | Per-operation loading flags. |
| `error` | string? | Local browser error only. |

## JiraImportProvenance

Advisory local UI metadata after import.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `source` | literal | Always `jira`. |
| `issueKey` | string | Imported issue key. |
| `boardId` | string | Board selected at import time. |
| `columnId` | string? | Column selected at import time. |
| `mode` | enum | Import mode used. |
| `targetType` | enum | `preset-objective` or `step-instructions`. |
| `importedAt` | string | Client timestamp for UI display only. |

## State Transitions

### Browser Selection

1. Closed -> Open with target.
2. Open -> Project selected.
3. Project selected -> Board selected.
4. Board selected -> Columns/issues loaded.
5. Column selected -> Visible issue list changes.
6. Issue selected -> Detail preview loaded.
7. Import confirmed -> Target text updated, provenance recorded, browser may remain open or close.

### Preset Reapply Signal

1. No preset applied -> Jira preset import updates feature request only.
2. Preset applied -> Jira preset import updates feature request and marks preset state as needing reapply.
3. User reapplies preset -> preset-generated steps update through existing explicit apply flow.

### Template-Bound Step Import

1. Template-bound step has matching template instructions.
2. Jira import writes different instructions through the existing step update path.
3. Step detaches template instruction identity and becomes manually customized.
