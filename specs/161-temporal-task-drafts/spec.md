# Feature Specification: Temporal Task Draft Reconstruction

**Feature Branch**: `161-temporal-task-drafts`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 using test-driven development from Task Editing Plan #021: Add /tasks/new mode plumbing and draft reconstruction for Temporal-native task editing. Restore task editing from the task details page in the Temporal architecture without reintroducing queue-era assumptions. Scope this specification to Phase 2 only: parse editExecutionId and rerunExecutionId route params, resolve mode precedence as rerunExecutionId then editExecutionId then create mode, model page modes as create/edit/rerun, load Temporal execution detail when edit or rerun params are present, validate initial support for workflow type MoonMind.Run only, validate requested mode against backend capabilities, implement buildTemporalSubmissionDraftFromExecution(execution), map execution detail into the shared create-form draft shape, reconstruct operator-visible instructions from inline execution fields or referenced input artifacts, prefill the first-slice field set including runtime, provider profile, model, effort, repository, starting branch, target branch, publish mode, task instructions, primary skill, and template state, update page title and primary CTA by mode, hide or omit unsupported controls such as recurring schedule options, and add explicit error states for unsupported workflow types, unreadable artifacts, or incomplete drafts. Preserve the temporalTaskEditing feature flag, support MoonMind.Run only, do not use editJobId, /tasks/queue/new, queue routes, queue update routes, or queue resubmit semantics, do not silently fall back to legacy queue behavior, do not mutate historical artifacts, and refuse to render misleading partial state when reconstruction fails. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resolve Submit Page Mode (Priority: P1)

As a MoonMind operator entering the shared task submission page from either a task detail action or a direct URL, I need the page to determine whether I am creating, editing, or rerunning a task so that the experience matches the Temporal execution action I requested.

**Why this priority**: Mode resolution is the first safety gate. Without it, the shared page cannot know whether to create a new run, reconstruct an editable draft, or prevent an unsupported Temporal action.

**Independent Test**: Open the shared task page with no task-editing parameter, with an edit execution identifier, with a rerun execution identifier, and with both identifiers; verify the resolved mode and selected execution are correct in each case.

**Acceptance Scenarios**:

1. **Given** no edit or rerun execution identifier is present, **When** the operator opens the shared submit page, **Then** the page resolves to create mode.
2. **Given** an edit execution identifier is present and no rerun execution identifier is present, **When** the operator opens the shared submit page, **Then** the page resolves to edit mode for that execution.
3. **Given** both edit and rerun execution identifiers are present, **When** the operator opens the shared submit page, **Then** rerun mode wins and the rerun execution is used.
4. **Given** the Temporal task editing feature flag is disabled, **When** the operator opens an edit or rerun URL, **Then** the page shows an explicit unavailable state instead of continuing into a legacy flow.

---

### User Story 2 - Reconstruct a Reviewable Draft (Priority: P2)

As a MoonMind operator editing or rerunning a supported Temporal task, I need the shared submit page to load the source execution and prefill the form with a trustworthy draft so that I can review the prior task configuration before later save or rerun submission work is added.

**Why this priority**: Draft reconstruction is the core Phase 2 value. Operators must see a complete and believable representation of the original task before any update or rerun submit path is safe.

**Independent Test**: Use a supported `MoonMind.Run` execution fixture with inline instructions and another fixture with artifact-backed instructions; verify both produce the same operator-visible draft fields.

**Acceptance Scenarios**:

1. **Given** a supported active `MoonMind.Run` execution with update capability, **When** the operator opens edit mode, **Then** the shared form is prefilled with runtime, provider profile, model, effort, repository, starting branch, target branch, publish mode, instructions, primary skill, and template state where available.
2. **Given** a supported terminal `MoonMind.Run` execution with rerun capability, **When** the operator opens rerun mode, **Then** the shared form is prefilled from the same reconstruction rules while preserving rerun-specific page labeling.
3. **Given** task instructions are stored inline on the execution detail, **When** the draft is reconstructed, **Then** those instructions appear as the operator-visible task instructions.
4. **Given** task instructions are stored in a referenced input artifact, **When** the draft is reconstructed, **Then** the artifact content is read and converted into the same operator-visible task instructions.

---

### User Story 3 - Refuse Unsafe or Incomplete Drafts (Priority: P3)

As a MoonMind operator, I need unsupported or incomplete edit and rerun states to fail clearly so that I do not accidentally submit a misleading partial task or fall back to queue-era behavior.

**Why this priority**: The feature must fail closed. Explicit refusal prevents data loss, incorrect operator assumptions, and accidental resurrection of legacy queue semantics.

**Independent Test**: Use unsupported workflow, missing capability, unreadable artifact, malformed artifact, and incomplete draft fixtures; verify each produces an operator-readable error and no submit-ready partial form state.

**Acceptance Scenarios**:

1. **Given** an execution whose workflow type is not `MoonMind.Run`, **When** the operator opens edit or rerun mode, **Then** the page shows an unsupported workflow error.
2. **Given** edit mode is requested but the execution lacks update capability, **When** the page loads, **Then** the page shows a capability error and does not present the draft as editable.
3. **Given** rerun mode is requested but the execution lacks rerun capability, **When** the page loads, **Then** the page shows a capability error and does not present the draft as rerunnable.
4. **Given** a referenced input artifact cannot be read or cannot be interpreted, **When** reconstruction runs, **Then** the page shows an artifact reconstruction error and prevents misleading partial state.
5. **Given** required task instructions cannot be reconstructed, **When** edit or rerun mode loads, **Then** the page shows an incomplete draft error.

### Edge Cases

- Both edit and rerun identifiers are present; rerun mode must take precedence.
- The feature flag is off while execution capabilities would otherwise allow edit or rerun.
- The execution is not `MoonMind.Run` but has fields that look similar to a task run.
- The requested mode does not match the execution's backend capabilities.
- Instructions exist only in a referenced artifact, and the artifact is missing, unreadable, malformed, or no longer accessible.
- Execution detail contains partial task configuration but no trustworthy operator-visible instructions.
- Historical artifacts must remain immutable; reconstruction may read them but must not change them.
- The page must not route through or generate `editJobId`, `/tasks/queue/new`, queue update routes, or queue resubmit behavior.
- Recurring schedule controls and other unsupported controls must not be presented as valid edit or rerun controls.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The shared task submit page MUST support create, edit, and rerun page modes.
- **FR-002**: The system MUST resolve mode precedence as rerun execution identifier first, edit execution identifier second, and create mode when neither identifier is present.
- **FR-003**: The system MUST load Temporal execution detail when edit or rerun mode is requested.
- **FR-004**: The system MUST support only `MoonMind.Run` executions for Phase 2 edit and rerun draft reconstruction.
- **FR-005**: The system MUST validate edit mode against the execution's input-update capability before presenting a reconstructed edit draft as usable.
- **FR-006**: The system MUST validate rerun mode against the execution's rerun capability before presenting a reconstructed rerun draft as usable.
- **FR-007**: The system MUST provide a single draft reconstruction entry point that converts Temporal execution detail into the shared submit-form draft shape.
- **FR-008**: The reconstructed draft MUST include the first-slice field set where available: runtime, provider profile, model, effort, repository, starting branch, target branch, publish mode, task instructions, primary skill, and template state.
- **FR-009**: The system MUST reconstruct task instructions from inline execution data when inline instructions are available.
- **FR-010**: The system MUST reconstruct task instructions from a referenced input artifact when inline instructions are absent and an input artifact is available.
- **FR-011**: The page title and primary action label MUST reflect create, edit, and rerun modes distinctly.
- **FR-012**: The page MUST hide or omit controls that are unsupported in edit or rerun mode, including recurring schedule controls.
- **FR-013**: Unsupported workflow types MUST produce an explicit operator-readable error state.
- **FR-014**: Missing capabilities MUST produce an explicit operator-readable error state for the requested mode.
- **FR-015**: Unreadable, malformed, or incomplete artifact-backed input MUST produce an explicit operator-readable error state.
- **FR-016**: Incomplete drafts that lack required operator-visible instructions MUST be refused rather than shown as submit-ready partial state.
- **FR-017**: The feature MUST remain gated by the `temporalTaskEditing` feature flag for edit and rerun modes.
- **FR-018**: The feature MUST NOT use or generate `editJobId`, `/tasks/queue/new`, queue routes, queue update routes, or queue resubmit semantics.
- **FR-019**: Historical input artifacts MUST NOT be mutated during draft reconstruction.
- **FR-020**: Runtime delivery MUST include production code changes and validation tests; docs-only or spec-only changes are insufficient.

### Key Entities *(include if feature involves data)*

- **Task Submit Page Mode**: The resolved operator intent for the shared submit page: create, edit, or rerun.
- **Temporal Source Execution**: The existing execution used to reconstruct a draft, identified by workflow identity and constrained to `MoonMind.Run` for this phase.
- **Task Editing Capability Set**: Backend-provided action flags that determine whether edit or rerun mode may proceed.
- **Temporal Submission Draft**: The shared form data reconstructed from execution detail and, when needed, referenced input artifacts.
- **Input Artifact Reference**: A historical immutable artifact reference that may be read to recover operator-visible task instructions.
- **Template State**: Prior applied template information restored into the reviewable draft when present.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Mode resolution tests cover create, edit, rerun, and both-identifiers-present cases with 100% expected outcomes.
- **SC-002**: Supported `MoonMind.Run` edit and rerun fixtures reconstruct all available first-slice draft fields in validation tests.
- **SC-003**: Inline and artifact-backed instruction fixtures both produce identical operator-visible instruction placement in the shared form.
- **SC-004**: Unsupported workflow, missing capability, unreadable artifact, malformed artifact, and incomplete draft fixtures each produce explicit error states in validation tests.
- **SC-005**: Edit and rerun modes do not expose recurring schedule controls in validation tests.
- **SC-006**: Validation tests confirm no Phase 2 task editing path generates `editJobId`, `/tasks/queue/new`, queue update routes, or queue resubmit behavior.
- **SC-007**: Runtime implementation passes the project unit-test runner and targeted frontend tests for mode resolution, draft reconstruction, prefill, and error handling.
