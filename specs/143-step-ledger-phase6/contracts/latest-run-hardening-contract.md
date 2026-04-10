# Latest-Run Hardening Contract

## Scope

This phase hardens the default latest-run read path for `MoonMind.Run` task detail.

## Contract Rules

1. `workflowId` remains the durable task/detail identifier.
2. `/api/executions/{workflowId}` may begin from a projection-backed row, but when bounded workflow query data exposes a newer current run, the response must adopt that latest `runId`.
3. `/api/executions/{workflowId}/steps` remains the authoritative latest-run step surface.
4. The public `progress` object remains the documented bounded `ExecutionProgress` contract.
5. Mission Control generic artifact browsing must use the latest/current run once it is known from the Steps surface.
6. Degraded reads may omit `progress`, but they must not invent a new `runId`.
