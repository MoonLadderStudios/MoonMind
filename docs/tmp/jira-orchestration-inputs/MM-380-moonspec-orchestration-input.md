# MM-380 MoonSpec Orchestration Input

## Source

- Jira issue: MM-380
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Policy-Gated Image Upload and Submit
- Labels: `moonmind-workflow-mm-5818081f-60f0-45dd-ad16-3f7753de93ae`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-380 from MM project
Summary: Policy-Gated Image Upload and Submit
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-380 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-380: Policy-Gated Image Upload and Submit

Source Reference
- Source Document: docs/UI/CreatePage.md
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

User Story
As a task author, I can add permitted image inputs, see validation and upload failures at the correct target, and submit only after local images become artifact-backed structured attachment refs.

Acceptance Criteria
- Given attachment policy is disabled, then all attachment entry points are hidden and the page remains fully usable for manual authoring.
- Given policy allows only image MIME types, then the UI uses an image-specific label such as Images.
- Given count, single-file size, total size, or content type validation fails, then the browser fails fast and visibly at the affected target before upload and again blocks submit if unresolved.
- Given upload fails, then the failure remains local to the affected target and I can remove or retry without losing unrelated draft state.
- Given preview fails, then attachment metadata remains visible, the draft is not corrupted, and removal remains available.
- Given I submit create, edit, or rerun with local images, then images upload to the artifact system first and the execution payload contains structured refs rather than binary content.

Requirements
- Read attachmentPolicy from server-provided runtime configuration.
- Validate attachment count, per-file bytes, total bytes, and content type before upload and at submit time.
- Represent upload and preview failure states without silently dropping selected images.
- Provide keyboard-accessible remove and retry actions plus concise per-target summaries.
- Upload local images to the artifact system before create, edit, or rerun submission.
- Submit task.inputAttachments and task.steps[n].inputAttachments as structured artifact refs.
- Block submit while attachments are invalid, failed, incomplete, or still uploading.
- Cover policy, validation, failure isolation, upload-before-create, and invalid/incomplete submit blocking in tests.

Relevant Implementation Notes
- Treat `docs/UI/CreatePage.md` as the source design for attachment policy, submission, failure, empty-state, and testing behavior.
- Preserve the Jira issue key MM-380 anywhere downstream artifacts summarize or verify the work.
- Keep attachment entry points policy-gated; a disabled policy must hide those entry points without blocking manual task authoring.
- Use image-specific copy when the server policy allows only image MIME types.
- Validate count, per-file size, total size, and MIME type before upload and again before submit.
- Keep upload and preview failures scoped to the affected target, with retry or remove actions that do not corrupt unrelated draft state.
- Convert local image selections into artifact-backed structured refs before create, edit, or rerun submission.
- Ensure submitted payloads use `task.inputAttachments` and `task.steps[n].inputAttachments` for structured attachment refs, not binary content.

Verification
- Confirm the Create page reads server-provided attachment policy and hides attachment entry points when policy is disabled.
- Confirm image-only policy surfaces image-specific labeling.
- Confirm client validation covers attachment count, single-file size, total size, and content type before upload and at submit time.
- Confirm upload failures, preview failures, retry, and remove behavior remain scoped to the affected target.
- Confirm create, edit, and rerun submission upload local images before sending execution payloads.
- Confirm execution payloads contain structured artifact refs under `task.inputAttachments` and `task.steps[n].inputAttachments`.
- Confirm submit is blocked while attachments are invalid, failed, incomplete, or uploading.
- Confirm tests cover policy, validation, failure isolation, upload-before-create, and invalid or incomplete submit blocking.

Out of Scope
- Embedding binary image content in task execution payloads.
- Inferring attachment validity from filenames instead of policy, size, MIME type, and upload state.
- Allowing unresolved invalid, failed, incomplete, or uploading attachments through create, edit, or rerun submission.
