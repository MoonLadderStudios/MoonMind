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
