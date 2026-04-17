# MM-373 MoonSpec Orchestration Input

## Source

- Jira issue: MM-373
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preview and download task images by target
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-373 from MM project
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
- None
