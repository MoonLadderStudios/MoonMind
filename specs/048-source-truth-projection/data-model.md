# Data Model: Temporal Source of Truth and Projection Model

## Entity: TemporalAuthoritySnapshot

- **Description**: Canonical runtime snapshot assembled from Temporal execution identity/history, close status/run chain, Visibility state, Search Attributes, and Memo.
- **Fields**:
  - `workflowId` (string)
  - `latestRunId` (string)
  - `namespace` (string)
  - `workflowType` (string)
  - `temporalStatus` (enum: `running|completed|failed|canceled|terminated|timed_out|continued_as_new`)
  - `mmState` (enum aligned to `MoonMindWorkflowState`)
  - `searchAttributes` (object)
  - `memo` (object)
  - `artifactRefs` (array[string])
  - `ownerType` (enum: `user|system|service`)
  - `ownerId` (string nullable)
  - `startedAt` (timestamp)
  - `updatedAt` (timestamp)
  - `closedAt` (timestamp nullable)
- **Rules**:
  - This snapshot is authoritative for Temporal-managed execution lifecycle truth.
  - `workflowId` is stable across Continue-As-New.
  - `latestRunId` tracks the latest run in the Temporal chain and is not a second durable identity.

## Entity: TemporalExecutionSourceMirror

- **Description**: App-local mirror row stored in `temporal_execution_sources` for staging, repair, and migration support.
- **Fields**:
  - Mirrors `TemporalAuthoritySnapshot`
  - `createIdempotencyKey` (string nullable)
  - `lastUpdateIdempotencyKey` (string nullable)
  - `lastUpdateResponse` (object nullable)
  - `pendingParametersPatch` (object nullable)
  - `paused` (boolean)
  - `awaitingExternal` (boolean)
  - `stepCount` (integer)
  - `waitCycleCount` (integer)
  - `rerunCount` (integer)
- **Rules**:
  - This row may help with migration and repair but does not replace Temporal as the source of truth.
  - When direct Temporal reads are available, APIs must not present this mirror as the final authority.

## Entity: ExecutionProjection

- **Description**: Primary app-local read model stored in `temporal_executions` for compatibility joins, indexing, repair, observability, and explicitly allowed fallback reads.
- **Fields**:
  - `workflowId` (string primary key)
  - `runId` (string; latest known run)
  - `namespace` (string)
  - `workflowType` (string)
  - `state` (enum aligned to `MoonMindWorkflowState`)
  - `closeStatus` (enum nullable)
  - `searchAttributes` (object)
  - `memo` (object)
  - `artifactRefs` (array[string])
  - `inputRef` (string nullable)
  - `planRef` (string nullable)
  - `manifestRef` (string nullable)
  - `parameters` (object)
  - `pendingParametersPatch` (object nullable)
  - `paused` (boolean)
  - `awaitingExternal` (boolean)
  - `stepCount` (integer)
  - `waitCycleCount` (integer)
  - `rerunCount` (integer)
  - `projectionVersion` (integer)
  - `lastSyncedAt` (timestamp)
  - `syncState` (enum: `fresh|stale|repair_pending|orphaned`)
  - `syncError` (string nullable)
  - `sourceMode` (enum: `projection_only|mixed|temporal_authoritative`)
  - `startedAt` (timestamp)
  - `updatedAt` (timestamp)
  - `closedAt` (timestamp nullable)
- **Rules**:
  - There is exactly one primary projection row per `workflowId`.
  - Continue-As-New updates `runId` in place and must not create a second primary row.
  - `sourceMode` and `syncState` describe provenance/health, not authority transfer.

## Entity: ProjectionSyncState

- **Description**: Operational state for one projection row relative to Temporal truth.
- **Fields**:
  - `syncState` (enum: `fresh|stale|repair_pending|orphaned`)
  - `syncError` (string nullable)
  - `lastSyncedAt` (timestamp)
  - `projectionVersion` (integer)
  - `sourceMode` (enum: `projection_only|mixed|temporal_authoritative`)
- **Rules**:
  - `fresh` means the latest repair/sync succeeded.
  - `stale` means the row is readable but known to lag.
  - `repair_pending` means a write or refresh succeeded authoritatively but the projection is not fully caught up.
  - `orphaned` means the projection lacks authoritative Temporal backing and must be hidden from normal active reads.

## Entity: WorkflowRunChain

- **Description**: Temporal-native lineage for one Workflow ID across Continue-As-New boundaries.
- **Fields**:
  - `workflowId` (string)
  - `latestRunId` (string)
  - `closeStatus` (enum nullable)
  - `rerunCount` (integer convenience field)
  - `continuedAsNew` (boolean)
- **Rules**:
  - Run-chain truth comes from Temporal, not from projection counters.
  - `rerunCount` may support UI messaging but cannot replace Temporal run history.

## Entity: CompatibilityExecutionView

- **Description**: Task-oriented or transitional payload that may combine canonical execution data with local joins while preserving Temporal identity.
- **Fields**:
  - `taskId` (string; must equal `workflowId` for Temporal-backed rows)
  - `workflowId` (string)
  - `runId` (string)
  - `sourceKind` (enum: `temporal_direct|compatibility_join|projection_fallback|mixed`)
  - `countMode` (enum: `exact|estimated_or_unknown`)
  - `sortMode` (string)
  - `state` (string)
  - `temporalStatus` (string)
  - `sourceMetadata` (object)
  - `compatibilityFields` (object)
- **Rules**:
  - Compatibility payloads must not invent queue-order semantics for Temporal-backed rows.
  - Count/sort metadata must describe the actual active source behavior.
  - Canonical identifiers remain present even when task-oriented labels are primary in the UI.

## Entity: ExecutionWriteRequest

- **Description**: Normalized request envelope for start/update/signal/cancel/terminate flows before they are sent to Temporal.
- **Fields**:
  - `operation` (enum: `start|update|signal|cancel|terminate`)
  - `workflowId` (string nullable for start)
  - `idempotencyKey` (string nullable)
  - `ownerContext` (object)
  - `payload` (object)
  - `validatedAt` (timestamp)
- **Rules**:
  - Validation and auth happen before the Temporal call.
  - Authoritative acceptance or rejection comes from Temporal/workflow outcomes, not projection mutation.

## Entity: ProjectionRepairJob

- **Description**: Deterministic repair pass that reconciles one workflow or a recent window of workflows against authoritative Temporal truth.
- **Fields**:
  - `trigger` (enum: `post_write|read_repair|periodic_sweep|startup_backfill`)
  - `workflowId` (string nullable for sweeps)
  - `expectedSourceMode` (enum)
  - `detectedDrift` (enum: `missing_projection|stale_run|stale_state|stale_visibility|artifact_drift|orphaned_projection`)
  - `appliedAction` (enum: `create|refresh|overwrite|quarantine|noop`)
  - `executedAt` (timestamp)
  - `error` (string nullable)
- **Rules**:
  - Repair ordering is: existence/identity, run/terminal status, domain state/search attributes, memo, artifact refs, then sync metadata.
  - Orphaned active rows must be quarantined rather than served as normal running executions.

## Entity: DegradedReadDecision

- **Description**: Route-level decision record for whether stale or partial reads are allowed when one subsystem is impaired.
- **Fields**:
  - `routeName` (string)
  - `requestedMode` (enum: `authoritative|compatibility|fallback`)
  - `temporalReachable` (boolean)
  - `visibilityReachable` (boolean)
  - `projectionStoreReachable` (boolean)
  - `fallbackAllowed` (boolean)
  - `servedSourceKind` (enum)
  - `operatorSignal` (object)
- **Rules**:
  - Production write operations ignore fallback and fail when Temporal is unavailable.
  - Reads may fall back only when the route contract explicitly allows it.
  - Outage state and source selection must be observable.

## State Transitions

- **Projection sync lifecycle**:
  - `fresh -> stale`
  - `fresh|stale -> repair_pending`
  - `repair_pending -> fresh`
  - `fresh|stale|repair_pending -> orphaned`
  - `orphaned -> fresh` after authoritative repair
- **Write-path lifecycle**:
  - `validated -> temporal_accepted -> sync_applied`
  - `validated -> temporal_accepted -> repair_pending`
  - `validated -> temporal_rejected`
- **Run-chain lifecycle**:
  - `workflow_started -> executing`
  - `executing -> continued_as_new` with same `workflowId` and new `latestRunId`
  - `executing -> terminal`

## Invariants

- Temporal execution identity/history, Visibility, Search Attributes, and Memo are authoritative for Temporal-managed execution truth.
- `workflowId` is the stable bridge identity across direct and compatibility surfaces.
- There is never more than one primary execution projection row for one Workflow ID.
- Production writes must not succeed through projection-only mutation.
- Orphaned projections must not appear as active executions by default.
- Projection freshness metadata informs repair and fallback decisions but does not change lifecycle authority.
