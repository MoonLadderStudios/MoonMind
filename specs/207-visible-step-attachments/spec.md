# Feature Specification: Visible Step Attachments

**Feature Branch**: `207-visible-step-attachments`
**Created**: 2026-04-18
**Status**: Draft
**Input**:

```text
Use the Jira preset brief for MM-410 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-410 MoonSpec Orchestration Input

## Source

- Jira issue: MM-410
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Add visible step attachment + button on Create page
- Labels: attachments, create-page, images, ui
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-410 from MM project
Summary: Add visible step attachment + button on Create page
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-410 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-410: Add visible step attachment + button on Create page

User Story
As a task author, I can click a visible + button on each Create-page step to attach an image or other policy-permitted file, see the selected attachment clearly under that step, and submit only after MoonMind has uploaded the file as a structured artifact-backed input for that exact step.

Context
MoonMind already has substantial attachment infrastructure, but the Create page still does not present a clear, polished UI affordance for adding attachments to a step. Current behavior is mostly implemented behind policy-gated file input controls. This work makes the feature fully usable and visually correct as a first-class step authoring interaction.

Requirements include policy-gated visibility, a per-step + button affordance, target-specific attachment ownership, append semantics, attachment type support, stable visual design, artifact-first upload/submit behavior, edit/rerun compatibility, and preservation of MM-410 in downstream artifacts.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Add Visible Step Attachments

**Summary**: As a task author, I want a compact + attachment button on every Create-page step so I can add policy-permitted files directly to the step they inform without using a generic browser file input or losing target ownership.

**Goal**: Step authors can append, review, validate, remove, retry, and submit step-scoped attachments through a clearly targeted, accessible Create-page control that preserves existing artifact-backed submission contracts.

**Independent Test**: Enable attachment policy, add files through the + button on one or more steps, reorder steps, remove and retry individual files, and submit the task. The story passes when files remain associated with their owning step, invalid or failed files block submission only for the affected target, and successful submission uploads files before execution create with refs under the owning `task.steps[n].inputAttachments`.

**Acceptance Scenarios**:

1. **Given** attachment policy is disabled, **when** the author views objective or step targets, **then** no + attachment buttons are visible and text-only task authoring remains usable.
2. **Given** image-only attachment policy is enabled, **when** the author views each step, **then** each step exposes a compact + button with image-oriented accessible copy and file accept filtering for the configured image MIME types.
3. **Given** mixed allowed content types are enabled, **when** the author views each step, **then** each step uses generic attachment copy and supports non-image files without showing a broken image preview.
4. **Given** the author clicks the + button for Step 1 and selects a file, **when** the selection completes, **then** the file appears only under Step 1 with filename, type, size, preview when supported, and a remove action.
5. **Given** Step 1 already has a selected file, **when** the author clicks + again and selects another file, **then** the new file is appended without replacing the existing file.
6. **Given** the same filename is selected on different steps, **when** files are validated, removed, retried, or submitted, **then** each action affects only the owning step target.
7. **Given** selected step attachments exist, **when** steps are reordered, **then** each selected attachment remains associated with the same logical step.
8. **Given** a selected file violates allowed type, per-file size, total size, or count policy, **when** validation runs, **then** the error is visible for the affected target and upload is not attempted for that invalid file.
9. **Given** upload fails or preview fails for one step attachment, **when** the page renders the target, **then** metadata remains visible, retry or remove remains available where applicable, unrelated draft state is preserved, and task submission is blocked until the failure is resolved.
10. **Given** task submission succeeds with step attachments, **when** the execution payload is created, **then** artifact upload happens before execution create and structured refs are included only under each owning step's `inputAttachments`.
11. **Given** edit or rerun includes persisted attachments and newly selected files, **when** the author changes one target, **then** persisted and new files are validated together and removals are preserved for the changed target without silently dropping other attachments.

### Edge Cases

- Attachment policy can be disabled by runtime configuration.
- Policy may allow only image MIME types or mixed image and non-image content.
- Duplicate local files can be selected more than once for the same target.
- Different steps can contain files with identical names, sizes, and content types.
- Image preview can fail even when the file itself remains valid.
- Upload can fail for one attachment while other targets remain unchanged.
- Persisted artifact-backed attachments can coexist with newly selected local files during edit or rerun.

## Assumptions

- Runtime mode is selected; this story changes Create-page behavior and tests rather than only documentation.
- The default for `AGENT_JOB_ATTACHMENT_ENABLED` remains unchanged unless implementation proves that changing it is necessary for this story.
- Drag-and-drop and paste support are out of scope unless they can reuse the same explicit target ownership and validation behavior without expanding the story.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/UI/CreatePage.md`, section 7.1): Each step region owns its own instructions and attachment state, and reordering a step moves its attachments with it. Scope: in scope. Mapped to FR-003, FR-005.
- **DESIGN-REQ-002** (`docs/UI/CreatePage.md`, section 7.2): Step fields include `Images` or `Input Attachments` when attachment policy is enabled, and step attachments are visible in the same card as the step instructions they inform. Scope: in scope. Mapped to FR-001, FR-002, FR-006.
- **DESIGN-REQ-003** (`docs/UI/CreatePage.md`, section 7.3): Step attachments are submitted through `task.steps[n].inputAttachments`, display filename, type, size, preview, upload/error state, support removal, and must not move implicitly to another step. Scope: in scope. Mapped to FR-003, FR-004, FR-005, FR-007, FR-010.
- **DESIGN-REQ-004** (`docs/UI/CreatePage.md`, section 7.4): Objective-scoped attachments are submitted through `task.inputAttachments` and are not copied down into step attachments automatically. Scope: in scope as a guardrail. Mapped to FR-010.
- **DESIGN-REQ-005** (`docs/UI/CreatePage.md`, section 11): Attachment behavior is policy-gated, hidden when disabled, labeled based on allowed content types, validated before upload and submit, and failures must be visible and recoverable. Scope: in scope. Mapped to FR-001, FR-002, FR-004, FR-007, FR-008.
- **DESIGN-REQ-006** (`docs/UI/CreatePage.md`, section 14): Create-page submission is artifact-first, sends structured refs instead of binary data, and defines attachment meaning by target rather than filename conventions. Scope: in scope. Mapped to FR-009, FR-010.
- **DESIGN-REQ-007** (`docs/UI/CreatePage.md`, section 16): Failure and empty-state rules require disabled policy hiding, local upload failures, preview failure resilience, edit/rerun reconstruction safety, and no create/edit/rerun proceeding with silently discarded attachments. Scope: in scope. Mapped to FR-001, FR-007, FR-008, FR-011.
- **DESIGN-REQ-008** (`docs/UI/CreatePage.md`, section 17): Open, target, upload, remove, and retry actions must be keyboard accessible, step regions must identify attachments, and validation errors must be associated with the failed target. Scope: in scope. Mapped to FR-002, FR-006, FR-007.
- **DESIGN-REQ-009** (`docs/UI/CreatePage.md`, section 18): Create-page tests should cover hidden entry points, validation, target isolation, reorder preservation, upload-before-submit, edit/rerun reconstruction, preview/upload failure, and invalid/incomplete submit blocking. Scope: in scope. Mapped to FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST hide all attachment entry points when runtime attachment policy is disabled while keeping text-only task authoring usable.
- **FR-002**: The Create page MUST expose one compact, keyboard-accessible + attachment button for each step when runtime attachment policy is enabled.
- **FR-003**: The + button for a step MUST open a file picker scoped to that step target and preserve accept filtering from the runtime attachment policy.
- **FR-004**: Selecting files through a step + button MUST append valid new selections to that step's existing selected files rather than replacing the target file list.
- **FR-005**: The Create page MUST keep every selected or persisted step attachment bound to the owning step's stable logical identity through add, remove, retry, validation, edit, rerun, and step reorder operations.
- **FR-006**: Step attachment summaries MUST render under the owning step with filename, content type, size, remove action, supported preview, and upload or error state when relevant.
- **FR-007**: Attachment validation and failure messages MUST remain target-specific for count, per-file size, total size, unsupported content type, preview failure, upload failure, and incomplete upload states.
- **FR-008**: Users MUST be able to remove failed or invalid step attachments without losing unrelated objective, step, repository, runtime, preset, or Jira draft state.
- **FR-009**: Create, edit, and rerun submission MUST upload local step attachments to the artifact system before execution submission and block submission while any attachment is invalid, failed, incomplete, or uploading.
- **FR-010**: Successful submission MUST send step attachment refs only through the owning `task.steps[n].inputAttachments` field and MUST NOT embed raw binary data, base64 content, data URLs, or generated attachment markdown in instructions.
- **FR-011**: Edit and rerun reconstruction MUST display persisted artifact-backed attachments under their owning target, validate persisted and new files together, and preserve explicit removals.
- **FR-012**: Automated coverage MUST preserve MM-410 traceability and validate policy-gated visibility, accessible + buttons, append semantics, target isolation, duplicate handling, reorder preservation, validation failures, upload failure, preview failure, upload-before-submit, and structured payload submission.

### Key Entities

- **Step Attachment Button**: A compact per-step control that opens a file picker for the owning step and exposes target-specific accessible copy.
- **Draft Attachment**: A selected local file or persisted artifact-backed ref with target identity, file metadata, preview status, validation state, upload state, and removal/retry affordances.
- **Attachment Target**: The objective target or one stable step-local target that determines where attachment refs are submitted.
- **Attachment Policy**: Runtime configuration that determines whether attachment controls are visible and which file count, size, total size, and content types are allowed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With attachment policy disabled, 100% of Create-page attachment entry points are hidden and text-only submission remains available in automated tests.
- **SC-002**: With image-only policy enabled, every rendered step exposes exactly one accessible + attachment control with image-oriented copy and configured MIME accept filtering.
- **SC-003**: In automated tests, adding files to the same step through repeated + selections appends files without replacing prior selections in 100% of covered cases.
- **SC-004**: In automated tests, selected step attachments preserve target ownership across step reorder and same-filename cases in 100% of covered cases.
- **SC-005**: Invalid, failed, incomplete, or uploading attachments block submission until resolved, while remove or retry actions preserve unrelated draft state.
- **SC-006**: Successful submission with step attachments uploads artifacts before execution create and includes no step attachment refs outside the owning step's `inputAttachments`.
- **SC-007**: Edit/rerun tests prove persisted and newly selected attachments are displayed, validated, and removed without silently dropping unrelated targets.
