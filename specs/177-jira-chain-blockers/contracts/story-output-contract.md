# Contract: Story Output Jira Export

## Inputs

`story.create_jira_issues` continues to accept existing story inputs:

- `stories`, `storyBreakdown`, `story_breakdown`, or `storyBreakdownJson`
- `storyOutput.jira.projectKey` or `projectKey`
- `storyOutput.jira.issueTypeId` or `issueTypeId`
- `storyOutput.jira.issueTypeName` or `issueTypeName` when the issue type id
 should be resolved through the trusted Jira metadata surface
- optional `storyBreakdownPath`
- optional `workflowId` or equivalent marker source

New dependency-mode inputs:

- `storyOutput.jira.dependencyMode`
- `storyOutput.jira.dependency_mode`
- `storyOutput.dependencyMode`
- `dependencyMode`
- `dependency_mode`

Supported values:

- `none`
- `linear_blocker_chain`

Source references:

- Canonical `moonspec-breakdown` output must put the source path in each
 story's `sourceReference.path`, or in the breakdown payload's
 `source.referencePath` / `source.path`.
- For path-only generated output, `story.create_jira_issues` also accepts a
 string `sourceReference` value or top-level `sourceDocument` as the source
 document path and normalizes it before Jira mutation.
- Missing source references fail before Jira mutation and return fallback
 metadata when fallback is enabled.

Validation:

- Missing mode resolves to `none` at the export boundary.
- Blank or unsupported mode raises validation failure when fallback is disabled, or returns a story-breakdown handoff fallback result when fallback is enabled before any Jira mutation.
- The mode must not be inferred from prompt text when an explicit structured value is present.

## Output Shape

The existing output keys remain:

```json
{
 "storyOutput": {
 "mode": "jira",
 "status": "jira_created",
 "storyCount": 3,
 "createdCount": 3
 },
 "jira": {
 "createdCount": 3,
 "createdIssues": []
 }
}
```

The Jira output is extended with dependency fields:

```json
{
 "jira": {
 "dependencyMode": "linear_blocker_chain",
 "issueMappings": [
 {"storyId": "STORY-001", "storyIndex": 1, "issueKey": "TOOL-1", "created": true},
 {"storyId": "STORY-002", "storyIndex": 2, "issueKey": "TOOL-2", "created": true}
 ],
 "linkResults": [
 {
 "fromStoryId": "STORY-001",
 "fromStoryIndex": 1,
 "toStoryId": "STORY-002",
 "toStoryIndex": 2,
 "blocksIssueKey": "TOOL-1",
 "blockedIssueKey": "TOOL-2",
 "status": "created"
 }
 ],
 "linkCount": 1,
 "dependencyChainComplete": true
 }
}
```

Partial link failure:

```json
{
 "storyOutput": {
 "mode": "jira",
 "status": "jira_partial",
 "storyCount": 3,
 "createdCount": 3
 },
 "jira": {
 "partial": true,
 "dependencyMode": "linear_blocker_chain",
 "dependencyChainComplete": false,
 "createdIssues": [],
 "linkResults": [
 {"blocksIssueKey": "TOOL-1", "blockedIssueKey": "TOOL-2", "status": "created"},
 {
 "blocksIssueKey": "TOOL-2",
 "blockedIssueKey": "TOOL-3",
 "status": "failed",
 "errorCode": "jira_request_failed",
 "message": "Jira link creation failed."
 }
 ]
 }
}
```

## Acceptance Coverage

- `linear_blocker_chain` creates adjacent issue-link requests only after every issue has an issue key.
- `none` creates no link requests and reports zero link operations.
- Partial link failure preserves created issue keys and failed link details.
- Retry/reuse reports existing issue/link state instead of duplicate creation.
