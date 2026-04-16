# Contract: Merge Gate Runtime Boundary

## Workflow Type

`MoonMind.MergeAutomation`

Purpose: Wait for configured pull request readiness signals after a parent implementation run publishes a PR, then create one resolver follow-up `MoonMind.Run` when the gate opens.

## Start Input

```json
{
  "workflowType": "MoonMind.MergeAutomation",
  "parent": {
    "workflowId": "mm:parent",
    "runId": "temporal-run-id"
  },
  "pullRequest": {
    "repo": "owner/repo",
    "number": 123,
    "url": "https://github.com/owner/repo/pull/123",
    "headSha": "abc123",
    "headBranch": "feature-branch",
    "baseBranch": "main"
  },
  "jiraIssueKey": "MM-341",
  "policy": {
    "checks": "required",
    "automatedReview": "required",
    "jiraStatus": "optional",
    "mergeMethod": "squash"
  },
  "idempotencyKey": "merge-automation:mm-parent:owner/repo:123:abc123"
}
```

Rules:

- `workflowType` must be exactly `MoonMind.MergeAutomation`.
- `pullRequest.repo`, `pullRequest.number`, `pullRequest.url`, and `pullRequest.headSha` are required.
- The start input must not contain check logs, PR comments, raw provider responses, credentials, or large artifact bodies.
- The parent `MoonMind.Run` starts this workflow only after PR publication succeeds and merge automation is enabled.

## Readiness Evaluation Activity

Activity name: `merge_automation.evaluate_readiness`

Request:

```json
{
  "pullRequest": {
    "repo": "owner/repo",
    "number": 123,
    "url": "https://github.com/owner/repo/pull/123",
    "headSha": "abc123"
  },
  "jiraIssueKey": "MM-341",
  "policy": {
    "checks": "required",
    "automatedReview": "required",
    "jiraStatus": "optional"
  }
}
```

Response:

```json
{
  "headSha": "abc123",
  "ready": false,
  "blockers": [
    {
      "kind": "checks_running",
      "summary": "Required checks are still running.",
      "retryable": true,
      "source": "github"
    }
  ]
}
```

Rules:

- Responses are compact and sanitized.
- `ready=true` is valid only when `blockers` is empty and `headSha` matches the gate's tracked revision.
- `pull_request_closed`, `stale_revision`, and `policy_denied` blockers are terminal unless a later operator action creates a new valid gate.

## External Event Signal

Signal name: `merge_automation.external_event`

Payload:

```json
{
  "source": "github",
  "eventType": "checks_updated",
  "repo": "owner/repo",
  "pullRequestNumber": 123,
  "headSha": "abc123",
  "receivedAt": "2026-04-16T00:00:00Z"
}
```

Rules:

- Signals trigger earlier re-evaluation when available.
- Signals do not carry readiness truth by themselves; the workflow still calls the readiness evaluation activity.
- Duplicate or out-of-order signals must not create duplicate resolver runs.

## Resolver Follow-up Creation Activity

Activity name: `merge_automation.create_resolver_run`

Request:

```json
{
  "parentWorkflowId": "mm:parent",
  "pullRequest": {
    "repo": "owner/repo",
    "number": 123,
    "url": "https://github.com/owner/repo/pull/123",
    "headSha": "abc123",
    "headBranch": "feature-branch",
    "baseBranch": "main"
  },
  "jiraIssueKey": "MM-341",
  "mergeMethod": "squash",
  "idempotencyKey": "resolver:mm-parent:owner/repo:123:abc123"
}
```

Response:

```json
{
  "workflowId": "mm:resolver",
  "runId": "temporal-run-id",
  "created": true
}
```

Rules:

- The created run must select `pr-resolver`.
- The created run must set publish mode to `none`.
- The activity must be idempotent for the supplied key.
- The merge gate records the returned resolver ref and never launches another resolver for the same tracked PR revision.

## Query / Projection Shape

Operator-visible gate summaries should expose:

```json
{
  "status": "waiting",
  "pullRequestUrl": "https://github.com/owner/repo/pull/123",
  "headSha": "abc123",
  "blockers": [
    {
      "kind": "automated_review_pending",
      "summary": "Automated review has not completed.",
      "retryable": true
    }
  ],
  "resolverRun": null
}
```

Rules:

- Summary data must distinguish implementation completion, gate waiting, and resolver follow-up progress.
- Projection data must remain bounded and sanitized.
