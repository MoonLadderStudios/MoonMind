# Feature Specification: Policy-Gated Image Upload and Submit

**Feature Branch**: `199-policy-gated-image-upload`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-380 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-380-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-380 from MM project
Summary: Policy-Gated Image Upload and Submit
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-380 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-380: Policy-Gated Image Upload and Submit

Source Reference:
- Source Document: `docs/UI/CreatePage.md`
- Source Title: Create Page
- Source Sections:
  - 11. Attachment policy and UX contract
  - 14. Submission contract
  - 16. Failure and empty-state rules
  - 18. Testing requirements
- Coverage IDs:
  - DESIGN-REQ-016
  - DESIGN-REQ-021
  - DESIGN-REQ-023
  - DESIGN-REQ-024
  - DESIGN-REQ-025
  - DESIGN-REQ-006

User Story:
As a task author, I can add permitted image inputs, see validation and upload failures at the correct target, and submit only after local images become artifact-backed structured attachment refs.

Acceptance Criteria:
- Given attachment policy is disabled, then all attachment entry points are hidden and the page remains fully usable for manual authoring.
- Given policy allows only image MIME types, then the UI uses an image-specific label such as Images.
- Given count, single-file size, total size, or content type validation fails, then the browser fails fast and visibly at the affected target before upload and again blocks submit if unresolved.
- Given upload fails, then the failure remains local to the affected target and I can remove or retry without losing unrelated draft state.
- Given preview fails, then attachment metadata remains visible, the draft is not corrupted, and removal remains available.
- Given I submit create, edit, or rerun with local images, then images upload to the artifact system first and the execution payload contains structured refs rather than binary content.

Requirements:
- Read attachmentPolicy from server-provided runtime configuration.
- Validate attachment count, per-file bytes, total bytes, and content type before upload and at submit time.
- Represent upload and preview failure states without silently dropping selected images.
- Provide keyboard-accessible remove and retry actions plus concise per-target summaries.
- Upload local images to the artifact system before create, edit, or rerun submission.
- Submit task.inputAttachments and task.steps[n].inputAttachments as structured artifact refs.
- Block submit while attachments are invalid, failed, incomplete, or still uploading.
- Cover policy, validation, failure isolation, upload-before-create, and invalid/incomplete submit blocking in tests.

Relevant Implementation Notes:
- Treat `docs/UI/CreatePage.md` as the source design for attachment policy, submission, failure, empty-state, and testing behavior.
- Preserve the Jira issue key MM-380 anywhere downstream artifacts summarize or verify the work.
- Keep attachment entry points policy-gated; a disabled policy must hide those entry points without blocking manual task authoring.
- Use image-specific copy when the server policy allows only image MIME types.
- Validate count, per-file size, total size, and MIME type before upload and again before submit.
- Keep upload and preview failures scoped to the affected target, with retry or remove actions that do not corrupt unrelated draft state.
- Convert local image selections into artifact-backed structured refs before create, edit, or rerun submission.
- Ensure submitted payloads use `task.inputAttachments` and `task.steps[n].inputAttachments` for structured attachment refs, not binary content.

Verification:
- Confirm the Create page reads server-provided attachment policy and hides attachment entry points when policy is disabled.
- Confirm image-only policy surfaces image-specific labeling.
- Confirm client validation covers attachment count, single-file size, total size, and content type before upload and at submit time.
- Confirm upload failures, preview failures, retry, and remove behavior remain scoped to the affected target.
- Confirm create, edit, and rerun submission upload local images before sending execution payloads.
- Confirm execution payloads contain structured artifact refs under `task.inputAttachments` and `task.steps[n].inputAttachments`.
- Confirm submit is blocked while attachments are invalid, failed, incomplete, or uploading.
- Confirm tests cover policy, validation, failure isolation, upload-before-create, and invalid or incomplete submit blocking.

Out of Scope:
- Embedding binary image content in task execution payloads.
- Inferring attachment validity from filenames instead of policy, size, MIME type, and upload state.
- Allowing unresolved invalid, failed, incomplete, or uploading attachments through create, edit, or rerun submission.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Policy-Gated Image Upload and Submit

**Summary**: As a task author, I can add permitted image inputs, see validation and upload failures at the correct target, and submit only after local images become artifact-backed structured attachment refs.

**Goal**: Task authors can safely include policy-permitted images in Create page drafts while the page prevents invalid, failed, incomplete, or still-uploading attachments from reaching create, edit, or rerun submission.

**Independent Test**: Can be fully tested by loading the Create page with enabled and disabled attachment policies, adding objective-scoped and step-scoped image inputs, exercising validation, upload, preview failure, retry, remove, and submit flows, and verifying submitted payloads contain only artifact-backed structured refs in the canonical attachment fields.

**Acceptance Scenarios**:

1. **Given** attachment policy is disabled, **When** the Create page loads, **Then** all attachment entry points are hidden and manual authoring remains usable.
2. **Given** attachment policy allows only image MIME types, **When** attachment entry points are visible, **Then** the page uses image-specific labeling such as `Images`.
3. **Given** selected images exceed allowed count, per-file size, total size, or content type policy, **When** the task author selects or submits them, **Then** the page reports the specific target error and blocks submission until the invalid selection is resolved.
4. **Given** an image upload fails for one target, **When** the task author continues editing the draft, **Then** the failure stays scoped to that target and retry or remove actions do not discard unrelated draft state.
5. **Given** an attachment preview fails, **When** the draft remains open, **Then** attachment metadata and remove actions remain available and the draft is not corrupted.
6. **Given** create, edit, or rerun submission includes local images, **When** the task author submits, **Then** the page uploads local images first and sends only structured artifact refs through `task.inputAttachments` and `task.steps[n].inputAttachments`.
7. **Given** any attachment is invalid, failed, incomplete, or still uploading, **When** the task author attempts create, edit, or rerun submission, **Then** submission is blocked with a visible target-specific explanation.

### Edge Cases

- Attachment policy is missing or disabled in runtime configuration.
- A selected file has an image extension but an unsupported or missing MIME type.
- Files are individually valid but exceed total byte or count limits together.
- An upload succeeds for one target and fails for another target.
- Preview generation fails for an otherwise uploaded attachment.
- A user removes or retries a failed attachment after editing unrelated fields.
- A submit attempt occurs while one or more uploads are still in progress.
- Existing edit or rerun attachments are present alongside newly selected local images.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/UI/CreatePage.md` is treated as source requirements for runtime behavior.
- The existing artifact system remains the upload destination for local image selections.
- Attachment policy is provided through server runtime configuration.
- The canonical attachment fields remain `task.inputAttachments` for objective-scoped refs and `task.steps[n].inputAttachments` for step-scoped refs.

## Source Design Requirements

- **DESIGN-REQ-016** (Source: `docs/UI/CreatePage.md`, section 11; MM-380 brief): Attachment behavior MUST be policy-gated, hide all attachment entry points when disabled, use image-specific labels when only image MIME types are allowed, validate before upload and at submit time, fail visibly for count, size, total-size, type, and upload failures, preserve selected images instead of silently dropping them, support remove and retry, and keep preview failure non-corrupting. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007.
- **DESIGN-REQ-021** (Source: `docs/UI/CreatePage.md`, section 14; MM-380 brief): Create page submission MUST upload local images to the artifact system before create, edit, or rerun, submit structured refs instead of raw binaries, require upload completion before execution eligibility, keep submit explicit, and preserve target meaning through `task.inputAttachments` and `task.steps[n].inputAttachments`. Scope: in scope. Maps to FR-008, FR-009, FR-010, FR-011, FR-012.
- **DESIGN-REQ-023** (Source: `docs/UI/CreatePage.md`, section 16; MM-380 brief): Failure and empty states MUST keep attachment-disabled drafts usable, scope upload failures to the affected target, preserve metadata and remove actions on preview failure, and prevent create, edit, or rerun from proceeding with silently discarded attachments. Scope: in scope. Maps to FR-001, FR-006, FR-007, FR-012, FR-013.
- **DESIGN-REQ-024** (Source: `docs/UI/CreatePage.md`, section 18; MM-380 brief): Create page tests SHOULD cover hidden entry points, validation, upload-before-create, preview and upload failure isolation, invalid or incomplete submit blocking, and attachment target preservation. Scope: in scope. Maps to FR-014.
- **DESIGN-REQ-025** (Source: `docs/UI/CreatePage.md`, MM-380 brief): Attachment interactions MUST remain target-specific and must not infer target binding from filenames or silently move attachments between objective and step targets. Scope: in scope. Maps to FR-010, FR-011, FR-013.
- **DESIGN-REQ-006** (Source: `docs/UI/CreatePage.md`, MM-380 brief): The Create page MUST preserve manual authoring and task-first submission semantics while adding image inputs as structured attachments. Scope: in scope. Maps to FR-001, FR-008, FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST read attachment policy from server-provided runtime configuration before showing attachment entry points.
- **FR-002**: System MUST hide all attachment entry points when attachment policy is disabled while preserving manual task authoring.
- **FR-003**: System MUST use image-specific labeling, such as `Images`, when the active policy allows only image MIME types.
- **FR-004**: System MUST validate selected attachments against count, per-file byte, total byte, and content type rules before upload.
- **FR-005**: System MUST repeat attachment validation at submit time before create, edit, or rerun submission proceeds.
- **FR-006**: System MUST show visible, target-specific errors for count, size, total-size, content type, upload, and incomplete attachment failures.
- **FR-007**: System MUST preserve selected attachment state and unrelated draft state when upload or preview failure occurs.
- **FR-008**: Users MUST be able to remove failed, invalid, or preview-failed attachments from the affected target.
- **FR-009**: Users MUST be able to retry failed uploads from the affected target without rebuilding unrelated draft content.
- **FR-010**: System MUST upload local images to the artifact system before create, edit, or rerun submission sends the execution payload.
- **FR-011**: System MUST submit objective-scoped uploaded image refs only through `task.inputAttachments`.
- **FR-012**: System MUST submit step-scoped uploaded image refs only through the owning `task.steps[n].inputAttachments`.
- **FR-013**: System MUST block create, edit, or rerun submission while any attachment is invalid, failed, incomplete, or still uploading.
- **FR-014**: Automated coverage MUST prove policy gating, validation, failure isolation, upload-before-submit, canonical payload fields, and invalid or incomplete submit blocking.
- **FR-015**: System MUST preserve Jira issue key MM-380 in MoonSpec artifacts and verification evidence for traceability.

### Key Entities

- **Attachment Policy**: Server-provided runtime rules for whether attachments are enabled, allowed content types, maximum count, maximum per-file bytes, and maximum total bytes.
- **Draft Attachment**: A local or persisted attachment associated with one explicit Create page target and carrying filename, content type, size, validation, upload, preview, and error state.
- **Objective Attachment Ref**: An artifact-backed structured ref submitted through `task.inputAttachments`.
- **Step Attachment Ref**: An artifact-backed structured ref submitted through the owning `task.steps[n].inputAttachments`.
- **Attachment Target**: The objective or a specific step that owns attachment state, validation messages, upload state, and submitted refs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With disabled policy, automated coverage verifies attachment entry points are hidden and manual authoring can still submit a task without attachments.
- **SC-002**: With image-only policy, automated coverage verifies visible attachment controls use image-specific labeling.
- **SC-003**: Automated coverage verifies count, per-file size, total size, and content type validation before upload and before submit.
- **SC-004**: Automated coverage verifies upload failure and preview failure remain scoped to the affected target and do not erase unrelated draft fields.
- **SC-005**: Automated coverage verifies create, edit, and rerun submission upload local images before sending payloads and submit structured refs under `task.inputAttachments` and `task.steps[n].inputAttachments`.
- **SC-006**: Automated coverage verifies submission is blocked while attachments are invalid, failed, incomplete, or still uploading.
