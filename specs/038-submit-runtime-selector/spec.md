# Feature Specification: Shared Submit Runtime Selector

**Feature Branch**: `038-submit-runtime-selector`  
**Created**: February 24, 2026  
**Status**: Draft  
**Input**: MOONMIND Task Objective – implement Phase 1 of the shared submit component with a 4-way runtime selector. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prioritized worker task submission (Priority: P1)

An operations engineer lands on either existing submit route, selects a worker runtime (Codex, Gemini, or Claude), fills in queue-task specifics, and submits the task without ever seeing orchestrator-only controls.

**Why this priority**: Worker submissions still represent the dominant flow and must remain frictionless while gaining the selector experience.

**Independent Test**: From `/tasks/queue/new`, select each supported worker runtime, complete the queue fields, submit, and verify the created job detail page receives the worker runtime payload.

**Acceptance Scenarios**:

1. **Given** the new `SubmitWorkForm` loads with config-provided worker runtimes, **When** an operator chooses Codex or Gemini, **Then** queue fields (steps, presets, repo, publish mode, priority/max attempts) remain visible and orchestrator-only inputs stay hidden.
2. **Given** queue-specific values are already entered, **When** the operator switches between worker runtimes, **Then** all previously entered queue values persist and validation continues to enforce the existing rules.
3. **Given** the operator submits a valid worker payload, **When** the API responds with a created job identifier, **Then** the UI navigates to `/tasks/queue/:jobId` using the existing route map.

---

### User Story 2 - Targeted orchestrator run submission (Priority: P2)

A release engineer needs to kick off an orchestrator workflow without the queue-specific clutter, so they switch the runtime selector to the Orchestrator option and submit only the orchestrator fields.

**Why this priority**: Ensures orchestrator work can be initiated from the same surface while preventing invalid payload combinations.

**Independent Test**: From `/tasks/orchestrator/new`, verify the form renders with runtime preselected to Orchestrator, accepts `targetService`, `priority`, and optional `approvalToken`, then submits to `/orchestrator/runs`.

**Acceptance Scenarios**:

1. **Given** the runtime selector is set to Orchestrator, **When** the form renders, **Then** queue-only fields remain hidden and the form shows instruction, `targetService`, `priority (normal/high)`, and optional `approvalToken` inputs.
2. **Given** instruction and `targetService` are required, **When** they are blank, **Then** the form blocks submission and shows inline validation errors specific to orchestrator rules (no queue validation messages fire).
3. **Given** a valid orchestrator payload is submitted, **When** the `/orchestrator/runs` endpoint returns a run identifier, **Then** the UI navigates to `/tasks/orchestrator/:runId` and surfaces any API error responses inline if submission fails.

---

### User Story 3 - Runtime switching without losing work (Priority: P3)

A dashboard user experiments with both worker and orchestrator runtimes before deciding which path fits best, so the form must hold separate in-memory drafts for each runtime shape during the session.

**Why this priority**: Prevents operators from re-entering long instructions or step lists when comparing runtime paths, meeting Phase 1 acceptance.

**Independent Test**: Populate queue fields for a worker runtime, switch to Orchestrator, enter its fields, switch back to the worker runtime, and confirm each set of entries remains intact without merging incompatible values.

**Acceptance Scenarios**:

1. **Given** the user populates worker-specific fields, **When** they switch to Orchestrator, **Then** the worker values are stored in a worker draft structure and hidden but not discarded.
2. **Given** the user switches back to a worker runtime, **When** the form re-renders, **Then** the previous worker draft repopulates the relevant inputs exactly as entered.
3. **Given** both drafts contain unique values, **When** the user submits from either runtime, **Then** only the active runtime’s draft data is serialized into the outgoing payload.

---

### Edge Cases

- Config returns no `supportedTaskRuntimes`: fall back to Codex as the only worker option while still exposing Orchestrator.
- Server rejects submission (validation or network failure): display structured errors without clearing either draft.
- Runtime selector receives an invalid value (e.g., via query param): default to `defaultTaskRuntime` for worker flow until a supported option is chosen.
- User navigates directly to `/tasks/orchestrator/new` with cached worker draft data present: ensure orchestrator shape loads cleanly without leaking worker-only state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The UI MUST render a `SubmitWorkForm` component that is used by both `/tasks/queue/new` and `/tasks/orchestrator/new` without duplicating logic.
- **FR-002**: The runtime selector MUST list every worker runtime provided by `system.supportedTaskRuntimes` in their configured order and append an `orchestrator` option with the label “Orchestrator”.
- **FR-003**: When the active runtime is a worker, the form MUST display queue-task inputs (instructions text area, primary/additional steps editor, template picker, model/effort override, repository, branch, publish mode, queue priority, max attempts) and hide orchestrator-only controls.
- **FR-004**: When the runtime is Orchestrator, the form MUST display only instruction, `targetService`, enum `priority` (`normal` default, `high`), and optional `approvalToken`, while hiding queue-only controls and their error states.
- **FR-005**: The UI MUST keep two independent in-memory drafts—`workerDraft` and `orchestratorDraft`—that store all inputs for their respective shapes and automatically rehydrate the form whenever the runtime selector changes.
- **FR-006**: Validation MUST be runtime-aware: worker submissions enforce existing queue rules (step required, repo format, publish enum, integer numeric fields) and orchestrator submissions enforce `instruction` + `targetService` + `priority in {normal, high}` while ignoring queue constraints.
- **FR-007**: Worker submissions MUST send a `POST` request to the configured queue create endpoint (`sources.queue.create`, currently `/api/queue/jobs`) with the existing `type="task"` payload contract and redirect to `/tasks/queue/:jobId` on success.
- **FR-008**: Orchestrator submissions MUST send a `POST` request to the orchestrator create endpoint (`sources.orchestrator.create`, currently `/orchestrator/runs`) with `{ instruction, targetService, priority, approvalToken? }` and redirect to `/tasks/orchestrator/:runId` on success.
- **FR-009**: Error handling MUST surface API and client-side validation errors inline without clearing drafts, allowing users to correct inputs per runtime context.
- **FR-010**: Both existing routes MUST be able to preselect a runtime (worker default or orchestrator) via props/query so the unified form still honors legacy deep links until Phase 3 route consolidation.
- **FR-011**: Automated tests (unit or component-level) MUST cover runtime switching, draft preservation, payload routing, and validation branching to satisfy the runtime deliverable guard.

### Key Entities *(include if feature involves data)*

- **SubmitTargetOption**: Represents a selectable runtime entry with `id` (`codex`, `gemini`, `claude`, `orchestrator`), human-readable label, and metadata (e.g., whether queue or orchestrator fields apply). Derived from dashboard runtime config plus UI-only Orchestrator entry.
- **SubmitWorkDraft**: Two-variant state container (`workerDraft`, `orchestratorDraft`) that stores all current form inputs for its variant, tracks validation errors, and exposes serialization helpers for the correct endpoint payload.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of worker submissions initiated from either legacy route complete without encountering orchestrator validation messages during Phase 1 rollout.
- **SC-002**: Orchestrator submissions from the shared form achieve the same success rate as the existing dedicated form (baseline) within ±2%, demonstrating no regression.
- **SC-003**: Switching between runtimes during a single session preserves previously entered data with zero field loss across at least five consecutive toggles in automated tests.
- **SC-004**: Automated test coverage includes at least one spec verifying worker payload routing and one verifying orchestrator payload routing, preventing regressions in CI.

## Assumptions

- Phase 1 stops short of adding `/tasks/new`; existing routes simply mount the shared component with preselected runtime props.
- LocalStorage persistence is deferred to Phase 2; Phase 1 only requires in-memory drafts that last while the component is mounted.
- Existing queue/orchestrator API contracts remain unchanged and already surfaced through `sources.queue.create` and `sources.orchestrator.create` config entries.
- Dashboard build tooling already supports conditionally rendering advanced fields (model override, effort override), so reusing those controls is in scope.
