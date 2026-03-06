# Temporal Execution Source Contract

## Purpose

Define the runtime contract for Temporal-authoritative execution lifecycle handling, projection mirroring, compatibility payloads, and degraded-mode behavior for `048-source-truth-projection`.

## 1. Authority Rules

- Temporal workflow execution identity/history, close status/run chain, Visibility state, Search Attributes, and Memo are authoritative for Temporal-managed executions.
- `TemporalExecutionRecord` is a derived read model.
- `TemporalExecutionCanonicalRecord` may remain as a migration mirror/repair seam, but it is not the final public authority for Temporal-managed list/detail semantics.
- Production architecture must not present a running execution unless authoritative Temporal backing exists.

## 2. Primary Projection Rules

- Primary key: `workflow_id`
- Mirrored latest run field: `run_id`
- One primary projection row exists per Workflow ID.
- Continue-As-New updates the existing projection row in place.
- `rerun_count` remains convenience metadata only.

## 3. Write-Path Contract

### 3.1 Start

1. Authenticate and validate request.
2. Start workflow in Temporal.
3. Receive canonical `workflowId` and `runId`.
4. Upsert source/projection mirrors from the accepted result.
5. Return source-backed execution payload.

Rules:

- If Temporal rejects the start, no production projection row may survive as an active execution.
- If Temporal accepts but source/projection persistence fails, mark repair work and preserve the accepted execution as real.

### 3.2 Update and Signal

1. Validate auth/policy and payload shape.
2. Send update or signal to Temporal.
3. Treat workflow response or accepted signal dispatch as authoritative.
4. Refresh or repair local mirrors from Temporal-visible outcomes.

Rules:

- Idempotency caches may reuse authoritative results.
- Projection-only acceptance is forbidden once the workflow is Temporal-managed.

### 3.3 Cancel or Terminate

1. Authorize request.
2. Send cancel or terminate request to Temporal.
3. Let Temporal terminal outcome become authoritative.
4. Refresh or repair projection state from the close result.

## 4. Read-Path Contract

### 4.1 Direct Temporal-Managed Views

- List/filter/count: Temporal Visibility
- Detail/describe: Temporal execution state plus safe MoonMind enrichment
- Count/sort semantics: truthful to the active source

### 4.2 Compatibility Views

- May join projections or local data for explicit compatibility reasons.
- Must preserve:
  - `workflowId`
  - `runId`
  - source metadata
  - `taskId == workflowId` for Temporal-backed rows
- Must not invent queue-order semantics for Temporal task queues.

## 5. Projection Sync and Repair

Required repair triggers:

1. Post-write refresh
2. Repair-on-read
3. Periodic sweeper
4. Startup/backfill repair

Repair ordering:

1. Execution existence and identity
2. Latest run and terminal status
3. Domain state and Search Attributes
4. Memo fields
5. Artifact refs and convenience fields
6. Sync metadata

Repair outcomes:

- `create`
- `refresh`
- `overwrite`
- `quarantine`
- `noop`

## 6. Ghost and Orphan Rules

- Active ghost rows are forbidden in production.
- A projection row without authoritative Temporal backing must transition to `sync_state=orphaned` or equivalent quarantine handling.
- Orphaned rows are hidden from default active list/detail behavior until authoritative repair succeeds.

## 7. Sync Metadata Rules

- `projection_version`, `last_synced_at`, `sync_state`, `sync_error`, and `source_mode` are operational metadata.
- These fields inform repair, observability, and fallback posture only.
- They do not transfer lifecycle authority from Temporal to the projection.

## 8. Degraded-Mode Contract

### 8.1 Temporal unavailable

- Production writes fail.
- Projection-only write fallback is allowed only in explicitly isolated local-dev/test modes.

### 8.2 Visibility unavailable, execution truth reachable

- Detail may continue from direct execution truth.
- List/count may fall back only on routes that explicitly permit stale or partial reads.
- `countMode` and source metadata must reveal the degraded posture.

### 8.3 Projection store unavailable, Temporal healthy

- Direct Temporal-backed routes may continue.
- Compatibility routes that require projections may degrade or return partial results.
- Backfill repair must restore missed projection updates after recovery.

## 9. Compatibility Serialization Rules

- Temporal-backed rows keep canonical identifiers visible.
- Compatibility payloads must include enough source metadata to distinguish:
  - direct Temporal reads
  - compatibility joins
  - projection fallback
  - mixed-source degraded results
- UI and API layers may remain task-oriented, but they cannot hide the canonical Workflow ID or authoritative source class.

## 10. Validation Gates

- No `DOC-REQ-*` item may remain unmapped in the feature traceability matrix.
- Runtime implementation must include production code plus automated validation tests.
- Tests must cover:
  - Temporal-first write semantics
  - truthful read/count/source metadata
  - Continue-As-New in-place projection updates
  - orphan/ghost handling
  - degraded-mode write rejection and honest read fallback
