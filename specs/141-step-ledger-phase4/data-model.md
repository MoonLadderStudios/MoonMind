# Data Model: Step Ledger Phase 4

## Task detail read order

1. `GET /api/executions/{workflowId}` for bounded execution metadata and `progress`
2. `GET /api/executions/{workflowId}/steps` for the latest/current run step ledger
3. `GET /api/task-runs/{taskRunId}/*` only when an expanded row exposes `taskRunId`
4. Execution-wide artifact browsing remains separate and secondary

## TaskDetailStepLedger

Uses the existing `StepLedgerSnapshotModel` response unchanged.

| Field | Type | Description |
| --- | --- | --- |
| `workflowId` | string | Durable task/execution identity |
| `runId` | string | Latest/current run identity |
| `runScope` | `"latest"` | Phase 4 continues latest-run-only behavior |
| `steps` | `StepLedgerRowModel[]` | Ordered latest/current run rows |

## TaskDetailStepRowView

Derived frontend state per `logicalStepId`.

| Field | Type | Description |
| --- | --- | --- |
| `row` | `StepLedgerRowModel` | Canonical API row |
| `expanded` | boolean | Whether the row panel is open |
| `hasObservability` | boolean | Whether `refs.taskRunId` exists |
| `observabilityState` | idle/loading/ready/error | Row-level `/api/task-runs/*` drilldown state |

## Expanded step evidence groups

Each expanded row renders the same semantic groups in a stable order:

1. **Summary**: row summary, waiting reason, last error, current attempt, status
2. **Checks**: structured `checks[]` badges and summaries
3. **Logs & Diagnostics**: live logs, stdout, stderr, diagnostics when `taskRunId` exists; delayed-binding copy otherwise
4. **Artifacts**: grouped artifact slot refs from `row.artifacts`
5. **Metadata**: logical step id, tool, dependencies, refs, timestamps, current run id

## Latest-run rules

- The task detail page stays anchored on `workflowId`.
- The Steps section surfaces the latest/current run only.
- Expanded-row state is keyed by `logicalStepId`, but step attempt identity continues to come from the API row fields (`runId`, `attempt`).
