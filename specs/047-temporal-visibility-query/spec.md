# Feature Specification: Temporal Visibility Query Model

**Feature Branch**: `047-temporal-visibility-query`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs/Temporal/VisibilityAndUiQueryModel.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/VisibilityAndUiQueryModel.md` §4.2 (lines 87-102) | Temporal Visibility must be the source of truth for Temporal-managed list, filter, pagination, and count semantics, and adapters must preserve Visibility semantics even if projections still exist. |
| DOC-REQ-002 | `docs/Temporal/VisibilityAndUiQueryModel.md` §5.1-5.2 (lines 108-124) | The canonical query entity is a Workflow Execution identified by durable `workflowId`, while `runId` remains debug metadata and must not replace `workflowId` as the primary product handle. |
| DOC-REQ-003 | `docs/Temporal/VisibilityAndUiQueryModel.md` §5.2.1 (lines 126-138) | Task-oriented compatibility surfaces must use `taskId == workflowId`, must not mint a second opaque identifier, and must canonicalize any temporary legacy aliases back to `workflowId` routes. |
| DOC-REQ-004 | `docs/Temporal/VisibilityAndUiQueryModel.md` §5.3-5.4 (lines 140-176) | Temporal-backed list and detail payloads must expose the documented stable canonical fields, including exact state fields and optional wait/debug metadata. |
| DOC-REQ-005 | `docs/Temporal/VisibilityAndUiQueryModel.md` §6.1-6.3 (lines 182-208) | MoonMind Search Attributes must use `mm_` snake_case naming, include required attributes `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, and `mm_entry`, and keep optional attributes bounded. |
| DOC-REQ-006 | `docs/Temporal/VisibilityAndUiQueryModel.md` §6.4-6.6 (lines 210-267) | Deferred attributes such as `mm_stage`, `mm_error_category`, free text, unbounded tags, and child/activity-level fields must stay out of v1 unless the document is updated first, and `mm_entry` is required in v1. |
| DOC-REQ-007 | `docs/Temporal/VisibilityAndUiQueryModel.md` §6.5 `mm_state` (lines 224-244) | `mm_state` must use the fixed v1 value set, be set at workflow start, and map terminal states consistently with Temporal close status. |
| DOC-REQ-008 | `docs/Temporal/VisibilityAndUiQueryModel.md` §6.5 `mm_owner_type` (lines 246-259) | `mm_owner_type` must use `user|system|service`, always be populated with `mm_owner_id`, never use `unknown`, and enforce user versus operator visibility rules. |
| DOC-REQ-009 | `docs/Temporal/VisibilityAndUiQueryModel.md` §7.1-7.3 (lines 271-293) | Memo must include required `title` and `summary`, optional safe references only, remain small and display-safe, and never become a filtering or large-payload channel. |
| DOC-REQ-010 | `docs/Temporal/VisibilityAndUiQueryModel.md` §8.1-8.4 (lines 297-357) | Temporal-backed queries must support the documented exact filter set, prioritize `workflowType`, `ownerId`, and `state` now, add `entry` and `ownerType` next, and keep free-text, fuzzy, OR, date-range, child, and activity filtering out of v1. |
| DOC-REQ-011 | `docs/Temporal/VisibilityAndUiQueryModel.md` §9.1-9.3 (lines 361-402) | Default ordering must be `mm_updated_at DESC` then `workflowId DESC`, with `mm_updated_at` changing only on meaningful user-visible mutations rather than low-level telemetry noise. |
| DOC-REQ-012 | `docs/Temporal/VisibilityAndUiQueryModel.md` §10.1-10.4 (lines 417-468) | Exact Temporal/MoonMind state must be preserved alongside compatibility `dashboardStatus`, including the fixed status mapping and required `waitingReason` plus `attentionRequired` semantics for `awaiting_external`. |
| DOC-REQ-013 | `docs/Temporal/VisibilityAndUiQueryModel.md` §11.1-11.4 (lines 472-516) | Pagination must use opaque `nextPageToken` with stable filter scope, and count responses must support `exact`, `estimated_or_unknown`, or omitted totals according to backend capability. |
| DOC-REQ-014 | `docs/Temporal/VisibilityAndUiQueryModel.md` §11.5-11.6 (lines 518-540) | Unified multi-source pages must not reuse Temporal page tokens as universal cursors and must handle action freshness by patching acted-on rows, background refetching, and exposing stale-state indicators. |
| DOC-REQ-015 | `docs/Temporal/VisibilityAndUiQueryModel.md` §12.1-12.3 (lines 544-584) | Adapter APIs should promote the documented top-level fields for UI consumers, may still return raw maps for debug/admin uses, and should keep `artifactRefs[]` off list rows unless payload size remains reasonable. |
| DOC-REQ-016 | `docs/Temporal/VisibilityAndUiQueryModel.md` §13.1-13.3 (lines 588-624) | UI integration must either preserve Temporal-backed behavior through compatibility adapters or add a first-class `temporal` dashboard source, with both paths preserving canonical identifiers, status, ordering, and wait metadata. |
| DOC-REQ-017 | `docs/Temporal/VisibilityAndUiQueryModel.md` §14.1-14.3 (lines 628-659) | Projection/cache layers may mirror canonical fields and add helper metadata, but they must not redefine canonical semantics and must repair drift in favor of Temporal-backed truth. |
| DOC-REQ-018 | `docs/Temporal/VisibilityAndUiQueryModel.md` §16 (lines 697-710) | Delivery is only complete when search attributes, memo fields, ordering, status mapping, pagination/count semantics, identifier bridge, owner semantics, waiting metadata, and UI integration path are all fixed and enforced in runtime behavior. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Query Temporal Executions Consistently (Priority: P1)

As a dashboard or API consumer, I can list and inspect Temporal-managed executions using one canonical query model so filtering, ordering, and row fields stay consistent across product surfaces.

**Why this priority**: Query consistency is the core contract this feature exists to establish, and other UI behavior depends on it.

**Independent Test**: Seed Temporal-backed executions with different owners, entries, workflow types, states, and update timestamps, then verify list/detail responses expose the canonical fields, exact filter behavior, and stable ordering.

**Acceptance Scenarios**:

1. **Given** Temporal-managed executions exist, **When** a list query is run, **Then** results are ordered by `updatedAt` descending with `workflowId` as the deterministic tie-breaker.
2. **Given** a Temporal-backed detail request, **When** the execution is returned, **Then** the payload exposes `workflowId` as the durable identifier and shows `runId` only as run/debug metadata.
3. **Given** a client applies supported exact filters, **When** the query executes, **Then** filtering uses only the documented Visibility-backed fields and excludes unsupported v1 filter types.

---

### User Story 2 - Preserve Task Compatibility Without Breaking Temporal Semantics (Priority: P1)

As a user navigating existing task-oriented surfaces, I can work with Temporal-backed rows without losing the exact Temporal state model, canonical identifiers, or ownership rules.

**Why this priority**: MoonMind currently still exposes task-oriented routes, so compatibility has to work without creating a second conflicting query contract.

**Independent Test**: Request Temporal-backed rows through compatibility adapters and verify `taskId == workflowId`, exact state plus compatibility status mapping, ownership scoping, and wait metadata behavior.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed row is shown on a task-oriented surface, **When** the row is serialized, **Then** `taskId` equals `workflowId` and no secondary opaque identifier is introduced.
2. **Given** an execution is in `awaiting_external`, **When** the row is returned, **Then** exact state remains visible while compatibility `dashboardStatus` is derived from the fixed mapping and wait metadata is included.
3. **Given** a standard end user queries task-oriented data, **When** the request is scoped, **Then** only that user’s `mm_owner_type=user` executions are visible unless an explicit operator/admin path is used.

---

### User Story 3 - Handle Pagination, Counts, and Refresh During Migration (Priority: P2)

As an operator using list and action flows during migration, I can page through Temporal-backed data safely and understand when counts or list freshness are degraded.

**Why this priority**: Migration behavior is where query semantics usually drift, so explicit pagination and freshness rules are necessary to prevent broken UI assumptions.

**Independent Test**: Exercise paginated list flows, change filters mid-session, trigger updates/cancels on one execution, and verify token invalidation, count handling, and acted-on row refresh behavior.

**Acceptance Scenarios**:

1. **Given** a client changes filters after receiving `nextPageToken`, **When** it tries to reuse the prior token, **Then** the token is treated as invalid for the new filter scope.
2. **Given** an action mutates one execution, **When** the action response returns, **Then** the acted-on row can be patched from that response while the broader list refresh happens asynchronously.
3. **Given** a multi-source task page merges Temporal-backed rows with other sources, **When** pagination is applied, **Then** Temporal page tokens are not reused as universal task-list cursors.

### Edge Cases

- A legacy task route receives an old alias instead of `workflowId` and must canonicalize without exposing `runId` as the durable handle.
- A non-admin caller attempts to query another user’s executions by manipulating `ownerId` or `ownerType`.
- An execution remains in `awaiting_external` with `attentionRequired = false`, and the compatibility dashboard must avoid implying the current user needs to act.
- Two executions share the same `mm_updated_at`, requiring deterministic ordering by descending `workflowId`.
- A client retries a stale `nextPageToken` after changing filters or sort scope.
- Projection data drifts from Temporal-backed canonical fields during migration, requiring repair rather than semantic fallback to projection values.
- Count is unavailable or not exact, so the UI must page without presenting synthetic authoritative totals.
- List rows are large enough that `artifactRefs[]` would exceed reasonable payload size and must stay detail-only.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat Temporal Visibility as the semantic source of truth for Temporal-managed list, filter, pagination, and count behavior, even when an app database projection still exists for migration or caching. (DOC-REQ-001)
- **FR-002**: The system MUST model the canonical query entity as a Workflow Execution identified by durable `workflowId`, with `runId` retained only as detail/debug metadata rather than the primary handle. (DOC-REQ-002)
- **FR-003**: The system MUST make `taskId` equal to `workflowId` on Temporal-backed compatibility surfaces, must not mint a second opaque identifier, and MUST canonicalize any accepted legacy aliases back to `workflowId` routes. (DOC-REQ-003)
- **FR-004**: The system MUST expose the documented canonical list and detail fields for Temporal-backed payloads, including exact state fields, owner fields, timestamps, and optional wait/debug metadata where applicable. (DOC-REQ-004, DOC-REQ-015)
- **FR-005**: The system MUST enforce MoonMind Search Attribute naming as `mm_`-prefixed lowercase snake_case and MUST persist required v1 attributes `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, and `mm_entry`. (DOC-REQ-005)
- **FR-006**: The system MUST keep optional filter attributes bounded (`mm_repo`, `mm_integration`) and MUST reject ad hoc introduction of deferred v1 attributes such as free text, unbounded tags, or child/activity-level filtering fields without first updating the governing document. (DOC-REQ-005, DOC-REQ-006)
- **FR-007**: The system MUST support only the v1 `mm_state` value set `initializing|planning|executing|awaiting_external|finalizing|succeeded|failed|canceled`, set `mm_state` on workflow start, and map terminal values consistently with Temporal close status. (DOC-REQ-007)
- **FR-008**: The system MUST support only `user|system|service` for `mm_owner_type`, MUST populate `mm_owner_type` and `mm_owner_id` together, MUST not use `unknown` as a target-state owner identifier, and MUST enforce user versus operator visibility semantics from those fields. (DOC-REQ-008)
- **FR-009**: The system MUST persist required Memo fields `title` and `summary`, MAY include safe artifact reference fields, and MUST keep Memo display-safe, bounded, and free of secrets or large payloads. (DOC-REQ-009)
- **FR-010**: The system MUST support exact-match filters for `workflowType`, `ownerType`, `ownerId`, `state`, `entry`, and optional `repo` and `integration` when those bounded fields exist. (DOC-REQ-010)
- **FR-011**: The system MUST preserve `workflowType`, `ownerId`, and `state` filtering on current Temporal-backed adapter surfaces, and MUST add `entry` and `ownerType` filtering before dashboard flows rely on Temporal-backed list filtering as a first-class product behavior. (DOC-REQ-010)
- **FR-012**: The system MUST keep free-text search, fuzzy title search, arbitrary multi-field OR composition, arbitrary date-range filtering, child-workflow filtering, and activity-level filtering out of the v1 query contract. (DOC-REQ-010)
- **FR-013**: The system MUST apply canonical default ordering as `mm_updated_at DESC` followed by `workflowId DESC`, and Temporal-backed rows MUST not imply queue position or FIFO semantics. (DOC-REQ-011)
- **FR-014**: The system MUST update `mm_updated_at` only for meaningful user-visible mutations such as state changes, accepted edits, visible signal effects, cancel/rerun actions, terminal transitions, bounded progress checkpoints, and material title/summary changes, and MUST not treat heartbeats, log lines, or internal retries as recency updates. (DOC-REQ-011)
- **FR-015**: The system MUST preserve exact `state`, `temporalStatus`, and `closeStatus` fields while also exposing compatibility `dashboardStatus` using the fixed v1 mapping for `queued`, `running`, `awaiting_action`, `succeeded`, `failed`, and `cancelled`. (DOC-REQ-012)
- **FR-016**: The system MUST populate bounded `waitingReason` values whenever `state = awaiting_external`, MUST set `attentionRequired` only for human/operator-blocked work, and MUST avoid implying user action is required when `attentionRequired = false`. (DOC-REQ-012)
- **FR-017**: The system MUST treat `nextPageToken` as opaque, MUST bind tokens to the same endpoint and filter/sort scope, MUST invalidate token reuse after scope changes, and MUST support `countMode` values `exact` and `estimated_or_unknown` or omit `count` when exact totals are not available. (DOC-REQ-013)
- **FR-018**: The system MUST keep separate source cursors for unified multi-source pages or use a backend aggregator that owns the merged contract, MUST patch the acted-on row from successful action responses when possible, MUST background-refetch the active query afterward, and MUST expose stale-state indicators on operator-facing list/detail views. (DOC-REQ-014)
- **FR-019**: The system MUST promote the documented Temporal-backed top-level adapter fields for UI consumers, MAY return raw `searchAttributes` and `memo` objects for admin/debug parity, and MUST keep `artifactRefs[]` off list responses unless payload size remains reasonable. (DOC-REQ-004, DOC-REQ-015)
- **FR-020**: The system MUST support one of two UI integration paths for Temporal-backed work: compatibility adapters that preserve canonical Temporal semantics or a first-class `temporal` dashboard source that implements the minimum list/detail/action/runtime-config requirements from the source document. (DOC-REQ-016, DOC-REQ-018)
- **FR-021**: The system MUST ensure any projection or cache layer mirrors canonical Temporal-backed fields faithfully, MAY add helper metadata only for non-semantic concerns, and MUST repair drift in favor of Temporal-backed truth rather than redefining the contract around projection behavior. (DOC-REQ-001, DOC-REQ-017)
- **FR-022**: Deliverables for this feature MUST include production runtime code changes and automated validation tests that enforce all mapped source-document requirements; docs-only completion is not acceptable. (DOC-REQ-018, Runtime intent guard)

### Key Entities *(include if feature involves data)*

- **WorkflowExecutionQueryRow**: Canonical Temporal-backed row exposed to list and detail consumers with durable identifiers, state fields, owner metadata, timestamps, and compatibility status.
- **VisibilitySearchMetadata**: Search Attribute contract for Temporal-managed executions including required `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`, and optional bounded filters such as repo or integration.
- **ExecutionMemoProjection**: Small, human-readable display metadata containing `title`, `summary`, and optional safe artifact references without carrying large payloads or filter semantics.
- **CompatibilityIdentifierBridge**: Mapping rules that keep `taskId`, `workflowId`, and any temporary legacy aliases aligned while preventing `runId` from becoming the durable route handle.
- **DashboardStatusProjection**: Compatibility grouping derived from exact Temporal-backed state plus bounded `waitingReason` and `attentionRequired` metadata for task-oriented surfaces.
- **PaginationContract**: Opaque cursor and count behavior tied to one query scope, including degraded-count behavior and migration-safe multi-source aggregation rules.
- **ProjectionMirrorRecord**: App-database or cache representation of Temporal-backed execution metadata that mirrors canonical fields and can be repaired when drift is detected.

### Assumptions & Dependencies

- Existing Temporal-backed execution APIs and task-oriented dashboard surfaces remain the immediate integration points for this feature.
- Authorization infrastructure can distinguish standard users from operator/admin callers for ownership scoping.
- Temporal-backed execution metadata is available to populate the canonical top-level fields without requiring raw artifact hydration for basic list rows.
- This delivery selects the compatibility-adapter path for UI integration; a first-class `temporal` dashboard source remains a follow-up rather than part of the required runtime slice.
- Validation can cover both direct API behavior and compatibility/task-surface behavior without relying on manual dashboard verification alone.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated validation shows 100% of Temporal-backed list/detail responses expose `workflowId`, exact state fields, owner metadata, and canonical ordering semantics for supported query paths.
- **SC-002**: Automated authorization tests show 100% rejection of non-admin attempts to query another user’s executions and 100% exclusion of `system` and `service` executions from standard end-user surfaces.
- **SC-003**: Automated lifecycle-query tests show 100% correct `mm_state` and `dashboardStatus` mapping for each documented v1 state, including `awaiting_external` wait metadata behavior.
- **SC-004**: Automated pagination tests show 100% opaque-token handling, filter-scope invalidation, and correct `countMode` behavior when counts are exact, estimated/unknown, or omitted.
- **SC-005**: Automated migration-behavior tests show acted-on rows can be refreshed from action responses while broader list refresh remains asynchronous and multi-source pages do not reuse Temporal page tokens as universal cursors.
- **SC-006**: The feature diff includes production runtime implementation changes plus validation tests that cover every `DOC-REQ-*` mapping, with no docs-only completion path.
