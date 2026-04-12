# Data Model: Jira Browser API

## JiraConnectionVerification

Represents whether the trusted Jira binding is usable for Create-page browser reads.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `ok` | boolean | True only when auth and policy checks pass. |
| `accountId` | string? | Safe Jira account identifier when verification is account-scoped. |
| `displayName` | string? | Safe display label from Jira profile. |
| `projectKey` | string? | Present when verification is project-scoped. |
| `projectName` | string? | Safe project display name when available. |

## JiraProject

An allowed Jira project visible through the configured trusted boundary.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `projectKey` | string | Uppercase Jira project key. |
| `name` | string | Display name; empty names normalize to the project key. |
| `id` | string? | Jira project ID when returned by Jira. |

## JiraBoard

A board available for browsing within an allowed project.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `id` | string | Board identifier represented as a string. |
| `name` | string | Safe board display name. |
| `projectKey` | string | Uppercase project key associated with the board. |
| `type` | string? | Board type, such as scrum or kanban, when available. |

## JiraColumn

A normalized board column derived from board configuration.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `id` | string | Stable MoonMind column ID derived from the column name and disambiguated when needed. |
| `name` | string | Jira board column display name. |
| `order` | integer | Zero-based board order. |
| `count` | integer | Number of grouped issues in this column for issue-list responses. |
| `statusIds` | string[] | Jira status IDs mapped to this board column. |

## JiraIssueSummary

An issue row suitable for board-column list display.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `issueKey` | string | Uppercase Jira issue key. |
| `summary` | string | Safe issue summary. |
| `issueType` | string? | Display issue type when available. |
| `statusId` | string? | Jira status ID when available. |
| `statusName` | string? | Display status name when available. |
| `assignee` | string? | Assignee display label only. |
| `updatedAt` | string? | Jira update timestamp when available. |
| `columnId` | string | Normalized column ID, or `unmapped` for unmapped statuses. |

## JiraBoardIssues

The board issue browsing response.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `boardId` | string | Board identifier. |
| `columns` | JiraColumn[] | Always present in board order, including empty columns. |
| `itemsByColumn` | map[string, JiraIssueSummary[]] | Includes arrays for each normalized visible column. |
| `unmappedItems` | JiraIssueSummary[] | Issues whose statuses cannot be mapped safely to a board column. |

## JiraIssueDetail

The issue preview and import-source read model.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `issueKey` | string | Uppercase Jira issue key. |
| `url` | string? | Safe browser URL when derivable. |
| `summary` | string | Safe issue summary. |
| `issueType` | string? | Display issue type when available. |
| `column` | object? | Normalized column ID/name when board context and status mapping are known. |
| `status` | object? | Jira status ID/name when available. |
| `descriptionText` | string | Normalized plain text; empty string when absent. |
| `acceptanceCriteriaText` | string | Normalized plain text; empty string when absent. |
| `recommendedImports.presetInstructions` | string | Recommended text for preset Feature Request / Initial Instructions. |
| `recommendedImports.stepInstructions` | string | Recommended text for step Instructions. |

## JiraBrowserError

A structured safe failure response for browser operations.

| Field | Type | Validation / Notes |
| --- | --- | --- |
| `code` | string | Stable MoonMind/Jira error code. |
| `message` | string | Safe generic message without raw provider response bodies or credentials. |

## State Transitions

### Connection Verification

1. Browser rollout disabled -> safe unavailable response.
2. Rollout enabled but auth missing or denied -> safe structured failure.
3. Rollout enabled and trusted auth succeeds -> safe verification result.

### Board Browsing

1. Project selected.
2. Project policy checked.
3. Boards listed for allowed project.
4. Board configuration resolved.
5. Columns normalized in board order.

### Issue Grouping

1. Board configuration resolves status-to-column mapping.
2. Board issues are read.
3. Each issue maps to a normalized column by status ID.
4. Issues without a mapped status move to `unmappedItems`.
5. Column counts are computed from grouped items.

### Issue Detail Normalization

1. Issue project is checked against policy.
2. Issue detail is read through the trusted Jira boundary.
3. Rich text is normalized to plain text.
4. Acceptance criteria are extracted when available.
5. Recommended preset and step import strings are generated.
