# Feature Specification: Edit Task Shows All Steps

**Feature Branch**: `192-edit-task-all-steps`
**Created**: 2026-04-16
**Status**: Draft
**Input**:

```text
# MM-340 MoonSpec Orchestration Input

## Source

- Jira issue: MM-340
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: You should see all steps from a multi-step task when you click edit
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

MM-340: You should see all steps from a multi-step task when you click edit

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-340 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Fix the Edit Task page so editing a multi-step task shows every step in the task, not only step 1.

Current behavior:

When a user opens the Edit Task page for a multi-step task, the page only displays step 1.

Expected behavior:

When a user clicks edit for a multi-step task, the Edit Task page displays all steps from that task so the user can review and edit the complete multi-step plan.

## Supplemental Acceptance Criteria

- Given a task has multiple steps, when the user opens the Edit Task page for that task, then every existing step is visible in the edit form.
- Given a task has a single step, when the user opens the Edit Task page, then the existing single-step editing behavior remains available.
- Given the Edit Task page loads an existing multi-step task, when step data includes more than one step, then the UI must not truncate the list to the first step.
- Given the user saves edits for a multi-step task, when the task is persisted, then unchanged steps that were loaded into the edit form are preserved unless the user explicitly changes or removes them.
- Given the task data is missing or malformed for some steps, when the Edit Task page loads, then the UI surfaces a clear recoverable state instead of silently hiding later valid steps.

## Implementation Notes

Investigate the task editing data flow from the task detail/edit entrypoint through the frontend state initialization. The likely issue is that the edit form initializes from only the first step instead of mapping the task's full step collection.

Touch these surfaces as needed:

- Mission Control task edit page and related frontend state initialization.
- API/read-model code that supplies task steps to the edit page, if the frontend boot payload currently omits later steps.
- Save/update handlers that serialize edited task steps back to the backend.
- Focused frontend tests for multi-step edit initialization and preservation.

Verification:

- Add or update tests proving the Edit Task page renders all steps for a multi-step task.
- Add or update tests proving single-step edit behavior is unchanged.
- Add or update tests proving save/update does not drop existing later steps.
- Run the focused frontend test target during iteration, then run the required unit verification before finalizing implementation.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Edit Multi-Step Task

**Summary**: As a MoonMind operator, I want the Edit Task form to load every step from a multi-step task so that I can review and update the complete task plan.

**Goal**: Opening Edit Task for a multi-step `MoonMind.Run` task reconstructs the full ordered step list into the shared task form instead of flattening or truncating later steps.

**Independent Test**: Load the Edit Task page with a supported `MoonMind.Run` execution whose input contains multiple task steps, then verify each step appears in order with its instructions and saving the unchanged draft preserves the later steps.

**Acceptance Scenarios**:

1. **Given** an editable `MoonMind.Run` execution has multiple task steps, **when** the operator opens `/tasks/new?editExecutionId=<workflowId>`, **then** the Edit Task form displays every step in the original order.
2. **Given** an editable `MoonMind.Run` execution has a single-step task, **when** the operator opens Edit Task, **then** the existing single-step form behavior is preserved.
3. **Given** an editable multi-step task is loaded and the operator saves without removing later steps, **when** the update payload is built, **then** the later steps remain present in the submitted task parameters.
4. **Given** a reconstructed task step has optional title, skill, skill inputs, or template binding metadata, **when** the Edit Task form loads, **then** those fields remain associated with the same step instead of being merged into step 1.
5. **Given** some step entries are malformed or empty, **when** the Edit Task form loads, **then** valid later steps still appear and invalid empty entries do not cause a silent fallback to only step 1.

### Edge Cases

- Task-level objective instructions may exist alongside explicit step entries; the primary form step should preserve the objective while later explicit steps remain separate.
- A first explicit step may be absent or empty while later steps contain valid instructions.
- Step-level skill inputs may be object-shaped and must remain serializable in the edit form.
- Template-bound step identifiers and titles must not be dropped during draft reconstruction.

## Assumptions

- MM-340 targets the existing Temporal task editing path for `MoonMind.Run` executions.
- The issue is observable in the frontend reconstruction and form initialization path; backend changes are only needed if execution detail omits step data.
- Attachments are out of scope unless they already appear as step metadata in the existing contract.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST reconstruct an ordered edit-form step list from available task step data for supported `MoonMind.Run` edit and rerun drafts.
- **FR-002**: The system MUST display every valid reconstructed step in the Edit Task form instead of flattening all step instructions into step 1.
- **FR-003**: The system MUST preserve single-step reconstruction behavior when only one step or only task-level instructions are available.
- **FR-004**: The system MUST preserve each reconstructed step's instructions, title, explicit skill selection, skill inputs, required capabilities, and template binding metadata when those values are present.
- **FR-005**: The system MUST preserve valid later steps when earlier step entries are empty or partially malformed.
- **FR-006**: The edit update payload MUST retain unchanged later steps unless the operator explicitly removes or edits them.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-340.

### Key Entities

- **Temporal Submission Draft**: Frontend draft reconstructed from execution detail and optional input artifact data.
- **Editable Task Step**: Ordered form step containing instructions, title, skill selection, skill inputs, required capabilities, and template binding metadata when available.
- **Task Edit Update Payload**: Parameters patch submitted through `UpdateInputs` or `RequestRerun` after the operator saves the reconstructed draft.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit-level reconstruction tests prove a multi-step execution produces a draft containing every valid step in order.
- **SC-002**: Frontend integration tests prove Edit Task renders all reconstructed steps for a multi-step execution.
- **SC-003**: Frontend integration tests prove saving an unchanged multi-step edit payload preserves later step instructions.
- **SC-004**: Existing single-step edit reconstruction tests continue to pass.
- **SC-005**: Verification evidence preserves MM-340 as the source Jira issue for the feature.
