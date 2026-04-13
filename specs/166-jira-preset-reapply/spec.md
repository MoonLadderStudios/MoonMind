# Feature Specification: Jira Preset Reapply Signaling

**Feature Branch**: `166-jira-preset-reapply`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 6 using test-driven development of the Jira UI plan. Goal: add preset reapply semantics and conflict signaling for Jira imports on the Create page. When Jira import updates preset instructions after a preset has already been applied, show a non-blocking message: 'Preset instructions changed. Reapply the preset to regenerate preset-derived steps.' Do not silently mutate already-expanded preset steps; preserve the current step list until the user explicitly reapplies. Add step-level conflict messaging: if importing into a template-bound step, warn that the step will become manually customized while still allowing import. Acceptance: no hidden rewrites of already-applied preset steps, and users understand when reapply is needed. Runtime mode. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Understand Preset Reapply Need (Priority: P1)

An operator who has already applied a task preset imports Jira story text into the preset instructions field and immediately understands that the existing expanded steps were not regenerated.

**Why this priority**: This prevents hidden task-plan rewrites and protects operators from assuming existing preset-derived steps automatically reflect newly imported Jira context.

**Independent Test**: Apply a preset, import Jira text into the preset instructions target, and confirm the draft step list remains unchanged while the page tells the operator to reapply the preset.

**Acceptance Scenarios**:

1. **Given** a preset has already been applied and expanded into steps, **When** the operator imports Jira text that changes the preset instructions field, **Then** the system displays "Preset instructions changed. Reapply the preset to regenerate preset-derived steps."
2. **Given** a preset has already been applied and expanded into steps, **When** the operator imports Jira text into the preset instructions field, **Then** the already-expanded steps remain unchanged until the operator explicitly reapplies the preset.
3. **Given** the preset instructions field is marked as needing reapply, **When** the operator restores the field to the last applied preset instructions value, **Then** the reapply-needed message is cleared.

---

### User Story 2 - Reapply Explicitly (Priority: P2)

An operator who sees that preset instructions changed can clearly identify the explicit action that will regenerate preset-derived steps.

**Why this priority**: The feature should not only warn about stale preset-derived steps; it should also make the safe next action obvious.

**Independent Test**: Change preset instructions after applying a preset and confirm the preset action is presented as a reapply action without changing the draft steps until selected.

**Acceptance Scenarios**:

1. **Given** a preset has already been applied and preset instructions have changed through Jira import, **When** the operator reviews the preset controls, **Then** the system presents an explicit reapply action.
2. **Given** the explicit reapply action is available, **When** the operator has not selected it, **Then** the system preserves the current step list exactly as authored.

---

### User Story 3 - Understand Template-Bound Step Customization (Priority: P3)

An operator importing Jira text into a template-bound preset step understands that the import will convert that step into a manual customization while still being allowed to proceed.

**Why this priority**: Template identity affects later task semantics and operator expectations, but the warning should not block intentional edits.

**Independent Test**: Open the Jira browser for a template-bound step and confirm a customization warning appears; complete the import and confirm the step becomes manually customized.

**Acceptance Scenarios**:

1. **Given** a step is still bound to a preset template, **When** the operator opens Jira import for that step, **Then** the system warns that importing into the step will make it manually customized.
2. **Given** a customization warning is displayed for a template-bound step, **When** the operator chooses to import Jira text, **Then** the import is allowed and updates only the targeted step instructions.
3. **Given** Jira text has been imported into a previously template-bound step, **When** the operator reviews or submits the task draft, **Then** that edited step is treated as manually customized rather than template-bound by instruction identity.

### Edge Cases

- If Jira import produces the same preset instructions text that is already present, the system must not mark the preset as needing reapply.
- If Jira import targets a step that is not template-bound, the system must not show a template customization warning.
- If a template-bound step was already manually edited before opening Jira import, the system must not show the template-bound warning for that step.
- If the operator closes the Jira browser without importing text, the system must not change preset reapply state or step template identity.
- If Jira browsing or issue loading fails, manual preset and step editing must remain available.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST detect when Jira import changes the preset instructions field after one or more presets have already been applied.
- **FR-002**: The system MUST display the exact non-blocking message "Preset instructions changed. Reapply the preset to regenerate preset-derived steps." when FR-001 occurs.
- **FR-003**: The system MUST NOT automatically regenerate, replace, reorder, or otherwise mutate already-expanded preset-derived steps when preset instructions change through Jira import.
- **FR-004**: Users MUST be able to explicitly reapply the preset after imported Jira text changes preset instructions.
- **FR-005**: The system MUST make the explicit reapply action clear while preset instructions are in the reapply-needed state.
- **FR-006**: The system MUST clear the reapply-needed state when the preset instructions no longer differ from the last applied preset instructions value.
- **FR-007**: The system MUST avoid entering the reapply-needed state when Jira import leaves the preset instructions effectively unchanged.
- **FR-008**: The system MUST detect when Jira import targets a step that is still template-bound by instruction identity.
- **FR-009**: The system MUST warn users that importing Jira text into a template-bound step will make the step manually customized.
- **FR-010**: The system MUST allow Jira import into a template-bound step after showing the warning.
- **FR-011**: Jira import into a step MUST update only the targeted step instructions.
- **FR-012**: Jira import into a template-bound step MUST count as a manual instruction edit and detach the step from template-bound instruction identity.
- **FR-013**: Jira import conflict and reapply signaling MUST be delivered through production runtime behavior, not docs-only or spec-only changes.
- **FR-014**: The feature MUST include validation tests covering preset reapply signaling, unchanged preset import behavior, preservation of expanded steps, template-bound step warnings, and manual customization after step import.

### Key Entities *(include if feature involves data)*

- **Preset Instructions**: The preset-owned text that can be populated manually or by Jira import and can drive preset-derived task generation.
- **Applied Preset State**: The record that a preset has already been applied to the draft, including the preset instructions value used for that application.
- **Preset-Derived Step**: A step created from a preset application that may retain template identity until manually customized.
- **Jira Import Target**: The selected destination for imported Jira text, either preset instructions or a specific step's instructions.
- **Reapply Needed State**: A transient UI state indicating that preset instructions changed after preset application and that expanded steps are intentionally unchanged until explicit reapply.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation, 100% of Jira imports that change previously applied preset instructions produce the reapply-needed message without modifying existing expanded steps.
- **SC-002**: In validation, 100% of Jira imports that do not change preset instructions avoid false reapply-needed messaging.
- **SC-003**: In validation, 100% of Jira imports opened against template-bound steps show the customization warning before import.
- **SC-004**: In validation, 100% of Jira imports into template-bound steps update only the targeted step and leave other steps unchanged.
- **SC-005**: Manual task creation remains available after Jira browser failures in all covered validation scenarios.

## Assumptions

- Jira browsing and import targets already exist; this feature focuses on the interaction safety around preset reapply state and template-bound step customization.
- Reapply means the existing explicit preset application flow, not automatic background regeneration.
- The warning for template-bound steps is informational and must not require an additional confirmation step.
