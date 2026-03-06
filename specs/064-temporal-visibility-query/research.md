# Research: Temporal Visibility Query Model

## Decision 1: Keep `workflowId` as the single durable product handle

- **Decision**: Temporal-backed compatibility rows will use `taskId == workflowId`, while `runId` remains detail/debug metadata only.
- **Rationale**: The source document explicitly makes Workflow Execution the canonical entity and prohibits replacing the durable handle with `runId` or a second opaque alias.
- **Alternatives considered**:
  - Use `runId` for detail routes: rejected because it breaks durable identity across Continue-As-New and conflicts with the documented product handle.
  - Mint a dashboard-only alias table: rejected because it creates another migration dependency and reintroduces semantic drift.

## Decision 2: Treat Temporal Visibility semantics as authoritative even while projections remain

- **Decision**: Query/filter/order/pagination/count behavior will be owned by the Temporal execution service and adapter contracts, with the `temporal_executions` table kept as a cache/reconciliation layer.
- **Rationale**: The current code already centralizes list behavior in `TemporalExecutionService`; extending that surface is lower risk than letting dashboard or projection code redefine semantics.
- **Alternatives considered**:
  - Let the projection stay semantically authoritative: rejected because it contradicts the document and complicates later migration off the projection.
  - Move all semantics into dashboard JS: rejected because the browser should consume normalized contracts, not reconstruct canonical backend rules.

## Decision 3: Freeze the v1 Search Attribute set to bounded fields only

- **Decision**: Required attributes are `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, and `mm_entry`; optional bounded fields are limited to `mm_repo` and `mm_integration` when intentionally introduced.
- **Rationale**: The document explicitly defers free text, unbounded tags, `mm_stage`, `mm_error_category`, and child/activity-level fields, and the existing model already carries the required v1 fields cleanly.
- **Alternatives considered**:
  - Add extra attributes now for convenience: rejected because it weakens the governance contract and expands filter surface area without clear product need.
  - Store title/summary in Search Attributes for filtering: rejected because the document reserves Memo for display-safe presentation and keeps free-text filtering out of v1.

## Decision 4: Keep canonical payloads top-level and exact-state preserving

- **Decision**: Adapter responses will continue to promote top-level canonical fields (`workflowId`, `taskId`, `state`, `temporalStatus`, `closeStatus`, `dashboardStatus`, `ownerType`, `ownerId`, timestamps, and bounded wait metadata) while raw `searchAttributes` and `memo` remain debug/admin parity fields.
- **Rationale**: UI consumers need stable fields without parsing raw metadata maps, but the system still needs parity/debug access for operators and reconciliation tooling.
- **Alternatives considered**:
  - Return only raw `searchAttributes` and `memo`: rejected because it pushes canonical parsing burden into multiple consumers.
  - Collapse exact state into only `dashboardStatus`: rejected because the source document requires exact Temporal/MoonMind state preservation.

## Decision 5: Preserve deterministic ordering and scope-bound opaque pagination

- **Decision**: Default ordering stays `mm_updated_at DESC`, then `workflowId DESC`; page tokens remain opaque and bound to the current filter/sort scope.
- **Rationale**: The current service already uses deterministic ordering and scope-bound tokens; extending the same pattern keeps behavior stable and testable.
- **Alternatives considered**:
  - Expose offset-based pagination publicly: rejected because it leaks implementation detail and weakens future compatibility with true Visibility cursors.
  - Reuse Temporal page tokens as unified dashboard cursors: rejected because mixed-source list pages need per-source cursors or an aggregator-owned merged contract.

## Decision 6: Use compatibility adapters for this delivery, not a first-class dashboard source

- **Decision**: The chosen UI path for this delivery is to keep Temporal-backed work on the existing compatibility adapters (`/api/executions` and task-oriented identifier semantics) while deferring a first-class `temporal` dashboard source.
- **Rationale**: The current runtime implementation already hardens the execution adapters around canonical identifiers, ownership, status mapping, and pagination semantics; forcing a dashboard-source expansion in the same slice would widen scope without changing the document-level contract.
- **Alternatives considered**:
  - Add a first-class `temporal` dashboard source now: deferred because it is optional under `DOC-REQ-016` and not required to preserve canonical Temporal semantics on the current compatibility path.
  - Add `temporal` as a worker runtime: rejected because Temporal is an orchestration substrate, not a model/runtime choice like `codex` or `gemini`.
  - Create a separate Temporal-only UI: rejected because it would split the product surface before the migration contract is complete.

## Decision 7: Make runtime behavior and validation the completion gate

- **Decision**: The feature remains in runtime implementation mode, meaning code changes and automated validation tests are required for completion; docs alone are insufficient.
- **Rationale**: The spec explicitly calls out production runtime deliverables and tests, and MoonMind orchestration mode for this task requires runtime-vs-docs alignment.
- **Alternatives considered**:
  - Treat the doc implementation as satisfied by plan/spec updates: rejected because it would violate both the spec and the selected orchestration mode.
