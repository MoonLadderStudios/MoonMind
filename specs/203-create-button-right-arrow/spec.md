# Feature Specification: Create Button Right Arrow

**Feature ID**: `203-create-button-right-arrow`  
**Managed PR Branch**: `mm-390`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "For a single-story Jira preset brief, run moonspec-specify unless an active spec.md already passes the specify gate.
For a broad technical or declarative design, run moonspec-breakdown first, then select the recommended first generated spec unless the issue brief explicitly requires processing all specs.
Preserve Jira issue MM-390 and the original preset brief in spec.md so final verification can compare against them."

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-390 from MM project
Summary: The Create button for the Create Page should actually be an arrow pointing to the right
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-390 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-390: The Create button for the Create Page should actually be an arrow pointing to the right

Source Reference
- Source Document: docs/UI/CreatePage.md
- Source Title: Create Page

User Story
As a Mission Control user, I want the Create Page submit action to use a right-pointing arrow icon so the primary action visually communicates forward progress.

Acceptance Criteria
- The Create Page primary Create button uses a right-pointing arrow icon for the submit action.
- The button remains recognizable as the primary Create action and preserves the existing submit behavior.
- The icon change does not alter task creation, validation, disabled/loading states, or Jira/preset import behavior.
- The button remains accessible, with existing text or accessible labeling sufficient for screen readers.
- The button layout remains stable across desktop and mobile Create Page viewports.

Requirements
- Update the Create Page submit button presentation so the visual icon points to the right.
- Preserve the existing task submission contract and all current Create Page controls.
- Preserve existing accessibility semantics for the submit action.
- Add or update focused UI coverage when an existing test asserts the button content or icon.

Relevant Implementation Notes
- Treat this as a narrow Create Page UI polish story.
- Prefer the existing Create Page component and icon system rather than introducing a new visual dependency.
- Do not change task execution payloads, preset expansion, Jira import, dependency controls, runtime controls, or publish behavior.
- If the current UI already uses an icon near the Create button, replace only that icon with the right-pointing arrow equivalent.
- If no icon is present, add the right-pointing arrow in a way that does not remove the Create action's accessible name.

Verification
- Confirm the Create Page primary Create button visibly uses a right-pointing arrow icon.
- Confirm submitting a valid task still uses the existing Create Page submission path.
- Confirm disabled/loading and validation states still behave as before.
- Confirm accessible labeling for the primary Create action remains intact.
- Preserve MM-390 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

Single-story runtime feature request. The brief contains one actor, one goal, and one independently testable behavior: the Create Page primary submit action must visually use a right-pointing arrow while preserving the existing create submission contract.

## User Story - Create Button Right Arrow

**Summary**: As a Mission Control user, I want the Create Page primary submit action to use a right-pointing arrow so that the action visually communicates moving forward with task creation.

**Goal**: Users can identify the Create Page primary submit action as a forward action without losing the existing Create action meaning, accessibility, or submission behavior.

**Independent Test**: Open the Create Page with a valid task draft, inspect the primary submit action in normal, disabled, and loading states, and submit the draft. The story passes when the primary submit action displays a right-pointing arrow, retains an accessible Create action name, remains layout-stable, and submits through the same create flow as before.

**Acceptance Scenarios**:

1. **Given** the Create Page renders with a valid draft, **when** the primary submit action is visible, **then** it includes a right-pointing arrow and remains identifiable as the Create action.
2. **Given** the Create Page submit action is disabled because the draft is invalid or submission is in progress, **when** the action is displayed, **then** the disabled or loading presentation remains stable and still communicates the same Create action.
3. **Given** a user submits a valid task from the Create Page, **when** the primary submit action is activated, **then** the existing task creation behavior is preserved.
4. **Given** assistive technology reads the primary submit action, **when** the action includes the right-pointing arrow, **then** the accessible action name still communicates Create or task creation.
5. **Given** the Create Page is viewed on desktop and mobile widths, **when** the primary submit action is rendered, **then** the button text and arrow fit without overlapping adjacent content or shifting the surrounding layout.

### Edge Cases

- The Create Page draft is invalid and the submit action is disabled.
- A submission is already in progress and the submit action shows a pending state.
- The action is rendered in a narrow mobile viewport.
- The action is rendered near other step controls at the bottom of the shared Steps card.
- Optional Jira, preset, dependency, or attachment controls are unavailable.

## Assumptions

- MM-390 is runtime UI behavior, not documentation-only work.
- `docs/UI/CreatePage.md` is treated as a runtime source requirements document for Create Page submit placement and submission behavior.
- The user-facing action may retain text such as Create while adding or replacing the visual indicator with a right-pointing arrow.
- Existing Create Page task validation and submission rules remain authoritative.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/UI/CreatePage.md`, section 7.1; MM-390 brief): The create, edit, and rerun submit actions appear at the bottom of the shared Steps card with the related step authoring controls. Scope: in scope. Maps to FR-001, FR-006.
- **DESIGN-REQ-002** (Source: `docs/UI/CreatePage.md`, section 14; MM-390 brief): Submit remains explicit and attachment selection alone does not create a task. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-003** (Source: `docs/UI/CreatePage.md`, section 14; MM-390 brief): Create Page submission preserves the existing task-shaped submit flow and must not alter payload meaning. Scope: in scope. Maps to FR-003, FR-005.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create Page primary submit action MUST display a right-pointing arrow as part of the visible action presentation.
- **FR-002**: The Create Page primary submit action MUST remain recognizable as the Create action to users.
- **FR-003**: Activating the Create Page primary submit action MUST preserve the existing task creation behavior for valid drafts.
- **FR-004**: The right-pointing arrow change MUST NOT create tasks implicitly or alter explicit submit requirements.
- **FR-005**: The right-pointing arrow change MUST NOT alter validation, disabled, loading, Jira import, preset, dependency, runtime, attachment, or publish behavior.
- **FR-006**: The primary submit action MUST remain visually stable at desktop and mobile widths, including disabled and loading states.
- **FR-007**: The primary submit action MUST retain an accessible name that communicates Create or task creation when the right-pointing arrow is present.
- **FR-008**: Automated coverage MUST verify the right-pointing arrow presentation when existing Create Page tests assert submit action content or behavior.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-390 and the original Jira preset brief.

### Key Entities

- **Create Page Primary Submit Action**: The user-facing control that explicitly submits a Create Page draft for task creation.
- **Right-Pointing Arrow**: The visible directional indicator attached to the primary submit action.
- **Task Draft**: The authored Create Page state that must remain governed by existing validation and submission rules.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Visual or UI-level verification confirms the primary Create Page submit action displays a right-pointing arrow in 100% of normal render cases.
- **SC-002**: Validation confirms 100% of existing enabled, disabled, and loading submit states preserve their previous behavioral outcome.
- **SC-003**: Accessibility verification confirms the primary submit action retains a Create-oriented accessible name in 100% of checked submit states.
- **SC-004**: Responsive verification confirms the primary submit action fits without overlap or layout shift in at least one desktop-width and one mobile-width viewport.
- **SC-005**: Submission verification confirms a valid draft still creates a task through the existing explicit submit flow.
- **SC-006**: Verification evidence preserves MM-390 and the original Jira preset brief as the source for the feature.
