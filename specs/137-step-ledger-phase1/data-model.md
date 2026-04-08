# Data Model: Step Ledger Phase 1

## ExecutionProgress

Represents the bounded execution-level progress snapshot surfaced by workflow queries now and by execution detail later.

| Field | Type | Notes |
| --- | --- | --- |
| `total` | int | Total planned logical steps for the active run |
| `pending` | int | Steps planned but not yet ready |
| `ready` | int | Steps eligible for dispatch |
| `running` | int | Steps actively executing |
| `awaitingExternal` | int | Steps waiting on external/runtime progress |
| `reviewing` | int | Steps in structured review/check processing |
| `succeeded` | int | Terminal succeeded count |
| `failed` | int | Terminal failed count |
| `skipped` | int | Terminal skipped count |
| `canceled` | int | Terminal canceled count |
| `currentStepTitle` | string or null | Operator-facing title of the most relevant active step |
| `updatedAt` | datetime | Last meaningful user-visible ledger mutation |

Rules:

- Derived entirely from compact workflow state.
- No artifact reads are required.
- Counts must always sum to `total`.

## StepLedgerSnapshot

Latest-run query response for `MoonMind.Run`.

| Field | Type | Notes |
| --- | --- | --- |
| `workflowId` | string | Stable logical task identity |
| `runId` | string | Current/latest Temporal run ID |
| `runScope` | string | Fixed to `"latest"` in v1 |
| `steps` | list[`StepLedgerRow`] | Ordered step rows for the active/latest run |

## StepLedgerRow

Compact current/latest attempt view for one logical step in the active run.

| Field | Type | Notes |
| --- | --- | --- |
| `logicalStepId` | string | Stable plan-node identifier |
| `order` | int | Stable display order |
| `title` | string | Operator-facing step title |
| `tool` | object | Display-safe tool descriptor |
| `dependsOn` | list[string] | Upstream logical step IDs |
| `status` | enum | One of the canonical v1 statuses |
| `waitingReason` | string or null | Bounded blocked-state reason |
| `attentionRequired` | bool | Whether operator action is currently needed |
| `attempt` | int | Current/latest attempt number for this run |
| `startedAt` | datetime or null | Start time for the current attempt |
| `updatedAt` | datetime | Last meaningful step mutation |
| `summary` | string or null | Short bounded operator summary |
| `checks` | list[`StepCheck`] | Structured check rows |
| `refs` | `StepRefs` | Child-workflow and task-run refs |
| `artifacts` | `StepArtifactSlots` | Semantic artifact refs |
| `lastError` | string or null | Bounded latest error summary |

Rules:

- Rows are keyed by `(workflowId, runId, logicalStepId)`; the active attempt number disambiguates retries.
- `summary` and `lastError` are bounded display strings only.
- Default empty/default values are returned for `checks`, `refs`, and `artifacts`.

## StepCheck

Structured per-step check or review state.

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | string | Check producer identifier |
| `status` | string | `pending`, `passed`, `failed`, or future bounded statuses |
| `summary` | string or null | Short display summary |
| `retryCount` | int | Count of retries triggered by the check |
| `artifactRef` | string or null | External evidence reference |

## StepRefs

| Field | Type | Notes |
| --- | --- | --- |
| `childWorkflowId` | string or null | Child workflow ID when relevant |
| `childRunId` | string or null | Child run ID when relevant |
| `taskRunId` | string or null | Managed/runtime observability ID when relevant |

## StepArtifactSlots

| Field | Type | Notes |
| --- | --- | --- |
| `outputSummary` | string or null | Ref for summary evidence |
| `outputPrimary` | string or null | Ref for primary output |
| `runtimeStdout` | string or null | Ref for stdout artifact |
| `runtimeStderr` | string or null | Ref for stderr artifact |
| `runtimeMergedLogs` | string or null | Ref for merged logs |
| `runtimeDiagnostics` | string or null | Ref for diagnostics bundle |
| `providerSnapshot` | string or null | Ref for provider payload snapshot |

## Transition Rules

- Initial state after plan resolution:
  - Dependency-free nodes become `ready`.
  - Dependent nodes remain `pending`.
- Dispatch transition:
  - `ready -> running`
  - increment attempt if this is a retry
  - set `startedAt` and refresh `updatedAt`
- Waiting transitions:
  - `running -> awaiting_external`
  - `running -> reviewing`
- Terminal transitions:
  - `running|awaiting_external|reviewing -> succeeded|failed|skipped|canceled`
- A successful upstream completion may transition downstream steps from `pending -> ready`.
