# MM-368 MoonSpec Orchestration Input

## Source

- Jira issue: MM-368
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enforce image artifact storage and policy
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-368 from MM project
Summary: Enforce image artifact storage and policy
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-368 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-368: Enforce image artifact storage and policy

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 6. Artifact model and storage contract
  - 7. Validation and policy contract
  - 12. Authorization and security contract
- Coverage IDs:
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-017

User Story
As an operator, I need uploaded image bytes stored as first-class execution artifacts and governed by server-defined attachment policy so invalid or unsupported image inputs never start an execution.

Acceptance Criteria
- Image bytes are stored in the Artifact Store and linked to the execution as input attachments.
- Allowed content types default to `image/png`, `image/jpeg`, and `image/webp`; `image/svg+xml` is rejected.
- Browser checks are repeated server-side before artifact completion or execution start.
- Max count, per-file size, total size, and integrity constraints are enforced.
- Worker-side uploads cannot overwrite or impersonate reserved input attachment namespaces.
- Disabled policy hides Create-page entry points and rejects submitted image refs.
- Unsupported future fields and incompatible runtimes fail explicitly rather than being ignored or dropped.

Requirements
- Persist uploaded image bytes as artifacts rather than execution payload data.
- Attach execution-owned artifact links for submitted images.
- Treat artifact metadata as observability, not as binding source of truth.
- Revalidate content type, signature, counts, sizes, and completion integrity server-side.
- Reject scriptable image content types and other untrusted image risks.
- Keep the task snapshot as the authoritative source for attachment target binding.
- Prevent worker-side uploads from overwriting or impersonating reserved input attachment namespaces.
- Enforce server-defined attachment policy even when browser-side checks have already run.
- Fail explicitly when image attachments are disabled, unsupported by the selected runtime, incomplete, invalid, or include unsupported future fields.

Relevant Implementation Notes
- Canonical image input field: `inputAttachments`.
- Objective-scoped attachments are submitted through `task.inputAttachments`.
- Step-scoped attachments are submitted through `task.steps[n].inputAttachments`.
- Image bytes must not be embedded in Temporal histories or task instruction text.
- The control plane submits structured attachment references, not raw binaries.
- The execution API persists the authoritative snapshot of attachment targeting.
- Image artifacts should be linked to the execution with execution-owned artifact links.
- Artifact metadata may include target diagnostics such as `source`, `attachmentKind`, `targetKind`, `stepRef`, `stepOrdinal`, and `originalFilename`, but metadata is not the binding source of truth.
- Integrity must be enforced at artifact completion time before execution start.
- Policy defaults should include `enabled=true`, max count, per-file size, total size, and allowed content types of `image/png`, `image/jpeg`, and `image/webp`.
- The Create page may label the feature as images, but the implementation should preserve the generic `inputAttachments` contract.
- Security boundaries are artifact-first and execution-owned: no direct browser access to object storage, no direct browser access to Jira or provider file endpoints, no scriptable image types, and no silent compatibility transforms that rewrite attachment refs or retarget them to another step.

Suggested Implementation Areas
- Artifact upload creation, completion, and validation paths.
- Execution submission validation and task snapshot persistence.
- Create-page image entry point visibility and browser-side policy checks.
- Server-side attachment policy enforcement before artifact completion or execution start.
- Worker artifact upload namespace protections.
- Tests covering artifact storage, policy rejection, execution linkage, disabled policy behavior, and unsupported runtime or future-field failure.

Validation
- Verify uploaded image bytes are persisted as artifacts and linked to the execution as input attachments.
- Verify `image/png`, `image/jpeg`, and `image/webp` are accepted by default and `image/svg+xml` is rejected.
- Verify server-side validation repeats browser checks for content type, signature, max count, per-file size, total size, and completion integrity.
- Verify invalid, incomplete, over-limit, or scriptable image uploads are rejected before execution start.
- Verify disabled attachment policy hides Create-page entry points and rejects submitted image refs.
- Verify worker-side uploads cannot overwrite or impersonate reserved input attachment namespaces.
- Verify unsupported future fields and incompatible runtimes fail explicitly instead of being ignored or dropped.

Non-Goals
- Embedding raw image bytes in Temporal histories, workflow payloads, or task instruction text.
- Treating artifact metadata as the authoritative attachment binding source.
- Allowing `image/svg+xml` or other scriptable image content types.
- Adding hidden compatibility transforms that silently rewrite attachment refs or retarget them to another step.
- Redesigning the broader artifact store, retention model, or runtime adapter architecture beyond the storage and policy enforcement needed for this story.

Needs Clarification
- None
