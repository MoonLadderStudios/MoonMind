# MM-377 MoonSpec Orchestration Input

## Source

- Jira issue: MM-377
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Step-First Draft and Attachment Targets
- Labels: `moonmind-workflow-mm-5818081f-60f0-45dd-ad16-3f7753de93ae`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-377 from MM project
Summary: Step-First Draft and Attachment Targets
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-377 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-377: Step-First Draft and Attachment Targets

Short Name
step-first-draft-attachment-targets

Source Reference
- Source document: `docs/UI/CreatePage.md`
- Source title: Create Page
- Source sections: 6. Draft model, 7.1 Step list, 7.2 Step fields, 7.3 Step attachment contract, 7.4 Objective-scoped attachment target
- Coverage IDs: DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-024, DESIGN-REQ-025

User Story
As a task author, I can build a step-first draft where instructions, skills, and image inputs belong to explicit objective or step targets and stay attached to the correct target through normal editing.

Acceptance Criteria
- Given the draft contains one step, then Step 1 is identified as Primary and the page remains valid when primary instructions or an explicit skill is present.
- Given additional steps exist, then the primary step requires instructions while non-primary steps may omit instructions or inherit the primary skill default.
- Given I add an image to a step, then it appears in that step card and submits through `task.steps[n].inputAttachments`.
- Given I add an objective-scoped image, then it belongs to the preset objective target and submits through `task.inputAttachments`.
- Given I reorder steps, then step attachments move with their owning steps and do not attach to another step implicitly.
- Given an attachment control is available, then open, upload, remove, retry, and target actions are keyboard accessible and labeled for assistive technology.

Requirements
- Represent attachments as structured `DraftAttachment` records with objective or step targets.
- Distinguish selected, uploading, uploaded, failed, local-file, and artifact-backed attachment states.
- Keep attachments out of instruction text and detached from filename or ordering conventions.
- Render step attachments in the same card as the step instructions they inform.
- Support add, remove, and reorder for steps without creating dependency edges.
- Allow objective-scoped attachments only as task-level objective inputs, not automatic step copies.
- Cover target isolation and reorder preservation in tests.

Relevant Implementation Notes
- The Create page draft model should be step-first: task instructions, selected skills, and attachments are represented as draft state before submission.
- Step 1 is the primary step and remains valid with primary instructions or an explicit skill.
- Non-primary steps may omit instructions when they inherit the primary skill default.
- Step-scoped images submit through `task.steps[n].inputAttachments`.
- Objective-scoped images submit through `task.inputAttachments`.
- Attachment ownership must survive normal edit operations, especially step reorder.
- Attachment controls must remain keyboard accessible and assistive-technology labeled.

Out of Scope
- Creating dependency edges when steps are added, removed, or reordered.
- Copying objective-scoped attachments automatically into individual steps.
- Encoding attachments inside instruction text.
- Inferring attachment target identity from filenames or current ordering.

Verification
- Verify one-step drafts identify Step 1 as Primary and remain valid with primary instructions or an explicit skill.
- Verify additional steps enforce primary-step instructions while allowing non-primary instruction omission or inherited primary skill defaults.
- Verify step-scoped images render in their owning step card and submit through `task.steps[n].inputAttachments`.
- Verify objective-scoped images submit through `task.inputAttachments`.
- Verify step reorder preserves attachment ownership.
- Verify attachment controls for open, upload, remove, retry, and target actions are keyboard accessible and labeled for assistive technology.
- Run focused Create page unit tests and `./tools/test_unit.sh` before completion when implementation changes are made.

Needs Clarification
- None
