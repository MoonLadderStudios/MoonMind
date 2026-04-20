# Feature Specification: Manual Dependency Wait Bypass

**Feature Branch**: `213-manual-dependency-bypass`  
**Created**: 2026-04-20  
**Status**: Draft  
**Input**: User request: "There should be a manual way in the UI to stop a workflow from waiting on dependencies if we realize we no longer need to wait for some reason that cannot be automatically discovered."

## User Story

As a MoonMind operator, I can manually release a `MoonMind.Run` that is waiting on declared dependencies so an operator-discovered exception can let the run proceed without canceling or recreating it.

## Acceptance Scenarios

1. **Given** a run is in `waiting_on_dependencies`, **when** the task detail page renders, **then** the Dependencies panel offers an explicit dependency-wait bypass action.
2. **Given** the operator confirms the bypass, **when** Mission Control submits the action, **then** the backend signals the workflow with `BypassDependencies`.
3. **Given** the workflow receives `BypassDependencies` while waiting on dependencies, **when** unresolved prerequisites remain, **then** the dependency gate records a `bypassed` resolution, records bypass outcomes for unresolved prerequisites, and allows the run to proceed.
4. **Given** a workflow is not waiting on dependencies, **when** `BypassDependencies` is received, **then** the signal is ignored without changing dependency state.

## Requirements

- **FR-001**: The execution detail API MUST expose a dependency bypass capability only for `waiting_on_dependencies` runs when actions are enabled.
- **FR-002**: The UI MUST render the manual bypass action in the dependency wait context, not as a generic task action.
- **FR-003**: The UI MUST require explicit operator confirmation before sending the bypass signal.
- **FR-004**: `MoonMind.Run` MUST support a `BypassDependencies` signal that releases the dependency wait without fabricating prerequisite completion.
- **FR-005**: The bypass MUST be visible in dependency metadata as a `bypassed` resolution with operator-readable outcome messages.

## Out Of Scope

- Editing dependency edges after run creation.
- Automatically deciding when dependencies are no longer needed.
- Bypassing other wait types such as provider slots, approvals, or merge gates.
