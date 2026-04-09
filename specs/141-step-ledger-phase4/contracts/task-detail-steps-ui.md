# Task Detail Steps UI Contract

## Read sequence

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{workflowId}/steps`
3. Optional row-level `/api/task-runs/{taskRunId}/*` fetches only after a row expands and exposes `taskRunId`

## Section order

1. Summary / top-level execution metadata
2. Steps
3. Timeline
4. Execution-wide Artifacts
5. Observation / debug / other secondary panels

## Expanded row groups

Each expanded step row renders the following groups in order:

1. Summary
2. Checks
3. Logs & Diagnostics
4. Artifacts
5. Metadata

## Row-level observability rules

- If `refs.taskRunId` is present, expanded rows may fetch observability summary and logs/diagnostics.
- If `refs.taskRunId` is absent, expanded rows show delayed-binding or unavailable copy and must not call `/api/task-runs/*`.
- Expanded rows remain keyed by `logicalStepId` so ordinary polling does not collapse operator context.
