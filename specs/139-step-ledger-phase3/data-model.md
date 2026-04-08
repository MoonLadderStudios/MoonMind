# Data Model: Step Ledger Phase 3

## ExecutionModel additions

### `progress`

Represents the bounded latest-run execution summary returned on execution detail.

| Field | Type | Description |
| --- | --- | --- |
| `progress` | `ExecutionProgress` or null | Latest-run progress summary for `MoonMind.Run`; `null` when unavailable or unsupported |

### `stepsHref`

Compatibility link to the latest-run step-ledger route.

| Field | Type | Description |
| --- | --- | --- |
| `stepsHref` | string or null | Canonical route for loading the latest/current run step ledger |

## ExecutionProgress

Reuses the Phase 1 bounded workflow contract.

| Field | Type | Description |
| --- | --- | --- |
| `total` | int | Total planned steps in the latest/current run |
| `pending` | int | Planned but not ready |
| `ready` | int | Ready to execute |
| `running` | int | Currently executing |
| `awaitingExternal` | int | Waiting on runtime/provider progress |
| `reviewing` | int | Under structured review/check processing |
| `succeeded` | int | Succeeded in the latest/current run |
| `failed` | int | Failed in the latest/current run |
| `skipped` | int | Intentionally skipped |
| `canceled` | int | Canceled |
| `currentStepTitle` | string or null | Display-safe active step label |
| `updatedAt` | datetime | Last meaningful progress mutation |

## StepLedgerSnapshotModel

The API route returns the same canonical payload already owned by workflow queries.

| Field | Type | Description |
| --- | --- | --- |
| `workflowId` | string | Durable execution identifier |
| `runId` | string | Current/latest run identifier |
| `runScope` | literal `"latest"` | V1 latest-run semantics |
| `steps` | list[`StepLedgerRow`] | Ordered rows for the latest/current run |

## Latest-run identity rules

- Execution detail stays keyed by `workflowId`.
- The steps route stays keyed by `workflowId`.
- `runId` comes from the workflow query payload and may differ from stale projection state during Continue-As-New rollover.
- Step attempt identity remains scoped by `(workflowId, runId, logicalStepId, attempt)`.
