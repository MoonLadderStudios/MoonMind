# Research: Temporal Source of Truth and Projection Model

## Decision 1: Make direct Temporal control-plane and Visibility reads the runtime authority

- **Decision**: Implement authoritative start/update/signal/cancel/describe/list behavior through dedicated Temporal control-plane and Visibility adapters under `moonmind/workflows/temporal/`.
- **Rationale**: The source document explicitly assigns lifecycle truth to Temporal execution identity/history, close status/run chain, Visibility state, and workflow-managed Search Attributes plus Memo. Keeping authority in local tables would fail the feature goal.
- **Alternatives considered**:
  - Keep `TemporalExecutionService` projection-authoritative and update docs only: rejected because runtime implementation is required.
  - Treat `TemporalExecutionCanonicalRecord` as the permanent source of truth: rejected because it remains an app-local mirror, not Temporal itself.

## Decision 2: Keep `TemporalExecutionCanonicalRecord` as a migration seam, not the final public authority

- **Decision**: Use `temporal_execution_sources` as a staging mirror, repair aid, and compatibility bridge while implementation moves live authority to Temporal-backed adapters.
- **Rationale**: The repo already has a dual-table model (`temporal_execution_sources` plus `temporal_executions`) and tests around sync markers/orphan repair. Reusing that seam reduces migration risk, but the source doc forbids normalizing app-local mirrors into the final authority.
- **Alternatives considered**:
  - Delete the source mirror immediately: rejected because repair/backfill and staged rollout would become harder.
  - Expose the source mirror as if it were canonical forever: rejected because it would preserve the same architecture mistake under a new table name.

## Decision 3: Preserve exactly one primary projection row per Workflow ID

- **Decision**: Keep `TemporalExecutionRecord.workflow_id` as the only primary key for the main execution projection and update `run_id` in place across Continue-As-New.
- **Rationale**: This directly matches the source document and the existing `TemporalExecutionRecord` schema. It also fits the current service behavior where reruns change `run_id` while keeping the same `workflow_id`.
- **Alternatives considered**:
  - Create one primary row per run: rejected because it would violate `DOC-REQ-006` and `DOC-REQ-019`.
  - Encode run history only in `rerun_count`: rejected because Temporal run chain remains the authoritative lineage source.

## Decision 4: Write paths must go Temporal first and treat projection persistence failure as repair work

- **Decision**: For start/update/signal/cancel/terminate, authorize and validate locally, execute the mutation against Temporal, then refresh or repair local mirrors from authoritative results.
- **Rationale**: The source doc requires that accepted workflow starts and mutations remain real even if local projection writes fail. Current projection-first behavior would create ghost rows or falsely accept projection-only changes.
- **Alternatives considered**:
  - Continue mutating projections first and synchronize later: rejected because projection-only success paths are explicitly forbidden.
  - Fail the user request when projection write fails after Temporal accepts: rejected because it would rewrite accepted Temporal history into a false negative.

## Decision 5: Separate authoritative Temporal reads from explicit compatibility or fallback reads

- **Decision**: `/api/executions` should move toward Visibility-backed list/filter/count and direct Temporal-backed detail reads, while compatibility/task-oriented routes may still join projection data when the route explicitly declares that posture.
- **Rationale**: The source document distinguishes final Temporal-backed routes from compatibility and degraded-mode exceptions. This separation is necessary to make `countMode`, sort semantics, and freshness claims truthful.
- **Alternatives considered**:
  - Keep one mixed read path with hidden source selection: rejected because it obscures truth and makes testing semantics ambiguous.
  - Force every compatibility route to become pure Temporal immediately: rejected because some joins/fallback routes still need staged migration.

## Decision 6: Add a hybrid repair model instead of relying on one sync trigger

- **Decision**: Use four repair triggers together: post-mutation best-effort refresh, repair-on-read, periodic sweeper, and startup/backfill repair.
- **Rationale**: The source document requires all four paths. The current service already has repair-on-read and sync markers; adding sweeper/startup flows completes the operational model and reduces long-lived drift.
- **Alternatives considered**:
  - Post-write sync only: rejected because outages and missed writes would leave drift uncorrected.
  - Periodic sweeper only: rejected because user-visible repair latency would be too large for active workflows.

## Decision 7: Treat `sync_state`, `sync_error`, and `source_mode` as operational metadata only

- **Decision**: Keep projection sync fields as indicators of freshness/provenance and repair need, but do not let them imply that authority moved from Temporal to the projection.
- **Rationale**: The source document explicitly says these fields are projection concerns, not an authority transfer. They remain useful for operators and fallback decisions.
- **Alternatives considered**:
  - Remove sync metadata once Temporal is authoritative: rejected because repair/degraded-mode observability still needs it.
  - Promote sync metadata into lifecycle authority flags: rejected because that would recreate local truth semantics.

## Decision 8: Production degraded-mode policy must reject writes without Temporal

- **Decision**: Reject production write operations when Temporal is unavailable; allow projection-only fallback only in explicitly isolated local-dev/test modes.
- **Rationale**: `DOC-REQ-018` and the spec both forbid production ghost rows and projection-only lifecycle mutation when Temporal cannot be reached.
- **Alternatives considered**:
  - Fall back to projection writes in production to keep APIs "available": rejected because it would fabricate execution truth.
  - Disable all reads when Temporal or the projection store degrades: rejected because the source document allows honest partial/stale fallback on approved routes.

## Decision 9: Compatibility surfaces must keep canonical identifiers and source metadata visible

- **Decision**: For Temporal-backed compatibility rows, preserve `taskId == workflowId`, retain canonical `workflowId`/`runId`, and expose whether the active payload is direct Temporal, compatibility-joined, or degraded projection fallback.
- **Rationale**: The migration only works if compatibility routes do not hide the Temporal backing or reintroduce legacy queue-order semantics.
- **Alternatives considered**:
  - Mint separate task IDs for compatibility views: rejected because the source document fixes the identifier bridge.
  - Hide source metadata from UI/API payloads: rejected because it makes degraded behavior and migration debugging dishonest.

## Decision 10: Introduce the official Temporal Python SDK behind adapters

- **Decision**: Add `temporalio` as a runtime dependency for direct workflow and Visibility access, but keep all SDK usage behind MoonMind adapter modules.
- **Rationale**: The repository already has the self-hosted Temporal foundation and worker topology, but no direct execution client yet. This feature needs live control-plane and Visibility integration.
- **Alternatives considered**:
  - Use shell calls to Temporal CLI/tools from request handlers: rejected because lifecycle APIs need typed, testable runtime integration.
  - Keep execution behavior table-only until a future feature: rejected because this feature's purpose is to move runtime authority now.

## Decision 11: Runtime mode is the mandatory completion gate

- **Decision**: Treat this feature as runtime implementation work end-to-end and require production code plus validation tests in downstream tasks.
- **Rationale**: The task objective and `FR-028` reject docs/spec-only completion.
- **Alternatives considered**:
  - Stop at plan/contracts: rejected as non-compliant.
  - Defer validation into a later feature: rejected because lifecycle authority changes need immediate regression coverage.
