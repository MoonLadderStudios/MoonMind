# Data Model: Step Ledger Phase 6

## LatestRunExecutionRead

Represents the task-detail execution model after latest-run reconciliation.

| Field | Type | Description |
| --- | --- | --- |
| `workflowId` | string | Stable logical execution identifier |
| `runId` | string | Latest/current Temporal run used by the task detail |
| `temporalRunId` | string | Alias of the latest/current run for debug compatibility |
| `progress` | `ExecutionProgress \| null` | Bounded latest-run summary, unchanged externally |

Rules:

- `runId` remains stable for one Temporal run and rotates on rerun / Continue-As-New.
- The public `progress` object does not expose extra reconciliation fields.

## InternalProgressQueryEnvelope

Internal workflow-to-router query payload used to keep detail reads truthful.

| Field | Type | Description |
| --- | --- | --- |
| `runId` | string | Latest/current workflow run id for reconciliation |
| `total` ... `updatedAt` | `ExecutionProgress` fields | Existing bounded progress fields |

Rules:

- The router may read `runId` from the raw query payload.
- Public API responses continue validating and returning only the documented `ExecutionProgress` shape.

## RunScopedArtifactRead

Represents generic execution-wide artifact browsing on task detail.

| Field | Type | Description |
| --- | --- | --- |
| `namespace` | string | Temporal namespace |
| `workflowId` | string | Stable logical execution id |
| `runId` | string | Latest/current run whose artifacts are being browsed |

Rules:

- Default task detail remains latest-run-only.
- Artifact reads must not silently mix rows from two run ids in one default panel.
