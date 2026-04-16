# Contract: Parent-Owned Merge Automation

## Scope

This contract defines the parent `MoonMind.Run` to merge automation child workflow boundary for MM-350. It supersedes detached completion semantics only for parent-owned merge automation runs.

## Parent Effective Input

The parent run accepts merge automation configuration without replacing the top-level publish mode contract.

```json
{
  "publishMode": "pr",
  "mergeAutomation": {
    "enabled": true,
    "strategy": "child_workflow_resolver_v1",
    "resolver": {
      "skill": "pr-resolver",
      "mergeMethod": "squash"
    },
    "gate": {
      "github": {
        "waitForExternalReviewSignal": true,
        "requireStatusChecksReportedOnHead": true,
        "requireNoRunningChecks": true,
        "reviewProviders": []
      },
      "jira": {
        "enabled": false,
        "issueKey": null,
        "allowedStatuses": []
      }
    },
    "timeouts": {
      "fallbackPollSeconds": 120,
      "expireAfterSeconds": 86400
    }
  }
}
```

Rules:
- `publishMode` remains top-level.
- Merge automation is ignored when `enabled` is false or `publishMode` is not `pr`.
- Unsupported strategy or effort/model values must fail through normal validation, not hidden fallback transforms.

## Parent Publish Context

Before child start, the parent must persist or reference:

```json
{
  "repository": "owner/repo",
  "prNumber": 123,
  "prUrl": "https://github.com/owner/repo/pull/123",
  "baseRef": "main",
  "headRef": "feature",
  "headSha": "abc123",
  "publishedAt": "2026-04-16T00:00:00Z",
  "jiraIssueKey": "MM-350",
  "artifactRef": "artifact://publish-context.json"
}
```

Rules:
- Required pull request identity fields must be available before child start.
- Full provider bodies and large logs must be stored as artifacts or omitted.

## Child Workflow Start

The parent starts one subordinate workflow for the publish context.

```json
{
  "workflowType": "MoonMind.MergeAutomation",
  "parentWorkflowId": "mm:parent",
  "parentRunId": "temporal-run-id",
  "publishContextRef": "artifact://publish-context.json",
  "publishContextSummary": {
    "repository": "owner/repo",
    "prNumber": 123,
    "prUrl": "https://github.com/owner/repo/pull/123",
    "headSha": "abc123",
    "jiraIssueKey": "MM-350"
  },
  "mergeAutomationConfig": {},
  "resolverTemplate": {
    "repository": "owner/repo",
    "targetRuntime": "codex",
    "requiredCapabilities": ["git", "gh"],
    "runtime": {},
    "publishMode": "none"
  }
}
```

Rules:
- The child workflow id is deterministic for the parent workflow and publish context.
- Parent retry or replay must not create a second child for the same publish context.
- The child is subordinate; it is not exposed as a replacement dependency target.

## Parent Waiting Semantics

While the child is active:
- parent `mm_state` is `awaiting_external`;
- parent metadata includes the child workflow id and compact merge automation state;
- parent terminal success is blocked.

## Child Outcome

The child returns:

```json
{
  "status": "merged",
  "prNumber": 123,
  "prUrl": "https://github.com/owner/repo/pull/123",
  "cycles": 1,
  "resolverChildWorkflowIds": ["resolver-child-1"],
  "lastHeadSha": "abc123",
  "blockers": [],
  "summary": "Merged after readiness gate opened."
}
```

Rules:
- `merged` and `already_merged` allow parent terminal success.
- `blocked`, `failed`, and `expired` fail the parent with an operator-readable reason.
- `canceled` maps to parent cancellation semantics.

## Dependency Semantics

Downstream dependencies continue to reference the original parent workflow id. They are satisfied only when the parent reaches terminal success after merge automation succeeds.
