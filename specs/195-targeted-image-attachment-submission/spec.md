# Feature Specification: Targeted Image Attachment Submission

**Feature Branch**: `195-targeted-image-attachment-submission`
**Created**: 2026-04-17
**Status**: Draft
**Input**:

```text
# MM-367 MoonSpec Orchestration Input

## Source

- Jira issue: MM-367
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Create targeted image attachment submission
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-367 from MM project
Summary: Create targeted image attachment submission
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-367 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-367: Create targeted image attachment submission

Short Name
targeted-image-attachment-submission

Source Reference
- Source document: `docs/Tasks/ImageSystem.md`
- Source title: Task Image Input System
- Source sections: 1. Purpose, 3. Product stance and terminology, 4. End-to-end desired-state flow, 5. Control-plane contract, 15. Non-goals
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-020

User Story
As a task author, I need the Create page and task-shaped execution submission to bind images to the objective or a specific step using structured `inputAttachments` refs so MoonMind.Run receives explicit lightweight references instead of raw image data.

Acceptance Criteria
- Create flow supports image refs on the task objective and on individual steps.
- Submitted payloads use `task.inputAttachments` and `task.steps[n].inputAttachments` as the only canonical target fields.
- Attachment identity and target meaning are not inferred from filenames.
- The workflow input carries artifact refs and compact metadata, not embedded image bytes or image data URLs.
- Legacy queue-specific attachment routes are not treated as the desired-state submission contract.

Requirements
- Use `inputAttachments` as the canonical control-plane field name.
- Preserve objective-scoped and step-scoped target meaning from the containing field.
- Normalize `TaskInputAttachmentRef` objects before workflow start.
- Keep all browser upload and download flows behind MoonMind-owned API endpoints.
- Represent explicit image-system non-goals in validation or documentation for this contract surface.

Relevant Implementation Notes
- The canonical submit path is task-shaped execution submission through `/api/executions`.
- Objective-scoped attachments are submitted through `task.inputAttachments`.
- Step-scoped attachments are submitted through `task.steps[n].inputAttachments`.
- The execution API must preserve target scoping through create, edit, and rerun.
- The original task input snapshot remains the source of truth for reconstructing attachment bindings.
- Workflow input should carry artifact refs and compact metadata only; uploaded image bytes and data URLs must stay out of workflow payloads and Temporal histories.
- Runtime adapters should consume structured refs or derived context for the target they are executing, not browser-local state or filename conventions.

Non-Goals
- Embedding raw image bytes in execution create payloads.
- Embedding images into instruction markdown as data URLs.
- Implicit attachment sharing across steps.
- Live Jira sync.
- Generic non-image attachment types by default.
- Provider-specific multimodal message formats as the control-plane contract.

Validation
- Verify objective-scoped image refs are accepted and preserved as `task.inputAttachments`.
- Verify step-scoped image refs are accepted and preserved as `task.steps[n].inputAttachments`.
- Verify submitted payloads and workflow input contain artifact refs and compact metadata, not image bytes or data URLs.
- Verify target binding survives task create, edit, and rerun flows without relying on filenames.
- Verify legacy queue-specific attachment routes are not documented or used as the desired-state submission contract.

Needs Clarification
- None
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Submit Targeted Image Attachments

**Summary**: As a task author, I want the Create page and task-shaped execution submission to bind image attachment refs to either the task objective or a specific step so that MoonMind.Run receives explicit lightweight references with durable target meaning.

**Goal**: Task submissions preserve objective-scoped and step-scoped image attachment refs through the control-plane contract without embedding image bytes, relying on filenames, or using legacy queue attachment routes.

**Independent Test**: Submit task-shaped execution payloads that include objective-level and step-level image attachment refs. The story passes when the execution request accepts both scopes, normalizes each attachment into the workflow input using only `task.inputAttachments` and `task.steps[n].inputAttachments`, preserves target meaning from the containing field, and rejects or excludes raw image bytes, data URLs, filename-derived targeting, and legacy queue-specific attachment routes as canonical submission behavior.

**Acceptance Scenarios**:

1. **Given** a task author attaches an image to the task objective, **when** the Create page submits the task-shaped execution request, **then** the payload carries the ref under `task.inputAttachments` and MoonMind.Run receives an objective-scoped lightweight attachment ref.
2. **Given** a task author attaches an image to a specific task step, **when** the Create page submits the task-shaped execution request, **then** the payload carries the ref under that step's `inputAttachments` field and MoonMind.Run receives a step-scoped lightweight attachment ref.
3. **Given** an image ref is submitted for either scope, **when** the control plane normalizes the task payload before workflow start, **then** target meaning comes from the containing field and is not inferred from the filename.
4. **Given** a task submission contains image attachment information, **when** the workflow input is built, **then** it contains artifact refs and compact metadata only and does not embed image bytes or image data URLs.
5. **Given** legacy queue-specific attachment fields or routes are available elsewhere in the system, **when** task-shaped execution submission is used, **then** they are not treated as the desired-state contract for image attachment submission.
6. **Given** a task input snapshot is stored for later edit or rerun, **when** the task is reconstructed, **then** objective and step attachment bindings are preserved from the snapshot rather than inferred from artifact links alone.

### Edge Cases

- The same image filename is used for objective-scoped and step-scoped attachments.
- A step attachment is submitted for a step that has an explicit id and ordinal.
- A submitted attachment ref is missing required compact metadata such as artifact id, filename, content type, or size.
- A task submission attempts to include embedded image bytes or a data URL in an attachment ref.
- A task is edited or rerun after the original submission and must preserve attachment targeting.

## Assumptions

- Image bytes have already been uploaded through MoonMind-owned artifact APIs before the task-shaped execution request is accepted.
- This story covers image attachment refs and target preservation in task-shaped submission; artifact storage enforcement and image policy hardening are tracked by linked issue MM-368.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tasks/ImageSystem.md` section 1. Purpose. The system MUST let users attach images to the task objective or to individual steps and submit lightweight references into MoonMind.Run. Scope: in scope. Mapped to FR-001, FR-002, FR-003.
- **DESIGN-REQ-002**: Source `docs/Tasks/ImageSystem.md` section 3. Product stance and terminology. Uploaded image bytes MUST NOT be embedded in Temporal histories or task instruction text; the control plane MUST submit structured attachment references. Scope: in scope. Mapped to FR-004, FR-005.
- **DESIGN-REQ-003**: Source `docs/Tasks/ImageSystem.md` section 3.2 Canonical terminology. The canonical control-plane field name MUST be `inputAttachments`, with objective refs submitted through `task.inputAttachments` and step refs through `task.steps[n].inputAttachments`. Scope: in scope. Mapped to FR-001, FR-002.
- **DESIGN-REQ-004**: Source `docs/Tasks/ImageSystem.md` section 3.2 Canonical terminology. Target meaning MUST come from the field that contains the ref, and attachment identity MUST NOT depend on filename conventions. Scope: in scope. Mapped to FR-003, FR-008.
- **DESIGN-REQ-005**: Source `docs/Tasks/ImageSystem.md` section 4. End-to-end desired-state flow. The execution API MUST validate and persist the authoritative snapshot of attachment targeting before MoonMind.Run starts. Scope: in scope. Mapped to FR-006, FR-007.
- **DESIGN-REQ-006**: Source `docs/Tasks/ImageSystem.md` section 5. Control-plane contract. The canonical submit path is task-shaped execution submission through `/api/executions`; legacy queue-specific attachment submission routes are not the desired-state contract. Scope: in scope. Mapped to FR-001, FR-009.
- **DESIGN-REQ-020**: Source `docs/Tasks/ImageSystem.md` section 15. Non-goals. The story MUST exclude embedded raw image bytes, data URLs in instruction markdown, implicit attachment sharing across steps, live Jira sync, generic non-image attachment types by default, and provider-specific multimodal message formats as the control-plane contract. Scope: in scope as guardrails. Mapped to FR-004, FR-005, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept objective-scoped image attachment refs only through `task.inputAttachments` in task-shaped execution submissions.
- **FR-002**: The system MUST accept step-scoped image attachment refs only through `task.steps[n].inputAttachments` in task-shaped execution submissions.
- **FR-003**: The system MUST preserve attachment target meaning from the containing objective or step field during request normalization and workflow input construction.
- **FR-004**: The system MUST reject or exclude embedded image bytes from task attachment refs and workflow inputs.
- **FR-005**: The system MUST reject or exclude image data URLs from task attachment refs, instruction markdown, and workflow inputs.
- **FR-006**: The system MUST normalize every accepted `TaskInputAttachmentRef` before workflow start into compact metadata containing artifact id, filename, content type, and size.
- **FR-007**: The system MUST persist enough task input snapshot data to reconstruct objective-scoped and step-scoped attachment bindings during edit and rerun flows.
- **FR-008**: The system MUST NOT infer attachment target identity from filenames, artifact names, or other naming conventions.
- **FR-009**: The system MUST NOT treat legacy queue-specific attachment routes or fields as the canonical image submission contract for task-shaped execution.
- **FR-010**: The system MUST keep explicit image-system non-goals visible through validation failures, omitted payload support, or documentation on this contract surface.

### Key Entities

- **TaskInputAttachmentRef**: A lightweight image attachment reference submitted by the control plane with artifact id, filename, content type, and size metadata.
- **Attachment Target Binding**: The durable association between a submitted attachment ref and either the task objective or one specific task step.
- **Task Input Snapshot**: The stored original task-shaped submission data used to reconstruct task text, steps, runtime settings, and target attachment refs during edit and rerun flows.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Objective-scoped and step-scoped image attachment refs can be submitted in one task-shaped request and are represented distinctly in the workflow input.
- **SC-002**: Unit validation covers missing compact metadata, embedded bytes, image data URLs, and filename-collision target handling.
- **SC-003**: Integration coverage proves `/api/executions` preserves objective and step attachment target bindings through workflow-start payload construction.
- **SC-004**: Edit or rerun reconstruction tests prove target bindings are preserved from the task input snapshot.
- **SC-005**: No successful task-shaped image attachment submission depends on a legacy queue-specific attachment field or route.
