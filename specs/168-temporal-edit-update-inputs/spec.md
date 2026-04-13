# Feature Specification: Temporal Edit UpdateInputs

**Feature Branch**: `168-temporal-edit-update-inputs`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 3 from Task Editing Plan #021 for the Temporal-native Task Editing System: ship active execution editing with UpdateInputs. Restore the core lost behavior where internal operators can open an active supported MoonMind.Run Temporal execution from its detail page, edit supported task fields in the shared /tasks/new edit mode, and save changes back to that same execution through the canonical Temporal update endpoint. Payload preparation must externalize edited instructions or oversized inputs according to existing artifact rules, create new artifact references rather than mutating historical artifacts, and build an artifact-safe update payload containing updateName UpdateInputs, inputArtifactRef when applicable, parametersPatch, and any required workflow-specific metadata. Submit handling must POST to /api/executions/{workflowId}/update with updateName UpdateInputs, handle backend states including accepted/applied, scheduled for safe point, and rejected because workflow state changed, and must not fall back to editJobId, /tasks/queue/new, queue routes, queue update routes, or queue resubmit semantics. Success UX must show a clear message reflecting backend semantics, redirect back to the Temporal execution detail view, and refresh detail data after navigation. Failure UX must show explicit operator-readable messages for capability mismatch, artifact creation failure, validation errors, and stale state where the workflow moved terminal before submit. Initial support is MoonMind.Run only and all behavior remains gated by the temporalTaskEditing feature flag and backend capability flags. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Save Active Execution Edits (Priority: P1)

As an internal operator viewing a supported active task, I need to edit supported task inputs from the shared task form and save those changes back to the same Temporal execution so that I can correct or refine in-flight work without restarting from a queue-era path.

**Why this priority**: This restores the core lost behavior and is the minimum useful Phase 3 vertical slice.

**Independent Test**: Open an active supported `MoonMind.Run` execution through the edit route, change a supported field, submit, and verify the same execution receives an `UpdateInputs` request and the operator returns to its Temporal detail view.

**Acceptance Scenarios**:

1. **Given** an active `MoonMind.Run` execution with the edit capability enabled, **When** the operator opens edit mode, changes supported task fields, and saves, **Then** the system submits an `UpdateInputs` request for that execution.
2. **Given** an accepted update response that reports immediate application, **When** the save completes, **Then** the operator sees a success message that indicates the change was saved and lands back on the Temporal detail view for the same execution.
3. **Given** an accepted update response that reports scheduling for a safe point, **When** the save completes, **Then** the operator sees a success message that indicates the change was scheduled and lands back on the Temporal detail view for the same execution.

---

### User Story 2 - Preserve Artifact Auditability (Priority: P2)

As an internal operator editing a task whose historical inputs may be artifact-backed, I need edits to create new input state rather than mutate previous artifacts so that task history remains auditable.

**Why this priority**: Artifact immutability is a safety and audit requirement for Temporal task editing.

**Independent Test**: Edit an execution that references an existing input artifact and verify the saved update uses a newly created artifact reference, not the historical one.

**Acceptance Scenarios**:

1. **Given** an editable execution with a historical input artifact reference, **When** the operator saves edited instructions, **Then** the system creates a new artifact reference for the edited input content.
2. **Given** edited input content exceeds the inline input policy, **When** the operator saves, **Then** the system externalizes the edited content and includes the new artifact reference in the update request.
3. **Given** a historical input artifact exists, **When** edits are saved, **Then** the historical artifact is not modified or reused as the edited input reference.

---

### User Story 3 - Explain Rejected Edits (Priority: P3)

As an internal operator, I need clear failure messages when an edit can no longer be applied so that I understand whether the task changed state, the payload was invalid, or artifact preparation failed.

**Why this priority**: Active task state can change between page load and submit; operators need explicit failure handling rather than silent fallback or misleading success.

**Independent Test**: Load an edit-capable execution, simulate capability loss, validation failure, artifact creation failure, and stale terminal-state rejection, and verify each outcome blocks redirect and shows an operator-readable reason.

**Acceptance Scenarios**:

1. **Given** an execution that was editable at page load but becomes terminal before submit, **When** the operator saves, **Then** the system shows a stale-state message and does not redirect as if the edit succeeded.
2. **Given** artifact creation fails while preparing edited content, **When** the operator saves, **Then** the system shows an artifact-preparation failure and does not submit a misleading update.
3. **Given** the backend rejects the update for validation or capability reasons, **When** the operator saves, **Then** the system shows the backend reason and keeps the operator in edit mode.

### Edge Cases

- Both edit and rerun route parameters are present; rerun precedence remains owned by the shared mode resolver, but this feature only enables edit submission.
- The `temporalTaskEditing` feature flag is disabled after the operator receives an edit link.
- The execution detail capability says editing is unavailable even though the execution appears active.
- The workflow type is not `MoonMind.Run`.
- The execution becomes terminal between draft load and submit.
- Edited inputs are small enough to remain inline.
- Edited inputs are oversized or replace historical artifact-backed inputs.
- Artifact creation, upload, or completion fails.
- The update response is accepted but reports deferred safe-point application.
- The update response is accepted but reports continue-as-new semantics.
- The update response is rejected with an operator-readable reason.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support saving edits for active `MoonMind.Run` Temporal executions only.
- **FR-002**: The system MUST gate edit submission behind the `temporalTaskEditing` feature flag and the execution's backend-provided edit capability.
- **FR-003**: The system MUST use the existing shared task form edit mode as the operator-facing edit surface.
- **FR-004**: The system MUST submit active edit saves to the same execution using the canonical `UpdateInputs` update name.
- **FR-005**: The update request MUST include a structured patch of the edited task input state.
- **FR-006**: The update request MUST include a new input artifact reference when edited input content is artifact-backed or must be externalized by existing artifact rules.
- **FR-007**: The system MUST create new artifact references for edited artifact-backed content and MUST NOT mutate historical artifacts in place.
- **FR-008**: The system MUST preserve all user-visible supported field edits from the shared form in the update request.
- **FR-009**: The system MUST handle accepted update responses that indicate immediate application, safe-point scheduling, or continue-as-new style handling.
- **FR-010**: The success message MUST reflect the backend outcome rather than always claiming immediate application.
- **FR-011**: After a successful edit update, the operator MUST return to the Temporal detail view for the relevant execution context.
- **FR-012**: The returned Temporal detail experience MUST refresh or refetch so the operator sees current execution state after navigation.
- **FR-013**: The system MUST show explicit operator-readable errors for capability mismatch, validation rejection, artifact preparation failure, and stale terminal-state rejection.
- **FR-014**: Failed edit saves MUST NOT redirect as successful updates.
- **FR-015**: This feature MUST NOT introduce or use `editJobId`, `/tasks/queue/new`, queue routes, queue update routes, or queue resubmit semantics.
- **FR-016**: Rerun submission remains out of scope for this feature and MUST NOT be silently enabled through the edit update flow.
- **FR-017**: Required deliverables MUST include production runtime code changes and validation tests; docs-only or spec-only changes do not satisfy this feature.

### Key Entities *(include if feature involves data)*

- **Editable Temporal Execution**: A supported active `MoonMind.Run` execution identified by workflow identity, current lifecycle state, and backend-provided edit capability.
- **Edited Task Input State**: The operator-reviewed task fields from the shared form that represent the desired replacement input state for the active execution.
- **Input Artifact Reference**: An immutable reference to task input content; edits create a new reference when artifact-backed content is replaced or externalization is required.
- **Update Outcome**: The backend result of the edit request, including accepted/applied, scheduled for safe point, continue-as-new style handling, or rejected state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation tests, 100% of supported active `MoonMind.Run` edit submissions send `UpdateInputs` to the same execution context.
- **SC-002**: In validation tests, 100% of artifact-backed edit submissions use a new input artifact reference and never reuse the historical artifact as the edited input reference.
- **SC-003**: In validation tests, accepted immediate, safe-point, and continue-as-new outcomes each produce distinct operator-visible success messaging.
- **SC-004**: In validation tests, stale terminal-state rejection, capability mismatch, validation rejection, and artifact preparation failure each block success redirect and show an explicit failure message.
- **SC-005**: Route and payload validation confirms no primary edit flow uses `editJobId`, `/tasks/queue/new`, queue update routes, or queue resubmit terminology.
- **SC-006**: The required runtime validation suite passes with production code changes and tests included.
