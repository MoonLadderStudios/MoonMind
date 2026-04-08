# Step Ledger Query Contract

## Progress Query

Representative payload:

```json
{
  "total": 6,
  "pending": 2,
  "ready": 0,
  "running": 1,
  "awaitingExternal": 0,
  "reviewing": 0,
  "succeeded": 3,
  "failed": 0,
  "skipped": 0,
  "canceled": 0,
  "currentStepTitle": "Run test suite",
  "updatedAt": "2026-04-04T18:11:15Z"
}
```

## Step Ledger Query

Representative payload:

```json
{
  "workflowId": "wf_123",
  "runId": "run_456",
  "runScope": "latest",
  "steps": [
    {
      "logicalStepId": "run-tests",
      "order": 4,
      "title": "Run test suite",
      "tool": {
        "type": "skill",
        "name": "repo.run_tests",
        "version": "1"
      },
      "dependsOn": ["apply-patch"],
      "status": "running",
      "waitingReason": null,
      "attentionRequired": false,
      "attempt": 1,
      "startedAt": "2026-04-04T18:10:00Z",
      "updatedAt": "2026-04-04T18:11:15Z",
      "summary": "Executing tests in sandbox",
      "checks": [],
      "refs": {
        "childWorkflowId": null,
        "childRunId": null,
        "taskRunId": null
      },
      "artifacts": {
        "outputSummary": null,
        "outputPrimary": null,
        "runtimeStdout": null,
        "runtimeStderr": null,
        "runtimeMergedLogs": null,
        "runtimeDiagnostics": null,
        "providerSnapshot": null
      },
      "lastError": null
    }
  ]
}
```

## Invariants

- `runScope` is `"latest"` for v1.
- Rows are ordered by resolved plan order.
- Field names and status values are frozen in this phase and may be consumed unchanged by later API/UI work.
- `checks`, `refs`, and `artifacts` are always present even when empty/default.
- Query payloads remain bounded and do not inline large evidence bodies.
