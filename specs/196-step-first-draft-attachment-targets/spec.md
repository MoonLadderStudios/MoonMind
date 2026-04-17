# Feature Specification: Step-First Draft and Attachment Targets

**Feature Branch**: `196-step-first-draft-attachment-targets`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-377 as the canonical Moon Spec orchestration input.

Jira issue: MM-377 from MM project
Summary: Step-First Draft and Attachment Targets
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-377 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-377: Step-First Draft and Attachment Targets

Short Name
step-first-draft-attachment-targets

Source Reference
- Source document: `docs/UI/CreatePage.md`
- Source title: Create Page
- Source sections: 6. Draft model, 7.1 Step list, 7.2 Step fields, 7.3 Step attachment contract, 7.4 Objective-scoped attachment target
- Coverage IDs: DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-024, DESIGN-REQ-025

User Story
As a task author, I can build a step-first draft where instructions, skills, and image inputs belong to explicit objective or step targets and stay attached to the correct target through normal editing.

Acceptance Criteria
- Given the draft contains one step, then Step 1 is identified as Primary and the page remains valid when primary instructions or an explicit skill is present.
- Given additional steps exist, then the primary step requires instructions while non-primary steps may omit instructions or inherit the primary skill default.
- Given I add an image to a step, then it appears in that step card and submits through `task.steps[n].inputAttachments`.
- Given I add an objective-scoped image, then it belongs to the preset objective target and submits through `task.inputAttachments`.
- Given I reorder steps, then step attachments move with their owning steps and do not attach to another step implicitly.
- Given an attachment control is available, then open, upload, remove, retry, and target actions are keyboard accessible and labeled for assistive technology.

Requirements
- Represent attachments as structured `DraftAttachment` records with objective or step targets.
- Distinguish selected, uploading, uploaded, failed, local-file, and artifact-backed attachment states.
- Keep attachments out of instruction text and detached from filename or ordering conventions.
- Render step attachments in the same card as the step instructions they inform.
- Support add, remove, and reorder for steps without creating dependency edges.
- Allow objective-scoped attachments only as task-level objective inputs, not automatic step copies.
- Cover target isolation and reorder preservation in tests.

Relevant Implementation Notes
- The Create page draft model should be step-first: task instructions, selected skills, and attachments are represented as draft state before submission.
- Step 1 is the primary step and remains valid with primary instructions or an explicit skill.
- Non-primary steps may omit instructions when they inherit the primary skill default.
- Step-scoped images submit through `task.steps[n].inputAttachments`.
- Objective-scoped images submit through `task.inputAttachments`.
- Attachment ownership must survive normal edit operations, especially step reorder.
- Attachment controls must remain keyboard accessible and assistive-technology labeled.

Out of Scope
- Creating dependency edges when steps are added, removed, or reordered.
- Copying objective-scoped attachments automatically into individual steps.
- Encoding attachments inside instruction text.
- Inferring attachment target identity from filenames or current ordering.

Verification
- Verify one-step drafts identify Step 1 as Primary and remain valid with primary instructions or an explicit skill.
- Verify additional steps enforce primary-step instructions while allowing non-primary instruction omission or inherited primary skill defaults.
- Verify step-scoped images render in their owning step card and submit through `task.steps[n].inputAttachments`.
- Verify objective-scoped images submit through `task.inputAttachments`.
- Verify step reorder preserves attachment ownership.
- Verify attachment controls for open, upload, remove, retry, and target actions are keyboard accessible and labeled for assistive technology.
- Run focused Create page unit tests and `./tools/test_unit.sh` before completion when implementation changes are made.

Needs Clarification
- None"

## User Story - Step-First Draft and Attachment Targets

**Summary**: As a task author, I want instructions, skills, and image inputs to stay attached to explicit objective or step targets so submitted tasks preserve the intent of my draft through normal editing.

**Goal**: A task author can compose a task from a primary step, optional follow-up steps, and explicit image attachment targets without losing target ownership when validating, importing, adding, removing, or reordering steps.

**Independent Test**: Can be fully tested by authoring a Create page draft with objective-scoped and step-scoped image inputs, adding and reordering steps, submitting the draft, and verifying the submitted task payload preserves `task.inputAttachments` for objective images and `task.steps[n].inputAttachments` for each owning step without embedding attachments in instruction text.

**Acceptance Scenarios**:

1. **Given** a draft contains one step, **When** the author provides primary instructions or an explicit skill, **Then** Step 1 is identified as Primary and the page remains valid.
2. **Given** a draft contains additional steps, **When** the author leaves the primary step instructions blank, **Then** submission is blocked; **When** non-primary steps omit instructions or skills, **Then** they can inherit the primary objective or skill defaults.
3. **Given** the author adds an image to a step, **When** the task is submitted, **Then** the image appears with that step in the draft and is submitted through that step's `inputAttachments`.
4. **Given** the author adds an objective-scoped image, **When** the task is submitted, **Then** the image is submitted through task-level `inputAttachments` and is not copied into step attachments.
5. **Given** step attachments exist, **When** the author reorders steps, **Then** each attachment stays with its owning step and is not reassigned by visual order, filename, or instruction text.
6. **Given** attachment controls are available, **When** the author uses open, upload, remove, retry, or target actions, **Then** those actions are keyboard accessible and labeled for assistive technology.

### Edge Cases

- Attachment policy may be disabled; manual authoring and step validation still work without rendering attachment controls.
- An uploaded image may fail validation or completion; the draft reports a target-specific failure without submitting a partial task.
- Reordering a step with attachments must preserve ownership even when another step has identical instructions or filenames.
- Removing a step removes only that step's local selected attachments and does not remove objective-scoped attachments or other step attachments.
- Imported Jira images must land on the selected target rather than an implicit primary-step fallback.

## Assumptions

- Objective-scoped attachments belong to the preset objective field when task presets are enabled; when presets are unavailable, the objective attachment target can remain hidden.
- The current Create page is the runtime implementation surface for this story.

## Source Design Requirements

- **DESIGN-REQ-005** (`docs/UI/CreatePage.md`, section 6): The browser draft is step-first and target-aware, with attachments represented as structured state rather than instruction text. Scope: in scope. Mapped to FR-001, FR-003, FR-004.
- **DESIGN-REQ-006** (`docs/UI/CreatePage.md`, section 7.1): The first step is always Step 1 (Primary), steps can be added, removed, and reordered, and attachments move with their owning step. Scope: in scope. Mapped to FR-002, FR-006.
- **DESIGN-REQ-007** (`docs/UI/CreatePage.md`, section 7.2): Step fields include instructions, optional attachment input, optional skill, optional skill args, and optional advanced required capabilities. Scope: in scope. Mapped to FR-002, FR-003.
- **DESIGN-REQ-008** (`docs/UI/CreatePage.md`, section 7.3): Step attachments are submitted through `task.steps[n].inputAttachments`, support removal before submit, and must not attach to another step implicitly. Scope: in scope. Mapped to FR-003, FR-005, FR-006.
- **DESIGN-REQ-009** (`docs/UI/CreatePage.md`, section 7.4): Objective-scoped attachments are submitted through `task.inputAttachments`, belong to the preset objective target, and are not copied into step attachments automatically. Scope: in scope. Mapped to FR-004, FR-005.
- **DESIGN-REQ-024** (`docs/UI/CreatePage.md`, section 6 and 7.3): Attachment state distinguishes selected, uploading/uploaded, failed, local-file, and artifact-backed states sufficiently for reliable authoring and retry behavior. Scope: in scope. Mapped to FR-001, FR-007.
- **DESIGN-REQ-025** (`docs/UI/CreatePage.md`, section 7.3): Attachment controls expose filename/type/size/status and are accessible for keyboard and assistive technology use. Scope: in scope. Mapped to FR-007, FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST represent task image attachments as structured draft records with explicit objective or step targets and local/upload state.
- **FR-002**: The Create page MUST identify the first step as Step 1 (Primary), require primary instructions or an explicit skill for single-step submission, and require primary instructions when additional steps exist.
- **FR-003**: The Create page MUST render step-scoped attachment controls inside the owning step card and submit uploaded step images through the corresponding `task.steps[n].inputAttachments`.
- **FR-004**: The Create page MUST render objective-scoped attachment controls with the preset objective target when available and submit uploaded objective images through `task.inputAttachments`.
- **FR-005**: The Create page MUST keep image attachments out of instruction text and must not infer target ownership from filenames, rendered order, or instruction content.
- **FR-006**: The Create page MUST preserve step attachment ownership when steps are added, removed, or reordered.
- **FR-007**: The Create page MUST distinguish selected, uploading, uploaded, failed, local-file, and artifact-backed attachment states sufficiently to validate, retry, remove, and submit the correct targets.
- **FR-008**: Attachment controls for open, upload, remove, retry, and target identification MUST be keyboard accessible and labeled for assistive technology.
- **FR-009**: The spec artifacts and verification evidence MUST preserve Jira issue key MM-377 and the canonical Jira preset brief.

### Key Entities

- **DraftAttachment**: A selected or uploaded image input with a local identity, explicit target, source type, upload status, filename, content type, size, optional artifact ID, optional preview URL, and optional error message.
- **AttachmentTarget**: The objective target or a step target identified by stable step-local identity.
- **StepDraft**: A draft step with stable local identity, instructions, optional skill configuration, template identity, and owned step attachments.
- **TaskDraft**: The whole Create page draft, including preset objective text, objective attachments, ordered steps, applied templates, execution context, dependencies, and submit controls.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Submitting a draft with an objective image produces task-level `inputAttachments` and no duplicate step attachment for that image.
- **SC-002**: Submitting a draft with step images produces `task.steps[n].inputAttachments` only for the owning steps.
- **SC-003**: Reordering steps before submit preserves attachment ownership for 100% of reordered step attachments in tests.
- **SC-004**: Create page validation blocks missing primary instructions when additional steps are present while allowing non-primary steps to omit instructions or skills.
- **SC-005**: Automated tests cover target isolation, reorder preservation, no instruction-text embedding, and MM-377 traceability.
