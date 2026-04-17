# Feature Specification: Preview and Download Task Images by Target

**Feature Branch**: `201-preview-download-task-images`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description: "Jira issue: MM-373 from MM project
Summary: Preview and download task images by target
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-373 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-373: Preview and download task images by target

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 11. UI preview and detail contract
  - 13. Edit and rerun durability contract
- Coverage IDs:
  - DESIGN-REQ-015
  - DESIGN-REQ-018

User Story
As a task reviewer, I need task detail, edit, and rerun surfaces to preview and download image inputs by their persisted target through MoonMind-owned APIs without losing metadata when previews fail.

Acceptance Criteria
- Previews and downloads use MoonMind-owned API endpoints.
- Preview and detail surfaces organize attachments by objective and step target.
- The UI never infers target binding from filenames.
- A preview failure does not remove metadata visibility or download actions.
- Edit and rerun surfaces distinguish persisted attachments from new local files not yet uploaded.
- Unchanged persisted refs remain available unless explicitly removed.

Requirements
- Use authoritative snapshot target bindings for UI reconstruction.
- Keep download actions available through execution-owned APIs.
- Expose enough target-aware metadata for reviewers to understand attachment scope.
- Organize preview and download controls by explicit objective-scoped and step-scoped targets.
- Use MoonMind-owned API endpoints for all browser-visible image preview and download flows.
- Preserve metadata and download access when an image preview cannot render.
- Distinguish persisted attachment refs from newly selected local files on edit and rerun surfaces.
- Preserve unchanged persisted attachment refs until the reviewer explicitly removes them.
- Do not infer target binding from filenames, artifact links, attachment ordering, or UI-only heuristics.

Relevant Implementation Notes
- The authoritative task input snapshot is the source of truth for target-aware UI reconstruction.
- Task detail, edit, and rerun surfaces should group image inputs by explicit target: objective-scoped or step-scoped.
- Preview and download actions should use execution-owned MoonMind API endpoints rather than raw object-store, Jira, or provider URLs.
- Preview failure must leave metadata visible, including attachment name or label, target scope, and download action.
- Edit and rerun UI should render persisted attachments separately from local files that have not yet been uploaded.
- Unchanged persisted refs should remain part of the draft unless the reviewer explicitly removes them.
- This story depends on the target-aware attachment contract and should not introduce filename-based binding recovery.

Suggested Implementation Areas
- Task detail image attachment grouping and preview/download rendering.
- Edit draft persisted attachment display and download controls.
- Rerun draft persisted attachment display and download controls.
- API payload normalization that exposes target-aware attachment metadata without leaking storage-provider URLs.
- Frontend tests covering preview failure, target grouping, persisted-vs-local attachment display, and download endpoint selection.
- Backend or route tests verifying browser-visible download URLs remain MoonMind-owned and execution-scoped.

Validation
- Verify task detail presents objective-scoped and step-scoped persisted image attachments grouped by target.
- Verify edit and rerun drafts distinguish persisted attachments from new local files not yet uploaded.
- Verify preview and download controls use MoonMind-owned API endpoints.
- Verify preview failure keeps metadata and download actions visible.
- Verify target grouping comes from authoritative snapshot bindings rather than filenames.
- Verify unchanged persisted refs remain available unless explicitly removed.

Non-Goals
- Inferring attachment target bindings from filenames, artifact links, attachment ordering, or generated preview metadata.
- Direct browser access to object storage, Jira attachment URLs, or provider-specific file endpoints.
- Changing artifact retention semantics.
- Adding generic non-image attachment support beyond this story's image preview and download contract.
- Reconstructing missing target bindings through hidden compatibility transforms.

Needs Clarification
- None"

## User Story - Target-Aware Image Review

**Summary**: As a task reviewer, I want task detail, edit, and rerun surfaces to show task image inputs by their persisted objective or step target so I can preview, download, and audit attachments without losing metadata when previews fail.

**Goal**: Reviewers can understand which task target each persisted image belongs to, download it through MoonMind-owned routes, and keep unchanged refs intact during edit or rerun flows.

**Independent Test**: Can be tested by loading a task with objective-scoped and step-scoped image inputs, confirming the detail and edit/rerun surfaces group them by persisted targets, forcing an image preview failure, and verifying metadata plus MoonMind-owned download actions remain available.

**Acceptance Scenarios**:

1. **Given** a task has persisted objective-scoped and step-scoped image inputs, **When** a reviewer opens task detail, **Then** images are grouped by objective and step targets from persisted metadata and each download action uses a MoonMind-owned API endpoint.
2. **Given** a persisted image preview fails to render, **When** the detail, edit, or rerun surface handles the error, **Then** attachment metadata and the download action remain visible.
3. **Given** a reviewer edits or reruns a task with unchanged persisted image refs, **When** the draft is submitted without removing those refs, **Then** unchanged persisted refs remain available and are not replaced by filename-derived bindings.
4. **Given** an edit or rerun draft includes both persisted refs and newly selected local files, **When** the surface renders attachments, **Then** persisted refs and new local files are distinguishable.

### Edge Cases

- Preview rendering fails because the browser cannot decode the image.
- Artifact metadata includes an image but lacks authoritative target binding.
- An artifact provides an external or storage-backed download URL.
- A reviewer removes one persisted attachment while leaving other persisted refs unchanged.

## Assumptions

- Existing edit and rerun draft reconstruction remains the authoritative source for preserving unchanged refs.
- Task detail can use artifact metadata produced by the existing dashboard attachment upload path to identify target scope.
- Artifacts without authoritative target metadata remain visible in the generic artifact table but are not target-grouped as task image inputs.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
|----|--------|-------------|-------|---------------------|
| DESIGN-REQ-015 | `docs/Tasks/ImageSystem.md` section 11 | Task detail, edit, and rerun surfaces may preview and download image inputs through MoonMind-owned APIs, organized by objective or step target, without filename-based binding inference, and preview failure must preserve metadata and download actions. | In scope | FR-001, FR-002, FR-003, FR-004, FR-005, FR-006 |
| DESIGN-REQ-018 | `docs/Tasks/ImageSystem.md` section 13 | Edit and rerun must use the same authoritative attachment contract, keep unchanged refs unless explicitly removed, and never drop attachments because a text-only draft was reconstructed. | In scope | FR-007, FR-008, FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Task detail MUST group persisted image inputs by explicit objective-scoped and step-scoped target metadata.
- **FR-002**: Task image preview and download actions MUST use MoonMind-owned artifact API endpoints for browser-visible access.
- **FR-003**: The UI MUST NOT infer target binding from filenames, artifact links, attachment ordering, or preview metadata.
- **FR-004**: Preview failure MUST preserve attachment filename or label, target scope, content metadata, and download action visibility.
- **FR-005**: Artifacts without authoritative target metadata MUST remain visible in the generic artifacts area rather than being target-grouped from heuristics.
- **FR-006**: Target-aware image rendering MUST expose enough metadata for reviewers to understand attachment scope.
- **FR-007**: Edit and rerun surfaces MUST distinguish persisted attachment refs from newly selected local files.
- **FR-008**: Unchanged persisted attachment refs MUST remain in the edit or rerun draft unless explicitly removed.
- **FR-009**: Removing a persisted attachment MUST be an explicit action and MUST NOT silently remove unrelated persisted refs.

### Key Entities

- **Task Image Input**: A persisted image artifact linked to a task execution with content type, size, filename or label, and authoritative target metadata.
- **Attachment Target**: The persisted binding that identifies whether an image belongs to the task objective or a specific step.
- **Persisted Attachment Ref**: An existing artifact reference reconstructed into edit or rerun drafts.
- **Local Attachment File**: A newly selected browser file that has not yet been uploaded or persisted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A task detail test proves objective-scoped and step-scoped image inputs render in separate target groups.
- **SC-002**: A UI test proves image preview failure leaves metadata and download actions visible.
- **SC-003**: UI tests prove task image download links use `/api/artifacts/{artifactId}/download` rather than storage-provider URLs.
- **SC-004**: Edit/rerun tests prove unchanged persisted refs remain serialized unless explicitly removed.
