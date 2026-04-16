# Contract: MoonMind.MergeAutomation

## Workflow Type

`MoonMind.MergeAutomation`

No `MoonMind.MergeGate` canonical alias is retained.

## Start Input

```json
{
  "workflowType": "MoonMind.MergeAutomation",
  "parentWorkflowId": "mm-parent",
  "parentRunId": "temporal-run-id",
  "publishContextRef": "artifact://runs/mm-parent/publish-context.json",
  "pullRequest": {
    "repo": "owner/repo",
    "number": 123,
    "url": "https://github.com/owner/repo/pull/123",
    "headSha": "abc123",
    "headBranch": "feature/mm-351",
    "baseBranch": "main"
  },
  "jiraIssueKey": "MM-351",
  "mergeAutomationConfig": {
    "gate": {
      "github": {
        "waitForExternalReviewSignal": true,
        "requireStatusChecksReportedOnHead": true,
        "requireNoRunningChecks": true,
        "reviewProviders": []
      },
      "jira": {
        "enabled": false,
        "issueKey": "MM-351",
        "allowedStatuses": []
      }
    },
    "resolver": {
      "skill": "pr-resolver",
      "mergeMethod": "squash"
    },
    "timeouts": {
      "fallbackPollSeconds": 120,
      "expireAfterSeconds": 86400
    }
  },
  "resolverTemplate": {
    "repository": "owner/repo",
    "targetRuntime": "codex",
    "requiredCapabilities": ["git", "gh"],
    "runtime": {"mode": "codex"}
  }
}
```

## Activities

### `merge_automation.evaluate_readiness`

Input: `MergeAutomationStartInput` JSON plus compact wait state.

Output:

```json
{
  "status": "waiting",
  "headSha": "abc123",
  "ready": false,
  "blockers": [
    {
      "kind": "checks_running",
      "summary": "Required checks are still running.",
      "retryable": true,
      "source": "github"
    }
  ],
  "readyToLaunchResolver": false
}
```

### `merge_automation.create_resolver_run`

Input: deterministic resolver request with idempotency key and child `MoonMind.Run` payload.

Output:

```json
{
  "workflowId": "merge-auto-resolver-mm-parent-1",
  "runId": "temporal-run-id",
  "created": true
}
```

## Signal

`merge_automation.external_event`

Payload is intentionally advisory and compact. Any signal wakes the workflow to re-evaluate readiness through the trusted activity boundary.

## Query

`summary`

Returns compact operator-visible state:

```json
{
  "status": "awaiting_external",
  "outputStatus": "waiting",
  "prNumber": 123,
  "prUrl": "https://github.com/owner/repo/pull/123",
  "headSha": "abc123",
  "cycles": 1,
  "blockers": [],
  "resolverChildWorkflowIds": []
}
```

## Terminal Output

Allowed `status` values:
- `merged`
- `already_merged`
- `blocked`
- `failed`
- `expired`
- `canceled`
- `resolver_launched`

The MM-351 implementation verifies deterministic gate-open output and resolver-launch request creation; full resolver completion/merge result handling remains downstream of resolver lifecycle work.
