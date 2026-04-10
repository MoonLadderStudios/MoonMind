# Step Ledger Evidence Contract

Phase 2 keeps the Phase 1 row schema unchanged and adds population rules only.

## Parent step refs

- `refs.childWorkflowId`: child `MoonMind.AgentRun` workflow ID
- `refs.childRunId`: child `MoonMind.AgentRun` run ID
- `refs.taskRunId`: managed-run observability identifier when available

## Parent step artifacts

- `artifacts.outputSummary`: durable summary artifact for the child/runtime result
- `artifacts.outputPrimary`: deterministic primary durable output ref
- `artifacts.runtimeStdout`: stdout artifact ref
- `artifacts.runtimeStderr`: stderr artifact ref
- `artifacts.runtimeMergedLogs`: merged log artifact ref when available
- `artifacts.runtimeDiagnostics`: diagnostics artifact ref
- `artifacts.providerSnapshot`: provider snapshot artifact ref when available

## Artifact metadata

When `agent_runtime.publish_artifacts` receives step context, it writes:

```json
{
  "step_id": "logical-step-id",
  "attempt": 2,
  "scope": "step"
}
```

These keys are additive metadata only. Artifact publication remains valid when they are absent.
