# Contract: Trusted Jira Dependency Links

## Service Boundary

Issue-link creation belongs to `moonmind.integrations.jira.tool.JiraToolService`.

The service must:

- enforce `jira_tool_enabled`
- enforce allowed action policy for the new link action
- enforce allowed projects for both issue keys
- use `JiraClient.request_json`
- return sanitized result fields only

## Request Model

Conceptual request:

```json
{
  "blocksIssueKey": "TOOL-1",
  "blockedIssueKey": "TOOL-2",
  "linkType": "Blocks"
}
```

Validation:

- `blocksIssueKey` and `blockedIssueKey` must match Jira issue-key syntax.
- The issue keys must not be equal.
- `linkType` defaults to the configured blocking link type when omitted by internal callers.
- Unsupported or blank link type values fail validation.

## Jira REST Shape

The Jira service posts to Jira's issue-link endpoint using Jira's inward/outward link semantics for the configured blocker link type.

The service result must be compact:

```json
{
  "linked": true,
  "blocksIssueKey": "TOOL-1",
  "blockedIssueKey": "TOOL-2",
  "linkType": "Blocks"
}
```

Duplicate/existing link handling:

```json
{
  "linked": false,
  "existing": true,
  "blocksIssueKey": "TOOL-1",
  "blockedIssueKey": "TOOL-2",
  "linkType": "Blocks"
}
```

## Error Handling

- Policy denial returns `jira_policy_denied`.
- Validation failures return `jira_validation_failed`.
- Provider failures return sanitized `JiraToolError` details.
- Raw response bodies, credentials, tokens, cookies, and auth headers must never appear in returned link results.
