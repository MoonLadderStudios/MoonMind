# MM-369 MoonSpec Orchestration Input

## Source

- Jira issue: MM-369
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preserve attachment bindings in snapshots and reruns
- Labels: `moonmind-workflow-mm-710b9b03-7ff6-4c87-ac25-ddef82bbf280`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-369 from MM project
Summary: Preserve attachment bindings in snapshots and reruns
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-369 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-369: Preserve attachment bindings in snapshots and reruns

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 5.3 Authoritative snapshot contract
  - 11. UI preview and detail contract
  - 13. Edit and rerun durability contract
- Coverage IDs:
  - DESIGN-REQ-007
  - DESIGN-REQ-015
  - DESIGN-REQ-018

User Story
As a user editing or rerunning a task, I need MoonMind to reconstruct attachments from the authoritative task input snapshot so unchanged bindings survive and changes are always explicit.

Acceptance Criteria
- The snapshot preserves text fields, target attachment refs, step identity/order, runtime, publish, repository settings, and applied preset metadata.
- Attachment target binding is reconstructed from the snapshot, not inferred from artifact links or filenames.
- Unchanged attachment refs survive edit and rerun unchanged.
- Removing an attachment and adding a new attachment are explicit user actions.
- A text-only draft reconstruction cannot silently drop attachments.
- The system fails explicitly if attachment bindings cannot be reconstructed.
- Historical artifacts may remain according to retention even after an edited draft stops referencing them.

Requirements
- Persist target attachment refs in the task input snapshot.
- Use the same attachment contract for create, edit, and rerun.
- Keep step identity and ordering stable enough to bind step-scoped attachments.
- Distinguish persisted attachment refs from new local files in edit/rerun flows.
- Preserve objective-scoped attachments in `task.inputAttachments`.
- Preserve step-scoped attachments in `task.steps[n].inputAttachments`.
- Normalize attachment refs before workflow start without changing target binding semantics.
- Reconstruct attachment target binding from the authoritative task input snapshot, not from artifact links, filenames, or UI-only heuristics.
- Preserve runtime, publish, repository settings, and applied preset metadata alongside text fields and attachment refs in the snapshot.
- Fail explicitly if edit or rerun reconstruction cannot preserve attachment bindings.

Relevant Implementation Notes
- The original task input snapshot is the source of truth for edit and rerun reconstruction.
- Task detail, edit, and rerun previews should be organized by explicit attachment target: objective-scoped or step-scoped.
- Preview and download surfaces must not infer target binding from filenames.
- Preview failure must not remove access to metadata or download actions.
- Edit and rerun surfaces must distinguish persisted attachments from new local files that have not yet been uploaded.
- Create, edit, and rerun should use the same authoritative attachment contract.
- Unchanged attachment refs should survive edit and rerun unchanged.
- Removing an existing attachment and adding a new attachment should remain explicit user actions.
- Historical source artifacts may remain according to retention policy even after an edited draft stops referencing them.

Suggested Implementation Areas
- Task input snapshot persistence for objective and step attachment refs.
- Edit draft reconstruction from persisted task snapshots.
- Rerun draft reconstruction from persisted task snapshots.
- Create-page or task-detail attachment preview grouping by target.
- Validation and error handling for missing or unreconstructable attachment bindings.
- Tests for create, edit, and rerun attachment binding preservation.

Validation
- Verify a task snapshot preserves text fields, target attachment refs, step identity/order, runtime, publish, repository settings, and applied preset metadata.
- Verify edit reconstruction uses persisted snapshot attachment refs and does not infer target binding from artifact links or filenames.
- Verify rerun reconstruction preserves unchanged objective-scoped and step-scoped attachment refs.
- Verify removing an attachment and adding a new attachment are explicit user actions.
- Verify a text-only draft reconstruction cannot silently drop existing attachments.
- Verify the system fails explicitly when attachment bindings cannot be reconstructed.
- Verify historical artifacts can remain under retention when an edited draft no longer references them.

Non-Goals
- Inferring attachment target bindings from filenames, artifact links, or attachment metadata.
- Silently dropping attachments during edit or rerun reconstruction.
- Rewriting attachment refs through hidden compatibility transforms.
- Changing artifact retention semantics beyond preserving references correctly in edit and rerun flows.
- Adding generic non-image attachment support beyond this story's image attachment binding durability contract.

Needs Clarification
- None
