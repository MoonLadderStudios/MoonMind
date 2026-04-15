# Data Model: Jira Chain Blockers

## JiraDependencyMode

Represents the requested Jira dependency behavior for a story export.

Fields:

- `dependencyMode`: string enum, required when present.
- Supported values: `none`, `linear_blocker_chain`.

Validation rules:

- Missing mode defaults to the existing safe behavior, `none`, only at the explicit export boundary.
- Blank, malformed, or unsupported values fail validation.
- Mode values are passed through exactly after normalization to the supported enum.

## OrderedStoryMapping

Represents one story from the ordered breakdown and its Jira issue result.

Fields:

- `storyId`: stable story identifier when present.
- `storyIndex`: one-based order used for issue creation.
- `summary`: created issue summary.
- `issueKey`: Jira issue key after creation or reuse.
- `issueId`: Jira issue id when returned by Jira.
- `created`: true when a new Jira issue was created.
- `existing`: true when an existing issue was reused.

Relationships:

- A `JiraExportResult` has one mapping per input story.
- `JiraDependencyLinkResult` references mappings by story id/index and issue key.

## JiraDependencyLinkRequest

Represents one requested Jira dependency link.

Fields:

- `blocksIssueKey`: issue key for the earlier story in the chain.
- `blockedIssueKey`: issue key for the later story in the chain.
- `linkType`: Jira link type used for blocking semantics.

Validation rules:

- Both issue keys must be non-empty Jira issue keys.
- Source and target issue keys must differ.
- Link type must be the configured blocker link type for Jira dependency links.

## JiraDependencyLinkResult

Represents the outcome of one requested Jira dependency link.

Fields:

- `fromStoryId` / `fromStoryIndex`: earlier story identifier/order.
- `toStoryId` / `toStoryIndex`: later story identifier/order.
- `blocksIssueKey`: earlier Jira issue key.
- `blockedIssueKey`: later Jira issue key.
- `status`: `created`, `existing`, `skipped`, or `failed`.
- `errorCode`: stable failure code when status is `failed`.
- `message`: sanitized operator-facing failure summary when status is `failed`.

Validation rules:

- `failed` results must identify the affected link.
- `existing` results must be reported as successful reuse, not fresh creation.
- Link results must not include raw credentials or provider traces.

## JiraExportResult

Aggregate output for Jira story export.

Fields:

- `dependencyMode`: selected mode.
- `storyCount`: number of stories processed.
- `createdCount`: number of newly created issues.
- `issueMappings`: ordered list of `OrderedStoryMapping`.
- `createdIssues`: existing compatibility-shaped issue list for current consumers.
- `linkResults`: list of `JiraDependencyLinkResult`.
- `linkCount`: number of created or existing link results.
- `partial`: true when some issue or link work failed after partial progress.
- `dependencyChainComplete`: true only when every requested link was created or reused.
- `fallback`: fallback metadata when Jira export cannot complete.

State transitions:

- `none`: issues created/reused, no link requests, chain complete is not applicable.
- `linear_blocker_chain`: issues created/reused, adjacent link requests attempted after issue mapping is available.
- Partial failure: issues may be complete while one or more links fail; result is partial and chain complete is false.
- Fallback: missing Jira target configuration or issue creation failure follows existing docs/tmp fallback behavior.
