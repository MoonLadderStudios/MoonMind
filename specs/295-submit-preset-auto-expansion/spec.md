# Feature Specification: Submit Preset Auto-Expansion

**Feature Branch**: `295-submit-preset-auto-expansion`
**Created**: 2026-05-04
**Status**: Draft
**Input**: User description: "Implement the docs/UI/CreatePage.md convenience path for expanding presets automatically"

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Submit Draft With Unresolved Presets

**Summary**: As a Create-page user, I want unresolved Preset steps to expand automatically when I explicitly submit the task so that I can create, update, or rerun tasks without manually previewing and applying trusted presets first.

**Goal**: Users can submit a valid draft that contains one or more unresolved Preset steps, and MoonMind turns those presets into executable Tool and/or Skill steps for the submitted task while preserving the user's visible draft and blocking unsafe or ambiguous submissions.

**Independent Test**: Can be fully tested by authoring a Create-page draft with unresolved Preset steps, clicking the primary create/update/rerun action, and verifying that the resulting task submission contains only executable Tool and/or Skill steps with preset provenance, or that no task is submitted when expansion cannot be completed safely.

**Acceptance Scenarios**:

1. **SCN-001**: **Given** a valid Create-page draft with one unresolved Preset step and valid required inputs, **When** the user clicks Create, Update, or Rerun, **Then** the preset is expanded during that submit attempt and the submitted task contains the generated executable steps instead of the unresolved Preset payload.
2. **SCN-002**: **Given** a valid draft with multiple unresolved Preset steps, **When** the user submits the draft, **Then** each unresolved Preset is expanded in authored step order and its generated executable steps occupy the same relative position in the submitted task.
3. **SCN-003**: **Given** a valid unresolved Preset whose expansion returns provenance and warnings that do not require review, **When** the user submits the draft, **Then** the submitted executable steps preserve available provenance and the submission feedback can show the warnings without blocking the task.
4. **SCN-004**: **Given** an unresolved Preset whose expansion is unavailable, unauthorized, invalid, ambiguous, stale, or fails, **When** the user submits the draft, **Then** no task is created, updated, or rerun and the relevant Preset step shows a recoverable error while the rest of the draft remains preserved.
5. **SCN-005**: **Given** a user selects a Preset, loads its descriptor, imports external context, uploads attachments, previews the Preset, or navigates within the Create page, **When** no primary submit action has been clicked, **Then** no task is created and submit-time expansion does not run.
6. **SCN-006**: **Given** a generated executable payload would require attachment retargeting or publish/merge constraint changes, **When** the mapping or constraint can be applied unambiguously, **Then** the final submission uses the same behavior and visible explanation as manual Apply; otherwise, submission is blocked for manual review.
7. **SCN-007**: **Given** submit-time expansion succeeds but the final task submission fails, **When** the Create page shows the failure, **Then** the original Preset draft state is not silently discarded and the user can continue editing or manually apply the expanded result.

### Edge Cases

- Expansion must ignore duplicate submit clicks and stale expansion results so that one explicit submit attempt cannot create duplicate tasks or corrupt the draft.
- If a Preset version is not selected, the latest active version visible to the current user is resolved or the submission fails with a validation error.
- Generated steps that include incompatible stale type-specific fields must be rejected or cleaned before submission so only fields relevant to executable Tool or Skill steps are submitted.
- Warnings that require review or acknowledgement must block auto-submission and present a review path instead of silently continuing.
- Authoritative task validation must still reject unresolved Preset steps even when the Create-page convenience path is available.

## Assumptions

- Manual Preview and Apply already exist and remain available; this feature adds the submit-time convenience path without removing manual review.
- The preset expansion behavior used at submit time should match manual Apply semantics from the user's perspective.
- Edit and rerun reconstruction continue to preserve unresolved Preset draft state from authoritative task input snapshots when such authoring-only data exists.
- The source design document is the canonical Create-page contract for this story.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/UI/CreatePage.md` lines 63 and 77. Preset steps are authoring-time placeholders, and submit-time Preset auto-expansion is a Create-page convenience that submits an executable copy rather than an unresolved Preset payload. Scope: in scope. Mapped to FR-001, FR-004, FR-009.
- **DESIGN-REQ-002**: Source `docs/UI/CreatePage.md` lines 132 and 144. The Submit section validates, expands unresolved Preset steps only after an explicit primary create/update/rerun click, uploads artifacts, and submits status; selecting Presets or loading context never creates a task by itself. Scope: in scope. Mapped to FR-002, FR-003.
- **DESIGN-REQ-003**: Source `docs/UI/CreatePage.md` lines 194-208 and 264-271. Submit expansion state is transient UI state on Preset drafts and must not be part of final task snapshots or required for edit/rerun reconstruction. Scope: in scope. Mapped to FR-010.
- **DESIGN-REQ-004**: Source `docs/UI/CreatePage.md` lines 448-460. Submit-time expansion uses the same preset expansion semantics as Preview/Apply, while manual Preview and Apply remain first-class. Scope: in scope. Mapped to FR-005, FR-011.
- **DESIGN-REQ-005**: Source `docs/UI/CreatePage.md` lines 461-462. Expansion uses the selected Preset key, selected or user-visible active version, current inputs, task context, and user visibility and permissions; the Create page must not infer unavailable or unauthorized presets or versions. Scope: in scope. Mapped to FR-006, FR-007.
- **DESIGN-REQ-006**: Source `docs/UI/CreatePage.md` lines 463-466. Multiple unresolved Presets expand in authored order into a frozen submission copy, generated steps are equivalent to manual Apply output, and failure is non-mutating. Scope: in scope. Mapped to FR-004, FR-008, FR-009.
- **DESIGN-REQ-007**: Source `docs/UI/CreatePage.md` lines 467-470. Warnings, attachment retargeting, publish/merge constraints, duplicate clicks, and stale responses must be handled visibly and safely during expansion and final submission. Scope: in scope. Mapped to FR-012, FR-013, FR-014, FR-015.
- **DESIGN-REQ-008**: Source `docs/UI/CreatePage.md` lines 769-780. The final submitted payload contains only fields relevant to executable Step Types, contains no unresolved Preset steps, preserves provenance where available, does not require live preset lookup, and preserves enough snapshot information for reconstruction. Scope: in scope. Mapped to FR-004, FR-009, FR-010, FR-016.
- **DESIGN-REQ-009**: Source `docs/UI/CreatePage.md` lines 788-797. The canonical submit flow freezes the draft, validates task and Preset fields, resolves attachment refs when needed, expands unresolved Presets in order, applies warnings and constraints, validates executable-only steps, uploads remaining artifacts, and submits normally. Scope: in scope. Mapped to FR-002, FR-004, FR-008, FR-012, FR-013, FR-016.
- **DESIGN-REQ-010**: Source `docs/UI/CreatePage.md` lines 801-806. Auto-expansion is not linked live Preset execution; authoritative task validation still rejects unresolved Presets; expansion success followed by submission failure does not discard the original draft; cancellation ignores stale results. Scope: in scope. Mapped to FR-003, FR-009, FR-015, FR-017.
- **DESIGN-REQ-011**: Source `docs/UI/CreatePage.md` lines 949-967. Failure states remain local and visible, including Preset expansion failure copy and preserved draft state. Scope: in scope. Mapped to FR-014, FR-018.
- **DESIGN-REQ-012**: Source `docs/UI/CreatePage.md` lines 1003-1008. Tests must cover valid auto-expansion, blocking failed expansion, non-mutating preview failure, manual Apply behavior, stale response handling, flat executable submission, and provenance preservation. Scope: in scope. Mapped to FR-019.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST support submit-time expansion for unresolved Preset steps when the user explicitly clicks the primary Create, Update, or Rerun action.
- **FR-002**: The system MUST treat expansion and final submission as one guarded user-initiated submit attempt from the user's perspective.
- **FR-003**: The system MUST NOT trigger submit-time expansion or task creation from Preset selection, descriptor loading, external context import, attachment upload, preview, navigation, or any other non-submit interaction.
- **FR-004**: The final submitted task payload MUST contain only executable Tool and/or Skill steps and MUST NOT contain unresolved Preset steps.
- **FR-005**: Submit-time expansion MUST use the same validation and generated-step semantics as manual Preview/Apply.
- **FR-006**: Submit-time expansion MUST use only the selected Preset key, selected or user-visible system-resolved version, current Preset inputs, current task context, relevant attachment references, and the current user's catalog visibility and permissions.
- **FR-007**: The system MUST NOT infer a different Preset, inactive version, unavailable version, unauthorized version, or hidden user data during submit-time expansion.
- **FR-008**: When multiple unresolved Preset steps exist, the system MUST expand them in authored order and replace each Preset placeholder with generated executable steps in the same relative position in the submission copy.
- **FR-009**: Submit-time expansion MUST operate on a frozen submission copy first and MUST NOT silently overwrite the visible draft before final submission succeeds or before the user explicitly applies generated steps.
- **FR-010**: Transient submit-expansion UI state MUST NOT be included in the final task snapshot and MUST NOT be required for edit or rerun reconstruction.
- **FR-011**: Manual Preview and Apply MUST remain available and first-class for users who want to inspect or edit generated steps before submission.
- **FR-012**: The system MUST preserve available Preset provenance on generated executable steps and MUST NOT require live Preset lookup for runtime correctness.
- **FR-013**: The system MUST apply expansion warnings, assumptions, required capabilities, attachment target mappings, and publish/merge constraints using the same user-visible rules as manual Apply before final payload validation.
- **FR-014**: If submit-time expansion is unavailable, unauthorized, invalid, ambiguous, stale, cancelled, or fails, the system MUST block final submission, create no task/update/rerun side effect, and show a relevant recoverable error on or near the affected Preset step.
- **FR-015**: The primary submit action MUST be guarded during expansion and final submission so duplicate clicks and stale expansion responses cannot create duplicate tasks or corrupt the draft.
- **FR-016**: Local attachments needed for expansion or final executable payloads MUST resolve to structured attachment references before they are used, and ambiguous attachment retargeting MUST block auto-submission for manual review.
- **FR-017**: Authoritative task validation MUST reject unresolved Preset steps even when submit-time expansion is available.
- **FR-018**: If expansion succeeds but final submission fails, the UI MAY expose the expanded submission copy for review, but MUST preserve the user's original Preset draft state.
- **FR-019**: Validation coverage MUST demonstrate successful auto-expansion, blocked failed expansion, duplicate-click protection, stale-response handling, ordered multi-Preset expansion, provenance preservation, executable-only final payloads, authoritative rejection of unresolved Preset payloads, and manual Preview/Apply continuity.

### Key Entities

- **Create-page Draft**: The user's visible task authoring state, including objective, ordered steps, Step Types, type-specific inputs, attachments, repository, publishing settings, runtime settings, and schedule settings.
- **Preset Step**: An authoring-time step that identifies a selected Preset, optional version, user-provided inputs, descriptor details, preview state, and transient submit-expansion status.
- **Frozen Submission Copy**: A submit-attempt-specific copy of the draft used to expand unresolved Presets and build the final task payload without silently overwriting the visible draft.
- **Generated Executable Step**: A Tool or Skill step produced from a Preset expansion, with normal executable payload fields and optional Preset provenance.
- **Submission Feedback**: User-visible progress, warning, and error state for expansion and final submission outcomes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of successful submissions that begin with unresolved Preset steps, the final submitted task contains zero unresolved Preset steps.
- **SC-002**: In 100% of failed submit-time expansion attempts, no create, update, or rerun side effect occurs and the user's original draft remains available for correction.
- **SC-003**: Valid drafts with three unresolved Preset steps preserve authored relative step order after auto-expansion in all covered acceptance tests.
- **SC-004**: Duplicate primary-submit clicks during expansion produce no more than one final create, update, or rerun side effect in all covered acceptance tests.
- **SC-005**: The feature's validation coverage includes at least one successful create-style submission, one update or rerun submission, one expansion failure, one stale response or cancellation case, one attachment retargeting ambiguity case, and one authoritative unresolved-Preset rejection case.
