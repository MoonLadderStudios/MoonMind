# Feature Specification: Task Details Edit and Rerun Actions

**Feature Branch**: `258-task-details-edit-rerun-actions`
**Created**: 2026-04-25
**Status**: Draft
**Input**: Add the Edit and Rerun buttons as described in `docs/UI/TaskDetailsPage.md` to the Task Details page and ensure they are present with the appropriate task statuses.
**Source Design**: `docs/UI/TaskDetailsPage.md`

## User Story - Show Task Details Recovery Actions (Priority: P1)

As an operator inspecting a task execution, I need the Task Details page to show **Edit task** and **Rerun** when the backend says those actions are available so I can either revise the original task for a new run or rerun it unchanged from the same page.

### Summary

Task Details must expose recovery actions from explicit backend capabilities instead of hiding edit-for-rerun behind live input update state.

### Goal

Operators can see and use the correct **Edit task** and **Rerun** actions for failed and other eligible task statuses without mutating the original terminal execution.

### Independent Test

Load Task Details with mocked execution detail records for failed, running, missing-snapshot, and unsupported workflow-type cases and verify the visible links and hrefs match the capability contract.

### Acceptance Scenarios

1. **Given** a failed `MoonMind.Run` execution with an original task input snapshot and task editing enabled, **When** the Task Details page loads, **Then** the Task Actions section shows **Edit task** and **Rerun** as separate links.
2. **Given** a running execution whose inputs can still be updated, **When** the Task Details page loads, **Then** the page shows **Edit task** and does not show **Rerun** unless the backend also explicitly allows rerun.
3. **Given** a terminal execution without an original task input snapshot, **When** the Task Details page loads, **Then** **Edit task** and **Rerun** remain hidden and disabled reasons identify the missing snapshot.
4. **Given** a non-`MoonMind.Run` execution, **When** the Task Details page loads, **Then** task-editing recovery actions remain hidden.

## Requirements

- **REQ-001**: The execution action capability contract MUST expose `canEditForRerun` separately from `canUpdateInputs` and `canRerun`.
- **REQ-002**: Failed, completed, canceled, timed-out, and terminated `MoonMind.Run` executions with an original task input snapshot and task editing enabled MUST expose `canEditForRerun=true` and `canRerun=true`.
- **REQ-003**: Running and other non-terminal editable statuses MUST continue to expose `canUpdateInputs=true` without implying `canEditForRerun=true`.
- **REQ-004**: The Task Details page MUST render an **Edit task** link when either `canEditForRerun` or `canUpdateInputs` is true.
- **REQ-005**: For edit-for-rerun, the **Edit task** link MUST target `/tasks/new?rerunExecutionId=:taskExecutionId&mode=edit`.
- **REQ-006**: The Task Details page MUST render **Rerun** when `canRerun=true`.
- **REQ-007**: **Edit task** and **Rerun** MUST be additive; showing one valid action MUST NOT hide the other.

## Out of Scope

- Changing rerun submission semantics beyond honoring the edit-for-rerun route.
- Adding new persistent storage.
- Reworking the full Task Details page layout.

## Success Criteria

- Unit tests cover the action capability matrix for failed and non-terminal statuses.
- Frontend tests cover Task Details rendering and hrefs for failed edit-for-rerun and running update-inputs modes.
