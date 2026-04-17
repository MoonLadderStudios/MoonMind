# MM-370 MoonSpec Orchestration Input

## Source

- Jira issue: MM-370
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Materialize attachment manifest and workspace files
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-370 from MM project
Summary: Materialize attachment manifest and workspace files
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-370 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-370: Materialize attachment manifest and workspace files

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 3.2 Canonical terminology
  - 4. End-to-end desired-state flow
  - 8. Prepare-time materialization contract
- Coverage IDs:
  - DESIGN-REQ-002
  - DESIGN-REQ-004
  - DESIGN-REQ-011

User Story
As a runtime executor, I need workflow prepare to deterministically download declared input attachments, write a canonical manifest, and place files in target-aware workspace paths before the relevant runtime or step executes.

Acceptance Criteria
- Prepare downloads all declared input attachments before the relevant runtime or step executes.
- Prepare writes `.moonmind/attachments_manifest.json` using the canonical manifest entry shape.
- Objective images are materialized under `.moonmind/inputs/objective/`.
- Step images are materialized under `.moonmind/inputs/steps/<stepRef>/`.
- Workspace paths are deterministic and target-aware; one target path does not depend on unrelated target ordering.
- A stable step reference is assigned when a step has no explicit id.
- Partial materialization is reported as failure, not best-effort success.

Requirements
- Include `artifactId`, `filename`, `contentType`, `sizeBytes`, `targetKind`, optional `stepRef`/`stepOrdinal`, `workspacePath`, and optional context/source paths in manifest entries.
- Sanitize filenames while preserving deterministic artifactId-prefixed paths.
- Treat execution payload and snapshot refs as the source for materialization.
- Preserve canonical target meaning from the field that contains each ref: objective-scoped attachments from `task.inputAttachments` and step-scoped attachments from `task.steps[n].inputAttachments`.
- Materialize objective-scoped attachments under `.moonmind/inputs/objective/<artifactId>-<sanitized-filename>`.
- Materialize step-scoped attachments under `.moonmind/inputs/steps/<stepRef>/<artifactId>-<sanitized-filename>`.
- Ensure attachment identity and target meaning do not depend on filename conventions or unrelated target ordering.
- Keep raw attachment bytes out of Temporal histories and task instruction text; runtime adapters consume structured refs, materialized files, and derived context.
- Fail explicitly when any declared attachment cannot be downloaded, written, or represented in the manifest.

Relevant Implementation Notes
- The canonical control-plane field name is `inputAttachments`.
- The canonical prepared manifest entry shape is `AttachmentManifestEntry` from `docs/Tasks/ImageSystem.md`.
- Workflow prepare owns deterministic local file materialization.
- Upload completion and execution snapshot persistence happen before workflow prepare receives refs.
- The execution API snapshot is the authoritative source for attachment targeting.
- Runtime adapters consume structured refs and derived context, not browser-local state.
- Target-aware workspace paths must be stable for objective and step targets independently.
- If a step has no explicit `id`, the control plane or prepare layer must assign a stable step reference for manifest and path purposes.
- Partial materialization must stop execution as a failure, preserving diagnostics for the missing or invalid attachment.

Suggested Implementation Areas
- Workflow prepare or artifact materialization activities that download input attachments.
- Manifest writer for `.moonmind/attachments_manifest.json`.
- Workspace path generation and filename sanitization helpers.
- Stable step reference assignment for steps without explicit ids.
- Execution payload or snapshot parsing for objective-scoped and step-scoped attachment refs.
- Failure handling and diagnostics for partial materialization.
- Unit and workflow/activity-boundary tests covering manifest shape, path determinism, target isolation, and failure behavior.

Validation
- Verify prepare downloads every declared objective-scoped and step-scoped input attachment before the relevant runtime or step executes.
- Verify `.moonmind/attachments_manifest.json` includes the canonical fields for every materialized attachment.
- Verify objective-scoped attachments are written under `.moonmind/inputs/objective/`.
- Verify step-scoped attachments are written under `.moonmind/inputs/steps/<stepRef>/`.
- Verify generated workspace paths are deterministic and independent of unrelated target ordering.
- Verify steps without explicit ids receive stable step references for path and manifest purposes.
- Verify a missing, invalid, or failed attachment download produces an explicit failure instead of best-effort success.
- Verify raw attachment bytes are not embedded in Temporal histories or task instruction text.

Non-Goals
- Generating target-aware vision context artifacts; that is covered by a separate story.
- Preserving attachment bindings across edit and rerun reconstruction; that is covered by a separate story.
- Enforcing upload policy, content-type policy, or artifact completion integrity beyond consuming already-declared refs.
- Inferring target bindings from filenames, artifact links, or artifact metadata.
- Adding generic non-image attachment support beyond the existing image input materialization contract.

Needs Clarification
- None
