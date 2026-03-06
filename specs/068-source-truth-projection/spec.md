# Feature Specification: Temporal Source of Truth and Projection Model

**Feature Branch**: `048-source-truth-projection`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs/Temporal/SourceOfTruthAndProjectionModel.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §4.1 (lines 75-82) | Temporal-managed execution truth must come from Temporal execution identity/history, close status/run chain, Visibility state, and workflow-managed Search Attributes and Memo fields. |
| DOC-REQ-002 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §4.2 (lines 84-89) | App-local projections may support compatibility joins, indexing, caching, and degraded reads, but they remain derived read models rather than final lifecycle authority. |
| DOC-REQ-003 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §4.5 (lines 104-108) | Production architecture must forbid ghost rows that claim a running execution without authoritative Temporal backing. |
| DOC-REQ-004 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §5.1, §6 (lines 114-129, 170-174) | Temporal and Temporal Visibility must remain the authoritative runtime layers, while compatibility payloads are adapters over canonical sources rather than independent authorities. |
| DOC-REQ-005 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §5.2-5.3 (lines 131-151) | Migration must move toward projection rows mirroring Temporal-managed executions, and current projection-authoritative behavior must be treated as a staging-only posture. |
| DOC-REQ-006 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §8.1, §9.1 (lines 232-244, 258-266) | The primary execution projection must be keyed by Workflow ID, cache the latest run ID, and update in place across Continue-As-New instead of creating a second primary row. |
| DOC-REQ-007 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §9.2 (lines 268-276) | Primary projection rows may exist for compatibility APIs, task-oriented dashboards, explicit local indexing/counts, repair tooling, and approved degraded-mode reads. |
| DOC-REQ-008 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §9.3 (lines 278-285) | Primary projection rows must not become a second lifecycle engine, workflow-history substitute, per-run audit source, or legacy queue-order model. |
| DOC-REQ-009 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §9.4 (lines 287-297) | Projection rows must track sync metadata including version, last sync timestamp, sync state, sync error, and source mode without implying authority transfer. |
| DOC-REQ-010 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §10.1 (lines 303-317) | Start behavior must authenticate first, start Temporal before treating the execution as real, create or upsert projections from canonical start results, and repair rather than erase accepted workflow starts when projection writes fail. |
| DOC-REQ-011 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §10.2-10.4 (lines 319-353) | Update, signal, and cancel flows must validate policy, send the request to Temporal, and refresh projections from Temporal-visible outcomes instead of accepting projection-only mutations. |
| DOC-REQ-012 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §10.5 (lines 355-363) | New implementation work must reduce dependence on projection-only lifecycle semantics and describe remaining local-authoritative behavior as staging. |
| DOC-REQ-013 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §11.1 (lines 369-375) | Final read posture for Temporal-managed executions must source list/filter/count from Temporal Visibility, detail from Temporal state plus safe enrichment, and compatibility views without losing canonical identifiers. |
| DOC-REQ-014 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §11.2-11.3 (lines 377-394) | Projection-backed reads are allowed only for explicit prototype, compatibility, join-dependent, or degraded-mode reasons, and count/sort semantics must truthfully reflect the active source. |
| DOC-REQ-015 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §8.1, §11.4, §16 (lines 235, 396-406, 567-574) | Temporal-backed compatibility surfaces must preserve source metadata and the fixed identifier bridge `taskId == workflowId`, and they must not invent queue-order semantics. |
| DOC-REQ-016 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §12.1-12.3 (lines 412-441) | Projection repair must run via post-mutation refresh, periodic sweeps, repair-on-read, and startup/backfill, using deterministic repair rules and ordering for missing, stale, and orphaned rows. |
| DOC-REQ-017 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §12.4 (lines 443-453) | Projection consistency for Temporal-backed flows must be eventually consistent with synchronous best-effort refresh, asynchronous repair, and seconds-level operational staleness. |
| DOC-REQ-018 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §13.1-13.4 (lines 457-491) | Degraded-mode behavior must reject writes when Temporal is unavailable, allow stale or partial read fallback only when explicitly permitted and observable, and preserve honest mixed-source outage reporting. |
| DOC-REQ-019 | `docs/Temporal/SourceOfTruthAndProjectionModel.md` §14.1-14.4 (lines 497-525) | Continue-As-New handling must preserve Workflow ID, update the latest run ID and mirrored fields in place, avoid duplicate primary execution rows, and treat `rerun_count` as a convenience field rather than authoritative run history. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Use Temporal as the Execution Authority (Priority: P1)

As a platform operator or API consumer, I can trust Temporal-managed execution lifecycle data because create, mutate, and read operations use Temporal as the authoritative source instead of a local projection state machine.

**Why this priority**: If lifecycle truth is still invented locally, every API and dashboard behavior built on top of it remains unstable and migration cannot complete safely.

**Independent Test**: Start an execution, issue update, signal, and cancel operations, then verify direct execution API responses and mirrored projection state match Temporal-accepted outcomes without any projection-only success path.

**Acceptance Scenarios**:

1. **Given** a valid execution start request, **When** Temporal accepts the workflow start, **Then** the system returns the canonical workflow identity and mirrors it into the projection as downstream state.
2. **Given** a start request that Temporal rejects, **When** the request fails, **Then** no production projection row is left representing an active execution.
3. **Given** an update, signal, or cancel request, **When** Temporal accepts or rejects it, **Then** the API response and resulting projection state reflect the Temporal outcome rather than an independently mutated local state.

---

### User Story 2 - Preserve Compatibility Without Hiding Temporal Backing (Priority: P1)

As a user of task-oriented APIs and dashboards, I can keep using compatibility surfaces during migration while still getting stable Temporal identities, truthful list semantics, and correct source metadata.

**Why this priority**: MoonMind still has compatibility routes, so migration fails if those views sever the connection to the authoritative execution chain.

**Independent Test**: Query execution and task-oriented list/detail routes for Temporal-backed work, then verify identifier mapping, list/count behavior, and source metadata remain truthful for both direct and compatibility views.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed row on a compatibility surface, **When** the row is serialized, **Then** the stable identifier bridge remains `taskId == workflowId`.
2. **Given** a Temporal-backed list route, **When** sort or count metadata is returned, **Then** the route reports the actual source behavior instead of presenting projection-only exactness as canonical Temporal truth.
3. **Given** a route that still depends on local joins or degraded fallback, **When** projection data is used, **Then** the route clearly remains a compatibility or fallback path rather than the authoritative execution layer.

---

### User Story 3 - Repair Drift and Degrade Honestly (Priority: P2)

As an operator, I can recover from projection drift or subsystem outages without producing ghost executions, duplicate primary rows, or misleading dashboard states.

**Why this priority**: Repair and degraded-mode behavior determine whether the migration remains operable under real failure conditions.

**Independent Test**: Simulate stale projections, missing rows, orphaned rows, Continue-As-New run changes, periodic sweep and startup/backfill repair triggers, and dependency outages, then verify repair jobs or read paths restore canonical state, drive the documented sync-state transitions, or surface truthful degradation.

**Acceptance Scenarios**:

1. **Given** a Temporal execution exists without a matching projection row, **When** repair runs, **Then** the projection is created or updated from Temporal truth.
2. **Given** a Workflow ID continues as new, **When** the latest run changes, **Then** the existing primary projection row is updated in place instead of creating a second primary row.
3. **Given** Temporal is unavailable for writes, **When** a mutating request is attempted, **Then** the system rejects the request and does not create a production ghost row.
4. **Given** periodic sweep or startup/backfill repair encounters stale or orphaned projection metadata, **When** reconciliation completes, **Then** the projection transitions through the documented sync states and ends as fresh or quarantined according to Temporal truth.

### Edge Cases

- Temporal accepts workflow start but the projection upsert fails immediately afterward.
- A compatibility list route can still access projection rows while Temporal Visibility is degraded.
- A projection row claims a running execution even though the authoritative Temporal workflow no longer exists.
- Continue-As-New rotates the latest run ID while the user is reading detail or compatibility views.
- App DB outage prevents projection reads while Temporal-backed describe or mutation paths remain available.
- A degraded fallback route would otherwise report an exact count or fresh state that it can no longer support truthfully.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat Temporal execution identity and history, close status/run chain, Visibility state, Search Attributes, and Memo as the canonical runtime truth for Temporal-managed executions. (DOC-REQ-001, DOC-REQ-004)
- **FR-002**: The system MUST treat app-local execution projections as derived read models that mirror Temporal-managed executions rather than define their lifecycle authority. (DOC-REQ-002, DOC-REQ-004, DOC-REQ-005)
- **FR-003**: The system MUST forbid production ghost rows that represent active Temporal-managed executions without authoritative Temporal backing. (DOC-REQ-003, DOC-REQ-018)
- **FR-004**: The system MUST maintain exactly one primary execution projection row per Workflow ID, with `runId` representing the latest known run in the chain. (DOC-REQ-006, DOC-REQ-019)
- **FR-005**: The system MUST update the existing primary projection row in place when Continue-As-New changes the latest run, rather than creating a second primary execution row. (DOC-REQ-006, DOC-REQ-019)
- **FR-006**: The system MUST mirror authoritative execution identity, workflow type, lifecycle status, memo fields, artifact references, and ownership metadata from canonical sources without minting a separate durable identity for Temporal-backed work. (DOC-REQ-004, DOC-REQ-006, DOC-REQ-015)
- **FR-007**: Compatibility surfaces for Temporal-backed work MUST preserve the fixed identifier bridge `taskId == workflowId` and retain source metadata even if task-oriented labels remain user-facing. (DOC-REQ-015)
- **FR-008**: The system MUST restrict primary projection usage to compatibility joins, approved local indexing/counts, repair tooling, observability, and explicitly allowed degraded-mode reads. (DOC-REQ-007, DOC-REQ-014)
- **FR-009**: The system MUST prevent primary execution projections from acting as a second lifecycle engine, workflow-history substitute, or queue-order model for Temporal-backed work. (DOC-REQ-008, DOC-REQ-012)
- **FR-010**: The system MUST track projection freshness and source metadata, including projection version, last successful sync, sync state, sync error, and source mode. (DOC-REQ-009)
- **FR-011**: The system MUST authenticate and validate start requests before starting Temporal workflows, and it MUST only create or activate production projections from canonical start results after Temporal accepts the start. (DOC-REQ-010)
- **FR-012**: If Temporal accepts a workflow start but projection persistence fails, the system MUST preserve the fact that the workflow exists and route the execution into repair instead of pretending the start never happened. (DOC-REQ-010, DOC-REQ-016)
- **FR-013**: The system MUST validate update requests, send them to Temporal, and treat the workflow response as the authoritative accept or reject result; local idempotency helpers may cache responses but may not invent acceptance. (DOC-REQ-011)
- **FR-014**: The system MUST validate signal policy, send signals to Temporal, and refresh projection state from canonical workflow outcomes rather than projection-only transitions. (DOC-REQ-011)
- **FR-015**: The system MUST authorize cancel or terminate requests, let Temporal terminal status become authoritative, and refresh projection state from the resulting close outcome. (DOC-REQ-011)
- **FR-016**: New runtime code paths MUST reduce reliance on projection-only lifecycle semantics, and any remaining local-authoritative behavior MUST be explicitly treated as staging rather than target architecture. (DOC-REQ-005, DOC-REQ-012)
- **FR-017**: For Temporal-managed executions, list, filter, and count behavior MUST come from Temporal Visibility unless the route is explicitly operating in prototype, compatibility, join-dependent, or degraded fallback mode. (DOC-REQ-013, DOC-REQ-014)
- **FR-018**: For Temporal-managed execution detail, the system MUST read from Temporal execution state with only safe MoonMind enrichment layered on top. (DOC-REQ-013)
- **FR-019**: Temporal-managed list APIs MUST use truthful sort and count semantics, reporting exact counts only when the active source can provide them truthfully and using a non-exact mode when that is the honest result. (DOC-REQ-014)
- **FR-020**: Compatibility surfaces MUST not invent queue-order semantics for Temporal task queues or hide that a row is Temporal-backed when projections or joins are used. (DOC-REQ-015)
- **FR-021**: The system MUST implement projection repair through post-mutation refresh, periodic sweeps, repair-on-read, and startup or backfill repair paths. (DOC-REQ-016, DOC-REQ-017)
- **FR-022**: Projection repair MUST create, refresh, overwrite, or quarantine projection rows according to authoritative Temporal and artifact truth, including stale run IDs, stale lifecycle state, stale memo/search data, missing rows, orphaned rows, and artifact-reference drift. (DOC-REQ-016)
- **FR-023**: Projection consistency for Temporal-backed flows MUST target seconds-level eventual consistency using synchronous best-effort refresh plus asynchronous repair. (DOC-REQ-017)
- **FR-024**: When Temporal is unavailable for write operations, the system MUST reject start, update, signal, cancel, and terminate requests in production and MUST NOT fall back to projection-only lifecycle mutation outside explicitly isolated local-development or test modes. (DOC-REQ-003, DOC-REQ-018)
- **FR-025**: When Temporal Visibility or the projection store is degraded, the system MUST allow only explicitly supported stale or partial read fallbacks, surface that degraded condition observably, and backfill missed projection updates after recovery. (DOC-REQ-014, DOC-REQ-018)
- **FR-026**: Mixed-source dashboards and compatibility views MUST surface source outages honestly and MUST NOT synthesize fake success, failure, terminal, or freshness claims to keep rows looking stable. (DOC-REQ-018)
- **FR-027**: The system MUST keep `rerun_count` and similar convenience fields non-authoritative for run history and must rely on the Temporal run chain for authoritative per-run lineage. (DOC-REQ-019)
- **FR-028**: Deliverables for this feature MUST include production runtime code changes implementing the Temporal-authoritative projection model and automated validation tests covering the documented lifecycle, compatibility, repair, and degraded-mode rules. (Runtime intent guard)

### Key Entities *(include if feature involves data)*

- **TemporalExecutionTruth**: Canonical execution record formed from Temporal workflow identity/history, close status, Visibility state, Search Attributes, and Memo.
- **ExecutionProjection**: App-local read model keyed by Workflow ID that mirrors the latest canonical execution state for compatibility, joins, repair, and approved fallback reads.
- **ProjectionSyncState**: Freshness and provenance metadata describing whether a projection is fresh, stale, repair-pending, or orphaned and which source mode currently applies.
- **CompatibilityExecutionView**: Task-oriented or transitional view that can join projection data with other local sources while preserving canonical Temporal identity and source metadata.
- **RepairOperation**: A post-mutation or asynchronous reconciliation pass that compares authoritative Temporal truth with projection state and applies ordered corrections.
- **DegradedReadPolicy**: Route-level policy that determines when stale or partial projection fallback is allowed and how that fallback must be signaled.
- **WorkflowRunChain**: The Temporal Workflow ID plus latest run ID lineage used to model Continue-As-New without duplicating the primary execution projection row.

### Assumptions & Dependencies

- Temporal-managed executions already have or will gain enough canonical metadata to reconstruct list and detail payloads without local lifecycle invention.
- Compatibility surfaces remain necessary during migration and can continue to use projection joins as long as they preserve canonical source identity and truthful semantics.
- A separate per-run projection is deferred for now; authoritative run history remains Temporal-native unless a later feature introduces a dedicated run-history read model.
- Production degraded-mode behavior differs from local development or test behavior, where isolated projection-only fallback can remain available for non-production workflows.
- Artifact storage and ownership policy systems remain the authoritative sources for artifact linkage and authorization concerns outside Temporal execution lifecycle truth.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated start, update, signal, and cancel tests show 100% agreement between exposed API outcomes and Temporal authoritative outcomes for Temporal-managed executions.
- **SC-002**: Automated compatibility tests show 100% of Temporal-backed rows preserve `taskId == workflowId` and never create more than one primary projection row per Workflow ID across Continue-As-New scenarios.
- **SC-003**: Reconciliation tests cover each documented drift class and either restore canonical projection state or quarantine orphaned rows in 100% of exercised cases.
- **SC-004**: Degraded-mode validation shows 100% of production write attempts fail cleanly when Temporal is unavailable and 100% of approved stale or partial fallback paths expose truthful source semantics.
- **SC-005**: Projection freshness tests demonstrate sync metadata transitions across fresh, stale, repair-pending, and orphaned states for all supported repair paths.
- **SC-006**: Release acceptance for this feature includes production runtime code changes plus automated validation tests, with no docs-only completion path accepted.
