# Feature Specification: Temporal Dashboard Integration

**Feature Branch**: `048-temporal-dashboard-integration`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs/UI/TemporalDashboardIntegration.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/UI/TemporalDashboardIntegration.md` §4.1 "Keep the product surface task-oriented", §13.1 "Vocabulary" | The dashboard MUST remain task-oriented in primary UX even when the underlying execution is Temporal-backed. |
| DOC-REQ-002 | `docs/UI/TemporalDashboardIntegration.md` §4.2 "Treat Temporal as another dashboard source" | Temporal-backed work MUST appear as a first-class dashboard source inside `/tasks*`, and the browser MUST go through MoonMind REST APIs instead of talking directly to Temporal services. |
| DOC-REQ-003 | `docs/UI/TemporalDashboardIntegration.md` §4.3 "Do not model Temporal as a worker runtime", §11.2 "Submit UX rule" | Temporal MUST NOT be exposed as a runtime picker value; engine selection remains hidden or explicitly separate from the runtime control. |
| DOC-REQ-004 | `docs/UI/TemporalDashboardIntegration.md` §6.1 "New `sources.temporal` block" | Runtime config MUST expose Temporal list, create, detail, action, and artifact endpoints for dashboard consumption. |
| DOC-REQ-005 | `docs/UI/TemporalDashboardIntegration.md` §6.2 "Transitional `statusMaps.temporal`" | Temporal-backed list/detail models MUST preserve normalized dashboard status plus raw lifecycle metadata such as `rawState`, `temporalStatus`, `closeStatus`, `waitingReason`, and `attentionRequired`. |
| DOC-REQ-006 | `docs/UI/TemporalDashboardIntegration.md` §6.3 "Feature flags" | Temporal dashboard rollout MUST be feature-flagged with phased enablement for list/detail, actions, submit, and optional debug fields. |
| DOC-REQ-007 | `docs/UI/TemporalDashboardIntegration.md` §7.1 "Keep existing canonical routes", §7.2 "Temporal detail routing requirement" | Canonical dashboard routes MUST remain `/tasks/list`, `/tasks/new`, and `/tasks/:taskId`, and Temporal-safe task identifiers MUST resolve through canonical server-side source resolution. |
| DOC-REQ-008 | `docs/UI/TemporalDashboardIntegration.md` §7.3 "Query parameters" | Temporal dashboard filters MUST support the documented Temporal query parameters, including `workflowType`, `state`, `entry`, `repo`, `integration`, and policy-controlled owner filters, while keeping owner filters restricted to operator/admin-only use. |
| DOC-REQ-009 | `docs/UI/TemporalDashboardIntegration.md` §8.1 "Mixed-source list behavior" | Mixed-source task lists MUST remain a convenience view with bounded merged slices and informational totals rather than a globally paginated source of truth. |
| DOC-REQ-010 | `docs/UI/TemporalDashboardIntegration.md` §8.2 "Temporal-only list behavior" | When the user pins `source=temporal`, the dashboard MUST treat Temporal as the authoritative list source and preserve API `nextPageToken`, `count`, and `countMode` semantics exactly as returned. |
| DOC-REQ-011 | `docs/UI/TemporalDashboardIntegration.md` §8.3 "Row model for Temporal-backed items", §8.4 "Sorting" | Temporal-backed rows MUST normalize the required task, ownership, status, timestamp, and run metadata fields and apply deterministic Temporal-aware sort behavior. |
| DOC-REQ-012 | `docs/UI/TemporalDashboardIntegration.md` §9.2 "Temporal detail fetch sequence", §12.4 "Run scoping" | Temporal detail MUST fetch execution detail first, then artifact lists scoped to the latest run, while keeping the canonical route anchored to `taskId == workflowId`. |
| DOC-REQ-013 | `docs/UI/TemporalDashboardIntegration.md` §9.3 "Detail header model", §9.4 "Timeline / event model" | Temporal detail MUST present normalized header metadata, blocked-state cues, artifacts, and a synthesized timeline without requiring raw Temporal event history in v1. |
| DOC-REQ-014 | `docs/UI/TemporalDashboardIntegration.md` §10.1 "Supported Temporal actions", §10.2 "Initial UI action matrix", §10.3 "Copy guidance" | Dashboard actions MUST map onto the documented Temporal create/update/signal/cancel behaviors and use task-oriented operator copy. |
| DOC-REQ-015 | `docs/UI/TemporalDashboardIntegration.md` §11.1-§11.4 "Submit Integration" | Submit integration MUST be phased, remain task-shaped in UX, allow backend-routed Temporal starts, and redirect Temporal-backed creates to the canonical task detail route. |
| DOC-REQ-016 | `docs/UI/TemporalDashboardIntegration.md` §12.1-§12.4 "Artifact Integration" | Temporal-managed dashboard flows MUST remain artifact-first, cover artifact create/upload/complete plus authorized metadata/download flows, respect preview and raw-access rules, and default detail to latest-run artifacts. |
| DOC-REQ-017 | `docs/UI/TemporalDashboardIntegration.md` §13.2 "Identifier policy", §13.3 "Mixed-source list caveat" | Temporal-backed records MUST keep `taskId`, `workflowId`, latest `temporalRunId`, and reserved legacy `runId` semantics distinct while preserving source-specific truth in mixed-source views. |
| DOC-REQ-018 | `docs/UI/TemporalDashboardIntegration.md` §14 "Rollout Plan", §15 "Acceptance Criteria", §16 "Implementation Checklist" | Production delivery MUST cover the rollout scope for runtime config, list/detail, actions, submit behavior, and artifact handling with validation evidence. |
| DOC-REQ-019 | `docs/UI/TemporalDashboardIntegration.md` §5.2 "Out of scope", §9.4 "Non-goal for v1" | The v1 delivery MUST exclude direct Temporal Web UI embedding, direct browser access to Temporal APIs, and raw Temporal event-history browsing. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operators Can See Temporal Tasks in the Existing Dashboard (Priority: P1)

As an operator, I can find Temporal-backed work in the same `/tasks` list and detail routes I already use so migration to Temporal does not require a second dashboard or a new identifier model.

**Why this priority**: Read visibility is the minimum viable behavior for a safe migration because operators need one place to discover and inspect in-flight work before any action or submit flows expand.

**Independent Test**: Enable Temporal list/detail flags, load mixed-source and `source=temporal` task views with `workflowType`, `state`, `entry`, `repo`, and `integration` filters, open a Temporal-backed task through `/tasks/:taskId`, and confirm the page resolves and renders normalized status, metadata, artifacts, and authoritative `countMode` semantics without direct Temporal browser access.

**Acceptance Scenarios**:

1. **Given** Temporal dashboard read integration is enabled, **When** an operator opens `/tasks/list` without pinning a source, **Then** Temporal-backed rows appear alongside queue and orchestrator rows as an informational mixed-source convenience view.
2. **Given** an operator filters to `source=temporal`, **When** the list page loads additional results, **Then** the dashboard preserves Temporal pagination tokens and counts exactly as returned by the authoritative Temporal-backed API.
3. **Given** a Temporal-backed task whose `taskId` contains a Temporal-safe opaque identifier, **When** the operator opens `/tasks/:taskId`, **Then** the detail page resolves through server-side source mapping and loads the correct Temporal execution.

---

### User Story 2 - Operators Can Understand State, Artifacts, and Allowed Actions (Priority: P2)

As an operator, I can inspect the current state of a Temporal-backed task, understand whether attention is required, review artifacts for the latest run, and use only the actions that are valid for that task's current state.

**Why this priority**: Once Temporal-backed work is visible, operators need enough context and safe controls to keep work moving without exposing raw Temporal internals or confusing action choices.

**Independent Test**: Open Temporal-backed tasks in multiple lifecycle states, verify normalized badges plus raw/debug metadata when enabled, confirm latest-run artifact lists load correctly, and exercise the allowed action set for each state.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed task in a blocked state, **When** the detail page renders, **Then** the dashboard shows the compatibility status plus `waitingReason` and whether attention is actually required.
2. **Given** a Temporal-backed task with a newer run instance than the list row originally showed, **When** the operator opens detail, **Then** artifact listing uses the latest run from execution detail rather than stale row data.
3. **Given** a Temporal-backed task in a terminal state, **When** the operator views detail actions, **Then** rerun and artifact access remain available while unsupported in-flight controls stay hidden or disabled.

---

### User Story 3 - Users Can Submit Task-Shaped Requests Without a Temporal Runtime Picker (Priority: P3)

As a user, I can continue using the existing task submission experience while the backend decides whether a request starts a Temporal-backed execution, so the migration does not force me to understand the orchestration engine.

**Why this priority**: Submit behavior is lower priority than read visibility, but it is necessary to complete the end-to-end runtime path and preserve product continuity as more work moves to Temporal.

**Independent Test**: Submit supported run-shaped and manifest-oriented task flows from the existing task UI, confirm Temporal remains absent from the runtime picker, and verify successful creates redirect to the canonical task detail route with source-aware context.

**Acceptance Scenarios**:

1. **Given** submit support for Temporal-backed work is enabled, **When** a user opens the standard task submit page, **Then** the runtime picker does not expose `temporal` as a selectable runtime value.
2. **Given** a supported task-shaped submit is routed to Temporal by backend policy, **When** create succeeds, **Then** the user is redirected to the canonical task detail route for that new task with Temporal source context preserved.
3. **Given** a task input requires large content, **When** the user submits or updates the task, **Then** the dashboard uses artifact-first handling rather than assuming large bytes are edited inline on the execution record.

### Edge Cases

- What happens when a Temporal-backed task appears in mixed-source results before canonical source mapping has been warmed for that `taskId`?
- How does the dashboard behave when the latest Temporal run changes between list render and detail load?
- What happens when `rawState=awaiting_external` but `attentionRequired` is false for the current operator?
- How does the list behave when Temporal-only pagination tokens are present but the user switches back to mixed-source mode?
- How does the list communicate authoritative `countMode=estimated_or_unknown` without implying exact mixed-source totals?
- What happens when artifact preview is allowed but raw artifact download is restricted?
- How are operator-only owner filters handled when a non-admin user attempts to deep-link them?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The delivery MUST include production runtime code changes that implement Temporal dashboard integration behavior in the live product surface, not docs-only updates. (Maps: DOC-REQ-018)
- **FR-002**: The delivery MUST include automated validation tests that cover Temporal dashboard list, detail, route resolution, action gating, submit routing, and artifact presentation behavior. (Maps: DOC-REQ-018)
- **FR-003**: The dashboard MUST keep task-oriented terminology and primary UX flows for Temporal-backed work while reserving workflow-execution terminology for advanced or debug metadata only. (Maps: DOC-REQ-001)
- **FR-004**: Temporal-backed work MUST appear as a first-class dashboard source within the existing `/tasks` product surface, and all browser interactions for that source MUST go through MoonMind-owned REST APIs rather than direct Temporal browser calls. (Maps: DOC-REQ-002, DOC-REQ-019)
- **FR-005**: The dashboard MUST NOT expose Temporal as a worker runtime picker option, and submit behavior MUST preserve task-shaped product inputs while backend policy decides whether the execution engine is Temporal-backed. (Maps: DOC-REQ-003, DOC-REQ-015)
- **FR-006**: Runtime configuration for the dashboard MUST expose Temporal source endpoints and phased feature flags required for list/detail, actions, submit, and optional debug behavior. (Maps: DOC-REQ-004, DOC-REQ-006)
- **FR-007**: Temporal-backed list and detail data MUST preserve a normalized dashboard status alongside raw lifecycle metadata, including raw state, Temporal status, close status, wait reason, and whether operator attention is required. (Maps: DOC-REQ-005)
- **FR-008**: Canonical dashboard routes MUST remain `/tasks/list`, `/tasks/new`, and `/tasks/:taskId`, and Temporal-safe task identifiers MUST resolve through canonical server-side source mapping rather than ID-shape probing as the documented contract. (Maps: DOC-REQ-007, DOC-REQ-017)
- **FR-009**: Temporal-specific filters MUST support the documented query capabilities for workflow type, state, entry, `repo`, `integration`, pagination size/token, and policy-approved ownership scoping while preventing general end-user exposure of operator-only owner controls. (Maps: DOC-REQ-008)
- **FR-010**: Mixed-source task lists MUST remain bounded convenience views with informational totals only, while `source=temporal` views MUST preserve authoritative Temporal `nextPageToken`, `count`, and `countMode` semantics exactly as returned by the API. (Maps: DOC-REQ-009, DOC-REQ-010)
- **FR-011**: Temporal-backed rows in the dashboard MUST normalize the documented identity, ownership, status, repository, integration, wait-state, timestamp, workflow, and latest-run fields and apply deterministic Temporal-aware sorting that prefers `mm_updated_at`, then `updatedAt`, then `workflowId DESC`, and finally `startedAt`. (Maps: DOC-REQ-011)
- **FR-012**: Temporal-backed detail MUST fetch execution detail first, derive the latest run from that response, and then fetch execution-scoped artifacts for that latest run while keeping the stable route anchored to `taskId == workflowId`. (Maps: DOC-REQ-012, DOC-REQ-017)
- **FR-013**: Temporal-backed detail MUST render normalized header fields, wait metadata, latest-run metadata, artifacts, and a synthesized task-oriented timeline summary without exposing raw Temporal event history in the v1 dashboard. (Maps: DOC-REQ-013, DOC-REQ-019)
- **FR-014**: Dashboard actions for Temporal-backed tasks MUST map onto the documented create, update, signal, rerun, and cancel behaviors, use task-oriented operator copy, and only appear when enabled and valid for the current task state. (Maps: DOC-REQ-014, DOC-REQ-006)
- **FR-015**: Submit flows for supported Temporal-backed work MUST roll out after read visibility, remain task-shaped in UX, allow backend-routed execution starts, and redirect successful creates to the canonical task detail route with Temporal source context. (Maps: DOC-REQ-015)
- **FR-016**: Temporal-backed dashboard flows MUST remain artifact-first for large inputs and outputs, support artifact create/upload/complete helpers plus execution-scoped artifact presentation and authorized download behavior, respect preview versus raw-access rules, and treat input changes as new artifact references rather than mutable bytes. (Maps: DOC-REQ-016)
- **FR-017**: The dashboard MUST preserve distinct semantics for `taskId`, `workflowId`, latest `temporalRunId`, and reserved legacy `runId`, and mixed-source views MUST continue to reflect queue, orchestrator, and Temporal records from their own authoritative backends without promising a single shared source of truth. (Maps: DOC-REQ-017)
- **FR-018**: The production rollout MUST deliver runtime config, list/detail integration, action integration, submit integration, and artifact handling in phased scope with validation evidence for each mapped source requirement before the feature is considered complete. (Maps: DOC-REQ-018)
- **FR-019**: The v1 delivery MUST exclude direct Temporal Web UI embedding, direct browser access to Temporal Server APIs, and raw event-history browsing, even when advanced/debug metadata is enabled. (Maps: DOC-REQ-019)

### Key Entities *(include if feature involves data)*

- **Temporal Dashboard Source Config**: Runtime dashboard configuration that describes Temporal list, detail, action, artifact, and feature-flag capabilities.
- **Temporal Dashboard Row**: Normalized task-list record representing a Temporal-backed execution with compatibility status, ownership, timestamps, and latest-run metadata.
- **Temporal Task Detail View**: Source-aware detail model that combines execution summary, normalized status, wait metadata, actions, and latest-run artifact data for one task route.
- **Source Resolution Record**: Canonical mapping that resolves a dashboard `taskId` to its authoritative backing source without relying on identifier shape heuristics.
- **Artifact Presentation Record**: Execution-linked artifact metadata used to render previews, downloads, and latest-run output evidence safely in task detail.
- **Action Capability Set**: State-aware description of which task actions are currently valid and exposed for a Temporal-backed execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation coverage demonstrates that 100% of source requirements (`DOC-REQ-001` through `DOC-REQ-019`) map to one or more runtime functional requirements plus at least one implementation task and one validation task in automated validation scope.
- **SC-002**: In validation runs, operators can open Temporal-backed list and detail experiences through canonical `/tasks` routes with no unresolved route failures for documented Temporal-safe task identifiers.
- **SC-003**: In validation runs covering supported lifecycle states, the dashboard exposes only allowed Temporal actions for the current state and completes supported action flows successfully in at least 95% of scripted cases.
- **SC-004**: In validation runs that include rerun or run-advancement behavior, latest-run artifact presentation resolves correctly for at least 95% of Temporal-backed detail loads, and detail never falls back to stale list-row run identifiers.
- **SC-005**: In validation runs of supported Temporal-backed submit flows, 100% of successful creates keep Temporal absent from the runtime picker, preserve artifact-first handling for large inputs, and redirect users to the canonical task detail route with correct source-aware context.

## Assumptions

- Existing Temporal execution and artifact APIs remain the authoritative backend contracts consumed by the task dashboard for this integration.
- Queue-backed and orchestrator-backed dashboard behaviors remain active during rollout, so mixed-source compatibility must be preserved rather than replaced in one release.
- Canonical server-side source resolution or an equivalent persisted task index can be introduced without requiring users to learn a new route family.

## Dependencies

- Temporal execution list, detail, action, and artifact API surfaces remain available and policy-compatible with dashboard needs.
- Existing task dashboard shell, runtime-config builder, and source-aware routing surfaces are available for extension.
- Validation infrastructure is able to exercise dashboard route handling, API-backed state transitions, and source-aware artifact behavior for Temporal-backed tasks.
