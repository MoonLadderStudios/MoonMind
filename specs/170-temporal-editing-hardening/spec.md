# Feature Specification: Temporal Editing Hardening

**Feature Branch**: `170-temporal-editing-hardening`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 5 using test-driven development from the Task Editing Plan #021 for the Temporal-native Task Editing System. Make task editing production-ready with hardening, observability, regression coverage, queue-era cleanup, and rollout readiness. Add client and server telemetry for detail-page edit click, detail-page rerun click, draft reconstruction success and failure, UpdateInputs submit attempt and result, and RequestRerun submit attempt and result. Capture failure reasons for missing capabilities, stale execution state, malformed or missing artifacts, validation errors, and artifact preparation failures. Add end-to-end or equivalent runtime regression tests for supported active execution edit, supported terminal execution rerun, unsupported workflow type, missing artifact during reconstruction, execution state changing between page load and submit, artifact externalization failure, and both query params present where rerun wins. Add unit coverage for route parsing, mode resolution, and payload building. Remove or deprecate primary UI flows and operator-facing language that still point at /tasks/queue/new, editJobId, or queue resubmit semantics for Temporal rerun. Ensure success paths return to Temporal detail views rather than queue/list pages. Update runtime-visible docs or internal references only where needed to reflect the Temporal-native model. Enable the temporalTaskEditing flag in local development and staging readiness paths, and define rollout readiness for dogfood, small production cohort, and all-operator expansion after error rates and support feedback are acceptable. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Observe Editing and Rerun Outcomes (Priority: P1)

An operator or maintainer can determine whether Temporal task editing and rerun flows are being used successfully, where they fail, and why they fail without inspecting raw browser or workflow payloads.

**Why this priority**: Phase 5 is about production readiness. Without observable attempts, outcomes, and failure reasons, the team cannot safely enable the feature beyond local dogfood.

**Independent Test**: Exercise detail-page edit and rerun entry points, draft reconstruction outcomes, and edit/rerun submits; verify client-visible and server-visible telemetry records bounded event names, mode, update name, outcome, and failure reason where applicable.

**Acceptance Scenarios**:

1. **Given** a supported execution detail page with edit capability, **When** the operator selects **Edit**, **Then** the system records an edit-click event tied to the Temporal execution context.
2. **Given** a supported terminal execution detail page with rerun capability, **When** the operator selects **Rerun**, **Then** the system records a rerun-click event tied to the Temporal execution context.
3. **Given** a Temporal execution draft is reconstructed for edit or rerun, **When** reconstruction succeeds or fails, **Then** the system records the mode and success or failure reason without recording unbounded task instructions or artifact content.
4. **Given** an operator submits edit or rerun changes, **When** the backend accepts, defers, continues, rejects, or fails the request, **Then** the system records the update name, result, backend application outcome, and bounded failure reason where applicable.

---

### User Story 2 - Prove Regression Safety for Key Flows (Priority: P2)

Maintainers can run automated regression coverage that proves the supported edit and rerun flows still work and that known failure modes are explicit and non-misleading.

**Why this priority**: Task editing touches routing, draft reconstruction, artifacts, lifecycle state, and submit semantics; production rollout needs coverage across those boundaries.

**Independent Test**: Run the task editing regression suite and verify it covers active edit, terminal rerun, unsupported workflow type, missing artifact, stale state, artifact externalization failure, query precedence, route parsing, mode resolution, and payload building.

**Acceptance Scenarios**:

1. **Given** an active supported `MoonMind.Run` execution with update capability, **When** edit mode is opened and submitted, **Then** the form is prefilled, `UpdateInputs` is requested, and the operator returns to the Temporal detail context.
2. **Given** a terminal supported `MoonMind.Run` execution with rerun capability, **When** rerun mode is opened and submitted, **Then** the form is prefilled, `RequestRerun` is requested, and the operator returns to a Temporal detail context.
3. **Given** both edit and rerun query parameters are present, **When** the shared task form resolves its mode, **Then** rerun mode wins.
4. **Given** unsupported workflow type, missing capability, missing or malformed artifact, stale execution state, validation error, or artifact preparation failure, **When** the operator attempts edit or rerun, **Then** submission is blocked or rejected with explicit operator-readable feedback.
5. **Given** route parsing, mode resolution, or payload building changes, **When** unit coverage runs, **Then** canonical route and payload semantics remain protected.

---

### User Story 3 - Remove Queue-Era Primary Flow Leakage (Priority: P3)

Operators using Temporal task editing no longer encounter primary routes or wording that describe the old queue edit or resubmit model.

**Why this priority**: Queue-era language and fallback routes undermine confidence in the Temporal-native model and can route operators into unsupported behavior.

**Independent Test**: Inspect primary task editing UI surfaces, route helpers, submit paths, and operator-facing copy; verify they use Temporal-native edit/rerun language and never route through queue-era edit or resubmit paths.

**Acceptance Scenarios**:

1. **Given** an operator starts edit from a supported Temporal detail page, **When** navigation occurs, **Then** the destination is the canonical shared task form edit route and not a queue route.
2. **Given** an operator starts rerun from a supported Temporal detail page, **When** navigation occurs, **Then** the destination is the canonical shared task form rerun route and not a queue resubmit route.
3. **Given** an edit or rerun succeeds, **When** the operator is redirected, **Then** the destination is a Temporal detail context rather than a queue page or generic task list.
4. **Given** primary user-facing copy is displayed for Temporal task editing, **When** the operator reads labels, help text, success messages, and errors, **Then** the copy distinguishes active edit from terminal rerun without queue-era terminology.

---

### User Story 4 - Support Controlled Rollout (Priority: P4)

MoonMind maintainers can enable Temporal task editing in local development and staging readiness paths, dogfood it internally, and expand exposure only after production health is acceptable.

**Why this priority**: Rollout controls protect operators while the feature is validated with real usage and support feedback.

**Independent Test**: Verify runtime-visible feature flag behavior, rollout state expectations, and acceptance criteria for moving from local/staging to dogfood, small production cohort, and all-operator exposure.

**Acceptance Scenarios**:

1. **Given** local development or staging readiness configuration, **When** the runtime feature flags are read, **Then** Temporal task editing can be enabled for validation without changing code.
2. **Given** the feature is in dogfood or limited production rollout, **When** telemetry indicates elevated failure rates or support issues, **Then** maintainers can keep or reduce exposure without falling back to queue-era flows.
3. **Given** error rates and support feedback are acceptable, **When** maintainers expand the rollout, **Then** all eligible operators can use Temporal-native task editing and rerun entry points.

### Edge Cases

- Both `rerunExecutionId` and `editExecutionId` are present; rerun mode wins and telemetry records rerun mode.
- The execution is supported at page load but becomes terminal or otherwise ineligible before submit; the backend rejection is surfaced and counted as a stale-state failure.
- The input artifact required for reconstruction is missing, unreadable, or malformed; the page blocks submit and records a reconstruction failure reason.
- Artifact creation or upload fails while preparing edited or rerun input; no update request is sent and the failure is visible to the operator.
- Backend validation rejects a submit after artifact preparation; the operator remains on the form and receives explicit feedback.
- The backend accepts an update but schedules it for a safe point or continues as new; success copy and telemetry distinguish the outcome.
- Telemetry delivery fails or is unavailable; task editing behavior continues unaffected.
- Searches for queue-era primary flow references may find archived or historical specs; those references are acceptable only outside current primary runtime flow surfaces.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST record client-side telemetry for detail-page edit clicks and detail-page rerun clicks, including event type, mode, and Temporal execution identity.
- **FR-002**: The system MUST record draft reconstruction success and failure for edit and rerun modes, including mode and bounded failure reason when reconstruction fails.
- **FR-003**: The system MUST record submit attempts and submit results for `UpdateInputs`, including update name, outcome, backend application state, and bounded failure reason when available.
- **FR-004**: The system MUST record submit attempts and submit results for `RequestRerun`, including update name, outcome, backend application state, and bounded failure reason when available.
- **FR-005**: Telemetry MUST avoid unbounded or sensitive task input content, artifact contents, raw credentials, and full payload dumps.
- **FR-006**: Telemetry failures MUST NOT block navigation, draft reconstruction, artifact preparation, or submit behavior.
- **FR-007**: Regression coverage MUST include supported active execution edit from prefilled form through successful `UpdateInputs` submit and Temporal detail return.
- **FR-008**: Regression coverage MUST include supported terminal execution rerun from prefilled form through successful `RequestRerun` submit and Temporal detail return.
- **FR-009**: Regression coverage MUST include unsupported workflow type, missing edit or rerun capability, missing artifact during reconstruction, malformed artifact during reconstruction, stale execution state between page load and submit, validation error, and artifact externalization failure.
- **FR-010**: Unit coverage MUST protect canonical route parsing, submit mode resolution, rerun-over-edit query precedence, and artifact-safe edit/rerun payload building.
- **FR-011**: The primary Temporal task editing flows MUST NOT use `/tasks/queue/new`, `editJobId`, queue update routes, queue resubmit language, or queue fallback behavior.
- **FR-012**: Successful edit and rerun flows MUST return operators to a Temporal detail context and refresh or make latest state available rather than sending operators to a queue page or generic list.
- **FR-013**: Operator-facing copy for Temporal task editing MUST distinguish active in-place edit from terminal rerun and MUST avoid queue-era resubmit language.
- **FR-014**: Runtime-visible documentation or internal references that describe active primary Temporal task editing behavior MUST reflect the Temporal-native model; historical or archived queue-era specs may remain only when clearly not primary flow guidance.
- **FR-015**: The `temporalTaskEditing` rollout control MUST support local development and staging validation, internal dogfood, small production cohort exposure, and all-operator expansion.
- **FR-016**: Rollout readiness MUST define acceptable health signals before broad enablement, including low edit/rerun failure rates, no queue fallback usage, and acceptable operator support feedback.
- **FR-017**: Required deliverables MUST include production runtime code changes plus validation tests; docs-only or spec-only completion is not sufficient.

### Key Entities

- **Temporal Task Editing Event**: A bounded observable record of a detail action, draft reconstruction outcome, submit attempt, or submit result, with event type, mode, workflow identity, update name when applicable, outcome, application state, and failure reason when applicable.
- **Failure Reason**: A normalized explanation for missing capability, unsupported workflow, stale state, malformed or missing artifact, validation failure, artifact preparation failure, or unexpected rejection.
- **Regression Scenario**: A repeatable local or CI-safe case proving a supported flow or explicit failure behavior.
- **Primary Runtime Flow**: The operator-visible Temporal task editing route, form, submit, redirect, and success/error copy used outside historical specs or migration notes.
- **Rollout Stage**: A feature exposure state such as local development, staging, internal dogfood, small production cohort, or all operators.

### Assumptions & Dependencies

- Initial task editing support remains scoped to supported `MoonMind.Run` Temporal executions.
- Capability flags remain authoritative for edit and rerun visibility, while submit still revalidates current workflow state.
- Existing artifact creation and read surfaces remain available for edit and rerun reconstruction and payload preparation.
- The shared task form remains the canonical create, edit, and rerun surface.
- Historical specs and migration documents may retain queue-era terms if they clearly describe prior behavior rather than current primary flow guidance.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of detail-page Edit and Rerun interactions in supported fixtures emit bounded client telemetry with the correct mode and execution identity.
- **SC-002**: 100% of edit and rerun submit attempts in automated coverage emit or expose bounded attempt and result telemetry with the correct update name and outcome.
- **SC-003**: Automated regression coverage exists for every listed Phase 5 scenario: active edit, terminal rerun, unsupported workflow type, missing artifact, malformed artifact, stale state before submit, validation failure, artifact externalization failure, and rerun-over-edit query precedence.
- **SC-004**: 100% of successful edit and rerun tests return to a Temporal detail context and do not route to queue or generic list pages.
- **SC-005**: Primary runtime task editing surfaces contain no references to `/tasks/queue/new`, `editJobId`, queue update routes, or queue resubmit terminology.
- **SC-006**: The feature can be enabled in local/staging readiness paths and has documented rollout gates for dogfood, limited production exposure, and all-operator expansion based on error rate and support feedback.
- **SC-007**: The implementation is accepted only when production runtime code changes and validation tests are present and pass through the project’s required verification path.
