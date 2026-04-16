# Contract: Merge Automation Visibility

## Parent Run Summary

When merge automation is enabled or has run, `reports/run_summary.json` includes:

```json
{
  "mergeAutomation": {
    "enabled": true,
    "status": "merged",
    "prNumber": 123,
    "prUrl": "https://github.com/owner/repo/pull/123",
    "latestHeadSha": "abc123",
    "childWorkflowId": "merge-automation:...",
    "resolverChildWorkflowIds": ["resolver:...:1"],
    "cycles": 1,
    "blockers": [],
    "artifactRefs": {
      "summary": "artifact-id",
      "gateSnapshots": ["artifact-id"],
      "resolverAttempts": ["artifact-id"]
    }
  }
}
```

Unknown optional values may be omitted or null. Raw provider payloads and secrets are not allowed.

## Mission Control

Task detail renders a merge automation section from the summary contract. It must remain inside the task detail run summary and must not create a separate dependency or schedule resource.
