# Remaining work: `docs/Api/ExecutionsApiContract.md`

Updated: 2026-04-04

## Step-ledger rollout

- Implement `GET /api/executions/{workflowId}/steps` backed by a workflow query for latest/current run step state.
- Add `progress` to the real execution-detail payload and keep `GET /api/executions/{workflowId}` cheap enough for normal polling.
- Return step-scoped refs (`childWorkflowId`, `childRunId`, `taskRunId`) and grouped artifact refs without forcing clients to parse generic artifacts.
- Add workflow-boundary and API contract tests covering step status vocabulary, attempt identity, and latest-run behavior across Continue-As-New.
