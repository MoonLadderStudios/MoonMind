# Feature Specification: Temporal Task Editing Entry Points

**Feature Branch**: `157-temporal-task-editing`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 0 and Phase 1 from Task Editing Plan #021 for the Temporal-native Task Editing System. Restore task editing from the task details page in the Temporal architecture without reintroducing queue-era assumptions. Scope this specification to contract alignment, rollout scaffolding, and detail-page entry points for supported MoonMind.Run Temporal executions. Establish backend/frontend read and update contract alignment for workflowId, workflow type, current input parameters, input artifact references, template/runtime/repository/model state, actions.canUpdateInputs, actions.canRerun, UpdateInputs, RequestRerun, and explicit outcome states. Add frontend route helpers for /tasks/new, /tasks/new?editExecutionId=<workflowId>, and /tasks/new?rerunExecutionId=<workflowId>. Add a temporalTaskEditing feature flag, placeholder page-mode types, typed API contracts, unsupported-state copy, initial prefill field-set decisions, and fixtures for supported, unsupported, active, and terminal execution states. Restore detail-page Edit and Rerun entry points gated by Temporal capability flags and the feature flag: render Edit only when actions.canUpdateInputs is true, render Rerun only when actions.canRerun is true, navigate to the canonical /tasks/new routes, omit unsupported actions, and ensure terminal executions are not implied editable in place. No silent fallback to editJobId, /tasks/queue/new, queue routes, or queue resubmit semantics is allowed. Initial support is MoonMind.Run only; unsupported executions must fail or hide actions explicitly. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.
**Source Design**: `docs/Tasks/TaskEditingSystem.md`

## Source Document Requirements

| ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Tasks/TaskEditingSystem.md` §3.1, §4.1 | Temporal execution state is the source of truth for task editing; the editable object is a Temporal execution identified by `workflowId`. |
| DOC-REQ-002 | `docs/Tasks/TaskEditingSystem.md` §4.2 | Initial support is limited to `MoonMind.Run`; all other workflow types are unsupported until explicitly added. |
| DOC-REQ-003 | `docs/Tasks/TaskEditingSystem.md` §5.1, §6.1 | Canonical entry routes are `/tasks/new`, `/tasks/new?editExecutionId=<workflowId>`, and `/tasks/new?rerunExecutionId=<workflowId>`. |
| DOC-REQ-004 | `docs/Tasks/TaskEditingSystem.md` §5.2, §9.4 | New Temporal task editing flows must not use `/tasks/queue/new`, `editJobId`, queue update routes, or queue resubmit semantics. |
| DOC-REQ-005 | `docs/Tasks/TaskEditingSystem.md` §6.1 | Task detail must show Edit only when `actions.canUpdateInputs` is true and Rerun only when `actions.canRerun` is true. |
| DOC-REQ-006 | `docs/Tasks/TaskEditingSystem.md` §4.4, §6.1 | Active executions can be edited in place only when capability flags allow it; terminal executions must not be presented as editable in place. |
| DOC-REQ-007 | `docs/Tasks/TaskEditingSystem.md` §11.1 | Execution detail must expose enough read data to reconstruct a submit draft, including workflow identity/type, input parameters, artifact references, capability flags, and task configuration state. |
| DOC-REQ-008 | `docs/Tasks/TaskEditingSystem.md` §9.2, §9.3, §11.2 | Update contract alignment must cover `UpdateInputs` and `RequestRerun` and allow structured parameter patches plus edited input artifact references. |
| DOC-REQ-009 | `docs/Tasks/TaskEditingSystem.md` §8.5, §13 | Unsupported workflow types, missing capabilities, unreadable artifacts, malformed drafts, and stale workflow state must produce explicit operator-readable failure states. |
| DOC-REQ-010 | `docs/Tasks/TaskEditingSystem.md` §10.1 | Historical input artifacts must not be mutated in place during edit or rerun flows. |
| DOC-REQ-011 | Task Editing Plan #021 Phase 0 | The initial rollout must include a `temporalTaskEditing` feature flag, typed contracts, route helpers, unsupported-state copy, prefill field-set decisions, and local/CI fixtures. |
| DOC-REQ-012 | Task Editing Plan #021 Phase 1 | Detail-page Edit and Rerun entry points must be feature-flagged, capability-gated, and covered by visibility and navigation tests. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Align Temporal Editing Contracts (Priority: P1)

As a MoonMind operator, I need the execution detail data and editing contracts to expose the information required for safe task editing so that the UI can make correct decisions before any submit flow exists.

**Why this priority**: The detail page and shared submit page cannot safely expose editing or rerun actions unless both sides agree on identity, lifecycle, capabilities, editable input state, and update outcomes.

**Independent Test**: Inspect supported and unsupported `MoonMind.Run` execution detail responses and confirm they include the agreed contract fields, capability flags, feature flag state, and explicit disabled reasons without queue-era fallback references.

**Acceptance Scenarios**:

1. **Given** a supported active `MoonMind.Run` execution, **When** execution detail is read, **Then** the response includes workflow identity, workflow type, lifecycle state, current input parameters, input artifact references, runtime/model/repository/publish state, and `actions.canUpdateInputs`.
2. **Given** a supported terminal `MoonMind.Run` execution, **When** execution detail is read, **Then** the response includes enough state to route to rerun and exposes `actions.canRerun` when rerun is allowed.
3. **Given** an unsupported workflow type, **When** execution detail is read, **Then** edit and rerun capabilities are absent or false with an explicit unsupported-state reason.
4. **Given** the `temporalTaskEditing` flag is off, **When** execution detail and dashboard configuration are read, **Then** the new edit and rerun entry points cannot be rendered even if the underlying execution would otherwise be eligible.

---

### User Story 2 - Navigate From Detail to Edit or Rerun (Priority: P2)

As a MoonMind operator viewing a Temporal task detail page, I need clear Edit and Rerun entry points only when the current execution supports them so that I can start the correct shared `/tasks/new` flow without accidentally invoking legacy queue behavior.

**Why this priority**: The detail page is the canonical entry point that restores the lost editing behavior while keeping active edit and terminal rerun semantics distinct.

**Independent Test**: Render task detail fixtures for active, terminal, unsupported, and flag-disabled executions; verify button visibility and link targets for each case.

**Acceptance Scenarios**:

1. **Given** an active supported execution with `actions.canUpdateInputs = true` and the feature flag enabled, **When** the operator views task detail, **Then** Edit is visible and navigates to `/tasks/new?editExecutionId=<workflowId>`.
2. **Given** a terminal supported execution with `actions.canRerun = true` and the feature flag enabled, **When** the operator views task detail, **Then** Rerun is visible and navigates to `/tasks/new?rerunExecutionId=<workflowId>`.
3. **Given** an active execution without update capability, **When** the operator views task detail, **Then** Edit is omitted rather than shown as a misleading disabled action.
4. **Given** a terminal execution without rerun capability, **When** the operator views task detail, **Then** Rerun is omitted rather than routed through any queue-era resubmit path.

---

### User Story 3 - Establish Rollout Fixtures and Operator Copy (Priority: P3)

As a MoonMind maintainer, I need local and CI fixtures plus consistent unsupported-state copy so that the feature can be rolled out safely and later phases can build on stable contracts.

**Why this priority**: Rollout guardrails and fixtures prevent future edit/rerun work from silently drifting into queue-era routes or misleading partial states.

**Independent Test**: Use fixture data for supported active, supported terminal, unsupported workflow, and feature-disabled states to verify route helpers, visibility rules, and operator-readable unsupported messages.

**Acceptance Scenarios**:

1. **Given** fixture data for supported and unsupported executions, **When** local tests run, **Then** each capability and route outcome is covered without requiring external credentials.
2. **Given** an unsupported execution state, **When** the UI evaluates available actions, **Then** it presents no invalid action and retains copy that explains unsupported edit/rerun state where an error state is needed.
3. **Given** both edit and rerun route helpers, **When** they are used by detail-page actions, **Then** they produce only canonical `/tasks/new` URLs and never produce queue-era URLs or parameters.

### Edge Cases

- Feature flag disabled while backend capability flags would otherwise allow editing or rerun.
- Active `MoonMind.Run` without `canUpdateInputs`; Edit must be omitted.
- Terminal `MoonMind.Run` without `canRerun`; Rerun must be omitted.
- Unsupported workflow type with lifecycle state that would otherwise look eligible.
- Missing or malformed input parameter metadata in execution detail.
- Missing or unreadable input artifact references in the read contract.
- Both edit and rerun route helpers available in the same page; active edit and terminal rerun language must remain distinct.
- Any attempted fallback to `editJobId`, `/tasks/queue/new`, queue routes, or queue resubmit terminology.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat a Temporal execution identified by `workflowId` as the editable object for this feature. (Maps: DOC-REQ-001)
- **FR-002**: The initial supported workflow type MUST be limited to `MoonMind.Run`. (Maps: DOC-REQ-002)
- **FR-003**: The system MUST expose a feature flag named `temporalTaskEditing` or an equivalent runtime-visible flag that gates the new edit/rerun entry points. (Maps: DOC-REQ-011)
- **FR-004**: Execution detail for the supported workflow type MUST expose workflow identity, workflow type, lifecycle state, current input parameters, input artifact references, template/runtime/repository/model state, and capability flags needed by editing and rerun flows. (Maps: DOC-REQ-007)
- **FR-005**: Execution detail MUST expose `actions.canUpdateInputs` and `actions.canRerun` as the authoritative visibility gates for Edit and Rerun. (Maps: DOC-REQ-005, DOC-REQ-006)
- **FR-006**: The update contract MUST explicitly recognize the logical update names `UpdateInputs` and `RequestRerun` for the supported workflow type. (Maps: DOC-REQ-008)
- **FR-007**: The update contract MUST allow edited input state to be represented through structured parameter changes and new input artifact references. (Maps: DOC-REQ-008, DOC-REQ-010)
- **FR-008**: Frontend route helpers MUST produce canonical route targets for create, edit, and rerun: `/tasks/new`, `/tasks/new?editExecutionId=<workflowId>`, and `/tasks/new?rerunExecutionId=<workflowId>`. (Maps: DOC-REQ-003)
- **FR-009**: The task detail page MUST render Edit only when the feature flag is enabled and `actions.canUpdateInputs` is true. (Maps: DOC-REQ-005, DOC-REQ-012)
- **FR-010**: The task detail page MUST render Rerun only when the feature flag is enabled and `actions.canRerun` is true. (Maps: DOC-REQ-005, DOC-REQ-012)
- **FR-011**: Unsupported edit or rerun actions MUST be omitted from the detail page rather than routed to an invalid or legacy flow. (Maps: DOC-REQ-004, DOC-REQ-009, DOC-REQ-012)
- **FR-012**: The detail page MUST not imply that terminal executions can be edited in place; terminal supported executions may only expose rerun when allowed. (Maps: DOC-REQ-006)
- **FR-013**: The system MUST provide placeholder page-mode types and typed API-facing contracts for create, edit, and rerun modes so later phases can consume the same contract without redefining it. (Maps: DOC-REQ-011)
- **FR-014**: The first-slice prefill field set MUST be explicitly decided and represented in contract or fixture coverage, including runtime, provider profile, model, effort, repository, branches, publish mode, task instructions, primary skill, and template state where available. (Maps: DOC-REQ-007, DOC-REQ-011)
- **FR-015**: The system MUST include fixtures or mocked responses for supported active, supported terminal, unsupported workflow, and feature-disabled states. (Maps: DOC-REQ-011, DOC-REQ-012)
- **FR-016**: Operator-facing unsupported-state copy MUST be explicit enough to distinguish unsupported workflow type, missing capability, malformed draft data, and unavailable artifacts when those states are surfaced. (Maps: DOC-REQ-009, DOC-REQ-011)
- **FR-017**: New primary flows MUST NOT use or generate `editJobId`, `/tasks/queue/new`, queue update routes, or queue resubmit terminology. (Maps: DOC-REQ-004)
- **FR-018**: Required deliverables MUST include production runtime code changes and validation tests; docs-only or spec-only changes do not satisfy this feature. (Maps: DOC-REQ-011, DOC-REQ-012)

### Key Entities

- **Temporal Execution**: The existing execution object identified by `workflowId`, with workflow type, lifecycle state, capability flags, and editable/rerunnable input state.
- **Action Capability Set**: The state-aware action visibility data containing `canUpdateInputs`, `canRerun`, and disabled or unsupported reasons.
- **Task Editing Route Target**: A canonical `/tasks/new` URL representing create, edit, or rerun mode for one execution.
- **Task Editing Read Contract**: The typed data shape shared by backend and frontend for detail-page gating and later draft reconstruction.
- **Task Editing Fixture**: A local or CI-safe mocked execution state used to prove behavior for active, terminal, unsupported, and feature-disabled cases.

### Assumptions & Dependencies

- The initial vertical slice targets `MoonMind.Run` only.
- Capability flags are authoritative for UI visibility; frontend lifecycle guesses are not sufficient.
- Phase 0 and Phase 1 do not require full `/tasks/new` draft reconstruction or submit behavior, but they must prepare stable contracts for those later phases.
- Artifact-backed instructions are read-only historical inputs for this phase; later edit/rerun submit phases must create new artifact references.
- Existing create-task UX remains available at `/tasks/new` in create mode.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For supported active `MoonMind.Run` fixtures with update capability, Edit is visible and resolves to the canonical edit route in 100% of visibility and navigation tests.
- **SC-002**: For supported terminal `MoonMind.Run` fixtures with rerun capability, Rerun is visible and resolves to the canonical rerun route in 100% of visibility and navigation tests.
- **SC-003**: For unsupported workflow type, missing capability, and feature-disabled fixtures, invalid Edit/Rerun actions are omitted in 100% of coverage cases.
- **SC-004**: Execution detail contract tests verify the presence of workflow identity, workflow type, input parameters, artifact references, runtime/model/repository state, and action capability flags for supported fixtures.
- **SC-005**: Route helper tests verify that no new task editing entry point generates `editJobId`, `/tasks/queue/new`, or queue resubmit targets.
- **SC-006**: Required runtime validation tests pass through the project unit-test runner before the feature is considered complete.
