# Feature Specification: Temporal Rerun Submit

**Feature Branch**: `169-temporal-rerun-submit`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 using test-driven development from Task Editing Plan #021: Add terminal execution rerun with RequestRerun. Reuse the shared /tasks/new form and Temporal draft reconstruction for terminal MoonMind.Run executions. Rerun mode must submit to the existing execution update endpoint with updateName RequestRerun, use the same artifact-safe payload preparation as edit mode, preserve the distinction between editing an active execution in place and rerunning a terminal execution, return operators to the original Temporal execution context after request acceptance, land on or clearly expose the latest run detail view when supported, preserve lineage metadata for the rerun source, newly created artifacts, and resulting run chain, and add regression tests comparing edit vs rerun semantics. Terminal executions must be rerunnable without queue-era routes, editJobId, or resubmit fallback behavior. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Rerun a Terminal Task (Priority: P1)

An operator reviewing a completed, failed, canceled, or otherwise terminal supported Temporal task can choose **Rerun**, review the reconstructed task draft in the shared task form, make any supported adjustments, and request a rerun without creating a queue-era resubmit flow.

**Why this priority**: This is the core Phase 4 value: terminal executions become actionable again from the Temporal-native task detail experience.

**Independent Test**: Start from a supported terminal `MoonMind.Run` execution with rerun capability, open the shared task form in rerun mode, submit the reviewed draft, and verify a rerun request is accepted for that execution.

**Acceptance Scenarios**:

1. **Given** a terminal `MoonMind.Run` execution with rerun capability, **When** the operator opens rerun mode from the task detail page, **Then** the shared task form loads with reconstructed task inputs and a **Rerun Task** primary action.
2. **Given** the rerun form contains reviewed task inputs, **When** the operator submits, **Then** the system requests a Temporal rerun for the source execution using the canonical rerun update name.
3. **Given** the source execution used artifact-backed inputs, **When** the operator submits modified rerun inputs, **Then** the system creates replacement input artifacts instead of mutating historical artifacts.

---

### User Story 2 - Preserve Rerun Lineage (Priority: P2)

An operator can understand which execution was used as the rerun source, which new input artifacts were produced for the rerun request, and which latest run context should be inspected after acceptance.

**Why this priority**: Rerun changes must remain auditable; without lineage, operators cannot confidently trace why a later run used different inputs.

**Independent Test**: Submit a rerun request with modified input content and verify the resulting success state references the source execution context and exposes the latest available run view or lineage outcome.

**Acceptance Scenarios**:

1. **Given** a rerun request is accepted, **When** the operator is redirected after submission, **Then** the operator lands back in the Temporal execution context rather than a queue page or generic list.
2. **Given** the backend reports a latest run or run-chain result, **When** the success state is displayed, **Then** the operator can identify the latest execution view or rerun lineage outcome.
3. **Given** new artifacts were created for rerun inputs, **When** the execution is inspected later, **Then** the rerun source and replacement artifact references are distinguishable from historical input artifacts.

---

### User Story 3 - Block Unsupported or Stale Reruns (Priority: P3)

An operator receives explicit, actionable feedback when rerun is unavailable because the execution type, capability flags, reconstruction state, artifact preparation, or backend lifecycle state does not support rerun.

**Why this priority**: The feature must avoid misleading partial submits and must not silently fall back to legacy queue behavior.

**Independent Test**: Exercise unsupported workflow types, missing rerun capability, unreadable input artifacts, and state changes between page load and submit; verify rerun is blocked with explicit messages.

**Acceptance Scenarios**:

1. **Given** an unsupported workflow type, **When** rerun mode is requested, **Then** the form does not allow submission and shows an operator-readable unsupported-state message.
2. **Given** a terminal execution lacks rerun capability, **When** rerun mode is requested, **Then** the system blocks submission instead of rendering a misleading rerun action.
3. **Given** an execution becomes ineligible after the form loads, **When** the operator submits, **Then** the backend rejection is surfaced and the operator is not redirected as if the rerun succeeded.

### Edge Cases

- Both edit and rerun identifiers are present in the shared task form route; rerun mode remains the selected mode.
- The input artifact needed for reconstruction is missing, unreadable, or malformed; the form blocks submission rather than showing partial state.
- Artifact creation fails while preparing modified rerun inputs; no rerun request is submitted.
- The backend accepts the rerun but reports different application semantics, such as continuation into a latest run view; the success message distinguishes rerun from in-place edit.
- The execution identifier is missing or malformed by the time of submit; the system fails explicitly.
- The operator changes fields that overlap with active edit behavior; rerun semantics remain distinct from in-place input update semantics.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support rerun submission from the shared task form for supported terminal `MoonMind.Run` executions that expose rerun capability.
- **FR-002**: The shared task form MUST resolve rerun mode ahead of edit and create modes when rerun and edit identifiers are both present.
- **FR-003**: The rerun form MUST reuse Temporal execution draft reconstruction so operators review the same supported task fields available in edit mode.
- **FR-004**: The rerun form MUST validate that the source execution is a supported workflow type and has rerun capability before allowing submission.
- **FR-005**: Rerun submission MUST use the canonical Temporal rerun update name `RequestRerun`.
- **FR-006**: Rerun submission MUST preserve the semantic distinction between rerunning a terminal execution and editing an active execution in place.
- **FR-007**: Rerun submission MUST use artifact-safe input preparation and create replacement input artifact references when modified inputs require artifact storage or the source used artifact-backed inputs.
- **FR-008**: The system MUST NOT mutate historical input artifacts when preparing a rerun request.
- **FR-009**: The system MUST return operators to the Temporal execution context after rerun request acceptance.
- **FR-010**: When a latest run view or run-chain result is available, the success state MUST make that latest rerun context clear to the operator.
- **FR-011**: The system MUST preserve enough rerun lineage metadata to identify the source execution, replacement input artifacts, and resulting run or run chain.
- **FR-012**: The system MUST surface backend rerun rejections, validation failures, stale lifecycle states, and artifact preparation failures without redirecting as if submission succeeded.
- **FR-013**: The system MUST NOT use queue-era routes, `editJobId`, queue resubmit terminology, or legacy queue fallback behavior for terminal Temporal reruns.
- **FR-014**: Regression coverage MUST compare edit and rerun semantics, including update name, capability validation, artifact handling, success redirect, and rejection handling.
- **FR-015**: Required deliverables MUST include production runtime code changes plus validation tests; docs-only or spec-only completion is not sufficient.

### Key Entities

- **Temporal Execution**: The source execution identified by workflow identity, workflow type, lifecycle state, current input state, artifact references, and rerun capability.
- **Rerun Draft**: The reconstructed operator-reviewable task inputs derived from execution fields and input artifacts.
- **Rerun Request**: The submitted rerun intent containing the canonical rerun update name, structured input changes, and any replacement artifact references.
- **Input Artifact Reference**: Immutable reference to task input content; historical references are preserved and rerun submissions create new references when content changes or must be externalized.
- **Rerun Lineage**: The relationship between source execution, replacement artifacts, accepted rerun request, and latest run or run-chain outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of supported terminal `MoonMind.Run` rerun submissions use `RequestRerun` and do not call create, queue, or legacy resubmit flows.
- **SC-002**: 100% of artifact-backed rerun submissions with modified inputs create a replacement artifact reference rather than reusing or mutating the historical input artifact.
- **SC-003**: Operators submitting an accepted rerun request return to a Temporal execution context in every successful rerun scenario.
- **SC-004**: Unsupported workflow type, missing capability, unreadable artifact, stale lifecycle, and artifact preparation failure scenarios each have automated regression coverage and explicit operator-visible failure messages.
- **SC-005**: Automated regression tests demonstrate that edit mode submits an in-place input update while rerun mode submits a terminal rerun request, with no cross-mode fallback.
- **SC-006**: No primary rerun path uses `/tasks/queue/new`, `editJobId`, or queue resubmit language.
