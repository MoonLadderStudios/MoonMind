# Contract: Post-Merge Jira Completion

## Scope

This contract defines the worker-bound and operator-visible surfaces for MM-403 post-merge Jira completion.

## Merge Automation Input Extension

Existing `MoonMind.MergeAutomation` input remains valid. A new optional `postMergeJira` block may be added inside `mergeAutomationConfig`.

```json
{
  "workflowType": "MoonMind.MergeAutomation",
  "jiraIssueKey": "MM-403",
  "mergeAutomationConfig": {
    "postMergeJira": {
      "enabled": true,
      "issueKey": null,
      "transitionId": null,
      "transitionName": null,
      "strategy": "done_category",
      "required": true,
      "fields": {}
    }
  }
}
```

Rules:

- Missing `postMergeJira` preserves existing merge automation invocation compatibility.
- Unsupported strategy values fail validation.
- Explicit transition IDs and names must be validated against current Jira transitions before mutation.
- `codex.model`, `codex.effort`, and unrelated runtime inputs are not transformed by this feature.

## Post-Merge Completion Activity Contract

Merge automation invokes a trusted activity/service boundary after resolver disposition `merged` or `already_merged`.

Input:

```json
{
  "parentWorkflowId": "mm:parent",
  "mergeAutomationWorkflowId": "merge-automation:mm-parent:repo:1:sha",
  "resolverDisposition": "merged",
  "pullRequest": {
    "repo": "MoonLadderStudios/MoonMind",
    "number": 403,
    "url": "https://github.com/MoonLadderStudios/MoonMind/pull/403",
    "headSha": "abc123"
  },
  "jiraIssueKey": "MM-403",
  "postMergeJira": {
    "enabled": true,
    "strategy": "done_category",
    "required": true
  },
  "candidateContext": {
    "taskOriginIssueKey": "MM-403",
    "publishContextIssueKey": "MM-403",
    "prMetadataKeys": ["MM-403"]
  }
}
```

Output:

```json
{
  "status": "succeeded",
  "required": true,
  "issueResolution": {
    "status": "resolved",
    "issueKey": "MM-403",
    "source": "merge_automation",
    "candidates": [
      {
        "issueKey": "MM-403",
        "source": "merge_automation",
        "validated": true,
        "statusName": "Code Review",
        "statusCategory": "indeterminate"
      }
    ],
    "reason": null
  },
  "transition": {
    "transitionId": "41",
    "transitionName": "Done",
    "toStatusName": "Done",
    "toStatusCategory": "done"
  },
  "alreadyDone": false,
  "transitioned": true,
  "reason": null,
  "artifactRefs": {
    "resolution": "artifact-id-resolution",
    "transition": "artifact-id-transition"
  }
}
```

Status values:

- `succeeded`: Jira transition was applied.
- `noop_already_done`: issue was already in a done-category status.
- `skipped`: run was not Jira-backed or completion was disabled.
- `blocked`: required completion could not safely select issue or transition.
- `failed`: trusted Jira call failed after a safe target was selected.

## Merge Automation Result Extension

The terminal merge automation result includes compact completion evidence.

```json
{
  "status": "merged",
  "prNumber": 403,
  "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/403",
  "cycles": 1,
  "resolverChildWorkflowIds": ["resolver:wf-parent:repo:403:abc123:1"],
  "postMergeJira": {
    "status": "succeeded",
    "issueKey": "MM-403",
    "issueKeySource": "merge_automation",
    "transitionId": "41",
    "transitionName": "Done",
    "alreadyDone": false,
    "transitioned": true,
    "reason": null
  },
  "artifactRefs": {
    "postMergeJiraResolution": "artifact-id-resolution",
    "postMergeJiraTransition": "artifact-id-transition"
  }
}
```

Rules:

- Parent `MoonMind.Run` treats `merged` and `already_merged` as success only after required post-merge Jira completion succeeds or no-ops.
- Required completion with `blocked` or `failed` status prevents terminal success and surfaces blocker evidence.
- Completion evidence must not contain raw credentials or large Jira payloads.

## Trusted Jira Boundary

Allowed Jira operations:

- fetch issue by key;
- fetch current transitions with field metadata when needed;
- apply a validated transition.

Forbidden behavior:

- raw Jira credentials in agent shells, resolver scripts, comments, artifacts, or workflow payloads;
- arbitrary Jira HTTP calls from agent runtime;
- fuzzy summary search as a default target resolver;
- transitioning more than one Jira issue for one merge automation run.
