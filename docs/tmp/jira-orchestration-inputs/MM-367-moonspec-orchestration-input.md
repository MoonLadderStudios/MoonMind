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
