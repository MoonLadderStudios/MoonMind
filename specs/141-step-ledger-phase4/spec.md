# Feature Specification: Step Ledger Phase 4

**Feature Branch**: `141-step-ledger-phase4`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 using test-driven development of the step-ledger rollout plan. Make Steps the primary Mission Control task-detail surface, reusing step-scoped observability and artifact refs instead of treating task-run observability as the whole-page model."

## Source Document Requirements

Source: `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/UI/MissionControlArchitecture.md`, `docs/tmp/remaining-work/UI-MissionControlArchitecture.md`, `docs/Tasks/TaskRunsApi.md`, `docs/UI/MissionControlStyleGuide.md`

| ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `StepLedgerAndProgressModel` §4, §6, §7 | Task detail must treat the latest/current run step ledger as the primary step surface, keyed by `workflowId`, with stable row fields and latest-run semantics. |
| DOC-REQ-002 | `MissionControlArchitecture` §9.2 | Task detail pages are the place for step ledger state, checks, managed-run observability, and artifact evidence. |
| DOC-REQ-003 | `MissionControlArchitecture` implementation tracking | The Steps section must appear above Timeline and generic Artifacts on task detail. |
| DOC-REQ-004 | `MissionControlArchitecture` implementation tracking | The browser must fetch execution detail first, then `/api/executions/{workflowId}/steps`, before falling back to execution-wide artifact browsing. |
| DOC-REQ-005 | `MissionControlArchitecture` implementation tracking | Expanded step rows must group Summary, Checks, Logs & Diagnostics, Artifacts, and Metadata. |
| DOC-REQ-006 | `TaskRunsApi` §2-§4 | Step-scoped `taskRunId` is the canonical handle for `/api/task-runs/*` observability drilldown and should only be used when a row exposes it. |
| DOC-REQ-007 | `StepLedgerAndProgressModel` §3.3, §9 | Checks, logs, diagnostics, and large evidence remain external surfaces; the UI consumes bounded summaries, checks, refs, and artifact slots without parsing workflow history. |
| DOC-REQ-008 | `MissionControlStyleGuide` §4-§5 | Mission Control must render dense, readable step status chips and compact review/check badges that work in light and dark themes. |
| DOC-REQ-009 | `MissionControlArchitecture` implementation tracking | Browser tests must cover latest-run-only steps, delayed `taskRunId` arrival, and step-scoped observability attachment. |

## User Scenarios & Testing

### User Story 1 - Steps become the primary task-detail surface (Priority: P1)

An operator needs the task detail page to show the latest/current run step ledger as the main execution surface instead of forcing them to interpret execution-wide timeline and task-run observability first.

**Why this priority**: This is the Phase 4 product outcome. Without it, the backend contract added in Phase 3 remains underused and the page still treats managed-run observability as the primary model.

**Independent Test**: Render the task-detail page for a Temporal run with step-ledger data and assert the Steps section appears above Timeline and Artifacts, showing latest-run rows and progress without requiring observability fetches to render the section.

**Acceptance Scenarios**:

1. **Given** execution detail includes `progress` and `stepsHref`, **When** the task-detail page loads, **Then** it fetches execution detail first and then loads the latest-run step ledger into a primary Steps section above Timeline and Artifacts.
2. **Given** a latest-run step ledger with multiple statuses, **When** the page renders, **Then** each row shows the canonical title, status, attempt, summary, and compact progress-oriented metadata without flattening the whole ledger into generic timeline rows.
3. **Given** the page is viewing a rerun or Continue-As-New execution, **When** the step ledger loads, **Then** it shows only the latest/current run rows and labels them with the current `runId`.

---

### User Story 2 - Expanded steps attach observability and evidence lazily (Priority: P1)

An operator needs step drilldown panels that attach observability and evidence only when a row is expanded, so the page remains cheap while still exposing detailed logs, diagnostics, checks, and artifacts when needed.

**Why this priority**: The current page eagerly centers task-run observability. Phase 4 must demote that to row-level drilldown and keep the default page bounded.

**Independent Test**: Expand step rows in browser tests and assert observability summary/log/diagnostic fetches only occur for rows with `taskRunId`, while rows without a binding show the delayed-launch messaging and still render other metadata/artifacts.

**Acceptance Scenarios**:

1. **Given** a step row has a `taskRunId`, **When** the operator expands it, **Then** the page fetches observability summary and logs/diagnostics for that step only.
2. **Given** a running step row has no `taskRunId` yet, **When** the operator expands it, **Then** the row shows launch/binding status copy and later attaches observability once a refreshed latest-run ledger exposes the binding.
3. **Given** a step row exposes canonical artifact refs but no task-run observability, **When** the row is expanded, **Then** Summary, Artifacts, and Metadata still render without requesting `/api/task-runs/*`.

---

### User Story 3 - Steps remain dense and readable across desktop/mobile themes (Priority: P2)

An operator needs compact step chips, check badges, and readable expanded evidence sections that fit existing Mission Control density on desktop and mobile in both light and dark themes.

**Why this priority**: The page already has a rich layout system, so the new steps surface must fit the existing visual system instead of looking bolted on.

**Independent Test**: Browser tests can assert the semantic groups and chips/badges render, while CSS verification and build checks prove the UI compiles cleanly.

**Acceptance Scenarios**:

1. **Given** a step row has status and checks, **When** it renders, **Then** the row shows compact status chips and check badges with readable labels.
2. **Given** the page is narrow or wide, **When** the step row expands, **Then** Summary, Checks, Logs & Diagnostics, Artifacts, and Metadata remain readable without hiding critical step context.
3. **Given** the generic execution-wide artifact table still exists, **When** the page renders, **Then** it appears below Steps and reads clearly as secondary evidence rather than the primary execution model.

### Edge Cases

- What happens when `/api/executions/{workflowId}/steps` is unavailable while execution detail still loads? The page should show an error state for Steps without breaking the rest of the detail view.
- What happens when a step row has no checks, refs, or artifact slots yet? The expanded groups should stay stable and render explicit empty-state copy.
- What happens when a row has `taskRunId` but observability summary returns 403 or 404? The row should show scoped error or unavailable messaging without collapsing the rest of the step panel.
- What happens when the execution detail poll updates but the latest step ledger is unchanged? The page should preserve expanded-row state and avoid re-centering the UI on execution-wide panels.
- What happens when the latest-run ledger rotates `runId` after Continue-As-New? The Steps section should replace the old run rows with the latest run only and continue using per-row observability handles from the new ledger.

## Requirements

### Functional Requirements

- **FR-001**: System MUST render a primary Steps section on task detail above Timeline and generic Artifacts, using `GET /api/executions/{workflowId}/steps` as the authoritative row source. Mappings: DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004.
- **FR-002**: System MUST fetch execution detail before the step ledger, keep execution detail polling bounded, and keep step-ledger reads separate from generic artifact reads. Mappings: DOC-REQ-003, DOC-REQ-004.
- **FR-003**: System MUST render step rows with latest-run identity, canonical step status, attempt, summary, checks, refs, and grouped artifact slots from the frozen step-ledger contract. Mappings: DOC-REQ-001, DOC-REQ-005, DOC-REQ-007.
- **FR-004**: System MUST lazily attach step-scoped observability using `taskRunId` only for expanded rows that expose a binding, reusing `/api/task-runs/*` endpoints as row-level drilldown rather than whole-page state. Mappings: DOC-REQ-005, DOC-REQ-006.
- **FR-005**: System MUST show stable empty-state or delayed-binding copy for expanded rows without current `taskRunId`, and MUST attach observability automatically when a later latest-run ledger refresh exposes the binding. Mappings: DOC-REQ-006, DOC-REQ-009.
- **FR-006**: System MUST render the expanded step panel using explicit groups for Summary, Checks, Logs & Diagnostics, Artifacts, and Metadata. Mappings: DOC-REQ-005, DOC-REQ-007.
- **FR-007**: System MUST style status chips, check badges, and step rows using existing Mission Control design tokens and readable density patterns for light and dark themes. Mappings: DOC-REQ-008.
- **FR-008**: System MUST add browser-facing tests covering latest-run-only steps, delayed `taskRunId` arrival, step-scoped observability attachment, and the Steps-first information hierarchy. Mappings: DOC-REQ-009.
- **FR-009**: System MUST implement production frontend/runtime code changes plus validation tests in this phase; documentation-only work is insufficient. Mappings: DOC-REQ-003, DOC-REQ-004, DOC-REQ-009.

### Key Entities

- **TaskDetailStepLedger**: Latest/current-run step snapshot loaded from `/api/executions/{workflowId}/steps`.
- **TaskDetailStepRowView**: UI view model for one step row, combining the frozen API row with local expansion/loading state.
- **StepObservabilityAttachment**: Lazy row-level fetch state for observability summary, live logs, static logs, and diagnostics keyed by a row `taskRunId`.
- **StepEvidenceGroups**: Expanded row groups for Summary, Checks, Logs & Diagnostics, Artifacts, and Metadata.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Browser tests prove the Steps section renders above Timeline and Artifacts using latest-run step-ledger data.
- **SC-002**: Browser tests prove observability requests are row-scoped and are only issued when an expanded row exposes `taskRunId`.
- **SC-003**: Browser tests prove delayed `taskRunId` attachment updates the expanded row from waiting copy to observability-backed content without requiring whole-page remounts.
- **SC-004**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `npm run ui:typecheck`, and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` pass.

## Assumptions

- Phase 4 stops at the Mission Control task-detail pivot; folding review verdict production deeper into `checks[]` remains the later review-gate phase.
- The API and latest-run step-ledger contract from Phase 3 remain authoritative and unchanged in this phase.
- Generic execution-wide Artifacts and Timeline sections still remain on the page, but they become secondary to the Steps surface.
