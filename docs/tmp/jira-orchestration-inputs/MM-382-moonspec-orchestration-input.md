# MM-382 MoonSpec Orchestration Input

## Source

- Jira issue: MM-382
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Edit and Rerun Attachment Reconstruction
- Labels: `moonmind-workflow-mm-5818081f-60f0-45dd-ad16-3f7753de93ae`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-382 from MM project
Summary: Edit and Rerun Attachment Reconstruction
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-382 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-382: Edit and Rerun Attachment Reconstruction

Source Reference
- Source Document: docs/UI/CreatePage.md
- Source Title: Create Page
- Source Sections:
  - 13. Edit and rerun contract
  - 14. Submission contract
  - 16. Failure and empty-state rules
  - 18. Testing requirements
- Coverage IDs:
  - DESIGN-REQ-019
  - DESIGN-REQ-020
  - DESIGN-REQ-021
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-023
  - DESIGN-REQ-025

User Story
As a task author, I can edit or rerun an existing MoonMind.Run and get a reconstructed draft that preserves objective text, attachments, templates, dependencies, runtime options, and untouched attachment refs unless I change them.

Acceptance Criteria
- Given I edit an existing MoonMind.Run, then the draft is reconstructed from the authoritative task input snapshot.
- Given I rerun an existing MoonMind.Run, then objective text, objective attachments, step instructions, step attachments, runtime and publish settings, applied templates and dirty state, and dependencies are reconstructed when they remain editable.
- Given persisted attachments exist, then they render distinctly from newly selected local files.
- Given I do not touch persisted attachments during rerun, then their refs survive the round trip without being dropped or duplicated.
- Given I remove, add, or replace an attachment, then only the authored target changes and unrelated draft state remains intact.
- Given one or more attachment bindings cannot be reconstructed, then edit or rerun fails explicitly rather than silently dropping attachments.

Requirements
- Use the authoritative task input snapshot as the source for edit and rerun draft reconstruction.
- Reconstruct objective text, objective-scoped attachments, step instructions, step-scoped attachments, runtime settings, publish settings, templates, dirty state, and dependencies.
- Differentiate existing persisted attachments from new local files in state and UI.
- Support keep, remove, add, and replace flows for persisted attachments.
- Preserve untouched attachment refs by default during rerun.
- Fail explicitly if attachment targeting or bindings cannot be reconstructed.
- Cover edit reconstruction, rerun preservation, and no silent drop/duplicate behavior in tests.

Relevant Implementation Notes
- Treat `docs/UI/CreatePage.md` as the source design for edit and rerun reconstruction, submission, failure, empty-state, and testing behavior.
- Preserve the Jira issue key MM-382 anywhere downstream artifacts summarize or verify the work.
- Reconstruct drafts from the authoritative task input snapshot rather than from lossy projected execution state.
- Preserve objective text, objective-scoped attachments, step instructions, step-scoped attachments, runtime settings, publish settings, template state, dirty state, and dependencies when those fields remain editable.
- Keep persisted attachment refs distinct from newly selected local files in draft state and UI.
- Preserve untouched attachment refs during rerun and only change the specific authored target when a user removes, adds, or replaces an attachment.
- Fail edit or rerun reconstruction explicitly when attachment targeting or bindings cannot be reconstructed; do not silently drop or duplicate attachments.

Verification
- Confirm edit reconstruction uses the authoritative task input snapshot.
- Confirm rerun reconstruction preserves objective text, objective attachments, step instructions, step attachments, runtime settings, publish settings, applied templates, dirty state, and dependencies when editable.
- Confirm persisted attachments render distinctly from newly selected local files.
- Confirm untouched persisted attachment refs survive the rerun round trip without being dropped or duplicated.
- Confirm remove, add, and replace actions only affect the authored target and leave unrelated draft state intact.
- Confirm reconstruction fails explicitly when one or more attachment bindings cannot be reconstructed.
- Confirm tests cover edit reconstruction, rerun preservation, and no silent drop/duplicate behavior.

Out of Scope
- Reconstructing edit or rerun drafts from lossy projected execution state instead of the authoritative task input snapshot.
- Silently dropping persisted attachments when bindings cannot be reconstructed.
- Duplicating untouched attachment refs during rerun.
