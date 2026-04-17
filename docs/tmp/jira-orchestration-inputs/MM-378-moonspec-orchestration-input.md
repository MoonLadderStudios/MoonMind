# MM-378 MoonSpec Orchestration Input

## Source

- Jira issue: MM-378
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preset Application and Reapply State
- Labels: `moonmind-workflow-mm-5818081f-60f0-45dd-ad16-3f7753de93ae`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-378 from MM project
Summary: Preset Application and Reapply State
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-378 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-378: Preset Application and Reapply State

Short Name
preset-application-reapply-state

User Story
As a task author, I can apply reusable task presets explicitly, edit preset objective inputs, and understand when expanded steps need an explicit reapply without losing my manual step customizations.

Acceptance Criteria
- Given only the initial empty step exists, when I apply a preset, then the preset may replace the placeholder step set with expanded blueprint steps.
- Given authored steps already exist, when I apply a preset, then expanded preset steps append to the current draft.
- Given I select a preset without pressing Apply, then the draft does not mutate.
- Given preset objective text is non-empty, then it is preferred over primary-step instructions for resolved objective text and title derivation.
- Given I change preset objective text or objective-scoped attachments after applying a preset, then the preset is marked as needing explicit reapply and expanded steps are not overwritten automatically.
- Given I manually edit a template-bound step instruction or attachment set, then that step detaches from template instruction or input identity.

Requirements
- Expose optional Preset, Feature Request / Initial Instructions, objective images, Apply, optional save-as-preset, and status controls.
- Treat applied preset steps as expanded blueprints rather than live bindings.
- Track template step identity only while authored instructions and attachments match the template input contract.
- Store templateAttachments for detachment comparisons.
- Mark applied preset state dirty when preset objective text or objective-scoped attachments change.
- Resolve objective text from preset objective, then primary instructions, then the most recent applied preset request alias.
- Cover preset dirty state and template detachment in tests.

Independent Test
Create page coverage verifies preset selection does not mutate the draft until Apply is pressed, initial empty steps can be replaced by expanded blueprint steps, authored drafts receive appended preset steps, preset objective text drives objective and title resolution, changed objective inputs mark the applied preset state as needing explicit reapply, and manual edits detach template-bound step instruction or input identity.

Source Document
- `docs/UI/CreatePage.md`

Source Sections
- 7.5 Template-bound steps
- 8. Task preset contract
- 15. Objective resolution and title derivation

Coverage IDs
- DESIGN-REQ-010
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-022
- DESIGN-REQ-025

Relevant Implementation Notes
- The preset area is optional and exposes Preset, Feature Request / Initial Instructions, objective-scoped image inputs when attachment policy is enabled, Apply, optional Save Current Steps as Preset, and status text.
- Applying a preset expands blueprint steps into the step list.
- When the form still contains only the initial empty default step, applying a preset may replace that placeholder step set.
- When authored steps already exist, applying a preset appends expanded preset steps to the current draft.
- Selecting a preset alone must not modify the draft.
- Preset application remains an explicit action.
- Feature Request / Initial Instructions is the preset-owned objective text source.
- Non-empty preset objective text is preferred over primary-step instructions for objective text resolution and title derivation.
- Objective-scoped attachments are the matching structured input source for preset objective text.
- Changing preset objective text or objective-scoped attachments after apply marks the preset state as needs reapply.
- Reapply is explicit and must not automatically overwrite expanded steps because preset inputs changed.
- Preset-expanded steps may carry template step identity only while authored instructions and attachment sets still match the template-authored step input contract.
- Manual edits to template-bound step instructions detach that step from template instruction identity.
- Manual edits to template-bound step attachment sets detach that step from template input identity.
- `templateAttachments` stores the template-authored attachment set used for detachment comparisons.
- Importing Jira text or Jira images into a template-bound step counts as a manual edit.

Out of Scope
- Treating applied preset steps as live bindings that update automatically when preset inputs change.
- Automatically rewriting expanded steps when preset objective text or objective-scoped attachments change.
- Copying objective-scoped attachments into step attachments unless a future preset contract explicitly defines that behavior.
- Changing unrelated Create page dependency, execution context, publish, or attachment policy behavior.

Verification
- Run focused Create page frontend tests covering preset apply, append, no-mutation-on-select, dirty reapply state, objective resolution, title derivation, and template detachment behavior.
- Verify Create page state preserves manual step customizations when preset objective inputs change.
- Verify `templateAttachments` supports detachment comparisons for template-bound step attachment sets.
- Run `./tools/test_unit.sh` before completion when implementation changes are made.

Needs Clarification
- None
