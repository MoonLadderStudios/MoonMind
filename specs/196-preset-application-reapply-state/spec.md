# Feature Specification: Preset Application and Reapply State

**Feature Branch**: `196-preset-application-reapply-state`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**:

```text
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
```

**Implementation Intent**: Runtime implementation. Required deliverables include Create page runtime behavior, production state handling, and focused validation tests.

## User Story - Preset Application and Reapply State

**Summary**: As a task author, I want reusable task presets to apply only when I explicitly choose Apply or Reapply so that edited preset objective inputs and manually customized expanded steps remain under my control.

**Goal**: Task authors can apply a preset to generate blueprint steps, edit preset objective text or objective-scoped attachments after apply, see a clear reapply-needed state, and keep manually customized template-derived steps from being treated as still template-bound.

**Independent Test**: Render the Create page, select and apply a preset, modify preset objective inputs and template-bound step inputs, then submit. The story passes when selecting a preset alone does not mutate steps, applying replaces only the initial empty step or appends to authored steps, preset objective text drives task objective and title, changed preset objective text or objective-scoped attachments mark the preset as needing explicit reapply without overwriting expanded steps, and edited template-bound instructions or attachments detach template identity.

**Acceptance Scenarios**:

1. **Given** the Create page contains only its initial empty step, **when** a task author applies a preset, **then** the expanded preset blueprint steps replace the placeholder step set.
2. **Given** the Create page already contains authored step content, **when** a task author applies a preset, **then** the expanded preset blueprint steps append after the existing authored steps.
3. **Given** a task author selects a preset in the preset picker, **when** Apply has not been pressed, **then** the current draft steps remain unchanged.
4. **Given** preset objective text is non-empty, **when** the task objective and title are resolved, **then** that preset objective text is preferred over the primary step instructions and the latest applied preset request alias.
5. **Given** a preset has already been applied, **when** the task author changes preset objective text or objective-scoped attachments, **then** the preset area is marked as needing explicit reapply and expanded steps are not overwritten automatically.
6. **Given** a preset-expanded step is still template-bound, **when** the task author edits that step's instruction text or attachment set, **then** the step detaches from template instruction or input identity before submission.

### Edge Cases

- Applying a preset that expands to zero steps keeps the draft valid with one empty step.
- Reverting preset objective text back to the last applied value clears the reapply-needed state when no other preset objective inputs are dirty.
- Selecting a different preset clears stale apply status without mutating draft steps.
- Jira import into the preset objective field counts as a preset objective text change.
- Jira image import or local file selection into the preset objective target counts as an objective-scoped attachment change.
- Jira text or image import into a template-bound step counts as a manual edit.

## Assumptions

- The existing Create page task template catalog remains the preset source.
- Objective-scoped attachments use the existing attachment policy and artifact upload path, but are bound to task-level `inputAttachments` instead of a specific step.
- Template attachment identity can be compared by stable attachment attributes available in the browser draft: artifact ID when present, otherwise filename, content type, and size.

## Source Design Requirements

- **DESIGN-REQ-010**: Source section 7.5 requires preset-expanded steps to remain template-bound only while authored instructions and attachment sets match the template-authored step input contract, with Jira imports counting as manual edits. Scope: in scope. Maps to FR-006, FR-007, and FR-008.
- **DESIGN-REQ-011**: Source section 8.1 requires the preset area to expose Preset, Feature Request / Initial Instructions, objective-scoped image inputs when attachments are enabled, Apply, optional save-as-preset, and status text. Scope: in scope. Maps to FR-001 and FR-005.
- **DESIGN-REQ-012**: Source sections 8.2 through 8.4 require preset selection to be non-mutating, preset application to be explicit, initial empty steps to be replaceable, authored steps to receive appended preset steps, and changed preset inputs to require explicit reapply without automatic step overwrites. Scope: in scope. Maps to FR-002, FR-003, FR-005, and FR-009.
- **DESIGN-REQ-022**: Source section 15 requires resolved objective text to prefer preset Feature Request / Initial Instructions, then primary step instructions, then the latest applied preset request alias. Scope: in scope. Maps to FR-004.
- **DESIGN-REQ-025**: Source sections 7.4, 7.5, and 15 require objective-scoped attachments to remain task-level objective inputs and template-bound step attachments to detach when manually changed. Scope: in scope. Maps to FR-005, FR-007, and FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST expose optional preset controls: Preset, Feature Request / Initial Instructions, objective-scoped image inputs when attachment policy is enabled, Apply or Reapply, optional Save Current Steps as Preset, and preset status text.
- **FR-002**: Selecting a preset in the preset picker MUST NOT mutate authored step instructions, step attachments, applied template metadata, task objective text, or submitted task payload until Apply is pressed.
- **FR-003**: Applying a preset MUST replace the initial empty placeholder step set, but MUST append expanded preset steps when authored steps already exist.
- **FR-004**: Resolved task objective text and title derivation MUST prefer non-empty preset objective text over primary step instructions and the latest applied preset request alias.
- **FR-005**: After a preset has been applied, changing preset objective text or objective-scoped attachments MUST mark the preset as needing explicit reapply and MUST NOT automatically overwrite expanded steps.
- **FR-006**: Preset-expanded steps MUST carry template step identity only while authored instructions match template instructions.
- **FR-007**: Preset-expanded steps MUST carry template input identity only while authored attachment sets match the template attachment set used for detachment comparisons.
- **FR-008**: Manual edits, Jira text imports, Jira image imports, and local attachment changes on template-bound steps MUST detach the affected step from template instruction or input identity before submission.
- **FR-009**: Reapplying a dirty preset MUST be explicit and MUST update the applied preset state only after Apply or Reapply succeeds.
- **FR-010**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-378.

### Key Entities

- **Preset Objective Input**: The preset-owned Feature Request / Initial Instructions text plus objective-scoped attachments that form task-level objective context.
- **Applied Preset State**: The stored applied template metadata and last applied preset objective inputs used to determine whether reapply is needed.
- **Template-Bound Step**: A step generated from a preset blueprint that still matches its template-authored instruction and attachment input contract.
- **Template Attachment Snapshot**: The attachment identity set captured from a preset-expanded step for later detachment comparisons.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Focused Create page tests verify preset selection alone leaves the step draft unchanged.
- **SC-002**: Focused Create page tests verify Apply replaces the initial empty step and appends to authored step drafts.
- **SC-003**: Focused Create page tests verify preset objective text controls task objective and derived title when non-empty.
- **SC-004**: Focused Create page tests verify changed preset objective text and objective-scoped attachments show Reapply preset without changing expanded step content.
- **SC-005**: Focused Create page tests verify manually edited or Jira-imported template-bound step instructions submit without template step ID.
- **SC-006**: Focused Create page tests verify changed template-bound step attachments submit without template input identity.
- **SC-007**: Verification evidence preserves MM-378 as the source Jira issue for the feature.
